import { useState, useRef, useEffect } from 'react'
import {
  Mic, MicOff, FlaskConical, Send, AlertTriangle, X, ArrowLeft,
  Sparkles, FileCheck, ChevronDown, ChevronUp, CheckCircle2, Clock,
  Hand, Filter, UserCheck, RotateCcw, Zap, FileText, Award
} from 'lucide-react'

type Status = 'idle' | 'listening' | 'transcribing' | 'answering'
// 'hinglish' = Roman script Hindi (e.g. "Aapka EMI 16,000 hoga"). The Hindi
// pill maps to this so replies match how RMs actually speak with customers.
// 'hindi' (pure Devanagari) is still emitted by the auto-detect heuristic.
type Language = 'english' | 'hindi' | 'hinglish' | 'marathi'
// What we send to Whisper (`/api/voice/stt`) — keep it tight: only the languages
// Whisper handles well in this region.  "auto" stays available but tends to
// misfire on short (<5s) clips — prefer the explicit hint when the user knows.
type SttLangHint = 'auto' | 'en' | 'hi' | 'mr'
type MicMode = 'push' | 'auto' | null
// Filter engine used in Auto mode:
//   - 'voiceprint': pre-enrolled MFCC fingerprint (the legacy flow — requires
//                   the 3-step enrolment dialog). Best when the agent will
//                   demo alone or against phone-on-speakerphone customer.
//   - 'adaptive':   online K-means clustering with no enrolment. The first ~8
//                   speech chunks calibrate two voice clusters, then the
//                   majority cluster is locked as 'agent'. Better when the
//                   customer is physically in the same room as the agent.
type FilterMode = 'voiceprint' | 'adaptive'
type EnrollStatus = 'idle' | 'recording' | 'sending' | 'done' | 'error'
type AnswerEngine = 'demo-cache' | 'ollama-fast' | 'openrouter-refined' | 'gpt-4o-mini' | 'unknown'

// ── Two-speaker live path (June 2026) ─────────────────────────────────────
type SpeakerLabel = 'Person 1' | 'Person 2' | 'Unknown'
type LiveStatus   = 'idle' | 'listening' | 'thinking' | 'suggestion_ready' | 'wait' | 'speaking'
interface SpeakerTurn { id: string; speaker: SpeakerLabel; text: string; timestamp: Date }

interface AnswerEntry {
  id: string
  question: string
  rawTranscript: string
  answer: string
  sources: string[]
  timestamp: Date
  language: Language
  speakerRole: string
  questionType: 'objection' | 'factual'
  // Timing + engine metadata for the per-answer badge.
  elapsedMs: number             // wall-clock from user-submit to first-stage done
  engine: AnswerEngine          // which engine produced the displayed answer
  refined: boolean              // true once the background refine has swapped in
  refineMs?: number             // wall-clock for the refinement call (if it ran)
}

interface SessionSummary {
  loading: boolean
  text: string
  engine?: string
  error?: string
}

interface SessionContext {
  customerType: 'salaried' | 'self-employed' | 'doctor' | 'government' | 'business-owner' | ''
  loanType: 'personal-loan' | 'business-loan' | ''
  customerStatus: 'new' | 'existing' | ''
  preApproved: 'yes' | 'no' | 'unknown'
}

interface CacheEntry {
  answer: string
  sources: string[]
  cachedAt: number
}

const CACHE_TTL_MS = 5 * 60 * 1000
const MIN_CHUNK_BYTES = 5000
const MIN_WORDS = 4
const MAX_HISTORY = 10
// June 2026: enrollment disabled — new two-speaker path (Person 1/Person 2)
// does not require voice fingerprinting. Set true to re-enable the 3-step
// enrollment dialog for voiceprint-based filtering.
const LIVE_ENROLLMENT_ENABLED = false

const HINDI_INDICATORS = new Set([
  'kya', 'hai', 'hain', 'ka', 'ki', 'ke', 'mein', 'me', 'se', 'bhi', 'nahi',
  'nahin', 'nhi', 'aur', 'jo', 'wo', 'woh', 'toh', 'to', 'matlab', 'bhai',
  'hoga', 'hogi', 'chahiye', 'kitna', 'kitne', 'kaise', 'kab', 'kyun', 'kyunki',
  'tha', 'thi', 'the', 'ho', 'liya', 'dena', 'lena', 'paisa', 'paise',
  'rupay', 'rupaye', 'saal', 'mahina', 'mahine', 'lekin', 'par', 'phir',
  'abhi', 'tab', 'yahan', 'wahan', 'iska', 'uska', 'mera', 'tera', 'apna',
  'kuch', 'sab', 'bahut', 'thoda', 'zyada', 'kam', 'accha', 'theek',
  'samajh', 'batao', 'bolo', 'dekho', 'sunno', 'karo', 'dijiye', 'lijiye',
  'milega', 'milegi', 'lagega', 'lagegi', 'hona', 'karna', 'rehna',
])

const OBJECTION_PATTERNS = [
  /\btoo\s+high\b/i, /\btoo\s+expensive\b/i, /\bbetter\s+rate\b/i,
  /\bother\s+bank\b/i, /\bthink\s+about\s+it\b/i, /\bbajaj\b/i,
  /\brejected\b/i, /\bwait\b/i, /\blater\b/i, /\balready\s+have\b/i,
  /\bcan'?t\s+afford\b/i, /\blow\s+cibil\b/i, /\bbad\s+credit\b/i,
  /\bhigh\s+emi\b/i, /\bnot\s+sure\b/i, /\bcompare\b/i,
  /\bwhy\s+should\s+i\b/i, /\binterest\s+is\s+(too\s+)?high\b/i,
  /\brate\s+(is\s+)?(too\s+)?high\b/i, /\bexpensive\b/i,
  /\bnot\s+interested\b/i, /\bmahenga\b/i, /\bzyada\s+byaj\b/i,
]

function detectLanguage(transcript: string): Language {
  const words = transcript.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return 'english'
  const hindiCount = words.filter(w => HINDI_INDICATORS.has(w)).length
  const isHindi = hindiCount >= 3 || (words.length >= 8 && hindiCount / words.length > 0.25)
  return isHindi ? 'hindi' : 'english'
}

// The selected language pill controls the entire pipeline — no auto-switching.
// Regardless of what the customer speaks, the reply language matches the agent's selection.
function resolveReplyLanguage(pill: SttLangHint, _transcript?: string): Language {
  if (pill === 'hi') return 'hindi'
  if (pill === 'mr') return 'marathi'
  return 'english'  // 'en' → English (Hinglish understood); 'auto' legacy → English
}

function getMajoritySpeaker(words: Array<{ speaker: number }>): number {
  if (!words || words.length === 0) return 0
  const counts: Record<number, number> = {}
  for (const w of words) counts[w.speaker] = (counts[w.speaker] || 0) + 1
  return Number(Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0])
}

function isObjection(text: string): boolean {
  return OBJECTION_PATTERNS.some(p => p.test(text))
}

function getMimeType(): string {
  const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']
  return types.find(t => MediaRecorder.isTypeSupported(t)) || ''
}

const CUSTOMER_TYPES = [
  { value: 'salaried', label: 'Salaried' },
  { value: 'self-employed', label: 'Self-Employed' },
  { value: 'doctor', label: 'Doctor' },
  { value: 'government', label: 'Government' },
  { value: 'business-owner', label: 'Business Owner' },
] as const

const LOAN_TYPES = [
  { value: 'personal-loan', label: 'Personal Loan' },
  { value: 'business-loan', label: 'Business Loan' },
] as const

const QUICK_TEST_PROMPTS = [
  'What is the interest rate?',
  'EMI kitni hogi 5 lakh ke liye?',
  'What documents are needed?',
  'Your rate is too high',
  'What is the maximum loan amount?',
  'I already have a loan with SBI',
]

// 3-step guided voice enrolment — one phrase per language so the fingerprint
// captures the user's voice across the languages they actually speak in calls.
// Phrases are chosen to (a) take ~5 seconds at normal pace and (b) cover a wide
// range of phonemes (multiple vowels + plosives + fricatives).
const ENROLL_PHRASES: Array<{
  language: 'English' | 'Hindi' | 'Hinglish'
  phrase: string
  hint: string
}> = [
  {
    language: 'English',
    phrase: 'Hello, my name is Rajesh from HDFC Bank, and I am here to help you with a personal loan today.',
    hint: 'Speak naturally — you can swap "Rajesh" for your actual name.',
  },
  {
    language: 'Hindi',
    phrase: 'Namaste, main HDFC Bank se baat kar raha hoon. Aaj main aapko personal loan ke baare mein bataunga.',
    hint: 'Hindi script also works — speak at your normal pace.',
  },
  {
    language: 'Hinglish',
    phrase: 'Sir, aapka CIBIL score kitna hai? Hum aapko best interest rate offer karenge HDFC se, with quick approval.',
    hint: 'This is how you actually speak in customer calls — switch freely between Hindi and English.',
  },
]

export default function LiveAssistant() {
  const [status, setStatus] = useState<Status>('idle')
  const [isActive, setIsActive] = useState(false)
  const [rawTranscript, setRawTranscript] = useState('')
  const [cleanedQuestion, setCleanedQuestion] = useState('')
  const [currentAnswer, setCurrentAnswer] = useState('')
  const [currentSources, setCurrentSources] = useState<string[]>([])
  const [currentQuestionType, setCurrentQuestionType] = useState<'objection' | 'factual'>('factual')
  const [answerHistory, setAnswerHistory] = useState<AnswerEntry[]>([])
  const [detectedLanguage, setDetectedLanguage] = useState<Language>('english')
  const [errorMessage, setErrorMessage] = useState('')
  const [showTranscript, setShowTranscript] = useState(false)
  const [expandedHistoryId, setExpandedHistoryId] = useState<string | null>(null)
  const [speakerRegistered, setSpeakerRegistered] = useState(false)
  const [waveformActive, setWaveformActive] = useState(false)
  const [testMode, setTestMode] = useState(false)
  const [testInput, setTestInput] = useState('')
  const [testLoading, setTestLoading] = useState(false)

  const [sessionContext, setSessionContext] = useState<SessionContext>({
    customerType: '',
    loanType: 'personal-loan',
    customerStatus: 'new',
    preApproved: 'unknown'
  })
  const [sessionStarted, setSessionStarted] = useState(false)

  // ── Mic mode (new) ────────────────────────────────────────────────────────
  const [micMode, setMicMode] = useState<MicMode>(null)
  const [rmSpeakerId, setRmSpeakerId] = useState<number | null>(null)
  const [enrollStatus, setEnrollStatus] = useState<EnrollStatus>('idle')
  const [enrollError, setEnrollError] = useState<string>('')
  const [enrollSeconds, setEnrollSeconds] = useState<number>(0)
  const [pttPressed, setPttPressed] = useState(false)
  const [pttProcessing, setPttProcessing] = useState(false)
  const [pttSpeechDetected, setPttSpeechDetected] = useState(false)
  const [singleSpeakerStreak, setSingleSpeakerStreak] = useState(0)

  // Voiceprint (acoustic fingerprint) — replaces Deepgram-label-only filtering
  const [voiceprintId, setVoiceprintId] = useState<string | null>(null)
  const [voiceprintBackend, setVoiceprintBackend] = useState<string>('')
  const [voiceprintSamples, setVoiceprintSamples] = useState<number>(0)
  const [enrollStep, setEnrollStep] = useState<number>(0)   // 0..2 = current phrase index
  const [agentSkipCount, setAgentSkipCount] = useState(0)
  const [lastSimilarity, setLastSimilarity] = useState<number | null>(null)

  // ── Adaptive (online clustering, no enrolment) — parallel to voiceprint ──
  // Default to the existing 'voiceprint' approach so nothing behaves differently
  // until the user explicitly flips to 'adaptive' on the mode-picker screen.
  const [filterMode, setFilterMode] = useState<FilterMode>('voiceprint')
  const [adaptiveSessionId, setAdaptiveSessionId] = useState<string | null>(null)
  const [adaptivePhase, setAdaptivePhase] = useState<'idle' | 'calibrating' | 'locked'>('idle')
  const [adaptiveChunksSeen, setAdaptiveChunksSeen] = useState(0)
  const [adaptiveCalibMin, setAdaptiveCalibMin] = useState<number>(8)
  // Ref mirror so processAudioChunk doesn't race with React batching when many
  // chunks fire back-to-back — we need the latest session id synchronously.
  const adaptiveSessionIdRef = useRef<string | null>(null)

  // What language the user is currently speaking with the customer in.
  // Explicit hint dramatically improves Whisper accuracy on short clips
  // (auto-detect on a 3-sec Hindi snippet often misfires). User can change
  // this mid-call via the chips in the live UI.
  const [sttLanguage, setSttLanguage] = useState<SttLangHint>('en')

  // ── Live chunk diagnostics (so the user can SEE every 3-sec audio chunk being
  //    captured and what the system decided to do with it). Without this the
  //    live session looks broken when the user tests alone — their own voice is
  //    correctly filtered as "agent", so no transcript ever appears.
  type ChunkLogEntry = {
    id: string                    // wall-clock for stable React key
    timestamp: Date
    bytes: number                 // size of the audio chunk
    similarity: number | null     // voiceprint cosine score
    outcome: 'agent-filtered' | 'too-short' | 'customer-processing' | 'customer-empty' | 'transcribers-failed' | 'verify-error'
    transcript?: string           // raw transcript when present (even for filtered chunks if available)
  }
  const [chunkLog, setChunkLog] = useState<ChunkLogEntry[]>([])
  function pushChunkLog(entry: Omit<ChunkLogEntry, 'id' | 'timestamp'>) {
    setChunkLog(prev => [
      { id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, timestamp: new Date(), ...entry },
      ...prev,
    ].slice(0, 8))   // keep last 8 chunks visible
  }

  // ── Per-answer timing + engine + two-stage refinement state ──────────────
  const [currentElapsedMs, setCurrentElapsedMs] = useState<number | null>(null)
  const [currentEngine, setCurrentEngine] = useState<AnswerEngine>('unknown')
  const [currentRefined, setCurrentRefined] = useState(false)
  const [refining, setRefining] = useState(false)
  const currentEntryIdRef = useRef<string | null>(null)

  // ── CRM Session Summary modal ────────────────────────────────────────────
  const [sessionSummary, setSessionSummary] = useState<SessionSummary | null>(null)

  // Scroll .main up to reveal the answer card whenever a new answer begins
  const answerCardRef = useRef<HTMLDivElement | null>(null)
  const prevAnswerEmptyRef = useRef(true)
  useEffect(() => {
    const nowEmpty = !currentAnswer && status !== 'answering'
    if (!nowEmpty && prevAnswerEmptyRef.current && answerCardRef.current) {
      answerCardRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    prevAnswerEmptyRef.current = nowEmpty
  }, [currentAnswer, status])

  const enrollRecorderRef = useRef<MediaRecorder | null>(null)
  const enrollChunksRef = useRef<Blob[]>([])
  const enrollStreamRef = useRef<MediaStream | null>(null)
  const enrollTimerRef = useRef<number | null>(null)
  const pttRecorderRef = useRef<MediaRecorder | null>(null)
  const pttChunksRef = useRef<Blob[]>([])
  const pttStreamRef = useRef<MediaStream | null>(null)
  const pttAudioCtxRef     = useRef<AudioContext | null>(null)
  const pttAnalyserRef     = useRef<AnalyserNode | null>(null)
  const pttVadTimerRef     = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pttMaxUttTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pttSpeechActiveRef = useRef(false)
  const pttVadRunningRef   = useRef(false)
  // Ref mirror of pttPressed — timers/RAF callbacks run in stale closures
  // and would see pttPressed=false from the render where pttStart() was called.
  const pttPressedRef      = useRef(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const firstSpeakerDetectedRef = useRef(false)
  const firstSpeakerIdRef = useRef<number>(0)
  const isActiveRef = useRef(false)

  // ── VAD (energy-based Voice Activity Detection) ───────────────────────────
  // Replaces the fixed 3-second chunk loop. Fires processAudioChunk only when
  // a speaker finishes a complete utterance (1.5s of silence after speech).
  const audioContextRef   = useRef<AudioContext | null>(null)
  const analyserRef       = useRef<AnalyserNode | null>(null)
  const vadRecorderRef    = useRef<MediaRecorder | null>(null)
  const vadChunksRef      = useRef<Blob[]>([])
  const vadSpeechActive   = useRef(false)
  const vadSilenceTimer   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const VAD_THRESHOLD     = 18    // RMS energy 0-255; higher = less false triggers on background noise
  const VAD_SILENCE_MS    = 800   // ms of silence that ends an utterance (was 1500 — saves ~700ms perceived latency)
  const VAD_MAX_UTT_MS    = 12000 // safety cap: flush if utterance runs too long
  const PTT_SILENCE_MS    = 2500  // ms of silence that auto-stops PTT recording (raised from 1500 — prevents premature cutoff during natural pauses)
  const PTT_MAX_UTT_MS    = 15000 // safety cap for PTT auto-stop

  // ── Labeled-transcript session state (legacy Deepgram-label path) ────────
  const sessionTranscriptRef = useRef<Array<{ speaker: string; text: string }>>([])
  const speakerMappingRef    = useRef<Map<number, string>>(new Map())
  const streamRef = useRef<MediaStream | null>(null)
  const answerCacheRef = useRef<Map<string, CacheEntry>>(new Map())

  // ── NEW: Two-speaker live path state (June 2026) ──────────────────────
  // Both speakers flow through — no filter. Person 1 / Person 2 labels
  // assigned by adaptive K-means (backend). GPT-4o-mini infers roles.
  const [liveSpeakerLog, setLiveSpeakerLog] = useState<SpeakerTurn[]>([])
  const liveSpeakerLogRef  = useRef<SpeakerTurn[]>([])   // ref mirror for stale-closure safety
  const [liveStatus, setLiveStatus] = useState<LiveStatus>('idle')
  const [myRole, _setMyRole] = useState<SpeakerLabel | null>(null)
  const myRoleRef          = useRef<SpeakerLabel | null>(null)
  function setMyRole(v: SpeakerLabel | null) { myRoleRef.current = v; _setMyRole(v) }
  const liveSessionIdRef   = useRef<string | null>(null)
  const transcriptPaneRef  = useRef<HTMLDivElement | null>(null)
  const waveformRef        = useRef<HTMLDivElement | null>(null)  // live RMS waveform

  // ── Always-on TTS + concurrency (full-duplex) ────────────────────────────
  // isSpeakingRef: true while the browser is playing back a TTS utterance.
  //   Used only for liveStatus UI transitions — VAD never pauses on this.
  //   Mic stays active full-duplex; echoCancellation on getUserMedia suppresses
  //   the speaker feed at the hardware level.
  // isProcessingLiveRef: true while a processAudioChunkLive SSE request is
  //   in-flight.  New blobs that arrive during that window are queued.
  // liveQueueRef: overflow queue — at most 1 pending blob (older ones dropped).
  const isSpeakingRef        = useRef(false)
  const isProcessingLiveRef  = useRef(false)
  const liveQueueRef         = useRef<Blob[]>([])
  const [ttsEnabled, _setTtsEnabled] = useState(true)
  const ttsEnabledRef        = useRef(true)
  function setTtsEnabled(v: boolean) { ttsEnabledRef.current = v; _setTtsEnabled(v) }

  // STT is fully local (faster-whisper on the backend) — no browser API key
  // required, so there's no Deepgram/Sarvam key check here anymore.

  function getCachedAnswer(key: string): CacheEntry | null {
    const entry = answerCacheRef.current.get(key)
    if (!entry) return null
    if (Date.now() - entry.cachedAt > CACHE_TTL_MS) {
      answerCacheRef.current.delete(key)
      return null
    }
    return entry
  }

  // Local faster-whisper (large-v3-turbo) on the backend is the ONLY STT engine.
  // Deepgram and Sarvam browser-side STT were removed — Whisper is GPU-accelerated,
  // free, fully offline, and handles English / Hindi / Marathi via the language hint.
  async function transcribeWithWhisper(audioBlob: Blob, language: SttLangHint = 'auto')
    : Promise<{ transcript: string; language: string; confidence: number }> {
    const fd = new FormData()
    fd.append('file', audioBlob, 'chunk.webm')
    fd.append('language', language)
    const res = await fetch('/api/voice/stt', { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`Whisper STT error: ${res.status}`)
    const data = await res.json()
    if (!data.success) throw new Error(data.error || 'Whisper failed')
    return {
      transcript: data.transcript || '',
      language: data.language || 'en',
      confidence: data.confidence ?? 0,
    }
  }

  async function cleanTranscript(rawText: string, language: Language): Promise<string> {
    try {
      const response = await fetch('/api/voice/clean-transcript', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_text: rawText, language })
      })
      if (!response.ok) return rawText
      const data = await response.json()
      return data.cleaned || rawText
    } catch { return rawText }
  }

  interface StreamResult {
    sources: string[]
    engine: AnswerEngine
    refinable: boolean
    wait?: boolean   // GPT-4o-mini says agent was last speaker — no hint needed
  }

  async function getAnswerStream(
    question: string, questionType: string, context: SessionContext,
    onToken: (token: string) => void, language: Language = 'english',
    sessionHistory: AnswerEntry[] = [],
    labeledTranscript?: string   // Speaker 1/2 labeled transcript → GPT-4o-mini path
  ): Promise<StreamResult> {
    const lastFive = sessionHistory.slice(0, 5).slice().reverse()
    const historyPayload = lastFive.flatMap(e => ([
      { role: 'user',      content: e.question },
      { role: 'assistant', content: e.answer },
    ]))

    const response = await fetch('/api/voice/answer/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        question_type: questionType,
        session_context: context,
        language,
        history: historyPayload,
        labeled_transcript: labeledTranscript,
      })
    })
    if (!response.ok) throw new Error(`Server error: ${response.status}`)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let sources: string[] = []
    let engine: AnswerEngine = 'unknown'
    let refinable = false
    let wait = false
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          if (data.token) onToken(data.token)
          if (data.done) {
            sources = data.sources ?? []
            if (data.engine) engine = data.engine as AnswerEngine
            refinable = !!data.refinable
            wait = !!data.wait
          }
        } catch { /* skip */ }
      }
    }
    return { sources, engine, refinable, wait }
  }

  // ── Two-stage refinement ─────────────────────────────────────────────────
  // After the fast llama3.2:3b answer is shown, kick off a background call
  // to a larger model. If it returns a meaningfully different answer within
  // a few seconds, silently swap it in. Failure is a no-op.
  async function refineAnswerInBackground(
    entryId: string,
    question: string,
    questionType: 'objection' | 'factual',
    context: SessionContext,
    language: Language,
    sessionHistory: AnswerEntry[],
  ) {
    const lastFive = sessionHistory.slice(0, 5).slice().reverse()
    const historyPayload = lastFive.flatMap(e => ([
      { role: 'user',      content: e.question },
      { role: 'assistant', content: e.answer },
    ]))
    const t0 = performance.now()
    try {
      const res = await fetch('/api/voice/answer/refine', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          question_type: questionType,
          session_context: context,
          language,
          history: historyPayload,
        }),
      })
      const data = await res.json()
      if (!data.success || !data.answer) return
      const refineMs = Math.round(performance.now() - t0)

      // Update the matching entry in history + the currently displayed answer
      // (only if the user is still viewing this question's response).
      setAnswerHistory(prev => prev.map(e =>
        e.id === entryId
          ? {
              ...e,
              answer: data.answer,
              sources: data.sources?.length ? data.sources : e.sources,
              engine: 'openrouter-refined',
              refined: true,
              refineMs,
            }
          : e
      ))
      setCurrentAnswer(curr => {
        // Only swap the live answer if it still matches the entry being refined.
        // This guards against the user having already asked the next question.
        const stillCurrent = (currentEntryIdRef.current === entryId)
        return stillCurrent ? data.answer : curr
      })
      if (currentEntryIdRef.current === entryId) {
        setCurrentSources(data.sources?.length ? data.sources : [])
        setCurrentEngine('openrouter-refined')
        setCurrentRefined(true)
      }
    } catch {
      /* refine is best-effort — silently ignore failures */
    }
  }

  // ── Voice enrollment (Auto Filter mode) ────────────────────────────────────
  const ENROLL_DURATION_S = 5

  async function startEnrollment() {
    setEnrollError('')
    setEnrollStatus('recording')
    setEnrollSeconds(0)
    enrollChunksRef.current = []

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
      })
      enrollStreamRef.current = stream

      const mimeType = getMimeType()
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream)
      enrollRecorderRef.current = recorder

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) enrollChunksRef.current.push(e.data)
      }
      recorder.onstop = async () => {
        // Cleanup mic stream
        if (enrollStreamRef.current) {
          enrollStreamRef.current.getTracks().forEach(t => t.stop())
          enrollStreamRef.current = null
        }
        const blob = new Blob(enrollChunksRef.current, { type: mimeType || 'audio/webm' })
        await sendEnrollmentToBackend(blob, mimeType || 'audio/webm')
      }

      recorder.start()

      // Tick the visual progress every 100ms; stop at ENROLL_DURATION_S
      const startTs = Date.now()
      if (enrollTimerRef.current) window.clearInterval(enrollTimerRef.current)
      enrollTimerRef.current = window.setInterval(() => {
        const elapsed = (Date.now() - startTs) / 1000
        setEnrollSeconds(elapsed)
        if (elapsed >= ENROLL_DURATION_S) {
          if (enrollTimerRef.current) { window.clearInterval(enrollTimerRef.current); enrollTimerRef.current = null }
          if (enrollRecorderRef.current && enrollRecorderRef.current.state !== 'inactive') {
            enrollRecorderRef.current.stop()
          }
        }
      }, 100) as unknown as number

    } catch (err: any) {
      setEnrollError(err.message || 'Could not access microphone for enrollment.')
      setEnrollStatus('error')
    }
  }

  async function sendEnrollmentToBackend(blob: Blob, mimeType: string) {
    setEnrollStatus('sending')
    try {
      const filename = `enroll.${mimeType.includes('webm') ? 'webm' : 'audio'}`

      // ── Voiceprint path: first step creates the profile, subsequent steps
      //    add samples to it (running-mean fingerprint that learns Hindi/English/Hinglish).
      let vpOk = false
      let vpBackendError = ''
      try {
        const vpFd = new FormData()
        vpFd.append('file', blob, filename)
        const endpoint = voiceprintId
          ? '/api/voice/voiceprint/enroll-sample'
          : '/api/voice/voiceprint/enroll'
        if (voiceprintId) vpFd.append('profile_id', voiceprintId)
        else vpFd.append('name', 'RM')

        const vpRes = await fetch(endpoint, { method: 'POST', body: vpFd })
        const vpData = await vpRes.json()
        if (vpData.success && vpData.profile_id) {
          setVoiceprintId(vpData.profile_id)
          setVoiceprintBackend(vpData.backend || '')
          setVoiceprintSamples(vpData.sample_count || 1)
          vpOk = true
        } else {
          // Surface the exact backend reason so the user knows whether to
          // speak louder, longer, or closer to the mic.
          vpBackendError = vpData.error || ''
          console.warn('Voiceprint step failed:', vpData.error)
        }
      } catch (err: any) {
        vpBackendError = err.message || 'network error'
        console.warn('Voiceprint backend unreachable, falling back to Deepgram label:', err)
      }

      // ── Deepgram-label path: only on the FIRST step (it's just a fallback signal)
      let dgOk = false
      if (enrollStep === 0 && !rmSpeakerId) {
        try {
          const dgFd = new FormData()
          dgFd.append('file', blob, filename)
          const dgRes = await fetch('/api/voice/enroll', { method: 'POST', body: dgFd })
          const dgData = await dgRes.json()
          if (dgData.success && typeof dgData.rm_speaker_id === 'number') {
            setRmSpeakerId(dgData.rm_speaker_id)
            dgOk = true
          }
        } catch (err) {
          console.warn('Deepgram enroll failed:', err)
        }
      }

      const stepSucceeded = vpOk || (enrollStep === 0 && dgOk)
      if (!stepSucceeded) {
        // Show the specific reason the backend rejected the clip if we have one,
        // otherwise fall back to a friendly generic prompt.
        const detail = vpBackendError
          ? ` Backend says: "${vpBackendError}". Tip: speak the whole phrase without long pauses, and stay close to the mic.`
          : ' Tip: speak the whole phrase without long pauses, and stay close to the mic.'
        setEnrollError('This step did not capture enough speech — please try again.' + detail)
        setEnrollStatus('error')
        return
      }

      // Advance to next step, or finish if we've done all 3.
      const nextStep = enrollStep + 1
      if (nextStep >= ENROLL_PHRASES.length) {
        setEnrollStatus('done')
      } else {
        setEnrollStep(nextStep)
        setEnrollSeconds(0)
        setEnrollStatus('idle')   // user clicks Start again for the next phrase
      }
    } catch (err: any) {
      setEnrollError(err.message || 'Network error while enrolling.')
      setEnrollStatus('error')
    }
  }

  function resetEnrollment() {
    setRmSpeakerId(null)
    setVoiceprintId(null)
    setVoiceprintBackend('')
    setVoiceprintSamples(0)
    setEnrollStep(0)
    setAgentSkipCount(0)
    setLastSimilarity(null)
    setEnrollStatus('idle')
    setEnrollError('')
    setEnrollSeconds(0)
    // Also drop any adaptive session — a Re-enroll click should clear both
    // engines' state so the next call starts clean regardless of mode.
    void resetAdaptiveSession()
  }

  // ── Adaptive session helpers (parallel path — does not touch voiceprint) ──

  async function ensureAdaptiveSession(): Promise<string | null> {
    // Reuse the existing session if we already have one this call.
    if (adaptiveSessionIdRef.current) return adaptiveSessionIdRef.current
    try {
      const res = await fetch('/api/voice/adaptive/start', { method: 'POST' })
      const data = await res.json()
      if (data.success && data.session_id) {
        adaptiveSessionIdRef.current = data.session_id
        setAdaptiveSessionId(data.session_id)
        setAdaptivePhase('calibrating')
        if (typeof data.calib_min_chunks === 'number') {
          setAdaptiveCalibMin(data.calib_min_chunks)
        }
        return data.session_id
      }
    } catch (err) {
      console.warn('Adaptive session start failed:', err)
    }
    return null
  }

  async function resetAdaptiveSession() {
    const sid = adaptiveSessionIdRef.current
    adaptiveSessionIdRef.current = null
    setAdaptiveSessionId(null)
    setAdaptivePhase('idle')
    setAdaptiveChunksSeen(0)
    if (sid) {
      try {
        await fetch(`/api/voice/adaptive/${sid}/reset`, { method: 'POST' })
      } catch {
        // best-effort cleanup; server GC will drop it eventually
      }
    }
  }

  // Adaptive analogue of isAgentChunk(). Returns is_agent + phase + diagnostics.
  // During calibration phase, is_agent is always false — every chunk passes
  // through to the LLM so the system has audio context while it learns.
  async function isAgentChunkAdaptive(
    blob: Blob,
  ): Promise<{ isAgent: boolean; phase: 'calibrating' | 'locked'; reason: string }> {
    const sid = await ensureAdaptiveSession()
    if (!sid) return { isAgent: false, phase: 'calibrating', reason: 'no_session' }
    try {
      const fd = new FormData()
      fd.append('file', blob, 'chunk.webm')
      fd.append('session_id', sid)
      const res = await fetch('/api/voice/adaptive/verify', { method: 'POST', body: fd })
      const data = await res.json()
      if (data.success) {
        if (typeof data.chunks_seen === 'number') setAdaptiveChunksSeen(data.chunks_seen)
        if (data.phase === 'locked' && adaptivePhase !== 'locked') setAdaptivePhase('locked')
        return {
          isAgent: !!data.is_agent,
          phase: data.phase || 'calibrating',
          reason: data.reason || '',
        }
      }
    } catch (err) {
      console.warn('Adaptive verify failed, falling through:', err)
    }
    return { isAgent: false, phase: 'calibrating', reason: 'verify_error' }
  }

  // Score a live chunk against the enrolled voiceprint.
  // Returns true if the chunk is the agent's voice (and should be skipped).
  // Return type carries the similarity so the caller can log it without an
  // extra round-trip on rag_instance state.
  async function isAgentChunk(blob: Blob): Promise<{ isAgent: boolean; similarity: number | null }> {
    if (!voiceprintId) return { isAgent: false, similarity: null }
    try {
      const fd = new FormData()
      fd.append('file', blob, 'chunk.webm')
      fd.append('profile_id', voiceprintId)
      const res = await fetch('/api/voice/voiceprint/verify', { method: 'POST', body: fd })
      const data = await res.json()
      if (data.success) {
        const sim = typeof data.similarity === 'number' ? data.similarity : null
        setLastSimilarity(sim)
        return { isAgent: !!data.is_agent, similarity: sim }
      }
      // verify endpoint returned success=false (e.g. audio too short)
      pushChunkLog({ bytes: blob.size, similarity: null, outcome: 'verify-error' })
    } catch (err) {
      console.warn('Voiceprint verify failed, falling through to Deepgram-label path:', err)
      pushChunkLog({ bytes: blob.size, similarity: null, outcome: 'verify-error' })
    }
    return { isAgent: false, similarity: null }
  }

  // ── Push-to-Talk (Mode 1) ──────────────────────────────────────────────────
  async function pttStart() {
    if (pttPressed || pttProcessing) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
      })
      pttStreamRef.current = stream
      pttChunksRef.current = []
      pttSpeechActiveRef.current = false
      pttPressedRef.current = true   // set before RAF starts — avoids stale-closure bug
      pttVadRunningRef.current = true

      const mimeType = getMimeType()
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream)
      pttRecorderRef.current = recorder

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) pttChunksRef.current.push(e.data)
      }
      recorder.onstop = async () => {
        pttVadRunningRef.current = false
        setPttSpeechDetected(false)
        if (pttStreamRef.current) {
          pttStreamRef.current.getTracks().forEach(t => t.stop())
          pttStreamRef.current = null
        }
        const blob = new Blob(pttChunksRef.current, { type: mimeType || 'audio/webm' })
        pttChunksRef.current = []
        if (blob.size > 800) {
          setPttProcessing(true)
          // In PTT mode, everything captured = customer audio. Skip speaker logic.
          await processAudioChunk(blob, { skipSpeakerLogic: true })
          setPttProcessing(false)
        }
      }

      recorder.start()
      setPttPressed(true)
      setPttSpeechDetected(false)
      setStatus('listening')

      // ── PTT VAD: auto-stop after speech + silence ──────────────────────
      // Mirrors the Auto-mode VAD loop. Fires pttStop() automatically when
      // the user pauses speaking for PTT_SILENCE_MS. The user can also stop
      // manually at any time by clicking the button (pttStop is guarded).
      const AudioCtx = (window.AudioContext || (window as any).webkitAudioContext) as typeof AudioContext
      const audioCtx = new AudioCtx({ sampleRate: 16000 })
      pttAudioCtxRef.current = audioCtx
      const source = audioCtx.createMediaStreamSource(stream)
      const analyser = audioCtx.createAnalyser()
      analyser.fftSize = 512
      analyser.smoothingTimeConstant = 0.3
      source.connect(analyser)
      pttAnalyserRef.current = analyser
      const freqData = new Uint8Array(analyser.frequencyBinCount)

      // Safety cap: auto-stop if recording runs longer than PTT_MAX_UTT_MS
      pttMaxUttTimerRef.current = setTimeout(() => {
        if (pttVadRunningRef.current) pttStop()
      }, PTT_MAX_UTT_MS)

      function pttVadTick() {
        if (!pttVadRunningRef.current) return
        analyser.getByteFrequencyData(freqData)
        const rms = Math.sqrt(freqData.reduce((s, v) => s + v * v, 0) / freqData.length)

        if (rms > VAD_THRESHOLD) {
          // Speech energy detected
          if (!pttSpeechActiveRef.current) {
            pttSpeechActiveRef.current = true
            setPttSpeechDetected(true)
          }
          // Cancel any pending silence timer
          if (pttVadTimerRef.current) { clearTimeout(pttVadTimerRef.current); pttVadTimerRef.current = null }
        } else {
          // Silence — start auto-stop timer once speech has been detected
          if (pttSpeechActiveRef.current && !pttVadTimerRef.current) {
            pttVadTimerRef.current = setTimeout(() => {
              pttVadTimerRef.current = null
              if (pttVadRunningRef.current) pttStop()
            }, PTT_SILENCE_MS)
          }
        }
        requestAnimationFrame(pttVadTick)
      }

      requestAnimationFrame(pttVadTick)

    } catch (err: any) {
      const msgMap: Record<string, string> = {
        NotAllowedError: 'Microphone access denied. Allow microphone permission in browser settings.',
        NotFoundError: 'No microphone found.',
        NotReadableError: 'Microphone is in use by another app.',
      }
      showError(msgMap[err.name] || `Could not access microphone: ${err.message}`)
    }
  }

  function pttStop() {
    if (!pttPressedRef.current) return   // use ref — safe from stale closures
    pttPressedRef.current = false
    // Stop VAD immediately so the RAF loop exits and timers don't double-fire
    pttVadRunningRef.current = false
    if (pttVadTimerRef.current) { clearTimeout(pttVadTimerRef.current); pttVadTimerRef.current = null }
    if (pttMaxUttTimerRef.current) { clearTimeout(pttMaxUttTimerRef.current); pttMaxUttTimerRef.current = null }
    if (pttAudioCtxRef.current) { pttAudioCtxRef.current.close(); pttAudioCtxRef.current = null }
    pttAnalyserRef.current = null
    pttSpeechActiveRef.current = false
    setPttSpeechDetected(false)
    setPttPressed(false)
    setStatus('transcribing')
    if (pttRecorderRef.current && pttRecorderRef.current.state !== 'inactive') {
      pttRecorderRef.current.stop()
    }
  }

  function pttToggle() {
    if (pttPressed) pttStop()
    else pttStart()
  }

  async function processAudioChunk(chunkBlob: Blob, opts?: { skipSpeakerLogic?: boolean }) {
    const minBytes = opts?.skipSpeakerLogic ? 800 : MIN_CHUNK_BYTES
    if (!chunkBlob || chunkBlob.size < minBytes) {
      // Tiny / silent chunk — still surface it so the user knows the mic IS
      // capturing (just nothing meaningful in this 3-sec window).
      pushChunkLog({ bytes: chunkBlob?.size || 0, similarity: null, outcome: 'too-short' })
      return
    }

    let lastSim: number | null = null
    try {
      // ── VOICE FILTERING REMOVED ──────────────────────────────────────────
      // Voiceprint + adaptive speaker filters have been replaced by the
      // labeled-transcript approach: both speakers are transcribed, labeled
      // Speaker 1 / Speaker 2, and GPT-4o-mini identifies the customer from
      // content. No enrollment or acoustic fingerprinting needed.
      //
      // Commented-out code kept below for reference:
      //
      // if (!opts?.skipSpeakerLogic && micMode === 'auto') {
      //   if (filterMode === 'adaptive') {
      //     const verdict = await isAgentChunkAdaptive(chunkBlob)
      //     if (verdict.isAgent) { ... return }
      //   } else if (voiceprintId) {
      //     const verdict = await isAgentChunk(chunkBlob)
      //     if (verdict.isAgent) { ... return }
      //   }
      // }

      setStatus('transcribing')
      let transcript = ''
      const words: Array<{ speaker: number; punctuated_word?: string; word?: string }> = []
      const usedWhisperOnly = true

      // ── Local Whisper is the only STT engine (no Deepgram/Sarvam) ──────────
      try {
        const w = await transcribeWithWhisper(chunkBlob, sttLanguage)
        transcript = w.transcript
      } catch (eWh) {
        console.warn('Whisper STT failed:', eWh)
        pushChunkLog({ bytes: chunkBlob.size, similarity: null, outcome: 'transcribers-failed' })
        setStatus('listening'); return
      }

      if (!transcript || transcript.trim().split(/\s+/).length < MIN_WORDS) {
        pushChunkLog({ bytes: chunkBlob.size, similarity: lastSim, outcome: 'customer-empty', transcript })
        setStatus('listening'); return
      }

      setRawTranscript(transcript)

      // ── Build session labeled transcript ──────────────────────────────────
      // Assign consistent Speaker 1 / Speaker 2 labels across the session.
      // GPT-4o-mini reads all labels and identifies the customer by content.
      let labeledTranscript: string | undefined
      if (!opts?.skipSpeakerLogic) {
        const dgSpeakerId = getMajoritySpeaker(words)
        // Map Deepgram speaker ID → consistent session label
        if (usedWhisperOnly) {
          // No Deepgram labels — alternate from the last known speaker
          const prev = sessionTranscriptRef.current[sessionTranscriptRef.current.length - 1]?.speaker
          const label = prev === 'Speaker 1' ? 'Speaker 2' : 'Speaker 1'
          sessionTranscriptRef.current = [...sessionTranscriptRef.current.slice(-9), { speaker: label, text: transcript }]
        } else {
          if (!speakerMappingRef.current.has(dgSpeakerId)) {
            speakerMappingRef.current.set(dgSpeakerId, `Speaker ${speakerMappingRef.current.size + 1}`)
          }
          const label = speakerMappingRef.current.get(dgSpeakerId)!
          sessionTranscriptRef.current = [...sessionTranscriptRef.current.slice(-9), { speaker: label, text: transcript }]
        }
        labeledTranscript = sessionTranscriptRef.current.map(u => `${u.speaker}: ${u.text}`).join('\n')
      }

      const speakerId = getMajoritySpeaker(words)

      const lang = resolveReplyLanguage(sttLanguage, transcript)
      setDetectedLanguage(lang)

      // Hindi/Marathi are handled directly by Whisper via the language hint —
      // no separate Sarvam pass needed.

      // PTT skips cleanTranscript — saves ~500ms LLM round-trip.
      // Whisper output is clean enough for RAG directly.
      const cleaned = opts?.skipSpeakerLogic ? transcript.trim() : await cleanTranscript(transcript, lang)
      setCleanedQuestion(cleaned)
      const questionType = isObjection(cleaned) ? 'objection' : 'factual'
      setCurrentQuestionType(questionType)

      // Log the chunk as successfully routed to the LLM pipeline
      pushChunkLog({
        bytes: chunkBlob.size, similarity: lastSim,
        outcome: 'customer-processing', transcript: cleaned || transcript,
      })

      const cacheKey = cleaned.toLowerCase().trim()
      const cached = getCachedAnswer(cacheKey)
      if (cached) {
        setCurrentAnswer(cached.answer)
        setCurrentSources(cached.sources)
        setCurrentElapsedMs(0)
        setCurrentEngine('unknown')   // runtime cache, not backend-cache
        setCurrentRefined(false)
        setStatus('listening'); return
      }

      setCurrentAnswer('')
      setCurrentElapsedMs(null)
      setCurrentEngine('unknown')
      setCurrentRefined(false)
      const entryId = Date.now().toString()
      currentEntryIdRef.current = entryId
      setStatus('answering')
      const tStart = performance.now()
      let answer = ''
      let sources: string[] = []
      let engine: AnswerEngine = 'unknown'
      let refinable = false
      try {
        const res = await getAnswerStream(cleaned, questionType, sessionContext, (token) => {
          answer += token
          setCurrentAnswer(prev => prev + token)
        }, lang, answerHistory, labeledTranscript)
        if ((res as any).wait) { setStatus('listening'); return }
        sources = res.sources
        engine = res.engine
        refinable = res.refinable
      } catch (err: any) {
        answer = `Could not get answer: ${err.message}`
        setCurrentAnswer(answer)
      }

      const elapsedMs = Math.round(performance.now() - tStart)
      if (!answer) { answer = 'Not in documents.'; setCurrentAnswer(answer) }
      setCurrentSources(sources)
      setCurrentElapsedMs(elapsedMs)
      setCurrentEngine(engine)
      answerCacheRef.current.set(cacheKey, { answer, sources, cachedAt: Date.now() })

      const speakerRole = speakerId === 1 ? 'Primary Applicant' : speakerId > 1 ? 'Co-Applicant' : 'Customer'
      const entry: AnswerEntry = {
        id: entryId,
        question: cleaned, rawTranscript: transcript, answer, sources,
        timestamp: new Date(), language: lang, speakerRole, questionType,
        elapsedMs, engine, refined: false,
      }
      setAnswerHistory(prev => [entry, ...prev].slice(0, MAX_HISTORY))
      setStatus('listening')

      // Kick off background refinement (only for fast-path answers; cached
      // answers and error replies are not refinable).
      if (refinable && answer && !answer.toLowerCase().startsWith('could not')) {
        setRefining(true)
        refineAnswerInBackground(entryId, cleaned, questionType, sessionContext, lang, answerHistory)
          .finally(() => {
            if (currentEntryIdRef.current === entryId) setRefining(false)
          })
      }

    } catch (err: any) {
      console.error('Processing error:', err)
      showError(err.message || 'Processing failed')
      setStatus('listening')
    }
  }

  // ── Two-speaker live path helpers ────────────────────────────────────────

  function pushLiveTurn(turn: Omit<SpeakerTurn, 'id'>) {
    const entry: SpeakerTurn = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      ...turn,
    }
    const next = [...liveSpeakerLogRef.current, entry].slice(-15)
    liveSpeakerLogRef.current = next
    setLiveSpeakerLog([...next])
    setTimeout(() => {
      transcriptPaneRef.current?.scrollTo({
        top: transcriptPaneRef.current.scrollHeight, behavior: 'smooth',
      })
    }, 40)
  }

  async function ensureLiveSession(): Promise<string | null> {
    if (liveSessionIdRef.current) return liveSessionIdRef.current
    try {
      const res  = await fetch('/api/voice/live/session/start', { method: 'POST' })
      const data = await res.json()
      if (data.success && data.session_id) {
        liveSessionIdRef.current = data.session_id
        console.info(`[LIVE] session started: ${data.session_id} whisper=${data.whisper_model}/${data.whisper_device}`)
        return data.session_id
      }
      // Backend responded but session didn't start — log the actual error for diagnosis
      console.error('[LIVE] session start rejected by backend (check server logs):', data)
    } catch (e) {
      console.warn('[LIVE] session start network error (server may be down or restarting):', e)
    }
    return null
  }

  // Speak the reply text via Web Speech API (SpeechSynthesis).
  // Mic stays active during playback (full-duplex). isSpeakingRef only drives
  // the liveStatus UI chip; it no longer pauses VAD.
  // When done (or on error), we drain the queue — at most one pending blob.
  function speakReply(text: string) {
    // When TTS is off or unavailable, drain queue immediately so the next
    // utterance is not stuck waiting for a speech-end event that never fires.
    if (!ttsEnabledRef.current || !('speechSynthesis' in window)) {
      drainLiveQueue()
      return
    }
    window.speechSynthesis.cancel()           // cancel any queued utterance
    isSpeakingRef.current = true
    setLiveStatus('speaking')
    const utt = new SpeechSynthesisUtterance(text)
    utt.rate  = 1.05
    utt.pitch = 1.0
    // Set TTS language to match the selected reply language for correct pronunciation
    const ttsLangMap: Record<SttLangHint, string> = {
      'en': 'en-IN', 'hi': 'hi-IN', 'mr': 'mr-IN', 'auto': 'en-IN',
    }
    utt.lang = ttsLangMap[sttLanguage] || 'en-IN'
    utt.onend = () => {
      isSpeakingRef.current = false
      if (isActiveRef.current) setLiveStatus('listening')
      drainLiveQueue()
    }
    utt.onerror = () => {
      isSpeakingRef.current = false
      if (isActiveRef.current) setLiveStatus('listening')
      drainLiveQueue()
    }
    window.speechSynthesis.speak(utt)
  }

  function drainLiveQueue() {
    const next = liveQueueRef.current.shift()
    if (next) processAudioChunkLive(next)
  }

  async function processAudioChunkLive(blob: Blob) {
    if (blob.size < 800) return

    // If a request is already in-flight, queue this blob (keep at most 2).
    // Oldest blob is dropped when the queue is full so we always process the
    // most recent speech rather than stale audio.
    if (isProcessingLiveRef.current) {
      liveQueueRef.current = [...liveQueueRef.current.slice(-1), blob]
      return
    }
    isProcessingLiveRef.current = true

    const sessionId = await ensureLiveSession()
    if (!sessionId) { showError('Could not start live session — restart the backend server and try again'); return }

    setLiveStatus('thinking')
    setStatus('answering')

    const prevTranscript = JSON.stringify(
      liveSpeakerLogRef.current.slice(-9).map(e => ({ speaker: e.speaker, text: e.text }))
    )

    const fd = new FormData()
    fd.append('file', blob, 'chunk.webm')
    fd.append('session_id', sessionId)
    fd.append('language', sttLanguage)
    fd.append('prev_transcript', prevTranscript)
    fd.append('session_context', JSON.stringify(sessionContext))
    fd.append('two_speaker_mode', 'true')   // always-on two-speaker path: relaxes gates
    if (myRoleRef.current) fd.append('role_hint', `${myRoleRef.current} is the HDFC agent`)

    const entryId = Date.now().toString()
    currentEntryIdRef.current = entryId
    setCurrentAnswer('')
    setCurrentSources([])

    try {
      const res = await fetch('/api/voice/live/process', { method: 'POST', body: fd })
      if (!res.ok) throw new Error(`Server error: ${res.status}`)

      const reader  = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let currentSpeaker: SpeakerLabel = 'Unknown'
      let currentText = ''
      let answer = ''
      let sources: string[] = []
      const tStart = performance.now()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))

            if (data.type === 'turn') {
              // ① Update the two-speaker transcript pane immediately
              currentSpeaker = (data.speaker as SpeakerLabel) || 'Unknown'
              currentText    = data.transcript || ''
              setRawTranscript(currentText)
              if (currentText) pushLiveTurn({ speaker: currentSpeaker, text: currentText, timestamp: new Date() })

            } else if (data.type === 'wait') {
              // Filler / agent spoke last / no context → show idle
              setLiveStatus('wait')
              setCurrentAnswer('')
              setStatus('listening')
              setTimeout(() => setLiveStatus('listening'), 1800)
              isProcessingLiveRef.current = false
              drainLiveQueue()
              return

            } else if (data.token && !data.done) {
              // ③ Streaming reply token
              answer += data.token
              setCurrentAnswer(prev => prev + data.token)
              setLiveStatus('suggestion_ready')

            } else if (data.done) {
              if (data.wait) {
                // GPT said WAIT after reading full transcript
                setCurrentAnswer('')
                setLiveStatus('wait')
                setStatus('listening')
                setTimeout(() => setLiveStatus('listening'), 1800)
                isProcessingLiveRef.current = false
                drainLiveQueue()
                return
              }
              sources = data.sources || []
              setCurrentSources(sources)
              setCurrentElapsedMs(data.total_ms ?? Math.round(performance.now() - tStart))
              // engine: 'gpt-4o-mini' | 'openrouter/gpt-4o-mini' | ollama model name
              const eng = (data.engine || '') as string
              setCurrentEngine(
                eng.includes('gpt-4o-mini') ? 'gpt-4o-mini'
                : eng.startsWith('openrouter') ? 'openrouter-refined'
                : eng ? 'ollama-fast'
                : 'unknown'
              )
              setLiveStatus('suggestion_ready')
            }
          } catch { /* skip malformed SSE lines */ }
        }
      }

      if (answer) {
        const lang  = resolveReplyLanguage(sttLanguage, currentText)
        const qType = isObjection(currentText) ? 'objection' : 'factual' as const
        setDetectedLanguage(lang)
        setCurrentQuestionType(qType)
        const entry: AnswerEntry = {
          id: entryId, question: currentText, rawTranscript: currentText,
          answer, sources, timestamp: new Date(), language: lang,
          speakerRole: currentSpeaker, questionType: qType,
          elapsedMs: Math.round(performance.now() - tStart),
          engine: currentEngine, refined: false,
        }
        setAnswerHistory(prev => [entry, ...prev].slice(0, MAX_HISTORY))
        answerCacheRef.current.set(currentText.toLowerCase().trim(), { answer, sources, cachedAt: Date.now() })
        // Speak the suggestion aloud. speakReply will drain the queue when done.
        isProcessingLiveRef.current = false
        speakReply(answer)
      } else {
        isProcessingLiveRef.current = false
        drainLiveQueue()
      }

      setStatus('listening')
      // liveStatus stays 'suggestion_ready' (or 'speaking' when TTS active) so
      // the card remains visible until the next utterance flips it to 'thinking'.

    } catch (err: any) {
      console.error('[LIVE] processAudioChunkLive:', err)
      showError(err.message || 'Live processing failed')
      isProcessingLiveRef.current = false
      drainLiveQueue()
      setLiveStatus('idle')
      setStatus('listening')
    }
  }

  // ─────────────────────────────────────────────────────────────────────────

  async function toggleSession() {
    if (!isActive) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
        })
        streamRef.current = stream
        isActiveRef.current = true
        setIsActive(true)
        setStatus('listening')
        setLiveStatus('listening')
        setSpeakerRegistered(false)
        firstSpeakerDetectedRef.current = false
        firstSpeakerIdRef.current = 0
        setWaveformActive(true)

        const mimeType = getMimeType()

        // ── VAD: energy-based Voice Activity Detection ────────────────────
        // Replaces the old fixed-3s chunk loop. Each complete utterance
        // (speech followed by VAD_SILENCE_MS of silence) is a self-contained
        // WebM blob sent to processAudioChunk. No mid-sentence responses.
        try {
          const micStream = streamRef.current!
          const AudioCtx = (window.AudioContext || (window as any).webkitAudioContext) as typeof AudioContext
          const audioCtx = new AudioCtx({ sampleRate: 16000 })
          audioContextRef.current = audioCtx
          const source = audioCtx.createMediaStreamSource(micStream)
          const analyser = audioCtx.createAnalyser()
          analyser.fftSize = 512
          analyser.smoothingTimeConstant = 0.3
          source.connect(analyser)
          analyserRef.current = analyser
          const freqData = new Uint8Array(analyser.frequencyBinCount)

          // Reset session speaker state on every new session
          sessionTranscriptRef.current = []
          speakerMappingRef.current = new Map()

          let maxUttTimer: ReturnType<typeof setTimeout> | null = null

          function flushUtterance() {
            if (vadSilenceTimer.current) { clearTimeout(vadSilenceTimer.current); vadSilenceTimer.current = null }
            if (maxUttTimer) { clearTimeout(maxUttTimer); maxUttTimer = null }
            vadSpeechActive.current = false
            if (vadRecorderRef.current?.state === 'recording') vadRecorderRef.current.stop()
          }

          // Frequency-band indices for 7 waveform bars (spaced across voice range).
          const WAVE_BINS = [2, 5, 9, 14, 20, 27, 35]

          function tick() {
            if (!isActiveRef.current) return
            // Full-duplex: mic stays active during TTS playback. echoCancellation
            // (set on getUserMedia) suppresses the speaker feed at the hardware
            // level. VAD/waveform never pause — new speech is captured and queued
            // (via isProcessingLiveRef / liveQueueRef) even while the AI is talking.
            analyser.getByteFrequencyData(freqData)
            const rms = Math.sqrt(freqData.reduce((s, v) => s + v * v, 0) / freqData.length)

            // Drive waveform bar heights directly — no React state, no re-renders.
            const waveEl = waveformRef.current
            if (waveEl) {
              const bars = waveEl.children
              for (let i = 0; i < WAVE_BINS.length && i < bars.length; i++) {
                const raw = freqData[WAVE_BINS[i]] / 255          // 0..1
                const h   = Math.max(0.08, Math.min(1, raw * 1.6)) // clamp + amplify
                ;(bars[i] as HTMLElement).style.height = `${h * 100}%`
              }
            }

            if (rms > VAD_THRESHOLD) {
              // ── Speech detected ──────────────────────────────────────────
              if (vadSilenceTimer.current) { clearTimeout(vadSilenceTimer.current); vadSilenceTimer.current = null }
              if (!vadSpeechActive.current) {
                vadSpeechActive.current = true
                vadChunksRef.current = []
                const rec = mimeType
                  ? new MediaRecorder(micStream, { mimeType })
                  : new MediaRecorder(micStream)
                vadRecorderRef.current = rec
                rec.ondataavailable = (e) => { if (e.data.size > 0) vadChunksRef.current.push(e.data) }
                rec.onstop = () => {
                  const blob = new Blob(vadChunksRef.current, { type: mimeType || 'audio/webm' })
                  vadChunksRef.current = []
                  vadRecorderRef.current = null
                  // Auto mode → new two-speaker live path (Person 1/2 labeling,
                  // Whisper STT, GPT-4o-mini decision). No Deepgram, no filter.
                  if (blob.size > MIN_CHUNK_BYTES) processAudioChunkLive(blob)
                }
                rec.start()
                // Safety: flush after VAD_MAX_UTT_MS even if no silence detected
                maxUttTimer = setTimeout(flushUtterance, VAD_MAX_UTT_MS)
              }
            } else {
              // ── Silence detected ─────────────────────────────────────────
              if (vadSpeechActive.current && !vadSilenceTimer.current) {
                vadSilenceTimer.current = setTimeout(flushUtterance, VAD_SILENCE_MS)
              }
            }
            requestAnimationFrame(tick)
          }

          requestAnimationFrame(tick)
        } catch (vadErr) {
          console.error('VAD init failed:', vadErr)
          showError('Could not start voice detection. Try refreshing.')
        }

      } catch (err: any) {
        const msgMap: Record<string, string> = {
          NotAllowedError: 'Microphone access denied. Allow microphone permission in browser settings.',
          NotFoundError: 'No microphone found. Connect a microphone and try again.',
          NotReadableError: 'Microphone is in use by another app. Close other apps and try again.',
        }
        showError(msgMap[err.name] || `Could not access microphone: ${err.message}`)
      }
    } else {
      isActiveRef.current = false
      setIsActive(false)
      setStatus('idle')
      setWaveformActive(false)
      setSpeakerRegistered(false)
      firstSpeakerDetectedRef.current = false
      // Reset live-path session so next call gets a fresh adaptive session
      liveSessionIdRef.current = null
      liveSpeakerLogRef.current = []
      setLiveSpeakerLog([])
      setLiveStatus('idle')
      // Cancel any in-progress TTS and reset concurrency state
      if ('speechSynthesis' in window) window.speechSynthesis.cancel()
      isSpeakingRef.current      = false
      isProcessingLiveRef.current = false
      liveQueueRef.current       = []
      // Stop VAD recorder + silence timer
      if (vadSilenceTimer.current) { clearTimeout(vadSilenceTimer.current); vadSilenceTimer.current = null }
      vadSpeechActive.current = false
      if (vadRecorderRef.current?.state === 'recording') vadRecorderRef.current.stop()
      vadRecorderRef.current = null
      // Close AudioContext (frees mic + CPU)
      if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null }
      analyserRef.current = null
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop()
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
        streamRef.current = null
      }
    }
  }

  function showError(msg: string) {
    setErrorMessage(msg)
    setTimeout(() => setErrorMessage(''), 6000)
  }

  // ── CRM Session Summary ──────────────────────────────────────────────────
  // Flattens the answer history (oldest first) into role/content pairs the
  // backend expects, hits /api/voice/session-summary, shows the result in a
  // modal. Backend tries local Ollama first, OpenRouter as fallback — both
  // paths return the same shape, so this caller stays simple.
  async function endSession() {
    if (answerHistory.length === 0) {
      showError('No conversation yet — ask at least one question first.')
      return
    }

    // Stop any active recording before generating the summary so the mic icon
    // settles to idle while the LLM thinks.
    if (isActive) toggleSession()
    if (pttPressed) pttStop()

    setSessionSummary({ loading: true, text: '' })

    // History is stored newest-first → reverse for chronological summary
    const conversation = answerHistory.slice().reverse().flatMap(e => ([
      { role: 'Customer',  content: e.question },
      { role: 'Assistant', content: e.answer },
    ]))

    try {
      const res = await fetch('/api/voice/session-summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation,
          session_context: sessionContext,
        }),
      })
      const data = await res.json()
      if (data.success && data.summary) {
        setSessionSummary({ loading: false, text: data.summary, engine: data.engine })
      } else {
        setSessionSummary({
          loading: false, text: '',
          error: data.error || 'Could not generate summary.',
        })
      }
    } catch (err: any) {
      setSessionSummary({
        loading: false, text: '',
        error: err.message || 'Network error while generating summary.',
      })
    }
  }

  function copySummary() {
    if (sessionSummary?.text) {
      navigator.clipboard.writeText(sessionSummary.text)
    }
  }

  async function simulateQuestion(raw: string) {
    if (!raw.trim() || testLoading) return
    setTestLoading(true)
    setCurrentAnswer('')
    setRawTranscript(raw)
    try {
      const lang = resolveReplyLanguage(sttLanguage, raw)
      setDetectedLanguage(lang)
      const cleaned = raw.trim()
      setCleanedQuestion(cleaned)
      const qType = isObjection(cleaned) ? 'objection' : 'factual'
      setCurrentQuestionType(qType)
      setStatus('answering')

      const cacheKey = cleaned.toLowerCase().trim()
      const cached = getCachedAnswer(cacheKey)
      if (cached) {
        setCurrentAnswer(cached.answer); setCurrentSources(cached.sources)
        setCurrentElapsedMs(0); setCurrentEngine('unknown'); setCurrentRefined(false)
        setStatus('idle'); setTestLoading(false); return
      }

      const entryId = Date.now().toString()
      currentEntryIdRef.current = entryId
      setCurrentElapsedMs(null); setCurrentEngine('unknown'); setCurrentRefined(false)
      const tStart = performance.now()

      let answer = ''; let sources: string[] = []
      let engine: AnswerEngine = 'unknown'; let refinable = false
      try {
        const res = await getAnswerStream(cleaned, qType, sessionContext, (token) => {
          answer += token
          setCurrentAnswer(prev => prev + token)
        }, lang, answerHistory)
        sources = res.sources; engine = res.engine; refinable = res.refinable
      } catch (err: any) {
        answer = `Could not get answer: ${err.message}`
        setCurrentAnswer(answer)
      }

      const elapsedMs = Math.round(performance.now() - tStart)
      if (!answer) { answer = 'Not in documents.'; setCurrentAnswer(answer) }
      setCurrentSources(sources)
      setCurrentElapsedMs(elapsedMs); setCurrentEngine(engine)
      answerCacheRef.current.set(cacheKey, { answer, sources, cachedAt: Date.now() })

      const entry: AnswerEntry = {
        id: entryId,
        question: cleaned, rawTranscript: raw, answer, sources,
        timestamp: new Date(), language: lang, speakerRole: 'Customer (test)', questionType: qType,
        elapsedMs, engine, refined: false,
      }
      setAnswerHistory(prev => [entry, ...prev].slice(0, MAX_HISTORY))

      if (refinable && answer && !answer.toLowerCase().startsWith('could not')) {
        setRefining(true)
        refineAnswerInBackground(entryId, cleaned, qType, sessionContext, lang, answerHistory)
          .finally(() => { if (currentEntryIdRef.current === entryId) setRefining(false) })
      }
    } catch (err: any) {
      showError(err.message || 'Test failed')
      setCurrentAnswer('')
    } finally {
      setStatus('idle')
      setTestLoading(false)
      setTestInput('')
    }
  }

  function getTimeAgo(date: Date): string {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
    if (seconds < 60) return `${seconds}s ago`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    return `${Math.floor(seconds / 3600)}h ago`
  }

  useEffect(() => {
    return () => {
      isActiveRef.current = false
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop()
      }
      if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop())
      if (enrollRecorderRef.current && enrollRecorderRef.current.state !== 'inactive') {
        enrollRecorderRef.current.stop()
      }
      if (enrollStreamRef.current) enrollStreamRef.current.getTracks().forEach(t => t.stop())
      if (enrollTimerRef.current) window.clearInterval(enrollTimerRef.current)
      if (pttRecorderRef.current && pttRecorderRef.current.state !== 'inactive') {
        pttRecorderRef.current.stop()
      }
      if (pttStreamRef.current) pttStreamRef.current.getTracks().forEach(t => t.stop())
      pttVadRunningRef.current = false
      if (pttVadTimerRef.current) clearTimeout(pttVadTimerRef.current)
      if (pttMaxUttTimerRef.current) clearTimeout(pttMaxUttTimerRef.current)
      if (pttAudioCtxRef.current) { pttAudioCtxRef.current.close(); pttAudioCtxRef.current = null }
    }
  }, [])

  // ───────────────────────── Mic mode picker (Step 1) ────────────────────────
  if (micMode === null) {
    return (
      <div className="live-shell">
        <div className="card">
          <div className="card-header">
            <div className="card-title">
              <Mic size={18} style={{ color: 'var(--color-red)' }} /> Choose Mic Mode
            </div>
          </div>
          <div className="card-body">
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
              Pick how the assistant should listen to your customer. You can change this anytime.
            </p>
            <div className="mode-picker">
              <button
                type="button"
                className="mode-card"
                onClick={() => setMicMode('push')}
              >
                <span className="mode-card-badge recommended">Recommended</span>
                <div className="mode-card-icon"><Hand size={22} /></div>
                <div className="mode-card-title">Push to Talk</div>
                <div className="mode-card-subtitle">
                  Hold the button when the client speaks. Mic is silent at all other times — simple and accurate, no setup needed.
                </div>
              </button>

              <button
                type="button"
                className="mode-card"
                onClick={() => setMicMode('auto')}
              >
                <span className="mode-card-badge advanced">Two-Speaker</span>
                <div className="mode-card-icon"><Filter size={22} /></div>
                <div className="mode-card-title">Always-On (2-Speaker)</div>
                <div className="mode-card-subtitle">
                  Mic stays on. Both you and the customer are transcribed and labeled Person 1 / Person 2 — no enrollment needed. GPT-4o-mini reads both sides and suggests replies only when the customer asks something.
                </div>
              </button>
            </div>

            {/* Filter-engine sub-toggle — only relevant when Auto is picked.
                Default is 'voiceprint' (pre-enrolled fingerprint) so the
                experience for existing users is unchanged. 'adaptive' is the
                new no-enrolment online clustering path. */}
            <div style={{
              marginTop: 18, padding: '12px 14px',
              background: 'var(--color-bg-soft, #fafafa)',
              border: '1px solid var(--color-border)', borderRadius: 8,
            }}>
              <div style={{
                fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
                textTransform: 'uppercase', color: 'var(--color-text-muted)',
                marginBottom: 8,
              }}>
                Auto Filter — Engine
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="button"
                  onClick={() => setFilterMode('voiceprint')}
                  className={`pill${filterMode === 'voiceprint' ? ' active' : ''}`}>
                  Voiceprint (pre-enrol)
                </button>
                <button type="button"
                  onClick={() => setFilterMode('adaptive')}
                  className={`pill${filterMode === 'adaptive' ? ' active' : ''}`}>
                  Adaptive (no enrol)
                </button>
              </div>
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
                {filterMode === 'voiceprint'
                  ? 'Records 3 short phrases up front to fingerprint your voice. Best when the customer is on a phone/speakerphone.'
                  : 'Skips enrolment. Learns the two speakers during the first ~8 chunks of the live call, then locks. Better when the customer is in the same room.'}
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ───────────────────────── Session setup screen (Step 2) ───────────────────
  if (!sessionStarted) {
    return (
      <div className="live-shell">
        <div className="card">
          <div className="card-header">
            <div className="card-title">
              <Sparkles size={18} style={{ color: 'var(--color-red)' }} /> Session Setup
              <span className="tag" style={{
                marginLeft: 8,
                background: micMode === 'push' ? 'var(--color-success-tint)' : '#f5f3ff',
                color: micMode === 'push' ? 'var(--color-success)' : '#7c3aed'
              }}>
                {micMode === 'push' ? 'Push to Talk' : 'Auto Filter'}
              </span>
            </div>
            <button className="btn btn-ghost" onClick={() => setMicMode(null)}
                    style={{ padding: '6px 10px', fontSize: 12 }}>
              <ArrowLeft size={13} /> Change mode
            </button>
          </div>
          <div className="card-body">
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
              Configure customer profile so live answers are tailored to your meeting.
            </p>

            <div className="session-config-row" style={{ marginBottom: 20 }}>
              <span className="session-config-row-label">Customer Type</span>
              <div className="pill-row">
                {CUSTOMER_TYPES.map(t => (
                  <button key={t.value} type="button"
                    onClick={() => setSessionContext(p => ({ ...p, customerType: t.value }))}
                    className={`pill${sessionContext.customerType === t.value ? ' active' : ''}`}>
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="session-config-row" style={{ marginBottom: 20 }}>
              <span className="session-config-row-label">Loan Type</span>
              <div className="pill-row">
                {LOAN_TYPES.map(t => (
                  <button key={t.value} type="button"
                    onClick={() => setSessionContext(p => ({ ...p, loanType: t.value }))}
                    className={`pill${sessionContext.loanType === t.value ? ' active' : ''}`}>
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="session-config-row" style={{ marginBottom: 20 }}>
              <span className="session-config-row-label">Customer Status</span>
              <div className="pill-row">
                {(['new', 'existing'] as const).map(s => (
                  <button key={s} type="button"
                    onClick={() => setSessionContext(p => ({ ...p, customerStatus: s }))}
                    className={`pill${sessionContext.customerStatus === s ? ' active' : ''}`}
                    style={{ textTransform: 'capitalize' }}>
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="session-config-row" style={{ marginBottom: 24 }}>
              <span className="session-config-row-label">Pre-Approved?</span>
              <div className="pill-row">
                {(['yes', 'no', 'unknown'] as const).map(s => (
                  <button key={s} type="button"
                    onClick={() => setSessionContext(p => ({ ...p, preApproved: s }))}
                    className={`pill${sessionContext.preApproved === s ? ' active' : ''}`}
                    style={{ textTransform: 'capitalize' }}>
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <button onClick={() => setSessionStarted(true)} className="btn btn-primary btn-lg btn-block">
              Confirm &amp; Proceed
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ───────────────────────── Enrollment (Auto Filter, Step 3a) ──────────────
  // Show the enrolment screen until ALL phrases have been recorded.
  // `enrollStatus === 'done'` is set by sendEnrollmentToBackend() only after
  // step (ENROLL_PHRASES.length - 1) completes — see line ~543.
  //
  // Previous bug: this used to short-circuit as soon as voiceprintId / rmSpeakerId
  // were non-null, which happens after step 1. That kicked the user out of the
  // enrolment flow before reading the Hindi (step 2) and Hinglish (step 3)
  // phrases — leaving a 1-sample fingerprint that mis-classified chunks
  // during the live session.
  // Adaptive mode skips enrolment entirely — clustering learns the agent
  // during the first ~8 chunks of the live call.
  const stillEnrolling =
    LIVE_ENROLLMENT_ENABLED &&
    micMode === 'auto' &&
    filterMode === 'voiceprint' &&
    enrollStatus !== 'done' &&
    enrollStep < ENROLL_PHRASES.length
  if (stillEnrolling) {
    return (
      <div className="live-shell">
        <div className="enroll-shell">
          <span className="enroll-step">
            <Filter size={11} /> Step {enrollStep + 1} of {ENROLL_PHRASES.length} —
            {' '}{ENROLL_PHRASES[enrollStep].language} sample
          </span>
          <div className="enroll-title">
            {enrollStep === 0 && 'Identify your voice'}
            {enrollStep === 1 && 'One more — Hindi sample'}
            {enrollStep === 2 && 'Last one — mixed (Hinglish)'}
          </div>
          <div className="enroll-subtitle" style={{ maxWidth: 560 }}>
            We capture {ENROLL_PHRASES.length} short clips ({ENROLL_DURATION_S}s each) in
            English, Hindi, and Hinglish so the filter recognises you no matter which
            language you switch to mid-call. Read the phrase below at your normal pace.
          </div>

          {/* The phrase the user should read on this step */}
          <div
            style={{
              maxWidth: 560,
              padding: '18px 22px',
              background: '#fff7f6',
              border: '1px solid #fecaca',
              borderRadius: 10,
              fontSize: 16,
              lineHeight: 1.55,
              color: 'var(--color-text)',
              fontWeight: 500,
              textAlign: 'center',
              boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
            }}
          >
            <span style={{
              display: 'inline-block', marginBottom: 8, padding: '3px 10px',
              fontSize: 10, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase',
              background: 'var(--color-red)', color: '#fff', borderRadius: 999,
            }}>
              {ENROLL_PHRASES[enrollStep].language}
            </span>
            <div>"{ENROLL_PHRASES[enrollStep].phrase}"</div>
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--color-text-muted)',
                          fontWeight: 400, fontStyle: 'italic' }}>
              {ENROLL_PHRASES[enrollStep].hint}
            </div>
          </div>

          {/* Tiny progress dots for the 3 steps */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
            {ENROLL_PHRASES.map((_, idx) => (
              <span key={idx}
                style={{
                  width: 8, height: 8, borderRadius: 999,
                  background: idx < enrollStep ? 'var(--color-success)'
                            : idx === enrollStep ? 'var(--color-red)'
                            : 'var(--color-border)',
                  transition: 'background 0.2s',
                }}
              />
            ))}
            <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--color-text-muted)' }}>
              {enrollStep} of {ENROLL_PHRASES.length} captured
              {voiceprintSamples > 0 && ` · ${voiceprintSamples} sample${voiceprintSamples === 1 ? '' : 's'} stored`}
            </span>
          </div>

          {enrollStatus === 'idle' && (
            <button className="enroll-record-btn" onClick={startEnrollment}>
              <Mic size={28} />
              <span>
                {enrollStep === 0 ? 'Start' : `Record sample ${enrollStep + 1}`}
              </span>
            </button>
          )}

          {enrollStatus === 'recording' && (
            <>
              <button className="enroll-record-btn recording" disabled>
                <Mic size={28} />
                <span>{Math.max(0, Math.ceil(ENROLL_DURATION_S - enrollSeconds))}s</span>
              </button>
              <div className="enroll-progress">
                <div className="enroll-progress-bar"
                     style={{ width: `${Math.min(100, (enrollSeconds / ENROLL_DURATION_S) * 100)}%` }} />
              </div>
              <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                Speaking… keep going.
              </div>
            </>
          )}

          {enrollStatus === 'sending' && (
            <>
              <div className="spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
              <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                Analyzing your voice profile…
              </div>
            </>
          )}

          {enrollStatus === 'error' && (
            <>
              <div className="alert alert-error" style={{ width: '100%', maxWidth: 440 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                  <AlertTriangle size={15} /> {enrollError || 'Enrollment failed.'}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-primary" onClick={startEnrollment}>
                  <RotateCcw size={14} /> Try again
                </button>
                <button className="btn btn-secondary" onClick={() => setMicMode('push')}>
                  Switch to Push to Talk
                </button>
              </div>
            </>
          )}

          <button className="btn btn-ghost" onClick={() => setMicMode(null)}
                  style={{ marginTop: 8 }}>
            <ArrowLeft size={13} /> Back to mode selection
          </button>
        </div>
      </div>
    )
  }

  // ───────────────────────── Live session screen (Step 3b) ──────────────────
  return (
    <div className="live-shell">

      {errorMessage && (
        <div className="alert alert-error">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={15} /> {errorMessage}
          </span>
          <button onClick={() => setErrorMessage('')} className="btn btn-ghost btn-icon-only">
            <X size={14} />
          </button>
        </div>
      )}

      {micMode === 'auto' && singleSpeakerStreak >= 3 && (
        <div className="alert alert-warning">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={15} />
            Having trouble separating voices — consider switching to Push-to-Talk mode.
          </span>
          <button onClick={() => { setMicMode('push'); resetEnrollment(); }}
                  className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: 12 }}>
            Switch to PTT
          </button>
        </div>
      )}

      {micMode === 'auto' && filterMode === 'adaptive' && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <span className="enroll-success" style={{
            background: adaptivePhase === 'locked' ? 'var(--color-success-tint)' : '#fff7ed',
            color: adaptivePhase === 'locked' ? 'var(--color-success)' : '#9a3412',
          }}>
            <UserCheck size={13} />
            {adaptivePhase === 'idle' && <>Adaptive mode — waiting for first chunk…</>}
            {adaptivePhase === 'calibrating' && (
              <>Adaptive — calibrating ({adaptiveChunksSeen}/{adaptiveCalibMin} chunks)
                 · all audio passes through during warm-up</>
            )}
            {adaptivePhase === 'locked' && (
              <>Adaptive — locked &amp; filtering ({adaptiveChunksSeen} chunks seen)</>
            )}
            {agentSkipCount > 0 && (
              <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.75 }}>
                · {agentSkipCount} agent chunk{agentSkipCount === 1 ? '' : 's'} skipped
              </span>
            )}
          </span>
          <button className="btn btn-ghost" onClick={resetEnrollment}
                  style={{ padding: '4px 10px', fontSize: 12 }}>
            <RotateCcw size={12} /> Reset clustering
          </button>
        </div>
      )}

      {micMode === 'auto' && filterMode === 'voiceprint' && (rmSpeakerId !== null || voiceprintId !== null) && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <span className="enroll-success" style={{ background: 'var(--color-success-tint)' }}>
            <UserCheck size={13} />
            {voiceprintId
              ? <>Voiceprint enrolled ({voiceprintBackend || 'mfcc'}
                  {voiceprintSamples > 1 && `, ${voiceprintSamples} samples`})
                  {rmSpeakerId !== null && <> + Deepgram label #{rmSpeakerId}</>}
                  {' '}— filtering active
                </>
              : <>Voice enrolled (speaker #{rmSpeakerId}) — filtering active</>
            }
            {agentSkipCount > 0 && (
              <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.75 }}>
                · {agentSkipCount} agent chunk{agentSkipCount === 1 ? '' : 's'} skipped
              </span>
            )}
            {lastSimilarity !== null && (
              <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.6 }}>
                · sim {lastSimilarity.toFixed(2)}
              </span>
            )}
          </span>
          <button className="btn btn-ghost" onClick={resetEnrollment}
                  style={{ padding: '4px 10px', fontSize: 12 }}>
            <RotateCcw size={12} /> Re-enroll
          </button>
        </div>
      )}

      {/* ── Reply language ────────────────────────────────────────────────
         Controls (a) STT hint to Whisper and (b) the LLM reply directive.
         The selected language governs the entire pipeline — no auto-switching
         regardless of what script the customer speaks in. */}
      <div className="card card-padded" style={{ padding: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)',
                         textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Language
          </span>
          <div className="pill-row" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {([
              { v: 'en', label: 'English' },
              { v: 'hi', label: 'Hindi' },
            ] as const).map(opt => (
              <button
                key={opt.v}
                onClick={() => setSttLanguage(opt.v)}
                className={`pill${sttLanguage === opt.v ? ' active' : ''}`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <span style={{ fontSize: 11, color: 'var(--color-text-faint)', marginLeft: 'auto' }}>
            Applies to transcription, responses, and text-to-speech throughout the call.
          </span>
        </div>
      </div>

      {/* ── Two-speaker live transcript pane (new June 2026 path) ─────────────
           Person 1 = blue, Person 2 = green. Newest turn at bottom, auto-scrolls.
           Only shown in Auto (always-on) mode while session is active. */}
      {micMode === 'auto' && isActive && (
        <div className="card card-padded" style={{ padding: 12 }}>
          {/* Status chip + role picker row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            {/* Persistent mic-live dot — always visible while session is active.
                Shows the agent that microphone capture is continuous regardless
                of what the processing status chip currently says. */}
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              padding: '3px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600,
              background: '#fee2e2', color: '#b91c1c',
            }}>
              <span className="status-dot listening" style={{ width: 6, height: 6 }} />
              Mic live
            </span>
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '4px 11px', borderRadius: 999, fontSize: 12, fontWeight: 600,
              background: liveStatus === 'thinking'          ? '#fef9c3'
                        : liveStatus === 'suggestion_ready'  ? '#dcfce7'
                        : liveStatus === 'speaking'          ? '#f5f3ff'
                        : liveStatus === 'wait'              ? '#f1f5f9'
                        : '#eff6ff',
              color:      liveStatus === 'thinking'          ? '#854d0e'
                        : liveStatus === 'suggestion_ready'  ? '#166534'
                        : liveStatus === 'speaking'          ? '#6d28d9'
                        : liveStatus === 'wait'              ? '#64748b'
                        : '#1d4ed8',
            }}>
              {liveStatus === 'listening'        && <><span className="status-dot listening" style={{ width: 8, height: 8 }} /> Listening</>}
              {liveStatus === 'thinking'         && <><span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} /> Thinking…</>}
              {liveStatus === 'suggestion_ready' && <><Sparkles size={12} /> Suggestion ready</>}
              {liveStatus === 'speaking'         && <><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 999, background: '#7c3aed', marginRight: 2 }} /> Speaking · mic live</>}
              {liveStatus === 'wait'             && <>· · · Waiting (agent spoke / filler)</>}
              {liveStatus === 'idle'             && <>Idle — tap mic to start</>}
            </span>

            {/* Optional role picker: helps GPT infer which person is the agent */}
            <span style={{ fontSize: 11, color: 'var(--color-text-faint)', marginLeft: 4 }}>I am:</span>
            {(['Person 1', 'Person 2'] as const).map(p => (
              <button key={p} type="button"
                onClick={() => setMyRole(myRole === p ? null : p)}
                className={`pill${myRole === p ? ' active' : ''}`}
                style={{ fontSize: 11, padding: '3px 9px' }}>
                {p}
              </button>
            ))}
            {myRole && (
              <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                (GPT told: {myRole} is the agent)
              </span>
            )}

            {/* TTS on/off toggle — always-on mode only */}
            <button
              type="button"
              onClick={() => {
                const next = !ttsEnabled
                setTtsEnabled(next)
                if (!next && 'speechSynthesis' in window) {
                  window.speechSynthesis.cancel()
                  isSpeakingRef.current = false
                  if (isActiveRef.current) setLiveStatus('listening')
                }
              }}
              title={ttsEnabled ? 'Voice reply is ON — click to mute' : 'Voice reply is OFF — click to enable'}
              style={{
                marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '3px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                border: '1px solid', transition: 'all 0.15s',
                background: ttsEnabled ? '#f5f3ff' : '#f1f5f9',
                color:      ttsEnabled ? '#6d28d9' : '#64748b',
                borderColor: ttsEnabled ? '#c4b5fd' : '#cbd5e1',
              }}
            >
              {ttsEnabled ? '🔊 Voice Reply ON' : '🔇 Voice Reply OFF'}
            </button>
          </div>

          {/* Transcript turns */}
          {liveSpeakerLog.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--color-text-faint)', padding: '8px 0', textAlign: 'center' }}>
              Waiting for first utterance…
            </div>
          ) : (
            <div
              ref={transcriptPaneRef}
              style={{ maxHeight: 200, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 3 }}
            >
              {liveSpeakerLog.map(entry => (
                <div key={entry.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '3px 0' }}>
                  <span style={{
                    fontSize: 11, fontWeight: 700, minWidth: 58, flexShrink: 0,
                    color: entry.speaker === 'Person 1' ? '#1d4ed8'
                         : entry.speaker === 'Person 2' ? '#059669'
                         : '#9ca3af',
                  }}>
                    {entry.speaker}
                  </span>
                  <span style={{ fontSize: 13, color: 'var(--color-text)', lineHeight: 1.45, wordBreak: 'break-word' }}>
                    {entry.text}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Live mic diagnostics (Push-to-Talk / legacy auto mode) ──────────── */}
      {isActive && chunkLog.length > 0 && micMode !== 'auto' && (
        <div className="card card-padded" style={{ padding: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)',
                           textTransform: 'uppercase', letterSpacing: '0.06em',
                           display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <CheckCircle2 size={13} style={{ color: 'var(--color-success)' }} />
              Mic active — last {chunkLog.length} chunk{chunkLog.length === 1 ? '' : 's'}
            </span>
            <span style={{ fontSize: 11, color: 'var(--color-text-faint)' }}>
              new chunk every 3s
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {chunkLog.map(entry => {
              const styles: Record<ChunkLogEntry['outcome'], { bg: string; color: string; label: string; emoji: string }> = {
                'agent-filtered':       { bg: '#eff6ff', color: '#1d4ed8', label: 'You spoke — filtered (correct)', emoji: '🎤' },
                'customer-processing':  { bg: '#ecfdf5', color: '#059669', label: 'Customer voice — sent to LLM',   emoji: '✅' },
                'customer-empty':       { bg: '#fff7ed', color: '#c2410c', label: 'Sound captured but no clear speech', emoji: '🔇' },
                'too-short':            { bg: '#fafafa', color: '#71717a', label: 'Chunk too quiet / too short',     emoji: '·'  },
                'transcribers-failed':  { bg: '#fef2f2', color: '#b91c1c', label: 'Transcription failed (Whisper STT error)', emoji: '⚠️' },
                'verify-error':         { bg: '#fef2f2', color: '#b91c1c', label: 'Voiceprint check failed',         emoji: '⚠️' },
              }
              const s = styles[entry.outcome]
              const t = entry.timestamp
              const hh = t.getHours().toString().padStart(2, '0')
              const mm = t.getMinutes().toString().padStart(2, '0')
              const ss = t.getSeconds().toString().padStart(2, '0')
              return (
                <div key={entry.id} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '6px 10px', borderRadius: 6,
                  background: s.bg, color: s.color, fontSize: 12,
                }}>
                  <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11, opacity: 0.7 }}>
                    {hh}:{mm}:{ss}
                  </span>
                  <span style={{ width: 14, textAlign: 'center' }}>{s.emoji}</span>
                  <span style={{ flex: 1, fontWeight: 500 }}>{s.label}</span>
                  {entry.similarity !== null && (
                    <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 11, opacity: 0.75 }}>
                      sim {entry.similarity.toFixed(2)}
                    </span>
                  )}
                  <span style={{ fontSize: 11, opacity: 0.6 }}>
                    {(entry.bytes / 1024).toFixed(0)}KB
                  </span>
                  {entry.transcript && (
                    <span style={{
                      fontSize: 11, fontStyle: 'italic', opacity: 0.8,
                      maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }} title={entry.transcript}>
                      "{entry.transcript}"
                    </span>
                  )}
                </div>
              )
            })}
          </div>
          {agentSkipCount > 0 && chunkLog.every(c => c.outcome === 'agent-filtered') && (
            <div style={{
              marginTop: 10, padding: 10, borderRadius: 6,
              background: '#fffbeb', border: '1px solid #fde68a',
              fontSize: 12, color: '#78350f',
            }}>
              <strong>Testing alone?</strong> Your voice is being correctly identified
              and filtered. To see customer transcription, have someone else speak —
              or switch to <strong>Push to Talk</strong> mode where every press
              treats audio as customer input.
            </div>
          )}
        </div>
      )}

      {/* Session config pill row */}
      <div className="session-config">
        <div className="session-config-row">
          <span className="session-config-row-label">Customer</span>
          <div className="pill-row">
            {CUSTOMER_TYPES.map(t => (
              <button key={t.value} type="button"
                onClick={() => setSessionContext(p => ({ ...p, customerType: t.value }))}
                className={`pill${sessionContext.customerType === t.value ? ' active' : ''}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="session-config-row">
          <span className="session-config-row-label">Loan Type</span>
          <div className="pill-row">
            {LOAN_TYPES.map(t => (
              <button key={t.value} type="button"
                onClick={() => setSessionContext(p => ({ ...p, loanType: t.value }))}
                className={`pill${sessionContext.loanType === t.value ? ' active' : ''}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div className="live-status-bar">
        <span className={`status-dot ${status}`} />
        <span className="status-text">
          {micMode === 'push' ? (
            <>
              {status === 'idle' && 'Hold the button when the client speaks.'}
              {status === 'listening' && 'Listening to client…'}
              {status === 'transcribing' && 'Transcribing audio…'}
              {status === 'answering' && 'Generating answer…'}
            </>
          ) : (
            <>
              {status === 'idle' && 'Ready — tap the microphone to start'}
              {status === 'listening' && (
                liveStatus === 'speaking'
                  ? 'Speaking reply — mic active (full-duplex)'
                  : liveStatus === 'wait'
                    ? 'Listening (agent spoke — no suggestion)'
                    : liveStatus === 'thinking'
                      ? 'Processing…'
                      : 'Listening — both speakers transcribed'
              )}
              {status === 'transcribing' && 'Transcribing audio…'}
              {status === 'answering' && 'Generating answer…'}
            </>
          )}
        </span>
        {(isActive || pttPressed || pttProcessing) && (
          <span style={{ marginLeft: 'auto' }} className={`tag ${detectedLanguage}`}>
            {detectedLanguage === 'marathi' ? 'Marathi'
              : detectedLanguage === 'hinglish' ? 'Hinglish'
              : detectedLanguage === 'hindi' ? 'Hindi'
              : 'English'}
          </span>
        )}
      </div>

      {/* Mic hero — Push-to-Talk OR continuous, depending on mode */}
      {micMode === 'push' ? (
        <div className="ptt-shell">
          <button
            className={`ptt-button${pttPressed ? ' pressed' : ''}${pttProcessing ? ' processing' : ''}`}
            onClick={pttToggle}
            onTouchStart={(e) => { e.preventDefault(); pttStart() }}
            onTouchEnd={(e) => { e.preventDefault(); pttStop() }}
            disabled={pttProcessing}
          >
            <span className="ptt-icon">
              {pttPressed ? <MicOff size={22} /> : pttProcessing ? <Sparkles size={22} /> : <Hand size={22} />}
            </span>
            <span>
              {pttProcessing ? 'Processing…'
                : pttPressed && pttSpeechDetected ? 'Listening…'
                : pttPressed ? 'Waiting…'
                : 'Talk'}
            </span>
            {pttPressed && !pttProcessing && (
              <span style={{ fontSize: 10, fontWeight: 600, opacity: 0.75, textTransform: 'none', letterSpacing: 0 }}>
                {pttSpeechDetected ? 'Auto-stops on silence' : 'Speak now'}
              </span>
            )}
            {!pttPressed && !pttProcessing && (
              <span style={{ fontSize: 10, fontWeight: 600, opacity: 0.75, textTransform: 'none', letterSpacing: 0 }}>
                Client speaking
              </span>
            )}
          </button>
          <div className="ptt-hint">
            Click to start — recording stops automatically when the client pauses. Click again to stop manually.
          </div>
        </div>
      ) : (
        <div className="live-mic-hero">
          <div className="mic-button-wrap">
            {isActive && (
              <>
                <span className="mic-pulse-ring" />
                <span className="mic-pulse-ring r2" />
                <span className="mic-pulse-ring r3" />
              </>
            )}
            <button
              onClick={toggleSession}
              className={`mic-button${isActive ? ' active' : ''}`}
              aria-label={isActive ? 'Stop listening' : 'Start listening'}
            >
              {isActive ? <MicOff size={34} /> : <Mic size={34} />}
              <span className="mic-button-label">{isActive ? 'Tap to stop' : 'Tap to start'}</span>
            </button>
          </div>

          <div className="live-mic-meta">
            {waveformActive && (
              <div className="waveform" ref={waveformRef}>
                {[0.08, 0.08, 0.08, 0.08, 0.08, 0.08, 0.08].map((h, i) => (
                  <div key={i} className="bar"
                    style={{ height: `${h * 100}%` }} />
                ))}
              </div>
            )}
            {speakerRegistered && isActive && rmSpeakerId === null && (
              <div style={{ fontSize: 12, color: 'var(--color-success)', fontWeight: 600,
                            display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <CheckCircle2 size={13} /> Salesperson voice registered
              </div>
            )}
          </div>
        </div>
      )}

      {/* Test mode toggle */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <span style={{ fontSize: 12, color: 'var(--color-text-faint)' }}>
          No microphone? Simulate questions for testing.
        </span>
        <button
          onClick={() => setTestMode(t => !t)}
          className={`btn ${testMode ? 'btn-primary' : 'btn-secondary'}`}
          style={{ padding: '7px 14px', fontSize: 12 }}>
          <FlaskConical size={13} /> Test Mode {testMode ? 'ON' : 'OFF'}
        </button>
      </div>

      {/* Test mode input */}
      {testMode && (
        <div className="test-block">
          <div className="test-block-title">Simulate Customer Question</div>
          <div className="test-block-hint">
            Type what a customer would say — runs the full pipeline (clean → RAG → answer)
          </div>
          <div className="test-input-row">
            <input
              type="text" className="form-input"
              value={testInput}
              onChange={e => setTestInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && simulateQuestion(testInput)}
              placeholder='e.g. "What is the interest rate?" or "EMI kitni hogi?"'
              disabled={testLoading}
            />
            <button
              onClick={() => simulateQuestion(testInput)}
              disabled={testLoading || !testInput.trim()}
              className="btn btn-primary">
              {testLoading ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
                           : <><Send size={13} /> Ask</>}
            </button>
          </div>
          <div className="test-prompts">
            {QUICK_TEST_PROMPTS.map(q => (
              <button key={q} className="test-prompt-chip"
                      onClick={() => simulateQuestion(q)} disabled={testLoading}>
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Live answer / suggestion card.
          In two-speaker mode (auto): show whenever there is coaching text
          (suggestion_ready, speaking, or after TTS when text is still set).
          Never show the card on WAIT (currentAnswer is cleared on wait). */}
      {(currentAnswer || status === 'answering') && (micMode !== 'auto' || !!currentAnswer || liveStatus === 'suggestion_ready' || liveStatus === 'speaking' || status === 'answering') && (
        <div ref={answerCardRef} className={`live-answer fade-in${currentAnswer.toLowerCase().startsWith('could not') ? ' error' : ''}`}>
          {cleanedQuestion && (
            <div className="live-answer-question">
              "{cleanedQuestion}"
            </div>
          )}
          <div className="live-answer-meta">
            <span className={`tag ${currentQuestionType}`}>
              {currentQuestionType === 'objection' ? 'Objection' : 'Factual'}
            </span>
            <span className={`tag ${detectedLanguage}`}>
              {detectedLanguage === 'marathi' ? 'Marathi'
                : detectedLanguage === 'hinglish' ? 'Hinglish'
                : detectedLanguage === 'hindi' ? 'Hindi'
                : 'English'}
            </span>
            {/* Timing badge: shown as soon as the first-stage answer completes */}
            {currentElapsedMs !== null && (
              <span
                className="tag"
                title="Time from question submit to first answer"
                style={{
                  background: currentElapsedMs <= 1500 ? '#dcfce7'
                            : currentElapsedMs <= 3500 ? '#fef9c3' : '#fee2e2',
                  color:      currentElapsedMs <= 1500 ? '#166534'
                            : currentElapsedMs <= 3500 ? '#854d0e' : '#991b1b',
                  display: 'inline-flex', alignItems: 'center', gap: 4
                }}>
                <Zap size={10} />{(currentElapsedMs / 1000).toFixed(currentElapsedMs < 1000 ? 2 : 1)}s
              </span>
            )}
            {/* Engine badge: which model produced the displayed answer */}
            {currentEngine === 'demo-cache' && (
              <span className="tag" title="Pre-cached answer — instant"
                    style={{ background: '#e0f2fe', color: '#075985',
                             display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <Zap size={10} />Cached
              </span>
            )}
            {currentRefined && (
              <span className="tag" title="Upgraded by a larger model in the background"
                    style={{ background: '#f5f3ff', color: '#6d28d9',
                             display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <Award size={10} />Refined
              </span>
            )}
            {refining && !currentRefined && (
              <span className="tag" title="A larger model is improving this answer in the background"
                    style={{ background: '#f5f3ff', color: '#6d28d9', opacity: 0.75,
                             display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <span className="spinner" style={{ width: 8, height: 8, borderWidth: 1.5 }} />
                Refining…
              </span>
            )}
            {/* TTS state indicator — only shown in always-on mode; clickable to toggle */}
            {micMode === 'auto' && liveStatus === 'speaking' && ttsEnabled && (
              <button type="button"
                onClick={() => {
                  setTtsEnabled(false)
                  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
                  isSpeakingRef.current = false
                  if (isActiveRef.current) setLiveStatus('suggestion_ready')
                }}
                title="Speaking aloud — click to mute"
                style={{
                  background: '#f5f3ff', color: '#6d28d9', marginLeft: 'auto', cursor: 'pointer',
                  display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 700,
                  border: '1px solid #c4b5fd', borderRadius: 999, padding: '2px 9px', fontSize: 11,
                }}>
                <span style={{
                  display: 'inline-block', width: 8, height: 8, borderRadius: 999,
                  background: '#7c3aed', animation: 'pulse 1s ease-in-out infinite',
                }} />
                🔊 Speaking — tap to mute
              </button>
            )}
            {micMode === 'auto' && !ttsEnabled && !!currentAnswer && (
              <button type="button"
                onClick={() => setTtsEnabled(true)}
                title="Voice muted — click to enable voice reply"
                style={{
                  background: '#f1f5f9', color: '#64748b', marginLeft: 'auto', cursor: 'pointer',
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  border: '1px solid #cbd5e1', borderRadius: 999, padding: '2px 9px', fontSize: 11,
                }}>
                🔇 Muted — tap to unmute
              </button>
            )}
          </div>
          <div className="live-answer-text">
            {currentAnswer || ' '}
            {status === 'answering' && <span className="cursor-blink" />}
          </div>

          {currentSources.length > 0 && (
            <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {currentSources.map((s, i) => (
                <span key={i} className="chat-source-pill"><FileCheck size={10} /> {s}</span>
              ))}
            </div>
          )}

          <button
            onClick={() => setShowTranscript(!showTranscript)}
            className="btn btn-ghost"
            style={{ marginTop: 10, fontSize: 12, padding: '4px 8px' }}>
            {showTranscript ? 'Hide' : 'Show'} raw transcript
          </button>
          {showTranscript && rawTranscript && (
            <div style={{ marginTop: 8, padding: 12, background: '#f8fafc',
                          border: '1px solid var(--color-border)', borderRadius: 6,
                          fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12,
                          color: 'var(--color-text-muted)', wordBreak: 'break-word' }}>
              "{rawTranscript}"
            </div>
          )}
        </div>
      )}

      {/* History */}
      {answerHistory.length > 0 && (() => {
        // Session-level latency stat — average of all first-stage answer times.
        const timed = answerHistory.filter(e => e.elapsedMs > 0)
        const avgMs = timed.length
          ? Math.round(timed.reduce((s, e) => s + e.elapsedMs, 0) / timed.length)
          : 0
        const cachedCount = answerHistory.filter(e => e.engine === 'demo-cache').length
        const refinedCount = answerHistory.filter(e => e.refined).length
        return (
        <div className="card card-padded">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        marginBottom: 12, gap: 12, flexWrap: 'wrap' }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text)',
                         margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Clock size={15} style={{ color: 'var(--color-text-muted)' }} />
              Recent Questions
              <span className="chat-source-pill" style={{ marginLeft: 4 }}>{answerHistory.length}</span>
              {avgMs > 0 && (
                <span className="tag" title="Average time to first answer this session"
                      style={{ marginLeft: 4, background: '#f1f5f9', color: '#334155',
                               display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <Zap size={10} />Avg {(avgMs / 1000).toFixed(1)}s
                </span>
              )}
              {cachedCount > 0 && (
                <span className="tag" title="Answers served instantly from the demo cache"
                      style={{ background: '#e0f2fe', color: '#075985' }}>
                  {cachedCount} cached
                </span>
              )}
              {refinedCount > 0 && (
                <span className="tag" title="Answers upgraded by the larger background model"
                      style={{ background: '#f5f3ff', color: '#6d28d9' }}>
                  {refinedCount} refined
                </span>
              )}
            </h3>
            <button
              onClick={endSession}
              disabled={sessionSummary?.loading}
              className="btn btn-primary"
              style={{ padding: '6px 12px', fontSize: 12 }}
              title="Generate a CRM debrief: objections, competitor mentions, next best action">
              {sessionSummary?.loading
                ? <><span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} /> Generating…</>
                : <><FileText size={13} /> End Session — CRM Debrief</>}
            </button>
          </div>
          <div className="live-history">
            {answerHistory.map(entry => {
              const expanded = expandedHistoryId === entry.id
              return (
                <div key={entry.id} className="live-history-row"
                     onClick={() => setExpandedHistoryId(expanded ? null : entry.id)}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, justifyContent: 'space-between' }}>
                      <div className="live-history-q" style={!expanded ? { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } : {}}>
                        {/[ऀ-ॿ]/.test(entry.rawTranscript || '') ? entry.rawTranscript : entry.question}
                      </div>
                      <div className="live-history-meta">
                        <span className="live-history-time">{getTimeAgo(entry.timestamp)}</span>
                        {entry.elapsedMs > 0 && (
                          <span className="tag" title="Time to first answer"
                                style={{ background: '#f1f5f9', color: '#334155',
                                         display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                            <Zap size={9} />{(entry.elapsedMs / 1000).toFixed(1)}s
                          </span>
                        )}
                        {entry.engine === 'demo-cache' && (
                          <span className="tag" title="From demo cache"
                                style={{ background: '#e0f2fe', color: '#075985' }}>Cached</span>
                        )}
                        {entry.refined && (
                          <span className="tag" title="Refined by larger model"
                                style={{ background: '#f5f3ff', color: '#6d28d9' }}>Refined</span>
                        )}
                        <span className={`tag ${entry.language}`}>{entry.language === 'marathi' ? 'MR' : entry.language === 'hinglish' ? 'HG' : entry.language === 'hindi' ? 'HI' : 'EN'}</span>
                        {entry.questionType === 'objection' && <span className="tag objection">Objection</span>}
                        <span className="tag speaker">{entry.speakerRole}</span>
                        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </div>
                    </div>
                    {expanded && (
                      <div className="live-history-body fade-in">
                        {entry.answer}
                        {entry.sources.length > 0 && (
                          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                            {entry.sources.map((s, i) => (
                              <span key={i} className="chat-source-pill"><FileCheck size={10} /> {s}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
        )
      })()}

      {/* CRM Session Summary modal */}
      {sessionSummary && (
        <div
          onClick={(e) => { if (e.target === e.currentTarget) setSessionSummary(null) }}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.55)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000, padding: 16,
          }}>
          <div style={{
            background: '#fff', borderRadius: 12, maxWidth: 720, width: '100%',
            maxHeight: '85vh', display: 'flex', flexDirection: 'column',
            boxShadow: '0 25px 50px -12px rgba(0,0,0,0.25)', overflow: 'hidden',
          }}>
            <div style={{
              padding: '16px 20px', borderBottom: '1px solid var(--color-border)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <FileText size={18} style={{ color: 'var(--color-red)' }} />
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text)' }}>
                    CRM Session Debrief
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    {answerHistory.length} customer question{answerHistory.length === 1 ? '' : 's'} reviewed
                    {sessionSummary.engine && ` · via ${sessionSummary.engine}`}
                  </div>
                </div>
              </div>
              <button onClick={() => setSessionSummary(null)}
                      className="btn btn-ghost btn-icon-only" aria-label="Close">
                <X size={16} />
              </button>
            </div>

            <div style={{ padding: 20, overflowY: 'auto', flex: 1 }}>
              {sessionSummary.loading && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '24px 0' }}>
                  <span className="spinner" style={{ width: 20, height: 20, borderWidth: 2.5 }} />
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-text)' }}>
                      Analysing the conversation…
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                      Identifying objections, competitor mentions, and next-best-action.
                    </div>
                  </div>
                </div>
              )}

              {sessionSummary.error && (
                <div className="alert alert-error">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <AlertTriangle size={15} /> {sessionSummary.error}
                  </span>
                </div>
              )}

              {sessionSummary.text && (
                <div style={{
                  fontSize: 14, lineHeight: 1.65, color: 'var(--color-text)',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>
                  {sessionSummary.text}
                </div>
              )}
            </div>

            <div style={{
              padding: '12px 20px', borderTop: '1px solid var(--color-border)',
              display: 'flex', justifyContent: 'flex-end', gap: 8,
            }}>
              {sessionSummary.text && (
                <button onClick={copySummary} className="btn btn-secondary"
                        style={{ padding: '6px 12px', fontSize: 12 }}>
                  Copy
                </button>
              )}
              <button onClick={() => setSessionSummary(null)} className="btn btn-primary"
                      style={{ padding: '6px 14px', fontSize: 12 }}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Footer: change customer */}
      {!isActive && !pttPressed && !pttProcessing && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 12, paddingTop: 8 }}>
          <button
            onClick={() => {
              setSessionStarted(false)
              setSessionContext({ customerType: '', loanType: 'personal-loan', customerStatus: 'new', preApproved: 'unknown' })
              setAnswerHistory([])
              setCurrentAnswer('')
              setCleanedQuestion('')
            }}
            className="btn btn-ghost">
            <ArrowLeft size={13} /> Change Customer
          </button>
          <button
            onClick={() => {
              setSessionStarted(false)
              setMicMode(null)
              resetEnrollment()
              setSingleSpeakerStreak(0)
              setAnswerHistory([])
              setCurrentAnswer('')
              setCleanedQuestion('')
            }}
            className="btn btn-ghost">
            <RotateCcw size={13} /> Change Mic Mode
          </button>
        </div>
      )}
    </div>
  )
}

