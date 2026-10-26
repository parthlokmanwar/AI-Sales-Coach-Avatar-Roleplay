// Live pitch-coverage checklist + transcript for the RolePlay call.
//
// Two parallel audio pipelines:
//
//  1. Web Speech API (primary — instant)
//     Provides real-time transcript display and fast checklist polling.
//     Works in Chrome/Edge. Falls back to Whisper-only on unsupported browsers.
//
//  2. Whisper chunks (secondary — accurate)
//     browser MediaRecorder → 5-second webm blobs → POST /api/roleplay/transcribe-chunk
//     → accumulated text stored in externalTranscriptRef (used for the final report).
//     More accurate (especially for Hindi/Hinglish), runs on GPU.
//
// Checklist polling:
//   Every 8 s, whichever transcript is longer is sent to /api/roleplay/check-params.
//   The LLM returns {opening, discovery, product_pitch, objection_handling, closing}
//   as "met" | "pending". Items transition forward-only (pending→met or pending→missed).

import { useEffect, useRef, useState, useCallback } from "react";

const CHUNK_MS   = 5_000;   // Whisper window (was 8s — shorter = faster first result)
const POLL_MS    = 8_000;   // LLM checklist poll interval

export const COACH_SECTIONS = [
  { id: "opening",             title: "Opening" },
  { id: "discovery",           title: "Discovery" },
  { id: "product_pitch",       title: "Product Pitch" },
  { id: "objection_handling",  title: "Objection Handling" },
  { id: "closing",             title: "Closing" },
];

const INITIAL_STATUS = {
  opening: "pending",
  discovery: "pending",
  product_pitch: "pending",
  objection_handling: "pending",
  closing: "pending",
};

// ─────────────────────────────────────────────────────────────────────────────

export function useLiveCoach(active, form = {}, externalTranscriptRef = null, opts = {}) {
  // opts.sessionId  — Tavus conversation_id, sent with every audio chunk so the
  //                   backend inactivity reaper knows this client is alive.
  // opts.onActivity — called whenever the USER speaks (Web Speech result or a
  //                   non-empty Whisper chunk); feeds the inactivity timer.
  const [status,     setStatus]     = useState(INITIAL_STATUS);
  const [supported,  setSupported]  = useState(true);
  const [listening,  setListening]  = useState(false);
  const [transcript, setTranscript] = useState("");

  const optsRef = useRef(opts);
  optsRef.current = opts;

  const activeRef          = useRef(false);
  const whisperRef         = useRef("");   // from Whisper chunks (accurate, used for report)
  const speechRef          = useRef("");   // from Web Speech API (instant, used for display + checklist)
  const lastSentRef        = useRef(0);
  const startRef           = useRef(0);

  const pollTimerRef       = useRef(null);
  const recorderRef        = useRef(null);
  const streamRef          = useRef(null);
  const borrowedMicRef     = useRef(false);  // true → mic belongs to the call, don't stop it
  const chunkTimerRef      = useRef(null);
  const speechRecRef       = useRef(null);

  const formRef = useRef(form);
  formRef.current = form;

  // ── Whisper chunk upload ────────────────────────────────────────────────
  const sendChunk = useCallback(async (blob) => {
    if (!blob || blob.size < 800) return;
    try {
      const fd = new FormData();
      fd.append("audio", blob, "chunk.webm");
      const lang = (formRef.current?.language || "").toLowerCase() === "hindi" ? "hi" : "en";
      fd.append("language", lang);
      fd.append("conversation_id", optsRef.current?.sessionId || "");
      const res = await fetch("/api/roleplay/transcribe-chunk", {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        console.warn("[liveCoach] transcribe-chunk", res.status, await res.text().catch(() => ""));
        return;
      }
      const { text } = await res.json();
      if (text && text.trim()) {
        optsRef.current?.onActivity?.();   // user spoke → reset inactivity timer
        whisperRef.current = (whisperRef.current + " " + text.trim()).trim();
        // Update external ref (final report uses Whisper for accuracy)
        if (externalTranscriptRef) externalTranscriptRef.current = whisperRef.current;
        // If Web Speech is not running, also surface Whisper text in the UI
        if (!speechRef.current) {
          setTranscript(whisperRef.current);
        }
      }
    } catch (err) {
      console.warn("[liveCoach] sendChunk error:", err?.message);
    }
  }, [externalTranscriptRef]);

  // ── LLM checklist parameter check ──────────────────────────────────────
  const checkParams = useCallback(async () => {
    // Use whichever transcript is longer (Web Speech is instant, Whisper is accurate)
    const t = speechRef.current.length >= whisperRef.current.length
      ? speechRef.current
      : whisperRef.current;

    if (!t.trim() || t.length === lastSentRef.current) return;
    lastSentRef.current = t.length;

    const f = formRef.current || {};
    const elapsed = Math.floor((Date.now() - startRef.current) / 1000);
    try {
      const res = await fetch("/api/roleplay/check-params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript:      t,
          prospect_name:   f.prospect_name || "",
          loan_type:       f.loan_type     || "",
          elapsed_seconds: elapsed,
          profession:      f.profession    || "",
          age:             f.age           || "",
          difficulty:      f.difficulty    || "",
          // Additive optional fields — let the backend grader switch to ETB criteria.
          call_type:       f.call_type      || "",
          feedback_state:  f.feedback_state || "",
        }),
      });
      if (!res.ok) {
        console.warn("[liveCoach] check-params", res.status);
        return;
      }
      const data = await res.json();

      setStatus(prev => {
        const next = { ...prev };
        for (const key of Object.keys(INITIAL_STATUS)) {
          if (data[key] === "met") {
            next[key] = "met";
          } else if (data[key] === "missed" && prev[key] === "pending") {
            next[key] = "missed";
          }
        }
        return next;
      });
    } catch (err) {
      console.warn("[liveCoach] checkParams error:", err?.message);
    }
  }, []);

  // ── Web Speech API — real-time transcript display ───────────────────────
  useEffect(() => {
    if (!active) return;

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      // Not supported (Firefox) — Whisper will still work, just no real-time display
      return;
    }

    const rec = new SR();
    rec.continuous      = true;
    rec.interimResults  = true;
    rec.lang = (formRef.current?.language || "").toLowerCase() === "hindi" ? "hi-IN" : "en-IN";
    speechRecRef.current = rec;

    let finalText = "";

    rec.onresult = (event) => {
      // Any recognition result (even interim) = the user is speaking right now.
      optsRef.current?.onActivity?.();
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalText += t + " ";
          speechRef.current = finalText.trim();
          // Also keep external ref updated (fallback if Whisper is slow)
          if (externalTranscriptRef && !whisperRef.current) {
            externalTranscriptRef.current = speechRef.current;
          }
        } else {
          interim = t;
        }
      }
      setTranscript((finalText + interim).trim());
    };

    rec.onerror = (e) => {
      // no-speech is expected when user is quiet — not a real error
      if (e.error !== "no-speech" && e.error !== "aborted") {
        console.warn("[liveCoach] SpeechRecognition error:", e.error);
      }
    };

    rec.onend = () => {
      // Auto-restart so it keeps running for the whole call duration
      if (activeRef.current) {
        try { rec.start(); } catch { }
      }
    };

    try {
      rec.start();
      setListening(true);
    } catch (err) {
      console.warn("[liveCoach] SpeechRecognition.start():", err?.message);
    }

    return () => {
      rec.onend = null; // prevent auto-restart on cleanup
      try { rec.abort(); } catch { }
      speechRecRef.current = null;
    };
  }, [active, externalTranscriptRef]);

  // ── Whisper MediaRecorder lifecycle ─────────────────────────────────────
  useEffect(() => {
    activeRef.current = active;
    if (!active) return;

    startRef.current      = Date.now();
    lastSentRef.current   = 0;
    whisperRef.current    = "";
    speechRef.current     = "";
    setTranscript("");
    setStatus(INITIAL_STATUS);

    pollTimerRef.current = setInterval(checkParams, POLL_MS);

    if (!navigator?.mediaDevices?.getUserMedia) {
      setSupported(false);
      return () => clearInterval(pollTimerRef.current);
    }

    // Fresh MediaRecorder per window — ensures every blob has its own EBML header
    // so faster-whisper can decode it independently (timeslice blobs are headerless
    // raw clusters that fail to decode after the first chunk).
    const startWindow = () => {
      if (!activeRef.current || !streamRef.current) return;
      let recorder;
      try {
        recorder = new MediaRecorder(streamRef.current, { mimeType: "audio/webm" });
      } catch {
        try { recorder = new MediaRecorder(streamRef.current); }
        catch (err) {
          console.warn("[liveCoach] MediaRecorder unsupported:", err?.message);
          setSupported(false);
          return;
        }
      }
      recorderRef.current = recorder;

      const parts = [];
      recorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) parts.push(e.data); };
      recorder.onerror = () => setListening(false);
      recorder.onstop = () => {
        if (parts.length) {
          sendChunk(new Blob(parts, { type: recorder.mimeType || "audio/webm" }));
        }
        if (activeRef.current) startWindow();
      };

      recorder.start();
      chunkTimerRef.current = setTimeout(() => {
        try { if (recorder.state === "recording") recorder.stop(); } catch { }
      }, CHUNK_MS);
    };

    const init = async () => {
      try {
        // Reuse the CALL's mic track when the live room can hand us one, rather
        // than opening a second capture of the same device. Desktop Chrome
        // tolerates two concurrent getUserMedia audio streams; iOS Safari often
        // does not — the second capture interrupts the first, which would kill
        // either the call audio or this transcript pipeline on a phone.
        const shared = optsRef.current.micTrack;
        if (shared && shared.readyState === "live") {
          // Not owned by us — the cleanup below must not stop it.
          streamRef.current = new MediaStream([shared]);
          borrowedMicRef.current = true;
        } else {
          streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
          borrowedMicRef.current = false;
        }
        // setListening is set by Web Speech API above; Whisper is a background pipeline
        startWindow();
      } catch (err) {
        console.warn("[liveCoach] mic unavailable:", err.message);
        setSupported(false);
      }
    };

    init();

    return () => {
      activeRef.current = false;
      clearInterval(pollTimerRef.current);
      clearTimeout(chunkTimerRef.current);
      try {
        const r = recorderRef.current;
        if (r && r.state === "recording") { r.onstop = null; r.stop(); }
      } catch { }
      // Only stop tracks we opened — stopping a borrowed call track would mute
      // the rep for the rest of the conversation.
      if (!borrowedMicRef.current) {
        try { streamRef.current?.getTracks().forEach(t => t.stop()); } catch { }
      }
      borrowedMicRef.current = false;
      recorderRef.current = null;
      streamRef.current   = null;
      setListening(false);
    };
  }, [active, sendChunk, checkParams]);

  const metCount = Object.values(status).filter(s => s === "met").length;

  return { status, supported, listening, metCount, transcript };
}
