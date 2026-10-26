import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Phone, PhoneOff, Loader2, AlertTriangle, Zap, User, Users,
  BookOpen, Lightbulb, Activity, ChevronRight,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

type Speaker    = 'Customer' | 'Agent'
type CallStatus = 'idle' | 'dialing' | 'ringing' | 'in-progress' | 'completed' | 'failed'
type ConnStatus = 'disconnected' | 'connecting' | 'connected' | 'error'
type LiveStatus = 'idle' | 'listening' | 'thinking' | 'suggestion_ready' | 'wait'

interface TranscriptTurn {
  id: string
  speaker: Speaker
  text: string
  ts: Date
}

interface CallRecord {
  callId: string
  customer: string
  startedAt: string
  status: string
}

// ── Constants ─────────────────────────────────────────────────────────────────

const API = (import.meta.env.VITE_API_URL || '')
// Default to the page's own origin so the WS rides the Vite /ws proxy —
// works on localhost AND through the https tunnel (wss). A hardcoded
// ws://localhost:8003 is mixed-content-blocked on https pages.
const WS_BASE = (import.meta.env.VITE_API_URL || window.location.origin).replace(/^http/, 'ws')
const BACKOFF = [500, 1000, 2000, 4000, 5000]
const HISTORY_KEY = 'callAssistantV2History'

const LANGUAGES = [
  { id: 'auto', label: 'Auto' },
  { id: 'english', label: 'English' },
  { id: 'hinglish', label: 'Hinglish' },
  { id: 'hindi', label: 'Hindi' },
  { id: 'marathi', label: 'Marathi' },
]

// ── Component ─────────────────────────────────────────────────────────────────

export default function CallAssistantV2() {
  // Call setup
  const [customerNumber, setCustomerNumber] = useState('')
  const [agentNumber, setAgentNumber]       = useState('')
  const [language, setLanguage]             = useState('auto')

  // Call lifecycle
  const [callId, setCallId]         = useState<string | null>(null)
  const [callStatus, setCallStatus] = useState<CallStatus>('idle')
  const [duration, setDuration]     = useState(0)
  const [errorMsg, setErrorMsg]     = useState('')
  const [starting, setStarting]     = useState(false)

  // Connection
  const [agentConn, setAgentConn] = useState<ConnStatus>('disconnected')
  const agentWsRef       = useRef<WebSocket | null>(null)
  const agentBackoffRef  = useRef(0)
  const reconnectTimer   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const callIdRef        = useRef<string | null>(null)

  // Live data
  const [liveStatus, setLiveStatus]   = useState<LiveStatus>('idle')
  const [transcript, setTranscript]   = useState<TranscriptTurn[]>([])
  const transcriptRef                 = useRef<TranscriptTurn[]>([])
  const [streamText, setStreamText]   = useState('')        // streaming spoken tokens
  const [suggestion, setSuggestion]   = useState('')        // finalized clean suggestion
  const [keyFacts, setKeyFacts]       = useState('')
  const [intent, setIntent]           = useState('')
  const [nextAction, setNextAction]   = useState('')
  const [sources, setSources]         = useState<string[]>([])
  const [timingInfo, setTimingInfo]   = useState('')

  // History
  const [history, setHistory] = useState<CallRecord[]>([])

  const transcriptPaneRef = useRef<HTMLDivElement | null>(null)

  // ── Load history ────────────────────────────────────────────────────────────
  useEffect(() => {
    try {
      const raw = localStorage.getItem(HISTORY_KEY)
      if (raw) setHistory(JSON.parse(raw))
    } catch { /* ignore */ }
  }, [])

  function pushHistory(rec: CallRecord) {
    setHistory(prev => {
      const next = [rec, ...prev.filter(r => r.callId !== rec.callId)].slice(0, 12)
      try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }
  function patchHistory(id: string, status: string) {
    setHistory(prev => {
      const next = prev.map(r => r.callId === id ? { ...r, status } : r)
      try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }

  // ── Duration timer (runs only while a call is active) ────────────────────────
  useEffect(() => {
    if (callStatus !== 'in-progress' && callStatus !== 'ringing' && callStatus !== 'dialing') return
    const id = setInterval(() => setDuration(s => s + 1), 1000)
    return () => clearInterval(id)
  }, [callStatus])

  function fmtDuration(s: number): string {
    const m = Math.floor(s / 60), sec = s % 60
    return `${m}:${String(sec).padStart(2, '0')}`
  }

  // ── Transcript helper ────────────────────────────────────────────────────────
  function pushTurn(speaker: Speaker, text: string) {
    const turn: TranscriptTurn = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      speaker, text, ts: new Date(),
    }
    const next = [...transcriptRef.current, turn].slice(-40)
    transcriptRef.current = next
    setTranscript([...next])
    setTimeout(() => {
      transcriptPaneRef.current?.scrollTo({ top: transcriptPaneRef.current.scrollHeight, behavior: 'smooth' })
    }, 40)
  }

  // ── Agent WebSocket ──────────────────────────────────────────────────────────
  const connectAgentWs = useCallback((cid: string) => {
    if (agentWsRef.current?.readyState === WebSocket.OPEN) return
    setAgentConn('connecting')
    const ws = new WebSocket(`${WS_BASE}/ws/call-assistant-v2/agent/${encodeURIComponent(cid)}`)
    agentWsRef.current = ws

    ws.onopen = () => {
      setAgentConn('connected')
      agentBackoffRef.current = 0
      ws.send(JSON.stringify({ type: 'set_language', language }))
    }

    ws.onmessage = (evt) => {
      let msg: Record<string, unknown>
      try { msg = JSON.parse(evt.data) } catch { return }
      const t = msg.type as string

      if (t === 'ping' || t === 'session_info' || t === 'language_set') return

      if (t === 'call_status') {
        const st = (msg.status as string) as CallStatus
        setCallStatus(st)
        if (callIdRef.current) patchHistory(callIdRef.current, st)
        if (st === 'completed' || st === 'failed') setLiveStatus('idle')
        return
      }

      if (t === 'transcript') {
        const spk = (msg.speaker as Speaker) || 'Customer'
        pushTurn(spk, (msg.text as string) || '')
        if (spk === 'Customer') {
          setLiveStatus('thinking')
          setStreamText('')
          setSuggestion('')
        }
        return
      }

      if (t === 'token') {
        setStreamText(prev => prev + ((msg.token as string) || ''))
        setLiveStatus('thinking')
        return
      }

      if (t === 'suggestion_done') {
        setSuggestion((msg.suggestion as string) || '')
        setStreamText('')
        setKeyFacts((msg.key_facts as string) === 'none' ? '' : ((msg.key_facts as string) || ''))
        setIntent((msg.intent as string) || '')
        setNextAction((msg.next_action as string) || '')
        setSources((msg.sources as string[]) || [])
        setLiveStatus('suggestion_ready')
        const stt = msg.stt_ms ? `STT ${msg.stt_ms}ms` : ''
        const rag = msg.rag_ms ? `RAG ${msg.rag_ms}ms` : ''
        const ft  = msg.first_token_ms ? `1st-tok ${msg.first_token_ms}ms` : ''
        const tot = msg.total_ms ? `total ${msg.total_ms}ms` : ''
        setTimingInfo([stt, rag, ft, tot].filter(Boolean).join(' · '))
        return
      }

      if (t === 'wait') {
        setLiveStatus(prev => (prev === 'thinking' ? 'listening' : prev))
        setStreamText('')
        return
      }
    }

    ws.onerror = () => setAgentConn('error')

    ws.onclose = () => {
      agentWsRef.current = null
      setAgentConn(prev => (prev === 'error' ? 'error' : 'disconnected'))
      // Reconnect only while a call is active
      if (callIdRef.current && (callStatusRef.current === 'in-progress' || callStatusRef.current === 'ringing' || callStatusRef.current === 'dialing')) {
        const delay = BACKOFF[Math.min(agentBackoffRef.current, BACKOFF.length - 1)]
        agentBackoffRef.current += 1
        reconnectTimer.current = setTimeout(() => connectAgentWs(cid), delay)
      }
    }
  }, [language])

  // Keep a ref of callStatus for the close handler
  const callStatusRef = useRef<CallStatus>('idle')
  useEffect(() => { callStatusRef.current = callStatus }, [callStatus])

  // ── Start / End call ─────────────────────────────────────────────────────────
  async function startCall() {
    setErrorMsg('')
    if (!customerNumber.trim()) { setErrorMsg('Enter the customer phone number.'); return }
    setStarting(true)
    setTranscript([]); transcriptRef.current = []
    setSuggestion(''); setStreamText(''); setKeyFacts(''); setIntent(''); setNextAction(''); setSources([])
    setDuration(0)
    setCallStatus('dialing')
    try {
      const res = await fetch(`${API}/api/call-assistant-v2/calls`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          customer_number: customerNumber.trim(),
          agent_number: agentNumber.trim() || undefined,
          language,
        }),
      })
      const data = await res.json()
      if (!data.success) throw new Error(data.error || 'Failed to start call')
      setCallId(data.call_id)
      callIdRef.current = data.call_id
      setCallStatus((data.status as CallStatus) || 'dialing')
      setLiveStatus('listening')
      pushHistory({ callId: data.call_id, customer: customerNumber.trim(), startedAt: new Date().toISOString(), status: 'dialing' })
      connectAgentWs(data.call_id)
    } catch (e: unknown) {
      const err = e as { message?: string }
      setErrorMsg(err.message || 'Could not start the call.')
      setCallStatus('failed')
    } finally {
      setStarting(false)
    }
  }

  async function endCall() {
    const cid = callIdRef.current
    setCallStatus('completed')
    setLiveStatus('idle')
    if (cid) {
      try { await fetch(`${API}/api/call-assistant-v2/calls/${encodeURIComponent(cid)}/end`, { method: 'POST' }) }
      catch { /* ignore */ }
    }
    agentWsRef.current?.close()
    agentWsRef.current = null
    callIdRef.current = null
    setCallId(null)
    setAgentConn('disconnected')
  }

  // Language change → notify backend live
  function changeLanguage(lang: string) {
    setLanguage(lang)
    if (agentWsRef.current?.readyState === WebSocket.OPEN) {
      agentWsRef.current.send(JSON.stringify({ type: 'set_language', language: lang }))
    }
  }

  // Cleanup on unmount
  useEffect(() => () => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    agentWsRef.current?.close()
  }, [])

  // ── Derived ──────────────────────────────────────────────────────────────────
  const callActive = callStatus === 'in-progress' || callStatus === 'ringing' || callStatus === 'dialing'
  const connColor = agentConn === 'connected' ? '#16a34a' : agentConn === 'connecting' ? '#d97706' : '#dc2626'
  const connDot   = agentConn === 'connected' ? '●' : agentConn === 'connecting' ? '○' : '✕'
  const statusMeta: Record<CallStatus, { label: string; color: string }> = {
    'idle':        { label: 'Idle',        color: '#94a3b8' },
    'dialing':     { label: 'Dialing…',    color: '#d97706' },
    'ringing':     { label: 'Ringing…',    color: '#d97706' },
    'in-progress': { label: 'In call',     color: '#16a34a' },
    'completed':   { label: 'Ended',       color: '#64748b' },
    'failed':      { label: 'Failed',      color: '#dc2626' },
  }
  const liveSuggestion = streamText || suggestion
  // Latest line from each speaker, shown as context inside the AI reply section
  const lastCustomer = [...transcript].reverse().find(t => t.speaker === 'Customer')
  const lastAgent    = [...transcript].reverse().find(t => t.speaker === 'Agent')

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Call setup + controls ──────────────────────────────────────────── */}
      <div className="glass-card" style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, color: connColor, fontWeight: 600, minWidth: 92 }}>
            {connDot} {agentConn === 'connected' ? 'Connected' : agentConn === 'connecting' ? 'Connecting…' : 'Offline'}
          </span>

          <span style={{
            fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 99,
            color: statusMeta[callStatus].color,
            background: `${statusMeta[callStatus].color}1a`,
          }}>
            {statusMeta[callStatus].label}{callActive ? ` · ${fmtDuration(duration)}` : ''}
          </span>

          <div style={{ flex: 1 }} />

          {!callActive ? (
            <>
              <input
                value={customerNumber}
                onChange={e => setCustomerNumber(e.target.value)}
                placeholder="Customer number  +91…"
                style={inputStyle}
              />
              <input
                value={agentNumber}
                onChange={e => setAgentNumber(e.target.value)}
                placeholder="Agent phone (optional)"
                style={inputStyle}
              />
              <select value={language} onChange={e => changeLanguage(e.target.value)} style={{ ...inputStyle, width: 110 }}>
                {LANGUAGES.map(l => <option key={l.id} value={l.id}>{l.label}</option>)}
              </select>
              <button onClick={startCall} disabled={starting} style={btnStyle(false)}>
                {starting ? <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> : <Phone size={16} />}
                Start Call
              </button>
            </>
          ) : (
            <>
              <select value={language} onChange={e => changeLanguage(e.target.value)} style={{ ...inputStyle, width: 110 }}>
                {LANGUAGES.map(l => <option key={l.id} value={l.id}>{l.label}</option>)}
              </select>
              <button onClick={endCall} style={btnStyle(true)}>
                <PhoneOff size={16} /> End Call
              </button>
            </>
          )}
        </div>

        {errorMsg && (
          <div style={alertStyle('#fef2f2', '#fca5a5', '#dc2626')}>
            <AlertTriangle size={14} /> {errorMsg}
          </div>
        )}
        {!callActive && !errorMsg && (
          <div style={alertStyle('#eff6ff', '#bfdbfe', '#1e40af')}>
            <Phone size={14} /> Enter the customer number and press Start. Your phone rings first; once you answer, the customer is dialed and the AI listens to the customer to coach you live.
          </div>
        )}
      </div>

      {/* ── Main grid: transcript | suggestion + insights ──────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.1fr', gap: 16, alignItems: 'start' }}>

        {/* LEFT: live transcript */}
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={panelHeader}>
            <Users size={15} /> Live Transcript
            {callStatus === 'in-progress' && (
              <span style={{ marginLeft: 'auto', fontSize: 11, color: '#16a34a', fontWeight: 600,
                animation: 'pulse-red 1.4s ease-in-out infinite' }}>● live</span>
            )}
          </div>
          <div ref={transcriptPaneRef} style={{ height: 460, overflowY: 'auto', padding: '12px 16px',
            display: 'flex', flexDirection: 'column', gap: 8 }}>
            {transcript.length === 0 && (
              <div style={emptyHint}>Transcript appears here once the customer speaks.</div>
            )}
            {transcript.map(turn => <TranscriptBubble key={turn.id} turn={turn} />)}
          </div>
        </div>

        {/* RIGHT: suggestion + insights stack */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* AI suggestion */}
          <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={panelHeader}>
              <Zap size={15} style={{ color: 'var(--color-red)' }} /> Say This Now
              {liveStatus === 'thinking' && (
                <Loader2 size={14} style={{ marginLeft: 'auto', animation: 'spin 1s linear infinite', opacity: 0.6 }} />
              )}
            </div>
            <div style={{ padding: 16, minHeight: 150 }}>
              {(lastCustomer || lastAgent) && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    In reply to
                  </span>
                  {lastCustomer && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                      <span style={chip('#eff6ff', '#2563eb', '#bfdbfe')}>Customer</span>
                      <span style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--color-text)' }}>{lastCustomer.text}</span>
                    </div>
                  )}
                  {lastAgent && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                      <span style={chip('#f0fdf4', '#16a34a', '#bbf7d0')}>Agent</span>
                      <span style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--color-text)' }}>{lastAgent.text}</span>
                    </div>
                  )}
                </div>
              )}
              {liveSuggestion ? (
                <div style={{
                  background: 'linear-gradient(to right, #fff1f2, #ffe4e6)',
                  border: '1px solid #fecdd3',
                  borderLeft: '4px solid #e11d48',
                  padding: '14px 18px',
                  borderRadius: '10px',
                  marginBottom: '16px'
                }}>
                  <p style={{ fontSize: 18, lineHeight: 1.6, fontWeight: 600, color: '#881337', margin: 0 }}>
                    {liveSuggestion}
                    {liveStatus === 'thinking' && (
                      <span style={{ display: 'inline-block', width: 8, height: 18, background: '#e11d48',
                        marginLeft: 6, verticalAlign: 'middle', animation: 'blink 0.8s step-end infinite' }} />
                    )}
                  </p>
                </div>
              ) : liveStatus === 'thinking' ? (
                <div style={centerHint}><Loader2 size={22} style={{ animation: 'spin 1s linear infinite', opacity: 0.5 }} /> Generating…</div>
              ) : (
                <div style={centerHint}><span style={{ fontSize: 26, opacity: 0.3 }}>…</span> Waiting for the customer to speak</div>
              )}

              {keyFacts && (
                <div style={{ marginTop: 14, display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase' }}>Key facts:</span>
                  {keyFacts.split(',').map((f, i) => f.trim() && (
                    <span key={i} style={chip('#fff7ed', '#9a3412', '#fed7aa')}>{f.trim()}</span>
                  ))}
                </div>
              )}

              {sources.length > 0 && (
                <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  <BookOpen size={13} style={{ color: '#64748b' }} />
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase' }}>Sources:</span>
                  {sources.slice(0, 4).map(src => (
                    <span key={src} style={chip('#f8fafc', '#475569', '#e2e8f0')}>{src}</span>
                  ))}
                </div>
              )}

              {timingInfo && <div style={{ marginTop: 8, fontSize: 11, color: '#94a3b8' }}>{timingInfo}</div>}
            </div>
          </div>

          {/* Customer insights */}
          <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={panelHeader}><Activity size={15} /> Customer Insight</div>
            <div style={{ padding: 16 }}>
              {intent ? (
                <span style={chip('#eff6ff', '#1e40af', '#bfdbfe')}>{intent}</span>
              ) : (
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>No intent detected yet.</span>
              )}
            </div>
          </div>

          {/* Suggested next action */}
          <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={panelHeader}><Lightbulb size={15} style={{ color: '#d97706' }} /> Suggested Next Action</div>
            <div style={{ padding: 16 }}>
              {nextAction ? (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 14, lineHeight: 1.55, color: 'var(--color-text)' }}>
                  <ChevronRight size={16} style={{ color: '#d97706', flexShrink: 0, marginTop: 2 }} />
                  {nextAction}
                </div>
              ) : (
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>The next best step appears here.</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Call history ──────────────────────────────────────────────────── */}
      {history.length > 0 && (
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={panelHeader}><Phone size={15} /> Recent Calls</div>
          <div style={{ padding: '8px 16px 14px' }}>
            {history.map(rec => (
              <div key={rec.callId} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0',
                borderBottom: '1px solid var(--glass-border)', fontSize: 13 }}>
                <User size={14} style={{ color: '#64748b' }} />
                <span style={{ fontWeight: 600 }}>{rec.customer}</span>
                <span style={{ color: '#94a3b8', fontFamily: 'monospace', fontSize: 11 }}>{rec.callId}</span>
                <span style={{ marginLeft: 'auto', color: '#94a3b8', fontSize: 12 }}>
                  {new Date(rec.startedAt).toLocaleString('en-IN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })}
                </span>
                <span style={chip('#f1f5f9', '#475569', '#e2e8f0')}>{rec.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulse-red { 0%,100%{opacity:1} 50%{opacity:0.5} }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes spin { from{transform:rotate(0)} to{transform:rotate(360deg)} }
      `}</style>
    </div>
  )
}

// ── Sub-components + styles ─────────────────────────────────────────────────────

function TranscriptBubble({ turn }: { turn: TranscriptTurn }) {
  const isCustomer = turn.speaker === 'Customer'
  const bg     = isCustomer ? '#eff6ff' : '#f0fdf4'
  const label  = isCustomer ? '#2563eb' : '#16a34a'
  const border = isCustomer ? '#bfdbfe' : '#bbf7d0'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: label, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {turn.speaker}{turn.speaker === 'Agent' && <span style={{ fontWeight: 400, marginLeft: 4, color: '#64748b' }}>(you)</span>}
        </span>
        <span style={{ fontSize: 10, color: '#94a3b8' }}>
          {turn.ts.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </span>
      </div>
      <div style={{ padding: '8px 12px', borderRadius: 10, fontSize: 14, lineHeight: 1.5,
        background: bg, border: `1px solid ${border}`, color: 'var(--color-text)' }}>
        {turn.text}
      </div>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  padding: '8px 12px', borderRadius: 10, fontSize: 13, border: '1px solid var(--color-border)',
  background: 'white', color: 'var(--color-text)', width: 180,
}

function btnStyle(danger: boolean): React.CSSProperties {
  return {
    padding: '8px 20px', borderRadius: 24, fontSize: 14, fontWeight: 700, border: 'none', cursor: 'pointer',
    background: danger ? '#dc2626' : 'var(--color-red, #e31837)', color: 'white',
    display: 'flex', alignItems: 'center', gap: 8,
    animation: danger ? 'pulse-red 1.6s ease-in-out infinite' : 'none',
  }
}

const panelHeader: React.CSSProperties = {
  padding: '10px 16px', borderBottom: '1px solid var(--glass-border)', fontWeight: 700, fontSize: 13,
  display: 'flex', gap: 8, alignItems: 'center',
}

const emptyHint: React.CSSProperties = {
  color: 'var(--color-text-muted)', fontSize: 13, marginTop: 20, textAlign: 'center',
}

const centerHint: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
  minHeight: 110, gap: 10, color: 'var(--color-text-muted)', fontSize: 13,
}

function chip(bg: string, color: string, border: string): React.CSSProperties {
  return { fontSize: 11, padding: '2px 9px', borderRadius: 99, background: bg, color, border: `1px solid ${border}`, fontWeight: 600 }
}

function alertStyle(bg: string, border: string, color: string): React.CSSProperties {
  return {
    marginTop: 10, padding: '7px 12px', borderRadius: 8, background: bg, border: `1px solid ${border}`,
    fontSize: 12, color, display: 'flex', gap: 6, alignItems: 'center',
  }
}
