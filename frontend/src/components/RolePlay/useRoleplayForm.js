import { useState, useCallback } from "react";

const EMPTY = {
  prospect_name: "", gender: "", age: "", city: "", city_tier: "",
  profession: "", years_experience: "", income: "",
  // Personality & behavior
  tone: "", knowledge_level: "", decision_style: "", trust_level: "",
  loan_type: "", loan_amount: "", loan_purpose: "",
  cibil: "", income_situation: "", competitor: "none", competitor_rate: "",
  // Scenario type
  call_type: "", difficulty: "",
  // Manager custom context
  special_situation: "", key_objection: "", hidden_information: "", training_focus: "",
  scenario: "",
  specialization: "", teacher_type: "", school_college_type: "",
  business_type: "", turnover: "", current_bank: "",
  govt_dept: "", govt_designation: "", years_to_retirement: "",
  spouse_profession: "", spouse_income: "",
  self_employed_type: "",
  retired_from: "", years_since_retirement: "", pension_amount: "",
  student_type: "", field_of_study: "", institution_type: "",
  family_income: "",
  service_issue: "", main_objection: "",
  // Existing-customer SERVICE-CALL (ETB) fields
  feedback_state: "", existing_facility: "", relationship_duration: "",
  offer_type: "", offer_amount: "",
  // Avatar prompt selector ("heckler" — set only by the heckler preset card)
  avatar_type: "",
  // Conversation language ("english" | "hindi")
  language: "english",
  // Avatar config — chosen on the Avatar step
  replica_id: "", model: "",
};

const PROFESSION_SPECIFIC_KEYS = [
  "specialization", "teacher_type", "school_college_type",
  "business_type", "turnover", "current_bank",
  "govt_dept", "govt_designation", "years_to_retirement",
  "spouse_profession", "spouse_income", "self_employed_type",
  "retired_from", "years_since_retirement", "pension_amount",
  "student_type", "field_of_study", "institution_type", "family_income",
];

export function useRoleplayForm() {
  const [form, setForm]       = useState(EMPTY);
  const [sessionUrl, setUrl]  = useState("");
  const [sessionId, setId]    = useState("");
  // Inactivity timeout — comes from the backend (/launch response) so the
  // central ROLEPLAY_IDLE_TIMEOUT_S env var drives both frontend and server.
  const [idleTimeoutS, setIdleTimeoutS] = useState(40);
  // "presets" | "form" | "avatar" | "call" | "report"
  const [step, setStep]       = useState("presets");
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");

  // Avatar step state
  const [avatars, setAvatars]           = useState([]);
  const [models, setModels]             = useState({});
  const [avatarsLoading, setAvLoading]  = useState(false);
  const [avatarsError, setAvError]      = useState("");

  const set = useCallback((key, val) => {
    setForm(prev => {
      if (key === "profession") {
        const cleared = {};
        PROFESSION_SPECIFIC_KEYS.forEach(k => { cleared[k] = ""; });
        return { ...prev, ...cleared, profession: val };
      }
      return { ...prev, [key]: val };
    });
  }, []);

  // Bulk-merge many fields at once (used by the LLM "auto-generate" button).
  const setAll = useCallback((obj) => {
    setForm(prev => ({ ...prev, ...obj }));
  }, []);

  // Load avatar/model options without changing the step (used by the gallery).
  const loadAvatars = useCallback(async () => {
    setAvLoading(true); setAvError("");
    try {
      const r = await fetch("/api/roleplay/avatars");
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      setAvatars(d.replicas || []);
      setModels(d.models || {});
      return d;
    } catch (e) {
      setAvError(e.message || "Failed to load avatars.");
      return null;
    } finally {
      setAvLoading(false);
    }
  }, []);

  // Form → Avatar: advance and load the avatar/model options.
  const goToAvatar = useCallback(async () => {
    setStep("avatar");
    const d = await loadAvatars();
    if (d) {
      const reps = d.replicas || [];
      const mdls = d.models || {};
      // Pre-select first avatar + first model so "Join" is enabled by default.
      setForm(prev => ({
        ...prev,
        replica_id: prev.replica_id || (reps[0]?.id ?? ""),
        model: prev.model || (Object.values(mdls)[0] ?? ""),
      }));
    }
  }, [loadAvatars]);

  // Launch a preset directly: set the whole form and start the call (skips the
  // configure + avatar steps because the preset already carries everything).
  const launchPreset = useCallback(async (presetForm) => {
    setForm(presetForm);
    setLoading(true); setError("");
    try {
      const r = await fetch("/api/roleplay/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(presetForm),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      setUrl(d.conversation_url);
      setId(d.session_id);
      if (d.idle_timeout_s) setIdleTimeoutS(d.idle_timeout_s);
      setStep("call");
    } catch (e) {
      setError(e.message || "Failed to launch session.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Avatar → Call: create the live Tavus session.
  const launch = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const r = await fetch("/api/roleplay/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `Server error ${r.status}`);
      }
      const d = await r.json();
      setUrl(d.conversation_url);
      setId(d.session_id);
      if (d.idle_timeout_s) setIdleTimeoutS(d.idle_timeout_s);
      setStep("call");
    } catch (e) {
      setError(e.message || "Failed to launch session.");
    } finally {
      setLoading(false);
    }
  }, [form]);

  // Leave the call — best-effort end on the server, then back to the form.
  const endCall = useCallback(async () => {
    const cid = sessionId;
    setUrl(""); setId(""); setStep("form"); setError("");
    if (cid) {
      try {
        await fetch("/api/roleplay/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: cid }),
        });
      } catch { /* best-effort */ }
    }
  }, [sessionId]);

  const backToForm = useCallback(() => {
    setStep("form"); setError("");
  }, []);

  const reset = useCallback(() => {
    setForm(EMPTY); setUrl(""); setId("");
    setStep("presets"); setError("");
  }, []);

  const canSubmit    = !!(form.prospect_name && form.profession &&
                         form.gender && form.age && form.scenario);
  const canEtbSubmit = !!(form.prospect_name && form.profession &&
                         form.gender && form.age && form.scenario &&
                         form.feedback_state);
  const canJoin      = !!(form.replica_id && form.model);

  return {
    form, sessionUrl, sessionId, idleTimeoutS, step, setStep,
    loading, error, canSubmit, canEtbSubmit, canJoin,
    avatars, models, avatarsLoading, avatarsError,
    set, setAll, goToAvatar, launch, launchPreset, loadAvatars,
    endCall, backToForm, reset,
  };
}
