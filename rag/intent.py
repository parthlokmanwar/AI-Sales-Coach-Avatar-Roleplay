"""
Query expansion + intent detection — pure helpers (no state, no self).

Used by:
  - Ask Me chat   → app calls expand_query() before retrieval, detect_intent() to shape the prompt
  - Voice path    → app calls expand_query() before retrieval
  - Inspector     → exposes both so the UI can show what the LLM sees

Detection is regex/keyword only — zero LLM latency.

Edit this file when you want to tune which keywords map to which intent
or which expansion phrases get appended for vector recall.
"""

from typing import List, Optional


# ── Query expansion: append domain keywords to improve recall ───────────────

EXPANSION_MAP = [
    ({"eligib", "qualify", "approve", "income", "salary", "foir", "obligation"},
     "FOIR Fixed Obligation Income Ratio eligibility segment maximum loan income criteria"),
    ({"emi", "instalment", "installment", "monthly payment", "repay"},
     "EMI equated monthly instalment tenure calculation reducing balance"),
    ({"rate", "interest", "roi", "percent", "%", "apr", "byaj"},
     "interest rate ROI reducing balance annual percentage processing fee"),
    ({"document", "doc", "kyc", "proof", "aadhaar", "pan", "passport", "form 16", "itr"},
     "KYC documents required proof identity address income salaried self-employed"),
    ({"cibil", "credit score", "credit history", "bureau", "score"},
     "CIBIL credit score bureau minimum required history"),
    ({"fee", "charge", "processing", "cost", "gst", "stamp"},
     "processing fee charges GST stamp duty upfront costs"),
    ({"foreclose", "prepay", "preclos", "early", "part payment", "part-pay"},
     "foreclosure prepayment charges penalty lock-in period outstanding principal"),
    ({"amount", "maximum", "minimum", "limit", "how much", "kitna", "lakh", "crore"},
     "maximum minimum loan amount limit slab segment"),
    ({"tenure", "duration", "period", "years", "months", "term", "saal", "mahina"},
     "tenure repayment period minimum maximum months years"),
    ({"business", "turnover", "vintage", "msme", "proprietor"},
     "business loan GST turnover vintage MSME proprietorship eligibility"),
    ({"home", "property", "lap", "mortgage", "ltv", "valuation"},
     "home loan LAP loan against property LTV valuation mortgage"),
]


def expand_query(question: str) -> str:
    """Return the original question with domain-specific keywords appended.
    The expanded text feeds vector retrieval; the original question feeds the LLM."""
    lower_q = (question or "").lower()
    expansions = [exp for kws, exp in EXPANSION_MAP if any(kw in lower_q for kw in kws)]
    return question + (" " + " ".join(expansions) if expansions else "")


def expansion_phrases_used(question: str) -> List[str]:
    """For the inspector — return the list of expansion phrases that triggered.
    Helps the UI show 'these are the extra terms we added before vector search'."""
    lower_q = (question or "").lower()
    return [exp for kws, exp in EXPANSION_MAP if any(kw in lower_q for kw in kws)]


# ── Intent classification ────────────────────────────────────────────────────

FOLLOWUP_MARKERS = [
    # English
    "tell me more", "more about that", "what about that", "and that",
    "what did you mean", "go on", "continue", "explain that",
    "the second", "the first", "the last one", "elaborate",
    "what else", "anything else", "more details",
    # Hinglish / Hindi shorthand
    "uska kya", "iske baare", "aur batao", "aur kuch", "or kuch",
    "kuch aur", "aur kya", "batao", "bata", "samjhao",
    "samjha nahi", "samajh nahi", "who bat", "wo bat",
    "yeh wala", "woh wala", "matlab kya",
]

COMPARISON_MARKERS = [
    " vs ", "versus", "compare", "compared to", "difference between",
    "better than", "sbi", "icici", "axis", "kotak", "bajaj", "bandhan",
    "other bank", "different bank",
]

OBJECTION_MARKERS = [
    "too high", "too expensive", "expensive", "rejected", "denied",
    "wait", "later", "think about", "not sure", "can't afford",
    "low cibil", "bad credit", "mahenga", "zyada", "nahi chahiye",
    "no thanks", "not interested", "concern", "worried about",
]

PROCEDURAL_MARKERS = [
    "how do i", "how to", "what is the process", "steps to",
    "apply for", "application", "kaise apply", "kaise karu",
    "procedure", "documents needed", "documents required",
]


def detect_intent(question: str, history: Optional[list] = None) -> str:
    """Classify the user's question.

    Returns one of:
      factual | comparison | objection | follow_up | procedural
    """
    q = (question or "").lower().strip()
    if not q:
        return "factual"

    if history and len(history) > 0 and any(m in q for m in FOLLOWUP_MARKERS):
        return "follow_up"
    # Short messages (≤ 5 words) that reference prior context
    if history and len(history) > 0 and len(q.split()) <= 5 and any(
        w in q for w in [
            "that", "this", "it", "them", "uska", "iska", "wo", "ye",
            "acha", "accha", "okie", "ok", "haan", "theek", "batao",
        ]
    ):
        return "follow_up"

    if any(m in q for m in COMPARISON_MARKERS):
        return "comparison"

    if any(m in q for m in OBJECTION_MARKERS):
        return "objection"

    if any(m in q for m in PROCEDURAL_MARKERS):
        return "procedural"

    return "factual"
