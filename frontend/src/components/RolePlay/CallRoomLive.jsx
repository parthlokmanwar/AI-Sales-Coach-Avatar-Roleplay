/*
 * CallRoomLive — the RolePlay call rendered from the Daily SDK instead of the
 * Tavus iframe. Visually and behaviourally a drop-in for CallRoom: same top
 * bar, timer, fullscreen, idle warning, coach checklist and end-call flow.
 *
 * The one thing it does that CallRoom CANNOT: because the avatar's video and
 * audio are same-origin tracks here rather than iframe pixels, recording works
 * on phones and tablets — no getDisplayMedia, no share dialog, no cloud bucket.
 *
 * Gated by VITE_ROLEPLAY_LIVE_ROOM (see index.jsx); the iframe room remains the
 * fallback so a bad join can never take the working desktop call down.
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Maximize2, Minimize2, Video, VideoOff, Square, Loader2,
  Download, Mic, MicOff,
} from "lucide-react";
import CoachChecklist from "./CoachChecklist";

export default function CallRoomLive({
  form, sessionUrl, sessionId, onEnd, transcriptRef,
  onActivity, onSttState, idleCountdown, recorder, room,
}) {
  const [seconds, setSeconds]           = useState(0);
  const [fullscreen, setFullscreen]     = useState(false);
  const [showRecGuide, setShowRecGuide] = useState(false);
  const askedRef = useRef(false);

  const caps = recorder.caps || { any: false, video: false };
  const connecting = !room.joined || !room.avatarOn;

  const beginRecording = () => { setShowRecGuide(false); recorder.start(); };

  // Offer recording once the avatar is actually on screen — starting earlier
  // would hit "no avatar video yet".
  useEffect(() => {
    if (connecting || askedRef.current || !caps.any) return;
    askedRef.current = true;
    if (!recorder.recording && !recorder.ready) setShowRecGuide(true);
  }, [connecting, caps.any, recorder.recording, recorder.ready]);

  // Refresh / tab close — end the Tavus session immediately (same as CallRoom).
  useEffect(() => {
    if (!sessionId) return;
    const endNow = () => {
      try {
        navigator.sendBeacon("/api/roleplay/end", new Blob(
          [JSON.stringify({ conversation_id: sessionId })], { type: "application/json" }));
      } catch { /* best-effort */ }
    };
    window.addEventListener("pagehide", endNow);
    return () => window.removeEventListener("pagehide", endNow);
  }, [sessionId]);

  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

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
            onClick={() => setFullscreen((f) => !f)}
            title={fullscreen ? "Shrink to small view" : "Expand to fullscreen"}
            aria-label={fullscreen ? "Shrink to small view" : "Expand to fullscreen"}
          >
            {fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            <span className="rp-call-size-label">{fullscreen ? "Small" : "Fullscreen"}</span>
          </button>
        </div>
      </div>

      <div className="rp-call-stage">
        {connecting && (
          <div className="rp-call-overlay">
            <div className="rp-call-spinner" />
            <span>{room.error ? room.error : `Connecting to ${name}…`}</span>
          </div>
        )}

        {idleCountdown != null && (
          <div className="rp-idle-warn" role="status">
            🔇 No voice detected — session closes in {idleCountdown}s.
            Say something to stay connected.
          </div>
        )}

        <CoachChecklist
          active={!connecting} form={form} transcriptRef={transcriptRef}
          sessionId={sessionId} onActivity={onActivity} onSttState={onSttState}
          micTrack={room.micTrack}
        />

        {/* The avatar. autoPlay+playsInline matter on iOS or it refuses to start. */}
        <video
          ref={room.remoteVideoEl}
          className="rp-live-video"
          autoPlay playsInline
        />

        {/* Rep self-view */}
        <video
          ref={room.localVideoEl}
          className={"rp-live-self" + (room.camOn ? "" : " rp-live-self--off")}
          autoPlay playsInline muted
        />
      </div>

      <div className="rp-call-controls">
        {/* --alert keeps this visible on phones, where the purely advisory copy
            is hidden to keep the control bar to one compact row. */}
        <span className={"rp-call-hint" + ((recorder.error || recorder.warning) ? " rp-call-hint--alert" : "")}>
          {recorder.error
            ? <span className="rp-rec-error">⚠️ {recorder.error}</span>
            : recorder.warning
              ? <span className="rp-rec-error">⚠️ {recorder.warning}</span>
              : recorder.recording
                ? <>🔴 Recording this call — video and both voices. Saved to your device.</>
                : <>🎤 Speak naturally. Your report is generated automatically when the call ends.</>}
        </span>

        <div className="rp-call-actions">
          <button
            className={"rp-rec-btn rp-rec-btn--icon" + (room.micOn ? "" : " rp-rec-btn--muted")}
            onClick={room.toggleMic}
            title={room.micOn ? "Mute microphone" : "Unmute microphone"}
            aria-label={room.micOn ? "Mute microphone" : "Unmute microphone"}
          >
            {room.micOn ? <Mic size={16} /> : <MicOff size={16} />}
          </button>
          <button
            className={"rp-rec-btn rp-rec-btn--icon" + (room.camOn ? "" : " rp-rec-btn--muted")}
            onClick={room.toggleCam}
            title={room.camOn ? "Turn camera off" : "Turn camera on"}
            aria-label={room.camOn ? "Turn camera off" : "Turn camera on"}
          >
            {room.camOn ? <Video size={16} /> : <VideoOff size={16} />}
          </button>

          {caps.any && (recorder.saving ? (
            <button className="rp-rec-btn" disabled>
              <Loader2 size={16} className="rp-spin" /> Saving…
            </button>
          ) : recorder.recording ? (
            <button className="rp-rec-btn rp-rec-btn--stop" onClick={() => recorder.stop({ save: true })}>
              <Square size={15} />
              <span className="rp-btn-full">Stop &amp; Save</span>
              <span className="rp-btn-short">Stop</span>
            </button>
          ) : (
            <>
              {recorder.ready && (
                <button className="rp-rec-btn rp-rec-btn--save" onClick={recorder.download}>
                  <Download size={16} />
                  <span className="rp-btn-full">Download</span>
                  <span className="rp-btn-short">Save</span>
                </button>
              )}
              <button className="rp-rec-btn rp-rec-btn--start" onClick={() => setShowRecGuide(true)}>
                <Video size={16} />
                <span className="rp-btn-full">{recorder.ready ? "Record again" : "Record"}</span>
                <span className="rp-btn-short">Rec</span>
              </button>
            </>
          ))}

          <button className="rp-call-leave" onClick={onEnd}>
            <span className="rp-call-leave-icon">📞</span>
            <span className="rp-btn-full">End Call</span>
            <span className="rp-btn-short">End</span>
          </button>
        </div>
      </div>

      {showRecGuide && (
        <div className="rp-rec-guide-overlay" onClick={() => setShowRecGuide(false)}>
          <div className="rp-rec-guide" onClick={(e) => e.stopPropagation()}>
            <div className="rp-rec-guide-head">
              <Video size={20} /> Record this session
            </div>
            <p className="rp-rec-guide-lead">
              Saves {name}'s video, {name}'s voice and yours as one file — on any device,
              phone included.
            </p>
            <p className="rp-rec-guide-note">
              No screen sharing and nothing is uploaded. Download it from this bar or from
              the report screen after the call.
            </p>
            <div className="rp-rec-guide-actions">
              <button className="rp-rec-guide-cancel" onClick={() => setShowRecGuide(false)}>
                Don't record
              </button>
              <button className="rp-rec-guide-go" onClick={beginRecording}>
                <Video size={16} /> Start recording
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
