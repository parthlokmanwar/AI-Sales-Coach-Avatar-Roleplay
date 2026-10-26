export const PROFESSIONS = [
  "Doctor", "Teacher", "Businessman",
  "Government Employee", "Housewife",
  "Self-Employed", "Retired", "Student",
];

export const GENDERS        = ["Male", "Female"];
export const AGE_GROUPS     = ["25-35", "35-50", "50+"];
export const LOAN_TYPES     = ["Personal Loan", "Business Loan"];

// ── Section 1 — Customer Profile (Basic) ──
export const CITY_TIERS = [
  "Metro (Mumbai, Delhi, Bangalore)",
  "Tier 2 (Pune, Surat, Jaipur)",
  "Tier 3",
  "Rural",
];

// ── Section 2 — Personality & Behavior ──
export const TONES = [
  "Friendly", "Neutral", "Skeptical", "Rough",
  "Confused", "Aggressive", "Very Busy", "Polite but Firm",
];
export const KNOWLEDGE_LEVELS = [
  "Knows nothing about loans",
  "Basic understanding",
  "Well researched online",
  "Taken loans before",
];
export const DECISION_STYLES = [
  "Takes decisions alone",
  "Needs spouse approval",
  "Needs time to think",
  "Quick decision maker",
];
export const TRUST_LEVELS = [
  "Trusts HDFC",
  "Neutral",
  "Prefers competitor",
  "Had bad experience with HDFC before",
];

// ── Section 3 — Loan Details ──
export const INCOME_SITUATIONS = [
  "Stable salaried", "Variable income",
  "Seasonal income", "High income irregular",
];

// ── Section 4 — Scenario Type ──
export const CALL_TYPES = [
  "Sales Call (salesperson pitching)",
  "Service Call (customer has a problem first)",
  "Follow-up Call (customer already inquired)",
  "Objection Handling (customer is resisting)",
];
export const DIFFICULTY_LEVELS = ["Easy", "Medium", "Hard", "Expert"];

// ── Existing-customer SERVICE-CALL (ETB) options ──
// feedback_state drives whether the existing customer is satisfied, lukewarm, or
// actively aggrieved when the RM cold-calls them about a pre-approved offer.
export const FEEDBACK_STATES = ["Happy", "Neutral", "Complaint"];

// Preset HDFC grievances an aggrieved existing customer might open with.
export const SERVICE_ISSUES = [
  "Reward points not credited",
  "Disputed charge not reversed",
  "KYC update stuck",
  "Relationship manager unresponsive",
  "Cheque book / card not delivered",
  "Wrong charge / hidden fee",
  "Net banking access issue",
  "FD renewal request stuck",
  "Complaint ticket unresolved",
  "Other issue",
];

export const CIBIL_OPTIONS = [
  { label: "Good (750+)",        value: "good 750+" },
  { label: "Average (650–750)",  value: "average 650-750" },
  { label: "Low (below 650)",    value: "low below 650" },
  { label: "Unknown",            value: "unknown" },
];

export const COMPETITOR_OPTIONS = [
  { label: "None",          value: "none" },
  { label: "SBI",           value: "SBI" },
  { label: "Bajaj Finserv", value: "Bajaj Finserv" },
  { label: "ICICI",         value: "ICICI" },
];

export const PROFESSION_FIELDS = {
  Doctor: [
    { key: "specialization",     label: "Specialization",
      placeholder: "e.g. Cardiologist, General Physician" },
  ],
  Teacher: [
    { key: "teacher_type",        label: "Teacher Type",
      placeholder: "e.g. Government, Private" },
    { key: "school_college_type", label: "School / College Type",
      placeholder: "e.g. CBSE school, Engineering college" },
  ],
  Businessman: [
    { key: "business_type", label: "Business Type",
      placeholder: "e.g. Trading, Manufacturing, Retail" },
    { key: "turnover",      label: "Annual Turnover",
      placeholder: "e.g. Rs. 1.5 Crore" },
    { key: "current_bank",  label: "Current Bank",
      placeholder: "e.g. ICICI, SBI" },
  ],
  "Government Employee": [
    { key: "govt_dept",           label: "Department",
      placeholder: "e.g. Railways, Municipal Corporation" },
    { key: "govt_designation",    label: "Designation",
      placeholder: "e.g. Senior Clerk, Deputy Manager" },
    { key: "years_to_retirement", label: "Years to Retirement",
      placeholder: "e.g. 8" },
  ],
  Housewife: [
    { key: "spouse_profession", label: "Spouse Profession",
      placeholder: "e.g. Software Engineer, Teacher" },
    { key: "spouse_income",     label: "Spouse Monthly Income",
      placeholder: "e.g. Rs. 80,000" },
  ],
  "Self-Employed": [
    { key: "self_employed_type", label: "Type of Work",
      placeholder: "e.g. Chartered Accountant, Architect" },
  ],
  Retired: [
    { key: "retired_from",            label: "Retired From",
      placeholder: "e.g. Indian Army, State Government" },
    { key: "years_since_retirement",  label: "Years Since Retirement",
      placeholder: "e.g. 3" },
    { key: "pension_amount",          label: "Monthly Pension",
      placeholder: "e.g. Rs. 45,000" },
  ],
  Student: [
    { key: "student_type",     label: "Student Type",
      placeholder: "e.g. Undergraduate, Postgraduate" },
    { key: "field_of_study",   label: "Field of Study",
      placeholder: "e.g. MBA, Engineering, Medicine" },
    { key: "institution_type", label: "Institution Type",
      placeholder: "e.g. IIT, Private college" },
    { key: "family_income",    label: "Family Monthly Income",
      placeholder: "e.g. Rs. 60,000" },
  ],
};
