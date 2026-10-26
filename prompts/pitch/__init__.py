"""
PROMPT: Pitch Generator (Vikram — senior HDFC RM coaching a junior).

When tuning pitch quality, edit THIS file.
Inputs:
  - retrieved_chunks: RAG context from HDFC documents (top-4 chunks)
  - scenario: the customer scenario from the Quick Form / Free Write
Output:
  - The full prompt string, ready to send to the LLM with json_mode=True.

Token-optimisation (Layer 1-3):
  - build_distillation_prompt(): compresses raw chunks into a compact fact sheet (once per pitch).
  - build_shared_prefix(): assembles the byte-identical system message reused across all 10 sections.
  - Per-section builders now return ONLY the section-specific user message (no persona, no raw
    chunks, no fact lock) — those live in the shared system message so OpenAI can cache them.
"""



# ============================================================================
# DESIGN NOTE — input-driven, RAG-grounded prompts.
# ============================================================================
# Earlier versions of this file injected a big "APPROVED HDFC TELECALLING
# FRAMEWORK" block (10-Second Loan numbers, approved script structure,
# objection Q&As) into every section's prompt. That forced every pitch into
# the 10-Second-Loan mold even when the agent typed something else (Home
# Loan, LAP, fresh PL, business loan for a SaaS founder, etc.).
#
# That reference data now lives in the RAG corpus as
# uploads/HDFC_10_Second_Loan_Approved_Pitch_Framework.txt. When the agent's
# brief is about a 10-Second Loan, similarity retrieval pulls those facts in
# via `retrieved_chunks` and the model uses them. When the brief is about a
# different product, the corpus retrieves different facts.
#
# Prompts in this file now contain ONLY structural instructions (length,
# tone, format, "use FEEL/FELT/FOUND framework"). All product facts,
# approved scripts, and document lists come from the RAG corpus or the
# agent's brief. No concrete examples, no fixed taxonomies, no defaults.


# ============================================================================
# SCENARIO ENRICHMENT — runs BEFORE the 10 pitch sections.
# ============================================================================
# Why: agents often type sparse briefs ("PL 5L Sharma", "engineer needs loan").
# If we hand that straight to the section builders, every section drifts to a
# generic "Mr. Sharma is likely salaried..." pattern.
#
# This prompt asks the LLM to:
#   1. Extract every fact the agent EXPLICITLY stated.
#   2. Infer the most-likely value for missing fields using HDFC customer
#      typicalities — but mark each field as "provided" or "inferred" so the
#      agent can see what was assumed.
#   3. Emit a 100-150 word ENRICHED NARRATIVE that downstream section builders
#      use as their `scenario` argument.
#
# Output: strict JSON. Format=json on the Ollama call enforces it.

def build_scenario_enrichment_prompt(raw_scenario: str) -> str:
    return f"""You are Vikram, a senior HDFC Bank RM analysing the agent's brief about an upcoming customer interaction. Your job: turn this brief into a clean, honest, structured customer profile that the downstream coaching system will use.

LANGUAGE NOTE: The agent's brief may be written in Hindi, Hinglish (Hindi-English mix), or English. Read and understand it in whichever language it is written. Extract facts correctly regardless of language. Produce ALL output fields in English.

Common Hinglish terms to recognise:
- "meeting hai" = there is a meeting / upcoming meeting
- "loan chahiye / lena hai" = needs a loan
- "business turnover" = business annual revenue
- "working capital ki problem" = working capital shortage / cash flow issue
- "interest rate / EMI ko lekar concern hai" = customer has concerns about interest rate / EMI
- "pitch karna hai" = need to pitch / present
- "kal / aaj" = tomorrow / today (urgency signal)
- "salaried / naukri" = salaried employee
- "ghar / makaan" = house / home
- "shaadi / wedding" = marriage / wedding expenses
- Numbers like "₹2 crore", "50 lakh", "5L" = loan or turnover amounts

RAW BRIEF FROM THE AGENT:
{raw_scenario}

WHAT TO DO:
1. Extract every fact the agent stated. Translate Hinglish/Hindi terms to English values but preserve the agent's meaning exactly. Use the agent's own wording (lightly normalised for grammar and case).
2. If the brief does NOT state a field, the field's value MUST be the literal string "unknown" and its source MUST be "missing". DO NOT guess. DO NOT apply bank-typical defaults. DO NOT invent.
3. Use source = "inferred" ONLY when the brief unambiguously implies the value without stating it (e.g. brief says "wants to buy a flat for 50 lakh" → loan_type = "Home Loan" is inferred). When in doubt, use "missing".
4. Write `scenario_narrative` — an 80-150 word paragraph that:
   - States everything the brief provided, in plain natural language.
   - Explicitly names what is NOT known (e.g. "Profession, income, urgency, and current-bank relationship are not stated.").
   - Does NOT invent any detail.
5. Populate `missing_fields` with the list of field names whose source is "missing".
6. Populate `agent_should_ask` with 1-3 short questions the agent can ask the customer on the call to fill the biggest gaps.

ABSOLUTE RULES:
- Never invent a customer name, profession, loan type, amount, age, income, current bank, urgency, or any other fact.
- LOAN TYPE IS SACRED: If the brief states "Business Loan", the loan_type value is "Business Loan". If it says "Home Loan", it is "Home Loan". Never substitute, upgrade, or reclassify it. A business loan is NOT a personal loan. Working capital is NOT debt consolidation.
- The schema uses <slot> markers. These are SLOT MARKERS — never copy them literally. Each is either replaced by a value from the brief or by the literal string "unknown".
- Use the agent's vocabulary. If the brief calls them a "SaaS founder", profession value is "SaaS founder", not "Self-employed Professional". If the brief calls it a "shaadi loan", goal value is "shaadi loan", not a paraphrase.
- "Vikram" is the analyst's name (your persona). It is NEVER the customer's name. Never put "Vikram" in the customer_name field or in the scenario_narrative as the customer.

Return your response as a single valid JSON object with this EXACT schema:
{{
  "customer_name": "<name as stated, or 'unknown'>",
  "is_name_provided": <true|false>,
  "loan_type": {{"value": "<as stated or unambiguously implied, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "loan_amount": {{"value": "<as stated, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "profession": {{"value": "<agent's own wording for the profession, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "age": {{"value": "<as stated, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "income": {{"value": "<as stated, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "current_bank_relationship": {{"value": "<as stated, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "urgency": {{"value": "<as stated, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "stated_goal": {{"value": "<what the brief says the customer needs the loan for, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "other_context": {{"value": "<any other context the agent provided, or 'unknown'>", "source": "<provided|inferred|missing>"}},
  "missing_fields": ["<field-name-1>", "<field-name-2>"],
  "agent_should_ask": ["<question 1 for the agent to ask on the call>", "<question 2>"],
  "scenario_narrative": "<80-150 word paragraph stating only what the brief provided + explicitly noting what is unknown. No invented details.>"
}}

IMPORTANT: Return ONLY the raw JSON object. No markdown, no code fences, no commentary."""


def build_pitch_prompt(retrieved_chunks: str, scenario: str, language: str = "English") -> str:
    return f"""You are Vikram, a top-performing HDFC Bank Relationship Manager with 12 years of experience selling home loans, personal loans, business loans and LAP (Loan Against Property). You have closed thousands of deals and are renowned for turning sceptical, objecting customers into loyal clients. You are now coaching a junior salesperson for this exact customer scenario.

Your coaching style is:
- EMPATHY FIRST: Always acknowledge the customer's feelings before countering. Never be defensive.
- STORY-DRIVEN: Use real-sounding examples ("I had a customer just like you last month...") to make the pitch feel human.
- SPIN SELLING: Ask Situation, Problem, Implication, Need-Payoff questions to uncover what truly matters to the customer.
- FEEL / FELT / FOUND: For every objection, use this structure — "I understand how you FEEL... Many of our best clients FELT the same way... What they FOUND was..."
- URGENCY WITHOUT PRESSURE: Create natural urgency using rate cycles, property appreciation, or limited-period offers from documents — never fake pressure.
- DOCUMENT-GROUNDED: Every fact, rate, EMI, or eligibility figure you quote MUST come from the HDFC document context OR the approved telecalling reference below. Never invent numbers.
- CLOSE MULTIPLE TIMES: Embed soft trial closes throughout the pitch ("Does that sound like it could work for you?", "Would Tuesday or Wednesday work better to submit the application?").
- SCRIPT COMPLIANT: For 10-Second PL/BL telecalling pitches, anchor on the approved framework below — never go off-script on interest rate, processing fee, or unapproved promises.

HDFC Document Context (quote facts from here directly when relevant):
{retrieved_chunks}

Customer Scenario to address:
{scenario}

COACHING INSTRUCTIONS — follow ALL of these:
1. opening_hook: First, deeply analyse the customer scenario: Who are they? What is their specific goal? What is their likely hidden worry or emotional concern? What specific detail from their profile should the RM reference?

This is a FACE-TO-FACE or BRANCH INTERACTION. Write 4–5 natural spoken sentences (100–140 words) the RM would say at the very start of sitting down with this customer:
- Open directly from the customer's SPECIFIC stated goal or situation — never start with "Good morning", "Thank you for coming", "Welcome to HDFC", or any phone-call style line.
- FORBIDDEN lines: "Am I speaking to Mr./Ms. X?", "Is this a good time to talk?", "I am calling from HDFC Bank." — none of these must appear.
- Beat 1: Acknowledge the customer's specific goal/situation. Beat 2: Normalise — show you have seen this before. Beat 3: Value hint from the documents. Beat 4: One soft open question.
- ANTI-HALLUCINATION: Every concrete detail — name, profession, age, loan type, stated need — MUST come from the brief above. Do not invent any name, profession, family situation, life event, or loan type not in the brief.
Write only the RM's spoken words. No narration, no labels, no bullet points. One flowing paragraph of natural dialogue.
2. questions_to_ask: Write 6-8 SPIN-style discovery questions. These should uncover financial goals, current pain, urgency, and decision-making authority. Make each question sound natural, not like a form.
3. what_not_to_say: List 5-6 specific phrases or behaviours to AVOID. Be concrete — e.g. "Don't say 'our rates are the lowest' without backing it up with a document figure" or "Don't jump to EMI before understanding their monthly cash flow".
4. anticipated_customer_questions: Generate 5-7 Q&A pairs. Each answer must be specific, confident, and grounded in document facts. Answers should be 3-5 sentences — not one-liners.
5. objection_handling: This is the MOST IMPORTANT section. Generate 6-8 objection-response pairs. For each:
   - Use the FEEL/FELT/FOUND framework.
   - Write the response as a natural, flowing conversation — at least 4-6 sentences.
   - Address objections like: "Your rates are too high", "HDFC rejected my application before", "I'll wait for rates to drop", "Another bank is offering better terms", "I need to think about it", "My CIBIL is low", "I can't afford the EMI right now".
   - For the rejection objection specifically: Acknowledge the past pain, explain what may have changed (eligibility norms, income documentation, product variants), and offer a fresh start with a document check. Make this feel like rebuilding trust, not selling.
6. key_product_facts: Pull 5-7 specific facts directly from the documents (rates, LTV ratios, tenure, eligibility bands, processing fees, etc.). Quote them as bullet points the salesperson can say out loud.
6b. documents_checklist: Produce a 6-10 line CHECKLIST of documents the agent must ask THIS customer to provide, tailored to their PROFESSION (salaried / doctor / CA / lawyer / business owner / farmer / pensioner / NRI). Group each line as "Category: specific items" (e.g. "Identity Proof: PAN + Aadhaar"). For pre-approved 10-Second PL/BL: start with "No documents required" and ONLY include the standard list as a fallback for branch-visit flow. Add Property Docs section for Home Loan / LAP. Never invent document names.
7. closing_strategy: Write a 3-4 sentence closing approach that feels natural. Include a specific next step (e.g., document checklist, site visit, application walkthrough). Use an alternative close ("Would you prefer to start with the eligibility check today or after you've spoken to your family?").
8. narrative_summary: Write a 280-380 word continuous sales narrative the RM reads and adapts during the meeting. NOT a script with Agent/Customer turns, no [PAUSE] or [CUE] markers, no dialogue format. One flowing narrative covering: customer's goal → needs analysis → relevant product benefits → addressing likely hesitation naturally → building trust → soft close and next steps. Every fact grounded in the document context.
9. follow_up_email: Write a warm, professional follow-up email (150-200 words). Subject line included. Reference the conversation, summarise the key benefit for THIS customer, and include a clear call to action.

Return your response as a single valid JSON object. Do not include literal types like "string" or placeholders. Write full sentences for every text field. Follow this EXACT JSON schema:
{{
  "opening_hook": "A warm 120-160 word face-to-face opening spoken by the RM. Starts directly from the customer's specific goal or situation — no phone-call lines, no 'Good morning' opener. Four beats: acknowledge goal, normalise, hint at value, soft open question. One paragraph, natural Indian banking sales language.",
  "questions_to_ask": [
    "A realistic SPIN discovery question 1 tailored to the client.",
    "A realistic SPIN discovery question 2..."
  ],
  "what_not_to_say": [
    "A concrete phrase or behavior to avoid 1 based on bank policy.",
    "A concrete phrase or behavior to avoid 2..."
  ],
  "anticipated_customer_questions": [
    {{
      "question": "A realistic question the customer will ask based on their profile.",
      "answer": "A detailed, 3-5 sentence answer grounded in HDFC rules."
    }}
  ],
  "objection_handling": [
    {{
      "objection": "A realistic customer objection (e.g. rate too high, CIBIL issue).",
      "response": "A detailed 4-6 sentence response using the Feel/Felt/Found empathy structure."
    }}
  ],
  "key_product_facts": [
    "A specific bullet point fact pulled directly from HDFC documents (rates, limits, fees, etc.).",
    "Another specific bullet point fact..."
  ],
  "documents_checklist": [
    "Each item formatted as 'Category: specific document names' — tailored to THIS customer's exact profession and loan type. No generic examples."
  ],
  "closing_strategy": "A 3-4 sentence warm closing approach outlining a specific next step with a soft alternative close.",
  "narrative_summary": "A 280-380 word continuous sales narrative — no Agent/Customer labels, no [PAUSE]/[CUE] markers, no dialogue format. Covers customer goal, needs analysis, product benefits, handling hesitation naturally, building trust, and soft close. One flowing narrative grounded in document facts.",
  "follow_up_email": "Subject and body of a follow-up email ready to be sent to the customer."
}}

LANGUAGE: Write ALL text content values inside the JSON in {language}. Keep all JSON keys exactly as shown (they must stay in English). Use natural, fluent {language} as a real HDFC RM would speak it. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English inside any language.

IMPORTANT: Do not return any other keys. Return ONLY the raw JSON object conforming exactly to this schema. No markdown, no code fences, no extra commentary."""


# ============================================================================
# LAYER 1 — DISTILLATION PROMPT (run ONCE per pitch, on cheap model)
# ============================================================================
# Converts raw retrieved chunks (~400-500 tok) into a compact DOCUMENT_FACTS
# bullet list (~200-300 tok) that is then embedded in the shared prefix and
# reused verbatim by all 10 section calls.
#
# Quality guard: every specific figure must be preserved verbatim. The prompt
# instructs the model to copy numbers exactly and never round or paraphrase.

def build_distillation_prompt(retrieved_chunks: str) -> str:
    return f"""You are a meticulous financial data analyst. Extract every factual data point from the HDFC policy document excerpts below and format them as a dense bullet list.

HDFC DOCUMENT EXCERPTS:
{retrieved_chunks}

RULES (follow every rule without exception):
1. Copy EVERY specific figure VERBATIM: interest rates, processing fees, foreclosure charges, disbursal timelines, tenure caps, eligibility thresholds (min income, max FOIR, min CIBIL), LTV ratios, loan amount limits, approved scripts, product names, and EXACT document names.
2. Do NOT paraphrase or round any number. "10.85% p.a." stays "10.85% p.a." — never "~11%" or "about 11%".
3. Do NOT add any figure not present in the excerpts above.
4. Strip ONLY: prose filler, repetitive phrasing, and text clearly about a different loan type than the main subject.
5. Group under these categories (omit empty ones): Interest Rates | Fees & Charges | Eligibility | Loan Terms | Documents Required | Process & Timelines | Other.
6. If a fact appears multiple times with the same value, write it once.

Output ONLY the bullet list. No intro sentence, no closing line, no markdown fences.
DOCUMENT FACTS:"""


# ============================================================================
# LAYER 2 — SHARED PREFIX BUILDER (byte-identical for all 10 section calls)
# ============================================================================
# Called once in features/pitch.py after distillation. Returns the system
# message string that is sent unchanged for every section in a pitch session.
# OpenAI caches prefixes > 1024 tok — section 1 fills the cache, 2-10 reuse it.
#
# Fixed constant parts so the prefix is truly byte-identical:
_SHARED_PERSONA = (
    "You are Vikram, a senior HDFC Bank Relationship Manager with 12 years of "
    "customer-facing experience. You are coaching a junior salesperson for the "
    "customer meeting described in CUSTOMER SCENARIO below.\n"
    "PERSONA NOTE: \"Vikram\" is YOUR coaching name as the RM — NOT the customer's name. "
    "Never address the customer as \"Vikram\". The customer's name is stated in the "
    "CUSTOMER SCENARIO."
)

_SHARED_GROUNDING = (
    "GROUNDING RULE:\n"
    "TIER 1 — HDFC-SPECIFIC (rates, fees, eligibility thresholds, document lists, "
    "disbursal timelines, approved scripts): MUST come from DOCUMENT FACTS above. "
    "If a figure is absent, say \"not in documents — confirm with branch.\" "
    "Never quote an HDFC-specific number from training data.\n"
    "TIER 2 — GENERAL KNOWLEDGE (standard KYC docs: PAN/Aadhaar/address proof; "
    "general loan process steps): May use, clearly marked \"(standard — confirm with branch)\"."
)


def build_shared_prefix(document_facts: str, fact_lock_block: str, language: str = "English") -> str:
    """Assemble the cacheable system message for all 10 section calls.

    The string is byte-identical for all sections in a pitch session so
    OpenAI prefix caching fires after the first section call.
    Pass language so a hard language override appears in the system message
    (highest priority) for non-English pitches.
    """
    lang_override = ""
    if language and language.strip().lower() not in ("english", "en", ""):
        lang_override = (
            f"\n\nOUTPUT LANGUAGE (highest priority — overrides all other instructions): "
            f"Write EVERY word of your output in {language}. "
            f"The ONLY English permitted is banking terms "
            f"(EMI, FOIR, CIBIL, foreclosure, lakh, crore, ITR, GST, KYC, PAN, NACH, NOC, LTV, FOIR, NPA) "
            f"and structural markers (list numbers, 'Q:', 'A:', 'Objection:', 'Response:').\n"
        )
    return (
        f"{_SHARED_PERSONA}\n\n"
        f"DOCUMENT FACTS (distilled from retrieved HDFC policy documents — "
        f"every figure is verbatim from the source):\n"
        f"{document_facts}\n\n"
        f"{_SHARED_GROUNDING}\n\n"
        f"{fact_lock_block}"
        f"{lang_override}"
    )


# ============================================================================
# SECTION-BY-SECTION PROMPTS — used by /api/pitch/stream
# ============================================================================
#
# Each section gets its own focused prompt. The big monolithic prompt above
# asked one model call to produce all 9 sections at once with JSON — slow
# (~2 min on a 32B model) and hard for the user (no progress feedback).
#
# The streaming endpoint loops these in order, passing earlier sections as
# context to later ones. Benefits:
#   - User sees output appearing within ~5 seconds (first section)
#   - Each section gets focused attention → better quality per section
#   - Smaller LLM works fine because the task per call is narrower
#   - Failed sections can be retried without restarting the whole pitch
#
# Per-section config:
#   - id: machine name for SSE events + state.pitch[id]
#   - label: human label for the UI step indicator
#   - min_words: hard minimum the streaming loop will retry below
#   - max_tokens: budget for Ollama num_predict
#   - depends_on: which prior sections to inject as context
#   - format: "text" | "list" | "qa_pairs" | "objection_pairs"
#
# All 9 sections use qwen2.5:14b by default (~60 tok/sec) — total wall-clock
# ~35–50s end-to-end, with progress shown the entire time.
#
# ── Prompt structure (Layer 2 optimisation) ──────────────────────────────────
# Persona / DOCUMENT FACTS / GROUNDING RULE / SCENARIO FACT LOCK now live in
# the shared system message (build_shared_prefix above), sent byte-identically
# to all 10 calls so OpenAI caches the prefix after the first section.
#
# Each builder below returns ONLY the section-specific user message:
#   CUSTOMER SCENARIO + prior context (where needed) + TASK + RULES + LANGUAGE.
# Builders no longer receive retrieved_chunks or fact_lock — both are in the prefix.


PITCH_SECTIONS = [
    {
        "id": "opening_hook",
        "label": "Opening Hook",
        "min_words": 100,
        "max_tokens": 400,
        "depends_on": [],
        "format": "text",
    },
    {
        "id": "questions_to_ask",
        "label": "Discovery Questions",
        "min_words": 80,
        "max_tokens": 400,
        "depends_on": ["opening_hook"],
        "format": "list",
    },
    {
        "id": "key_product_facts",
        "label": "Key Product Facts",
        "min_words": 80,
        "max_tokens": 400,
        "depends_on": [],
        "format": "list",
    },
    {
        "id": "documents_checklist",
        "label": "Documents Checklist",
        "min_words": 70,
        "max_tokens": 450,
        "depends_on": [],
        "format": "list",
    },
    {
        "id": "what_not_to_say",
        "label": "What NOT to Say",
        "min_words": 80,
        "max_tokens": 350,
        "depends_on": [],
        "format": "list",
    },
    {
        "id": "anticipated_customer_questions",
        "label": "Customer Might Ask",
        "min_words": 150,
        "max_tokens": 600,
        "depends_on": ["key_product_facts"],
        "format": "qa_pairs",
    },
    {
        "id": "objection_handling",
        "label": "Objection Handling",
        "min_words": 200,
        "max_tokens": 800,
        "depends_on": ["key_product_facts"],
        "format": "objection_pairs",
    },
    {
        "id": "closing_strategy",
        "label": "Closing Strategy",
        "min_words": 60,
        "max_tokens": 300,
        "depends_on": ["opening_hook", "key_product_facts"],
        "format": "text",
    },
    {
        "id": "narrative_summary",
        "label": "Full Pitch Script",
        "min_words": 250,
        "max_tokens": 900,
        "depends_on": ["opening_hook", "key_product_facts"],
        "format": "text",
    },
    {
        "id": "follow_up_email",
        "label": "Follow-Up Email",
        "min_words": 120,
        "max_tokens": 500,
        "depends_on": ["opening_hook"],
        "format": "text",
    },
    {
        "id": "red_flags",
        "label": "Red Flags & Watch Out",
        "min_words": 120,
        "max_tokens": 600,
        "depends_on": [],
        "format": "list",
    },
    {
        "id": "competitor_handling",
        "label": "Competitor Handling",
        "min_words": 150,
        "max_tokens": 650,
        "depends_on": ["key_product_facts"],
        "format": "objection_pairs",
    },
    {
        "id": "reengagement_script",
        "label": "Re-engagement Script",
        "min_words": 100,
        "max_tokens": 400,
        "depends_on": ["closing_strategy"],
        "format": "text",
    },
    {
        "id": "cross_sell",
        "label": "Cross-Sell Opportunity",
        "min_words": 60,
        "max_tokens": 300,
        "depends_on": ["key_product_facts"],
        "format": "text",
    },
]


# ── Per-section prompt builders ──────────────────────────────────────────────
#
# Signature (all 10 builders): (scenario, prior_sections, language="English") -> str
#   • scenario       — enriched customer narrative (same for all 10 calls)
#   • prior_sections — {section_id: rendered_text} for already-completed sections
#   • language       — output language directive
#
# retrieved_chunks, fact_lock, and persona are NO LONGER parameters here.
# They live in the shared system message built by build_shared_prefix() above.


def _render_prior(prior_sections: dict, only: list = None, max_words: int = 40) -> str:
    """Render previously-generated sections as short summaries (first max_words words).

    Injecting full prior sections balloons the prompt for later sections.
    A 40-word summary preserves continuity without overflowing the context window.
    """
    if not prior_sections:
        return "(no previous sections yet — you are generating the first one)"
    keys = only if only is not None else list(prior_sections.keys())
    parts = []
    for key in keys:
        if key not in prior_sections or not prior_sections[key]:
            continue
        label = key.replace("_", " ").upper()
        text = prior_sections[key]
        words = text.split()
        if len(words) > max_words:
            text = " ".join(words[:max_words]) + "…"
        parts.append(f"--- {label} (summary) ---\n{text}")
    return "\n\n".join(parts) if parts else "(no previous sections yet)"


def build_opening_hook_section(scenario: str, _prior_sections: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO:
{scenario}

TASK — Write ONLY the OPENING HOOK the salesperson says out loud at the start of this face-to-face branch meeting.

RULES:
- Length: 100-160 words. One flowing paragraph of natural spoken dialogue — no bullet points, no headers.
- FACE-TO-FACE INTERACTION ONLY. These phone-call openers are FORBIDDEN:
    • "Am I speaking to Mr./Ms. X?" | "Is this a good time to talk?" | "I am calling from HDFC Bank." | "Good morning/afternoon, welcome to HDFC."
- Open DIRECTLY from the customer's specific stated goal, need, or situation.
- Four beats: (1) acknowledge the customer's specific goal; (2) normalise — show empathy, you've seen this before; (3) value hint grounded in DOCUMENT FACTS; (4) one soft open question inviting the customer to share more.
- Tone: warm, consultative, unhurried RM sitting across the table — not a script reader. Natural spoken rhythm.
- ANTI-HALLUCINATION: Every concrete detail (name, profession, age, loan type, amount) MUST come from the CUSTOMER SCENARIO. If a field is not stated, keep language general — never invent. Do NOT default to "Mr. Sharma" or any name, profession, income, or life event not explicitly in the scenario.

Write ONLY the spoken dialogue. No "Vikram says:" prefix. No quotes around it.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English.
OPENING HOOK:"""


def build_questions_section(scenario: str, prior_sections: dict, language: str = "English") -> str:
    prior = _render_prior(prior_sections, only=["opening_hook"])
    return f"""CUSTOMER SCENARIO:
{scenario}

WHAT HAS BEEN DRAFTED SO FAR:
{prior}

TASK — Write 6 to 8 SPIN discovery questions the salesperson should ask. SPIN = Situation, Problem, Implication, Need-payoff.

RULES:
- Length: MINIMUM 80 words total across all questions.
- Format: numbered list, one question per line.
- Each question must be tailored to THIS customer's profile — never generic.
- Mix question types: at least 2 Situation, 2 Problem, 1 Implication, 1 Need-payoff.
- Sound natural and conversational — NOT like a form.
- Reference DOCUMENT FACTS where useful (e.g. ask about FOIR if income docs matter).

Write ONLY the numbered list. No intro line, no closing line.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English. Keep the numbered list format ('1.', '2.', etc.) in English; write the content in {language}.
DISCOVERY QUESTIONS:"""


def build_key_facts_section(scenario: str, _prior_sections: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO:
{scenario}

TASK — Pull 5 to 7 specific facts from DOCUMENT FACTS the salesperson can quote out loud to this customer.

RULES:
- Length: MINIMUM 80 words total.
- Format: numbered list, one fact per line. Each fact must be specific (a number, range, or named policy).
- Pull ALL 5-7 facts from DOCUMENT FACTS — never include any figure not present there.
- Select facts most relevant to THIS customer's loan type, amount, and profession. Irrelevant product categories must not appear.
- A fact is only useful if it contains a number, named policy, rate, tenure cap, or disbursal time. Vague claims without a specific figure are NOT facts — exclude them.
- COMPLIANCE: If the scenario involves a pre-approved digital disbursal product, omit interest rate and processing fee figures — they are not in the approved script.

Write ONLY the numbered list. No intro line, no source citations.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English. Keep the numbered list format ('1.', '2.', etc.) in English; write the content in {language}.
KEY FACTS:"""


def build_documents_checklist_section(scenario: str, _prior_sections: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO:
{scenario}

━━━ LOAN TYPE LOCK ━━━
Use the loan type from the CUSTOMER SCENARIO as a strict filter for the ENTIRE checklist.
- Include ONLY documents required for THAT ONE loan type.
- If DOCUMENT FACTS mentions documents for a different loan type, IGNORE them entirely.
- The loan type in the scenario is final. Do not substitute or reclassify it.
━━━━━━━━━━━━━━━━━━━━━

TASK — Write a 6 to 10-line CHECKLIST of documents this customer must bring or upload, tailored to their profession and locked loan type.

CATEGORY STRUCTURE (cover only what applies to the locked loan type):
- Identity Proof
- Address Proof
- Income / Earnings Proof (specifics depend on the customer's stated profession)
- Bank Statements
- Profession-specific docs (ITR, GST returns, registration certificates — only where relevant)
- Property documents (ONLY for Home Loan or LAP — NEVER for Personal Loan or Business Loan)

RULES:
- Use the profession and loan type EXACTLY as stated in the scenario.
- Source document names from DOCUMENT FACTS (Tier 1). If absent, use standard Indian KYC (Tier 2) and mark every item "(standard — confirm exact HDFC list with branch)".
- For pre-approved 10-Second offers: lead with "No documents required for pre-approved digital flow".
- NEVER list documents for a different loan type. NEVER invent document names.
- FORMAT: Numbered list, one category per line as "<Category>: <documents>". Min 70 words.

Write ONLY the numbered checklist. No intro line, no closing line.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English. Keep the numbered list format and the 'Category:' prefix in English; write document names in {language}.
DOCUMENTS CHECKLIST:"""


def build_what_not_to_say_section(scenario: str, _prior_sections: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO:
{scenario}

TASK — List 5 to 6 SPECIFIC things to AVOID saying or doing with this customer.

RULES:
- Length: MINIMUM 80 words total.
- Format: numbered list, one item per line.
- Each item must be SPECIFIC, not generic. Bad: "don't be pushy". Good: "Don't quote 'our rates are the lowest' without backing it with a figure from DOCUMENT FACTS — the customer will ask which bank you compared with."
- For 10-Second PL/BL telecalling pitches, include compliance rules: never mention interest rate or processing fee; never promise beyond the approved framework; never skip the [PAUSE] confirmation step.
- Also cover: language pitfalls, premature pricing, false urgency, tone (keep warm Hindi-English conversational tone).

Write ONLY the numbered list.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English. Keep the numbered list format ('1.', '2.', etc.) in English; write the content in {language}.
THINGS TO AVOID:"""


def build_anticipated_qa_section(scenario: str, prior_sections: dict, language: str = "English") -> str:
    prior = _render_prior(prior_sections, only=["key_product_facts"])
    return f"""CUSTOMER SCENARIO:
{scenario}

KEY FACTS ALREADY DRAFTED (you may quote these in answers):
{prior}

TASK — Write 5 to 7 anticipated questions this customer is likely to ask, each with a confident answer drawn from DOCUMENT FACTS.

RULES:
- Length: MINIMUM 150 words total.
- Format: alternating lines starting with "Q:" and "A:". Example:
  Q: How long does disbursal take?
  A: For pre-approved customers, disbursal happens within 4 hours. For fresh applications, 48-72 hours. We've made the process largely paperless.
- Tailor questions to THIS customer's profile (salaried vs business, loan amount, urgency).
- Answers must reference specific figures from DOCUMENT FACTS. Never invent a number.
- No bullet points inside answers. Flowing 3-5 sentence prose only.

Write ONLY the Q/A pairs. No intro line.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English. STRUCTURAL MARKERS: Keep 'Q:' and 'A:' in English exactly as shown; write question and answer text in {language}.
ANTICIPATED Q&A:"""


def build_objection_handling_section(scenario: str, prior_sections: dict, language: str = "English") -> str:
    prior = _render_prior(prior_sections, only=["key_product_facts", "anticipated_customer_questions"])
    return f"""CUSTOMER SCENARIO:
{scenario}

PRIOR DRAFTS (summaries):
{prior}

PRODUCT RULE: Every objection and response MUST use the exact loan type from the scenario. If DOCUMENT FACTS mentions a different product, ignore those references. Write general empathetic responses when DOCUMENT FACTS for the locked product are unavailable.

TASK — Write 6 to 8 OBJECTION → RESPONSE pairs using the FEEL/FELT/FOUND framework.

RULES:
- Length: MINIMUM 200 words total.
- FEEL/FELT/FOUND: "I understand how you FEEL... Many of our customers FELT the same way... What they FOUND was..."
- Each response: 4-6 sentences. No bullets inside responses.
- Format: alternating "Objection:" and "Response:" lines.
- Choose objections most likely for THIS customer's profile and loan type.
- Ground responses in DOCUMENT FACTS for the locked product only. Never invent figures.
- COMPLIANCE: Never mention interest rate or processing fee.

Write ONLY the objection/response pairs. No intro.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English. STRUCTURAL MARKERS: Keep 'Objection:' and 'Response:' in English exactly as shown; write text in {language}.
OBJECTIONS:"""


def build_closing_strategy_section(scenario: str, prior_sections: dict, language: str = "English") -> str:
    prior = _render_prior(prior_sections, only=["opening_hook", "key_product_facts"])
    return f"""CUSTOMER SCENARIO:
{scenario}

EARLIER DRAFTS (summaries):
{prior}

TASK — Write a 3 to 4 sentence closing approach the salesperson uses to convert this meeting into a concrete next step.

RULES:
- Length: MINIMUM 60 words. One flowing paragraph — no bullets, no headers.
- Include ONE specific next step (e.g. soft eligibility check today, document checklist email, follow-up call).
- Use an alternative close: "Would you prefer A or B?" — never a single yes/no question.
- Ground any figure mentioned in DOCUMENT FACTS. Never invent a rate, fee, or eligibility number.
- Tone: warm, confident, never pushy.

Write ONLY the closing dialogue. No "Vikram says:" prefix.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English.
CLOSING STRATEGY:"""


def build_narrative_summary_section(scenario: str, prior_sections: dict, language: str = "English") -> str:
    prior = _render_prior(
        prior_sections,
        only=["opening_hook", "key_product_facts", "objection_handling", "closing_strategy"],
    )
    return f"""CUSTOMER SCENARIO:
{scenario}

EARLIER DRAFTS (summaries — for continuity; the CUSTOMER SCENARIO overrides any conflicting detail):
{prior}

TASK — Write a 280-380 word continuous sales narrative. NOT a conversation script. One flowing narrative from the RM's perspective covering:
1. Customer's goal / situation (go deeper than the opening hook)
2. Needs analysis — the aspiration or concern beneath the stated request
3. Relevant product benefits grounded in DOCUMENT FACTS
4. Address likely hesitation naturally (no Q&A exchange)
5. Build confidence and trust
6. Soft close toward a concrete next step

FORMAT RULES:
- NO "Agent:" / "RM:" / "Customer:" labels. NO customer dialogue. NO [PAUSE]/[CUE] markers.
- One continuous narrative, paragraphs only. No bullets, no headers.
- Every figure from DOCUMENT FACTS or the scenario. NEVER invent numbers, rates, or tenures.
- COMPLIANCE: never mention interest rate or processing fee.
- ANTI-HALLUCINATION: customer name, loan type, and amount MUST come from CUSTOMER SCENARIO. "unknown" fields → keep language general. If prior drafts conflict with the scenario, the scenario wins.

Write ONLY the narrative. No intro prefix.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English.
FULL PITCH SCRIPT:"""


def build_follow_up_email_section(scenario: str, prior_sections: dict, language: str = "English") -> str:
    prior = _render_prior(prior_sections, only=["closing_strategy"])
    return f"""CUSTOMER SCENARIO:
{scenario}

WHAT WAS AGREED IN THE MEETING (closing strategy summary):
{prior}

TASK — Write a warm, professional follow-up email ready to copy-paste.

RULES:
- Length: MINIMUM 120 words, ideally 150-200 words.
- Format: Subject line, blank line, then body.
- Address by name if the scenario includes it.
- Reference ONE fact from DOCUMENT FACTS (rate, tenure, or disbursal time).
- End with a clear next step and specific timeframe.
- Sign as "HDFC Relationship Team".

Write ONLY the email (subject + body). No "Here is the email:" intro.
LANGUAGE DIRECTIVE: Write the ENTIRE output in {language}. Banking terms (EMI, FOIR, CIBIL, foreclosure, lakh, crore) stay in English.
FOLLOW-UP EMAIL:"""


def build_red_flags_section(scenario: str, _prior: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO:
{scenario}

TASK — red_flags:
You are a senior HDFC credit and sales risk analyst. Analyse this customer profile and produce a RED FLAGS report for the sales agent to read BEFORE the meeting.

A red flag is a specific, named risk that could cause this particular pitch to fail or this particular customer to be ineligible. Generic observations ("customer may ask about rates") are NOT red flags.

Cover ALL of these risk dimensions — skip a dimension only if the scenario provides zero information about it:

1. ELIGIBILITY RISK — income vs. EMI affordability, age vs. tenure, employment type (salaried/self-employed/retired), CIBIL history signals.
2. INCOME INSTABILITY RISK — variable income, seasonal business, commission-only, recently self-employed (<2 years).
3. DOCUMENTATION RISK — self-employed without ITR, cash-income business, missing KYC docs, NRI status.
4. BEHAVIOURAL RISK — customer deflects income questions, vague about existing obligations, mentions "EMI already tight", has been rejected before.
5. COMPETITOR/PRICE RISK — heavy comparison shopping, fixated on rate alone, has a rival offer in hand.
6. TIMING RISK — customer's deadline is tighter than normal processing time, need is speculative ("might need it").
7. OVER-COMMITMENT RISK — customer is trying to borrow more than their profile can support.

FORMAT — for each red flag you identify:
🚩 [SHORT FLAG TITLE]
Risk: [One sentence — what the specific risk is for THIS customer]
Why it matters: [One sentence — concrete consequence if ignored]
Agent action: [One sentence — exact thing the agent should do or say to address this proactively]

If a dimension is NOT a risk for this customer, skip it entirely. Do NOT invent risks.
Do NOT give generic HDFC policy text — everything must be grounded in THIS customer's profile.
Output 3-7 red flags maximum. Quality over quantity.

OUTPUT LANGUAGE: {language} for headers/labels; keep financial terms in English."""


def build_competitor_handling_section(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["key_product_facts"], max_words=50)
    return f"""CUSTOMER SCENARIO:
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — competitor_handling:
The customer is likely comparing HDFC with one or more competitors. Write a competitor handling guide for the agent.

Step 1 — Identify the competitor(s) from the scenario. If none are mentioned, use the most common competitors for this product type (e.g. for personal loan: Bajaj Finserv, ICICI, SBI; for home loan: Axis, SBI, LIC HFL; for business loan: IDFC First, Bajaj, Kotak).

Step 2 — For each identified competitor, write an Objection/Response pair using the FEEL/FELT/FOUND framework. The response must:
- Acknowledge the competitor's strength honestly (never dismiss it — customers know)
- Pivot to a specific HDFC differentiator grounded in DOCUMENT FACTS (rate, speed, digital process, pre-approved limit, relationship benefits)
- End with a soft question that keeps the conversation going

Step 3 — Add a "Positioning Line" — a single crisp sentence the agent can say when the customer asks "why HDFC over X?" — specific to this customer's stated goal.

FORMAT:
Competitor: [Name]
Objection: [What customer says when comparing]
Response: [FEEL/FELT/FOUND response]

...then:
POSITIONING LINE: [One crisp sentence]

Generate 2-4 competitor pairs maximum. Grounded in DOCUMENT FACTS.

OUTPUT LANGUAGE: {language}."""


def build_reengagement_script_section(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["closing_strategy"], max_words=40)
    return f"""CUSTOMER SCENARIO:
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — reengagement_script:
Write the script the agent uses when following up with a customer who said "call me later", "not now", "I'll think about it", or "send me the details" in the previous interaction.

The re-engagement must do all of these:
1. Open with a warm, non-pushy reference to the previous conversation ("When we last spoke, you mentioned...")
2. Give the customer ONE specific new reason to reconsider NOW — a rate cycle update, an offer expiry, a document they can submit easily, or a relevant event from their profile (their stated deadline, their seasonal business cycle, etc.)
3. NOT repeat the full pitch — assume they remember it. This is a brief re-opener.
4. End with a low-commitment ask: "Would it be okay if we spent just 10 minutes to walk through one specific aspect that seemed important to you?"

Tone: warm, confident, unhurried. Not needy. Not scripted.
Length: 120-160 words. One flowing paragraph.

OUTPUT LANGUAGE: {language}."""


def build_cross_sell_section(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["key_product_facts"], max_words=40)
    return f"""CUSTOMER SCENARIO:
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — cross_sell:
Based on this customer's profile and the primary product being pitched, identify ONE (maximum two) natural cross-sell opportunity and write the agent's one-liner to introduce it.

Rules for selecting the cross-sell:
- It must be genuinely relevant to THIS customer's stated life situation or goal — not a generic suggestion
- It must be a different product category from the primary pitch (e.g. if pitching personal loan → credit card, insurance, or FD; if pitching home loan → home insurance, home loan top-up, or savings account)
- If the customer's profile has no obvious cross-sell opportunity, say so clearly and suggest what to watch for in future interactions instead

For each cross-sell:
PRODUCT: [Name]
WHY IT FITS: [One sentence — specific to this customer's profile]
ONE-LINER: [Exact words the agent says at the end of the meeting to introduce it naturally — e.g. "One more thing, [Name] — given that you're buying a home, our home insurance covers you from day one of the loan and the premium gets bundled into your EMI..."]
TIMING: [When in the meeting to bring it up — e.g. "After closing the primary application" or "Only if customer seems satisfied and time permits"]

OUTPUT LANGUAGE: {language}."""


# Map section_id → builder function — used by the streaming endpoint to
# dispatch in PITCH_SECTIONS order.
SECTION_BUILDERS = {
    "opening_hook":                  build_opening_hook_section,
    "questions_to_ask":              build_questions_section,
    "key_product_facts":             build_key_facts_section,
    "documents_checklist":           build_documents_checklist_section,
    "what_not_to_say":               build_what_not_to_say_section,
    "anticipated_customer_questions": build_anticipated_qa_section,
    "objection_handling":            build_objection_handling_section,
    "closing_strategy":              build_closing_strategy_section,
    "narrative_summary":             build_narrative_summary_section,
    "follow_up_email":               build_follow_up_email_section,
    "red_flags":                     build_red_flags_section,
    "competitor_handling":           build_competitor_handling_section,
    "reengagement_script":           build_reengagement_script_section,

    "cross_sell":                    build_cross_sell_section,
}

# ============================================================================
# SERVICE CALL (ETB) — PITCH SECTIONS & BUILDERS
# ============================================================================
# Service-cum-Sales Call for Existing To Bank (ETB) customers.
# Structure follows the HDFC approved framework:
#   Phase 1 — Opening + Service Check (build trust, no selling yet)
#   Phase 2 — Transition Bridge (natural pivot from service to offer)
#   Phase 3 — Loan Offer + Objection Handling + Dual Close
#
# These sections replace the new-customer PITCH_SECTIONS for service calls.
# The shared prefix (build_shared_prefix) is reused unchanged.

SERVICE_CALL_SECTIONS = [
    {
        "id": "opening_script",
        "label": "Opening Script",
        "min_words": 80,
        "max_tokens": 380,
        "depends_on": [],
        "format": "text",
    },
    {
        "id": "service_check_questions",
        "label": "Service Check Questions",
        "min_words": 80,
        "max_tokens": 400,
        "depends_on": ["opening_script"],
        "format": "list",
    },
    {
        "id": "happy_customer_escalation",
        "label": "Happy Customer Path",
        "min_words": 80,
        "max_tokens": 380,
        "depends_on": ["service_check_questions"],
        "format": "text",
    },
    {
        "id": "transition_bridge",
        "label": "Service → Offer Transition",
        "min_words": 60,
        "max_tokens": 320,
        "depends_on": ["happy_customer_escalation"],
        "format": "text",
    },
    {
        "id": "loan_pitch_lines",
        "label": "Loan Offer Pitch",
        "min_words": 100,
        "max_tokens": 450,
        "depends_on": ["transition_bridge"],
        "format": "text",
    },
    {
        "id": "objection_handling",
        "label": "Objection Handling",
        "min_words": 200,
        "max_tokens": 800,
        "depends_on": ["loan_pitch_lines"],
        "format": "objection_pairs",
    },
    {
        "id": "closing_strategy",
        "label": "Closing Strategy",
        "min_words": 60,
        "max_tokens": 320,
        "depends_on": ["loan_pitch_lines"],
        "format": "text",
    },
    {
        "id": "what_not_to_say",
        "label": "What NOT to Say",
        "min_words": 70,
        "max_tokens": 350,
        "depends_on": [],
        "format": "list",
    },
    {
        "id": "documents_checklist",
        "label": "Documents Checklist",
        "min_words": 40,
        "max_tokens": 300,
        "depends_on": [],
        "format": "list",
    },
    {
        "id": "full_script",
        "label": "Full Call Script",
        "min_words": 280,
        "max_tokens": 1000,
        "depends_on": ["opening_script", "loan_pitch_lines", "objection_handling"],
        "format": "text",
    },
    {
        "id": "follow_up_template",
        "label": "Follow-Up Template",
        "min_words": 100,
        "max_tokens": 450,
        "depends_on": ["opening_script"],
        "format": "text",
    },
]


def build_sc_opening_script(scenario: str, _prior: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

TASK — opening_script:
Write the exact words the agent says to OPEN this service-cum-sales call. This is a PHONE CALL to an EXISTING HDFC customer.

The opening must follow this exact 4-beat structure:
Beat 1 — Greeting + Identity: "Greetings Mr./Ms. [name], my name is ___, calling from HDFC Bank – [branch]."
Beat 2 — Ask for time: "Is this a good time to speak with you for a quick service call?"
Beat 3 — Purpose (SERVICE first, not sales): "The purpose of my call is to ensure you are satisfied with the services offered by HDFC Bank and to understand how we can serve you better."
Beat 4 — Permission: "With your permission, may I ask you a few quick questions?"

RULES:
- This is PURE SERVICE — do NOT mention the loan offer or any product at all in this section.
- Keep it warm and relationship-first. They are an existing customer.
- Reference the existing facility/product the customer holds (from the scenario) to personalize.
- 3-4 natural spoken sentences. Not a bullet list.
- FORBIDDEN: "I am calling to inform you about our offer", any mention of loan/EMI/rate in opening.

Write only the agent's spoken words. Plain paragraph.

OUTPUT LANGUAGE: {language}. Keep all names, bank terms (HDFC Bank, branch) in English.
Write the opening_script content only — no JSON, no labels."""


def build_sc_service_check_questions(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["opening_script"], max_words=40)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — service_check_questions:
Write exactly 4 service satisfaction questions the agent asks BEFORE any product pitch.
These questions build rapport and uncover any service issues.

Include these 4 specific questions (adapt naturally to the customer's profile):
1. "Are you satisfied with the banking services you currently receive from HDFC Bank?"
2. "Which of our services do you find the most useful?"
3. "Do you actively use our Digital Banking services such as Mobile Banking or NetBanking?"
4. "Have you availed any loan products from us previously? If yes, was the process smooth and timely?"

If the scenario mentions a KNOWN SERVICE ISSUE, add that as a 5th question to proactively address it:
e.g. "I can also see you had a query regarding [issue] — may I take a moment to address that for you?"

RULES:
- These are DISCOVERY questions. No product pitch yet.
- Keep each question natural and conversational, not robotic form-filling.
- Each question should be on its own line.
- Return as a plain numbered list of questions.

OUTPUT LANGUAGE: {language}."""


def build_sc_happy_customer_escalation(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["service_check_questions"], max_words=40)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — happy_customer_escalation:
Write the agent's lines for TWO sub-scenarios AFTER the service check questions:

SUB-SCENARIO A — Customer is SATISFIED / HAPPY:
- Acknowledge their loyalty and positive feedback warmly.
- If the customer mentioned ANY concern or issue (even minor), explicitly assure them:
  "Thank you for sharing that. I am taking note of your concern and will personally escalate it to the concerned authority/department to ensure it is resolved at the earliest."
- Then naturally transition toward the offer: "I am delighted to hear that. We truly value your continued relationship with us."

SUB-SCENARIO B — Customer has a COMPLAINT / ISSUE:
- Acknowledge the issue with full empathy: "I sincerely apologize for the inconvenience caused."
- Take ownership: "Please allow me to raise this with the concerned department right away. I will personally follow up to ensure it is resolved as a priority."
- De-escalate: Assure them of resolution timeline if known from documents.
- Only AFTER resolving/acknowledging the complaint, very gently attempt the transition.

RULES:
- Complaint escalation ALWAYS comes before any product pitch. Never skip this.
- These are the agent's spoken words — natural, empathetic, warm.
- Format as two clearly labeled sub-sections: "If Customer is Satisfied:" and "If Customer has a Complaint:"

OUTPUT LANGUAGE: {language}."""


def build_sc_transition_bridge(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["happy_customer_escalation"], max_words=40)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — transition_bridge:
Write the exact bridge lines the agent uses to NATURALLY pivot from the service check to introducing the pre-approved loan offer.

The transition must feel organic — NOT like a sudden shift to a sales pitch.
Use the HDFC Offers Page as the bridge:
"Thank you for your feedback, Mr./Ms. [name]. Just to keep you updated — have you recently had a chance to visit our HDFC Bank Offers Page?"

Then reveal the offer:
"I would like to inform you that you have an exclusive, pre-approved [Product] offer available on your account — [offer amount] at a very attractive rate, exclusive to you."

RULES:
- This should feel like you are informing them of something beneficial they already qualify for — NOT selling cold.
- Use the exact offer amount and product from the scenario.
- Mention the disbursement speed (10 seconds / 2-4 hours) if the product supports it.
- Keep it 3-4 natural spoken sentences.
- Do NOT launch into full pitch yet — just reveal the offer exists.

OUTPUT LANGUAGE: {language}."""


def build_sc_loan_pitch_lines(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["transition_bridge"], max_words=40)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — loan_pitch_lines:
The customer has been told about their pre-approved offer. Now write the full LOAN PITCH the agent delivers IF the customer shows interest.

Cover ALL of these key points in natural spoken language:
1. Loan Amount: The specific pre-approved amount (from scenario)
2. Rate: "At a very attractive rate, exclusive to you as a valued HDFC customer" (get exact rate from documents if available)
3. Disbursement speed: "Disbursement as fast as 10 seconds / 2 to 4 hours directly into your account"
4. Process: "This offer is completely paperless and instant, designed for your convenience"
5. Loyalty angle: "As an existing HDFC customer, you have already been pre-approved — no lengthy documentation required"
6. Urgency (natural, not pressure): Reference the offer validity or rate cycle if available from documents

RULES:
- This is only delivered IF the customer shows interest. Frame it as information, not pressure.
- Use facts from DOCUMENT FACTS in the shared prefix where available.
- Natural spoken language — 5-7 sentences.
- Do NOT pressure. Offer, explain, invite.

OUTPUT LANGUAGE: {language}."""


def build_sc_objection_handling(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["loan_pitch_lines"], max_words=40)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — objection_handling (ETB service call specific):
Generate 6-8 objection-response pairs specific to EXISTING customers on a service call.

Include responses for these ETB-specific objections:
1. "I already have a loan, I don't need another one right now."
2. "Please send me the details, I'll think about it." (a common delay tactic)
3. "I'm not interested in any loan right now, I called you for service."
4. "Can you call me later? I'm busy."
5. "What's the interest rate? [Concern it's too high for an existing customer]"
6. "Why are you calling me? I didn't ask for this."
7. "I'm happy with my current bank, I don't need more loans."
8. One objection specific to the customer's known service issue (if mentioned in scenario).

For each objection:
- Use FEEL / FELT / FOUND framework.
- Acknowledge first, never be defensive.
- Keep response 3-5 sentences, conversational.
- For "send details" objection: agree AND still ask for a micro-commitment (e.g., "Of course, and may I also share one specific benefit that applies to your profile specifically?")

Format: list of Objection: / Response: pairs.

OUTPUT LANGUAGE: {language}."""


def build_sc_closing_strategy(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["loan_pitch_lines"], max_words=40)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — closing_strategy (DUAL CLOSE for service call):
Write the closing approach for TWO outcomes:

OUTCOME A — Customer is INTERESTED:
"If you'd like, I can assist you with the process right now and help you complete the loan in just a few steps. Shall I guide you through it?"
Then: guide through next steps (which are almost nil for pre-approved: just Aadhaar OTP / paperless).

OUTCOME B — Customer DECLINES:
"No problem at all, Mr./Ms. [name]. Please feel free to reach out to me anytime on [number] for any service-related assistance or future requirements. Thank you once again for your continued relationship with HDFC Bank. We truly appreciate your time."
Key: leave the door open warmly. No pressure. This is a relationship call — a 'no' today is fine.

RULES:
- NEVER pressure on a service call. If they decline, accept graciously and maintain the relationship.
- The exit on decline should be as warm and professional as the opening.
- 4-6 sentences total per outcome.

OUTPUT LANGUAGE: {language}."""


def build_sc_what_not_to_say(scenario: str, _prior: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

TASK — what_not_to_say (ETB service call specific):
List 6-7 specific phrases or behaviours to AVOID on an ETB service-cum-sales call.

Focus on ETB-specific mistakes:
1. Jumping to the loan pitch before completing the service check questions.
2. Ignoring a service complaint/issue and pivoting straight to selling.
3. Pressuring the customer after they say "no" or "call me later".
4. Treating the customer like a new/cold prospect (they are an existing loyal customer).
5. Using generic lines like "our rates are the lowest" without specifying their offer.
6. Mentioning the loan offer in the opening before completing the service phase.
7. Ending the call abruptly if they decline — always close warmly and preserve the relationship.

Make each point concrete and actionable — not generic advice.
Return as a plain numbered list.

OUTPUT LANGUAGE: {language}."""


def build_sc_documents_checklist(scenario: str, _prior: dict, language: str = "English") -> str:
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

TASK — documents_checklist (ETB pre-approved — minimal):
Write the document checklist for THIS pre-approved offer to an existing HDFC customer.

Key insight: For pre-approved ETB customers, the process is designed to be PAPERLESS.
Start with: "Pre-Approved Offer — Minimal Documentation"

Typical ETB checklist:
- Aadhaar OTP (for e-KYC) — no physical documents required for digital path
- PAN (already on record for existing customers)
- Bank account details (already linked)

If the scenario mentions branch visit OR non-digital customer:
- Add standard KYC: PAN + Aadhaar + address proof
- Income proof if required (salary slip / ITR based on profile)

RULES:
- Keep it SHORT — this is the key benefit of ETB pre-approved offers.
- Only list what's actually needed. Do NOT add generic long checklists.
- Group as "Digital Path:" and "Branch Visit Path:" if applicable.

Return as a plain numbered/bulleted list.

OUTPUT LANGUAGE: {language}."""


def build_sc_full_script(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(
        prior,
        only=["opening_script", "loan_pitch_lines", "objection_handling"],
        max_words=60
    )
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — full_script:
Write a complete, flowing call script the agent reads and adapts during the service call.
This is a NARRATIVE — not Agent/Customer dialogue turns. No [PAUSE] or [CUE] markers.

Cover the full arc in 300-380 words:
1. Opening & Greeting (warm, relationship-first, purpose is service)
2. Service Check Phase (ask the 4 satisfaction questions, listen)
3. Acknowledge feedback — if complaint: escalate and assure; if happy: affirm loyalty
4. Transition to offer (natural bridge, HDFC Offers Page)
5. The pre-approved offer reveal (amount, rate, speed, paperless)
6. Handling likely resistance (briefly — they may not need loan now)
7. Dual close: interested path (guide through process) and decline path (warm exit)
8. Sign-off maintaining the relationship

RULES:
- This reads as the agent's internal coaching script — flowing prose, not a word-for-word script.
- Embed all key facts from the scenario (offer amount, product, known issue if any).
- Every fact grounded in DOCUMENT FACTS from the shared prefix.
- Warm, professional, relationship-first tone throughout.

OUTPUT LANGUAGE: {language}."""


def build_sc_follow_up_template(scenario: str, prior: dict, language: str = "English") -> str:
    prior_ctx = _render_prior(prior, only=["opening_script"], max_words=30)
    return f"""CUSTOMER SCENARIO (ETB Service Call):
{scenario}

PRIOR SECTION CONTEXT:
{prior_ctx}

TASK — follow_up_template:
Write a warm, professional follow-up WhatsApp message OR email the agent sends after the service call.
This is a RELATIONSHIP-tone message — not a hard sales follow-up.

Include:
- Thank the customer for their time on today's service call
- Confirm any service issue raised has been escalated (if applicable from scenario)
- Briefly mention the pre-approved offer details (amount + product) as a reminder
- A soft call-to-action: "The offer remains valid. Feel free to call me anytime at [number]."
- Warm sign-off maintaining the relationship

RULES:
- 120-170 words. Warm and personal — not a copy-paste marketing message.
- Include Subject line if it's an email, or open with "Hi [Name]," if WhatsApp.
- Reference specific details from the scenario.
- NEVER pressure. This is a relationship-maintenance message.

OUTPUT LANGUAGE: {language}."""


# Map section IDs to their builder functions (mirrors the new-customer pattern)
SERVICE_CALL_SECTION_BUILDERS = {
    "opening_script":           build_sc_opening_script,
    "service_check_questions":  build_sc_service_check_questions,
    "happy_customer_escalation": build_sc_happy_customer_escalation,
    "transition_bridge":        build_sc_transition_bridge,
    "loan_pitch_lines":         build_sc_loan_pitch_lines,
    "objection_handling":       build_sc_objection_handling,
    "closing_strategy":         build_sc_closing_strategy,
    "what_not_to_say":          build_sc_what_not_to_say,
    "documents_checklist":      build_sc_documents_checklist,
    "full_script":              build_sc_full_script,
    "follow_up_template":       build_sc_follow_up_template,
}
