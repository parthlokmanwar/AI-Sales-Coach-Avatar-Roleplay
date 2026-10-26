/*
 * useSessionRecorder — records the RolePlay session to a file the user
 * downloads to their own machine. No backend, no cloud, nothing uploaded.
 *
 * TWO MODES, chosen by what the DEVICE can actually do:
 *
 *   "screen" (desktop) — the avatar call lives in a cross-origin Tavus iframe,
 *     so its video/audio can't be read from the DOM. We capture the TAB's
 *     rendered output with getDisplayMedia (avatar video + its voice as tab
 *     audio) and mix in the rep's mic. Full video + both voices.
 *
 *   "audio" (mobile) — getDisplayMedia DOES NOT EXIST on iOS Safari or Android
 *     Chrome, and there is no other route to the iframe's pixels. So a phone
 *     records the mic only: the rep's voice, plus the avatar's voice as it comes
 *     out of the speaker (echoCancellation is off precisely so that survives —
 *     on headphones only the rep is captured). Not as good as video, but it is
 *     the honest ceiling of what a mobile browser permits, and a voice recording
 *     of the call is still worth having for coaching.
 *
 * Capability is FEATURE-DETECTED, never UA-sniffed, and exposed as `caps` so the
 * UI can offer the right thing (or nothing) instead of promising video and then
 * failing at the share dialog.
 *
 * The finished blob is HELD (not force-downloaded) so the call report screen can
 * offer it next to the PDF report; `download()` saves it on demand. This hook is
 * owned by RolePlaySimulation, not CallRoom, so the recording survives the
 * unmount that happens the instant the call ends.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { composeCallVideo } from "./composeCallVideo";

const isSupported = (m) => !!window.MediaRecorder && MediaRecorder.isTypeSupported(m);

/* Codec choice drives BOTH quality and where the file will play.
 * H.264 first: it is hardware-encoded on essentially every machine, so the
 * encoder keeps up with a full-desktop capture instead of dropping and smearing
 * blocks the way software VP9 does — and MP4/H.264 opens in any player, Teams
 * or PowerPoint, which .webm does not. VP9/VP8 remain as fallbacks. */
function pickVideoMime() {
  const candidates = [
    ["video/mp4;codecs=avc1.42E01E,mp4a.40.2", "mp4"],   // H.264 + AAC (hardware)
    ["video/mp4", "mp4"],
    ["video/webm;codecs=h264,opus", "webm"],             // H.264 in WebM (hardware)
    ["video/webm;codecs=vp9,opus", "webm"],
    ["video/webm;codecs=vp8,opus", "webm"],              // cheaper to encode than VP9
    ["video/webm", "webm"],
  ];
  for (const [mime, ext] of candidates) if (isSupported(mime)) return { mime, ext };
  return { mime: "", ext: "webm" };
}

/* m4a first — it plays in the iOS/Android system player and WhatsApp; .webm
 * audio does not play on iOS at all. */
function pickAudioMime() {
  const candidates = [
    ["audio/mp4;codecs=mp4a.40.2", "m4a"],
    ["audio/mp4", "m4a"],
    ["audio/webm;codecs=opus", "webm"],
    ["audio/webm", "webm"],
  ];
  for (const [mime, ext] of candidates) if (isSupported(mime)) return { mime, ext };
  return { mime: "", ext: "webm" };
}

/* Bits/pixel/frame that keeps UI text legible without absurd file sizes.
 * A flat 2.5 Mbps starved a 1080p capture (~0.04 bpp) and produced blocky
 * smearing on static panels; ~0.09 bpp is the usual "good" target. */
function bitrateFor(width, height, fps) {
  const bits = (width || 1280) * (height || 720) * (fps || 30) * 0.09;
  return Math.round(Math.min(12_000_000, Math.max(3_000_000, bits)));
}

function sanitize(s) {
  return String(s || "session").replace(/[^\w-]+/g, "_").slice(0, 40);
}

/** @param trackSource - optional () => ({ videoTrack, audioTracks[] }) supplied by
 *  the live (Daily SDK) call room. When present, recording uses those tracks
 *  directly and works on mobile; when absent we fall back to screen capture. */
export default function useSessionRecorder(filenameBase = "roleplay", trackSource = null) {
  const [state, setState] = useState("idle");   // idle | recording | saving | ready
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");   // non-fatal (e.g. no tab audio)
  const [result, setResult] = useState(null);   // { url, filename, size, seconds, kind }

  const recorderRef = useRef(null);
  const chunksRef   = useRef([]);
  const streamsRef  = useRef([]);
  const acRef       = useRef(null);
  const startedRef  = useRef(0);
  const saveOnStop  = useRef(false);
  const urlRef      = useRef("");               // live object URL, for revoking
  const kindRef     = useRef("video");          // what the in-flight take is
  const compRef     = useRef(null);             // canvas compositor, if any

  // What this device can do.
  //   tracks — the live-room path supplies the avatar's own MediaStreamTracks,
  //     so there is nothing to screen-capture. MediaRecorder is all that's
  //     needed, and that EXISTS on iOS Safari and Android Chrome — which is why
  //     this path, unlike screen capture, records video on a phone.
  //   screen — iframe path: getDisplayMedia, desktop only.
  //   audio  — last resort: mic only.
  const caps = useMemo(() => {
    const md = navigator.mediaDevices;
    const rec = !!window.MediaRecorder;
    const screen = !!(md && md.getDisplayMedia) && rec;
    const audio = !!(md && md.getUserMedia) && rec;
    const video = trackSource ? rec : screen;   // live room → video anywhere
    return {
      video, audio, any: video || audio,
      mode: trackSource ? "tracks" : screen ? "screen" : audio ? "audio" : "none",
    };
  }, [trackSource]);

  const releaseStreams = useCallback(() => {
    // Stop the canvas render loop FIRST — it holds a requestAnimationFrame that
    // would keep painting (and burning battery) long after the recording ended.
    if (compRef.current) {
      try { compRef.current.stop(); } catch { /* noop */ }
      compRef.current = null;
    }
    streamsRef.current.forEach((s) => {
      try { s.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
    });
    streamsRef.current = [];
    if (acRef.current) {
      try { acRef.current.close(); } catch { /* noop */ }
      acRef.current = null;
    }
  }, []);

  const saveToDisk = useCallback((res) => {
    if (!res) return;
    const a = document.createElement("a");
    a.href = res.url;
    a.download = res.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, []);

  const attachRecorder = useCallback((rec, mime, ext, kind) => {
    chunksRef.current = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data); };
    rec.onerror = () => setError("Recording stopped unexpectedly.");
    rec.onstop = () => {
      setState("saving");
      const blob = new Blob(chunksRef.current, { type: mime || "application/octet-stream" });
      chunksRef.current = [];               // release the buffered chunks
      releaseStreams();
      if (!blob.size) { setState("idle"); return; }

      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      const res = {
        url,
        filename: `${sanitize(filenameBase)}_${stamp}.${ext}`,
        size: blob.size,
        seconds: Math.round((Date.now() - startedRef.current) / 1000),
        format: ext,
        kind,                                // "video" | "audio"
      };
      setResult(res);
      setState("ready");
      if (saveOnStop.current) saveToDisk(res);
    };
    recorderRef.current = rec;
    startedRef.current = Date.now();
    saveOnStop.current = false;
    rec.start(1000);
    setState("recording");
  }, [filenameBase, releaseStreams, saveToDisk]);

  // ── Live-room path: record the avatar's OWN tracks (works on mobile) ──────
  // The tracks come from the Daily call object, so they are same-origin media
  // in this page — no iframe, no screen capture, nothing a phone lacks. Audio
  // is mixed through an AudioContext so the avatar and the rep land in one
  // track; on iOS the context must be resumed inside the click that started us.
  const startFromTracks = useCallback(async () => {
    const src = trackSource ? trackSource() : null;
    const videoTrack = src && src.videoTrack;
    const audioTracks = (src && src.audioTracks) || [];
    if (!videoTrack) {
      throw Object.assign(new Error("no avatar video yet"), { name: "NotReadableError" });
    }

    let mixed = null;
    if (audioTracks.length) {
      const ac = new (window.AudioContext || window.webkitAudioContext)();
      acRef.current = ac;
      if (ac.state === "suspended") { try { await ac.resume(); } catch { /* noop */ } }
      const dest = ac.createMediaStreamDestination();
      audioTracks.forEach((t) => {
        try { ac.createMediaStreamSource(new MediaStream([t])).connect(dest); } catch { /* skip */ }
      });
      mixed = dest.stream.getAudioTracks()[0] || null;
    }
    if (!mixed) {
      setWarning("Recording without audio — the call's audio tracks weren't available yet.");
    }

    // Composite the avatar AND the rep's camera into one frame, so the file
    // shows both sides of the call rather than the avatar alone. Falls back to
    // the raw avatar track if a canvas can't be set up.
    const comp = composeCallVideo(src.remoteEl, src.localEl, { fps: 30 });
    compRef.current = comp;

    // Clone the fallback so stopping the recorder never kills the LIVE call.
    const vTrack = comp ? comp.track : videoTrack.clone();
    const w = comp ? comp.width  : (videoTrack.getSettings?.().width  || 1280);
    const h = comp ? comp.height : (videoTrack.getSettings?.().height || 720);

    const combined = new MediaStream(mixed ? [vTrack, mixed] : [vTrack]);
    streamsRef.current.push(combined);

    const { mime, ext } = pickVideoMime();
    const opts = {
      videoBitsPerSecond: bitrateFor(w, h, 30),
      audioBitsPerSecond: 128_000,
    };
    if (mime) opts.mimeType = mime;
    attachRecorder(new MediaRecorder(combined, opts), mime, ext, "video");
  }, [trackSource, attachRecorder]);

  // ── Mobile / no-screen-capture path: mic only ─────────────────────────────
  const startAudioOnly = useCallback(async () => {
    // echoCancellation off on purpose: it would strip the avatar's voice coming
    // back through the speaker, leaving a recording of one side of the call.
    const mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: true },
    });
    streamsRef.current.push(mic);
    kindRef.current = "audio";
    const { mime, ext } = pickAudioMime();
    const opts = { audioBitsPerSecond: 128_000 };
    if (mime) opts.mimeType = mime;
    attachRecorder(new MediaRecorder(mic, opts), mime, ext, "audio");
  }, [attachRecorder]);

  // ── Desktop path: tab video + tab audio + mic ──────────────────────────────
  const startScreen = useCallback(async () => {
    // preferCurrentTab / selfBrowserSurface pre-select THIS tab in Chrome's
    // picker, which is the only choice that captures the avatar. Unknown keys
    // are ignored by browsers that don't support them.
    const display = await navigator.mediaDevices.getDisplayMedia({
      // Cap at 1080p: on a larger display, downscaling and spending the bits
      // on quality beats capturing more pixels than the encoder can serve.
      video: {
        frameRate: { ideal: 30, max: 30 },
        width:     { ideal: 1920, max: 1920 },
        height:    { ideal: 1080, max: 1080 },
      },
      audio: { echoCancellation: false, noiseSuppression: false },
      preferCurrentTab: true,
      selfBrowserSurface: "include",
      systemAudio: "include",
    });
    streamsRef.current.push(display);
    kindRef.current = "video";

    // Capture the rep's mic (best-effort — recording still works without it).
    let mic = null;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamsRef.current.push(mic);
    } catch { /* mic optional */ }

    // Mix tab audio (avatar voice) + mic (rep voice) into one audio track.
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    acRef.current = ac;
    const dest = ac.createMediaStreamDestination();
    const tabAudio = display.getAudioTracks();
    if (tabAudio.length) {
      ac.createMediaStreamSource(new MediaStream([tabAudio[0]])).connect(dest);
    } else {
      // Shared a surface without audio (or Safari, which can't capture tab
      // audio at all) — say so now rather than after they open a silent file.
      setWarning("Tab audio wasn't shared — the recording will have your voice "
               + "but not the avatar's. Stop and re-record with “Also share tab audio” ticked.");
    }
    if (mic && mic.getAudioTracks().length) {
      ac.createMediaStreamSource(new MediaStream([mic.getAudioTracks()[0]])).connect(dest);
    }

    const videoTrack = display.getVideoTracks()[0];
    // "detail" tells the encoder to protect spatial detail (UI text, the
    // checklist, the transcript) over frame smoothness. Without it a screen
    // capture is encoded like camera video and static text turns to mush.
    videoTrack.contentHint = "detail";

    const mixedAudio = dest.stream.getAudioTracks()[0];
    const combined = new MediaStream(mixedAudio ? [videoTrack, mixedAudio] : [videoTrack]);

    const s = videoTrack.getSettings ? videoTrack.getSettings() : {};
    const { mime, ext } = pickVideoMime();
    const opts = {
      videoBitsPerSecond: bitrateFor(s.width, s.height, s.frameRate),
      audioBitsPerSecond: 128_000,
    };
    if (mime) opts.mimeType = mime;

    // The browser's own "Stop sharing" bar → finalise and keep the file.
    videoTrack.addEventListener("ended", () => {
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    });

    attachRecorder(new MediaRecorder(combined, opts), mime, ext, "video");
  }, [attachRecorder]);

  const start = useCallback(async () => {
    setError("");
    setWarning("");
    if (!caps.any) {
      setError("This browser can't record the session. Try Chrome or Edge on a computer.");
      return;
    }
    try {
      if (caps.mode === "tracks")      await startFromTracks();
      else if (caps.mode === "screen") await startScreen();
      else                             await startAudioOnly();
    } catch (e) {
      releaseStreams();
      const name = e && e.name;
      if (name === "NotAllowedError") {
        setError(caps.mode === "screen"
          ? "Screen share was cancelled — recording not started."
          : "Microphone permission was denied — recording not started.");
      } else if (name === "NotFoundError") {
        setError("No microphone was found on this device.");
      } else if (name === "NotReadableError") {
        setError("The avatar hasn't connected yet — wait a moment and press Record again.");
      } else {
        setError("Couldn't start recording. Please try again.");
      }
      setState("idle");
    }
  }, [caps, startFromTracks, startScreen, startAudioOnly, releaseStreams]);

  /** stop({ save }) — `save: true` downloads immediately (explicit "Stop & Save");
   *  otherwise the file is just held for the report screen's download button. */
  const stop = useCallback(({ save = false } = {}) => {
    saveOnStop.current = save;
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") rec.stop();   // → onstop finalises
    else releaseStreams();
  }, [releaseStreams]);

  const download = useCallback(() => saveToDisk(result), [result, saveToDisk]);

  const reset = useCallback(() => {
    if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = ""; }
    setResult(null);
    setError("");
    setWarning("");
    setState("idle");
  }, []);

  // Never leak the capture streams or the blob URL if the page tears down.
  useEffect(() => () => {
    try {
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    } catch { /* noop */ }
    releaseStreams();
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, [releaseStreams]);

  return {
    state, error, warning, result, caps,
    start, stop, download, reset,
    recording: state === "recording",
    saving:    state === "saving",
    ready:     state === "ready",
  };
}
