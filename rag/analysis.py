"""
Live speech analysis — pure function, zero LLM latency.

Used by the Live Voice path to enrich the prompt with:
  language_mix    — english | hindi | hinglish | marathi | marathi_mix
  tone            — curious | skeptical | worried | confused | interested
                    | objecting | comparing_competitor
  question_type   — rate_query | eligibility_query | emi_query | process_query
                    | objection | competitor_mention | general
  key_entities    — extracted: loan_amount, tenure, income, cibil, product
  urgency         — high | normal
  is_followup     — bool (only true if `history` was provided AND the
                    transcript looks like a continuation)
  summary         — human-readable one-line digest

Everything is regex/keyword based — edit the marker sets in this file
to tune sensitivity.
"""

import re
from typing import Any, Dict, Optional


HINGLISH_MARKERS = {
    # Unambiguous Hindi words that do NOT appear in Indian English prose
    'kitna', 'kya', 'kaise', 'matlab', 'bhai', 'zyada', 'thoda',
    'aapka', 'mera', 'hoga', 'chahiye', 'milega', 'saal',
    'mahina', 'mahine', 'nahi', 'haan', 'lekin', 'toh',
    'liye', 'tak', 'kam', 'accha', 'theek',
    'bilkul', 'zaroor', 'bahut', 'wala', 'kab', 'kyun',
    # 'lakh' removed — standard Indian English; appears in every loan question in English
    # 'aur', 'pe', 'ke', 'se' removed — too short, appear in English as substrings
}

MARATHI_MARKERS = {'aahe', 'aaplyala', 'tumhi', 'mala', 'kasa', 'kaay',
                   'pahije', 'milel', 'karaycha'}

COMPETITOR_TERMS = {'sbi', 'icici', 'axis', 'kotak', 'bajaj', 'bandhan',
                    'idfc', 'yes bank', 'standard chartered', 'pnb'}

WORRY_MARKERS = {'worried', 'tension', 'problem', 'issue', 'reject', 'denied',
                 'mushkil', 'dar', 'pareshaan', 'fail', 'bad credit'}

OBJECTION_MARKERS = {'expensive', 'high rate', 'better offer', 'compare',
                     'wait', 'sochna', 'zyada', 'mehenga', 'costly',
                     'too high', "can't afford", 'cant afford', 'mahenga'}

CONFUSED_MARKERS = {'samajh nahi', "i don't understand", 'confused', 'matlab kya',
                    'clarify', 'kya matlab', 'explain'}

INTERESTED_MARKERS = {'sounds good', 'interested', 'tell me more', 'go ahead',
                      'pasand', 'theek lagta'}

URGENCY_MARKERS = ['jaldi', 'urgent', 'asap', 'right now', 'abhi', 'turant',
                   'today only', 'immediately', 'aaj hi', 'quick']


def analyze_speech(text: str, history: Optional[list] = None) -> Dict[str, Any]:
    lower = (text or "").lower().strip()
    result: Dict[str, Any] = {
        "language_mix": "english",
        "tone": "curious",
        "question_type": "general",
        "key_entities": {},
        "urgency": "normal",
        "is_followup": False,
        "summary": "",
    }
    if not lower:
        return result

    # ── Language mix ────────────────────────────────────────────────────────
    has_devanagari = bool(re.search(r'[ऀ-ॿ]', text))
    words = lower.split()
    hinglish_count = sum(1 for w in words if w in HINGLISH_MARKERS)
    marathi_count = sum(1 for w in words if w in MARATHI_MARKERS)

    if has_devanagari and marathi_count >= 2:
        result["language_mix"] = "marathi"
    elif has_devanagari and hinglish_count == 0:
        result["language_mix"] = "hindi"
    elif has_devanagari:
        result["language_mix"] = "hinglish"
    elif marathi_count >= 1:
        result["language_mix"] = "marathi_mix"
    elif hinglish_count >= 3:
        # Require 3+ unambiguous Hindi words (not just "lakh + pe") to call it Hinglish
        result["language_mix"] = "hinglish"
    else:
        result["language_mix"] = "english"

    # ── Tone ────────────────────────────────────────────────────────────────
    has_competitor = any(c in lower for c in COMPETITOR_TERMS)
    if has_competitor:
        result["tone"] = "comparing_competitor"
    elif any(m in lower for m in OBJECTION_MARKERS):
        result["tone"] = "objecting"
    elif any(m in lower for m in WORRY_MARKERS):
        result["tone"] = "worried"
    elif any(m in lower for m in CONFUSED_MARKERS):
        result["tone"] = "confused"
    elif any(m in lower for m in INTERESTED_MARKERS):
        result["tone"] = "interested"
    elif '?' in text or any(w in {'what', 'how', 'when', 'kitna', 'kaise', 'kab', 'kya'} for w in words):
        result["tone"] = "curious"
    else:
        result["tone"] = "skeptical" if 'really' in lower or 'sure' in lower else "curious"

    # ── Question type ───────────────────────────────────────────────────────
    if has_competitor:
        result["question_type"] = "competitor_mention"
    elif any(w in lower for w in ['rate', 'interest', 'roi', 'byaj', '%', 'percent']):
        result["question_type"] = "rate_query"
    elif any(w in lower for w in ['eligib', 'qualify', 'eligibility', 'foir', 'income required',
                                   'salary kitni', 'minimum income']):
        result["question_type"] = "eligibility_query"
    elif any(w in lower for w in ['emi', 'instalment', 'installment', 'monthly', 'kitni emi',
                                   'emi kitni', 'payment']):
        result["question_type"] = "emi_query"
    elif any(w in lower for w in ['how do i', 'how to apply', 'process', 'steps', 'documents',
                                   'kaise apply', 'kaise karu', 'procedure']):
        result["question_type"] = "process_query"
    elif result["tone"] in ("objecting", "skeptical", "worried"):
        result["question_type"] = "objection"
    else:
        result["question_type"] = "general"

    # ── Key entities ────────────────────────────────────────────────────────
    entities: Dict[str, str] = {}

    # --- Income / salary extracted FIRST so its number can guard the raw fallback ---
    # Pattern A: income keyword BEFORE the number ("income is 50k", "salary 2 lakh")
    # earn\w* catches earn/earns/earning/earnings; net monthly / monthly income are phrases.
    income_m = re.search(
        r'(salary|income|earn\w*|kamai|net monthly|monthly income)[^\d]{0,20}(\d+(?:[.,]\d+)?)\s*(k|lakh|lac|cr|rs\.?)?',
        lower,
    )
    if income_m:
        entities["income"] = income_m.group(0).strip()

    # Pattern B: number BEFORE income keyword ("50k income", "2 lakh salary", "2 lakh per month salary")
    # Only runs when Pattern A didn't fire, so it never double-extracts.
    if not income_m:
        income_m = re.search(
            r'(\d+(?:[.,]\d+)?)\s*(?:k|lakh|lac|cr\b|lacs|lakhs|rs\.?)?\s*'
            r'(?:per\s+month\s+|monthly\s+|pm\s+)?(?:net\s+)?(?:salary|income)\b',
            lower,
        )
        if income_m:
            entities["income"] = income_m.group(0).strip()

    # Named Indian units: "5 lakh", "2.5 crore"
    # Iterate ALL matches so a sentence like "I earn 2 lakh, want 20 lakh loan"
    # correctly skips the income amount and captures the loan amount.
    for amount_m in re.finditer(
        r'(\d+(?:[.,]\d+)?)\s*(lakh|lac|cr\b|crore|thousand|hazaar|k\b|lacs|lakhs)',
        lower,
    ):
        # Guard 1: number falls inside the income match span (keyword-before-number case)
        inside_income = income_m and (income_m.start() <= amount_m.start() <= income_m.end())
        # Guard 2: an income/salary keyword appears immediately AFTER the matched amount
        # (catches "2 lakh income", "50k salary", "2 lakh per month" — number-before-keyword)
        has_income_after = bool(re.search(
            r'^\s*(?:per\s+month\s+|monthly\s+|pm\s+)?(?:net\s+)?(?:salary|income|earn\w*|kamai)\b',
            lower[amount_m.end(): amount_m.end() + 40],
        ))
        if not inside_income and not has_income_after:
            entities["loan_amount"] = amount_m.group(0)
            break  # first valid (non-income) amount is the loan amount

    # Raw Indian comma-formatted numbers: ₹10,00,000 or 1000000 or 10,00000
    # Only used if a named-unit amount was NOT already found AND the number is
    # NOT the same digits already captured as income.
    if "loan_amount" not in entities:
        raw_m = re.search(r'(?:₹|rs\.?\s*)?(\d[\d,]{4,})\b', lower)
        if raw_m:
            raw_digits = raw_m.group(1).replace(',', '')
            # Extract numeric digits from the income match for comparison
            income_digits = None
            if income_m:
                income_num = re.search(r'\d+(?:[.,]\d+)?', income_m.group(0))
                if income_num:
                    income_digits = income_num.group(0).replace(',', '')
            # Guard: skip if this number IS the income number
            is_income_number = (income_digits is not None and income_digits == raw_digits)
            # Guard: skip if income/salary keywords surround this number
            raw_start = raw_m.start()
            surrounding = lower[max(0, raw_start - 45): raw_start + 15]
            has_income_context = bool(re.search(
                r'salary|income|earn\w*|kamai|net monthly|monthly|net pay', surrounding
            ))
            if not is_income_number and not has_income_context:
                entities["loan_amount_raw"] = raw_digits

    # Tenure (first explicit tenure): "5 years", "36 months", "5 saal"
    tenure_m = re.search(r'(\d+)\s*(saal|year|years|yr\b|yrs|month|mahina|mahine|months)', lower)
    if tenure_m:
        entities["tenure"] = tenure_m.group(0)

    # Desired early closure / foreclosure period — distinct from the loan tenure.
    # Handles: "close in 3 years", "foreclose after 36 months", "prepay in 3 years"
    closure_m = re.search(
        r'(?:close|foreclose|foreclos\w*|prepay\w*|closure|early\s+(?:closure|repayment))'
        r'[^.]{0,40}?(\d+)\s*(year|years|yr\b|yrs|month|months)',
        lower,
    )
    if closure_m:
        entities["foreclosure_period"] = closure_m.group(0).strip()

    # Percentage rates: "2%", "10.5%", "18 percent"
    pct_m = re.search(r'(\d+(?:\.\d+)?)\s*(?:%|percent)', lower)
    if pct_m:
        entities["rate_pct"] = pct_m.group(0)

    # Outstanding principal / balance — common in foreclosure / prepayment Qs
    outstanding_m = re.search(
        r'outstanding\s*(?:principal|balance|amount)?[^\d]{0,15}(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|lacs|lakhs|crore|cr)?',
        lower,
    )
    if outstanding_m:
        entities["outstanding_principal"] = outstanding_m.group(0).strip()

    # CIBIL score
    cibil_m = re.search(r'cibil[^\d]{0,10}(\d{3,4})|score[^\d]{0,10}(\d{3,4})', lower)
    if cibil_m:
        entities["cibil"] = cibil_m.group(0)

    # Product type
    for product, label in [
        ('personal loan', 'personal loan'),
        ('home loan', 'home loan'),
        ('business loan', 'business loan'),
        ('lap ', 'LAP'),
        ('loan against property', 'LAP'),
        ('credit card', 'credit card'),
    ]:
        if product in lower:
            entities["product"] = label
            break

    result["key_entities"] = entities

    # ── Urgency ─────────────────────────────────────────────────────────────
    impatient = sum(1 for m in URGENCY_MARKERS if m in lower) > 0
    many_exclaims = text.count('!') >= 2
    result["urgency"] = "high" if impatient or many_exclaims else "normal"

    # ── Follow-up detection ─────────────────────────────────────────────────
    followup_text_markers = ['tell me more', 'more about that', 'what about that',
                             'and that', 'what did you mean', 'explain that',
                             'continue', 'go on', 'aur batao', 'uska kya', 'iske baare']
    result["is_followup"] = bool(history) and (
        any(m in lower for m in followup_text_markers)
        or (len(words) <= 4 and any(w in lower for w in ['that', 'this', 'it', 'them',
                                                          'uska', 'iska', 'wo', 'ye']))
    )

    # Human-readable summary
    parts = [f"Language: {result['language_mix']}", f"Tone: {result['tone']}",
             f"Type: {result['question_type']}", f"Urgency: {result['urgency']}"]
    if entities:
        parts.append("Entities: " + ", ".join(f"{k}={v}" for k, v in entities.items()))
    if result["is_followup"]:
        parts.append("(follow-up)")
    result["summary"] = " | ".join(parts)
    return result
