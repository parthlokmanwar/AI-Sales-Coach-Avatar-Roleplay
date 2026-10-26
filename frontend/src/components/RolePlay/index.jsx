import React, { useState, useRef, useEffect, useCallback } from "react";
import { ArrowLeft, Bot, Calendar, Clock, PlayCircle, RefreshCw } from "lucide-react";
import RoleplayForm   from "./RoleplayForm";
import ETBServiceForm from "./ETBServiceForm";
import CallRoom       from "./CallRoom";
import CallRoomLive   from "./CallRoomLive";
import CallReport     from "./CallReport";
import useDailyRoom   from "./useDailyRoom";
import PresetGallery  from "./PresetGallery";
import { useRoleplayForm } from "./useRoleplayForm";
import { useReport } from "./useReport";
import useSessionRecorder from "./useSessionRecorder";
import { downloadReportPDF } from "./pdfExport";
import { resolveAvatar, ROLEPLAY_MODEL } from "./avatarMap";
import "./RolePlay.css";

const STEPS    = ["Configure", "Call", "Report"];
const STEP_IDX = { form: 0, etbForm: 0, call: 1, inactive: 1, report: 2 };

// Live room = join the Tavus Daily room with the SDK instead of the iframe.
// This is what makes call recording work on phones (the iframe's video is
// cross-origin, so it needs screen capture, which mobile browsers lack).
//
// DEFAULT OFF until it has been through a real call on real devices: flipping
// it changes how every session connects, and the iframe path is what works
// today. Enable with VITE_ROLEPLAY_LIVE_ROOM=true in frontend/.env, and set it
// back to false to roll back instantly.
const LIVE_ROOM = String(import.meta.env.VITE_ROLEPLAY_LIVE_ROOM ?? "false")
  .toLowerCase() === "true";

const fmtSize = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
const fmtDur  = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

/** Post-call card for the session's video+audio recording.
 *  ALWAYS renders something: the download when a recording exists, otherwise a
 *  plain explanation. A silently-absent card is why the feature looked missing —
 *  the user has no way to tell "you didn't record" from "the button is broken". */
function RecordingCard({ recorder, showEmpty = true }) {
  if (recorder.saving) {
    return (
      <div className="rpr-rec-card rpr-rec-card--busy">
        <div className="rp-call-spinner rp-call-spinner--sm" />
        <span>Finalising your call recording…</span>
      </div>
    );
  }

  const caps = recorder.caps || { any: false, video: false };

  if (!recorder.ready || !recorder.result) {
    if (!showEmpty) return null;      // don't nag while the report is still loading
    // The reason differs per device, and a rep on a phone must not be told to
    // press a button that their browser cannot honour.
    const why = recorder.error ? recorder.error
      : !caps.any
        ? <>This browser can't record the call. Run the session in Chrome or Edge on a
            computer to capture video and audio.</>
      : !caps.video
        ? <>Phones and tablets can't record the screen — only audio. Press <b>Record audio</b>
            during the call, or use a computer for full video.</>
        : <>The video is captured in your browser, so it has to be started during the
            call — press <b>Record</b> in the call bar when the next session begins.</>;
    return (
      <div className="rpr-rec-card rpr-rec-card--empty">
        <span className="rpr-rec-icon">{caps.video || !caps.any ? "🎬" : "🎙️"}</span>
        <div className="rpr-rec-meta">
          <span className="rpr-rec-title">No recording for this call</span>
          <span className="rpr-rec-sub">{why}</span>
        </div>
      </div>
    );
  }

  const { size, seconds, format, kind } = recorder.result;
  const isAudio = kind === "audio";
  return (
    <div className="rpr-rec-card">
      <span className="rpr-rec-icon">{isAudio ? "🎙️" : "🎬"}</span>
      <div className="rpr-rec-meta">
        <span className="rpr-rec-title">
          {isAudio ? "Call audio ready" : "Call recording ready"}
        </span>
        <span className="rpr-rec-sub">
          {isAudio ? "Audio only" : "Video + audio"} · {fmtDur(seconds)} · {fmtSize(size)} · .{format || "webm"}
        </span>
      </div>
      <button className="rpr-btn-rec" onClick={recorder.download}>
        ⬇ Download {isAudio ? "Audio" : "Recording"}
      </button>
    </div>
  );
}

const fmtSlotTime = (iso) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
const fmtSlotDay = (iso) =>
  new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short" });

export default function RolePlaySimulation({
  bookedLaunch = null, onBookedConsumed = null, onGoToBooking = null,
  // The simulation is entered through a booked slot, so Back always returns to
  // the booking page — that's where the session (and any other slot) lives.
  onBack = null, backLabel = "Book a Session",
}) {
  const {
    form, sessionUrl, sessionId, idleTimeoutS, step, setStep,
    loading, error, canSubmit, canEtbSubmit,
    avatars, models, avatarsLoading, avatarsError,
    set, setAll, launchPreset, loadAvatars,
    endCall, reset,
  } = useRoleplayForm();

  // Booked-seat support (three-mode redesign). The booking id rides along on
  // every launch so the backend can validate the slot window, pin the admin's
  // scenario, and mark attendance. Assistant seats auto-launch the pinned
  // scenario; practice seats keep the normal chooser with the booking attached.
  const bookingIdRef = useRef("");
  const bookedHandledRef = useRef(false);
  const launchWithBooking = useCallback(
    (payload) => launchPreset({ ...payload, booking_id: bookingIdRef.current || "" }),
    [launchPreset],
  );
  useEffect(() => {
    if (!bookedLaunch || bookedHandledRef.current) return;
    bookedHandledRef.current = true;
    bookingIdRef.current = bookedLaunch.booking_id || "";
    const cfg = bookedLaunch.scenario_config || {};
    if (bookedLaunch.kind === "assistant" && Object.keys(cfg).length) {
      launchPreset({ ...cfg, booking_id: bookedLaunch.booking_id });
    }
    onBookedConsumed?.();
  }, [bookedLaunch, launchPreset, onBookedConsumed]);

  // Slot gate (three-mode redesign): the tab is usable only during a booked
  // slot window. The server enforces this on /launch; this state just renders
  // the friendly gate screen with the user's next allotted time.
  // null = still checking · {gated, allowed, live, next} afterwards.
  const [access, setAccess] = useState(bookedLaunch ? { gated: true, allowed: true, live: null, next: null } : null);
  const refreshAccess = useCallback(async () => {
    try {
      const r = await fetch("/api/roleplay/access");
      const d = await r.json();
      setAccess(d);
      if (d.allowed && d.live?.booking_id) bookingIdRef.current = d.live.booking_id;
    } catch {
      // Can't reach the check — render the tab; the launch endpoint still gates.
      setAccess({ gated: false, allowed: true, live: null, next: null });
    }
  }, []);
  useEffect(() => {
    if (!bookedLaunch) refreshAccess();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const {
    report,
    error: reportError,
    lastStatus: reportStatus,
    generateReport,
    clearReport,
  } = useReport();

  // Session recording (video + audio). Owned HERE, not in CallRoom: CallRoom
  // unmounts the instant the call ends, and MediaRecorder finalises the file
  // asynchronously after that — a recorder living in CallRoom would lose the
  // last chunks and the finished blob.
  // Join the Daily room only while actually in the call, so no connection is
  // held open on the form/report screens.
  const room = useDailyRoom(sessionUrl, { enabled: LIVE_ROOM && step === "call" });
  const recorder = useSessionRecorder(
    `roleplay_${form.prospect_name || "customer"}`,
    LIVE_ROOM ? room.getTracks : null,
  );
  const { stop: stopRecording, reset: resetRecording } = recorder;  // stable refs

  // Local Whisper transcript accumulated during the call (written by liveCoach.js).
  const localTranscriptRef = useRef("");

  // Local analysis state for the "End Call → auto-report" flow.
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState("");

  const idx = STEP_IDX[step];
  const inCall = step === "call";
  const onPresets = step === "presets";

  // ── Inactivity timeout (cost optimisation) ──────────────────────────────
  // If the user sends no voice input for idleTimeoutS seconds (value comes
  // from the backend's ROLEPLAY_IDLE_TIMEOUT_S), end the avatar session and
  // show the "closed due to inactivity" screen. liveCoach.js reports user
  // speech via onActivity; sttLiveRef guards against killing a call when the
  // mic pipeline isn't running (permission denied) and silence can't be seen.
  const lastActivityRef = useRef(Date.now());
  const sttLiveRef      = useRef(false);
  // Countdown shown over the call during the final 10s before auto-close, so
  // the user gets a chance to speak and keep the session alive.
  const [idleCountdown, setIdleCountdown] = useState(null);

  const handleActivity = useCallback(() => {
    lastActivityRef.current = Date.now();
  }, []);
  const handleSttState = useCallback((live) => {
    sttLiveRef.current = !!live;
  }, []);

  const handleInactivityTimeout = useCallback((cid) => {
    setStep("inactive");           // unmounts CallRoom → mic, Whisper chunks,
    stopRecording();               // Web Speech and checklist polling all stop.
    if (cid) {                     // Recording is finalised, not discarded.
      fetch("/api/roleplay/end", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: cid }),
      }).catch(() => { /* backend reaper is the safety net */ });
    }
  }, [setStep, stopRecording]);

  useEffect(() => {
    if (!inCall) return;
    lastActivityRef.current = Date.now();   // fresh deadline per call
    const t = setInterval(() => {
      if (!sttLiveRef.current) { setIdleCountdown(null); return; }
      const remaining = idleTimeoutS - (Date.now() - lastActivityRef.current) / 1000;
      setIdleCountdown(remaining <= 10 ? Math.max(0, Math.ceil(remaining)) : null);
      if (remaining <= 0) handleInactivityTimeout(sessionId);
    }, 1000);
    return () => { clearInterval(t); setIdleCountdown(null); };
  }, [inCall, idleTimeoutS, sessionId, handleInactivityTimeout]);

  // Try a few times — Tavus needs a moment after the call ends to finalise the
  // transcript, so the first attempt may legitimately 404. Only 404 ("not ready
  // yet") and network failures are retried: any other status repeats
  // deterministically and each retry burns a full grading LLM call.
  const runAnalysis = async (cid) => {
    if (!cid) {
      setAnalyzing(false);
      setAnalyzeError("This call has no session id, so it can't be graded. Start a new session.");
      return;
    }
    setAnalyzing(true);
    setAnalyzeError("");
    let rep = null;
    let permanent = false;
    for (let i = 0; i < 6 && !rep; i++) {
      if (i > 0) await new Promise(r => setTimeout(r, 4000));
      rep = await generateReport({
        sessionId:       cid,
        prospectName:    form.prospect_name,
        profession:      form.profession,
        loanType:        form.loan_type,
        scenario:        form.scenario,
        localTranscript: localTranscriptRef.current,
        callType:        form.call_type,
        feedbackState:   form.feedback_state,
      });
      const st = reportStatus.current;
      if (!rep && st !== 404 && st !== 0) { permanent = true; break; }
    }
    setAnalyzing(false);
    // On a permanent failure leave analyzeError blank so the screen shows the
    // server's actual reason (reportError) instead of a misleading "still
    // processing" — the two need different fixes.
    if (!rep && !permanent) {
      setAnalyzeError(
        "We couldn't analyse this call yet — the transcript may still be " +
        "processing. Wait a few seconds and retry."
      );
    }
  };

  // End the live call → end the Tavus session → auto-generate the report.
  const handleEndCall = async () => {
    const cid = sessionId;
    // Finalise (but don't force-download) any recording — the report screen
    // offers it next to the PDF.
    stopRecording();
    setStep("report");
    if (cid) {
      try {
        await fetch("/api/roleplay/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: cid }),
        });
      } catch { /* best-effort */ }
    }
    await runAnalysis(cid);
  };

  // Custom form submitted → auto-assign the avatar (by profession/gender/age)
  // and model, then launch straight into the call (no avatar-picker step).
  const handleConfigureSubmit = () => {
    const replica_id = resolveAvatar(form.profession, form.gender, form.age);
    launchWithBooking({
      ...form,
      customer_type: form.customer_type || "new",
      replica_id,
      // gpt-4o-mini for both English and Hindi; the language flag (carried in
      // ...form) separately drives the Hindi voice + prompt on the backend.
      model: ROLEPLAY_MODEL,
    });
  };

  // Custom ETB service-call form submitted → force the existing-customer +
  // service-call contract so the backend selects the ETB avatar prompt + grader,
  // then resolve the avatar/model the same way and launch.
  const handleEtbSubmit = () => {
    const replica_id = resolveAvatar(form.profession, form.gender, form.age);
    launchWithBooking({
      ...form,
      customer_type: "existing",
      call_type: "Service Call (customer has a problem first)",
      replica_id,
      model: ROLEPLAY_MODEL,
    });
  };

  // Start over — back to the preset gallery.
  const handleReset = () => {
    reset();
    clearReport();
    resetRecording();          // drop the previous call's video + its blob URL
    setAnalyzing(false);
    setAnalyzeError("");
    setStep("presets");
  };

  return (
    <div className={"rp-page" + (inCall ? " rp-page--incall" : "")}>

      {/* PAGE HEADER + stepper — hidden during the call so the meeting view
          fills the viewport and the whole device menu is visible without scroll */}
      {!inCall && (
      <div className="rp-page-header">
        {/* Page-level back — the gallery/gate screens have no other way out
            except the sidebar. Inner steps keep their own "back to presets"
            link, so this only shows where nothing else goes back. */}
        {onBack && onPresets && (
          <button className="rp-back-link" onClick={onBack}>
            <ArrowLeft size={14} style={{ verticalAlign: "-2px" }} /> Back to {backLabel}
          </button>
        )}
        <div className="rp-page-title-row">
          {/* Matches the RolePlay nav icon — a hand-drawn headphones glyph here
              and a different icon in the sidebar read as two separate places. */}
          <span className="rp-page-icon" aria-hidden="true">
            <Bot size={22} />
          </span>
          <div>
            <h2 className="rp-page-title">RolePlay Simulation</h2>
            <p className="rp-page-subtitle">
              Practise a live loan-pitch call against an avatar customer, then get graded
            </p>
          </div>
        </div>

        {/* 4-step stepper — only for the custom flow (hidden on the preset gallery) */}
        {!onPresets && (
        <div className="rp-stepper">
          {STEPS.map((s, i) => (
            <React.Fragment key={s}>
              <div className={
                "rp-step" +
                (i === idx ? " rp-step--active" : "") +
                (i < idx   ? " rp-step--done"   : "")
              }>
                <span className="rp-step-num">{i + 1}</span>
                <span className="rp-step-label">{s}</span>
              </div>
              {i < STEPS.length - 1 &&
                <div className={
                  "rp-step-connector" +
                  (i < idx ? " rp-step-connector--done" : "")
                } />
              }
            </React.Fragment>
          ))}
        </div>
        )}
      </div>
      )}

      {/* CONTENT */}
      {inCall ? (
        LIVE_ROOM ? (
          <CallRoomLive
            form={form} sessionUrl={sessionUrl} sessionId={sessionId}
            onEnd={handleEndCall} transcriptRef={localTranscriptRef}
            onActivity={handleActivity} onSttState={handleSttState}
            idleCountdown={idleCountdown} recorder={recorder} room={room}
          />
        ) : (
          <CallRoom
            form={form} sessionUrl={sessionUrl} sessionId={sessionId}
            onEnd={handleEndCall} transcriptRef={localTranscriptRef}
            onActivity={handleActivity} onSttState={handleSttState}
            idleCountdown={idleCountdown} recorder={recorder}
          />
        )
      ) : onPresets ? (
        access === null ? (
          <div className="rp-content-card rpr-analyzing">
            <div className="rp-call-spinner" />
            <p className="rpr-analyzing-title">Checking your session access…</p>
          </div>
        ) : access.gated && !access.allowed ? (
          /* Outside any booked slot window — the simulation stays closed. */
          <div className="rp-content-card rpr-analyzing">
            <span style={{ fontSize: 40, lineHeight: 1 }} aria-hidden="true">🗓️</span>
            <p className="rpr-analyzing-title">No active session right now</p>
            <p className="rpr-analyzing-sub" style={{ maxWidth: "52ch" }}>
              Role-play simulation opens during your booked slot time.
              {access.next ? (
                <> Your next session is <b>{fmtSlotDay(access.next.start_at)} ·{" "}
                {fmtSlotTime(access.next.start_at)} – {fmtSlotTime(access.next.end_at)}</b>
                {" "}— access unlocks 5 minutes before the start.</>
              ) : (
                <> You have no upcoming booking — reserve a seat first.</>
              )}
            </p>
            <div className="rpr-analyzing-actions">
              {onGoToBooking && (
                <button className="rpr-btn-pdf" onClick={onGoToBooking}>
                  <Calendar size={15} style={{ verticalAlign: "-2px" }} /> Book a Session
                </button>
              )}
              <button className="rp-btn-secondary" onClick={() => { setAccess(null); refreshAccess(); }}>
                <RefreshCw size={14} style={{ verticalAlign: "-2px" }} /> Check again
              </button>
            </div>
          </div>
        ) : (
          /* Inside a live booked window: the WHOLE simulation is open — every
             avatar in the gallery is selectable. An admin-assigned scenario is
             offered as the highlighted default, never a lock. */
          <>
            {access.gated && access.live && (
              access.live.kind === "assistant" &&
              Object.keys(access.live.scenario_config || {}).length ? (
                <div style={{
                  display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12,
                  margin: "0 0 12px", padding: "12px 16px", borderRadius: 10,
                  background: "#fef3c7", color: "#92400e",
                }}>
                  <Clock size={16} style={{ flex: "0 0 auto" }} />
                  <span style={{ flex: 1, minWidth: 220, fontSize: 13.5, fontWeight: 600 }}>
                    {access.live.title || "Your scheduled session"} is live until{" "}
                    {fmtSlotTime(access.live.end_at)} — join your admin's assigned
                    scenario, or pick any customer below.
                  </span>
                  <button
                    disabled={loading}
                    onClick={() => launchPreset({
                      ...access.live.scenario_config,
                      booking_id: access.live.booking_id,
                    })}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      border: "none", borderRadius: 8, padding: "9px 16px",
                      background: "#b45309", color: "#fff", fontWeight: 700,
                      fontSize: 13, cursor: loading ? "default" : "pointer",
                      opacity: loading ? 0.7 : 1,
                    }}
                  >
                    <PlayCircle size={15} />
                    {loading ? "Connecting…" : "Join assigned scenario"}
                  </button>
                </div>
              ) : (
                <div style={{
                  display: "flex", alignItems: "center", gap: 8, margin: "0 0 12px",
                  padding: "10px 14px", borderRadius: 10, fontSize: 13.5, fontWeight: 600,
                  background: "#d1fae5", color: "#065f46",
                }}>
                  <Clock size={15} />
                  Your session window is live until {fmtSlotTime(access.live.end_at)} — pick a
                  customer below to start.
                </div>
              )
            )}
            <PresetGallery
              avatars={avatars} models={models}
              avatarsLoading={avatarsLoading} avatarsError={avatarsError}
              loading={loading} error={error}
              onLoadAvatars={loadAvatars}
              onJoinPreset={launchWithBooking}
              onCustom={() => setStep("form")}
              onCustomExisting={() => {
                // Pre-set the form to existing-customer follow-up before opening
                // the standard RoleplayForm so the call_type is correct from the start.
                setAll({ customer_type: "existing", call_type: "Follow-up Call (customer already inquired)" });
                setStep("form");
              }}
              onCustomEtb={() => setStep("etbForm")}
            />
          </>
        )
      ) : (
        <div className="rp-content-card">
          {step === "form" && (
            <>
              <button className="rp-back-link" onClick={() => setStep("presets")}>
                ← Back to preset customers
              </button>
              <RoleplayForm
                form={form} set={set} setAll={setAll}
                onSubmit={handleConfigureSubmit}
                loading={loading} error={error}
                canSubmit={canSubmit}
              />
            </>
          )}

          {step === "etbForm" && (
            <>
              <button className="rp-back-link" onClick={() => setStep("presets")}>
                ← Back to preset customers
              </button>
              <ETBServiceForm
                form={form} set={set} setAll={setAll}
                onSubmit={handleEtbSubmit}
                loading={loading} error={error}
                canSubmit={canEtbSubmit}
              />
            </>
          )}

          {/* Session auto-closed after idleTimeoutS seconds of user silence */}
          {step === "inactive" && (
            <div className="rpr-analyzing">
              <p className="rpr-analyzing-title">⏱ Session closed due to inactivity</p>
              <p className="rpr-analyzing-sub">
                No voice input was detected for {idleTimeoutS} seconds, so the
                avatar call was ended to save resources. Click Start to begin a
                new conversation.
              </p>
              <RecordingCard recorder={recorder} />
              <div className="rpr-analyzing-actions">
                <button className="rpr-btn-pdf" onClick={handleReset}>
                  ▶ Start New Conversation
                </button>
              </div>
            </div>
          )}

          {/* Report step: analysing loader → report card → error/retry */}
          {step === "report" && (
            report ? (
              <CallReport
                report={report}
                form={form}
                onReset={handleReset}
                onDownload={() => downloadReportPDF(report, form)}
                recordingSlot={<RecordingCard recorder={recorder} />}
              />
            ) : (
              <div className="rpr-analyzing">
                {analyzing ? (
                  <>
                    <div className="rp-call-spinner" />
                    <p className="rpr-analyzing-title">Analysing your call…</p>
                    <p className="rpr-analyzing-sub">
                      Grading the transcript across 7 parameters. This takes ~20 seconds.
                    </p>
                    <RecordingCard recorder={recorder} showEmpty={false} />
                  </>
                ) : (
                  <>
                    <p className="rpr-analyzing-title">Report not ready</p>
                    <p className="rpr-analyzing-sub">{analyzeError || reportError}</p>
                    <RecordingCard recorder={recorder} />
                    <div className="rpr-analyzing-actions">
                      <button className="rp-btn-secondary" onClick={handleReset}>
                        ← New Session
                      </button>
                      <button
                        className="rpr-btn-pdf"
                        onClick={() => runAnalysis(sessionId)}
                      >
                        ↻ Retry Analysis
                      </button>
                    </div>
                  </>
                )}
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
}
