"""
Shared grounding rule — imported by every prompt builder that answers questions.
Define ONCE here; import GROUNDING_RULE so wording is identical across features.
"""

GROUNDING_RULE = """GROUNDING — TWO TIERS:

TIER 1 — HDFC-SPECIFIC FACTS (rates, processing/foreclosure fees and charges,
eligibility thresholds: min income / max FOIR / min CIBIL, exact HDFC document
lists, product policies, approved scripts, disbursal timelines, offers):
→ MUST come from the documents. If absent, say it is not in the documents and to
  confirm with the product team / branch. Never quote a specific HDFC number that
  is not present in the retrieved context.
→ NEVER state a specific HDFC figure or policy as a guess, even with a qualifier
  ("approximately", "typically", "usually"). Model training data is NOT a source
  of current HDFC pricing or policy — a confident stale number is worse than none.

TIER 2 — GENERAL, NON-PROPRIETARY KNOWLEDGE (standard Indian KYC documents:
PAN, Aadhaar, address proof, property papers, ITR, GST returns; term definitions;
general loan process steps):
→ If absent from the documents, MAY give standard general guidance, clearly marked:
  "This isn't from the HDFC documents — as a general rule…"  or
  "(standard — confirm the exact HDFC list with the branch)".
→ NEVER attach an HDFC-specific number to a Tier-2 answer."""
