import React, { useState } from "react";

// ── Grade color helper ────────────────────────────────────
const GRADE_COLOR = {
  A: { bg: "#d1fae5", text: "#065f46", border: "#6ee7b7" },
  B: { bg: "#dbeafe", text: "#1e40af", border: "#93c5fd" },
  C: { bg: "#fef3c7", text: "#92400e", border: "#fcd34d" },
  D: { bg: "#fed7aa", text: "#9a3412", border: "#fb923c" },
  F: { bg: "#fee2e2", text: "#991b1b", border: "#fca5a5" },
};

const TAG_COLOR = {
  "GOOD":           { bg: "#d1fae5", text: "#065f46", dot: "#22c55e" },
  "NEEDS WORK":     { bg: "#fef3c7", text: "#92400e", dot: "#f59e0b" },
  "CRITICAL ERROR": { bg: "#fee2e2", text: "#991b1b", dot: "#ef4444" },
  "NEUTRAL":        { bg: "#f3f4f6", text: "#6b7280", dot: "#9ca3af" },
};

const PARAM_LABELS = {
  tone_professionalism:   "Tone & Professionalism",
  product_knowledge:      "Product Knowledge",
  objection_handling:     "Objection Handling",
  listening_empathy:      "Listening & Empathy",
  pitch_structure:        "Pitch Structure & Flow",
  closing_technique:      "Closing Technique",
  language_communication: "Language & Communication",
};

const PARAM_ICONS = {
  tone_professionalism:   "🎯",
  product_knowledge:      "📚",
  objection_handling:     "🛡️",
  listening_empathy:      "❤️",
  pitch_structure:        "📋",
  closing_technique:      "🔐",
  language_communication: "💬",
};

// ── Score bar ─────────────────────────────────────────────
function ScoreBar({ score }) {
  const pct = (Number(score) / 10) * 100;
  const color = score >= 8 ? "#22c55e" : score >= 6 ? "#f59e0b" : "#ef4444";
  return (
    <div className="rpr-bar-wrap">
      <div className="rpr-bar-track">
        <div className="rpr-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="rpr-bar-label">{score}/10</span>
    </div>
  );
}

// ── Parameter card ────────────────────────────────────────
function ParamCard({ paramKey, data }) {
  return (
    <div className="rpr-param-card">
      <div className="rpr-param-header">
        <span className="rpr-param-icon">{PARAM_ICONS[paramKey]}</span>
        <span className="rpr-param-name">{PARAM_LABELS[paramKey] || paramKey}</span>
      </div>
      <ScoreBar score={data.score} />
      <p className="rpr-param-verdict">{data.verdict}</p>
    </div>
  );
}

// ── Transcript line ───────────────────────────────────────
function TranscriptLine({ line }) {
  const isRep = line.speaker === "Rep";
  const tagStyle = isRep && line.tag ? TAG_COLOR[line.tag] : null;

  return (
    <div className={`rpr-line ${isRep ? "rpr-line--rep" : "rpr-line--customer"}`}>
      <div className="rpr-line-meta">
        <span className="rpr-line-ts">{line.timestamp}</span>
        <span className={`rpr-line-speaker ${isRep ? "rpr-speaker-rep" : "rpr-speaker-cust"}`}>
          {line.speaker}
        </span>
        {isRep && tagStyle && (
          <span className="rpr-line-tag" style={{ background: tagStyle.bg, color: tagStyle.text }}>
            <span className="rpr-tag-dot" style={{ background: tagStyle.dot }} />
            {line.tag}
          </span>
        )}
        {isRep && line.parameter && (
          <span className="rpr-line-param">
            {PARAM_LABELS[line.parameter] || line.parameter}
          </span>
        )}
      </div>
      <p className="rpr-line-text">{line.text}</p>
      {isRep && line.comment && (
        <p className="rpr-line-comment">💬 {line.comment}</p>
      )}
    </div>
  );
}

// ── Moment card ───────────────────────────────────────────
function MomentCard({ moment, type }) {
  const isBest = type === "best";
  return (
    <div className={`rpr-moment ${isBest ? "rpr-moment--best" : "rpr-moment--worst"}`}>
      <span className="rpr-moment-ts">{moment.timestamp}</span>
      <p className="rpr-moment-quote">"{moment.quote}"</p>
      <p className="rpr-moment-reason">{moment.reason}</p>
    </div>
  );
}

// ── Main report component ─────────────────────────────────
export default function CallReport({ report, form, onReset, onDownload, recordingSlot = null }) {
  const [activeTab, setActiveTab] = useState("overview");
  const gradeStyle = GRADE_COLOR[report.overall_grade] || GRADE_COLOR["C"];

  const lines    = Array.isArray(report.lines) ? report.lines : [];
  const repLines = lines.filter(l => l.speaker === "Rep");
  const criticalCount = repLines.filter(l => l.tag === "CRITICAL ERROR").length;
  const goodCount     = repLines.filter(l => l.tag === "GOOD").length;
  const needsCount    = repLines.filter(l => l.tag === "NEEDS WORK").length;

  const bestMoments  = report.best_moments  || [];
  const worstMoments = report.worst_moments || [];
  const coachingNotes = report.coaching_notes || [];

  return (
    <div className="rpr-wrap">

      {/* ── REPORT HEADER ── */}
      <div className="rpr-header">
        <div className="rpr-header-left">
          <h2 className="rpr-title">Call Performance Report</h2>
          <p className="rpr-meta">
            {form.prospect_name} · {form.profession} · {form.loan_type}
            {form.loan_amount ? ` · ₹${form.loan_amount}L` : ""}
            · Duration: {report.call_duration}
          </p>
        </div>
        <div className="rpr-grade-badge"
          style={{ background: gradeStyle.bg, color: gradeStyle.text,
                   border: `2px solid ${gradeStyle.border}` }}>
          <span className="rpr-grade-letter">{report.overall_grade}</span>
          <span className="rpr-grade-score">{report.overall_score}/100</span>
        </div>
      </div>

      {/* ── QUICK STATS ROW ── */}
      <div className="rpr-stats-row">
        <div className="rpr-stat rpr-stat--good">
          <span className="rpr-stat-num">{goodCount}</span>
          <span className="rpr-stat-label">Good Moments</span>
        </div>
        <div className="rpr-stat rpr-stat--warn">
          <span className="rpr-stat-num">{needsCount}</span>
          <span className="rpr-stat-label">Needs Work</span>
        </div>
        <div className="rpr-stat rpr-stat--crit">
          <span className="rpr-stat-num">{criticalCount}</span>
          <span className="rpr-stat-label">Critical Errors</span>
        </div>
        <div className="rpr-stat rpr-stat--neutral">
          <span className="rpr-stat-num">{repLines.length}</span>
          <span className="rpr-stat-label">Rep Lines Total</span>
        </div>
      </div>

      {/* ── TABS ── */}
      <div className="rpr-tabs">
        {["overview", "transcript", "moments", "coaching"].map(t => (
          <button key={t}
            className={`rpr-tab ${activeTab === t ? "rpr-tab--active" : ""}`}
            onClick={() => setActiveTab(t)}>
            {{ overview: "📊 Overview", transcript: "📝 Transcript",
               moments: "⚡ Key Moments", coaching: "🎓 Coaching" }[t]}
          </button>
        ))}
      </div>

      {/* ── TAB: OVERVIEW ── */}
      {activeTab === "overview" && (
        <div className="rpr-tab-content">
          <p className="rpr-section-title">Parameter Scores</p>
          <div className="rpr-params-grid">
            {Object.entries(report.parameters || {}).map(([key, val]) => (
              <ParamCard key={key} paramKey={key} data={val} />
            ))}
          </div>

          {/* ── Pitch Coverage — only params NOT already in the 7-param report above:
               Opening and Discovery are new; Product Pitch adds element-level coverage
               (rate / fee / tenure / EMI). Objection Handling and Closing are already
               covered by report.parameters.objection_handling & closing_technique. ── */}
          {report.pitch_parameters && Object.keys(report.pitch_parameters).length > 0 && (
            <>
              <p className="rpr-section-title" style={{ marginTop: 28 }}>
                Call Coverage
                <span className="rpr-section-note">opening · discovery · product elements — from Whisper transcript</span>
              </p>
              <div className="rpr-pitch-grid">
                {[
                  {
                    id: "opening",
                    label: "Opening",
                    hint: "Name · HDFC mention · Permission asked",
                  },
                  {
                    id: "discovery",
                    label: "Discovery",
                    hint: "Purpose · Amount · Income · Obligations",
                  },
                  {
                    id: "product_pitch",
                    label: "Product Elements",
                    hint: "Rate · Fee · Tenure · EMI",
                  },
                ].map(({ id, label, hint }) => {
                  const p = report.pitch_parameters[id];
                  if (!p) return null;
                  const pct = Math.round((p.score / 10) * 100);
                  const color = p.met ? "#22c55e" : p.score >= 4 ? "#f59e0b" : "#ef4444";
                  return (
                    <div key={id} className="rpr-pitch-card">
                      <div className="rpr-pitch-top">
                        <span className="rpr-pitch-label">{label}</span>
                        <span className="rpr-pitch-score" style={{ color }}>{p.score}/10</span>
                      </div>
                      <p className="rpr-pitch-hint">{hint}</p>
                      <div className="rpr-pitch-bar-bg">
                        <div className="rpr-pitch-bar-fill" style={{ width: `${pct}%`, background: color }} />
                      </div>
                      <span className={`rpr-pitch-badge ${p.met ? "rpr-pitch-badge--met" : "rpr-pitch-badge--missed"}`}>
                        {p.met ? "✓ Met" : "✗ Missed"}
                      </span>
                      <p className="rpr-pitch-verdict">{p.verdict}</p>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          <p className="rpr-section-title" style={{ marginTop: 28 }}>
            Overall Verdict
          </p>
          <div className="rpr-verdict-box">
            <p className="rpr-verdict-text">{report.overall_verdict}</p>
          </div>
        </div>
      )}

      {/* ── TAB: TRANSCRIPT ── */}
      {activeTab === "transcript" && (
        <div className="rpr-tab-content">
          <p className="rpr-section-title">
            Line-by-Line Breakdown
            <span className="rpr-section-note">
              Rep lines are scored · Customer lines shown for context
            </span>
          </p>

          {/* Tag legend */}
          <div className="rpr-legend">
            {Object.entries(TAG_COLOR).map(([tag, style]) => (
              <span key={tag} className="rpr-legend-item">
                <span className="rpr-tag-dot" style={{ background: style.dot }} />
                {tag}
              </span>
            ))}
          </div>

          <div className="rpr-transcript">
            {lines.map((line, i) => (
              <TranscriptLine key={i} line={line} />
            ))}
          </div>
        </div>
      )}

      {/* ── TAB: KEY MOMENTS ── */}
      {activeTab === "moments" && (
        <div className="rpr-tab-content">
          <div className="rpr-moments-cols">
            <div>
              <p className="rpr-section-title rpr-section-title--good">
                ✅ Top 3 Best Moments
              </p>
              {bestMoments.map((m, i) => (
                <MomentCard key={i} moment={m} type="best" />
              ))}
            </div>
            <div>
              <p className="rpr-section-title rpr-section-title--bad">
                ❌ Top 3 Worst Moments
              </p>
              {worstMoments.map((m, i) => (
                <MomentCard key={i} moment={m} type="worst" />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── TAB: COACHING ── */}
      {activeTab === "coaching" && (
        <div className="rpr-tab-content">
          <p className="rpr-section-title">Manager Coaching Notes</p>
          <p className="rpr-section-subtitle">
            Specific to this call — actionable improvement points
          </p>
          <div className="rpr-coaching-list">
            {coachingNotes.map((note, i) => (
              <div key={i} className="rpr-coaching-item">
                <span className="rpr-coaching-num">{i + 1}</span>
                <p className="rpr-coaching-text">{note}</p>
              </div>
            ))}
          </div>

          <p className="rpr-section-title" style={{ marginTop: 28 }}>
            Overall Verdict
          </p>
          <div className="rpr-verdict-box">
            <p className="rpr-verdict-text">{report.overall_verdict}</p>
          </div>
        </div>
      )}

      {/* ── DOWNLOADS + ACTIONS ── */}
      {recordingSlot}
      <div className="rpr-actions">
        <button className="rp-btn-secondary" onClick={onReset}>
          ← New Session
        </button>
        <button className="rpr-btn-pdf" onClick={onDownload}>
          ⬇ Download PDF Report
        </button>
      </div>
    </div>
  );
}
