"""
Deterministic EMI calculator + Hinglish/English number parsing.

Why this exists:
  LLMs (even 14B ones) regularly mis-compute EMI because the formula has a
  compounded exponential. Doing it in Python guarantees the number is right,
  and the LLM just has to phrase the pre-computed values naturally.

Public API:
  parse_amount("5 lakh") -> 500000
  parse_tenure("3 saal") -> 36   (months)
  compute_emi(principal_rupees, n_months, annual_rate_pct) -> dict
  format_for_prompt(calc_result) -> str  (a one-line summary the LLM can quote)

Used by features/voice.py — when the question is an EMI / calculation query,
we inject the computed numbers into the prompt before the LLM ever sees it.
"""

from __future__ import annotations

import re
from typing import Optional, Dict


# ── Hindi number words (Roman script — what STT/clean-transcript actually outputs)
# Cover the common 1-50 range that fits "X lakh / X saal" patterns.
_HINDI_NUMS: Dict[str, int] = {
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4,
    "paanch": 5, "panch": 5, "che": 6, "chhe": 6, "saat": 7, "saath": 7,
    "aath": 8, "nau": 9, "naw": 9, "das": 10, "gyarah": 11, "barah": 12,
    "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16, "satrah": 17,
    "atharah": 18, "unnis": 19, "bees": 20, "biis": 20,
    "pachees": 25, "pachis": 25, "tees": 30, "chalis": 40, "pachas": 50,
    # Plain English numerals as words (sometimes Whisper writes "five" instead of "5")
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "fifty": 50,
}


def _word_to_number(word: str) -> Optional[int]:
    """Map a single Hindi/English number word to its integer, or None."""
    return _HINDI_NUMS.get(word.lower().strip())


# ── Amount parsing ──────────────────────────────────────────────────────────

# Multipliers — what comes after the number maps it to rupees.
_AMOUNT_UNITS = {
    "k": 1_000, "thousand": 1_000, "hazaar": 1_000, "hajaar": 1_000,
    "lakh": 100_000, "lac": 100_000, "lakhs": 100_000, "lacs": 100_000,
    "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
}


def parse_amount(text: str) -> Optional[int]:
    """
    Extract a loan amount from free-form text and return rupees as int.

    Accepts:
      "5 lakh"        -> 500000
      "paanch lakh"   -> 500000  (Hindi numeral)
      "10 lakhs"      -> 1000000
      "1 crore"       -> 10000000
      "50000"         -> 50000   (bare number, treated as rupees)
      "5L"            -> 500000  (shorthand)

    Returns None if no amount found.
    """
    if not text:
        return None
    t = text.lower()

    # 1) Digit + unit  (5 lakh, 10 lakhs, 1.5 crore)
    m = re.search(
        r'(\d+(?:[.,]\d+)?)\s*(lakhs?|lacs?|crores?|cr|thousand|hazaar|hajaar|k)\b',
        t,
    )
    if m:
        num = float(m.group(1).replace(",", ""))
        unit = m.group(2)
        if unit == "cr":
            unit = "crore"
        return int(num * _AMOUNT_UNITS[unit])

    # 2) "5L" shorthand (no space)
    m = re.search(r'(\d+(?:\.\d+)?)l\b', t)
    if m:
        return int(float(m.group(1)) * 100_000)

    # 3) Hindi number word + unit  (paanch lakh, das lakh)
    for word, n in _HINDI_NUMS.items():
        m = re.search(rf'\b{word}\b\s*(lakhs?|lacs?|crores?|cr|thousand|hazaar|hajaar|k)\b', t)
        if m:
            unit = m.group(1)
            if unit == "cr":
                unit = "crore"
            return int(n * _AMOUNT_UNITS[unit])

    # 4) Bare large number — assume rupees if >= 10000
    m = re.search(r'\b(\d{5,})\b', t)
    if m:
        return int(m.group(1))

    return None


# ── Tenure parsing ──────────────────────────────────────────────────────────

def parse_tenure(text: str) -> Optional[int]:
    """
    Extract tenure from free-form text and return months as int.

    Accepts:
      "5 year" / "5 saal" / "5 yrs"  -> 60
      "60 months" / "60 mahine"      -> 60
      "paanch saal"                   -> 60
      "teen saal"                     -> 36

    Returns None if no tenure found.
    """
    if not text:
        return None
    t = text.lower()

    # 1) Digit + unit
    m = re.search(r'(\d+)\s*(saal|year|years|yr|yrs)\b', t)
    if m:
        return int(m.group(1)) * 12
    m = re.search(r'(\d+)\s*(months?|mahine|mahina|mhne)\b', t)
    if m:
        return int(m.group(1))

    # 2) Hindi word + unit
    for word, n in _HINDI_NUMS.items():
        m = re.search(rf'\b{word}\b\s*(saal|year|years|yr|yrs)\b', t)
        if m:
            return n * 12
        m = re.search(rf'\b{word}\b\s*(months?|mahine|mahina|mhne)\b', t)
        if m:
            return n

    return None


# ── EMI math ────────────────────────────────────────────────────────────────

def compute_emi(
    principal_rupees: int,
    n_months: int,
    annual_rate_pct: float = 10.5,
) -> Dict[str, float]:
    """
    Standard reducing-balance EMI formula.
        EMI = P * r * (1+r)^n / ((1+r)^n - 1)
    where r is monthly rate (annual/12/100), n is months.

    Returns a dict with all the numbers a sales pitch needs.
    """
    if principal_rupees <= 0 or n_months <= 0:
        raise ValueError("principal and tenure must be positive")
    if annual_rate_pct <= 0:
        raise ValueError("interest rate must be positive")

    r = annual_rate_pct / 12.0 / 100.0
    n = n_months
    pow_factor = (1 + r) ** n
    emi = principal_rupees * r * pow_factor / (pow_factor - 1)
    total = emi * n
    interest = total - principal_rupees
    return {
        "principal": principal_rupees,
        "tenure_months": n_months,
        "annual_rate_pct": annual_rate_pct,
        "emi": round(emi),
        "total_payment": round(total),
        "total_interest": round(interest),
    }


# ── Prompt-ready formatting ─────────────────────────────────────────────────

def _fmt_inr(rupees: float) -> str:
    """Indian-style comma grouping: 1,44,820 not 144,820."""
    n = int(round(rupees))
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        # Group rest by 2 from right
        parts = []
        while len(rest) > 2:
            parts.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.append(rest)
        out = ",".join(reversed(parts)) + "," + last3
    return ("-" if n < 0 else "") + out


def format_for_prompt(calc: Dict[str, float]) -> str:
    """One-line summary the LLM can quote verbatim — no math needed."""
    return (
        f"COMPUTED EMI (exact, do not recompute): "
        f"Principal = Rs. {_fmt_inr(calc['principal'])}, "
        f"Tenure = {calc['tenure_months']} months "
        f"({calc['tenure_months'] // 12} year{'s' if calc['tenure_months'] // 12 != 1 else ''}), "
        f"Rate = {calc['annual_rate_pct']}% p.a. → "
        f"Monthly EMI = Rs. {_fmt_inr(calc['emi'])}, "
        f"Total Payment = Rs. {_fmt_inr(calc['total_payment'])}, "
        f"Total Interest = Rs. {_fmt_inr(calc['total_interest'])}."
    )


# ── Question classifier ─────────────────────────────────────────────────────

_CALC_KEYWORDS = [
    # English
    "emi", "calculate", "calculation", "computation", "monthly payment",
    "monthly installment", "monthly instalment", "installment", "instalment",
    "how much will i pay", "how much i pay", "kitna pay", "kitna dena",
    "total interest", "total payment",
    # Hindi/Hinglish
    "kitni emi", "emi kitni", "kitna hoga", "kitne ka hoga", "calculate kar",
    "nikal", "nikalo", "nikalke", "byaj kitna", "byaj kitne",
]


def is_calculation_question(text: str) -> bool:
    """Heuristic: should we run the EMI calculator + use the smarter model?"""
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in _CALC_KEYWORDS)
