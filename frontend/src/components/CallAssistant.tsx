import { useState, useRef, useEffect, useCallback } from 'react'
import { Mic, MicOff, Loader2, AlertTriangle, Zap, User, Users } from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

type SpeakerLabel = 'Person 1' | 'Person 2' | 'Unknown'
type LiveStatus   = 'idle' | 'listening' | 'thinking' | 'suggestion_ready' | 'wait'
type ConnStatus   = 'disconnected' | 'connecting' | 'connected' | 'error'

interface TranscriptTurn {
  id: string
  speaker: SpeakerLabel
  text: string
  ts: Date
}

// ── Constants ─────────────────────────────────────────────────────────────────

// Default to the page's own origin (rides the Vite /ws proxy in dev, the https
// tunnel via wss in prod). A hardcoded ws://localhost:8003 is mixed-content-blocked
// on an https page, so VITE_API_URL must be set when hosted on Vercel.
const WS_BASE = (import.meta.env.VITE_API_URL || window.location.origin)
  .replace(/^http/, 'ws')

// VAD thresholds — match backend defaults
const VAD_THRESHOLD   = 18      // RMS 0–255; lower = more sensitive
const VAD_SILENCE_MS  = 700     // ms of silence that ends an utterance
const VAD_MAX_UTT_MS  = 12000   // safety flush after 12s
const MIN_BLOB_BYTES  = 800     // discard tiny chunks

// Auto-reconnect backoff (ms): 500, 1000, 2000, 4000, 5000, 5000, …
const BACKOFF = [500, 1000, 2000, 4000, 5000]

function getMimeType(): string {
  const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']
  return types.find(t => MediaRecorder.isTypeSupported(t)) || ''
}

function genCallSid(): string {
  return 'call-' + Math.random().toString(36).slice(2, 10)
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function CallAssistant() {
  // ── Call / session state ───────────────────────────────────────────────────
  const [callSid]     = useState<string>(() => genCallSid())
  const [agentPerson, setAgentPerson] = useState<SpeakerLabel | null>(null)
  const [duration, setDuration]       = useState(0)

  // ── WebSocket state ────────────────────────────────────────────────────────
  const [agentConn, setAgentConn]     = useState<ConnStatus>('disconnected')
  const agentWsRef  = useRef<WebSocket | null>(null)
  const audioWsRef  = useRef<WebSocket | null>(null)
  const agentBackoffRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Recording state ────────────────────────────────────────────────────────
  const [isRecording, setIsRecording] = useState(false)
  const streamRef        = useRef<MediaStream | null>(null)
  const audioCtxRef      = useRef<AudioContext | null>(null)
  const analyserRef      = useRef<AnalyserNode | null>(null)
  const vadRecorderRef   = useRef<MediaRecorder | null>(null)
  const vadChunksRef     = useRef<Blob[]>([])
  const vadSpeechActive  = useRef(false)
  const vadSilenceTimer  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isRecordingRef   = useRef(false)   // ref mirror for stale-closure safety

  // ── Live path state ────────────────────────────────────────────────────────
  const [liveStatus, setLiveStatus]   = useState<LiveStatus>('idle')
  const [transcript, setTranscript]   = useState<TranscriptTurn[]>([])
  const transcriptRef = useRef<TranscriptTurn[]>([])
  const [suggestion, setSuggestion]   = useState('')
  const [prevSuggestion, setPrevSuggestion] = useState('')
  const [sources, setSources]         = useState<string[]>([])
  const [errorMsg, setErrorMsg]       = useState('')
  const [timingInfo, setTimingInfo]   = useState<string>('')

  const transcriptPaneRef = useRef<HTMLDivElement | null>(null)

  // ── Duration timer ─────────────────────────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => setDuration(s => s + 1), 1000)
    return () => clearInterval(id)
  }, [])

  function fmtDuration(s: number): string {
    const m = Math.floor(s / 60), sec = s % 60
    return `${m}:${String(sec).padStart(2, '0')}`
  }

  // ── Auto-scroll transcript ─────────────────────────────────────────────────
  function pushTurn(speaker: SpeakerLabel, text: string) {
    const turn: TranscriptTurn = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      speaker, text, ts: new Date(),
    }
    const next = [...transcriptRef.current, turn].slice(-30)
    transcriptRef.current = next
    setTranscript([...next])
    setTimeout(() => {
      transcriptPaneRef.current?.scrollTo({
        top: transcriptPaneRef.current.scrollHeight, behavior: 'smooth',
      })
    }, 40)
  }

  // ── Agent WebSocket (suggestions in, identity out) ─────────────────────────

  const connectAgentWs = useCallback(() => {
    if (agentWsRef.current?.readyState === WebSocket.OPEN) return
    setAgentConn('connecting')
    const url = `${WS_BASE}/ws/agent/${encodeURIComponent(callSid)}`
    const ws  = new WebSocket(url)
    agentWsRef.current = ws

    ws.onopen = () => {
      setAgentConn('connected')
      agentBackoffRef.current = 0
      // If agent already picked their person, re-send
      if (agentPerson) {
        ws.send(JSON.stringify({ type: 'set_agent', person: agentPerson }))
      }
    }

    ws.onmessage = (evt) => {
      let msg: Record<string, unknown>
      try { msg = JSON.parse(evt.data) } catch { return }

      const t = msg.type as string

      if (t === 'ping') return

      if (t === 'transcript') {
        pushTurn(
          (msg.speaker as SpeakerLabel) || 'Unknown',
          (msg.text as string) || '',
        )
        setLiveStatus('thinking')
        return
      }

      if (t === 'token') {
        const tok = (msg.token as string) || ''
        setSuggestion(prev => prev + tok)
        setLiveStatus('suggestion_ready')
        return
      }

      if (t === 'suggestion_done') {
        const srcs = (msg.sources as string[]) || []
        setSources(srcs)
        setLiveStatus('suggestion_ready')
        const stt  = msg.stt_ms  ? `STT ${msg.stt_ms}ms` : ''
        const rag  = msg.rag_ms  ? `RAG ${msg.rag_ms}ms` : ''
        const ft   = msg.first_token_ms ? `1st-tok ${msg.first_token_ms}ms` : ''
        const tot  = msg.total_ms ? `total ${msg.total_ms}ms` : ''
        setTimingInfo([stt, rag, ft, tot].filter(Boolean).join(' · '))
        return
      }

      if (t === 'wait') {
        setLiveStatus('wait')
        setSuggestion('')
        setTimeout(() => setLiveStatus(prev => prev === 'wait' ? 'listening' : prev), 1800)
        return
      }

      if (t === 'agent_set') {
        // backend confirmed identity
        return
      }
    }

    ws.onerror = () => {
      setAgentConn('error')
    }

    ws.onclose = () => {
      agentWsRef.current = null
      if (agentConn !== 'error') setAgentConn('disconnected')
      // Exponential backoff reconnect
      const delay = BACKOFF[Math.min(agentBackoffRef.current, BACKOFF.length - 1)]
      agentBackoffRef.current += 1
      reconnectTimerRef.current = setTimeout(connectAgentWs, delay)
    }
  }, [callSid]) // agentPerson excluded intentionally — re-send on open handles it

  // Connect on mount, clean up on unmount
  useEffect(() => {
    connectAgentWs()
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      agentWsRef.current?.close()
    }
  }, [connectAgentWs])

  // ── One-tap agent identity ─────────────────────────────────────────────────
  function selectPerson(p: SpeakerLabel) {
    setAgentPerson(p)
    if (agentWsRef.current?.readyState === WebSocket.OPEN) {
      agentWsRef.current.send(JSON.stringify({ type: 'set_agent', person: p }))
    }
  }

  // ── Audio WebSocket (open when recording, close when stopped) ─────────────

  function openAudioWs() {
    if (audioWsRef.current?.readyState === WebSocket.OPEN) return
    const url = `${WS_BASE}/ws/call/audio/${encodeURIComponent(callSid)}`
    const ws  = new WebSocket(url)
    audioWsRef.current = ws
    ws.onerror = () => log('[AUDIO-WS] error')
    ws.onclose = () => { audioWsRef.current = null }
  }

  function closeAudioWs() {
    if (audioWsRef.current?.readyState === WebSocket.OPEN) {
      audioWsRef.current.send(JSON.stringify({ type: 'stop' }))
      audioWsRef.current.close()
    }
    audioWsRef.current = null
  }

  function sendAudioBlob(blob: Blob) {
    if (!audioWsRef.current || audioWsRef.current.readyState !== WebSocket.OPEN) {
      openAudioWs()
    }
    if (audioWsRef.current?.readyState === WebSocket.OPEN) {
      blob.arrayBuffer().then(buf => {
        audioWsRef.current?.send(buf)
      }).catch(e => log('[AUDIO-WS] send error', e))
    }
  }

  // ── VAD-driven recording ───────────────────────────────────────────────────

  async function startRecording() {
    setErrorMsg('')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 },
      })
    } catch (err: unknown) {
      const e = err as { name?: string; message?: string }
      const msgs: Record<string, string> = {
        NotAllowedError: 'Microphone access denied — allow it in browser settings.',
        NotFoundError:   'No microphone found.',
        NotReadableError:'Microphone in use by another app.',
      }
      setErrorMsg(msgs[e.name || ''] || `Mic error: ${e.message}`)
      return
    }

    streamRef.current = stream
    isRecordingRef.current = true
    setIsRecording(true)
    setLiveStatus('listening')

    // Open audio WS early (saves ~100ms on first utterance)
    openAudioWs()

    const mimeType = getMimeType()

    // ── Energy-based VAD (same as LiveAssistant.tsx) ─────────────────────
    try {
      const AudioCtx = (window.AudioContext || (window as unknown as Record<string, unknown>).webkitAudioContext) as typeof AudioContext
      const audioCtx = new AudioCtx({ sampleRate: 16000 })
      audioCtxRef.current = audioCtx
      const source  = audioCtx.createMediaStreamSource(stream)
      const analyser = audioCtx.createAnalyser()
      analyser.fftSize = 512
      analyser.smoothingTimeConstant = 0.3
      source.connect(analyser)
      analyserRef.current = analyser
      const freqData = new Uint8Array(analyser.frequencyBinCount)

      let maxUttTimer: ReturnType<typeof setTimeout> | null = null

      function flushUtterance() {
        if (vadSilenceTimer.current) { clearTimeout(vadSilenceTimer.current); vadSilenceTimer.current = null }
        if (maxUttTimer) { clearTimeout(maxUttTimer); maxUttTimer = null }
        vadSpeechActive.current = false
        if (vadRecorderRef.current?.state === 'recording') vadRecorderRef.current.stop()
      }

      function tick() {
        if (!isRecordingRef.current) return
        analyser.getByteFrequencyData(freqData)
        const rms = Math.sqrt(freqData.reduce((s, v) => s + v * v, 0) / freqData.length)

        if (rms > VAD_THRESHOLD) {
          if (vadSilenceTimer.current) { clearTimeout(vadSilenceTimer.current); vadSilenceTimer.current = null }
          if (!vadSpeechActive.current) {
            vadSpeechActive.current = true
            vadChunksRef.current    = []
            const rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
            vadRecorderRef.current  = rec
            rec.ondataavailable = (e) => { if (e.data.size > 0) vadChunksRef.current.push(e.data) }
            rec.onstop = () => {
              const blob = new Blob(vadChunksRef.current, { type: mimeType || 'audio/webm' })
              vadChunksRef.current = []
              vadRecorderRef.current = null
              if (blob.size >= MIN_BLOB_BYTES) {
                sendAudioBlob(blob)
              }
            }
            rec.start()
            maxUttTimer = setTimeout(flushUtterance, VAD_MAX_UTT_MS)
          }
        } else {
          if (vadSpeechActive.current && !vadSilenceTimer.current) {
            vadSilenceTimer.current = setTimeout(flushUtterance, VAD_SILENCE_MS)
          }
        }
        requestAnimationFrame(tick)
      }

      requestAnimationFrame(tick)
    } catch (err) {
      setErrorMsg('VAD init failed — try refreshing.')
      log('[VAD] init error', err)
    }
  }

  function stopRecording() {
    isRecordingRef.current = false
    setIsRecording(false)
    setLiveStatus('idle')

    if (vadSilenceTimer.current) { clearTimeout(vadSilenceTimer.current); vadSilenceTimer.current = null }
    vadSpeechActive.current = false
    if (vadRecorderRef.current?.state === 'recording') vadRecorderRef.current.stop()
    vadRecorderRef.current = null

    if (audioCtxRef.current) { audioCtxRef.current.close(); audioCtxRef.current = null }
    analyserRef.current = null

    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null }

    closeAudioWs()
  }

  function toggleRecord() {
    // Before a new suggestion starts, save the current one so it stays visible
    if (!isRecording) {
      setSuggestion('')
      setPrevSuggestion(suggestion)
      startRecording()
    } else {
      stopRecording()
    }
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      isRecordingRef.current = false
      streamRef.current?.getTracks().forEach(t => t.stop())
      audioCtxRef.current?.close()
      closeAudioWs()
    }
  }, [])

  // ── Derived state ──────────────────────────────────────────────────────────
  const connDot = agentConn === 'connected' ? '●' : agentConn === 'connecting' ? '○' : '✕'
  const connColor =
    agentConn === 'connected'  ? '#16a34a' :
    agentConn === 'connecting' ? '#d97706' : '#dc2626'

  const activeSuggestion = suggestion || (liveStatus === 'suggestion_ready' ? prevSuggestion : '')
  const showIdle = liveStatus === 'wait' || (liveStatus === 'listening' && !activeSuggestion)

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Header controls ─────────────────────────────────────────────── */}
      <div className="glass-card" style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>

          {/* Connection indicator */}
          <span style={{ fontSize: 13, color: connColor, fontWeight: 600, minWidth: 90 }}>
            {connDot} {agentConn === 'connected' ? 'Connected' : agentConn === 'connecting' ? 'Connecting…' : 'Offline'}
          </span>

          {/* Call SID + duration */}
          <span style={{ fontSize: 12, color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
            {callSid} · {fmtDuration(duration)}
          </span>

          {/* Spacer */}
          <div style={{ flex: 1 }} />

          {/* Person 1 / Person 2 selector */}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>I am:</span>
            {(['Person 1', 'Person 2'] as const).map(p => (
              <button
                key={p}
                onClick={() => selectPerson(p)}
                style={{
                  padding: '5px 14px', borderRadius: 20, fontSize: 13, fontWeight: 600,
                  border: `2px solid ${agentPerson === p ? (p === 'Person 1' ? '#2563eb' : '#16a34a') : 'var(--color-border)'}`,
                  background: agentPerson === p ? (p === 'Person 1' ? '#eff6ff' : '#f0fdf4') : 'white',
                  color: agentPerson === p ? (p === 'Person 1' ? '#2563eb' : '#16a34a') : 'var(--color-text-muted)',
                  cursor: 'pointer', transition: 'all 0.15s',
                  display: 'flex', alignItems: 'center', gap: 5,
                }}
              >
                <User size={13} />
                {p}
              </button>
            ))}
          </div>

          {/* Toggle record button */}
          <button
            onClick={toggleRecord}
            disabled={agentConn !== 'connected'}
            style={{
              padding: '8px 20px', borderRadius: 24, fontSize: 14, fontWeight: 700,
              border: 'none', cursor: agentConn === 'connected' ? 'pointer' : 'not-allowed',
              background: isRecording ? '#dc2626' : 'var(--color-red, #e31837)',
              color: 'white',
              display: 'flex', alignItems: 'center', gap: 8,
              animation: isRecording ? 'pulse-red 1.5s ease-in-out infinite' : 'none',
              opacity: agentConn !== 'connected' ? 0.5 : 1,
            }}
          >
            {isRecording ? <MicOff size={16} /> : <Mic size={16} />}
            {isRecording ? 'Stop' : 'Start Listening'}
          </button>
        </div>

        {!agentPerson && agentConn === 'connected' && (
          <div style={{
            marginTop: 10, padding: '7px 12px', borderRadius: 8,
            background: '#fffbeb', border: '1px solid #fcd34d',
            fontSize: 12, color: '#92400e', display: 'flex', gap: 6, alignItems: 'center',
          }}>
            <AlertTriangle size={14} />
            Tap "I am: Person 1" or "Person 2" before starting — this tells the AI which voice is yours.
          </div>
        )}

        {errorMsg && (
          <div style={{
            marginTop: 10, padding: '7px 12px', borderRadius: 8,
            background: '#fef2f2', border: '1px solid #fca5a5',
            fontSize: 12, color: '#dc2626', display: 'flex', gap: 6, alignItems: 'center',
          }}>
            <AlertTriangle size={14} /> {errorMsg}
          </div>
        )}
      </div>

      {/* ── Two-panel layout ────────────────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16,
        alignItems: 'start',
      }}>

        {/* ── LEFT: Rolling transcript ─────────────────────────────────── */}
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{
            padding: '10px 16px', borderBottom: '1px solid var(--glass-border)',
            fontWeight: 700, fontSize: 13, display: 'flex', gap: 8, alignItems: 'center',
          }}>
            <Users size={15} /> Live Transcript
            {isRecording && (
              <span style={{
                marginLeft: 'auto', fontSize: 11, color: '#dc2626', fontWeight: 600,
                animation: 'pulse-red 1.2s ease-in-out infinite',
              }}>
                🎤 listening…
              </span>
            )}
          </div>
          <div
            ref={transcriptPaneRef}
            style={{
              height: 440, overflowY: 'auto', padding: '12px 16px',
              display: 'flex', flexDirection: 'column', gap: 8,
            }}
          >
            {transcript.length === 0 && (
              <div style={{ color: 'var(--color-text-muted)', fontSize: 13, marginTop: 20, textAlign: 'center' }}>
                Transcript will appear here once recording starts.
              </div>
            )}
            {transcript.map(turn => (
              <TranscriptBubble key={turn.id} turn={turn} agentPerson={agentPerson} />
            ))}
          </div>
        </div>

        {/* ── RIGHT: Suggestion card ───────────────────────────────────── */}
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{
            padding: '10px 16px', borderBottom: '1px solid var(--glass-border)',
            fontWeight: 700, fontSize: 13, display: 'flex', gap: 8, alignItems: 'center',
          }}>
            <Zap size={15} style={{ color: 'var(--color-red)' }} /> Coaching Suggestion
            {liveStatus === 'thinking' && (
              <Loader2 size={14} style={{ marginLeft: 'auto', animation: 'spin 1s linear infinite', opacity: 0.6 }} />
            )}
          </div>
          <div style={{ padding: '16px', minHeight: 440 }}>
            {showIdle ? (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: 400, color: 'var(--color-text-muted)',
                fontSize: 13, gap: 8,
              }}>
                <span style={{ fontSize: 28, opacity: 0.3 }}>…</span>
                <span>Waiting for customer question</span>
              </div>
            ) : activeSuggestion ? (
              <div>
                <p style={{
                  fontSize: 16, lineHeight: 1.65, fontWeight: 500,
                  color: 'var(--color-text)', margin: 0,
                }}>
                  {activeSuggestion}
                  {liveStatus === 'thinking' && (
                    <span style={{ display: 'inline-block', width: 8, height: 16,
                      background: 'var(--color-red)', marginLeft: 4, verticalAlign: 'middle',
                      animation: 'blink 0.8s step-end infinite' }} />
                  )}
                </p>
                {sources.length > 0 && (
                  <div style={{ marginTop: 14, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {sources.slice(0, 3).map(src => (
                      <span key={src} style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 99,
                        background: '#f8fafc', color: '#475569', border: '1px solid #e2e8f0',
                      }}>
                        {src}
                      </span>
                    ))}
                  </div>
                )}
                {timingInfo && (
                  <div style={{ marginTop: 8, fontSize: 11, color: '#94a3b8' }}>
                    {timingInfo}
                  </div>
                )}
              </div>
            ) : liveStatus === 'thinking' ? (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: 400, gap: 12,
                color: 'var(--color-text-muted)', fontSize: 13,
              }}>
                <Loader2 size={24} style={{ animation: 'spin 1s linear infinite', opacity: 0.5 }} />
                <span>Generating suggestion…</span>
              </div>
            ) : null}
          </div>
        </div>

      </div>

      {/* Inline styles for animations */}
      <style>{`
        @keyframes pulse-red {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}

// ── Transcript bubble ─────────────────────────────────────────────────────────

function TranscriptBubble({
  turn, agentPerson,
}: {
  turn: TranscriptTurn
  agentPerson: SpeakerLabel | null
}) {
  const isAgent = agentPerson && turn.speaker === agentPerson
  const isPerson1 = turn.speaker === 'Person 1'

  const bubbleColor  = isPerson1 ? '#eff6ff' : '#f0fdf4'
  const labelColor   = isPerson1 ? '#2563eb' : '#16a34a'
  const borderColor  = isPerson1 ? '#bfdbfe' : '#bbf7d0'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{
          fontSize: 11, fontWeight: 700, color: labelColor,
          textTransform: 'uppercase', letterSpacing: '0.04em',
        }}>
          {turn.speaker}
          {isAgent && <span style={{ fontWeight: 400, marginLeft: 4, color: '#64748b' }}>(you)</span>}
        </span>
        <span style={{ fontSize: 10, color: '#94a3b8' }}>
          {turn.ts.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </span>
      </div>
      <div style={{
        padding: '8px 12px', borderRadius: 10, fontSize: 14, lineHeight: 1.5,
        background: bubbleColor, border: `1px solid ${borderColor}`,
        color: 'var(--color-text)',
      }}>
        {turn.text}
      </div>
    </div>
  )
}

// Minimal console logger (avoids lint warnings on console.log)
function log(...args: unknown[]) {
  // eslint-disable-next-line no-console
  console.log('[CallAssistant]', ...args)
}
