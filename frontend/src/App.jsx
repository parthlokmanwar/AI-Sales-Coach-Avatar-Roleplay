import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import {
  Sparkles, MessageSquare, Mic, Clock, FolderOpen,
  Copy, Download, RefreshCw, Save, Trash2, ChevronDown,
  Target, HelpCircle, ShieldAlert, MessageCircle, Lightbulb, BookOpen,
  HeartHandshake, FileText, Mail, FileCheck, ClipboardList, ArrowLeft, Search,
  CheckCircle2, AlertTriangle, Languages, PenLine, Bot, User, Send,
  Pencil, Eye, ExternalLink, Activity, Zap, Layers, Database, Menu, Phone,
  Headphones, Volume2, Square, Play, Pause, LogOut,
  LayoutDashboard, TrendingUp, Award, ArrowRight, PhoneCall,
  ThumbsUp, ThumbsDown, Minus, X, Check, Users,
  // Nav icons, chosen per destination rather than per vibe. `Home` is aliased
  // because this file already imports a `Home` landing-page component.
  // `NotebookPen` (the literal book-and-pen glyph) landed in lucide after
  // 0.263, so the pitch icon uses `FileEdit` — a page with a pen — instead.
  Home as HomeIcon, FileEdit, GraduationCap, Calculator, History, Calendar,
} from 'lucide-react';
import LiveAssistant from './components/LiveAssistant';
import LoanCalculators from './components/LoanCalculators';
import CallAssistant from './components/CallAssistant';
import CallAssistantV2 from './components/CallAssistantV2';
import RolePlaySimulation from './components/RolePlay';
import BookSession from './components/BookSession';
import EvaVoice from './components/EvaVoice';
import Login from './components/Login';
import Home from './components/Home'; // DEMO landing page — see auth gate below
import AdminApp from './admin/AdminApp';
import ForcePasswordReset from './account/ForcePasswordReset';
import ResetPassword from './account/ResetPassword';
import ProfileModal from './account/ProfileModal';
import { installAuthFetch, fetchMe, logout as authLogout, getToken, getStoredUser, setStoredUser } from './auth';
import { startProductTour } from './tour/engine';
import { shouldAutoStartTour, persistTourDone } from './tour/tourState';

const API = '';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fileExt(name) { return (name || '').split('.').pop().toLowerCase(); }

function truncateWords(str, maxLen) {
  if (!str || str.length <= maxLen) return str;
  const cut = str.slice(0, maxLen);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut) + '…';
}

function shortError(err, maxLen = 140) {
  if (!err) return 'Something went wrong.';
  const s = String(err);
  return s.length > maxLen ? s.slice(0, maxLen) + '…' : s;
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) + ' · ' +
    d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

function useToast() {
  const [toast, setToast] = useState(null);
  const show = useCallback((msg, type = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }, []);
  return { toast, show };
}

// ── TTS text extractor ──────────────────────────────────────────────────────
// innerText picks up [PAUSE...], CUE pills, and ol/li numbers — strip them
// so TTS doesn't read out interface annotations.
function cleanForTTS(raw) {
  return raw
    .split('\n')
    .map(l => l.trim())
    .filter(l =>
      l &&
      !l.match(/^[⏸⏸]\s*(PAUSE|pause)/i) &&
      !l.match(/^CUE\b/i) &&
      !l.match(/^\d+$/)        // bare ol numbers
    )
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// ── Chat markdown renderer ───────────────────────────────────────────────────
// The chat prompt (RULE 5) asks the LLM for ChatGPT-style structure — bullets,
// numbered steps, tables, bold figures. AI bubbles render that Markdown;
// user bubbles stay plain text. Sanitised so model output can't inject HTML.

marked.setOptions({ gfm: true, breaks: true });

function ChatMarkdown({ text }) {
  const html = useMemo(() => {
    try {
      return DOMPurify.sanitize(marked.parse(text || '', { async: false }));
    } catch {
      return null;   // parser hiccup on a weird partial stream — fall back to plain text
    }
  }, [text]);
  if (html === null) return <>{text}</>;
  return <div className="chat-md" dangerouslySetInnerHTML={{ __html: html }} />;
}

// ── Pitch Section (collapsible) ──────────────────────────────────────────────

function PitchSection({ icon: Icon, title, accentColor, accentTint, defaultOpen = false, forceOpen = false, lang = 'English', children }) {
  const [open, setOpen] = useState(defaultOpen);
  const [speechState, setSpeechState] = useState('idle'); // idle, loading, playing, paused
  const audioUrlRef = useRef(null); // track blob URL for cleanup
  const bodyRef = useRef(null);
  const audioRef = useRef(null);
  const isOpen = forceOpen || open;

  // Ensure audio element exists with event handlers; call once on mount.
  const getAudio = () => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.onended = () => setSpeechState('idle');
      audioRef.current.onerror = () => setSpeechState('idle');
    }
    return audioRef.current;
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (audioRef.current) { audioRef.current.pause(); audioRef.current.src = ''; }
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePlayPause = async (e) => {
    e.stopPropagation();

    if (speechState === 'playing') {
      audioRef.current?.pause();
      setSpeechState('paused');
      return;
    }

    if (speechState === 'paused') {
      try {
        await audioRef.current.play();
        setSpeechState('playing');
      } catch {
        setSpeechState('idle');
      }
      return;
    }

    // idle → fetch then play
    if (!bodyRef.current) return;
    const text = cleanForTTS(bodyRef.current.innerText || '');
    if (!text) return;

    // Create audio element NOW (before await) to keep the click user-gesture context.
    const audio = getAudio();

    setSpeechState('loading');
    try {
      const res = await fetch('/api/tts/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, lang }),
      });
      if (!res.ok) throw new Error(`TTS ${res.status}`);
      const blob = await res.blob();

      // Revoke old blob URL
      if (audioUrlRef.current) { URL.revokeObjectURL(audioUrlRef.current); audioUrlRef.current = null; }
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;

      audio.src = url;
      audio.load();
      await audio.play();
      setSpeechState('playing');
    } catch (err) {
      console.error('[TTS]', err);
      setSpeechState('idle');
    }
  };

  const handleStop = (e) => {
    e.stopPropagation();
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    setSpeechState('idle');
  };

  const ttsIcon = (() => {
    if (speechState === 'loading') return <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />;
    if (speechState === 'playing') return <Pause size={14} fill="currentColor" />;
    return <Play size={14} fill={speechState === 'paused' ? 'currentColor' : 'none'} />;
  })();

  return (
    <div
      className="pitch-section"
      style={{ '--accent-color': accentColor, '--accent-tint': accentTint }}
    >
      <div className="pitch-section-header" onClick={() => setOpen(o => !o)}>
        <div className="pitch-section-title">
          <span className="pitch-section-icon"><Icon size={16} /></span>
          <span>{title}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {isOpen && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <button
                className="icon-btn"
                title={speechState === 'playing' ? 'Pause' : speechState === 'paused' ? 'Resume' : 'Read Aloud'}
                onClick={handlePlayPause}
                disabled={speechState === 'loading'}
                style={{
                  color: speechState === 'playing' ? 'var(--color-primary)'
                    : speechState === 'paused' ? 'var(--color-primary)'
                    : 'var(--color-text-faint)',
                  padding: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}
              >
                {ttsIcon}
              </button>
              {speechState !== 'idle' && speechState !== 'loading' && (
                <button
                  className="icon-btn"
                  title="Stop"
                  onClick={handleStop}
                  style={{ color: 'var(--color-red)', padding: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                >
                  <Square size={14} fill="currentColor" />
                </button>
              )}
            </div>
          )}
          <span className={`chevron${isOpen ? ' open' : ''}`}><ChevronDown size={18} /></span>
        </div>
      </div>
      {isOpen && <div className="pitch-section-body fade-in" ref={bodyRef}>{children}</div>}
    </div>
  );
}

// ── Pitch Coaching Script renderer ──────────────────────────────────────────
// Parses the agent-only coaching script format:
//   Regular text  → speech bubble (what the agent says)
//   [PAUSE — ...] → purple pause pill
//   [CUE: ...]    → amber coaching hint
//   **bold**      → bold inline in speech
// No markdown library needed — pure React.

function renderInlineBold(text) {
  const parts = text.split(/\*\*([^*]+)\*\*/g);
  return parts.map((part, i) =>
    i % 2 === 1 ? <strong key={i}>{part}</strong> : part
  );
}

// Renders a continuous narrative — splits on blank lines into paragraphs,
// no speech-bubble borders. Used for Full Pitch Script.
function PitchNarrative({ text }) {
  if (!text) return null;
  const paragraphs = text.split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
  if (paragraphs.length <= 1) {
    return <p className="pitch-body-text" style={{ margin: 0 }}>{renderInlineBold(text.trim())}</p>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1em' }}>
      {paragraphs.map((para, i) => (
        <p key={i} className="pitch-body-text" style={{ margin: 0 }}>
          {renderInlineBold(para)}
        </p>
      ))}
    </div>
  );
}

function PitchDialogue({ text }) {
  if (!text) return null;

  // Ensure [PAUSE...] and [CUE:...] each get their own line
  const normalized = text
    .replace(/(\[PAUSE[^\]]*\])/gi, '\n$1\n')
    .replace(/(\[CUE:[^\]]*\])/gi, '\n$1\n');

  const lines = normalized.split('\n').map(l => l.trim()).filter(Boolean);

  return (
    <div className="pitch-dialogue">
      {lines.map((line, i) => {
        const pauseM = line.match(/^\[PAUSE[—–-]?\s*(.*?)\]$/i);
        const cueM = line.match(/^\[CUE:\s*(.*?)\]$/i);

        if (pauseM) return (
          <div key={i} className="dl-pause-wrap">
            <span className="dl-pause">
              ⏸ {pauseM[1] ? pauseM[1] : 'PAUSE — wait for customer response'}
            </span>
          </div>
        );
        if (cueM) return (
          <div key={i} className="dl-cue">
            <span className="dl-cue-label">CUE</span>
            <span className="dl-cue-text">{cueM[1]}</span>
          </div>
        );
        // Regular speech line
        return (
          <div key={i} className="dl-speech">
            {renderInlineBold(line)}
          </div>
        );
      })}
    </div>
  );
}

// ── Pitch Output ─────────────────────────────────────────────────────────────

function PitchOutput({ pitch, docsUsed, onRegenerate, onSave, showToast, forceAllOpen = false, lang = 'English' }) {
  if (!pitch) return null;

  const cleanText = (val) => {
    if (typeof val === 'string') return val;
    if (val && typeof val === 'object') return val.fact || val.quote || val.bullet || val.text || val.question || JSON.stringify(val);
    return String(val ?? '');
  };

  const fullText = [
    `OPENING HOOK:\n${pitch.opening_hook || ''}`,
    `\nKEY FACTS:\n${(pitch.key_product_facts || []).map(cleanText).join('\n')}`,
    `\nQUESTIONS TO ASK:\n${(pitch.questions_to_ask || []).map(cleanText).join('\n')}`,
    `\nOBJECTION HANDLING:\n${(pitch.objection_handling || []).filter(o => o && (o.objection || o.response)).map(o => `Q: ${o.objection || ''}\nA: ${o.response || ''}`).join('\n\n')}`,
    `\nDOCUMENTS CHECKLIST:\n${(pitch.documents_checklist || []).map(cleanText).join('\n')}`,
    `\nCLOSING:\n${pitch.closing_strategy || ''}`,
    `\nFULL PITCH SCRIPT:\n${pitch.narrative_summary || ''}`,
    `\nFOLLOW-UP EMAIL:\n${pitch.follow_up_email || ''}`,
  ].join('\n');

  const handleDownloadDocx = async () => {
    try {
      const res = await fetch(`${API}/api/pitch/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pitch }),
      });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'HDFC_Sales_Pitch.docx';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      showToast?.('DOCX downloaded', 'success');
    } catch (e) {
      showToast?.(`Download failed: ${shortError(e.message)}`, 'error');
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(fullText);
    showToast?.('Pitch copied to clipboard', 'success');
  };

  return (
    <div className="slide-up">
      <div className="pitch-toolbar">
        <div className="pitch-toolbar-identity">
          <div className="pitch-toolbar-icon-tile">
            <Sparkles size={20} />
          </div>
          <div>
            <div className="pitch-toolbar-heading">Generated Pitch</div>
            <div className="pitch-toolbar-sub">
              {pitch.opening_hook ? 'Tap any section to expand · use while on call' : 'Generating…'}
            </div>
          </div>
          {pitch.opening_hook && (
            <span className="pitch-ready-badge">
              <CheckCircle2 size={10} /> READY
            </span>
          )}
        </div>
        <div className="pitch-toolbar-actions">
          <button className="btn btn-secondary" onClick={handleCopy}><Copy size={14} /> Copy</button>
          <button className="btn btn-secondary" onClick={handleDownloadDocx}><Download size={14} /> DOCX</button>
          {onSave && <button className="btn btn-secondary" onClick={onSave}><Save size={14} /> Save</button>}
          {onRegenerate && <button className="btn btn-primary" onClick={onRegenerate}><RefreshCw size={14} /> Regenerate</button>}
        </div>
      </div>

      <PitchSection icon={Target} title="Opening Hook" accentColor="#E31837" accentTint="#fdecef" defaultOpen forceOpen={forceAllOpen} lang={lang}>
        <p className="pitch-body-text">{pitch.opening_hook}</p>
      </PitchSection>

      <PitchSection icon={HelpCircle} title="Questions to Ask" accentColor="#0A1628" accentTint="#eef2f7" forceOpen={forceAllOpen} lang={lang}>
        {(() => {
          const items = (pitch.questions_to_ask || []).filter(Boolean);
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No discovery questions generated.</p>;
          return <ol className="pitch-numbered">{items.map((q, i) => <li key={i}>{cleanText(q)}</li>)}</ol>;
        })()}
      </PitchSection>

      <PitchSection icon={ShieldAlert} title="What NOT to Say" accentColor="#F59E0B" accentTint="#fffbeb" forceOpen={forceAllOpen} lang={lang}>
        {(() => {
          const items = (pitch.what_not_to_say || []).filter(Boolean);
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No warnings generated.</p>;
          return items.map((w, i) => <div key={i} className="warn-item">{cleanText(w)}</div>);
        })()}
      </PitchSection>

      <PitchSection icon={MessageCircle} title="Customer Might Ask" accentColor="#2563EB" accentTint="#eff6ff" forceOpen={forceAllOpen} lang={lang}>
        {(() => {
          const items = (pitch.anticipated_customer_questions || []).filter(qa => qa && (qa.question || qa.answer));
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No anticipated questions generated.</p>;
          return items.map((qa, i) => (
            <div key={i} className="qa-pair">
              <div className="qa-q">Q: {qa.question || 'N/A'}</div>
              <div className="qa-a">{qa.answer || 'N/A'}</div>
            </div>
          ));
        })()}
      </PitchSection>

      <PitchSection icon={Lightbulb} title="Objection Handling" accentColor="#E31837" accentTint="#fdecef" forceOpen={forceAllOpen} lang={lang}>
        {(() => {
          const items = (pitch.objection_handling || []).filter(o => o && (o.objection?.trim() || o.response?.trim()));
          if (!items.length) {
            return (
              <p style={{ color: 'var(--color-text-faint)', fontSize: 13, textAlign: 'center', padding: '12px 0' }}>
                No major objections expected for this customer profile.
              </p>
            );
          }
          return items.map((o, i) => (
            <div key={i} className="obj-pair">
              {o.objection?.trim() && <div className="obj-objection"><AlertTriangle size={13} /> {o.objection}</div>}
              {o.response?.trim() && <div className="obj-response">{o.response}</div>}
            </div>
          ));
        })()}
      </PitchSection>

      <PitchSection icon={BookOpen} title="Key Product Facts" accentColor="#10B981" accentTint="#ecfdf5" forceOpen={forceAllOpen} lang={lang}>
        {(() => {
          const items = (pitch.key_product_facts || []).filter(Boolean);
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No key product facts generated.</p>;
          return <ul className="pitch-list">{items.map((f, i) => <li key={i}>{cleanText(f)}</li>)}</ul>;
        })()}
      </PitchSection>

      <PitchSection icon={ClipboardList} title="Documents Checklist" accentColor="#0EA5E9" accentTint="#f0f9ff" forceOpen={forceAllOpen} lang={lang}>
        {(() => {
          const items = (pitch.documents_checklist || []).filter(Boolean);
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No documents checklist generated.</p>;
          return <ol className="pitch-numbered">{items.map((d, i) => <li key={i}>{cleanText(d)}</li>)}</ol>;
        })()}
      </PitchSection>

      <PitchSection icon={HeartHandshake} title="Closing Strategy" accentColor="#0A1628" accentTint="#eef2f7" forceOpen={forceAllOpen} lang={lang}>
        <p className="pitch-body-text">{pitch.closing_strategy}</p>
      </PitchSection>

      <PitchSection icon={FileText} title="Full Pitch Script" accentColor="#7C3AED" accentTint="#f5f3ff" forceOpen={forceAllOpen} lang={lang}>
        <div className="narrative-box">
          <PitchNarrative text={pitch.narrative_summary} />
        </div>
      </PitchSection>

      {pitch.follow_up_email && (
        <PitchSection icon={Mail} title="Follow-Up Email Template" accentColor="#0EA5E9" accentTint="#f0f9ff" forceOpen={forceAllOpen} lang={lang}>
          <div className="narrative-box" style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
            {pitch.follow_up_email}
          </div>
        </PitchSection>
      )}

      {/* ── 5 new sections ────────────────────────────────────────────── */}

      {(pitch.red_flags) && (
        <PitchSection icon={AlertTriangle} title="Red Flags & Watch Out" accentColor="#DC2626" accentTint="#fef2f2" forceOpen={forceAllOpen} lang={lang}>
          {(() => {
            const flags = Array.isArray(pitch.red_flags) ? pitch.red_flags : [pitch.red_flags];
            if (!flags.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No significant risk flags for this profile.</p>;
            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {flags.map((flag, i) => {
                  // Split each flag block into lines and render with colour-coded labels
                  const lines = String(flag).split('\n').map(l => l.trim()).filter(Boolean);
                  const title = lines[0]?.replace(/^🚩\s*/, '');
                  const rest = lines.slice(1);
                  return (
                    <div key={i} style={{ background: '#fef2f2', border: '1px solid rgba(220,38,38,0.18)', borderRadius: 8, padding: '12px 14px' }}>
                      <div style={{ fontWeight: 700, color: '#b91c1c', fontSize: 13, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <AlertTriangle size={13} /> {title}
                      </div>
                      {rest.map((line, j) => {
                        const isLabel = /^(Risk|Why it matters|Agent action):/i.test(line);
                        if (isLabel) {
                          const colonIdx = line.indexOf(':');
                          const label = line.slice(0, colonIdx);
                          const text = line.slice(colonIdx + 1).trim();
                          return (
                            <div key={j} style={{ fontSize: 12.5, marginBottom: 4, lineHeight: 1.5 }}>
                              <span style={{ fontWeight: 600, color: '#991b1b', marginRight: 4 }}>{label}:</span>
                              <span style={{ color: '#374151' }}>{text}</span>
                            </div>
                          );
                        }
                        return <div key={j} style={{ fontSize: 12.5, color: '#374151', lineHeight: 1.5 }}>{line}</div>;
                      })}
                    </div>
                  );
                })}
              </div>
            );
          })()}
        </PitchSection>
      )}

      {(pitch.competitor_handling) && (
        <PitchSection icon={TrendingUp} title="Competitor Handling" accentColor="#7C3AED" accentTint="#f5f3ff" forceOpen={forceAllOpen} lang={lang}>
          {(() => {
            const items = Array.isArray(pitch.competitor_handling)
              ? pitch.competitor_handling.filter(o => o && (o.objection?.trim() || o.response?.trim()))
              : [];
            if (!items.length) {
              return (
                <div className="narrative-box">
                  <PitchNarrative text={typeof pitch.competitor_handling === 'string' ? pitch.competitor_handling : ''} />
                </div>
              );
            }
            return items.map((o, i) => (
              <div key={i} className="obj-pair">
                {o.objection?.trim() && <div className="obj-objection"><AlertTriangle size={13} /> {o.objection}</div>}
                {o.response?.trim() && <div className="obj-response">{o.response}</div>}
              </div>
            ));
          })()}
        </PitchSection>
      )}

      {pitch.reengagement_script && (
        <PitchSection icon={RefreshCw} title="Re-engagement Script" accentColor="#F59E0B" accentTint="#fffbeb" forceOpen={forceAllOpen} lang={lang}>
          <p className="pitch-body-text">{pitch.reengagement_script}</p>
        </PitchSection>
      )}

      {pitch.cross_sell && (
        <PitchSection icon={Sparkles} title="Cross-Sell Opportunity" accentColor="#0EA5E9" accentTint="#f0f9ff" forceOpen={forceAllOpen} lang={lang}>
          <div className="narrative-box">
            <PitchNarrative text={typeof pitch.cross_sell === 'string' ? pitch.cross_sell : ''} />
          </div>
        </PitchSection>
      )}

      {docsUsed && docsUsed.length > 0 && (
        <div className="doc-chip-row">
          <span className="doc-chip-label">Sources:</span>
          {docsUsed.map((d, i) => <span key={i} className="doc-chip"><FileCheck size={12} /> {d}</span>)}
        </div>
      )}
    </div>
  );
}

// ── Quick Form ───────────────────────────────────────────────────────────────

const MEETING_TYPES = ['First Meeting', 'Follow-up', 'Product Demo', 'Closing Meeting', 'Objection Handling'];
const LANGUAGES = ['English', 'Hindi', 'Marathi', 'Tamil', 'Bengali', 'Telugu'];

// Sample pitch scenarios for the "Generate" button — picked at random to
// pre-fill the Quick Form, then editable by the user.
const RANDOM_PITCHES = [
  {
    customerName: 'Mr. Rajesh Sharma', productName: 'HDFC Business Loan',
    meetingType: 'First Meeting',
    customerProfile: '45-year-old owner of a textile shop in Surat, ~Rs. 6 crore annual turnover, currently banks with SBI. Sharp negotiator, values speed.',
    goal: 'Get him interested in a Rs. 40L working-capital loan and book a follow-up.',
    context: 'He has an export order to fulfil in 5 weeks. Worried about processing fees and disbursal time.',
  },
  {
    customerName: 'Dr. Ananya Reddy', productName: 'HDFC Personal Loan',
    meetingType: 'Follow-up',
    customerProfile: 'Pediatrician in Hyderabad, runs an evening clinic, ~Rs. 3L/month income. Organised, asks precise questions.',
    goal: 'Move her from interest to application for a Rs. 10L clinic-renovation loan.',
    context: 'Wants the work done before monsoon. Cares about tenure flexibility and prepayment.',
  },
  {
    customerName: 'Mr. Suresh Iyer', productName: 'HDFC Personal Loan',
    meetingType: 'First Meeting',
    customerProfile: 'Government school teacher in Coimbatore, 25 years of service, ~Rs. 70k/month. Careful, EMI-conscious.',
    goal: 'Reassure him on EMI affordability and get him to start the Rs. 6L education loan.',
    context: 'Needs funds for his son’s engineering admission in 4 weeks. Nervous about hidden charges.',
  },
  {
    customerName: 'Mrs. Kavita Reddy', productName: 'HDFC Business Loan',
    meetingType: 'Product Demo',
    customerProfile: 'Founder of a fashion boutique + online label in Bangalore, ~Rs. 2 crore turnover. Data-driven, compares offers.',
    goal: 'Show a competitive all-in cost vs Bajaj and get a verbal yes on Rs. 18L.',
    context: 'Signing a lease for a second store in a month. Wants fast disbursal and easy prepayment.',
  },
  {
    customerName: 'Mr. Pradeep Kumar', productName: 'HDFC Personal Loan',
    meetingType: 'Objection Handling',
    customerProfile: 'Senior railways clerk in Lucknow, near retirement, ~Rs. 90k/month. Risk-averse, banks with SBI, assumes a catch.',
    goal: 'Overcome his "what is the catch" objection and close a Rs. 8L renovation loan.',
    context: 'Contractor starts in 6 weeks. Keeps asking about foreclosure charges and rate changes.',
  },
  {
    customerName: 'Mr. Amit Patel', productName: 'HDFC Car Loan',
    meetingType: 'Closing Meeting',
    customerProfile: 'Self-employed CA in Ahmedabad, variable income, tech-savvy and impatient. Hates paperwork.',
    goal: 'Close the car loan today and get documents submitted digitally.',
    context: 'Comparing with an ICICI offer. Wants the whole process online and quick.',
  },
  {
    customerName: 'Mrs. Meena Gupta', productName: 'HDFC Personal Loan',
    meetingType: 'First Meeting',
    customerProfile: 'Homemaker in Jaipur, first-time borrower, relies on husband’s income. Warm but easily confused by jargon.',
    goal: 'Build trust and explain eligibility simply for a Rs. 3L loan.',
    context: 'Needs money for home appliances and a family function. Keeps asking "is this safe?".',
  },
  {
    customerName: 'Mr. Vikram Singh', productName: 'HDFC Home Loan',
    meetingType: 'Follow-up',
    customerProfile: 'IT manager in Pune, ~Rs. 1.8L/month, stable salaried. Well-researched online, knows current rates.',
    goal: 'Convert his research into a Rs. 60L home-loan application this week.',
    context: 'Has a property shortlisted. Comparing HDFC rate and processing fee with Axis.',
  },
];

function QuickForm({ onGenerate, loading }) {
  const [form, setForm] = useState({
    customerName: '', productName: '', meetingType: 'First Meeting',
    customerProfile: '', goal: '', context: '', language: 'English'
  });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  // Fill the form with a random sample pitch (user can then edit any field).
  const fillRandom = () => {
    const pick = RANDOM_PITCHES[Math.floor(Math.random() * RANDOM_PITCHES.length)];
    setForm(f => ({ ...f, ...pick }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    const scenario = `Customer: ${form.customerName}\nProduct/Service: ${form.productName}\nMeeting Type: ${form.meetingType}\nCustomer Profile: ${form.customerProfile}\nGoal: ${form.goal}\nAdditional Context: ${form.context}`;
    onGenerate(scenario, form.language);
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 14 }}>
        <button type="button" className="btn btn-primary" onClick={fillRandom} disabled={loading}>
          <Sparkles size={14} /> Generate sample pitch
        </button>
      </div>

      <div className="form-grid-2" style={{ marginBottom: 16 }}>
        <div>
          <label className="form-label">Customer Name</label>
          <input className="form-input" type="text" placeholder="e.g. Mr. Sharma"
            value={form.customerName} onChange={e => set('customerName', e.target.value)} required />
        </div>
        <div>
          <label className="form-label">Product / Service</label>
          <input className="form-input" type="text" placeholder="e.g. HDFC Business Loan"
            value={form.productName} onChange={e => set('productName', e.target.value)} required />
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">Meeting Type</label>
        <select className="form-select" value={form.meetingType} onChange={e => set('meetingType', e.target.value)}>
          {MEETING_TYPES.map(t => <option key={t}>{t}</option>)}
        </select>
      </div>

      <div className="form-group">
        <label className="form-label">Customer Profile</label>
        <textarea className="form-textarea"
          placeholder="e.g. 45 year old business owner, runs a textile shop, current bank is SBI..."
          value={form.customerProfile} onChange={e => set('customerProfile', e.target.value)} rows={3} />
      </div>

      <div className="form-group">
        <label className="form-label">Your Goal</label>
        <textarea className="form-textarea"
          placeholder="e.g. Get him interested in an overdraft facility for working capital..."
          value={form.goal} onChange={e => set('goal', e.target.value)} rows={2} />
      </div>

      <div className="form-group">
        <label className="form-label">Special Context (optional)</label>
        <textarea className="form-textarea"
          placeholder="e.g. He was concerned about interest rates last time, his business is seasonal..."
          value={form.context} onChange={e => set('context', e.target.value)} rows={2} />
      </div>

      <div className="form-group">
        <label className="form-label"><Languages size={11} style={{ display: 'inline', marginRight: 4, verticalAlign: -1 }} /> Language</label>
        <select className="form-select" value={form.language} onChange={e => set('language', e.target.value)}>
          {LANGUAGES.map(l => <option key={l}>{l}</option>)}
        </select>
      </div>

      <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={loading}>
        {loading
          ? <><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> Generating Pitch…</>
          : <><Sparkles size={16} /> Generate Pitch</>}
      </button>
    </form>
  );
}

// ── Free Write Form ──────────────────────────────────────────────────────────

function FreeWriteForm({ onGenerate, loading, seedScenario, onSeedConsumed }) {
  const [scenario, setScenario] = useState('');
  const [language, setLanguage] = useState('English');

  // Consume an external seed (from Ask Me chat → Generate Pitch flow)
  useEffect(() => {
    if (seedScenario && !scenario) {
      setScenario(seedScenario);
      onSeedConsumed?.();
    }
  }, [seedScenario]);

  const handleSubmit = (e) => {
    e.preventDefault();
    onGenerate(scenario, language);
  };

  return (
    <form onSubmit={handleSubmit}>
      <div className="form-group">
        <label className="form-label">Describe Your Scenario</label>
        <textarea
          className="form-textarea"
          value={scenario}
          onChange={e => setScenario(e.target.value)}
          placeholder="Write your complete scenario here... e.g. I have to meet Mr. Sharma today, he is a 45 year old business owner, I want to pitch HDFC Business Loan to him, his current bank is SBI, he seemed interested last time but worried about interest rates..."
          rows={9}
          required
        />
      </div>

      <div className="form-group">
        <label className="form-label"><Languages size={11} style={{ display: 'inline', marginRight: 4, verticalAlign: -1 }} /> Language</label>
        <select className="form-select" value={language} onChange={e => setLanguage(e.target.value)}>
          {LANGUAGES.map(l => <option key={l}>{l}</option>)}
        </select>
      </div>

      <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={loading || !scenario.trim()}>
        {loading
          ? <><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> Generating Pitch…</>
          : <><Sparkles size={16} /> Generate Pitch</>}
      </button>
    </form>
  );
}

// ── Ask Me Chat Panel ────────────────────────────────────────────────────────

function summariseChatForPitch(messages) {
  // Pull out the most useful customer-context tidbits from the chat to seed a pitch.
  const userTurns = messages.filter(m => m.role === 'user').map(m => m.text);
  if (!userTurns.length) return '';
  const bullets = userTurns.slice(-6).map(t => `- ${t}`).join('\n');
  return (
    `Customer scenario (extracted from Ask Me chat):\n${bullets}\n\n` +
    `Use the above as the customer context. Build a pitch that addresses these specific questions and concerns.`
  );
}

// ── Voice input (batch STT via local Whisper /api/voice/stt) ─────────────────
// Flow: tap mic → MediaRecorder captures the full utterance → tap ✓ → the whole
// blob is POSTed to the backend Whisper endpoint → transcript auto-sent to chat.
// Batch beats streaming here: one round-trip, no websocket, and large-v3-turbo
// gets full-utterance context (better Hindi/Marathi accuracy than chunked STT).

const VOICE_MAX_SECONDS = 120;          // auto-stop cap so a forgotten mic can't grow unbounded
const VOICE_MIN_MS = 500;               // taps shorter than this are almost never real speech
const STT_LANG_HINTS = { English: 'en', Hindi: 'hi', Marathi: 'mr' }; // others → Whisper auto-detect

function pickRecorderMime() {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
  const candidates = [
    'audio/webm;codecs=opus',  // Chrome / Edge / Firefox
    'audio/webm',
    'audio/mp4',               // Safari
    'audio/ogg;codecs=opus',
  ];
  return candidates.find(m => MediaRecorder.isTypeSupported(m)) || '';
}

function useVoiceRecorder({ language = 'English', onTranscript }) {
  const [phase, setPhase] = useState('idle');   // idle | recording | transcribing
  const [elapsed, setElapsed] = useState(0);    // seconds while recording
  const [error, setError] = useState('');
  const [speaking, setSpeaking] = useState(false);      // true only while voice is detected (drives the blinking icon)
  const [liveTranscript, setLiveTranscript] = useState('');  // interim transcript shown while still recording

  const recRef = useRef(null);        // MediaRecorder
  const streamRef = useRef(null);     // MediaStream (to stop tracks)
  const chunksRef = useRef([]);
  const discardRef = useRef(false);   // true → onstop throws the audio away
  const startedAtRef = useRef(0);
  const tickRef = useRef(null);
  const errTimerRef = useRef(null);
  const aliveRef = useRef(true);
  const audioCtxRef = useRef(null);   // AudioContext for the level meter
  const analyserRef = useRef(null);
  const interimTimerRef = useRef(null);   // periodic partial-transcription loop
  const interimBusyRef = useRef(false);
  const interimCtrlRef = useRef(null);
  // Keep latest callbacks/props without re-wiring recorder events on every render
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;
  const languageRef = useRef(language);
  languageRef.current = language;

  const releaseStream = () => {
    try { streamRef.current?.getTracks().forEach(t => t.stop()); } catch { /* already stopped */ }
    streamRef.current = null;
    try { audioCtxRef.current?.close(); } catch { /* already closed */ }
    audioCtxRef.current = null;
    analyserRef.current = null;
  };

  const stopInterimLoop = () => {
    clearInterval(interimTimerRef.current);
    interimTimerRef.current = null;
    try { interimCtrlRef.current?.abort(); } catch { /* no request in flight */ }
    interimCtrlRef.current = null;
  };

  useEffect(() => {
    aliveRef.current = true;   // re-arm after StrictMode's dev mount→unmount→remount cycle
    return () => {             // unmount: kill timers, recorder and mic light
      aliveRef.current = false;
      clearInterval(tickRef.current);
      clearTimeout(errTimerRef.current);
      stopInterimLoop();
      discardRef.current = true;
      try { if (recRef.current && recRef.current.state !== 'inactive') recRef.current.stop(); } catch { /* ignore */ }
      releaseStream();
    };
  }, []);

  const showError = (msg) => {
    if (!aliveRef.current) return;
    setError(msg);
    clearTimeout(errTimerRef.current);
    errTimerRef.current = setTimeout(() => { if (aliveRef.current) setError(''); }, 5000);
  };

  const transcribe = async (blob) => {
    if (!blob || blob.size < 1024) {
      setPhase('idle');
      showError('No audio captured — please try again.');
      return;
    }
    setPhase('transcribing');
    const type = blob.type || 'audio/webm';
    const ext = type.includes('mp4') ? 'mp4' : type.includes('ogg') ? 'ogg' : type.includes('wav') ? 'wav' : 'webm';
    const fd = new FormData();
    fd.append('file', blob, `voice-input.${ext}`);
    fd.append('language', STT_LANG_HINTS[languageRef.current] || 'auto');
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 60000);
    try {
      const res = await fetch(`${API}/api/voice/stt`, { method: 'POST', body: fd, signal: ctrl.signal });
      let data = {};
      try { data = await res.json(); } catch { /* non-JSON error body */ }
      if (!res.ok || !data.success) {
        throw new Error(data.error || data.detail || `Transcription failed (${res.status})`);
      }
      const text = (data.transcript || '').trim();
      if (!aliveRef.current) return;
      if (!text) { showError('No speech detected — please try again closer to the mic.'); return; }
      onTranscriptRef.current?.(text);
    } catch (err) {
      if (!aliveRef.current) return;
      showError(err?.name === 'AbortError'
        ? 'Transcription timed out — please try again.'
        : shortError(err?.message || 'Transcription failed.'));
    } finally {
      clearTimeout(timeout);
      if (aliveRef.current) { setPhase('idle'); setLiveTranscript(''); }
    }
  };

  const finishRecording = (discard) => {
    clearInterval(tickRef.current);
    stopInterimLoop();
    if (aliveRef.current) {
      setSpeaking(false);
      if (discard) setLiveTranscript('');
    }
    discardRef.current = discard;
    const rec = recRef.current;
    if (rec && rec.state !== 'inactive') {
      try { rec.stop(); return; } catch { /* fall through to hard cleanup */ }
    }
    releaseStream();
    if (aliveRef.current) setPhase('idle');
  };

  const start = async () => {
    if (recRef.current && recRef.current.state === 'recording') return;
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      showError(window.isSecureContext === false
        ? 'Voice input needs a secure (https) connection.'
        : 'Voice input is not supported in this browser.');
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const name = err?.name || '';
      showError(
        name === 'NotAllowedError' || name === 'SecurityError'
          ? 'Microphone access is blocked. Allow the mic in your browser and try again.'
          : name === 'NotFoundError' || name === 'OverconstrainedError'
            ? 'No microphone was found on this device.'
            : name === 'NotReadableError'
              ? 'The microphone is busy in another app.'
              : shortError(err?.message || 'Could not access the microphone.'));
      return;
    }
    if (!aliveRef.current) { stream.getTracks().forEach(t => t.stop()); return; }
    streamRef.current = stream;

    const mime = pickRecorderMime();
    let rec;
    try {
      rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    } catch {
      try { rec = new MediaRecorder(stream); } catch {
        releaseStream();
        showError('Could not start the audio recorder.');
        return;
      }
    }
    chunksRef.current = [];
    discardRef.current = false;
    rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
    rec.onerror = () => {
      clearInterval(tickRef.current);
      discardRef.current = true;
      try { if (rec.state !== 'inactive') rec.stop(); } catch { /* ignore */ }
      releaseStream();
      if (aliveRef.current) { setPhase('idle'); showError('Recording failed — please try again.'); }
    };
    rec.onstop = () => {
      releaseStream();
      clearInterval(tickRef.current);
      const parts = chunksRef.current;
      chunksRef.current = [];
      recRef.current = null;
      if (discardRef.current || !aliveRef.current) {
        if (aliveRef.current) setPhase('idle');
        return;
      }
      transcribe(new Blob(parts, { type: rec.mimeType || mime || 'audio/webm' }));
    };

    // Level meter — drives the blinking "someone is speaking" indicator.
    // Optional: if AudioContext fails, recording still works without it.
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      const srcNode = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      srcNode.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
    } catch { /* no level meter — icon just stays steady */ }

    recRef.current = rec;
    startedAtRef.current = Date.now();
    setElapsed(0);
    setLiveTranscript('');
    setPhase('recording');
    rec.start(250);
    tickRef.current = setInterval(() => {
      const secs = Math.floor((Date.now() - startedAtRef.current) / 1000);
      if (aliveRef.current) setElapsed(secs);
      // RMS of the waveform → "speaking" only while there is actual voice energy
      const an = analyserRef.current;
      if (an && aliveRef.current) {
        const buf = new Uint8Array(an.fftSize);
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
        setSpeaking(Math.sqrt(sum / buf.length) > 0.02);
      }
      if (secs >= VOICE_MAX_SECONDS) {   // hard cap: stop & transcribe what we have
        if (aliveRef.current) setPhase('transcribing');
        finishRecording(false);
      }
    }, 150);

    // Live transcript — every 3s send the audio recorded SO FAR to Whisper and
    // show the partial text in the chat. Best-effort: a failed interim pass is
    // silently skipped; the authoritative transcription still happens on stop.
    interimTimerRef.current = setInterval(async () => {
      if (interimBusyRef.current || !chunksRef.current.length) return;
      const blob = new Blob(chunksRef.current.slice(), { type: rec.mimeType || mime || 'audio/webm' });
      if (blob.size < 2048) return;
      interimBusyRef.current = true;
      const ctrl = new AbortController();
      interimCtrlRef.current = ctrl;
      try {
        const fd = new FormData();
        fd.append('file', blob, 'voice-live.webm');
        fd.append('language', STT_LANG_HINTS[languageRef.current] || 'auto');
        const res = await fetch(`${API}/api/voice/stt`, { method: 'POST', body: fd, signal: ctrl.signal });
        const d = await res.json().catch(() => ({}));
        // Only show it if this exact take is still being recorded
        if (aliveRef.current && d.success && recRef.current === rec && rec.state === 'recording') {
          const t = (d.transcript || '').trim();
          if (t) setLiveTranscript(t);
        }
      } catch { /* interim preview is best-effort */ }
      finally {
        interimBusyRef.current = false;
        interimCtrlRef.current = null;
      }
    }, 3000);
  };

  const stop = () => {
    if (!recRef.current || recRef.current.state !== 'recording') return;
    if (Date.now() - startedAtRef.current < VOICE_MIN_MS) {
      finishRecording(true);
      showError('Recording was too short — tap the mic, then speak.');
      return;
    }
    setPhase('transcribing');
    finishRecording(false);
  };

  const cancel = () => finishRecording(true);

  return { phase, elapsed, error, speaking, liveTranscript, start, stop, cancel };
}

function formatRecClock(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function ChatPanel({
  navigate, onSeedPitch,
  sessionId, onSessionUpdated, onNewChat, sessionName,
  onOpenSidebar,
  compact = false, onClose, onExpand, onSessionNameLoaded,
}) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [chatLanguage, setChatLanguage] = useState('English');
  const [fabVoice, setFabVoice] = useState(false); // compact FAB: Chat vs Voice mode
  const [showLearnings, setShowLearnings] = useState(false);
  const [loadingLearnings, setLoadingLearnings] = useState(false);
  const [learnings, setLearnings] = useState('');
  const [learningsError, setLearningsError] = useState('');
  const chatBodyRef = useRef(null);
  const textareaRef = useRef(null);

  // When sessionId changes (user clicks a session in the sidebar OR starts new),
  // hydrate the messages array from the backend.
  useEffect(() => {
    let cancelled = false;
    if (!sessionId) { setMessages([]); return; }
    setLoadingSession(true);
    fetch(`${API}/api/chat/sessions/${sessionId}`)
      .then(r => r.json())
      .then(d => {
        if (cancelled || !d.success || !d.session) return;
        const hydrated = d.session.messages.map(m => ({
          role: m.role === 'user' ? 'user' : 'ai',
          text: m.content,
          sources: m.sources || [],
        }));
        setMessages(hydrated);
        if (d.session.name) onSessionNameLoaded?.(d.session.name);
      })
      .catch(() => { })
      .finally(() => { if (!cancelled) setLoadingSession(false); });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    const el = chatBodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  // `overrideText` lets voice input inject a transcript straight into the same
  // send path (button onClick passes the click event, hence the typeof check).
  const handleSend = async (overrideText) => {
    const fromVoice = typeof overrideText === 'string';
    const q = (fromVoice ? overrideText : input).trim();
    if (!q || loading) return;
    if (!fromVoice) setInput('');
    setMessages(prev => [...prev, { role: 'user', text: q, sources: [] }]);
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, session_id: sessionId || null, language: chatLanguage }),
      });
      if (!res.ok) {
        const errText = res.status === 500
          ? 'Server error — the backend may be busy. Try again.'
          : res.status === 404
            ? 'API endpoint not found. Check the server is running.'
            : `Request failed (${res.status})`;
        setMessages(prev => [...prev, { role: 'ai', text: errText, sources: [] }]);
        return;
      }

      // Stream tokens — show text as it arrives
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let streamText = '';
      let finalSources = [];

      // Add placeholder so the typing bubble disappears immediately on first token
      setLoading(false);
      setMessages(prev => [...prev, { role: 'ai', text: '', sources: [] }]);

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === 'token') {
            streamText += evt.text;
            setMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { role: 'ai', text: streamText, sources: [] };
              return msgs;
            });
          } else if (evt.type === 'done') {
            finalSources = evt.sources || [];
            setMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { role: 'ai', text: evt.answer, sources: finalSources };
              return msgs;
            });
            if (evt.session_id) onSessionUpdated?.(evt.session_id, evt.session_name);
            break outer;
          } else if (evt.type === 'error') {
            setMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { role: 'ai', text: shortError(evt.detail || 'Unknown error'), sources: [] };
              return msgs;
            });
            break outer;
          }
        }
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'ai', text: shortError(err.message), sources: [] }]);
    } finally {
      setLoading(false);
    }
  };

  const voice = useVoiceRecorder({
    language: chatLanguage,
    onTranscript: (text) => handleSend(text),
  });

  // Keep the live voice bubble in view as the interim transcript grows
  useEffect(() => {
    const el = chatBodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [voice.liveTranscript, voice.phase]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewChat = () => {
    setMessages([]);
    setInput('');
    onNewChat?.();
  };

  return (
    <div className="chat-container">
      <div className="chat-header">
        {onOpenSidebar && (
          <button className="icon-btn chatgpt-menu-btn" onClick={onOpenSidebar} title="Chat sessions">
            <Menu size={18} />
          </button>
        )}
        <div className="chat-header-title">
          {compact
            ? <img src="/chatbot-fab.png" alt="" style={{ width: 24, height: 24, objectFit: 'contain', flexShrink: 0 }} draggable={false} />
            : <MessageSquare size={18} style={{ color: 'var(--color-red)' }} />}
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
            <span style={{
              flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }} title={sessionName || (compact ? 'Eva 2.0' : 'AI Mentor')}>
              {sessionId ? (sessionName || '…') : (compact ? 'Eva 2.0' : 'AI Mentor')}
            </span>
            {!compact && <span className="chat-badge" style={{ flexShrink: 0 }}>DOC-GROUNDED</span>}
          </span>
        </div>
        <select
          className="form-select"
          data-tour={compact ? undefined : 'chat-language'}
          value={chatLanguage}
          onChange={e => setChatLanguage(e.target.value)}
          style={{ padding: '5px 8px', fontSize: 12, width: 'auto', minWidth: 90 }}
        >
          {LANGUAGES.map(l => <option key={l}>{l}</option>)}
        </select>
        {!compact && (
          <button
            className="btn btn-secondary"
            data-tour="chat-learnings"
            onClick={async () => {
              if (loadingLearnings) return;
              setLoadingLearnings(true);
              setLearningsError('');
              try {
                if (!sessionId) {
                  setLearnings("There isn't enough in this conversation yet to draw key learnings — keep the chat going and I'll have takeaways once you've discussed the customer's need.");
                } else {
                  const res = await fetch(`${API}/api/chat/sessions/${sessionId}/key-learnings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ language: chatLanguage }),
                  });
                  const d = await res.json().catch(() => ({}));
                  if (!res.ok || !d.success) throw new Error(d.detail || `Request failed (${res.status})`);
                  setLearnings(d.learnings || '');
                }
                setShowLearnings(true);
              } catch (err) {
                setLearningsError(shortError(err.message));
                setShowLearnings(true);
              } finally {
                setLoadingLearnings(false);
              }
            }}
            style={{ padding: '6px 12px', fontSize: 12, marginRight: 8 }}
            disabled={loadingLearnings}
          >
            {loadingLearnings ? <span className="spinner" style={{ width: 13, height: 13, borderWidth: 2 }} /> : <Lightbulb size={13} />}
            {loadingLearnings ? ' Generating...' : ' Key Learnings'}
          </button>
        )}
        <button className="btn btn-secondary" onClick={handleNewChat}
          title="New Chat"
          data-tour={compact ? undefined : 'chat-new'}
          style={{ padding: '6px 12px', fontSize: 12 }}>
          <Sparkles size={13} />{compact ? null : ' New Chat'}
        </button>
        {compact && (
          <>
            <button className="icon-btn" onClick={onExpand} title="Open in AI Mentor page">
              <ExternalLink size={15} />
            </button>
            <button className="icon-btn" onClick={onClose} title="Close assistant">
              <X size={16} />
            </button>
          </>
        )}
      </div>

      {compact && (
        <div className="fab-mode-toggle">
          <button className={`fab-mode-btn${!fabVoice ? ' on' : ''}`} onClick={() => setFabVoice(false)}>
            <MessageSquare size={13} /> Chat
          </button>
          <button className={`fab-mode-btn${fabVoice ? ' on' : ''}`} onClick={() => setFabVoice(true)}>
            <Mic size={13} /> Voice
          </button>
        </div>
      )}

      {compact && fabVoice ? (
        <div className="fab-voice-wrap"><EvaVoice compact /></div>
      ) : (
        <>
      <div className="chat-messages" ref={chatBodyRef}>
        {messages.length === 0 && !loading && voice.phase === 'idle' ? (
          compact ? (
            <div className="chat-empty fab-empty">
              <img src="/chatbot-fab.png" alt="" className="fab-empty-mascot" draggable={false} />
              <div className="chat-empty-title">Hi! I'm your AI assistant</div>
              <div className="chat-empty-hint">Products · Rates · Eligibility · Documents</div>
              <div className="fab-quick-chips">
                {[
                  'Personal loan interest rates',
                  'Documents needed for a personal loan',
                  'Compare home loan vs personal loan',
                ].map(q => (
                  <button key={q} className="fab-quick-chip" onClick={() => handleSend(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="chat-empty">
              <div className="chat-empty-icon"><Search size={26} /></div>
              <div className="chat-empty-title">Ask anything from your documents</div>
              <div className="chat-empty-hint">Compare products · Look up rates · Summarise schemes</div>
              <div className="chat-empty-hint" style={{ marginTop: 4 }}>
                Answers are strictly grounded in your uploaded files
              </div>
            </div>
          )
        ) : (
          messages.map((msg, i) => (
            <div key={i}>
              <div className={`chat-row ${msg.role}`}>
                <div className={`chat-avatar ${msg.role}`}>
                  {msg.role === 'ai' ? <Bot size={16} /> : <User size={16} />}
                </div>
                <div className="chat-bubble">
                  {msg.role === 'ai' ? <ChatMarkdown text={msg.text} /> : msg.text}
                </div>
              </div>
              {msg.role === 'ai' && msg.sources?.length > 0 && (
                <div className="chat-sources">
                  {msg.sources.map((s, j) => (
                    <span key={j} className="chat-source-pill"><FileCheck size={10} /> {s}</span>
                  ))}
                </div>
              )}
            </div>
          ))
        )}

        {/* Live voice bubble — shows what the mic is hearing, while it hears it */}
        {(voice.phase === 'recording' || voice.phase === 'transcribing') && (
          <div className="chat-row user">
            <div className={`chat-avatar user voice-live-avatar${voice.speaking ? ' speaking' : ''}`}>
              <Mic size={16} />
            </div>
            <div className="chat-bubble voice-live-bubble">
              {voice.liveTranscript || (voice.phase === 'recording' ? 'Listening…' : 'Transcribing…')}
              <span className="voice-caret" />
            </div>
          </div>
        )}

        {loading && (
          <div className="chat-row ai">
            <div className="chat-avatar ai"><Bot size={16} /></div>
            <div className="chat-typing">
              <div className="chat-typing-dot" />
              <div className="chat-typing-dot" />
              <div className="chat-typing-dot" />
            </div>
          </div>
        )}

        {!loading && messages.filter(m => m.role === 'user').length >= 5
          && messages.length > 0
          && messages[messages.length - 1]?.role === 'ai' && (
            <div className="chat-suggestion fade-in">
              <Sparkles size={14} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>
                  Want me to generate a full pitch based on what we just discussed?
                </div>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                  I'll pre-fill the pitch generator with the customer details from this chat.
                </div>
              </div>
              <button
                className="btn btn-primary"
                style={{ padding: '8px 14px', fontSize: 13 }}
                onClick={() => {
                  const seed = summariseChatForPitch(messages);
                  onSeedPitch?.(seed);
                  navigate?.('generator');
                }}
              >
                <Sparkles size={13} /> Generate Pitch
              </button>
            </div>
          )}

      </div>

      <div className="chat-input-bar">
        {voice.error && (
          <div className="voice-error" role="alert">
            <AlertTriangle size={13} style={{ flexShrink: 0 }} />
            {voice.error}
          </div>
        )}
        {voice.phase === 'recording' ? (
          <>
            <div className="voice-rec-bar">
              <Mic size={15} className={`voice-rec-mic${voice.speaking ? ' speaking' : ''}`} />
              <span className={`voice-rec-dot${voice.speaking ? ' speaking' : ''}`} />
              <span className="voice-rec-time">{formatRecClock(voice.elapsed)}</span>
              <span className="voice-rec-hint">
                {voice.speaking ? 'Hearing you…' : 'Listening…'} tap ✓ to transcribe &amp; send
              </span>
            </div>
            <button className="chat-mic voice-cancel" onClick={voice.cancel} title="Discard recording">
              <X size={16} />
            </button>
            <button className="chat-send" onClick={voice.stop} title="Stop, transcribe & send">
              <Check size={16} />
            </button>
          </>
        ) : (
          <>
            <textarea
              ref={textareaRef}
              className="chat-input"
              rows={1}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={voice.phase === 'transcribing'
                ? 'Transcribing your voice…'
                : compact ? 'Ask a question…' : 'Ask a question…  (Shift+Enter for newline)'}
              disabled={loading || voice.phase === 'transcribing'}
            />
            <button
              className={`chat-mic${voice.phase === 'transcribing' ? ' busy' : ''}`}
              onClick={voice.start}
              disabled={loading || voice.phase === 'transcribing'}
              title={voice.phase === 'transcribing' ? 'Transcribing…' : 'Ask by voice'}
            >
              {voice.phase === 'transcribing'
                ? <span className="spinner" style={{ width: 15, height: 15, borderWidth: 2 }} />
                : <Mic size={16} />}
            </button>
            <button className="chat-send" onClick={handleSend} disabled={loading || voice.phase === 'transcribing' || !input.trim()} title="Send (Enter)">
              <Send size={16} />
            </button>
          </>
        )}
      </div>

      <div className="chat-footnote">
        Note: In case any issues encountered, stuck or experience a technical glitch, please refresh the page and try again or start new chat
      </div>
        </>
      )}

      {showLearnings && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 99999, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }} onClick={() => setShowLearnings(false)}>
          <div style={{ background: 'var(--color-bg)', borderRadius: 12, width: '100%', maxWidth: 600, maxHeight: '90vh', overflowY: 'auto', padding: 24, boxShadow: '0 20px 40px rgba(0,0,0,0.2)' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header" style={{ borderBottom: '1px solid var(--color-border)', paddingBottom: 16, marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ background: 'var(--color-bg-secondary)', padding: 8, borderRadius: 8, color: 'var(--color-primary)' }}>
                  <Lightbulb size={20} />
                </div>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text)' }}>AI Sales Coaching</div>
                  <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Key learnings extracted from this conversation</div>
                </div>
              </div>
            </div>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {learningsError ? (
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', color: 'var(--color-red)', fontSize: 13 }}>
                  <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>Couldn't generate key learnings: {learningsError}</span>
                </div>
              ) : (
                <div style={{ fontSize: 14, color: 'var(--color-text)' }}>
                  <ChatMarkdown text={learnings} />
                </div>
              )}
            </div>

            <div style={{ marginTop: 24, display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowLearnings(false)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── HISTORY PAGE (ChatGPT-style conversations) ───────────────────────────────

function InlineRename({ value, onSave, onCancel, large = false }) {
  const [text, setText] = useState(value);
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const commit = () => {
    const trimmed = text.trim();
    if (trimmed && trimmed !== value) onSave(trimmed);
    else onCancel();
  };

  return (
    <input
      ref={inputRef}
      type="text"
      className={large ? 'conv-name-input' : 'inline-name-input'}
      value={text}
      onChange={e => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={e => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
      }}
      onClick={e => e.stopPropagation()}
    />
  );
}

function HistoryListItem({ entry, active, onOpen, onRename, onDelete }) {
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const meta = entry.metadata || {};

  return (
    <div className={`history-row${active ? ' active' : ''}`} onClick={() => !renaming && !confirmingDelete && onOpen(entry.id)}>
      <div className="history-row-content">
        {renaming ? (
          <InlineRename
            value={entry.name}
            onSave={(name) => { onRename(entry.id, name); setRenaming(false); }}
            onCancel={() => setRenaming(false)}
          />
        ) : (
          <div className="history-row-name" title={entry.name}>{entry.name}</div>
        )}
        <div className="history-row-subtitle">
          <span>{formatTime(entry.updated_at || entry.created_at)}</span>
          {meta.customerName && (<><span className="sep">·</span><span>{meta.customerName}</span></>)}
          {meta.product && (<><span className="sep">·</span><span>{meta.product}</span></>)}
          {entry.message_count > 2 && (
            <><span className="sep">·</span><span style={{ color: 'var(--color-red)', fontWeight: 600 }}>
              {entry.message_count - 2} reply{entry.message_count - 2 !== 1 ? 'ies' : ''}
            </span></>
          )}
        </div>
        {entry.preview && <div className="history-row-preview">{entry.preview}</div>}
      </div>

      {confirmingDelete ? (
        <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
          <span>Delete?</span>
          <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: 12 }}
            onClick={() => setConfirmingDelete(false)}>Cancel</button>
          <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: 12 }}
            onClick={() => { onDelete(entry.id); setConfirmingDelete(false); }}>Delete</button>
        </div>
      ) : (
        <div className="history-row-actions" onClick={e => e.stopPropagation()}>
          <button className="icon-btn" title="Rename" onClick={() => setRenaming(true)}>
            <Pencil size={14} />
          </button>
          <button className="icon-btn danger" title="Delete" onClick={() => setConfirmingDelete(true)}>
            <Trash2 size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

function ChatBubble({ msg }) {
  if (msg.role === 'user') {
    return (
      <div>
        <div className="chat-row user">
          <div className="chat-avatar user"><User size={16} /></div>
          <div className="chat-bubble">{msg.content}</div>
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="chat-row ai">
        <div className="chat-avatar ai"><Bot size={16} /></div>
        <div className="chat-bubble">{msg.content}</div>
      </div>
      {msg.sources?.length > 0 && (
        <div className="chat-sources">
          {msg.sources.map((s, i) => (
            <span key={i} className="chat-source-pill"><FileCheck size={10} /> {s}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function ConversationView({ convId, onBack, onRenamed, onDeleted, showToast, navigate }) {
  const [conv, setConv] = useState(null);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [expandAllPitch, setExpandAllPitch] = useState(true);
  const feedEndRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${API}/api/pitch/history/${convId}`)
      .then(r => r.json())
      .then(d => { if (!cancelled && d.success) setConv(d.conversation); })
      .catch(() => { })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [convId]);

  useEffect(() => {
    const end = feedEndRef.current;
    if (!end) return;
    // Find the nearest scrollable ancestor (the .main container) and only
    // auto-scroll if the user is already near the bottom — don't hijack
    // the scroll when they've intentionally scrolled up to read earlier content.
    let parent = end.parentElement;
    while (parent) {
      const { overflowY } = window.getComputedStyle(parent);
      if ((overflowY === 'auto' || overflowY === 'scroll') && parent.scrollHeight > parent.clientHeight) {
        const distFromBottom = parent.scrollHeight - parent.scrollTop - parent.clientHeight;
        if (distFromBottom < 300) parent.scrollTop = parent.scrollHeight;
        return;
      }
      parent = parent.parentElement;
    }
  }, [conv?.messages?.length, sending]);

  const handleRename = async (newName) => {
    try {
      const res = await fetch(`${API}/api/pitch/history/${convId}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      const d = await res.json();
      if (d.success) {
        setConv(d.conversation);
        onRenamed?.();
        showToast?.('Renamed', 'success');
      }
    } catch { showToast?.('Rename failed', 'error'); }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setSending(true);
    const ts = new Date().toISOString();
    setConv(c => ({ ...c, messages: [...c.messages, { role: 'user', content: text, type: 'chat', timestamp: ts }] }));

    try {
      const res = await fetch(`${API}/api/pitch/history/${convId}/continue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: 'user', content: text, type: 'chat' }),
      });
      const d = await res.json();
      if (d.conversation) {
        setConv(d.conversation);
        onRenamed?.(); // refresh list to update updated_at sort
      }
    } catch (e) {
      setConv(c => ({
        ...c,
        messages: [...c.messages, { role: 'assistant', content: `Error: ${shortError(e.message)}`, type: 'chat', timestamp: new Date().toISOString() }]
      }));
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete conversation "${conv?.name}"?`)) return;
    try {
      await fetch(`${API}/api/pitch/history/${convId}`, { method: 'DELETE' });
      onDeleted?.();
      onBack();
      showToast?.('Conversation deleted', 'success');
    } catch { showToast?.('Delete failed', 'error'); }
  };

  if (loading) {
    return (
      <div className="loading-card">
        <div className="spinner" />
        <div className="loading-card-title">Loading conversation…</div>
      </div>
    );
  }
  if (!conv) {
    return (
      <div className="card card-padded">
        <div className="history-empty">
          <div style={{ fontSize: 13 }}>Conversation not found.</div>
          <button className="btn btn-secondary" style={{ marginTop: 12 }} onClick={onBack}>
            <ArrowLeft size={14} /> Back to history
          </button>
        </div>
      </div>
    );
  }

  const meta = conv.metadata || {};
  // First two messages are scenario + pitch — render pitch specially; subsequent are chat
  const chatMessages = (conv.messages || []).slice(2);

  return (
    <div className="conv-shell slide-up">
      <div className="conv-header">
        <div style={{ flex: 1, minWidth: 0 }}>
          <button className="btn btn-ghost" onClick={onBack} style={{ marginBottom: 8, padding: '4px 8px' }}>
            <ArrowLeft size={14} /> Back to History
          </button>
          <div className="conv-name-wrap">
            {renaming ? (
              <InlineRename
                value={conv.name}
                large
                onSave={(name) => { handleRename(name); setRenaming(false); }}
                onCancel={() => setRenaming(false)}
              />
            ) : (
              <>
                <span className="conv-name" title={conv.name}>{conv.name}</span>
                <button className="icon-btn" title="Rename" onClick={() => setRenaming(true)}>
                  <Pencil size={15} />
                </button>
              </>
            )}
          </div>
          <div className="conv-meta">
            <span>Started {formatTime(conv.created_at)}</span>
            <span className="sep">·</span>
            <span>Last activity {formatTime(conv.updated_at)}</span>
            {meta.language && (<><span className="sep">·</span><span>{meta.language}</span></>)}
            {meta.product && (<><span className="sep">·</span><span style={{ color: 'var(--color-red)', fontWeight: 600 }}>{meta.product}</span></>)}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-danger-outline" onClick={handleDelete}>
            <Trash2 size={14} /> Delete
          </button>
        </div>
      </div>

      {/* Conversation feed */}
      <div className="conv-feed">
        {/* Pitch as first assistant message */}
        <div className="conv-pitch-msg">
          <div className="chat-avatar ai"><Bot size={16} /></div>
          <div className="conv-pitch-body">
            <div className="conv-pitch-header">
              <Sparkles size={12} /> Generated Pitch · First message
            </div>
            <div className="card card-padded">
              <PitchOutput
                pitch={conv.pitch_data}
                docsUsed={conv.documents_used}
                lang={meta.language || 'English'}
                onRegenerate={null}
                onSave={null}
                showToast={showToast}
                forceAllOpen={expandAllPitch}
              />
            </div>
          </div>
        </div>

        {/* Subsequent chat messages */}
        {chatMessages.map((m, i) => <ChatBubble key={i} msg={m} />)}

        {/* Typing indicator */}
        {sending && (
          <div className="chat-row ai">
            <div className="chat-avatar ai"><Bot size={16} /></div>
            <div className="chat-typing">
              <div className="chat-typing-dot" />
              <div className="chat-typing-dot" />
              <div className="chat-typing-dot" />
            </div>
          </div>
        )}
        <div ref={feedEndRef} />
      </div>

      {/* Sticky input bar */}
      <div className="conv-input-card">
        <div className="conv-quick-actions">
          <button className="btn btn-secondary" style={{ padding: '6px 12px', fontSize: 12 }}
            onClick={() => { setExpandAllPitch(true); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>
            <Eye size={13} /> View Full Pitch
          </button>
          <button className="btn btn-secondary" style={{ padding: '6px 12px', fontSize: 12 }}
            onClick={() => navigate?.('askme')}>
            <ExternalLink size={13} /> Open in AI Mentor
          </button>
        </div>
        <div className="conv-input-row">
          <textarea
            className="chat-input"
            rows={1}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Continue the conversation — e.g. 'Make the opening hook more aggressive' or 'What if they ask about foreclosure?'"
            disabled={sending}
            style={{ borderRadius: 8 }}
          />
          <button className="btn btn-primary" onClick={handleSend} disabled={sending || !input.trim()}
            style={{ height: 40, padding: '0 18px' }}>
            {sending ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
              : <><Send size={14} /> Send</>}
          </button>
        </div>
      </div>
    </div>
  );
}

function HistoryPage({ history, openConvId, setOpenConvId, onDelete, onRename, onClearAll, showToast, navigate }) {
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState('new_customer'); // 'new_customer' | 'service_call'

  if (openConvId) {
    return (
      <ConversationView
        convId={openConvId}
        onBack={() => setOpenConvId(null)}
        onRenamed={onRename}
        onDeleted={onDelete}
        showToast={showToast}
        navigate={navigate}
      />
    );
  }

  const newCustomerHistory = history.filter(h => !h.metadata?.pitch_type || h.metadata.pitch_type === 'new_customer');
  const serviceCallHistory = history.filter(h => h.metadata?.pitch_type === 'service_call');
  const activeHistory = tab === 'service_call' ? serviceCallHistory : newCustomerHistory;

  const filtered = activeHistory.filter(h => !search ||
    (h.name || '').toLowerCase().includes(search.toLowerCase()) ||
    (h.preview || '').toLowerCase().includes(search.toLowerCase()) ||
    (h.metadata?.customerName || '').toLowerCase().includes(search.toLowerCase()) ||
    (h.metadata?.product || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Pitch History</h2>
          <p className="page-subtitle">{history.length} conversation{history.length !== 1 ? 's' : ''} — click any to continue</p>
        </div>
        {history.length > 0 && (
          <div className="page-actions">
            <button className="btn btn-danger-outline" onClick={onClearAll}>
              <Trash2 size={14} /> Clear All
            </button>
          </div>
        )}
      </div>

      {/* Tab selector */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <button
          className={`segmented-btn${tab === 'new_customer' ? ' active' : ''}`}
          style={{ flex: 1 }}
          onClick={() => setTab('new_customer')}
        >
          <Sparkles size={13} /> New Customer Pitch
          {newCustomerHistory.length > 0 && (
            <span style={{ marginLeft: 6, background: tab === 'new_customer' ? 'rgba(255,255,255,0.25)' : 'var(--color-surface-raised)', borderRadius: 99, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>
              {newCustomerHistory.length}
            </span>
          )}
        </button>
        <button
          className={`segmented-btn${tab === 'service_call' ? ' active' : ''}`}
          style={{ flex: 1 }}
          onClick={() => setTab('service_call')}
        >
          <PhoneCall size={13} /> Service Call Pitch
          {serviceCallHistory.length > 0 && (
            <span style={{ marginLeft: 6, background: tab === 'service_call' ? 'rgba(255,255,255,0.25)' : 'var(--color-surface-raised)', borderRadius: 99, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>
              {serviceCallHistory.length}
            </span>
          )}
        </button>
      </div>

      {activeHistory.length > 0 && (
        <div className="card card-padded" style={{ padding: 12 }}>
          <div style={{ position: 'relative' }}>
            <Search size={16} style={{
              position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)',
              color: 'var(--color-text-faint)'
            }} />
            <input className="form-input" type="text"
              placeholder="Search by name, customer, product…"
              value={search} onChange={e => setSearch(e.target.value)}
              style={{ paddingLeft: 40 }} />
          </div>
        </div>
      )}

      {activeHistory.length === 0 ? (
        <div className="card card-padded">
          <div className="history-empty">
            <div className="history-empty-icon"><Clock size={36} /></div>
            <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 4 }}>
              {tab === 'service_call' ? 'No service call pitches yet' : 'No conversations yet'}
            </div>
            <div style={{ fontSize: 13 }}>
              {tab === 'service_call'
                ? 'Generate your first service call pitch in the Pitch Generator.'
                : 'Generate your first pitch in the Pitch Generator.'}
            </div>
          </div>
        </div>
      ) : (
        <div className="history-list">
          {filtered.map(entry => (
            <HistoryListItem
              key={entry.id}
              entry={entry}
              active={false}
              onOpen={(id) => setOpenConvId(id)}
              onRename={(id, name) => onRename(id, name)}
              onDelete={(id) => onDelete(id)}
            />
          ))}
          {filtered.length === 0 && (
            <div className="history-empty">
              <div style={{ fontSize: 13 }}>No conversations match your search.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── DOCUMENTS PAGE ───────────────────────────────────────────────────────────

function DocumentsPage({ files }) {
  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Loaded Documents</h2>
          <p className="page-subtitle">
            {files.length} document{files.length !== 1 ? 's' : ''} indexed for RAG retrieval
          </p>
        </div>
      </div>

      {files.length === 0 ? (
        <div className="card card-padded">
          <div className="history-empty">
            <div className="history-empty-icon"><FolderOpen size={36} /></div>
            <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 4 }}>No documents loaded</div>
            <div style={{ fontSize: 13 }}>
              Add files to the <code style={{ background: '#f1f5f9', padding: '2px 6px', borderRadius: 4 }}>uploads/</code> folder and restart the server.
            </div>
          </div>
        </div>
      ) : (
        <div className="card card-padded">
          <div className="doc-grid">
            {files.map((f, i) => {
              const ext = fileExt(f.name);
              return (
                <div key={i} className="doc-card">
                  <div className={`doc-icon-tile ${ext}`}>{ext}</div>
                  <div className="doc-info">
                    <div className="doc-name" title={f.name}>{f.name}</div>
                    <div className="doc-meta">
                      {f.type === 'document'
                        ? `${f.units || 0} units · ${f.chunks || 0} chunks`
                        : `${f.rows || 0} rows`}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── RAG INSPECTOR PAGE ───────────────────────────────────────────────────────

const RAG_SAMPLE_QUERIES = [
  "What is the minimum CIBIL score for HDFC personal loan?",
  "What documents are needed for a salaried customer?",
  "EMI kitni hogi 5 lakh ke liye 3 saal mein?",
  "Foreclosure charges for personal loan?",
  "Maximum loan amount for self-employed?",
];

function RagStageCard({ icon: Icon, num, title, subtitle, accent, children }) {
  return (
    <div className="rag-stage-card" style={{ '--rag-accent': accent }}>
      <div className="rag-stage-header">
        <div className="rag-stage-num">{num}</div>
        <Icon size={16} style={{ color: accent }} />
        <div style={{ flex: 1 }}>
          <div className="rag-stage-title">{title}</div>
          {subtitle && <div className="rag-stage-subtitle">{subtitle}</div>}
        </div>
      </div>
      <div className="rag-stage-body">{children}</div>
    </div>
  );
}

function RagInspectorPage() {
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(6);
  const [includeLLM, setIncludeLLM] = useState(false);
  const [useReranker, setUseReranker] = useState(true);
  const [maxChunksPerFile, setMaxChunksPerFile] = useState(2);
  const [loading, setLoading] = useState(false);
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState('');
  const [expandedChunkIdx, setExpandedChunkIdx] = useState(null);
  const [showFullPrompt, setShowFullPrompt] = useState(false);

  const handleInspect = async (q) => {
    const text = (q ?? query).trim();
    if (!text || loading) return;
    setLoading(true);
    setError('');
    setTrace(null);
    setExpandedChunkIdx(null);
    setShowFullPrompt(false);
    try {
      const res = await fetch(`${API}/api/rag/inspect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: text,
          top_k: topK,
          candidates: 20,
          max_files: 3,
          max_chunks_per_file: maxChunksPerFile,
          use_reranker: useReranker,
          include_llm: includeLLM,
        }),
      });
      const data = await res.json();
      if (data.success) {
        setTrace(data.trace);
      } else {
        setError(shortError(data.detail || data.error || 'Inspect failed'));
      }
    } catch (e) {
      setError(shortError(e.message));
    } finally {
      setLoading(false);
    }
  };

  const ms = (k) => trace?.timings_ms?.[k] ?? 0;

  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">RAG Inspector</h2>
          <p className="page-subtitle">
            See exactly what your RAG pipeline does: intent → expansion → embeddings → top-K chunks → prompt → answer.
          </p>
        </div>
      </div>

      {/* Query input */}
      <div className="card card-padded">
        <label className="form-label">Inspect a query</label>
        <textarea
          className="form-textarea"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="What is the minimum CIBIL score for HDFC personal loan?"
          rows={2}
          disabled={loading}
        />
        <div className="rag-controls">
          <div className="rag-control">
            <label className="form-label">Top K chunks</label>
            <select className="form-select" value={topK} onChange={e => setTopK(Number(e.target.value))} disabled={loading}>
              <option value={3}>3</option>
              <option value={6}>6 (default)</option>
              <option value={10}>10</option>
              <option value={15}>15</option>
            </select>
          </div>
          <div className="rag-control">
            <label className="form-label">Max chunks/file</label>
            <select className="form-select" value={maxChunksPerFile} onChange={e => setMaxChunksPerFile(Number(e.target.value))} disabled={loading}>
              <option value={1}>1</option>
              <option value={2}>2 (default)</option>
              <option value={3}>3</option>
            </select>
          </div>
          <div className="rag-control" style={{ display: 'flex', alignItems: 'flex-end', gap: 12, flexWrap: 'wrap' }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text)', cursor: 'pointer' }}>
              <input type="checkbox" checked={useReranker} onChange={e => setUseReranker(e.target.checked)} disabled={loading} />
              Cross-encoder rerank
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text)', cursor: 'pointer' }}>
              <input type="checkbox" checked={includeLLM} onChange={e => setIncludeLLM(e.target.checked)} disabled={loading} />
              Also run LLM
            </label>
          </div>
          <button className="btn btn-primary" onClick={() => handleInspect()} disabled={loading || !query.trim()}
            style={{ alignSelf: 'flex-end' }}>
            {loading ? <><span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> Inspecting…</>
              : <><Activity size={14} /> Inspect Pipeline</>}
          </button>
        </div>

        <div className="rag-samples">
          <span style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Try:
          </span>
          {RAG_SAMPLE_QUERIES.map(q => (
            <button key={q} className="test-prompt-chip"
              onClick={() => { setQuery(q); handleInspect(q); }}
              disabled={loading}>
              {q}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="alert alert-error">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={15} /> {error}
          </span>
        </div>
      )}

      {trace && (
        <>
          {/* Top metric strip */}
          <div className="rag-metrics-strip">
            <div className="rag-metric">
              <div className="rag-metric-label">Total time</div>
              <div className="rag-metric-value">{ms('total_ms')} ms</div>
            </div>
            <div className="rag-metric">
              <div className="rag-metric-label">RAG only</div>
              <div className="rag-metric-value">
                {ms('embedding_ms') + ms('dense_ms') + ms('bm25_ms') + ms('fusion_plus_rerank_ms')} ms
              </div>
            </div>
            {includeLLM && (
              <div className="rag-metric">
                <div className="rag-metric-label">LLM call</div>
                <div className="rag-metric-value">{ms('llm_ms')} ms</div>
              </div>
            )}
            <div className="rag-metric">
              <div className="rag-metric-label">Files indexed</div>
              <div className="rag-metric-value">{trace.config.files_indexed}</div>
            </div>
            <div className="rag-metric">
              <div className="rag-metric-label">Chunks → LLM</div>
              <div className="rag-metric-value">
                {trace.stages['8_context']?.blocks?.reduce((n, b) => n + (b.chunks?.length || 0), 0) || 0}
              </div>
            </div>
          </div>

          {/* Stages */}
          <RagStageCard icon={Zap} num="1" title="Intent Detection"
            subtitle={`${ms('intent_ms')}ms · pure regex/keyword classifier`}
            accent="#7C3AED">
            <span className="tag" style={{ background: '#f5f3ff', color: '#7C3AED' }}>
              {trace.stages['1_intent'].intent}
            </span>
          </RagStageCard>

          <RagStageCard icon={Layers} num="2" title="Query Expansion"
            subtitle={`${ms('expansion_ms')}ms · domain keywords appended for better vector recall`}
            accent="#0EA5E9">
            <div className="rag-row-label">Original</div>
            <div className="rag-text-block">{trace.stages['2_query_expansion'].original}</div>
            <div className="rag-row-label">+ Expanded ({trace.stages['2_query_expansion'].expansion_count} phrases added)</div>
            <div className="rag-text-block dim">{trace.stages['2_query_expansion'].expanded}</div>
          </RagStageCard>

          <RagStageCard icon={MessageCircle} num="3" title="Speech Analysis"
            subtitle={`${ms('analysis_ms')}ms · used by the Live Voice path`}
            accent="#F59E0B">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 8 }}>
              {Object.entries(trace.stages['3_speech_analysis']).filter(([k]) =>
                ['language_mix', 'tone', 'question_type', 'urgency', 'is_followup'].includes(k)
              ).map(([k, v]) => (
                <div key={k} className="rag-kv">
                  <span className="rag-kv-key">{k}</span>
                  <span className="rag-kv-val">{String(v)}</span>
                </div>
              ))}
            </div>
            {Object.keys(trace.stages['3_speech_analysis'].key_entities || {}).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="rag-row-label">Entities extracted</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {Object.entries(trace.stages['3_speech_analysis'].key_entities).map(([k, v]) => (
                    <span key={k} className="chat-source-pill">{k}: {v}</span>
                  ))}
                </div>
              </div>
            )}
          </RagStageCard>

          <RagStageCard icon={Database} num="4" title="Query Embedding"
            subtitle={`${ms('embedding_ms')}ms · sentence-transformers/all-MiniLM-L6-v2 (CPU)`}
            accent="#10B981">
            <div className="rag-kv-row">
              <span className="rag-kv-key">Dimension</span>
              <span className="rag-kv-val">{trace.stages['4_query_embedding'].embedding_dim}</span>
              <span className="rag-kv-key">L2 norm</span>
              <span className="rag-kv-val">{trace.stages['4_query_embedding'].embedding_norm}</span>
            </div>
          </RagStageCard>

          {/* Stage 5a — Dense retrieval */}
          {trace.stages['5a_dense']?.ranking && (
            <RagStageCard icon={Database} num="5a" title="Dense Retrieval (semantic)"
              subtitle={`${ms('dense_ms')}ms · cosine over chunk embeddings · top ${trace.stages['5a_dense'].candidates_returned}`}
              accent="#0EA5E9">
              <div className="rag-rank-list">
                {trace.stages['5a_dense'].ranking.slice(0, 5).map(r => (
                  <div key={r.rank} className="rag-rank-row">
                    <span className="rag-rank-num">#{r.rank}</span>
                    <div className="rag-rank-bar">
                      <div className="rag-rank-bar-fill" style={{ width: `${Math.min(100, Math.max(0, r.score * 100))}%` }} />
                    </div>
                    <span className="rag-rank-score">{r.score.toFixed(3)}</span>
                    <span className="rag-rank-file">{r.file} <span style={{ color: 'var(--color-text-faint)' }}>· chunk {r.chunk_id}/{r.total_chunks_in_file}</span></span>
                  </div>
                ))}
              </div>
            </RagStageCard>
          )}

          {/* Stage 5b — BM25 retrieval */}
          {trace.stages['5b_bm25']?.ranking && (
            <RagStageCard icon={Search} num="5b" title="BM25 Retrieval (keyword)"
              subtitle={`${ms('bm25_ms')}ms · sparse keyword score · top ${trace.stages['5b_bm25'].candidates_returned}`}
              accent="#F59E0B">
              {trace.stages['5b_bm25'].ranking.length === 0 ? (
                <div className="rag-row-label">No BM25 matches above zero score.</div>
              ) : (() => {
                const maxScore = Math.max(...trace.stages['5b_bm25'].ranking.map(r => r.score)) || 1;
                return (
                  <div className="rag-rank-list">
                    {trace.stages['5b_bm25'].ranking.slice(0, 5).map(r => (
                      <div key={r.rank} className="rag-rank-row">
                        <span className="rag-rank-num">#{r.rank}</span>
                        <div className="rag-rank-bar">
                          <div className="rag-rank-bar-fill" style={{ width: `${(r.score / maxScore) * 100}%`, background: 'linear-gradient(90deg, #F59E0B 0%, #fbbf24 100%)' }} />
                        </div>
                        <span className="rag-rank-score">{r.score.toFixed(2)}</span>
                        <span className="rag-rank-file">{r.file} <span style={{ color: 'var(--color-text-faint)' }}>· chunk {r.chunk_id}/{r.total_chunks_in_file}</span></span>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </RagStageCard>
          )}

          {/* Stage 6 — RRF fusion */}
          {trace.stages['6_fused']?.ranking && (
            <RagStageCard icon={Layers} num="6" title="Reciprocal Rank Fusion"
              subtitle={`Merging dense + BM25 (k=${trace.stages['6_fused'].k || 60})`}
              accent="#7C3AED">
              <div className="rag-rank-list">
                {trace.stages['6_fused'].ranking.map(r => (
                  <div key={r.rank} className="rag-rank-row">
                    <span className="rag-rank-num">#{r.rank}</span>
                    <span className="chat-source-pill" style={{ minWidth: 56, textAlign: 'center' }}>
                      d:{r.dense_rank ?? '—'} / b:{r.bm25_rank ?? '—'}
                    </span>
                    <span className="rag-rank-score">{r.rrf_score.toFixed(4)}</span>
                    <span className="rag-rank-file">{r.file} <span style={{ color: 'var(--color-text-faint)' }}>· chunk {r.chunk_id}/{r.total_chunks_in_file}</span></span>
                  </div>
                ))}
              </div>
            </RagStageCard>
          )}

          {/* Stage 7 — Cross-encoder rerank (optional) */}
          {trace.stages['7_reranked']?.ranking && (
            <RagStageCard icon={Zap} num="7" title="Cross-encoder Rerank"
              subtitle="Final precision pass · ms-marco-MiniLM-L-6-v2"
              accent="#10B981">
              <div className="rag-rank-list">
                {trace.stages['7_reranked'].ranking.map(r => (
                  <div key={r.rank} className="rag-rank-row">
                    <span className="rag-rank-num">#{r.rank}</span>
                    <span className="chat-source-pill" style={{ minWidth: 56, textAlign: 'center' }}>
                      d:{r.dense_rank ?? '—'} / b:{r.bm25_rank ?? '—'}
                    </span>
                    <span className="rag-rank-score">{r.rrf_score.toFixed(3)}</span>
                    <span className="rag-rank-file">{r.file} <span style={{ color: 'var(--color-text-faint)' }}>· chunk {r.chunk_id}/{r.total_chunks_in_file}</span></span>
                  </div>
                ))}
              </div>
            </RagStageCard>
          )}
          {trace.stages['7_reranked']?.status === 'skipped' && (
            <RagStageCard icon={Zap} num="7" title="Cross-encoder Rerank"
              subtitle="Skipped — toggle 'use_reranker' on for +9% recall"
              accent="#94A3B8">
              <div className="rag-row-label">{trace.stages['7_reranked'].reason}</div>
            </RagStageCard>
          )}

          {/* Stage 8 — Context blocks (what LLM sees) */}
          {trace.stages['8_context']?.blocks && (
            <RagStageCard icon={FileText} num="8" title="Context Blocks → LLM"
              subtitle={`${trace.stages['8_context'].block_count} files · ${trace.stages['8_context'].total_context_chars.toLocaleString()} chars · grouped by source`}
              accent="#0A1628">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {trace.stages['8_context'].blocks.map((b, i) => (
                  <div key={i} className="rag-chunk">
                    <div className="rag-chunk-head"
                      onClick={() => setExpandedChunkIdx(expandedChunkIdx === i ? null : i)}>
                      <span className="rag-chunk-num">#{b.rank}</span>
                      <span style={{ flex: 1, fontWeight: 600, fontSize: 13 }}>{b.filename}</span>
                      <span className="chat-source-pill">rrf {b.rrf_score.toFixed(3)}</span>
                      <span className="chat-source-pill">{b.chunks.length} chunk{b.chunks.length !== 1 ? 's' : ''}</span>
                      <span className="chat-source-pill">{b.char_count} chars</span>
                      <ChevronDown size={14} style={{
                        transform: expandedChunkIdx === i ? 'rotate(180deg)' : 'none',
                        transition: 'transform 0.15s',
                      }} />
                    </div>
                    <div className="rag-chunk-preview">
                      {b.chunks.map((c, ci) => (
                        <div key={ci} style={{ marginBottom: 8 }}>
                          <div style={{ fontSize: 10, color: 'var(--color-text-faint)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                            chunk {c.chunk_id + 1} of {c.total_in_file} · {c.char_count} chars
                          </div>
                          {expandedChunkIdx === i ? c.content : c.preview}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </RagStageCard>
          )}

          {/* Stage 9 — Prompt */}
          {trace.stages['9_prompt'] && (
            <RagStageCard icon={Bot} num="9" title="Prompt Built for LLM"
              subtitle={`${ms('prompt_build_ms')}ms`}
              accent="#7C3AED">
              <div className="rag-kv-row">
                <span className="rag-kv-key">Total chars</span>
                <span className="rag-kv-val">{trace.stages['9_prompt'].prompt_chars.toLocaleString()}</span>
                <span className="rag-kv-key">Context chars</span>
                <span className="rag-kv-val">{trace.stages['9_prompt'].context_chars.toLocaleString()}</span>
                <span className="rag-kv-key">Files included</span>
                <span className="rag-kv-val">{trace.stages['9_prompt'].files_included}</span>
                <span className="rag-kv-key">Chunks included</span>
                <span className="rag-kv-val">{trace.stages['9_prompt'].chunks_included}</span>
              </div>
              <button className="btn btn-ghost" style={{ marginTop: 8, fontSize: 12 }}
                onClick={() => setShowFullPrompt(s => !s)}>
                {showFullPrompt ? 'Hide' : 'Show'} full prompt
              </button>
              <div className="rag-text-block dim" style={{ marginTop: 8, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12, maxHeight: showFullPrompt ? 'none' : 200, overflow: 'auto' }}>
                {showFullPrompt ? trace.stages['9_prompt'].prompt_full : trace.stages['9_prompt'].prompt_preview}
              </div>
            </RagStageCard>
          )}

          {/* Stage 10 — LLM answer */}
          {trace.stages['10_llm'] && (
            <RagStageCard icon={Sparkles} num="10" title="LLM Answer"
              subtitle={`${ms('llm_ms')}ms`}
              accent="#10B981">
              {trace.stages['10_llm'].error ? (
                <div className="alert alert-error">{trace.stages['10_llm'].error}</div>
              ) : (
                <>
                  <div className="rag-row-label">Raw answer ({trace.stages['10_llm'].raw_answer_chars} chars)</div>
                  <div className="rag-text-block">{trace.stages['10_llm'].raw_answer}</div>
                  {trace.stages['10_llm'].post_processing_applied && (
                    <>
                      <div className="rag-row-label" style={{ marginTop: 10 }}>
                        Post-processed (what the user sees) — {trace.stages['10_llm'].post_processed_chars} chars
                      </div>
                      <div className="rag-text-block dim">{trace.stages['10_llm'].post_processed_answer}</div>
                    </>
                  )}
                </>
              )}
            </RagStageCard>
          )}
        </>
      )}
    </div>
  );
}

// ── PITCH GENERATOR PAGE ─────────────────────────────────────────────────────

function PitchStreamProgress({ stream }) {
  if (!stream) return null;
  const doneCount = stream.sections.filter(s => s.status === 'done').length;
  const activeIdx = stream.sections.findIndex(s => s.status === 'generating');
  return (
    <div className="pitch-stream-card">
      <div className="pitch-stream-head">
        <div>
          <div className="pitch-stream-title">
            <Sparkles size={16} style={{ color: 'var(--color-red)' }} />
            Building pitch · {doneCount} of {stream.total} sections ready
          </div>
          <div className="pitch-stream-sub">
            {(doneCount === 0 && activeIdx === -1)
              ? 'Preparing scenario and retrieving context…'
              : 'Streaming each section as it finishes'}
          </div>
        </div>
        {stream.ms_total != null && (
          <span className="chat-source-pill">
            Total: {(stream.ms_total / 1000).toFixed(1)}s
          </span>
        )}
      </div>

      <div className="pitch-stream-timeline">
        {stream.sections.map((s, i) => (
          <div key={s.id} className={`pitch-step pitch-step-${s.status}`}>
            <span className="pitch-step-dot">
              {s.status === 'done' ? <CheckCircle2 size={14} />
                : s.status === 'generating' ? <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />
                  : s.status === 'error' ? <AlertTriangle size={14} />
                    : <span style={{ fontSize: 11, fontWeight: 700 }}>{i + 1}</span>}
            </span>
            <span className="pitch-step-label">{s.label}</span>
            {s.status === 'done' && (
              <span className="pitch-step-meta">
                {s.word_count}w · {(s.ms / 1000).toFixed(1)}s
                {s.retried && <span title="LLM was below min word count on first try; retried with bigger budget"> · retried</span>}
              </span>
            )}
            {s.status === 'error' && (
              <span className="pitch-step-meta" style={{ color: 'var(--color-error)' }}>
                {(s.error || 'error').slice(0, 60)}
              </span>
            )}
            {i < stream.sections.length - 1 && <span className="pitch-step-bar" />}
          </div>
        ))}
      </div>

      {/* Show each completed section's text as it streams in */}
      {stream.sections.some(s => s.status === 'done' || s.status === 'generating') && (
        <div className="pitch-stream-bodies">
          {stream.sections.map(s => (
            (s.status === 'done' || s.status === 'generating') ? (
              <details key={s.id} className="pitch-stream-section" open={s.status === 'generating' || s.status === 'done'}>
                <summary>
                  <span style={{ fontWeight: 600 }}>{s.label}</span>
                  {s.status === 'generating' && (
                    <span className="chat-source-pill" style={{ marginLeft: 8 }}>writing…</span>
                  )}
                </summary>
                <div className="pitch-stream-section-body">
                  {s.content ? (
                    <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', margin: 0 }}>{s.content}</pre>
                  ) : (
                    <span style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>Waiting for the LLM…</span>
                  )}
                </div>
              </details>
            ) : null
          ))}
        </div>
      )}
    </div>
  );
}

// ── Service Call (ETB) Components ────────────────────────────────────────────

const SC_LANGUAGES = ['English', 'Hinglish', 'Marathi', 'Bengali', 'Tamil', 'Telugu'];

const SC_SERVICE_ISSUES = [
  'EMI deduction issue',
  'Account statement not received',
  'Net Banking / Mobile Banking not working',
  'Cheque bounce / return',
  'Loan account query',
  'Credit card billing query',
  'ATM card not received / blocked',
  'Account statement discrepancy',
  'Loan NOC not received',
  'Other issue',
];

const RANDOM_SC_PITCHES = [
  {
    customerName: 'Mr. Ramesh Nair',
    existingFacility: 'HDFC Personal Loan (₹5L, taken 2 years ago)',
    relationshipDuration: '5 years',
    offerProduct: 'HDFC Personal Loan (Top-up)',
    offerAmount: '₹8 Lakhs',
    digitalAdoption: 'Uses Mobile Banking regularly',
    priorLoan: 'Yes — repaid on time',
    serviceIssue: 'EMI deduction issue',
    goal: 'Address the EMI concern and introduce the pre-approved top-up loan offer.',
  },
  {
    customerName: 'Mrs. Sunita Joshi',
    existingFacility: 'HDFC Savings Account + Debit Card',
    relationshipDuration: '8 years',
    offerProduct: 'HDFC Personal Loan',
    offerAmount: '₹5 Lakhs',
    digitalAdoption: 'Net Banking user',
    priorLoan: 'No prior loan',
    serviceIssue: 'None',
    goal: 'Service check, then introduce a pre-approved personal loan for home renovation.',
  },
  {
    customerName: 'Mr. Aditya Rao',
    existingFacility: 'HDFC Home Loan (₹35L, 6 years into tenure)',
    relationshipDuration: '10 years',
    offerProduct: 'HDFC Personal Loan',
    offerAmount: '₹12 Lakhs',
    digitalAdoption: 'Prefers branch visits',
    priorLoan: 'Yes — home loan repaying regularly',
    serviceIssue: 'Account statement not received',
    goal: 'Resolve the statement issue, then pitch a pre-approved top-up or personal loan.',
  },
  {
    customerName: 'Ms. Priya Mehta',
    existingFacility: 'HDFC Credit Card (Millennia)',
    relationshipDuration: '3 years',
    offerProduct: 'HDFC Personal Loan',
    offerAmount: '₹3 Lakhs',
    digitalAdoption: 'Highly digital — PhonePe, app-only customer',
    priorLoan: 'No prior loan',
    serviceIssue: 'None',
    goal: 'Service check on card satisfaction, then introduce small pre-approved loan.',
  },
];

// Tints and text lifted for the dark glass theme. The old values (#ecfdf5 fills
// with #059669 text) were drawn for white cards and rendered as bright blocks
// floating on the backdrop. These are set inline rather than in CSS, so the
// values have to change here.
const SC_FEEDBACK_OPTIONS = [
  { value: 'happy',   label: 'Happy / Satisfied',    hint: 'Good feedback — proceed to sales pitch', color: '#6EE7B7', bg: 'rgba(16,185,129,0.16)', border: 'rgba(110,231,183,0.40)', Icon: ThumbsUp },
  { value: 'neutral', label: 'Neutral',               hint: 'No strong feeling either way',           color: '#FCD34D', bg: 'rgba(251,191,36,0.16)', border: 'rgba(252,211,77,0.40)',  Icon: Minus },
  { value: 'unhappy', label: 'Has Complaint / Issue', hint: 'Resolve issue first, then pitch',        color: '#FDA4AF', bg: 'rgba(244,63,94,0.16)',  border: 'rgba(253,164,175,0.40)', Icon: ThumbsDown },
];

function ServiceCallQuickForm({ onGenerate, loading }) {
  const [form, setForm] = useState({
    customerName: '',
    existingFacility: '',
    relationshipDuration: '',
    offerProduct: '',
    offerAmount: '',
    digitalAdoption: '',
    priorLoan: '',
    customerFeedback: 'happy',
    serviceIssue: '',
    serviceIssueCustom: '',
    goal: '',
    language: 'English',
  });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const fillRandom = () => {
    const pick = RANDOM_SC_PITCHES[Math.floor(Math.random() * RANDOM_SC_PITCHES.length)];
    setForm(f => ({ ...f, ...pick, serviceIssueCustom: '', customerFeedback: pick.customerFeedback || 'happy' }));
  };

  const effectiveIssue = form.serviceIssue === 'Other issue' && form.serviceIssueCustom
    ? form.serviceIssueCustom
    : form.serviceIssue;

  const feedbackMeta = SC_FEEDBACK_OPTIONS.find(o => o.value === form.customerFeedback);

  const handleSubmit = (e) => {
    e.preventDefault();
    const feedbackLine = form.customerFeedback === 'happy'
      ? 'Customer Feedback: HAPPY — Customer expressed satisfaction with existing HDFC service/facility.'
      : form.customerFeedback === 'neutral'
      ? 'Customer Feedback: NEUTRAL — Customer has no strong feelings about existing service.'
      : `Customer Feedback: UNHAPPY — Customer has a complaint/issue${effectiveIssue ? `: ${effectiveIssue}` : '.'}  Pitch must resolve the issue first before transitioning to the sales offer.`;
    const lines = [
      `Customer: ${form.customerName}`,
      `Existing Facility: ${form.existingFacility}`,
      `Relationship Duration: ${form.relationshipDuration}`,
      feedbackLine,
      `Pre-Approved Offer Product: ${form.offerProduct}`,
      `Pre-Approved Offer Amount: ${form.offerAmount}`,
      `Digital Adoption: ${form.digitalAdoption}`,
      `Prior Loan History: ${form.priorLoan}`,
      `Goal: ${form.goal}`,
    ].filter(Boolean);
    onGenerate(lines.join('\n'), form.language);
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 14 }}>
        <button type="button" className="btn btn-primary" onClick={fillRandom} disabled={loading}>
          <Sparkles size={14} /> Generate sample pitch
        </button>
      </div>

      {/* ── Customer Feedback — primary branch selector ── */}
      <div style={{ marginBottom: 20 }}>
        <label className="form-label" style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--color-text-secondary)', marginBottom: 8, display: 'block' }}>
          Customer Feedback on Existing Service *
        </label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
          {SC_FEEDBACK_OPTIONS.map(opt => {
            const active = form.customerFeedback === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => { set('customerFeedback', opt.value); if (opt.value !== 'unhappy') set('serviceIssue', ''); }}
                style={{
                  padding: '12px 10px',
                  borderRadius: 10,
                  border: active ? `2px solid ${opt.color}` : '1.5px solid var(--color-border)',
                  background: active ? opt.bg : 'var(--color-surface)',
                  cursor: 'pointer',
                  textAlign: 'center',
                  transition: 'all 0.15s',
                  boxShadow: active ? `0 0 0 3px ${opt.border}` : 'none',
                }}>
                <div style={{ marginBottom: 6, display: 'flex', justifyContent: 'center' }}>
                  <opt.Icon size={22} color={active ? opt.color : 'var(--color-text-faint)'} strokeWidth={2} />
                </div>
                <div style={{ fontWeight: 700, fontSize: 12.5, color: active ? opt.color : 'var(--color-text)', marginBottom: 2 }}>{opt.label}</div>
                <div style={{ fontSize: 11, color: active ? opt.color : 'var(--color-text-faint)', opacity: 0.9 }}>{opt.hint}</div>
              </button>
            );
          })}
        </div>
        {feedbackMeta && (
          <div style={{
            marginTop: 8, padding: '7px 12px', borderRadius: 7,
            background: feedbackMeta.bg, border: `1px solid ${feedbackMeta.border}`,
            fontSize: 12, color: feedbackMeta.color, fontWeight: 500,
          }}>
            {form.customerFeedback === 'happy' && 'Pitch will open with appreciation → transition directly to the pre-approved offer.'}
            {form.customerFeedback === 'neutral' && 'Pitch will do a light service check → then introduce the offer once the customer is engaged.'}
            {form.customerFeedback === 'unhappy' && 'Pitch will handle the complaint first with empathy + escalation assurance, then carefully transition to the offer.'}
          </div>
        )}
      </div>

      {/* ── Service issue picker — only when unhappy ── */}
      {form.customerFeedback === 'unhappy' && (
        <div style={{ marginBottom: 16, padding: '16px 18px', background: 'rgba(244,63,94,0.14)', borderRadius: 14, border: '1px solid rgba(253,164,175,0.34)' }}>
          <label className="form-label" style={{ color: '#FDA4AF' }}>What is the complaint / issue?</label>
          <select className="form-select" value={form.serviceIssue} onChange={e => set('serviceIssue', e.target.value)}
            style={{ borderColor: 'rgba(253,164,175,0.40)' }}>
            <option value="">Select or describe below</option>
            {SC_SERVICE_ISSUES.map(i => <option key={i}>{i}</option>)}
          </select>
          {form.serviceIssue === 'Other issue' && (
            <input className="form-input" type="text" placeholder="Describe the specific issue..."
              value={form.serviceIssueCustom} onChange={e => set('serviceIssueCustom', e.target.value)}
              style={{ marginTop: 8, borderColor: 'rgba(253,164,175,0.40)' }} />
          )}
        </div>
      )}

      <div className="form-grid-2" style={{ marginBottom: 16 }}>
        <div>
          <label className="form-label">Customer Name</label>
          <input className="form-input" type="text" placeholder="e.g. Mr. Ramesh Nair"
            value={form.customerName} onChange={e => set('customerName', e.target.value)} required />
        </div>
        <div>
          <label className="form-label">Existing Facility / Product</label>
          <input className="form-input" type="text" placeholder="e.g. HDFC Personal Loan (₹5L)"
            value={form.existingFacility} onChange={e => set('existingFacility', e.target.value)} required />
        </div>
      </div>

      <div className="form-grid-2" style={{ marginBottom: 16 }}>
        <div>
          <label className="form-label">Relationship Duration</label>
          <input className="form-input" type="text" placeholder="e.g. 5 years"
            value={form.relationshipDuration} onChange={e => set('relationshipDuration', e.target.value)} />
        </div>
        <div>
          <label className="form-label">Pre-Approved Offer Product</label>
          <input className="form-input" type="text" placeholder="e.g. HDFC Personal Loan"
            value={form.offerProduct} onChange={e => set('offerProduct', e.target.value)} required />
        </div>
      </div>

      <div className="form-grid-2" style={{ marginBottom: 16 }}>
        <div>
          <label className="form-label">Pre-Approved Offer Amount</label>
          <input className="form-input" type="text" placeholder="e.g. ₹8 Lakhs"
            value={form.offerAmount} onChange={e => set('offerAmount', e.target.value)} required />
        </div>
        <div>
          <label className="form-label">Digital Adoption</label>
          <input className="form-input" type="text" placeholder="e.g. Uses Mobile Banking"
            value={form.digitalAdoption} onChange={e => set('digitalAdoption', e.target.value)} />
        </div>
      </div>

      <div className="form-grid-2" style={{ marginBottom: 16 }}>
        <div>
          <label className="form-label">Prior Loan History</label>
          <input className="form-input" type="text" placeholder="e.g. Yes — repaid on time"
            value={form.priorLoan} onChange={e => set('priorLoan', e.target.value)} />
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">Your Goal</label>
        <textarea className="form-textarea"
          placeholder="e.g. Address the EMI concern and introduce the pre-approved top-up loan..."
          value={form.goal} onChange={e => set('goal', e.target.value)} rows={2} />
      </div>

      <div className="form-group">
        <label className="form-label"><Languages size={11} style={{ display: 'inline', marginRight: 4, verticalAlign: -1 }} /> Language</label>
        <select className="form-select" value={form.language} onChange={e => set('language', e.target.value)}>
          {SC_LANGUAGES.map(l => <option key={l}>{l}</option>)}
        </select>
      </div>

      <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={loading}>
        {loading
          ? <><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> Generating Service Call Pitch…</>
          : <><Sparkles size={16} /> Generate Service Call Pitch</>}
      </button>
    </form>
  );
}

function ServiceCallFreeWriteForm({ onGenerate, loading }) {
  const [scenario, setScenario] = useState('');
  const [language, setLanguage] = useState('English');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!scenario.trim()) return;
    onGenerate(scenario.trim(), language);
  };

  return (
    <form onSubmit={handleSubmit}>
      <div className="form-group">
        <label className="form-label">Describe the Service Call Scenario</label>
        <textarea className="form-textarea"
          placeholder={`Describe the ETB customer: their existing HDFC facility, the pre-approved offer details, any known service issue, and your goal for this call.\n\nExample: Mr. Ramesh Nair has been an HDFC customer for 5 years with a ₹5L personal loan. He's been repaying on time. There's a pre-approved offer of ₹8L. He recently raised an EMI deduction concern. Goal: resolve the concern and introduce the top-up offer.`}
          value={scenario} onChange={e => setScenario(e.target.value)}
          rows={7} required />
      </div>
      <div className="form-group">
        <label className="form-label"><Languages size={11} style={{ display: 'inline', marginRight: 4, verticalAlign: -1 }} /> Language</label>
        <select className="form-select" value={language} onChange={e => setLanguage(e.target.value)}>
          {SC_LANGUAGES.map(l => <option key={l}>{l}</option>)}
        </select>
      </div>
      <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={loading || !scenario.trim()}>
        {loading
          ? <><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> Generating Service Call Pitch…</>
          : <><Sparkles size={16} /> Generate Service Call Pitch</>}
      </button>
    </form>
  );
}

function ServiceCallOutput({ pitch, onRegenerate, showToast, lang = 'English' }) {
  if (!pitch) return null;

  const cleanText = (val) => {
    if (typeof val === 'string') return val;
    if (val && typeof val === 'object') return val.fact || val.text || val.bullet || JSON.stringify(val);
    return String(val ?? '');
  };

  const handleCopy = () => {
    const parts = [
      pitch.opening_script ? `OPENING SCRIPT:\n${pitch.opening_script}` : '',
      Array.isArray(pitch.service_check_questions) ? `\nSERVICE CHECK QUESTIONS:\n${pitch.service_check_questions.join('\n')}` : '',
      pitch.happy_customer_escalation ? `\nHAPPY CUSTOMER PATH:\n${pitch.happy_customer_escalation}` : '',
      pitch.transition_bridge ? `\nTRANSITION:\n${pitch.transition_bridge}` : '',
      pitch.loan_pitch_lines ? `\nLOAN OFFER PITCH:\n${pitch.loan_pitch_lines}` : '',
      Array.isArray(pitch.objection_handling) ? `\nOBJECTION HANDLING:\n${pitch.objection_handling.map(o => `Q: ${o.objection}\nA: ${o.response}`).join('\n\n')}` : '',
      pitch.closing_strategy ? `\nCLOSING STRATEGY:\n${pitch.closing_strategy}` : '',
      Array.isArray(pitch.what_not_to_say) ? `\nWHAT NOT TO SAY:\n${pitch.what_not_to_say.join('\n')}` : '',
      Array.isArray(pitch.documents_checklist) ? `\nDOCUMENTS CHECKLIST:\n${pitch.documents_checklist.join('\n')}` : '',
      pitch.full_script ? `\nFULL CALL SCRIPT:\n${pitch.full_script}` : '',
      pitch.follow_up_template ? `\nFOLLOW-UP TEMPLATE:\n${pitch.follow_up_template}` : '',
    ].filter(Boolean).join('\n');
    navigator.clipboard.writeText(parts);
    showToast?.('Pitch copied to clipboard', 'success');
  };

  return (
    <div className="slide-up">
      <div className="pitch-toolbar">
        <div className="pitch-toolbar-identity">
          <div className="pitch-toolbar-icon-tile" style={{ background: 'linear-gradient(135deg, #0A1628 0%, #1e3a5f 100%)' }}>
            <PhoneCall size={20} />
          </div>
          <div>
            <div className="pitch-toolbar-heading">Service Call Pitch</div>
            <div className="pitch-toolbar-sub">
              {pitch.opening_script ? 'ETB customer — tap any section · use while on call' : 'Generating…'}
            </div>
          </div>
          {pitch.opening_script && (
            <span className="pitch-ready-badge" style={{ background: 'linear-gradient(90deg, #0A1628, #1e3a5f)' }}>
              <CheckCircle2 size={10} /> READY
            </span>
          )}
        </div>
        <div className="pitch-toolbar-actions">
          <button className="btn btn-secondary" onClick={handleCopy}><Copy size={14} /> Copy</button>
          {onRegenerate && <button className="btn btn-primary" onClick={onRegenerate}><RefreshCw size={14} /> Regenerate</button>}
        </div>
      </div>

      <PitchSection icon={PhoneCall} title="Opening Script" accentColor="#0A1628" accentTint="#eef2f7" defaultOpen lang={lang}>
        <PitchNarrative text={typeof pitch.opening_script === 'string' ? pitch.opening_script : ''} />
      </PitchSection>

      <PitchSection icon={HelpCircle} title="Service Check Questions" accentColor="#2563EB" accentTint="#eff6ff" lang={lang}>
        {(() => {
          const items = Array.isArray(pitch.service_check_questions)
            ? pitch.service_check_questions
            : typeof pitch.service_check_questions === 'string'
              ? pitch.service_check_questions.split('\n').map(l => l.replace(/^\s*\d+[.)]\s*/, '').trim()).filter(Boolean)
              : [];
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No questions generated.</p>;
          return <ol className="pitch-numbered">{items.map((q, i) => <li key={i}>{cleanText(q)}</li>)}</ol>;
        })()}
      </PitchSection>

      <PitchSection icon={HeartHandshake} title="Happy Customer Path" accentColor="#10B981" accentTint="#ecfdf5" lang={lang}>
        <PitchNarrative text={typeof pitch.happy_customer_escalation === 'string' ? pitch.happy_customer_escalation : ''} />
      </PitchSection>

      <PitchSection icon={ArrowRight} title="Service → Offer Transition" accentColor="#7C3AED" accentTint="#f5f3ff" lang={lang}>
        <PitchNarrative text={typeof pitch.transition_bridge === 'string' ? pitch.transition_bridge : ''} />
      </PitchSection>

      <PitchSection icon={Sparkles} title="Loan Offer Pitch" accentColor="#E31837" accentTint="#fdecef" lang={lang}>
        <PitchNarrative text={typeof pitch.loan_pitch_lines === 'string' ? pitch.loan_pitch_lines : ''} />
      </PitchSection>

      <PitchSection icon={Lightbulb} title="Objection Handling" accentColor="#E31837" accentTint="#fdecef" lang={lang}>
        {(() => {
          const items = (pitch.objection_handling || []).filter(o => o && (o.objection?.trim() || o.response?.trim()));
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No objections generated.</p>;
          return items.map((o, i) => (
            <div key={i} className="obj-pair">
              {o.objection?.trim() && <div className="obj-objection"><AlertTriangle size={13} /> {o.objection}</div>}
              {o.response?.trim() && <div className="obj-response">{o.response}</div>}
            </div>
          ));
        })()}
      </PitchSection>

      <PitchSection icon={Target} title="Closing Strategy" accentColor="#0A1628" accentTint="#eef2f7" lang={lang}>
        <PitchNarrative text={typeof pitch.closing_strategy === 'string' ? pitch.closing_strategy : ''} />
      </PitchSection>

      <PitchSection icon={ShieldAlert} title="What NOT to Say" accentColor="#F59E0B" accentTint="#fffbeb" lang={lang}>
        {(() => {
          const items = Array.isArray(pitch.what_not_to_say)
            ? pitch.what_not_to_say
            : typeof pitch.what_not_to_say === 'string'
              ? pitch.what_not_to_say.split('\n').map(l => l.replace(/^\s*\d+[.)]\s*/, '').trim()).filter(Boolean)
              : [];
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No warnings generated.</p>;
          return items.map((w, i) => <div key={i} className="warn-item">{cleanText(w)}</div>);
        })()}
      </PitchSection>

      <PitchSection icon={ClipboardList} title="Documents Checklist" accentColor="#0EA5E9" accentTint="#f0f9ff" lang={lang}>
        {(() => {
          const items = Array.isArray(pitch.documents_checklist)
            ? pitch.documents_checklist
            : typeof pitch.documents_checklist === 'string'
              ? pitch.documents_checklist.split('\n').map(l => l.replace(/^\s*[-*•·\d.)\s]+/, '').trim()).filter(Boolean)
              : [];
          if (!items.length) return <p style={{ color: 'var(--color-text-faint)', fontSize: 13 }}>No checklist generated.</p>;
          return <ol className="pitch-numbered">{items.map((d, i) => <li key={i}>{cleanText(d)}</li>)}</ol>;
        })()}
      </PitchSection>

      <PitchSection icon={FileText} title="Full Call Script" accentColor="#7C3AED" accentTint="#f5f3ff" lang={lang}>
        <div className="narrative-box">
          <PitchNarrative text={typeof pitch.full_script === 'string' ? pitch.full_script : ''} />
        </div>
      </PitchSection>

      {pitch.follow_up_template && (
        <PitchSection icon={Mail} title="Follow-Up Template" accentColor="#0EA5E9" accentTint="#f0f9ff" lang={lang}>
          <div className="narrative-box" style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
            {pitch.follow_up_template}
          </div>
        </PitchSection>
      )}
    </div>
  );
}

function GeneratorPage({
  loading, pitch, pitchStream, docsUsed, lastScenario, lastLang, handleGenerate, showToast, seedScenario, onSeedConsumed,
  scLoading, scPitch, scPitchStream, scLastScenario, scLastLang, handleServiceCallGenerate,
}) {
  // 'new' = New Customer pitch, 'sc' = Service Call pitch
  const [view, setView] = useState('new');
  const [mode, setMode] = useState(seedScenario ? 'free' : 'quick');
  const [scMode, setScMode] = useState('quick');

  useEffect(() => {
    if (seedScenario) { setView('new'); setMode('free'); }
  }, [seedScenario]);

  const outputAnchorRef = useRef(null);
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (loading && !prevLoadingRef.current && outputAnchorRef.current) {
      outputAnchorRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    prevLoadingRef.current = loading;
  }, [loading]);

  const scOutputAnchorRef = useRef(null);
  const prevScLoadingRef = useRef(false);
  useEffect(() => {
    if (scLoading && !prevScLoadingRef.current && scOutputAnchorRef.current) {
      scOutputAnchorRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    prevScLoadingRef.current = scLoading;
  }, [scLoading]);

  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Pitch Generator</h2>
          <p className="page-subtitle">
            {view === 'new'
              ? 'New customer sales pitch — document-grounded, section-by-section'
              : 'Existing customer service-cum-sales call coaching'}
          </p>
        </div>
      </div>

      {/* ── Page-level tab switcher ──────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 20, border: '1px solid var(--color-border)', borderRadius: 10, overflow: 'hidden', background: 'var(--color-surface)' }}>
        <button
          onClick={() => setView('new')}
          style={{
            flex: 1, padding: '13px 20px', border: 'none', cursor: 'pointer', fontWeight: 600,
            fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            background: view === 'new' ? 'var(--color-red)' : 'transparent',
            color: view === 'new' ? '#fff' : 'var(--color-text-muted)',
            borderRight: '1px solid var(--color-border)',
            transition: 'background 0.15s, color 0.15s',
          }}
        >
          <Sparkles size={15} />
          New Customer Pitch
        </button>
        <button
          onClick={() => setView('sc')}
          style={{
            flex: 1, padding: '13px 20px', border: 'none', cursor: 'pointer', fontWeight: 600,
            fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            background: view === 'sc' ? '#0A1628' : 'transparent',
            color: view === 'sc' ? '#fff' : 'var(--color-text-muted)',
            transition: 'background 0.15s, color 0.15s',
          }}
        >
          <PhoneCall size={15} />
          Existing Customer — Service Call
        </button>
      </div>

      {/* ── New Customer view ────────────────────────────────────────────── */}
      {view === 'new' && (
        <>
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                <Sparkles size={18} style={{ color: 'var(--color-red)' }} />
                New Customer — Sales Pitch
              </div>
              <div className="segmented">
                <button className={`segmented-btn${mode === 'quick' ? ' active' : ''}`} onClick={() => setMode('quick')}>
                  <FileText size={13} /> Quick Form
                </button>
                <button className={`segmented-btn${mode === 'free' ? ' active' : ''}`} onClick={() => setMode('free')}>
                  <PenLine size={13} /> Free Write
                </button>
              </div>
            </div>
            <div className="card-body">
              {mode === 'quick'
                ? <QuickForm onGenerate={handleGenerate} loading={loading} />
                : <FreeWriteForm onGenerate={handleGenerate} loading={loading}
                    seedScenario={seedScenario} onSeedConsumed={onSeedConsumed} />
              }
            </div>
          </div>

          <div ref={outputAnchorRef} />
          {(loading || pitchStream) && <PitchStreamProgress stream={pitchStream} />}
          {loading && !pitchStream && (
            <div className="loading-card">
              <div className="spinner" />
              <div className="loading-card-title">Generating your pitch…</div>
              <div className="loading-card-hint">Starting pitch session…</div>
            </div>
          )}
          {!loading && pitch && (
            <div className="card card-padded">
              <PitchOutput
                pitch={pitch}
                docsUsed={docsUsed}
                lang={lastLang}
                onRegenerate={() => handleGenerate(lastScenario, lastLang)}
                onSave={() => showToast('Pitch is auto-saved to history', 'success')}
                showToast={showToast}
              />
            </div>
          )}
        </>
      )}

      {/* ── Service Call (ETB) view ──────────────────────────────────────── */}
      {view === 'sc' && (
        <>
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                <PhoneCall size={18} style={{ color: '#0A1628' }} />
                Existing Customer — Service Call Pitch
              </div>
              <div className="segmented">
                <button className={`segmented-btn${scMode === 'quick' ? ' active' : ''}`} onClick={() => setScMode('quick')}>
                  <FileText size={13} /> Quick Form
                </button>
                <button className={`segmented-btn${scMode === 'free' ? ' active' : ''}`} onClick={() => setScMode('free')}>
                  <PenLine size={13} /> Free Write
                </button>
              </div>
            </div>

            <div style={{
              padding: '12px 24px 14px',
              background: 'rgba(251,146,60,0.14)',
              borderBottom: '1px solid rgba(251,146,60,0.28)',
              display: 'flex', alignItems: 'flex-start', gap: 10,
            }}>
              <AlertTriangle size={14} style={{ color: '#FDBA74', flexShrink: 0, marginTop: 2 }} />
              <p style={{ fontSize: 12.5, color: '#FDD7A8', margin: 0, lineHeight: 1.5 }}>
                <strong>ETB Only:</strong> This provision is only for ETB (Existing to Bank) customers.
                Proceed to sales call only if the existing customer provides good feedback against existing facility availed.
              </p>
            </div>

            <div className="card-body">
              {scMode === 'quick'
                ? <ServiceCallQuickForm onGenerate={handleServiceCallGenerate} loading={scLoading} />
                : <ServiceCallFreeWriteForm onGenerate={handleServiceCallGenerate} loading={scLoading} />
              }
            </div>
          </div>

          <div ref={scOutputAnchorRef} />
          {(scLoading || scPitchStream) && <PitchStreamProgress stream={scPitchStream} />}
          {scLoading && !scPitchStream && (
            <div className="loading-card">
              <div className="spinner" />
              <div className="loading-card-title">Generating service call pitch…</div>
              <div className="loading-card-hint">Starting service call pitch session…</div>
            </div>
          )}
          {!scLoading && scPitch && (
            <div className="card card-padded">
              <ServiceCallOutput
                pitch={scPitch}
                lang={scLastLang}
                onRegenerate={() => handleServiceCallGenerate(scLastScenario, scLastLang)}
                showToast={showToast}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── ASK ME PAGE ──────────────────────────────────────────────────────────────

function ChatSessionRow({ session, active, onOpen, onRename, onDelete, onShowTooltip, onHideTooltip }) {
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  return (
    <div className={`chat-session-row${active ? ' active' : ''}`}
      onMouseEnter={e => !renaming && onShowTooltip && onShowTooltip(session.name, e.currentTarget.getBoundingClientRect())}
      onMouseLeave={() => onHideTooltip && onHideTooltip()}
      onClick={() => !renaming && !confirmingDelete && onOpen(session.id)}>
      <div className="chat-session-row-content">
        {renaming ? (
          <InlineRename
            value={session.name}
            onSave={(name) => { onRename(session.id, name); setRenaming(false); }}
            onCancel={() => setRenaming(false)}
          />
        ) : (
          <div className="chat-session-row-name" title={session.name}>{session.name}</div>
        )}
        <div className="chat-session-row-meta">
          <span>{formatTime(session.updated_at || session.created_at)}</span>
          {session.message_count > 0 && (
            <><span className="sep">·</span><span>{session.message_count} msg</span></>
          )}
        </div>
      </div>
      {confirmingDelete ? (
        <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
          <span>Delete?</span>
          <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: 12 }}
            onClick={() => setConfirmingDelete(false)}>Cancel</button>
          <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: 12 }}
            onClick={() => { onDelete(session.id); setConfirmingDelete(false); }}>Delete</button>
        </div>
      ) : (
        <div className="chat-session-row-actions" onClick={e => e.stopPropagation()}>
          <button className="icon-btn" title="Rename" onClick={() => setRenaming(true)}>
            <Pencil size={13} />
          </button>
          <button className="icon-btn danger" title="Delete" onClick={() => setConfirmingDelete(true)}>
            <Trash2 size={13} />
          </button>
        </div>
      )}
    </div>
  );
}

function AskMePage({ navigate, onSeedPitch }) {
  const [sessions, setSessions] = useState([]);
  const [sessionsError, setSessionsError] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [activeSessionName, setActiveSessionName] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sessionTooltip, setSessionTooltip] = useState(null); // { text, rect }
  const [mentorMode, setMentorMode] = useState('chat'); // 'chat' | 'voice'

  const fetchSessions = (autoOpenLatest = false) => {
    setSessionsError(false);
    fetch(`${API}/api/chat/sessions`)
      .then(r => r.json())
      .then(d => {
        if (d.success) {
          const list = d.sessions || [];
          setSessions(list);
          if (autoOpenLatest && list.length > 0 && !activeSessionId) {
            setActiveSessionId(list[0].id);
          }
        } else {
          setSessionsError(true);
        }
      })
      .catch(() => setSessionsError(true));
  };

  useEffect(() => { fetchSessions(true); }, []);

  // Keep the displayed session name in sync with the latest list snapshot
  useEffect(() => {
    if (activeSessionId) {
      const found = sessions.find(s => s.id === activeSessionId);
      if (found) setActiveSessionName(found.name);
    } else {
      setActiveSessionName('');
    }
  }, [activeSessionId, sessions]);

  const handleOpenSession = (id) => setActiveSessionId(id);

  const handleNewChat = () => {
    setActiveSessionId(null);
    setActiveSessionName('');
  };

  const handleSessionUpdated = (id, name) => {
    // Called by ChatPanel after each send. Pin the session id (a fresh
    // chat gets one assigned on first send), refresh the sidebar list.
    setActiveSessionId(id);
    if (name) setActiveSessionName(name);
    fetchSessions();
  };

  const handleRename = async (id, newName) => {
    try {
      await fetch(`${API}/api/chat/sessions/${id}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      fetchSessions();
    } catch { }
  };

  const handleDelete = async (id) => {
    try {
      await fetch(`${API}/api/chat/sessions/${id}`, { method: 'DELETE' });
      if (id === activeSessionId) setActiveSessionId(null);
      fetchSessions();
    } catch { }
  };

  const handleClearAll = async () => {
    if (!window.confirm('Delete ALL chat sessions? This cannot be undone.')) return;
    await fetch(`${API}/api/chat/sessions`, { method: 'DELETE' });
    setActiveSessionId(null);
    fetchSessions();
  };

  return (
    <>
      <div className="chatgpt-layout">
        {sidebarOpen && (
          <div className="chatgpt-overlay" onClick={() => setSidebarOpen(false)} />
        )}

        <aside className={`chatgpt-sidebar${sidebarOpen ? ' open' : ''}`}>
          <div className="chatgpt-sidebar-top">
            <div className="chatgpt-sidebar-brand">
              <MessageSquare size={15} style={{ color: 'var(--color-red)' }} />
              <span>Chat Sessions</span>
            </div>
            <button className="chatgpt-close-btn" onClick={() => setSidebarOpen(false)}>×</button>
          </div>

          <button className="btn btn-primary chatgpt-new-btn"
            onClick={() => { handleNewChat(); setSidebarOpen(false); }}>
            <Sparkles size={14} /> New Chat
          </button>

          <div className="chatgpt-sessions-list">
            {sessionsError ? (
              <div className="chat-sessions-empty">
                <div style={{ fontSize: 22, marginBottom: 8 }}>⚠️</div>
                <div style={{ fontSize: 12, color: 'var(--color-red)', fontWeight: 600 }}>
                  Backend offline
                </div>
                <div style={{ fontSize: 11, marginTop: 4, color: 'var(--color-text-faint)' }}>
                  Start the server then <span
                    style={{ color: 'var(--color-red)', cursor: 'pointer', textDecoration: 'underline' }}
                    onClick={() => fetchSessions(true)}>retry</span>
                </div>
              </div>
            ) : sessions.length === 0 ? (
              <div className="chat-sessions-empty">
                <MessageSquare size={22} style={{ color: 'var(--color-text-faint)', marginBottom: 8 }} />
                <div style={{ fontSize: 12 }}>
                  No saved chats yet.<br />Send a message — it's named automatically.
                </div>
              </div>
            ) : (
              sessions.map(s => (
                <ChatSessionRow
                  key={s.id}
                  session={s}
                  active={s.id === activeSessionId}
                  onOpen={(id) => { handleOpenSession(id); setSidebarOpen(false); }}
                  onRename={handleRename}
                  onDelete={handleDelete}
                  onShowTooltip={(text, rect) => setSessionTooltip({ text, rect })}
                  onHideTooltip={() => setSessionTooltip(null)}
                />
              ))
            )}
          </div>

          {sessions.length > 0 && (
            <div className="chatgpt-sidebar-footer">
              <button className="btn btn-danger-outline btn-block"
                style={{ fontSize: 12, padding: '7px 12px' }}
                onClick={handleClearAll}>
                <Trash2 size={13} /> Clear All
              </button>
            </div>
          )}
        </aside>

        <div className="chatgpt-main">
          <div className="mentor-mode-toggle">
            <button
              className={`mentor-mode-btn${mentorMode === 'chat' ? ' active' : ''}`}
              onClick={() => setMentorMode('chat')}
            >
              <MessageSquare size={14} /> Chat
            </button>
            <button
              className={`mentor-mode-btn${mentorMode === 'voice' ? ' active' : ''}`}
              onClick={() => setMentorMode('voice')}
            >
              <Mic size={14} /> Voice Assistant
            </button>
          </div>

          {mentorMode === 'voice' ? (
            <div className="mentor-voice-wrap">
              <EvaVoice />
            </div>
          ) : (
            <ChatPanel
              navigate={navigate}
              onSeedPitch={onSeedPitch}
              sessionId={activeSessionId}
              sessionName={activeSessionName}
              onSessionUpdated={handleSessionUpdated}
              onNewChat={handleNewChat}
              onOpenSidebar={() => setSidebarOpen(prev => !prev)}
            />
          )}
        </div>
      </div>
      {sessionTooltip && createPortal(

        <div style={{
          position: 'fixed',
          left: sessionTooltip.rect.right + 10,
          top: sessionTooltip.rect.top + sessionTooltip.rect.height / 2,
          transform: 'translateY(-50%)',
          background: '#1e293b',
          color: '#f1f5f9',
          fontSize: 12,
          fontWeight: 500,
          lineHeight: 1.5,
          padding: '8px 14px',
          borderRadius: 8,
          boxShadow: '0 8px 28px rgba(0,0,0,0.22), 0 2px 8px rgba(0,0,0,0.14)',
          maxWidth: 280,
          wordBreak: 'break-word',
          pointerEvents: 'none',
          zIndex: 99999,
          border: '1px solid rgba(255,255,255,0.08)',
        }}>
          {sessionTooltip.text}
        </div>,
        document.body
      )}
    </>
  );
}

// ── LIVE ASSISTANT PAGE ──────────────────────────────────────────────────────

function LivePage() {
  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Live Voice Assistant</h2>
          <p className="page-subtitle">Listens to customer questions in real-time and feeds you grounded answers</p>
        </div>
      </div>
      <LiveAssistant />
    </div>
  );
}

// ── CALL ASSISTANT PAGE ───────────────────────────────────────────────────────

function CallAssistantPage() {
  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Call Assistant</h2>
          <p className="page-subtitle">Real-time co-pilot — two-speaker diarization, streaming suggestions, &lt;1.5s end-to-end latency</p>
        </div>
      </div>
      <CallAssistant />
    </div>
  );
}

// ── CALL ASSISTANT V2 PAGE (Twilio live copilot) ──────────────────────────────

function CallAssistantV2Page() {
  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Call Assistant</h2>
          <p className="page-subtitle">Live Twilio call copilot — transcribes the customer, retrieves grounded HDFC facts, and tells you what to say in real time</p>
        </div>
      </div>
      <CallAssistantV2 />
    </div>
  );
}

// ── LOAN SUITE PAGE ───────────────────────────────────────────────────────────

function LoanSuitePage() {
  return (
    <div className="slide-up">
      <div className="page-header">
        <div>
          <h2 className="page-title">Loan Intelligence Suite</h2>
          <p className="page-subtitle">On-the-spot financial calculators — use while speaking with the customer</p>
        </div>
      </div>
      <LoanCalculators />
    </div>
  );
}

// ── HOME DASHBOARD (sales agent landing) ─────────────────────────────────────

// The four things the workspace is for. Purely orienting — no click targets,
// so the shortcuts below stay the only "do something" row on the page.
const HOME_PILLARS = [
  { icon: Target, title: 'Focus', sub: 'on what matters', accent: '#7C6BF5' },
  { icon: TrendingUp, title: 'Track', sub: 'your progress', accent: '#2F86F6' },
  { icon: Users, title: 'Manage', sub: 'your pipeline', accent: '#14B8A6' },
  { icon: Sparkles, title: 'Achieve', sub: 'your goals', accent: '#F59E0B' },
];

function HomePage({ navigate }) {
  const [pitches, setPitches] = useState([]);
  const [chats, setChats] = useState([]);
  const [docCount, setDocCount] = useState(null);
  const [stats, setStats] = useState(null);
  const [nextBooking, setNextBooking] = useState(null);

  useEffect(() => {
    fetch(`${API}/api/pitch/history`).then(r => r.json()).then(d => { if (d.success) setPitches(d.history || []); }).catch(() => {});
    fetch(`${API}/api/chat/sessions`).then(r => r.json()).then(d => { if (d.success) setChats(d.sessions || []); }).catch(() => {});
    fetch(`${API}/api/files`).then(r => r.json()).then(d => { if (d.success) setDocCount((d.files || []).length); }).catch(() => {});
    fetch(`${API}/api/me/stats`).then(r => r.json()).then(d => { if (d.success) setStats(d.stats); }).catch(() => {});
    // Soonest upcoming booked session, if any — surfaces on the hero card.
    fetch(`${API}/api/me/bookings`).then(r => r.json()).then(d => {
      if (d.success) {
        const up = (d.bookings || []).filter(b => b.slot && new Date(b.slot.end_at) > new Date());
        setNextBooking(up[0] || null);
      }
    }).catch(() => {});
  }, []);

  const timeline = [
    ...pitches.map(p => ({ kind: 'pitch', id: p.id, name: p.name, ts: p.updated_at || p.created_at })),
    ...chats.map(c => ({ kind: 'chat', id: c.id, name: c.name, ts: c.updated_at || c.created_at })),
  ].sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));

  // The newest item gets promoted to the resume card, so the list below starts
  // at the second — otherwise the same row appears twice.
  const resume = timeline[0] || null;
  const recent = timeline.slice(1, 7);
  const hasScore = !!stats && stats.avg_score != null;

  const shortcuts = [
    // The three modes, in the order of the new IA: learn → attend → practise.
    { icon: GraduationCap, label: 'Training', desc: 'Pitch generator, AI mentor & voice assistant', page: 'askme', accent: '#2563EB' },
    { icon: Calendar, label: 'Assistant', desc: 'Sessions assigned to you & slot booking', page: 'booking', accent: '#F59E0B' },
    { icon: Bot, label: 'Practice', desc: 'Self-practice with an AI customer', page: 'roleplay', accent: '#10B981' },
    // Hidden from the UI (page + route still exist in code):
    // { icon: Phone, label: 'Call Assistant', desc: 'Live help on customer calls', page: 'callassistv2', accent: '#10B981' },
  ];

  return (
    <div className="home slide-up">
      <div className="home-hero">
        <div>
          {/* No name here on purpose — the workspace is shared/demoed, and a
              stale or truncated account name reads worse than no name at all. */}
          <h1 className="home-title">Welcome back! 👋</h1>
          <p className="home-sub">Your sales workspace at a glance.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('generator')}><Sparkles size={15} /> New Pitch</button>
      </div>

      <div className="home-pillars">
        <span className="home-pillars-rule" aria-hidden="true" />
        <div className="home-pillars-row">
          {HOME_PILLARS.map(({ icon: Icon, title, sub, accent }) => (
            <div className="home-pillar" key={title}>
              <span className="home-pillar-ic" style={{ background: accent }}><Icon size={16} /></span>
              <span className="home-pillar-body">
                <span className="home-pillar-title">{title}</span>
                <span className="home-pillar-sub">{sub}</span>
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* At a glance — a thin strip, not five cards. These are context for the
          work below, not the point of the page. */}
      <div className="home-glance">
        <div className="home-glance-cell">
          <span className="home-glance-value">{stats ? stats.pitches : pitches.length}</span>
          <span className="home-glance-label">Pitches</span>
        </div>
        <div className="home-glance-cell">
          <span className="home-glance-value">{stats ? stats.chats : chats.length}</span>
          <span className="home-glance-label">Chats</span>
        </div>
        <div className="home-glance-cell">
          <span className="home-glance-value">{stats ? stats.roleplays : '—'}</span>
          <span className="home-glance-label">Roleplays</span>
        </div>
        <div className="home-glance-cell">
          <span className="home-glance-value">{docCount == null ? '—' : docCount}</span>
          <span className="home-glance-label">Documents</span>
        </div>
      </div>

      {/* Next booked session — when one exists it outranks everything else on
          this page: it's the only item here with a clock attached. */}
      {nextBooking && (
        <button className="home-resume" onClick={() => navigate('booking')}
          style={{ borderColor: '#F59E0B' }}>
          <span className="home-resume-ic" style={{ background: '#FEF3C7', color: '#92400E' }}>
            <Calendar size={18} />
          </span>
          <span className="home-resume-body">
            <span className="home-resume-eyebrow">Your next session</span>
            <span className="home-resume-name">
              {nextBooking.slot.title || (nextBooking.slot.kind === 'assistant' ? 'Scheduled role-play' : 'Practice hour')}
            </span>
          </span>
          <span className="home-resume-meta">{formatTime(nextBooking.slot.start_at)}</span>
          <span className="home-resume-go">Open <ArrowRight size={15} /></span>
        </button>
      )}

      {/* Resume — the single most valuable thing on this page: one click back
          into whatever the agent was last working on. */}
      {resume && (
        <button className="home-resume" onClick={() => navigate(resume.kind === 'pitch' ? 'history' : 'askme')}>
          <span className="home-resume-ic">
            {resume.kind === 'pitch' ? <Sparkles size={18} /> : <MessageSquare size={18} />}
          </span>
          <span className="home-resume-body">
            <span className="home-resume-eyebrow">Pick up where you left off</span>
            <span className="home-resume-name">{resume.name || '(untitled)'}</span>
          </span>
          <span className="home-resume-meta">{resume.ts ? formatTime(resume.ts) : ''}</span>
          <span className="home-resume-go">Open <ArrowRight size={15} /></span>
        </button>
      )}

      <div className="home-section-label">Choose a mode</div>
      <div className="home-shortcuts">
        {shortcuts.map(s => {
          const Icon = s.icon;
          return (
            <button key={s.page} className="home-shortcut" onClick={() => navigate(s.page)}>
              <span className="home-shortcut-ic" style={{ background: s.accent }}><Icon size={18} /></span>
              <span className="home-shortcut-body">
                <span className="home-shortcut-label">{s.label}</span>
                <span className="home-shortcut-desc">{s.desc}</span>
              </span>
              <ArrowRight size={16} className="home-shortcut-arrow" />
            </button>
          );
        })}
      </div>

      <div className="home-cols">
        <div className="home-col">
          <div className="home-section-label" style={{ marginTop: 22 }}>Recent activity</div>
          <div className="home-card">
            {recent.length === 0 ? (
              <div className="home-empty">
                {timeline.length === 0
                  ? 'No activity yet — generate a pitch or start a chat to get going.'
                  : "That's everything so far."}
              </div>
            ) : recent.map(r => (
              <button key={r.kind + r.id} className="home-activity" onClick={() => navigate(r.kind === 'pitch' ? 'history' : 'askme')}>
                <span className={`home-activity-ic ${r.kind}`}>{r.kind === 'pitch' ? <Sparkles size={14} /> : <MessageSquare size={14} />}</span>
                <span className="home-activity-name">{r.name || '(untitled)'}</span>
                <span className="home-activity-meta">{r.ts ? formatTime(r.ts) : ''}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Coaching, not notifications: the roleplay score is the one number on
            this page that tells an agent what to do next. */}
        <div className="home-col home-col--side">
          <div className="home-section-label" style={{ marginTop: 22 }}>Your practice</div>
          <div className="home-card home-practice">
            {hasScore ? (
              <>
                <div className="home-practice-score">
                  <span className="home-practice-value">{stats.avg_score}</span>
                  <span className="home-practice-outof">/ 100</span>
                </div>
                <div className="home-practice-meter">
                  <span className="home-practice-fill" style={{ width: `${Math.min(100, Math.max(0, stats.avg_score))}%` }} />
                </div>
                <div className="home-practice-caption">
                  Average across {stats.roleplays || 0} roleplay{stats.roleplays === 1 ? '' : 's'}
                </div>
              </>
            ) : (
              <div className="home-practice-empty">
                <Award size={24} />
                <div className="home-practice-empty-title">No score yet</div>
                <div className="home-practice-empty-sub">Finish a roleplay and you'll be graded on it.</div>
              </div>
            )}
            <button className="btn btn-secondary btn-block" onClick={() => navigate('roleplay')}>
              <Headphones size={15} /> Start a roleplay
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── GLOBAL FLOATING AI ASSISTANT ─────────────────────────────────────────────
// A draggable chat bubble available on every page (except the full Chat Bot
// page, where it would duplicate the same panel). Reuses ChatPanel + the same
// backend chat sessions, so a conversation started here shows up in the Chat
// Bot page's session sidebar. Button position and the active session id are
// remembered in localStorage across reloads.

const FAB_POS_KEY = 'pv_fab_pos';
const FAB_SESSION_KEY = 'pv_fab_session';
const FAB_SIZE = 60;   // must match .fab-btn width/height in index.css so clamping keeps it clear of the bottom nav
const FAB_DRAG_THRESHOLD = 6;   // px of movement before a press counts as a drag, not a click

function clampFabPos(p) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // Keep clear of the fixed bottom tab bar on phones (≤640px, --bottom-nav-h: 66px)
  const bottomInset = vw <= 640 ? 74 : 12;
  return {
    x: Math.min(Math.max(p.x, 8), Math.max(8, vw - FAB_SIZE - 8)),
    y: Math.min(Math.max(p.y, 8), Math.max(8, vh - FAB_SIZE - bottomInset)),
  };
}

function loadFabPos() {
  try {
    const saved = JSON.parse(localStorage.getItem(FAB_POS_KEY) || 'null');
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) return clampFabPos(saved);
  } catch { /* corrupt value — fall through to default */ }
  return clampFabPos({ x: 20, y: window.innerHeight - FAB_SIZE - 20 });   // bottom-left default
}

function FloatingAssistant({ navigate, onSeedPitch, hidden }) {
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);   // panel stays mounted after first open so a streaming reply survives close/re-open
  const [pos, setPos] = useState(loadFabPos);
  const [dragging, setDragging] = useState(false);
  const [sessionId, setSessionId] = useState(() => {
    try { return localStorage.getItem(FAB_SESSION_KEY) || null; } catch { return null; }
  });
  const [sessionName, setSessionName] = useState('');
  const dragRef = useRef(null);   // { pointerId, startX, startY, origX, origY, moved }

  // Keep the bubble inside the viewport when the window is resized
  useEffect(() => {
    const onResize = () => setPos(p => clampFabPos(p));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const onPointerDown = (e) => {
    try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* capture is an optimisation — drag still tracks via button events */ }
    dragRef.current = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, origX: pos.x, origY: pos.y, moved: false };
  };

  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < FAB_DRAG_THRESHOLD) return;
    d.moved = true;
    setDragging(true);
    setPos(clampFabPos({ x: d.origX + dx, y: d.origY + dy }));
  };

  const onPointerUp = (e) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (d.moved) {
      setPos(p => {
        try { localStorage.setItem(FAB_POS_KEY, JSON.stringify(p)); } catch { /* storage disabled */ }
        return p;
      });
    } else {
      setOpen(o => !o);
      setEverOpened(true);
    }
  };

  const handleSessionUpdated = (id, name) => {
    setSessionId(id);
    if (name) setSessionName(name);
    try { localStorage.setItem(FAB_SESSION_KEY, id); } catch { /* storage disabled */ }
  };

  const handleNewChat = () => {
    setSessionId(null);
    setSessionName('');
    try { localStorage.removeItem(FAB_SESSION_KEY); } catch { /* storage disabled */ }
  };

  if (hidden) return null;

  // Panel opens above the bubble when there's room, below otherwise; horizontal
  // edge is clamped so it never leaves the viewport no matter where the bubble is.
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const panelW = Math.min(420, vw - 24);
  const panelH = Math.min(560, vh - 48);
  let panelLeft = pos.x;
  if (panelLeft + panelW > vw - 12) panelLeft = Math.max(12, vw - panelW - 12);
  let panelTop = pos.y - panelH - 12;
  const opensUp = panelTop >= 12;
  if (!opensUp) panelTop = Math.min(Math.max(12, pos.y + FAB_SIZE + 12), Math.max(12, vh - panelH - 12));

  return createPortal(
    <>
      {everOpened && (
        <div
          className="fab-panel"
          style={{
            left: panelLeft, top: panelTop, width: panelW, height: panelH,
            display: open ? 'flex' : 'none',
            transformOrigin: opensUp ? 'bottom left' : 'top left',
          }}
        >
          <ChatPanel
            compact
            navigate={(page) => { setOpen(false); navigate?.(page); }}
            onSeedPitch={onSeedPitch}
            sessionId={sessionId}
            sessionName={sessionName}
            onSessionUpdated={handleSessionUpdated}
            onSessionNameLoaded={(name) => setSessionName(name)}
            onNewChat={handleNewChat}
            onClose={() => setOpen(false)}
            onExpand={() => { setOpen(false); navigate?.('askme'); }}
          />
        </div>
      )}
      {/* "Ask me !" callout — visible while the assistant is closed; follows
          the bubble and flips sides so it never leaves the screen. */}
      {!open && !dragging && (
        <button
          className={`fab-callout${pos.x + FAB_SIZE / 2 > vw / 2 ? ' flip' : ''}`}
          style={
            pos.x + FAB_SIZE / 2 > vw / 2
              ? { right: vw - pos.x + 10, top: pos.y + FAB_SIZE / 2 }
              : { left: pos.x + FAB_SIZE + 10, top: pos.y + FAB_SIZE / 2 }
          }
          onClick={() => { setOpen(true); setEverOpened(true); }}
        >
          Eva 2.0
        </button>
      )}
      <button
        className={`fab-btn${dragging ? ' dragging' : ''}${open ? ' open' : ''}`}
        data-tour="fab"
        style={{ left: pos.x, top: pos.y }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => { dragRef.current = null; setDragging(false); }}
        onClick={(e) => {
          // Keyboard activation (Enter/Space) produces a click with detail 0 and
          // no pointer sequence — handle it here; pointer clicks toggle in pointerup.
          if (e.detail === 0) { setOpen(o => !o); setEverOpened(true); }
        }}
        title={open ? 'Close AI assistant' : 'AI assistant — drag to move'}
        aria-label={open ? 'Close AI assistant' : 'Open AI assistant'}
      >
        {open
          ? <X size={22} />
          : <img src="/chatbot-fab.png" alt="" className="fab-mascot" draggable={false} />}
      </button>
    </>,
    document.body
  );
}

// ── MAIN APP ─────────────────────────────────────────────────────────────────

// Three-mode information architecture: Training (learn & prepare), Assistant
// (admin-scheduled sessions the agent books into), Practice (independent
// role-play). Page ids are unchanged — this is grouping, not rerouting — so
// deep links and the product tour keep working.
const NAV_GROUPS = [
  {
    label: 'Workspace',
    items: [{ id: 'home', label: 'Home', short: 'Home', icon: HomeIcon }],
  },
  {
    label: 'Training',
    items: [
      { id: 'generator', label: 'Pitch Generator', short: 'Pitch', icon: FileEdit },
      { id: 'askme', label: 'AI Mentor', short: 'Mentor', icon: GraduationCap },
      { id: 'loansuite', label: 'Loan Suite', short: 'Loans', icon: Calculator },
      { id: 'history', label: 'Pitch History', short: 'History', icon: History },
    ],
  },
  {
    label: 'Assistant',
    items: [
      { id: 'booking', label: 'My Sessions', short: 'Sessions', icon: Calendar },
    ],
  },
  {
    label: 'Practice',
    items: [
      // Bot, not a headset: lucide 0.263 has no `Headset` glyph (added upstream
      // later), and the thing you practise against here *is* an AI avatar.
      { id: 'roleplay', label: 'RolePlay Simulation', short: 'RolePlay', icon: Bot },
    ],
  },
  // Hidden from the sidebar (pages + routes still exist in code):
  // { id: 'live', label: 'Live Assistant', short: 'Live', icon: Mic },
  // { id: 'callassistv2', label: 'Call Assistant', short: 'Call', icon: Phone },
  // { id: 'callassist', label: 'Call Assistant (v1 Exotel)', short: 'Call', icon: Activity },
  // { id: 'docs',      label: 'Documents',       icon: FolderOpen },
  // { id: 'inspector', label: 'RAG Inspector',   icon: Activity },
];

function MainApp({ user, onSignOut }) {
  const [activePage, setActivePage] = useState('home');
  // Booked-seat handoff: set by Book a Session's Join, consumed by RolePlay.
  // Assistant seats auto-launch the admin's pinned scenario; practice seats
  // open the normal chooser with the booking attached.
  const [bookedLaunch, setBookedLaunch] = useState(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [acctUser, setAcctUser] = useState(user);
  const [files, setFiles] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [pitch, setPitch] = useState(null);
  const [docsUsed, setDocsUsed] = useState([]);
  // Section-by-section streaming progress for /api/pitch/stream
  const [pitchStream, setPitchStream] = useState(null);  // {sections: [{id,label,status,content,structured,ms,word_count}], model, ms_total}
  const [openConvId, setOpenConvId] = useState(null);
  const [lastScenario, setLastScenario] = useState('');
  const [lastLang, setLastLang] = useState('English');
  const [seedScenario, setSeedScenario] = useState('');
  // Service Call pitch state
  const [scLoading, setScLoading] = useState(false);
  const [scPitch, setScPitch] = useState(null);
  const [scPitchStream, setScPitchStream] = useState(null);
  const [scLastScenario, setScLastScenario] = useState('');
  const [scLastLang, setScLastLang] = useState('English');
  const { toast, show: showToast } = useToast();

  // ── Product tour ──────────────────────────────────────────────────────────
  // Finishing OR explicitly skipping both persist "done" (server + local
  // mirror); an interrupted run persists nothing, so it re-offers next login.
  const handleTourFinish = useCallback(() => {
    persistTourDone(user).then((freshUser) => {
      if (freshUser) { setStoredUser(freshUser); setAcctUser(prev => ({ ...prev, ...freshUser })); }
    });
  }, [user]);

  const launchTour = useCallback(() => {
    startProductTour({ navigate: setActivePage, onFinish: handleTourFinish });
  }, [handleTourFinish]);

  // Auto-run on sign-in until completed or explicitly skipped. The short delay
  // lets the workspace paint first so the spotlight has real targets.
  useEffect(() => {
    if (!shouldAutoStartTour(user)) return;
    const t = setTimeout(launchTour, 800);
    return () => clearTimeout(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetch(`${API}/api/files`).then(r => r.json()).then(d => { if (d.success) setFiles(d.files); }).catch(() => { });
    fetchHistory();
  }, []);

  const fetchHistory = () => {
    fetch(`${API}/api/pitch/history`).then(r => r.json()).then(d => { if (d.success) setHistory(d.history); }).catch(() => { });
  };

  const handleGenerate = async (scenario, language) => {
    setLoading(true);
    setPitch(null);
    setPitchStream(null);
    setOpenConvId(null);
    setLastScenario(scenario);
    setLastLang(language);
    try {
      const res = await fetch(`${API}/api/pitch/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, language }),
      });
      if (!res.ok) {
        showToast(`Pitch stream failed (${res.status})`, 'error');
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === 'session_start') {
            setPitchStream({
              sections: evt.sections.map(s => ({
                id: s.id, label: s.label, status: 'pending',
                content: '', structured: null, ms: 0, word_count: 0,
              })),
              model: evt.model,
              total: evt.total_sections,
              docs: evt.documents_used || [],
            });
            setDocsUsed(evt.documents_used || []);
          } else if (evt.type === 'section_start') {
            setPitchStream(prev => prev && ({
              ...prev,
              sections: prev.sections.map(s =>
                s.id === evt.section_id ? { ...s, status: 'generating' } : s),
            }));
          } else if (evt.type === 'section_done') {
            setPitchStream(prev => prev && ({
              ...prev,
              sections: prev.sections.map(s =>
                s.id === evt.section_id ? {
                  ...s, status: 'done',
                  content: evt.content,
                  structured: evt.structured,
                  ms: evt.ms,
                  word_count: evt.word_count,
                  retried: evt.retried,
                } : s),
            }));
          } else if (evt.type === 'section_error') {
            setPitchStream(prev => prev && ({
              ...prev,
              sections: prev.sections.map(s =>
                s.id === evt.section_id ? { ...s, status: 'error', error: evt.error } : s),
            }));
          } else if (evt.type === 'pitch_complete') {
            setPitch(evt.pitch);
            setDocsUsed(evt.documents_used || []);
            setPitchStream(prev => prev && ({ ...prev, ms_total: evt.ms_total, docs: evt.documents_used || [] }));
            fetchHistory();
            showToast(`Pitch ready in ${(evt.ms_total / 1000).toFixed(1)}s`, 'success');
          }
        }
      }
    } catch (err) {
      showToast(shortError(err.message), 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleServiceCallGenerate = async (scenario, language) => {
    setScLoading(true);
    setScPitch(null);
    setScPitchStream(null);
    setScLastScenario(scenario);
    setScLastLang(language);
    try {
      const res = await fetch(`${API}/api/pitch/service-call/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, language }),
      });
      if (!res.ok) {
        showToast(`Service call pitch failed (${res.status})`, 'error');
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }
          if (evt.type === 'session_start') {
            setScPitchStream({
              sections: evt.sections.map(s => ({
                id: s.id, label: s.label, status: 'pending',
                content: '', structured: null, ms: 0, word_count: 0,
              })),
              model: evt.model,
              total: evt.total_sections,
              docs: evt.documents_used || [],
            });
          } else if (evt.type === 'section_start') {
            setScPitchStream(prev => prev && ({
              ...prev,
              sections: prev.sections.map(s =>
                s.id === evt.section_id ? { ...s, status: 'generating' } : s),
            }));
          } else if (evt.type === 'section_done') {
            setScPitchStream(prev => prev && ({
              ...prev,
              sections: prev.sections.map(s =>
                s.id === evt.section_id ? {
                  ...s, status: 'done',
                  content: evt.content,
                  structured: evt.structured,
                  ms: evt.ms,
                  word_count: evt.word_count,
                  retried: evt.retried,
                } : s),
            }));
          } else if (evt.type === 'section_error') {
            setScPitchStream(prev => prev && ({
              ...prev,
              sections: prev.sections.map(s =>
                s.id === evt.section_id ? { ...s, status: 'error', error: evt.error } : s),
            }));
          } else if (evt.type === 'pitch_complete') {
            setScPitch(evt.pitch);
            setScPitchStream(prev => prev && ({ ...prev, ms_total: evt.ms_total }));
            fetchHistory();
            showToast(`Service call pitch ready in ${(evt.ms_total / 1000).toFixed(1)}s`, 'success');
          }
        }
      }
    } catch (err) {
      showToast(shortError(err.message), 'error');
    } finally {
      setScLoading(false);
    }
  };

  const handleDeleteHistory = async (id) => {
    try {
      await fetch(`${API}/api/pitch/history/${id}`, { method: 'DELETE' });
      if (openConvId === id) setOpenConvId(null);
      fetchHistory();
      showToast('Conversation deleted', 'success');
    } catch { showToast('Delete failed', 'error'); }
  };

  const handleRenameHistory = async (id, newName) => {
    if (!id) { fetchHistory(); return; }
    try {
      await fetch(`${API}/api/pitch/history/${id}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      fetchHistory();
    } catch { showToast('Rename failed', 'error'); }
  };

  const handleClearAllHistory = async () => {
    if (!window.confirm('Clear all pitch history? This cannot be undone.')) return;
    await fetch(`${API}/api/pitch/history`, { method: 'DELETE' });
    setHistory([]);
    setOpenConvId(null);
    showToast('History cleared', 'success');
  };

  // When switching pages, clear openConvId unless we're on history page
  useEffect(() => {
    if (activePage !== 'history') setOpenConvId(null);
  }, [activePage]);

  const renderPage = () => {
    switch (activePage) {
      case 'home': return <HomePage navigate={setActivePage} />;
      case 'booking':
        return (
          <BookSession
            onJoin={(b) => { setBookedLaunch(b); setActivePage('roleplay'); }}
          />
        );
      case 'roleplay':
        return (
          <RolePlaySimulation
            bookedLaunch={bookedLaunch}
            onBookedConsumed={() => setBookedLaunch(null)}
            onGoToBooking={() => setActivePage('booking')}
            onBack={() => setActivePage('booking')}
          />
        );
      case 'generator':
        return (
          <GeneratorPage
            loading={loading} pitch={pitch} docsUsed={docsUsed}
            pitchStream={pitchStream}
            lastScenario={lastScenario} lastLang={lastLang}
            handleGenerate={handleGenerate} showToast={showToast}
            seedScenario={seedScenario}
            onSeedConsumed={() => setSeedScenario('')}
            scLoading={scLoading} scPitch={scPitch} scPitchStream={scPitchStream}
            scLastScenario={scLastScenario} scLastLang={scLastLang}
            handleServiceCallGenerate={handleServiceCallGenerate}
          />
        );
      case 'askme':
        return (
          <AskMePage
            navigate={setActivePage}
            onSeedPitch={(scenario) => setSeedScenario(scenario)}
          />
        );
      case 'live': return <LivePage />;
      case 'callassistv2': return <CallAssistantV2Page />;
      case 'callassist': return <CallAssistantPage />;
      case 'loansuite': return <LoanSuitePage />;
      case 'history':
        return (
          <HistoryPage
            history={history}
            openConvId={openConvId}
            setOpenConvId={setOpenConvId}
            onDelete={handleDeleteHistory}
            onRename={handleRenameHistory}
            onClearAll={handleClearAllHistory}
            showToast={showToast}
            navigate={setActivePage}
          />
        );
      case 'docs': return <DocumentsPage files={files} />;
      case 'inspector': return <RagInspectorPage />;
      default: return null;
    }
  };

  const navCounts = {
    history: history.length,
    docs: files.length,
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-logo-chip">
            <img className="topbar-logo-img" src="/posiview-mark-glow-128.png" alt="Posiview" />
          </span>
          <div>
            <div className="topbar-title">Posiview Edge</div>
            <div className="topbar-subtitle">Connect Better, Convert Faster</div>
          </div>
          {/* Co-brand lockup: the product on the left, the bank it's built for
              on the right of a hairline divider. Standard "product · client"
              arrangement — neither logo sits inside the other's space. */}
          <span className="topbar-cobrand-rule" aria-hidden="true" />
          <span className="topbar-cobrand-chip">
            <img className="topbar-cobrand" src="/hdfc-logo.svg" alt="HDFC Bank" />
          </span>
        </div>
        <div className="topbar-meta">
          <button
            className="topbar-profile"
            data-tour="help"
            onClick={launchTour}
            title="Take the product tour"
            aria-label="Take the product tour"
          >
            <HelpCircle size={16} />
          </button>
          {acctUser && (
            <div className="topbar-user" data-tour="profile">
              <button className="topbar-user-avatar" title="Your account" onClick={() => setProfileOpen(true)}
                style={{ border: 'none', cursor: 'pointer' }}>
                {(acctUser.name || acctUser.email || '?').trim().charAt(0).toUpperCase()}
              </button>
              <div className="topbar-user-info" onClick={() => setProfileOpen(true)} style={{ cursor: 'pointer' }}>
                <div className="topbar-user-name">{acctUser.name || 'Signed in'}</div>
                <div className="topbar-user-email">{acctUser.email}</div>
              </div>
              <button className="topbar-profile" onClick={() => setProfileOpen(true)} title="Account settings">
                <User size={16} />
              </button>
              <button className="topbar-signout" onClick={onSignOut} title="Sign out">
                <LogOut size={16} />
              </button>
            </div>
          )}
        </div>
      </header>

      <div className="app-body">
        <nav className="sidebar">
          {NAV_GROUPS.map(group => (
            <div key={group.label} className="sidebar-group">
              <div className="sidebar-section-label">{group.label}</div>
              {group.items.map(item => {
                const Icon = item.icon;
                const count = navCounts[item.id];
                return (
                  <button
                    key={item.id}
                    className={`nav-item${activePage === item.id ? ' active' : ''}`}
                    data-tour={`nav-${item.id}`}
                    onClick={() => setActivePage(item.id)}
                  >
                    <span className="nav-item-icon">
                      {item.emoji
                        ? <span style={{ fontSize: 16, lineHeight: 1 }}>{item.emoji}</span>
                        : Icon ? <Icon size={17} /> : null}
                    </span>
                    <span className="nav-item-label">{item.label}</span>
                    <span className="nav-item-short">{item.short}</span>
                    {count > 0 && <span className="nav-item-count">{count}</span>}
                  </button>
                );
              })}
            </div>
          ))}


          <div className="sidebar-spacer" />

          {/* Hidden — "System healthy / v2.0 · RAG online" footer (kept in code, not rendered): */}
          {/* <div className="sidebar-footer">
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <CheckCircle2 size={12} style={{ color: 'var(--color-success)' }} />
              <span style={{ color: 'rgba(255,255,255,0.7)', fontWeight: 600 }}>System healthy</span>
            </div>
            <div>v2.0 · RAG online</div>
          </div> */}
        </nav>

        <main className={`main${activePage === 'home' ? ' main--home' : ''}${activePage === 'askme' ? ' main--chatgpt' : ''}${activePage === 'history' && openConvId ? ' main--conv' : ''}`}>
          <div className="main-inner">{renderPage()}</div>
        </main>
      </div>

      {toast && (
        <div className={`toast ${toast.type}`}>
          {toast.type === 'success' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          {toast.msg}
        </div>
      )}

      {profileOpen && (
        <ProfileModal
          user={acctUser}
          onClose={() => setProfileOpen(false)}
          onUserUpdated={(u) => setAcctUser((prev) => ({ ...prev, ...u }))}
        />
      )}

      {/* Global assistant bubble — hidden on the Chat Bot page, which already
          shows the same panel full-size. */}
      <FloatingAssistant
        navigate={setActivePage}
        onSeedPitch={(scenario) => setSeedScenario(scenario)}
        hidden={activePage === 'askme'}
      />
    </div>
  );
}

// ── Auth gate ──────────────────────────────────────────────────────────────
// Wraps the whole app: validates any stored session on load, shows the branded
// sign-in page when logged out, and renders the workspace once authenticated.
// `status`: 'checking' → 'in' → renders app · 'out' → renders <Login/>.

export default function App() {
  const [status, setStatus] = useState('checking');
  const [user, setUser] = useState(() => getStoredUser());
  const [loginPassword, setLoginPassword] = useState(''); // captured at login for forced reset
  const [showLogin, setShowLogin] = useState(false); // DEMO: false → landing page, true → sign-in
  const resetToken = new URLSearchParams(window.location.search).get('reset_token');

  // Install the global fetch interceptor exactly once, before any API call.
  useEffect(() => {
    installAuthFetch(() => { setUser(null); setStatus('out'); });
  }, []);

  // On load, validate a stored token against the backend.
  useEffect(() => {
    let cancelled = false;
    if (!getToken()) { setStatus('out'); return; }
    fetchMe()
      .then((u) => {
        if (cancelled) return;
        if (u) { setUser(u); setStatus('in'); }
        else { setUser(null); setStatus('out'); }
      })
      .catch(() => { if (!cancelled) setStatus('out'); });
    return () => { cancelled = true; };
  }, []);

  const handleSignOut = async () => {
    await authLogout();
    setUser(null);
    setStatus('out');
  };

  // Password-reset link (/?reset_token=…) takes precedence over everything.
  if (resetToken) {
    return <ResetPassword token={resetToken} onDone={() => { window.location.href = '/'; }} />;
  }

  if (status === 'checking') {
    return (
      <div style={{
        position: 'fixed', inset: 0, display: 'flex', alignItems: 'center',
        justifyContent: 'center', background: 'var(--bg-base, #eef2ff)',
      }}>
        <div className="spinner" style={{ width: 34, height: 34, borderWidth: 3 }} />
      </div>
    );
  }

  if (status === 'out' || !user) {
    // ── DEMO landing page — TEMPORARILY DISABLED ───────────────────────────
    // For now we go straight to the sign-in page. To bring the marketing Home
    // page back, uncomment the `if (!showLogin)` block below and re-add the
    // `onBack={() => setShowLogin(false)}` prop to <Login/>. (The Home import
    // and the `showLogin` state above are left in place for that.)
    //
    // if (!showLogin) {
    //   return <Home onEnter={() => setShowLogin(true)} />;
    // }
    return (
      <Login
        onSuccess={(u, pw) => { setUser(u); setLoginPassword(pw || ''); setStatus('in'); }}
      />
    );
  }

  // First-login / admin-reset: must set a new password before entering the app.
  if (user.must_reset_pw) {
    return (
      <ForcePasswordReset
        user={user}
        knownCurrent={loginPassword}
        onSignOut={handleSignOut}
        onDone={() => { const u = { ...user, must_reset_pw: false }; setUser(u); setStoredUser(u); }}
      />
    );
  }

  // Role-based routing: admins land in their console, agents in the tools app.
  if (user.role === 'super_admin' || user.role === 'company_admin') {
    return <AdminApp user={user} onSignOut={handleSignOut} />;
  }

  return <MainApp user={user} onSignOut={handleSignOut} />;
}
