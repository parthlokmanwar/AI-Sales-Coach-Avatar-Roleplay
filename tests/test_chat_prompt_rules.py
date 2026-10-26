"""
Static checks for the Arjun chat prompt rules (RULE 9 scope + RULE 3 source tiers).

These do not call an LLM — they assert that every builder ships the rule text
that drives the four behavioral scenarios:
  (a) "customer wants a personal loan for a Europe trip" → answer, financing angle
  (b) "suggest places to visit in Europe"                → decline, verbatim line
  (c) "how does our rate compare to SBI's?"              → Tier C caveat + HDFC pivot
  (d) HDFC fact missing from context                     → exact fallback line

Run:  python -m pytest tests/test_chat_prompt_rules.py -q
"""

from prompts.chat import (
    _FALLBACK,
    _NO_LEARNINGS,
    build_chat_prompt,
    build_chat_with_messages_prompt,
    build_key_learnings_prompt,
    build_trivial_chat_prompt,
)

DECLINE_LINE = (
    "I'm Arjun, your HDFC Banking & Sales assistant. I can help with loans, "
    "banking products, eligibility, documentation, and sales conversations"
)


def _chat_prompt(question, intent="factual"):
    return build_chat_prompt(
        question=question,
        intent=intent,
        history_lines="(no prior turns — this is the first message)\n",
        context="HDFC personal loan rate: 10.5% p.a. Processing fee: 1%.",
    )


def _messages_prompt(question):
    return build_chat_with_messages_prompt(
        question=question,
        history_str="",
        pitch_block="",
        context="HDFC personal loan rate: 10.5% p.a.",
    )


# ── Scenario (a)+(b): RULE 9 is intent-based, not topic-based ────────────────

def test_scope_rule_answers_on_financial_hook():
    p = _chat_prompt("customer wants a personal loan for a Europe trip")
    assert "decide by INTENT, not surface topic" in p
    assert "REASON for a loan" in p          # trip/wedding etc. is in-scope context
    assert "travel" in p and "wedding" in p  # the reason list is present
    assert "financing angle" in p            # ambiguous → pull toward financing


def test_scope_rule_declines_only_on_zero_hook():
    p = _chat_prompt("suggest places to visit in Europe")
    assert "ZERO financial hook" in p
    assert DECLINE_LINE in p                 # verbatim decline line shipped
    assert "do not add anything after" in p  # nothing appended after decline


def test_messages_builder_mirrors_scope():
    p = _messages_prompt("customer wants a loan for his daughter's wedding")
    assert "decide by INTENT, not surface topic" in p
    assert "ZERO financial hook" in p


def test_trivial_builder_invites_financing_instead_of_declining():
    p = build_trivial_chat_prompt("hi")
    assert "rather than declining" in p
    assert "not general knowledge" not in p


# ── Scenario (c): competitor comparison routes through Tier C ────────────────

def test_comparison_intent_routes_through_tier_c():
    p = _chat_prompt("how does our rate compare to SBI's?", intent="comparison")
    assert "TIER C" in p
    assert "indicative market context" in p
    assert "never quote an exact competitor rate" in p
    assert "verifying their current rates" in p       # the caveat
    assert "document-backed edge" in p                # the pivot back to HDFC
    # RULE 8 comparison style routes both sides explicitly
    assert "competitor side via Tier C only" in p
    assert "HDFC side via Tier A documents" in p


def test_tier_hard_line_present_in_both_builders():
    for p in (_chat_prompt("q"), _messages_prompt("q")):
        assert "HARD LINE" in p
        assert "Tier C knowledge must NEVER produce a Tier A claim" in p


# ── Scenario (d): missing HDFC fact → exact fallback, no invented number ─────

def test_fallback_line_is_interpolated_verbatim():
    for p in (_chat_prompt("q"), _messages_prompt("q")):
        assert _FALLBACK in p
        assert "never substitute a number" in p


# ── Regression: signatures & structure unchanged ─────────────────────────────

def test_rule_structure_preserved():
    p = _chat_prompt("q")
    for rule in ("RULE 1", "RULE 2", "RULE 3", "RULE 4", "RULE 5",
                 "RULE 6", "RULE 7", "RULE 8", "RULE 9"):
        assert f"━━━ {rule} " in p


def test_calc_rule_untouched_tier_b_defers_to_it():
    p = _chat_prompt("q")
    assert "TIER B" in p
    assert "You do NOT calculate" in p  # RULE 4 still governs the math


# ── Key Learnings debrief builder ────────────────────────────────────────────

KL_TRANSCRIPT = (
    "Agent: customer wants a 20 lakh personal loan for his shop, income 80k\n"
    "Arjun: At 10.5% for 5 years the EMI comes to ₹43,391...\n"
    "Agent: he says the processing fee is too high, I told him it's 3%\n"
    "Arjun: Correction — the processing fee is 1% per the documents...\n"
)


def test_key_learnings_full_chat_prompt_shape():
    # scenario (a): full deal chat with an objection and a computed EMI
    p = build_key_learnings_prompt(KL_TRANSCRIPT, entities={"loan_amount": "20 lakh"})
    assert "GROUNDING — TRANSCRIPT ONLY" in p
    for section in ("Customer snapshot", "Facts to remember",
                    "Objections & handling", "Gaps to fix", "Next step"):
        assert section in p
    assert "exactly ONE action" in p                       # single next step
    assert "EXACTLY as it appeared" in p                   # figures copied, never re-derived
    assert "Do NOT add new HDFC facts from memory" in p    # parametric-leak guard
    assert "never address the customer" in p               # coaching, not customer-facing
    assert KL_TRANSCRIPT in p
    assert "Loan amount: 20 lakh" in p                     # _entities_block reused


def test_key_learnings_thin_chat_abstains():
    # scenario (b): greeting-only chat → verbatim "not enough" line, nothing invented
    p = build_key_learnings_prompt("Agent: hi\nArjun: Hello! How can I help?\n")
    assert _NO_LEARNINGS in p
    assert "reply ONLY with" in p
    assert "Do not invent sections" in p


def test_key_learnings_misquote_surfaces_in_gaps():
    # scenario (c): misquoted charge → Gaps to fix with both numbers
    doc = "HDFC personal loan processing fee: 1% of sanctioned amount."
    with_ctx = build_key_learnings_prompt(KL_TRANSCRIPT, context=doc)
    without_ctx = build_key_learnings_prompt(KL_TRANSCRIPT)
    # in-chat correction path works even without retrieval context
    assert "later corrected, show both numbers" in without_ctx
    assert "DOCUMENT CONTEXT" not in without_ctx
    # with context: verification-only block, never a topic source
    assert "VERIFICATION only" in with_ctx
    assert doc in with_ctx
    assert "flag that under Gaps to fix with both numbers" in with_ctx
    assert "introduce topics the conversation never touched" in with_ctx


def test_key_learnings_language_modes():
    en = build_key_learnings_prompt("Agent: x\n", language="english")
    assert "predominantly used" in en                      # dominant-language mirror
    mr = build_key_learnings_prompt("Agent: x\n", language="marathi")
    assert "Marathi ONLY" in mr and "Devanagari script" in mr


def test_key_learnings_does_not_touch_existing_builders():
    # the four pre-existing public builders keep their signatures & structure
    p = _chat_prompt("q")
    assert "━━━ RULE 9 " in p
    assert "KEY LEARNINGS" not in p
