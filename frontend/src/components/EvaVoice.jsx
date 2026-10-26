/*
 * EvaVoice — the Eva 2.0 hands-free voice assistant (inside AI Mentor).
 *
 * Flow (tap-to-talk, chatbot-style with a confirm tick):
 *   tap mic → listen (live interim transcript) → tap ✓ (or auto-stop on silence)
 *          → /api/voice/stt (local Whisper)
 *          → /api/chat/stream  (voice:true → Eva persona, short plain answer, SAME RAG)
 *          → sentence-chunked /api/tts/eva  (edge-tts, Indian accent, faster rate)
 *          → back to idle
 *
 * Eva character reacts to live audio: antenna receives while listening, gears
 * turn while thinking, mouth moves to the TTS amplitude while speaking.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { Mic, Check, X, Square } from 'lucide-react';
import './EvaVoice.css';

const LANGUAGES = ['English', 'Hinglish'];
// Pin Whisper to the chosen language — never 'auto'. Auto-detect on short/accented
// clips sometimes guesses Urdu (acoustically close to Hindi); forcing en/hi stops that.
const STT_HINT = { English: 'en', Hinglish: 'hi' };

/* voice-activity thresholds (RMS of low-frequency energy, 0..1) */
const SPEECH_ON = 0.030;
const SPEECH_OFF = 0.020;
const SILENCE_MS = 2200;     // trailing silence auto-confirms (backup to the ✓ button)
const MIN_SPEECH_MS = 350;
const MAX_LISTEN_MS = 20000;
const NO_SPEECH_MS = 12000;
const INTERIM_MS = 1600;     // how often we refresh the live transcript

/* antenna reception arcs */
function arcTop(cx, cy, r) {
  const a0 = (208 * Math.PI) / 180, a1 = (332 * Math.PI) / 180;
  const x0 = cx + Math.cos(a0) * r, y0 = cy + Math.sin(a0) * r;
  const x1 = cx + Math.cos(a1) * r, y1 = cy + Math.sin(a1) * r;
  return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}
const RECV_R = [30, 22, 14];
function gearTeeth(cx, cy, teeth, rBody, tLen, tW) {
  return Array.from({ length: teeth }, (_, i) => {
    const a = (i * 360) / teeth;
    return <rect key={i} x={cx - tW / 2} y={cy - rBody - tLen} width={tW} height={tLen + 2}
      rx="1" fill="#8ec5f0" transform={`rotate(${a} ${cx} ${cy})`} />;
  });
}

/* ────────────────────────── Eva character ────────────────────────── */
function EvaCharacter({ stateRef, sampleLevel }) {
  const svgRef = useRef(null);
  useEffect(() => {
    let raf, t = 0, mLift = 6, mOpen = 3, ehCur = 28, eyY = 0, eyX = 0, blink = 1, nextBlink = 1.2;
    const svg = svgRef.current;
    const $ = (id) => svg.querySelector('#' + id);
    const rig = $('eva_rig'), eyeL = $('eva_eyeL'), eyeR = $('eva_eyeR'), eyes = $('eva_eyes'),
      mouth = $('eva_mouth'), recv = $('eva_recv'), think = $('eva_think'),
      gear1 = $('eva_gear1'), gear2 = $('eva_gear2'), tip = $('eva_tip'),
      finL = $('eva_finL'), finR = $('eva_finR');
    const recvArcs = recv ? [...recv.children] : [];
    const conns = think ? [...think.querySelectorAll('.eva-conn')] : [];
    const lerp = (a, b, f) => a + (b - a) * f;
    const expr = (st) => st === 'listening' ? { lift: 5, open: 2, eyeH: 32, up: 0 }
      : st === 'thinking' ? { lift: -2, open: 2, eyeH: 18, up: -9 }
        : st === 'speaking' ? { lift: 5, open: 6, eyeH: 17, up: 0 }
          : { lift: 7, open: 3, eyeH: 28, up: 0 };
    const setMouth = (lift, openH, xoff) => {
      const cx = 150 + xoff, cy = 166, w = openH > 10 ? 36 : 32;
      const Lx = cx - w / 2, Rx = cx + w / 2, corner = cy - lift;
      mouth.setAttribute('d', `M ${Lx} ${corner} Q ${cx} ${cy - lift * 0.35 - openH * 0.12} ${Rx} ${corner} Q ${cx} ${cy + openH} ${Lx} ${corner} Z`);
    };
    const frame = () => {
      t += 0.016;
      const state = stateRef.current;
      let lv = 0; try { lv = sampleLevel() || 0; } catch { lv = 0; }
      const speaking = state === 'speaking', listening = state === 'listening', thinking = state === 'thinking';
      const e = expr(state);
      const bob = Math.sin(t * 1.6) * 4 + (speaking ? Math.sin(t * 7) * 1.5 : 0);
      rig.setAttribute('transform', `translate(0 ${bob}) rotate(${Math.sin(t * 0.9) * 1.2 + (thinking ? 3 : 0)} 150 150)`);
      finL.setAttribute('transform', `rotate(-2 76 150) translate(0 ${listening ? -lv * 3 : 0})`);
      finR.setAttribute('transform', `rotate(2 224 150) translate(0 ${listening ? -lv * 3 : 0})`);
      tip.setAttribute('r', 6 + (listening ? lv * 3 : state !== 'idle' ? lv * 1.5 : 0.6 * Math.sin(t * 2) + 0.6));
      recv.setAttribute('opacity', listening ? 1 : 0);
      recvArcs.forEach((p, i) => {
        if (!listening) { p.setAttribute('opacity', '0'); return; }
        p.setAttribute('opacity', Math.min(1, (0.12 + 0.8 * Math.abs(Math.sin(t * 3 - i * 0.95))) * (0.45 + lv)).toFixed(2));
      });
      mLift = lerp(mLift, e.lift, 0.15);
      mOpen = lerp(mOpen, speaking ? 6 + lv * 24 : e.open + (state === 'idle' ? 0.6 * Math.sin(t * 2) : 0), speaking ? 0.4 : 0.15);
      setMouth(mLift, mOpen, thinking ? 4 : 0);
      mouth.setAttribute('opacity', thinking ? 0 : 1);
      nextBlink -= 0.016;
      if (nextBlink <= 0) { blink = 0; if (nextBlink < -0.12) { blink = 1; nextBlink = 1.4 + Math.random() * 3; } }
      ehCur = lerp(ehCur, e.eyeH, 0.15); eyY = lerp(eyY, e.up, 0.12); eyX = lerp(eyX, thinking ? Math.sin(t * 1.3) * 4 : 0, 0.12);
      const eh = ehCur * Math.max(0.08, blink), cyEye = 132;
      [eyeL, eyeR].forEach((o) => { o.setAttribute('height', eh.toFixed(1)); o.setAttribute('y', (cyEye - eh / 2 + eyY).toFixed(1)); });
      eyes.setAttribute('transform', `translate(${eyX.toFixed(1)} 0)`);
      think.setAttribute('opacity', thinking ? 1 : 0);
      if (thinking) {
        gear1.setAttribute('transform', `rotate(${t * 70} 234 50)`);
        gear2.setAttribute('transform', `rotate(${-t * 90} 248 62)`);
        conns.forEach((c, i) => c.setAttribute('opacity', (0.3 + 0.7 * Math.abs(Math.sin(t * 3 - i * 0.8))).toFixed(2)));
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [stateRef, sampleLevel]);

  return (
    <svg ref={svgRef} className="eva-svg" viewBox="0 0 300 320" aria-label="Eva">
      <defs>
        <linearGradient id="eva_body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" /><stop offset="1" stopColor="#e6eefb" /></linearGradient>
        <linearGradient id="eva_vis" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#132844" /><stop offset="1" stopColor="#0a1628" /></linearGradient>
        <radialGradient id="eva_eyeg" cx=".5" cy=".4" r=".7"><stop offset="0" stopColor="#8fd0ff" /><stop offset=".5" stopColor="#3b82f6" /><stop offset="1" stopColor="#1e40af" /></radialGradient>
        <filter id="eva_glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <g id="eva_think" opacity="0">
        <circle className="eva-conn" cx="196" cy="92" r="3" fill="#9fd4ff" />
        <circle className="eva-conn" cx="212" cy="76" r="4" fill="#9fd4ff" />
        <circle cx="240" cy="54" r="24" fill="#10233c" />
        <circle cx="240" cy="54" r="24" fill="none" stroke="#2c4a72" strokeWidth="1.4" />
        <g id="eva_gear1"><circle cx="234" cy="50" r="7" fill="#8ec5f0" />{gearTeeth(234, 50, 8, 7, 3.2, 3)}<circle cx="234" cy="50" r="2.9" fill="#10233c" /></g>
        <g id="eva_gear2"><circle cx="248" cy="62" r="5.5" fill="#5a9fd6" />{gearTeeth(248, 62, 7, 5.5, 2.8, 2.6)}<circle cx="248" cy="62" r="2.3" fill="#10233c" /></g>
      </g>
      <g id="eva_rig">
        <g id="eva_recv" opacity="0">{RECV_R.map((r, i) => <path key={i} d={arcTop(150, 36, r)} fill="none" stroke="#0ea5c4" strokeWidth="3" strokeLinecap="round" opacity="0" />)}</g>
        <g id="eva_finL"><path d="M76 108 q-40 6 -34 46 q4 30 34 30 q-14 -40 0 -76 z" fill="#E31837" /><path d="M76 116 q-28 6 -24 38 q3 20 24 22 q-10 -30 0 -60 z" fill="#ff3d5f" opacity="0.55" /></g>
        <g id="eva_finR"><path d="M224 108 q40 6 34 46 q-4 30 -34 30 q14 -40 0 -76 z" fill="#E31837" /><path d="M224 116 q28 6 24 38 q-3 20 -24 22 q10 -30 0 -60 z" fill="#ff3d5f" opacity="0.55" /></g>
        <line x1="150" y1="66" x2="150" y2="42" stroke="#c3d0e6" strokeWidth="4" strokeLinecap="round" />
        <circle id="eva_tip" cx="150" cy="38" r="6" fill="#E31837" />
        <rect x="72" y="66" width="156" height="150" rx="46" fill="url(#eva_body)" stroke="#c7d4ea" strokeWidth="1.5" />
        <rect x="92" y="92" width="116" height="96" rx="30" fill="url(#eva_vis)" />
        <rect x="92" y="92" width="116" height="96" rx="30" fill="none" stroke="#20375a" strokeWidth="1.5" />
        <g id="eva_eyes" filter="url(#eva_glow)">
          <rect id="eva_eyeL" x="118" y="118" width="22" height="28" rx="10" fill="url(#eva_eyeg)" />
          <rect id="eva_eyeR" x="160" y="118" width="22" height="28" rx="10" fill="url(#eva_eyeg)" />
        </g>
        <path id="eva_mouth" fill="#38bdf8" filter="url(#eva_glow)" />
      </g>
    </svg>
  );
}

/* ────────────────────────── Voice loop ────────────────────────── */
export default function EvaVoice({ compact = false }) {
  const [phase, setPhase] = useState('idle');   // idle|listening|thinking|speaking
  const [language, setLanguage] = useState('English');
  const [interim, setInterim] = useState('');
  const [userText, setUserText] = useState('');
  const [evaText, setEvaText] = useState('');
  const [hint, setHint] = useState('');

  const phaseRef = useRef('idle');
  const langRef = useRef('English');
  const sessionRef = useRef(null);

  const acRef = useRef(null);
  const micStreamRef = useRef(null);
  const micAnalyserRef = useRef(null);
  const micDataRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const vadRef = useRef(null);
  const interimTimerRef = useRef(null);
  const interimBusyRef = useRef(false);
  const sendOnStopRef = useRef(true);

  const ttsAudioRef = useRef(null);
  const ttsAnalyserRef = useRef(null);
  const ttsDataRef = useRef(null);
  const mediaConnRef = useRef(false);

  const queueRef = useRef([]);
  const pumpingRef = useRef(false);
  const streamDoneRef = useRef(false);
  const ttsBufRef = useRef('');
  const abortRef = useRef(false);

  const setPhaseBoth = useCallback((p) => { phaseRef.current = p; setPhase(p); }, []);
  useEffect(() => { langRef.current = language; }, [language]);

  const sampleLevel = useCallback(() => {
    const ph = phaseRef.current;
    let an = null, data = null;
    if (ph === 'listening') { an = micAnalyserRef.current; data = micDataRef.current; }
    else if (ph === 'speaking') { an = ttsAnalyserRef.current; data = ttsDataRef.current; }
    if (!an || !data) return 0;
    an.getByteFrequencyData(data);
    let s = 0; for (let i = 0; i < 20; i++) s += (data[i] || 0) / 255;
    return Math.min(1, s / 10);
  }, []);

  const ensureAudioCtx = () => {
    if (!acRef.current) acRef.current = new (window.AudioContext || window.webkitAudioContext)();
    if (acRef.current.state === 'suspended') acRef.current.resume();
    return acRef.current;
  };

  /* ── TTS ── */
  const fetchTTS = async (text) => {
    try {
      const res = await fetch('/api/tts/eva', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, lang: langRef.current }),
      });
      if (!res.ok) return null;
      return URL.createObjectURL(await res.blob());
    } catch { return null; }
  };
  const playUrl = (url) => new Promise((resolve) => {
    const audio = ttsAudioRef.current;
    if (!audio || abortRef.current) { resolve(); return; }
    if (!mediaConnRef.current) {
      try {
        const ac = ensureAudioCtx();
        const src = ac.createMediaElementSource(audio);
        const an = ac.createAnalyser(); an.fftSize = 256;
        src.connect(an); an.connect(ac.destination);
        ttsAnalyserRef.current = an; ttsDataRef.current = new Uint8Array(an.frequencyBinCount);
        mediaConnRef.current = true;
      } catch { }
    }
    audio.onended = () => resolve();
    audio.onerror = () => resolve();
    audio.src = url;
    audio.play().catch(() => resolve());
  });
  const pump = async () => {
    if (pumpingRef.current) return;
    pumpingRef.current = true;
    if (phaseRef.current !== 'speaking') setPhaseBoth('speaking');
    while (queueRef.current.length && !abortRef.current) {
      const item = queueRef.current.shift();
      const url = await item.promise.catch(() => null);
      if (url && !abortRef.current) { await playUrl(url); URL.revokeObjectURL(url); }
    }
    pumpingRef.current = false;
    if (streamDoneRef.current && !queueRef.current.length) finishTurn();
  };
  const enqueueTTS = (text) => {
    const clean = text.replace(/[*_#`]/g, '').replace(/\s+/g, ' ').trim();
    if (!clean) return;
    queueRef.current.push({ text: clean, promise: fetchTTS(clean) });
    pump();
  };
  const drainSentences = (flush = false) => {
    let buf = ttsBufRef.current;
    const re = /[^.!?।॥\n]+[.!?।॥\n]+/g;
    let m, lastEnd = 0;
    while ((m = re.exec(buf)) !== null) { const s = m[0].trim(); if (s.length >= 2) enqueueTTS(s); lastEnd = re.lastIndex; }
    buf = buf.slice(lastEnd);
    if (flush && buf.trim()) { enqueueTTS(buf.trim()); buf = ''; }
    ttsBufRef.current = buf;
  };

  /* ── RAG turn (voice-tuned) ── */
  const ask = async (question) => {
    setPhaseBoth('thinking'); setEvaText(''); setHint('');
    streamDoneRef.current = false; ttsBufRef.current = ''; queueRef.current = []; abortRef.current = false;
    let answer = '';
    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, session_id: sessionRef.current || null, language: langRef.current, voice: true }),
      });
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let sseBuf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (abortRef.current) { reader.cancel(); break; }
        sseBuf += dec.decode(value, { stream: true });
        const parts = sseBuf.split('\n\n'); sseBuf = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          let ev; try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (ev.type === 'token') { answer += ev.text; setEvaText(answer); ttsBufRef.current += ev.text; drainSentences(false); }
          else if (ev.type === 'done') { if (ev.session_id) sessionRef.current = ev.session_id; if (ev.answer) { answer = ev.answer; setEvaText(answer); } }
          else if (ev.type === 'error') setHint(ev.detail || 'Something went wrong.');
        }
      }
    } catch { setHint('Connection problem — please try again.'); }
    drainSentences(true);
    streamDoneRef.current = true;
    if (!queueRef.current.length && !pumpingRef.current) finishTurn(); else pump();
  };
  const finishTurn = () => { setPhaseBoth('idle'); setHint('Tap the mic to ask another question.'); };

  /* ── STT ── */
  const buildBlob = () => new Blob(chunksRef.current, { type: (recorderRef.current && recorderRef.current.mimeType) || 'audio/webm' });
  const sttRequest = async (blob) => {
    const ext = (blob.type.split('/')[1] || 'webm').split(';')[0];
    const fd = new FormData();
    fd.append('file', blob, `eva.${ext}`);
    fd.append('language', STT_HINT[langRef.current] || 'auto');
    const res = await fetch('/api/voice/stt', { method: 'POST', body: fd });
    return res.json();
  };
  const refreshInterim = async () => {
    if (interimBusyRef.current || phaseRef.current !== 'listening' || !chunksRef.current.length) return;
    interimBusyRef.current = true;
    try { const d = await sttRequest(buildBlob()); if (phaseRef.current === 'listening' && d.transcript) setInterim(d.transcript.trim()); }
    catch { } finally { interimBusyRef.current = false; }
  };
  const finalTranscribe = async (blob) => {
    setPhaseBoth('thinking'); setInterim('');
    try {
      const d = await sttRequest(blob);
      const text = (d.transcript || '').trim();
      if (!d.success || !text) { setHint("Didn't catch that — tap the mic and try again."); setPhaseBoth('idle'); return; }
      setUserText(text); setEvaText('');
      ask(text);
    } catch { setHint('Could not transcribe — tap the mic to retry.'); setPhaseBoth('idle'); }
  };

  /* ── listening ── */
  const startListening = async () => {
    abortRef.current = false; setEvaText(''); setUserText(''); setInterim(''); setHint('');
    sendOnStopRef.current = true;
    try {
      const ac = ensureAudioCtx();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = stream;
      const src = ac.createMediaStreamSource(stream);
      const an = ac.createAnalyser(); an.fftSize = 512; an.smoothingTimeConstant = 0.8; src.connect(an);
      micAnalyserRef.current = an; micDataRef.current = new Uint8Array(an.frequencyBinCount);
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
          : MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '';
      chunksRef.current = [];
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = () => {
        clearInterval(vadRef.current); clearInterval(interimTimerRef.current);
        const blob = buildBlob();
        stream.getTracks().forEach((t) => t.stop());
        micAnalyserRef.current = null;
        if (!sendOnStopRef.current) { setPhaseBoth('idle'); setInterim(''); setHint('Cancelled. Tap the mic when ready.'); return; }
        if (blob.size < 1400) { setPhaseBoth('idle'); setInterim(''); setHint("Didn't catch that — tap the mic and try again."); return; }
        finalTranscribe(blob);
      };
      recorderRef.current = rec;
      rec.start(250);
      setPhaseBoth('listening');
      setHint('Listening… speak now, then tap ✓');

      interimTimerRef.current = setInterval(refreshInterim, INTERIM_MS);

      const t0 = performance.now();
      let spoke = false, speechStart = 0, silenceStart = 0;
      clearInterval(vadRef.current);
      vadRef.current = setInterval(() => {
        const a = micAnalyserRef.current, data = micDataRef.current;
        if (!a) { clearInterval(vadRef.current); return; }
        a.getByteFrequencyData(data);
        let s = 0; for (let i = 0; i < 20; i++) s += (data[i] || 0) / 255;
        const lv = Math.min(1, s / 10), now = performance.now();
        if (lv > SPEECH_ON) { if (!spoke) { spoke = true; speechStart = now; } silenceStart = 0; }
        else if (lv < SPEECH_OFF && spoke) { if (!silenceStart) silenceStart = now; }
        const heard = spoke && now - speechStart > MIN_SPEECH_MS;
        if (heard && silenceStart && now - silenceStart > SILENCE_MS) confirmListening();
        else if (spoke && now - speechStart > MAX_LISTEN_MS) confirmListening();
        else if (!spoke && now - t0 > NO_SPEECH_MS) cancelListening(true);
      }, 60);
    } catch {
      setHint('Microphone blocked. Allow mic access to talk to Eva.'); setPhaseBoth('idle');
    }
  };
  const confirmListening = () => {
    if (phaseRef.current !== 'listening') return;
    sendOnStopRef.current = true;
    const rec = recorderRef.current; if (rec && rec.state !== 'inactive') rec.stop();
  };
  const cancelListening = (noSpeech = false) => {
    clearInterval(vadRef.current); clearInterval(interimTimerRef.current);
    sendOnStopRef.current = false;
    const rec = recorderRef.current;
    if (rec && rec.state !== 'inactive') rec.stop();
    else { if (micStreamRef.current) micStreamRef.current.getTracks().forEach((t) => t.stop()); micAnalyserRef.current = null; setPhaseBoth('idle'); }
    if (noSpeech) setHint("Didn't hear anything — tap the mic to talk.");
  };

  /* ── speaking control ── */
  const stopSpeaking = () => {
    abortRef.current = true; queueRef.current = []; pumpingRef.current = false;
    const a = ttsAudioRef.current; if (a) { try { a.pause(); } catch { } a.src = ''; }
    setPhaseBoth('idle'); setHint('Stopped. Tap the mic to ask again.');
  };

  const tapMic = () => {
    if (!ttsAudioRef.current) ttsAudioRef.current = new Audio();
    ensureAudioCtx();
    startListening();
  };

  useEffect(() => () => {
    clearInterval(vadRef.current); clearInterval(interimTimerRef.current);
    const rec = recorderRef.current; if (rec && rec.state !== 'inactive') { rec.onstop = null; try { rec.stop(); } catch { } }
    if (micStreamRef.current) micStreamRef.current.getTracks().forEach((t) => t.stop());
    if (acRef.current) acRef.current.close().catch(() => { });
  }, []);

  const badge = { idle: 'Ready', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking' }[phase];

  return (
    <div className={`eva-voice${compact ? ' eva-voice--compact' : ''}`}>
      <div className="eva-topbar">
        <div className="eva-title">Eva <b>2.0</b><span className="eva-sub">Voice Assistant</span></div>
        <div className="eva-lang-seg">
          {LANGUAGES.map((l) => (
            <button key={l} className={`eva-lang-pill${language === l ? ' on' : ''}`}
              disabled={phase !== 'idle'} onClick={() => setLanguage(l)}>{l}</button>
          ))}
        </div>
      </div>

      <div className={`eva-stage eva-${phase}`}>
        <div className="eva-badge"><span className="eva-dot" />{badge}</div>
        <div className="eva-halo"><EvaCharacter stateRef={phaseRef} sampleLevel={sampleLevel} /></div>

        <div className="eva-captions">
          {phase === 'listening' && (
            <div className="eva-live">{interim ? interim : <span className="eva-muted">Listening…</span>}</div>
          )}
          {phase !== 'listening' && userText && <div className="eva-bubble eva-you"><span>You</span>{userText}</div>}
          {phase !== 'listening' && evaText && <div className="eva-bubble eva-said"><span>Eva</span>{evaText}</div>}
          {!userText && !evaText && phase === 'idle' && (
            <div className="eva-hint">{hint || 'Tap the mic and ask about HDFC products, rates, eligibility or documents.'}</div>
          )}
          {phase === 'idle' && (userText || evaText) && hint && <div className="eva-hint">{hint}</div>}
        </div>
      </div>

      <div className="eva-controls">
        {phase === 'idle' && (
          <button className="eva-mic-btn" onClick={tapMic} aria-label="Tap to speak">
            <Mic size={26} /><span>Tap to speak</span>
          </button>
        )}
        {phase === 'listening' && (
          <div className="eva-listen-row">
            <button className="eva-round eva-cancel" onClick={() => cancelListening(false)} aria-label="Cancel"><X size={22} /></button>
            <button className="eva-round eva-confirm" onClick={confirmListening} aria-label="Done"><Check size={30} /></button>
            <div className="eva-round-label">Tap ✓ when done</div>
          </div>
        )}
        {phase === 'thinking' && <div className="eva-working">Eva is thinking…</div>}
        {phase === 'speaking' && (
          <button className="eva-round eva-stop" onClick={stopSpeaking} aria-label="Stop"><Square size={20} /></button>
        )}
      </div>
    </div>
  );
}
