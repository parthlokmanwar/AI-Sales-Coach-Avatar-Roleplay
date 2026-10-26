"""
Pure Python financial calculator for banking loan products.

Architecture rationale
──────────────────────
LLMs cannot reliably do multi-step floating-point arithmetic.  A 20 lakh loan
at 14% for 60 months requires computing (1.01167)^60, then doing a chain of
divisions — and small models routinely get the total-interest sign wrong, invent
impossible outstanding balances, or hallucinate income.

Solution: ALL arithmetic happens here in Python before the LLM is called.
The LLM receives a PRE-COMPUTED FINANCIAL RESULTS block and is told to narrate
those exact numbers — it never performs calculations itself.

Formulas used
─────────────
  EMI (reducing balance) — standard Indian bank method (RBI mandated)
    EMI = P × r × (1+r)^n / ((1+r)^n − 1)
    where r = annual_rate / 12 / 100,  n = tenure_months

  Total interest (correct)
    Total Interest = (EMI × n) − P

  Outstanding principal after k payments (reducing balance)
    Outstanding(k) = EMI × (1 − (1+r)^(−(n−k))) / r
    Boundary checks:  k=0 → P  |  k=n → 0

  FOIR
    FOIR% = (existing_obligations + new_EMI) / net_income × 100
    max_eligible_EMI  = net_income × FOIR_limit% − existing_obligations
    max_eligible_loan = max_EMI × ((1+r)^n − 1) / (r × (1+r)^n)

  Foreclosure total cost
    charge  = outstanding × charge_rate%
    GST     = charge × gst_rate%  (or "prevailing regulations" if unknown)
    total   = outstanding + charge + GST
"""

import re
from typing import Optional


# ── Policy config (override these when values are confirmed from the product doc) ──
class _Config:
    foir_threshold_pct: float = 50.0
    gst_pct: float = 18.0
    foreclosure_charge_pct: Optional[float] = None   # None → "confirm with product team"

CONFIG = _Config()


# ── Parsing helpers ────────────────────────────────────────────────────────────

def parse_amount(text: str) -> Optional[float]:
    """
    Parse Indian amount strings → float rupees.
    Handles: '20 lakh', '2.5 crore', '₹10,00,000', '500000', '50k',
             'income is 45,000 rs', 'monthly income is 45,000'
    """
    if not text:
        return None
    s = (text.lower()
         .replace(',', '')
         .replace('₹', '')
         .replace('rs.', '')
         .replace('rs', '')
         .strip())

    m = re.search(r'([\d.]+)\s*(crore|cr\b)', s)
    if m:
        return float(m.group(1)) * 1_00_00_000

    m = re.search(r'([\d.]+)\s*(lakh|lac|lakhs|lacs)', s)
    if m:
        return float(m.group(1)) * 1_00_000

    m = re.search(r'([\d.]+)\s*k\b', s)
    if m:
        return float(m.group(1)) * 1_000

    m = re.search(r'(\d+)', s)
    if m:
        return float(m.group(1))

    return None


def parse_tenure_months(text: str) -> Optional[int]:
    """
    Parse tenure/closure strings → int months.
    Handles: '5 years', '60 months', '5 saal', 'close in 3 years'
    """
    if not text:
        return None
    s = text.lower()

    m = re.search(r'(\d+)\s*(year|years|yr\b|yrs|saal)', s)
    if m:
        return int(m.group(1)) * 12

    m = re.search(r'(\d+)\s*(month|months|mahina|mahine)', s)
    if m:
        return int(m.group(1))

    return None


def parse_rate(text: str) -> Optional[float]:
    """Parse '14%', '14.5 percent', '14.5% p.a.' → float."""
    if not text:
        return None
    m = re.search(r'([\d.]+)\s*(?:%|percent)', text.lower())
    if m:
        val = float(m.group(1))
        if 1.0 < val < 50.0:   # sane rate range
            return val
    return None


# ── Core financial formulas ────────────────────────────────────────────────────

def compute_emi(principal: float, annual_rate_pct: float, tenure_months: int) -> dict:
    """
    Reducing-balance EMI (RBI / Indian bank standard).

    EMI = P × r × (1+r)^n / ((1+r)^n − 1)
    Total interest = (EMI × n) − P       ← the ONLY correct formula
    """
    P = principal
    r = annual_rate_pct / 12 / 100
    n = tenure_months

    if r == 0:
        emi = P / n
    else:
        factor = (1 + r) ** n
        emi = P * r * factor / (factor - 1)

    total_payment = emi * n
    total_interest = total_payment - P          # correct — not some other variant

    return {
        "emi":            round(emi),
        "total_payment":  round(total_payment),
        "total_interest": round(total_interest),
        "rate_pct":       annual_rate_pct,
        "tenure_months":  n,
        "principal":      round(P),
    }


def compute_outstanding(principal: float, annual_rate_pct: float,
                        tenure_months: int, paid_months: int) -> float:
    """
    Outstanding principal after `paid_months` regular payments.

    Formula: EMI × (1 − (1+r)^(−(n−k))) / r
    Verified: k=0 → P,  k=n → 0
    """
    if paid_months >= tenure_months:
        return 0.0

    r = annual_rate_pct / 12 / 100
    n = tenure_months
    k = paid_months

    if r == 0:
        emi = principal / n
        return round(emi * (n - k))

    factor = (1 + r) ** n
    emi    = principal * r * factor / (factor - 1)
    outstanding = emi * (1 - (1 + r) ** (-(n - k))) / r
    return round(outstanding)


def compute_foir(net_income: float, new_emi: float,
                 existing_obligations: float = 0.0) -> dict:
    """FOIR = (existing + new_emi) / net_income × 100"""
    total_emi = existing_obligations + new_emi
    foir_pct  = (total_emi / net_income * 100) if net_income > 0 else None
    return {
        "foir_pct":            round(foir_pct, 1) if foir_pct is not None else None,
        "total_monthly_emi":   round(total_emi),
        "existing_obligations": round(existing_obligations),
        "new_emi":             round(new_emi),
        "net_income":          round(net_income),
    }


def max_eligible_loan(net_income: float, annual_rate_pct: float,
                      tenure_months: int, foir_limit_pct: float = 50.0,
                      existing_obligations: float = 0.0) -> dict:
    """
    Largest loan where FOIR stays within the policy limit.

    max_EMI  = net_income × foir_limit% − existing_obligations
    max_loan = max_EMI × ((1+r)^n − 1) / (r × (1+r)^n)
    """
    max_emi = net_income * foir_limit_pct / 100 - existing_obligations
    if max_emi <= 0:
        return {"max_emi": 0, "max_loan": 0, "foir_limit_pct": foir_limit_pct}

    r = annual_rate_pct / 12 / 100
    n = tenure_months

    if r == 0:
        max_loan = max_emi * n
    else:
        factor   = (1 + r) ** n
        max_loan = max_emi * (factor - 1) / (r * factor)

    return {
        "max_emi":        round(max_emi),
        "max_loan":       round(max_loan),
        "foir_limit_pct": foir_limit_pct,
    }


def compute_foreclosure(principal: float, annual_rate_pct: float,
                        tenure_months: int, paid_months: int,
                        charge_rate_pct: float,
                        gst_rate_pct: Optional[float] = None) -> dict:
    """
    Total cost to foreclose a loan after `paid_months` regular payments.

    outstanding = compute_outstanding(...)
    charge      = outstanding × charge_rate_pct / 100
    GST         = charge × gst_rate_pct / 100  (None if rate unknown from docs)
    """
    outstanding = compute_outstanding(
        principal, annual_rate_pct, tenure_months, paid_months
    )
    charge = round(outstanding * charge_rate_pct / 100)

    if gst_rate_pct is not None:
        gst       = round(charge * gst_rate_pct / 100)
        total_cost = outstanding + charge + gst
        gst_note   = f"{gst_rate_pct}% (from documents)"
    else:
        gst        = None
        total_cost = outstanding + charge
        gst_note   = "as per prevailing regulations — not specified in loaded documents"

    return {
        "outstanding":         outstanding,
        "charge_rate_pct":     charge_rate_pct,
        "foreclosure_charge":  charge,
        "gst_rate_pct":        gst_rate_pct,
        "gst_on_charge":       gst,
        "gst_note":            gst_note,
        "total_closure_cost":  round(total_cost),
        "paid_months":         paid_months,
    }


# ── Formatting ─────────────────────────────────────────────────────────────────

def fmt_inr(amount: float) -> str:
    """Format a float as Indian rupee string: 1227880 → ₹12,27,880"""
    s = str(int(round(abs(amount))))
    if len(s) <= 3:
        return ("−" if amount < 0 else "") + f"₹{s}"
    last3 = s[-3:]
    rest  = s[:-3]
    groups = []
    while len(rest) > 2:
        groups.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.append(rest)
    groups.reverse()
    formatted = ",".join(groups) + "," + last3
    return ("−" if amount < 0 else "") + f"₹{formatted}"


# ── Master analysis function ───────────────────────────────────────────────────

def run_financial_analysis(
    entities: dict,
    doc_foreclosure_charge_pct: Optional[float] = None,
    doc_gst_rate_pct: Optional[float] = None,
    doc_foir_limit_pct: float = 50.0,
) -> dict:
    """
    Parse entities, run all applicable calculations, return a `calc_block`
    string ready to inject into the LLM prompt.

    Design invariants:
      ✗ Never invents income — FOIR is skipped with a clear message if absent.
      ✗ Never invents a specific rate — shows results at 12 / 15 / 18% if unknown.
      ✗ Never invents GST — says "prevailing regulations" if not in documents.
      ✓ All arithmetic is Python. LLM receives numbers, not formulas.

    Returns: {"calc_block": str, "results": dict, "missing": list[str]}
    """
    missing  = []
    results  = {}
    lines    = []

    # ── Parse entities ────────────────────────────────────────────────────────
    principal = None
    if "loan_amount" in entities:
        principal = parse_amount(entities["loan_amount"])
    if principal is None and "loan_amount_raw" in entities:
        try:
            principal = float(str(entities["loan_amount_raw"]).replace(",", ""))
        except (ValueError, TypeError):
            pass

    tenure_months      = parse_tenure_months(entities.get("tenure", ""))
    foreclosure_months = parse_tenure_months(entities.get("foreclosure_period", ""))
    rate_pct           = parse_rate(entities.get("rate_pct", ""))
    existing_oblig     = 0.0   # future: parse from entities["existing_emi"] if added

    income = None
    if "income" in entities:
        income = parse_amount(entities["income"])

    # If outstanding_principal is explicit, treat it as the loan base for foreclosure
    if "outstanding_principal" in entities and principal is None:
        principal = parse_amount(entities["outstanding_principal"])

    # ── Guard: nothing to compute without a loan amount ───────────────────────
    if principal is None:
        return {"calc_block": "", "results": {}, "missing": ["loan_amount"]}

    # ── Rate selection ────────────────────────────────────────────────────────
    if rate_pct:
        use_rates = [rate_pct]
        rate_note = f"at {rate_pct}% p.a. as stated in the conversation"
    else:
        use_rates = [12.0, 15.0, 18.0]
        rate_note = "at 12%, 15%, and 18% p.a. (no specific rate mentioned — showing range)"
        missing.append("rate_pct")

    # ── EMI section ───────────────────────────────────────────────────────────
    if tenure_months:
        lines.append("── EMI CALCULATION ──────────────────────────────────────")
        lines.append(f"Principal = {fmt_inr(principal)}   |   Tenure = {tenure_months} months   |   Rate = {rate_note}")
        lines.append("")

        emi_results = []
        for r in use_rates:
            res = compute_emi(principal, r, tenure_months)
            emi_results.append(res)
            if len(use_rates) > 1:
                lines.append(f"  At {r}% p.a.:")
            lines.append(f"    Monthly EMI          = {fmt_inr(res['emi'])}")
            lines.append(f"    Total repayment      = {fmt_inr(res['total_payment'])}")
            lines.append(f"    Total interest paid  = {fmt_inr(res['total_interest'])}")
            lines.append(f"    [Check: {fmt_inr(res['total_payment'])} − {fmt_inr(principal)} = {fmt_inr(res['total_interest'])}]")
            lines.append("")

        results["emi"]     = emi_results[0] if len(emi_results) == 1 else emi_results
        results["all_emi"] = emi_results
    else:
        missing.append("tenure")

    # ── FOIR / Eligibility section ────────────────────────────────────────────
    lines.append("── FOIR / ELIGIBILITY ───────────────────────────────────")
    if income is None:
        lines.append("⚠  Income not provided — FOIR eligibility cannot be calculated.")
        lines.append("   Ask: 'What is the customer's net monthly income, and do they have any existing EMI obligations?'")
        lines.append("")
        missing.append("income")
    elif tenure_months:
        # Use the most conservative (highest) rate for eligibility — worst case for agent
        elig_rate  = use_rates[-1]
        emi_at_rate = compute_emi(principal, elig_rate, tenure_months)["emi"]
        foir_res   = compute_foir(income, emi_at_rate, existing_oblig)
        max_res    = max_eligible_loan(income, elig_rate, tenure_months,
                                       doc_foir_limit_pct, existing_oblig)

        lines.append(f"Net monthly income            = {fmt_inr(income)}")
        lines.append(f"Existing monthly obligations  = {fmt_inr(existing_oblig)}")
        lines.append(f"New EMI (at {elig_rate}%)            = {fmt_inr(emi_at_rate)}")
        lines.append(f"Total monthly obligations     = {fmt_inr(foir_res['total_monthly_emi'])}")
        lines.append(
            f"FOIR = {fmt_inr(foir_res['total_monthly_emi'])} ÷ {fmt_inr(income)} × 100"
            f" = {foir_res['foir_pct']}%"
        )
        lines.append(f"HDFC permissible FOIR limit   = {doc_foir_limit_pct}% (from documents)")
        lines.append("")

        if foir_res["foir_pct"] is not None and foir_res["foir_pct"] > doc_foir_limit_pct:
            lines.append("⚠  ELIGIBILITY VERDICT: LIKELY NOT ELIGIBLE at the requested amount.")
            lines.append(f"   Required EMI  {fmt_inr(emi_at_rate)} > Max permissible EMI {fmt_inr(max_res['max_emi'])}")
            lines.append(f"   Max eligible loan at {elig_rate}% for {tenure_months} months = {fmt_inr(max_res['max_loan'])}")
            lines.append(f"   (Customer may need a smaller loan or a longer tenure.)")
        else:
            lines.append(f"✓  ELIGIBILITY VERDICT: WITHIN FOIR LIMIT — customer qualifies.")
        lines.append("")

        results["foir"]             = foir_res
        results["max_loan"]         = max_res
        results["eligibility_rate"] = elig_rate

    # ── Foreclosure section ───────────────────────────────────────────────────
    if foreclosure_months is not None and tenure_months:
        lines.append("── FORECLOSURE IMPACT ───────────────────────────────────")
        fc_rate = use_rates[-1]   # conservative rate for outstanding computation

        if doc_foreclosure_charge_pct is not None:
            fc_res = compute_foreclosure(
                principal, fc_rate, tenure_months,
                foreclosure_months,
                doc_foreclosure_charge_pct,
                doc_gst_rate_pct,
            )
            lines.append(f"Scenario: customer closes after {foreclosure_months} months (computed at {fc_rate}% p.a.)")
            lines.append(f"  Outstanding principal at month {foreclosure_months}  = {fmt_inr(fc_res['outstanding'])}")
            lines.append(
                f"  Foreclosure charge ({fc_res['charge_rate_pct']}% of outstanding)  "
                f"= {fmt_inr(fc_res['foreclosure_charge'])}"
            )
            if fc_res["gst_on_charge"] is not None:
                lines.append(
                    f"  GST on charge ({fc_res['gst_rate_pct']}%)               "
                    f"= {fmt_inr(fc_res['gst_on_charge'])}"
                )
                lines.append(f"  ── Total foreclosure cost                 = {fmt_inr(fc_res['total_closure_cost'])}")
            else:
                lines.append(f"  GST on charge: {fc_res['gst_note']}")
                lines.append(f"  ── Minimum closure cost (before GST)      = {fmt_inr(fc_res['total_closure_cost'])}")
            results["foreclosure"] = fc_res
        else:
            outstanding = compute_outstanding(
                principal, fc_rate, tenure_months, foreclosure_months
            )
            lines.append(f"Scenario: customer closes after {foreclosure_months} months (computed at {fc_rate}% p.a.)")
            lines.append(f"  Outstanding principal at month {foreclosure_months}  = {fmt_inr(outstanding)}")
            lines.append("  Foreclosure charge rate: not found in loaded documents — confirm with product team.")
            results["outstanding_at_closure"] = outstanding
        lines.append("")

    if not lines:
        return {"calc_block": "", "results": results, "missing": missing}

    # ── Build the header — income gate comes first if income is missing ───────
    header_lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║  PRE-COMPUTED FINANCIAL RESULTS — Python-calculated          ║",
        "║  ⛔ DO NOT recalculate. DO NOT show formulas. Narrate only. ║",
        "╚══════════════════════════════════════════════════════════════╝",
    ]

    if "income" in missing:
        header_lines += [
            "",
            "┌─ MANDATORY INCOME GATE ────────────────────────────────────┐",
            "│ Income was NOT provided. FOIR cannot be calculated.        │",
            "│                                                             │",
            "│ YOUR FIRST SENTENCE MUST BE a question asking for income:  │",
            "│  'To check FOIR eligibility, I'll need the customer's      │",
            "│   net monthly income and any existing EMI obligations.     │",
            "│   What is their net monthly income?'                       │",
            "│                                                             │",
            "│ NEVER assume, estimate, or invent an income figure.        │",
            "│ NEVER compute FOIR without real income data.               │",
            "└────────────────────────────────────────────────────────────┘",
            "",
        ]

    block = "\n" + "\n".join(header_lines) + "\n\n" + "\n".join(lines) + "\n"
    return {"calc_block": block, "results": results, "missing": missing}


# ── Short-name public API (matches task spec) ──────────────────────────────────

def parse_income(text: str) -> Optional[float]:
    """Parse income/salary strings → float rupees (same parser as parse_amount)."""
    return parse_amount(text)


def inr(amount: float) -> str:
    """Format a float as Indian Rupee string (alias for fmt_inr)."""
    return fmt_inr(amount)


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    """Return just the monthly EMI amount as a float."""
    return float(compute_emi(principal, annual_rate_pct, months)["emi"])


def outstanding_balance(principal: float, rate_pct: float,
                        total_months: int, paid_months: int) -> float:
    """Outstanding principal after paid_months payments (alias for compute_outstanding)."""
    return compute_outstanding(principal, rate_pct, total_months, paid_months)


def foir(net_income: float, new_emi: float, existing_emi: float = 0.0) -> dict:
    """FOIR calculation dict (alias for compute_foir)."""
    return compute_foir(net_income, new_emi, existing_emi)


def foreclosure(outstanding: float) -> dict:
    """
    Foreclosure cost using CONFIG policy values.
    When CONFIG.foreclosure_charge_pct is None, returns a dict with a
    'confirm with product team' message instead of computed numbers.
    """
    if CONFIG.foreclosure_charge_pct is None:
        return {
            "outstanding": round(outstanding),
            "charge_rate_pct": None,
            "foreclosure_charge": None,
            "message": "Foreclosure charge rate not found in loaded documents — confirm with product team.",
        }
    charge = round(outstanding * CONFIG.foreclosure_charge_pct / 100)
    gst = round(charge * CONFIG.gst_pct / 100)
    return {
        "outstanding": round(outstanding),
        "charge_rate_pct": CONFIG.foreclosure_charge_pct,
        "foreclosure_charge": charge,
        "gst_on_charge": gst,
        "total_closure_cost": round(outstanding + charge + gst),
    }


def assert_emi_sane(emi_value: float, principal: float, months: int) -> None:
    """
    Raise ValueError if emi_value is impossibly low.
    The zero-interest floor (principal / months) is the absolute minimum —
    any EMI below that cannot amortise the principal regardless of rate.
    This catches the ₹1,112-on-₹20L bug class before numbers reach the user.
    """
    floor = principal / months
    if emi_value < floor:
        raise ValueError(
            f"EMI {fmt_inr(emi_value)} is impossible for "
            f"P={fmt_inr(principal)}, n={months} months "
            f"(zero-rate floor = {fmt_inr(floor)}). "
            "Check the rate/input values — this must not reach the user."
        )


def build_calc_block(entities: dict) -> str:
    """
    Convenience wrapper: run all financial calculations for `entities` using
    CONFIG policy values and return the formatted block string for LLM injection.

    Returns empty string when insufficient data (no loan_amount).
    For the engine path (where doc-extracted rates are available) use
    run_financial_analysis() directly so document-grounded values are used.
    """
    result = run_financial_analysis(
        entities=entities,
        doc_foreclosure_charge_pct=CONFIG.foreclosure_charge_pct,
        doc_gst_rate_pct=CONFIG.gst_pct if CONFIG.foreclosure_charge_pct is not None else None,
        doc_foir_limit_pct=CONFIG.foir_threshold_pct,
    )
    return result.get("calc_block", "")


def update_entities(entities: dict, field: str, raw_text: str) -> None:
    """
    Route raw_text to the correct key in entities.
    Prevents income values from landing in loan_amount and vice versa.
    Mutates the dict in-place.

    Supported fields: loan_amount, tenure, income, rate_pct,
                      foreclosure_period, existing_emi.
    """
    _INCOME_KEYS = {"income", "existing_emi"}
    if field == "loan_amount":
        entities["loan_amount"] = raw_text
        entities.pop("loan_amount_raw", None)   # precise named-unit retires raw capture
    elif field in _INCOME_KEYS:
        entities[field] = raw_text              # income can never contaminate loan_amount
    else:
        entities[field] = raw_text


# ── Self-test (run: python rag/calculator.py) ──────────────────────────────────

if __name__ == "__main__":
    import sys, io
    # Force UTF-8 output so INR symbols print correctly on Windows consoles.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    def _check(cond: bool, msg: str) -> None:
        if not cond:
            print(f"FAIL: {msg}", file=sys.stderr)
            sys.exit(1)

    # ── EMI sanity check ─────────────────────────────────────────────────────
    # ₹20L, 36 months, 12% → EMI ≈ ₹66,429
    e = emi(2_000_000, 12.0, 36)
    _check(66_000 < e < 67_000, f"12% EMI for 20L/36m should be ~₹66,429, got {e}")
    print(f"[OK] EMI 12%/36m = {fmt_inr(e)}")

    # ── assert_emi_sane catches the ₹1,112-on-₹20L bug ──────────────────────
    try:
        assert_emi_sane(1_112, 2_000_000, 36)
        _check(False, "assert_emi_sane should have raised on ₹1,112")
    except ValueError:
        print("[OK] assert_emi_sane correctly rejects impossible EMI ₹1,112")

    # ── build_calc_block: income gate present when income missing ────────────
    b_no_income = build_calc_block({"loan_amount": "20 lakh", "tenure": "36 months"})
    _check("MANDATORY INCOME GATE" in b_no_income, "income gate expected when income absent")
    print("[OK] income gate appears when income is missing")

    # ── build_calc_block: income gate gone once income is supplied ───────────
    b_with_income = build_calc_block({
        "loan_amount": "20 lakh",
        "tenure": "36 months",
        "income": "50000",
    })
    _check("MANDATORY INCOME GATE" not in b_with_income, "income gate must not appear when income is set")
    _check("66,429" in b_with_income, f"12% EMI row missing from calc_block")
    _check("FOIR" in b_with_income, "FOIR section missing from calc_block")
    print("[OK] income gate absent + 12% EMI ₹66,429 + FOIR verdict present")

    # ── foreclosure: confirm message when charge rate is unset ───────────────
    fc = foreclosure(1_500_000)
    _check("confirm with product team" in fc.get("message", "").lower(),
           "foreclosure should say 'confirm with product team' when charge % is None")
    print("[OK] foreclosure returns 'confirm with product team' (CONFIG.foreclosure_charge_pct=None)")

    # ── build_calc_block: foreclosure section says 'confirm with product team'
    b_fc = build_calc_block({
        "loan_amount": "20 lakh",
        "tenure": "36 months",
        "income": "50000",
        "foreclosure_period": "12 months",
    })
    _check("confirm with product team" in b_fc,
           "calc_block should include 'confirm with product team' for foreclosure when charge % unset")
    print("[OK] calc_block foreclosure section: 'confirm with product team'")

    # ── update_entities: income never writes to loan_amount ──────────────────
    ents: dict = {}
    update_entities(ents, "income", "50,000 Rs")
    update_entities(ents, "loan_amount", "20 lakh")
    _check("income" in ents and "loan_amount" in ents, "both keys must be present")
    _check(ents["income"] == "50,000 Rs" and ents["loan_amount"] == "20 lakh",
           "income and loan_amount must stay in separate keys")
    print("[OK] update_entities keeps income and loan_amount separate")

    print("\nAll self-tests passed.")
