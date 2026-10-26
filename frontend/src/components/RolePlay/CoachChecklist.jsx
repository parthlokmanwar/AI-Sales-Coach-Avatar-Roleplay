import React, { useState, useRef, useEffect } from "react";
import { Check, X, ChevronDown, ChevronUp, MicOff, FileText } from "lucide-react";
import { COACH_SECTIONS, useLiveCoach } from "./liveCoach";

// Descriptions shown inside each row (collapsed into the heading as requested)
const PARAM_DESC = {
  opening:            "Greeting · Permission · Purpose",
  discovery:          "Loan purpose · Amount · Income · CIBIL",
  product_pitch:      "Interest rate · Fee · Tenure · EMI",
  objection_handling: "Acknowledge · Stay calm · Offer solution",
  closing:            "Next step · Confirm follow-up · End professionally",
};

// micTrack (optional) — the live room's own mic track. Passing it stops this
// component opening a SECOND capture of the same device, which iOS Safari
// handles badly (the newer capture interrupts the older one).
export default function CoachChecklist({ active, form, transcriptRef, sessionId, onActivity, onSttState, micTrack }) {
  const { status, supported, listening, metCount, transcript } =
    useLiveCoach(active, form, transcriptRef, { sessionId, onActivity, micTrack });
  const [open, setOpen] = useState(true);
  const [txOpen, setTxOpen] = useState(true);
  const txBodyRef = useRef(null);

  // Tell the inactivity timer whether the mic pipeline actually works — if it
  // doesn't (permission denied / unsupported browser) the timer must stay
  // disarmed, otherwise it would cut off a user who IS talking to the avatar.
  useEffect(() => {
    onSttState?.(supported);
  }, [supported, onSttState]);

  // Auto-scroll the transcript to the newest line as it grows.
  useEffect(() => {
    if (txBodyRef.current) txBodyRef.current.scrollTop = txBodyRef.current.scrollHeight;
  }, [transcript]);

  const total = COACH_SECTIONS.length;

  return (
    <>
    <div className={"rp-coach" + (open ? "" : " rp-coach--collapsed")}>

      {/* Header */}
      <div className="rp-coach-head" onClick={() => setOpen(o => !o)}>
        <span className="rp-coach-title">
          <span className={"rp-coach-dot" + (supported && listening ? " rp-coach-dot--live" : "")} />
          Pitch Checklist
        </span>
        <span className="rp-coach-count">{metCount}/{total}</span>
        {open ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
      </div>

      {open && (
        <div className="rp-coach-body">
          {!supported && (
            <p className="rp-coach-note">
              <MicOff size={12} style={{ flexShrink: 0 }} />
              Live detection unavailable — allow mic access and use Chrome/Edge.
            </p>
          )}

          {COACH_SECTIONS.map(sec => {
            const s       = status[sec.id];
            const isMet    = s === "met";
            const isMissed = s === "missed";

            return (
              <div
                key={sec.id}
                className={
                  "rp-coach-item rp-coach-item--cat" +
                  (isMet ? " rp-coach-item--done" : isMissed ? " rp-coach-item--missed" : "")
                }
              >
                {/* Status indicator */}
                <span className={
                  "rp-coach-box" +
                  (isMet ? " rp-coach-box--done" : isMissed ? " rp-coach-box--missed" : "")
                }>
                  {isMet    && <Check size={11} />}
                  {isMissed && <X    size={11} />}
                  {!isMet && !isMissed && <span className="rp-coach-spin" />}
                </span>

                {/* Label + sub-description */}
                <span className="rp-coach-label-wrap">
                  <span className="rp-coach-label">{sec.title}</span>
                  <span className="rp-coach-sub">{PARAM_DESC[sec.id]}</span>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>

    {/* Live agent transcript — what YOU (the rep) say, via local Whisper */}
    <div className={"rp-tx" + (txOpen ? "" : " rp-tx--collapsed")}>
      <div className="rp-tx-head" onClick={() => setTxOpen(o => !o)}>
        <span className="rp-tx-title">
          <FileText size={14} style={{ flexShrink: 0 }} />
          Your Transcript
          <span className={"rp-coach-dot" + (supported && listening ? " rp-coach-dot--live" : "")} />
        </span>
        {txOpen ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
      </div>
      {txOpen && (
        <div className="rp-tx-body" ref={txBodyRef}>
          {transcript
            ? <p className="rp-tx-text">{transcript}</p>
            : <p className="rp-tx-empty">
                {supported
                  ? listening
                    ? "Listening… start speaking"
                    : "Allow mic access when the browser asks…"
                  : "Use Chrome or Edge to see your live transcript."}
              </p>}
        </div>
      )}
    </div>
    </>
  );
}
