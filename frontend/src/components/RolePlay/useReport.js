import { useState, useRef, useCallback } from "react";

export function useReport() {
  const [report, setReport]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");

  // HTTP status of the last failed attempt, in a ref so a caller can read it
  // synchronously right after `await generateReport(...)` (state would still be
  // the previous render's value). 404 = "transcript not ready yet" — the ONLY
  // retryable failure; every other one repeats deterministically and each retry
  // costs a full grading LLM call.
  const lastStatus = useRef(0);

  const generateReport = useCallback(async ({
    sessionId, prospectName, profession, loanType, scenario, localTranscript = "",
    callType = "", feedbackState = "",
  }) => {
    setLoading(true);
    setError("");
    lastStatus.current = 0;
    try {
      const r = await fetch("/api/roleplay/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id:       sessionId,
          prospect_name:    prospectName,
          profession,
          loan_type:        loanType,
          scenario,
          local_transcript: localTranscript,
          // Additive optional fields — let the backend grader switch to ETB criteria.
          call_type:        callType,
          feedback_state:   feedbackState,
        }),
      });
      lastStatus.current = r.status;
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      setReport(d.report);
      return d.report;
    } catch (e) {
      setError(e.message || "Failed to generate report.");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const clearReport = useCallback(() => {
    setReport(null);
    setError("");
    lastStatus.current = 0;
  }, []);

  return { report, loading, error, lastStatus, generateReport, clearReport };
}
