import React, { useState, useEffect } from "react";
import { Sparkles } from "lucide-react";
import ProfessionFields from "./ProfessionFields";
import {
  PROFESSIONS, GENDERS, AGE_GROUPS, LOAN_TYPES,
  CIBIL_OPTIONS, COMPETITOR_OPTIONS,
  CITY_TIERS, TONES, KNOWLEDGE_LEVELS, DECISION_STYLES, TRUST_LEVELS,
  INCOME_SITUATIONS, CALL_TYPES, DIFFICULTY_LEVELS,
} from "./formOptions";
import { LOCKED_AGE } from "./avatarMap";

// Small helper for a labelled dropdown
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

export default function RoleplayForm({ form, set, setAll, onSubmit, loading, error, canSubmit }) {
  const [genLoading, setGenLoading]   = useState(false);
  const [narrLoading, setNarrLoading] = useState(false);
  const [genError, setGenError]       = useState("");

  // Some professions have a fixed age (Retired → 50+, Student → 25-35).
  const lockedAge = LOCKED_AGE[form.profession];
  const ageOptions = lockedAge ? [lockedAge] : AGE_GROUPS;

  // Enforce the locked age whenever the profession demands it.
  useEffect(() => {
    if (lockedAge && form.age !== lockedAge) set("age", lockedAge);
  }, [lockedAge, form.age, set]);

  // Auto-fill the ENTIRE form with a random, LLM-generated prospect.
  const handleGenerateAll = async () => {
    setGenLoading(true); setGenError("");
    try {
      const r = await fetch("/api/roleplay/generate", { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      if (d.form && setAll) setAll(d.form);
    } catch (e) {
      setGenError(e.message || "Failed to generate.");
    } finally {
      setGenLoading(false);
    }
  };

  // Write a full scenario narrative from the fields chosen above.
  const handleGenerateNarrative = async () => {
    setNarrLoading(true); setGenError("");
    try {
      const r = await fetch("/api/roleplay/generate-narrative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
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
          {genLoading ? "Generating…" : "Generate full profile"}
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

      {/* ── SECTION 1 — CUSTOMER PROFILE (BASIC) ── */}
      <div className="rp-group">
        <p className="rp-group-title">Section 1 — Customer Profile</p>

        <div className="rp-field">
          <label className="rp-label">Prospect Name</label>
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

      {/* ── SECTION 2 — PERSONALITY & BEHAVIOR ── */}
      <div className="rp-group">
        <p className="rp-group-title">Section 2 — Personality &amp; Behavior</p>
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
      </div>

      {/* ── SECTION 3 — LOAN DETAILS ── */}
      <div className="rp-group">
        <p className="rp-group-title">Section 3 — Loan Details</p>

        <div className="rp-row-2">
          <Select label="Loan Type" value={form.loan_type}
            onChange={e => set("loan_type", e.target.value)} options={LOAN_TYPES} />
          <div className="rp-field">
            <label className="rp-label">Loan Amount (Lakhs)</label>
            <input className="rp-input" type="text"
              placeholder="e.g. 10"
              value={form.loan_amount}
              onChange={e => set("loan_amount", e.target.value)} />
          </div>
        </div>

        <div className="rp-field">
          <label className="rp-label">Loan Purpose</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Clinic equipment upgrade"
            value={form.loan_purpose}
            onChange={e => set("loan_purpose", e.target.value)} />
        </div>

        <div className="rp-row-2">
          <Select label="CIBIL Situation" value={form.cibil}
            onChange={e => set("cibil", e.target.value)} options={CIBIL_OPTIONS} />
          <Select label="Income Situation" value={form.income_situation}
            onChange={e => set("income_situation", e.target.value)} options={INCOME_SITUATIONS} />
        </div>
      </div>

      {/* ── SECTION 4 — SCENARIO TYPE ── */}
      <div className="rp-group">
        <p className="rp-group-title">Section 4 — Scenario Type</p>
        <div className="rp-row-2">
          <Select label="Call Type" value={form.call_type}
            onChange={e => set("call_type", e.target.value)} options={CALL_TYPES} />
          <Select label="Difficulty Level" value={form.difficulty}
            onChange={e => set("difficulty", e.target.value)} options={DIFFICULTY_LEVELS} />
        </div>
        <div className="rp-row-2">
          <div className="rp-field">
            <label className="rp-label">Competitor to Mention</label>
            <select className="rp-select" value={form.competitor}
              onChange={e => set("competitor", e.target.value)}>
              {COMPETITOR_OPTIONS.map(c =>
                <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          {form.competitor !== "none" && (
            <div className="rp-field">
              <label className="rp-label">Competitor Rate Customer Will Cite</label>
              <input className="rp-input" type="text"
                placeholder="e.g. 10.5%"
                value={form.competitor_rate}
                onChange={e => set("competitor_rate", e.target.value)} />
            </div>
          )}
        </div>
      </div>

      {/* ── SECTION 5 — MANAGER CUSTOM CONTEXT ── */}
      <div className="rp-group">
        <p className="rp-group-title">Section 5 — Manager Custom Context</p>
        <p className="rp-group-hint">
          Optional free-text controls. Anything you write here the avatar treats
          as true and acts on. Leave blank to skip.
        </p>

        <div className="rp-field">
          <label className="rp-label">Special Situation</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Previous loan was rejected by HDFC 6 months ago — still upset"
            value={form.special_situation}
            onChange={e => set("special_situation", e.target.value)} />
        </div>
        <div className="rp-field">
          <label className="rp-label">Key Objection</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Will strongly resist any mention of processing fees"
            value={form.key_objection}
            onChange={e => set("key_objection", e.target.value)} />
        </div>
        <div className="rp-field">
          <label className="rp-label">Hidden Information</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Actually has the money but is testing the rep's patience"
            value={form.hidden_information}
            onChange={e => set("hidden_information", e.target.value)} />
        </div>
        <div className="rp-field">
          <label className="rp-label">Training Focus</label>
          <input className="rp-input" type="text"
            placeholder="e.g. Wants the rep to practise closing techniques specifically"
            value={form.training_focus}
            onChange={e => set("training_focus", e.target.value)} />
        </div>
      </div>

      {/* ── FULL SCENARIO ── */}
      <div className="rp-group">
        <p className="rp-group-title">Full Scenario</p>
        <p className="rp-group-hint">
          Write the customer's full situation — or hit Generate to have AI write
          it from the details you picked above. The avatar behaves entirely from this.
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
              "e.g. Rajesh is a 42-year-old cardiologist whose loan application was " +
              "rejected by HDFC 6 months ago due to a documentation error. He is still " +
              "irritated but secretly needs Rs. 20L urgently for new equipment. He will " +
              "not show urgency — he will make the rep work for it. He has been quoted " +
              "11% by Bajaj Finserv and will mention this. His real concern is speed."
            }
            value={form.scenario}
            onChange={e => set("scenario", e.target.value)}
          />
        </div>
      </div>

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
