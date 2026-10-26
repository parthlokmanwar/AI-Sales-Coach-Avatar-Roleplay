import React, { useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2, Video, Square, Loader2, Download, Mic } from "lucide-react";
import CoachChecklist from "./CoachChecklist";

export default function CallRoom({ form, sessionUrl, sessionId, onEnd, transcriptRef, onActivity, onSttState, idleCountdown, recorder }) {
  const [connecting, setConnecting] = useState(true);
  const [seconds, setSeconds]       = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [showRecGuide, setShowRecGuide] = useState(false);

  const askedRef = useRef(false);
  const caps = recorder.caps || { any: false, video: false };
  const audioOnly = caps.any && !caps.video;   // mobile: no screen capture exists

  const beginRecording = () => { setShowRecGuide(false); recorder.start(); };

  // Offer recording ONCE, as soon as the call is up. A recording can only be
  // started from inside the live call, so a button the user has to notice mid-
  // conversation gets missed — and they only find out at the report screen,
  // when it's too late. Fires after the "connecting" overlay clears so it never
  // collides with the browser's own mic/camera permission prompt. Never fires
  // on a device that can't record at all.
  useEffect(() => {
    if (connecting || askedRef.current || !caps.any) return;
    askedRef.current = true;
    if (!recorder.recording && !recorder.ready) setShowRecGuide(true);
  }, [connecting, caps.any, recorder.recording, recorder.ready]);

  // Refresh / tab close / navigation away — end the Tavus session IMMEDIATELY
  // instead of waiting for any timeout. sendBeacon survives page unload and
  // carries the auth cookie. (Deliberately NOT on visibilitychange: switching
  // tabs must not kill a live call.)
  useEffect(() => {
    if (!sessionId) return;
    const endNow = () => {
      try {
        const body = new Blob(
          [JSON.stringify({ conversation_id: sessionId })],
          { type: "application/json" },
        );
        navigator.sendBeacon("/api/roleplay/end", body);
      } catch { /* best-effort */ }
    };
    window.addEventListener("pagehide", endNow);
    return () => window.removeEventListener("pagehide", endNow);
  }, [sessionId]);

  // Hide the "connecting" overlay after the avatar has had a moment to join.
  useEffect(() => {
    const t = setTimeout(() => setConnecting(false), 4000);
    return () => clearTimeout(t);
  }, []);

  // Call timer.
  useEffect(() => {
    const t = setInterval(() => setSeconds(s => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // Lock page scroll while the call is expanded to fullscreen.
  useEffect(() => {
    if (!fullscreen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [fullscreen]);

  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");

  const name = form.prospect_name || "Customer";

  return (
    <div className={"rp-call-room" + (fullscreen ? " rp-call-room--full" : "")}>

      {/* Top bar — meeting title + live status + size toggle */}
      <div className="rp-call-topbar">
        <div className="rp-call-meta">
          <span className="rp-call-live-dot" />
          <span className="rp-call-title">{name}</span>
          <span className="rp-call-sub">RolePlay practice call</span>
        </div>
        <div className="rp-call-topbar-right">
          {recorder.recording && (
            <span className="rp-rec-badge" title="Recording this session">
              <span className="rp-rec-dot" /> REC
            </span>
          )}
          <span className="rp-call-timer">{mm}:{ss}</span>
          <button
            type="button"
            className="rp-call-size-btn"
            onClick={() => setFullscreen(f => !f)}
            title={fullscreen ? "Shrink to small view" : "Expand to fullscreen"}
            aria-label={fullscreen ? "Shrink to small view" : "Expand to fullscreen"}
          >
            {fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            <span className="rp-call-size-label">
              {fullscreen ? "Small" : "Fullscreen"}
            </span>
          </button>
        </div>
      </div>

      {/* Stage — the avatar video */}
      <div className="rp-call-stage">
        {connecting && (
          <div className="rp-call-overlay">
            <div className="rp-call-spinner" />
            <span>Connecting to {name}…</span>
          </div>
        )}

        {/* Inactivity countdown — last 10s before the session auto-closes */}
        {idleCountdown != null && (
          <div className="rp-idle-warn" role="status">
            🔇 No voice detected — session closes in {idleCountdown}s.
            Say something to stay connected.
          </div>
        )}

        {/* Live pitch-coverage checklist — floats bottom-left over the iframe */}
        <CoachChecklist
          active={!connecting} form={form} transcriptRef={transcriptRef}
          sessionId={sessionId} onActivity={onActivity} onSttState={onSttState}
        />

        <iframe
          title="Avatar call"
          className="rp-call-iframe"
          src={sessionUrl}
          allow="camera; microphone; autoplay; fullscreen; display-capture"
          allowFullScreen
        />
      </div>

      {/* Bottom control bar — Zoom/Meet style */}
      <div className="rp-call-controls">
        <span className={"rp-call-hint" + ((recorder.error || recorder.warning) ? " rp-call-hint--alert" : "")}>
          {recorder.error
            ? <span className="rp-rec-error">⚠️ {recorder.error}</span>
            : recorder.warning
              ? <span className="rp-rec-error">⚠️ {recorder.warning}</span>
              : recorder.recording
                ? <>🔴 Recording this call. It's saved to your machine when you press Stop &amp; Save, and offered again on the report screen.</>
                : <>🎤 Allow mic &amp; camera when your browser asks. Your report is generated automatically when the call ends.</>}
        </span>

        <div className="rp-call-actions">
          {/* Nothing to offer on a device with neither screen capture nor a mic */}
          {caps.any && (recorder.saving ? (
            <button className="rp-rec-btn" disabled>
              <Loader2 size={16} className="rp-spin" /> Saving…
            </button>
          ) : recorder.recording ? (
            <button className="rp-rec-btn rp-rec-btn--stop" onClick={() => recorder.stop({ save: true })}>
              <Square size={15} /> Stop &amp; Save
            </button>
          ) : (
            <>
              {/* Finalised mid-call (e.g. the browser's own "Stop sharing" bar)
                  — offer the file here too, not only on the report screen. */}
              {recorder.ready && (
                <button className="rp-rec-btn rp-rec-btn--save" onClick={recorder.download}>
                  <Download size={16} /> Download
                </button>
              )}
              <button className="rp-rec-btn rp-rec-btn--start" onClick={() => setShowRecGuide(true)}>
                {audioOnly ? <Mic size={16} /> : <Video size={16} />}
                {recorder.ready ? "Record again" : audioOnly ? "Record audio" : "Record"}
              </button>
            </>
          ))}

          <button className="rp-call-leave" onClick={onEnd}>
            <span className="rp-call-leave-icon">📞</span> End Call
          </button>
        </div>
      </div>

      {/* Guided pre-prompt — makes the browser's native share dialog feel intentional */}
      {showRecGuide && (
        <div className="rp-rec-guide-overlay" onClick={() => setShowRecGuide(false)}>
          <div className="rp-rec-guide" onClick={(e) => e.stopPropagation()}>
            <div className="rp-rec-guide-head">
              {audioOnly ? <Mic size={20} /> : <Video size={20} />}
              {audioOnly ? "Record this call (audio)" : "Record this session"}
            </div>

            {audioOnly ? (
              <>
                <p className="rp-rec-guide-lead">
                  Phones and tablets can't record the screen, so this saves the
                  conversation as an <b>audio file</b>.
                </p>
                <ol className="rp-rec-guide-steps">
                  <li>Allow <b>microphone</b> access when asked</li>
                  <li>Keep the phone off headphones so {name}'s voice is picked up too</li>
                </ol>
                <p className="rp-rec-guide-note">
                  Captures your voice, and {name}'s from the speaker. For full video of the
                  call, run the session on a computer. Nothing is uploaded.
                </p>
              </>
            ) : (
              <>
                <p className="rp-rec-guide-lead">
                  Your browser will ask what to share. Two quick picks and you're recording:
                </p>
                <ol className="rp-rec-guide-steps">
                  <li>Keep <b>“This Tab”</b> selected</li>
                  <li>Tick <b>“Also share tab audio”</b> — this captures {name}'s voice</li>
                </ol>
                <p className="rp-rec-guide-note">
                  Records the avatar video, {name}'s voice and your mic into one file. Download it
                  from this bar or from the report screen after the call. Nothing is uploaded.
                </p>
              </>
            )}
            <div className="rp-rec-guide-actions">
              <button className="rp-rec-guide-cancel" onClick={() => setShowRecGuide(false)}>
                Don't record
              </button>
              <button className="rp-rec-guide-go" onClick={beginRecording}>
                {audioOnly ? <Mic size={16} /> : <Video size={16} />} Start recording
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
