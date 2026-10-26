import React, { useState, useEffect } from "react";
import { Sparkles, ThumbsUp, Minus, ThumbsDown } from "lucide-react";
import ProfessionFields from "./ProfessionFields";
import {
  PROFESSIONS, GENDERS, AGE_GROUPS, LOAN_TYPES,
  CITY_TIERS, TONES, KNOWLEDGE_LEVELS, DECISION_STYLES, TRUST_LEVELS,
  DIFFICULTY_LEVELS, FEEDBACK_STATES, SERVICE_ISSUES,
} from "./formOptions";
import { LOCKED_AGE } from "./avatarMap";

// Small helper for a labelled dropdown (mirrors RoleplayForm.jsx)
function Select({ label, value, onChange, options, placeholder = "Select" }) {
  return (
    <div className="rp-field">
      <label className="rp-label">{label}</label>
      <select className="rp-select" value={value} onChange={onChange}>
        <option value="">{placeholder}</option>
        {options.map(o =>
          typeof o === "string"
            ? <option key={o}>{o}</option>
            : <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

// Three-card "segmented control" for the existing customer's current satisfaction.
const FEEDBACK_CARDS = [
  { value: "Happy",     Icon: ThumbsUp, hint: "Satisfied — open to the offer" },
  { value: "Neutral",   Icon: Minus,    hint: "Indifferent — needs a reason to care" },
  { value: "Complaint", Icon: ThumbsDown, hint: "Has a live grievance to handle first" },
];

export default function ETBServiceForm({ form, set, setAll, onSubmit, loading, error, canSubmit }) {
  const [narrLoading, setNarrLoading] = useState(false);
  const [genLoading, setGenLoading]   = useState(false);
  const [genError, setGenError]       = useState("");
  // Tracks whether the user selected "Other issue" from the dropdown. Kept
  // separate from form.service_issue so the custom text input stays visible
  // while the user is typing (setting form.service_issue to the typed text
  // must not collapse the input).
  const [otherIssue, setOtherIssue] = useState(false);

  // Some professions have a fixed age (Retired → 50+, Student → 25-35).
  const lockedAge = LOCKED_AGE[form.profession];
  const ageOptions = lockedAge ? [lockedAge] : AGE_GROUPS;

  // Enforce the locked age whenever the profession demands it.
  useEffect(() => {
    if (lockedAge && form.age !== lockedAge) set("age", lockedAge);
  }, [lockedAge, form.age, set]);

  // Reset otherIssue flag when the feedback state changes away from Complaint.
  useEffect(() => {
    if (form.feedback_state !== "Complaint") setOtherIssue(false);
  }, [form.feedback_state]);

  const isComplaint = form.feedback_state === "Complaint";

  // Auto-fill the ENTIRE ETB form with a random AI-generated existing customer.
  const handleGenerateAll = async () => {
    setGenLoading(true); setGenError("");
    try {
      const r = await fetch("/api/roleplay/generate-etb", { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      if (d.form && setAll) {
        setAll(d.form);
        // Sync the otherIssue flag with the generated service_issue
        setOtherIssue(false);
      }
    } catch (e) {
      setGenError(e.message || "Failed to generate sample.");
    } finally {
      setGenLoading(false);
    }
  };

  // Write a full scenario narrative from the fields chosen above (reuses the same
  // endpoint as RoleplayForm — the backend reads call_type/feedback_state too).
  const handleGenerateNarrative = async () => {
    setNarrLoading(true); setGenError("");
    try {
      const r = await fetch("/api/roleplay/generate-narrative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          customer_type: "existing",
          call_type: "Service Call (customer has a problem first)",
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      if (d.scenario) set("scenario", d.scenario);
    } catch (e) {
      setGenError(e.message || "Failed to generate scenario.");
    } finally {
      setNarrLoading(false);
    }
  };

  return (
    <div className="rp-form-wrap">

      {/* ── AUTO-GENERATE FULL PROFILE ── */}
      <div className="rp-gen-bar">
        <button
          type="button"
          className="rp-gen-btn"
          onClick={handleGenerateAll}
          disabled={genLoading}
        >
          <Sparkles size={15} />
          {genLoading ? "Generating…" : "Generate sample pitch"}
        </button>
      </div>
      {genError && <div className="rp-error">{genError}</div>}

      {/* ── CONVERSATION LANGUAGE ── */}
      <div className="rp-group">
        <p className="rp-group-title">Conversation Language</p>
        <p className="rp-group-hint">
          The language the avatar customer speaks on the call. Hindi uses a
          natural-Hinglish voice; English is the default.
        </p>
        <div className="rp-lang-toggle">
          {[
            { value: "english", label: "English" },
            { value: "hindi",   label: "हिंदी / Hindi" },
          ].map(opt => (
            <button
              key={opt.value}
              type="button"
              className={
                "rp-lang-btn" +
                ((form.language || "english") === opt.value ? " rp-lang-btn--active" : "")
              }
              onClick={() => set("language", opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── HOW THEY FEEL (feedback_state) ── */}
      <div className="rp-group">
        <p className="rp-group-title">How They Feel About HDFC</p>
        <p className="rp-group-hint">
          You (the RM) are cold-calling an EXISTING customer about a pre-approved
          offer they did not ask for. Pick their current state — a Complaint must be
          handled before they'll hear the offer.
        </p>
        <div className="rp-etb-feedback">
          {FEEDBACK_CARDS.map(({ value, Icon, hint }) => (
            <button
              key={value}
              type="button"
              className={
                "rp-etb-fb-card rp-etb-fb-card--" + value.toLowerCase() +
                (form.feedback_state === value ? " rp-etb-fb-card--active" : "")
              }
              onClick={() => set("feedback_state", value)}
            >
              <span className="rp-etb-fb-icon"><Icon size={22} /></span>
              <span className="rp-etb-fb-name">{value}</span>
              <span className="rp-etb-fb-hint">{hint}</span>
            </button>
          ))}
        </div>

        {/* Complaint-only: which grievance + their main objection */}
        {isComplaint && (
          <div className="rp-profession-extra" style={{ marginTop: 16 }}>
            <p className="rp-field-group-label">Service Grievance</p>
            <Select
              label="Service Issue"
              value={otherIssue ? "Other issue" : form.service_issue}
              onChange={e => {
                const val = e.target.value;
                if (val === "Other issue") {
                  setOtherIssue(true);
                  set("service_issue", "");
                } else {
                  setOtherIssue(false);
                  set("service_issue", val);
                }
              }}
              options={SERVICE_ISSUES}
              placeholder="Select a grievance"
            />
            {otherIssue && (
              <div className="rp-field">
                <label className="rp-label">Describe the Issue</label>
                <input className="rp-input" type="text"
                  placeholder="e.g. Loan statement shows a charge I never authorised"
                  value={form.service_issue}
                  onChange={e => set("service_issue", e.target.value)} />
              </div>
            )}
            <div className="rp-field">
              <label className="rp-label">Main Objection</label>
              <input className="rp-input" type="text"
                placeholder="e.g. Why sell me a loan when my last complaint is still open?"
                value={form.main_objection}
                onChange={e => set("main_objection", e.target.value)} />
            </div>
          </div>
        )}
      </div>

      {/* ── THE OFFER & RELATIONSHIP ── */}
      <div className="rp-group">
        <p className="rp-group-title">Relationship &amp; Pre-approved Offer</p>
        <div className="rp-row-2">
          <div className="rp-field">
            <label className="rp-label">Existing Facility</label>
            <input className="rp-input" type="text"
              placeholder="e.g. Salary account + HDFC credit card"
              value={form.existing_facility}
              onChange={e => set("existing_facility", e.target.value)} />
          </div>
          <div className="rp-field">
            <label className="rp-label">Relationship Duration</label>
            <input className="rp-input" type="text"
              placeholder="e.g. 8 years"
              value={form.relationship_duration}
              onChange={e => set("relationship_duration", e.target.value)} />
          </div>
        </div>
        <div className="rp-row-2">
          <div className="rp-field">
            <label className="rp-label">Offer Type</label>
            <input className="rp-input" type="text"
              placeholder="e.g. Pre-approved personal loan"
              value={form.offer_type}
              onChange={e => set("offer_type", e.target.value)} />
          </div>
          <div className="rp-field">
            <label className="rp-label">Offer Amount</label>
            <input className="rp-input" type="text"
              placeholder="e.g. ₹10 lakhs"
              value={form.offer_amount}
              onChange={e => set("offer_amount", e.target.value)} />
          </div>
        </div>
        <div className="rp-row-2">
          <Select label="Loan Type" value={form.loan_type}
            onChange={e => set("loan_type", e.target.value)} options={LOAN_TYPES} />
          <div className="rp-field">
            <label className="rp-label">Loan Purpose (if they consider it)</label>
            <input className="rp-input" type="text"
              placeholder="e.g. Clinic equipment upgrade"
              value={form.loan_purpose}
              onChange={e => set("loan_purpose", e.target.value)} />
          </div>
        </div>
      </div>

      {/* ── CUSTOMER PROFILE (BASIC) ── */}
      <div className="rp-group">
        <p className="rp-group-title">Customer Profile</p>

        <div className="rp-field">
          <label className="rp-label">Customer Name</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Rajesh Mehta"
            value={form.prospect_name}
            onChange={e => set("prospect_name", e.target.value)} />
        </div>

        <div className="rp-row-2">
          <Select label="Gender" value={form.gender}
            onChange={e => set("gender", e.target.value)} options={GENDERS} />
          <Select label="Age Group" value={form.age}
            onChange={e => set("age", e.target.value)} options={ageOptions} />
        </div>

        <div className="rp-row-2">
          <Select label="Profession" value={form.profession}
            onChange={e => set("profession", e.target.value)} options={PROFESSIONS} />
          <Select label="City Tier" value={form.city_tier}
            onChange={e => set("city_tier", e.target.value)} options={CITY_TIERS} />
        </div>

        <div className="rp-row-2">
          <div className="rp-field">
            <label className="rp-label">City</label>
            <input className="rp-input" type="text"
              placeholder="e.g. Mumbai"
              value={form.city}
              onChange={e => set("city", e.target.value)} />
          </div>
          <div className="rp-field">
            <label className="rp-label">Years of Experience</label>
            <input className="rp-input" type="text"
              placeholder="e.g. 12"
              value={form.years_experience}
              onChange={e => set("years_experience", e.target.value)} />
          </div>
        </div>

        <div className="rp-field">
          <label className="rp-label">Monthly Income</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Rs. 1.2 lakhs"
            value={form.income}
            onChange={e => set("income", e.target.value)} />
        </div>

        {/* Profession-specific fields — only renders when profession is selected */}
        {form.profession && (
          <ProfessionFields profession={form.profession} form={form} set={set} />
        )}
      </div>

      {/* ── PERSONALITY & BEHAVIOR ── */}
      <div className="rp-group">
        <p className="rp-group-title">Personality &amp; Behavior</p>
        <div className="rp-row-2">
          <Select label="Tone" value={form.tone}
            onChange={e => set("tone", e.target.value)} options={TONES} />
          <Select label="Knowledge Level" value={form.knowledge_level}
            onChange={e => set("knowledge_level", e.target.value)} options={KNOWLEDGE_LEVELS} />
        </div>
        <div className="rp-row-2">
          <Select label="Decision Style" value={form.decision_style}
            onChange={e => set("decision_style", e.target.value)} options={DECISION_STYLES} />
          <Select label="Trust Level" value={form.trust_level}
            onChange={e => set("trust_level", e.target.value)} options={TRUST_LEVELS} />
        </div>
        <div className="rp-row-2">
          <Select label="Difficulty Level" value={form.difficulty}
            onChange={e => set("difficulty", e.target.value)} options={DIFFICULTY_LEVELS} />
          <div className="rp-field" />
        </div>
      </div>

      {/* ── FULL SCENARIO ── */}
      <div className="rp-group">
        <p className="rp-group-title">Full Scenario</p>
        <p className="rp-group-hint">
          Write the existing customer's full situation — who called whom, their
          satisfaction/grievance, the unsolicited pre-approved offer, and how wary
          they are. Or hit Generate to draft it from the details above.
        </p>
        <div className="rp-textarea-wrap">
          <button
            type="button"
            className="rp-gen-btn rp-gen-btn--corner"
            onClick={handleGenerateNarrative}
            disabled={narrLoading}
            title="Generate a full scenario from the fields above"
          >
            <Sparkles size={14} />
            {narrLoading ? "Generating…" : "Generate"}
          </button>
          <textarea
            className="rp-textarea"
            rows={6}
            placeholder={
              "e.g. EXISTING HDFC CUSTOMER — SERVICE CALL. You (the rep) cold-called " +
              "this customer; they did NOT ask for a loan. They have banked with HDFC " +
              "for years and are <happy / neutral / aggrieved>. HDFC is offering a " +
              "pre-approved loan they weren't seeking. They are wary of being sold to " +
              "and (if a complaint) want their grievance handled before hearing the offer."
            }
            value={form.scenario}
            onChange={e => set("scenario", e.target.value)}
          />
        </div>
      </div>

      {genError && <div className="rp-error">{genError}</div>}
      {error && <div className="rp-error">{error}</div>}

      <button
        className="rp-btn-primary"
        onClick={onSubmit}
        disabled={loading || !canSubmit}
      >
        {loading ? "Connecting…" : "Start Call →"}
      </button>
    </div>
  );
}
