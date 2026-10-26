import React from "react";

export default function PromptPreview({ prompt, onLaunch, onBack, loading, error }) {
  return (
    <div className="rp-preview-wrap">
      <p className="rp-group-title">Assembled System Prompt</p>
      <p className="rp-group-hint">
        This is exactly what the avatar will receive as its system prompt.
        Review it before launching the session.
      </p>

      <div className="rp-prompt-box">
        <pre className="rp-prompt-text">{prompt}</pre>
      </div>

      {error && <div className="rp-error">{error}</div>}

      <div className="rp-action-row">
        <button className="rp-btn-secondary"
          onClick={onBack} disabled={loading}>
          ← Edit Form
        </button>
        <button className="rp-btn-primary"
          onClick={onLaunch} disabled={loading}>
          {loading ? "Launching…" : "Launch Session →"}
        </button>
      </div>
    </div>
  );
}
