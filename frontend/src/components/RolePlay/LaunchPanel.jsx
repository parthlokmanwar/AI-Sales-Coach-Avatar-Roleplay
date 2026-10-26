import React from "react";

const SUMMARY_FIELDS = [
  ["Prospect",   f => f.prospect_name],
  ["Profession", f => f.profession],
  ["Age Group",  f => f.age],
  ["City",       f => f.city],
  ["Loan Type",  f => f.loan_type],
  ["Amount",     f => f.loan_amount ? `₹${f.loan_amount}L` : "—"],
  ["CIBIL",      f => f.cibil],
  ["Competitor", f => f.competitor],
];

export default function LaunchPanel({ form, sessionUrl, onReset }) {
  const copy = () => navigator.clipboard.writeText(sessionUrl);

  return (
    <div className="rp-launch-wrap">

      {/* SUCCESS BANNER */}
      <div className="rp-success-banner">
        <span className="rp-check-icon">✓</span>
        <div>
          <p className="rp-group-title" style={{ margin: 0 }}>Session Ready</p>
          <p className="rp-group-hint" style={{ margin: "4px 0 0" }}>
            Share the link below with your sales rep to begin the practice call.
          </p>
        </div>
      </div>

      {/* SESSION SUMMARY */}
      <div className="rp-summary-card">
        <p className="rp-summary-section-label">Session Summary</p>
        <div className="rp-summary-grid">
          {SUMMARY_FIELDS.map(([label, getter]) => (
            <div key={label} className="rp-summary-item">
              <span className="rp-summary-key">{label}</span>
              <span className="rp-summary-val">{getter(form) || "—"}</span>
            </div>
          ))}
        </div>
        <div className="rp-scenario-block">
          <span className="rp-summary-key">Scenario</span>
          <p className="rp-scenario-text">{form.scenario}</p>
        </div>
      </div>

      {/* SESSION LINK */}
      <div className="rp-group">
        <p className="rp-group-title">Avatar Session Link</p>
        <div className="rp-link-row">
          <input
            className="rp-input rp-link-readonly"
            type="text"
            readOnly
            value={sessionUrl}
          />
          <button className="rp-btn-copy" onClick={copy}>Copy</button>
        </div>
        <a
          className="rp-open-link"
          href={sessionUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open in New Tab →
        </a>
      </div>

      <button className="rp-btn-secondary" onClick={onReset}>
        ← Start New Session
      </button>
    </div>
  );
}
