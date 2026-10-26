"""
Pre-loaded demo answer cache for the Live Voice Assistant.

Loads `core/demo_cache.json` at import time and exposes a single fuzzy lookup:
`lookup(question)` → CacheEntry | None.

Why this exists:
- Real customer voice questions repeat constantly during sales calls
  (EMI, processing fee, documents, eligibility, interest rate).
- Hitting RAG + LLM for these wastes 2-4 seconds each time.
- A static, vetted-correct answer is also safer for an HDFC demo than
  whatever the LLM chooses to compose from chunks on a given run.

Matching strategy:
1. Normalize: lowercase, strip punctuation, collapse whitespace.
2. Exact normalized hit → return immediately.
3. Otherwise score each entry by Dice coefficient against the entry's
   keyword set. Hit if score >= MATCH_THRESHOLD AND overlap >= MIN_OVERLAP.
   Dice (2·|A∩B|/(|A|+|B|)) handles short Hinglish questions like
   "EMI kitni hogi" better than Jaccard, while still requiring enough
   shared signal to avoid false positives.

Editing the cache:
- Add/remove entries in core/demo_cache.json (no code change needed).
- The "keywords" field is what's matched on — keep them lowercase and
  exclude filler words ("what", "is", "the", "a", "for").
- A cache miss is fine: the normal RAG path runs as before.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, List, Dict, TypedDict

# Dice-coefficient threshold for fuzzy hits (0..1). 0.50 catches paraphrases
# like "EMI kitni hogi" → "How is EMI calculated" while still rejecting
# unrelated questions. Dice = 2·|A∩B| / (|A|+|B|).
MATCH_THRESHOLD = 0.50

# Minimum absolute keyword overlap — guards against 1-word questions like
# "EMI" falsely matching every entry that mentions it.
MIN_OVERLAP = 2

_CACHE_PATH = Path(__file__).resolve().parent / "demo_cache.json"


class CacheEntry(TypedDict):
    answer: str
    sources: List[str]
    matched_keywords: List[str]
    similarity: float


class _Entry(TypedDict):
    question: str
    keywords: List[str]
    answer: str
    sources: List[str]


_NORM_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")

# Filler / stop words excluded from keyword matching. Keep this short —
# over-aggressive filtering breaks short legitimate questions.
_STOP = {
    # English articles + pronouns + auxiliaries
    "what", "is", "the", "a", "an", "of", "to", "for", "in", "on",
    "and", "or", "do", "does", "did", "i", "you", "we", "me", "my", "your",
    "how", "can", "should", "would", "could", "will", "be", "are", "was",
    "this", "that", "it", "if", "with", "by", "have", "has", "had",
    "so", "too", "very", "really", "just", "any", "some",
    # Hindi filler particles
    "kya", "hai", "ka", "ki", "ke", "kaise", "kitna", "kitne",
    "mein", "se", "ko", "bhi", "bhai", "wo", "woh", "toh", "to",
    "matlab", "na", "abhi",
}


def _normalize(text: str) -> str:
    if not text:
        return ""
    s = text.lower().strip()
    s = _NORM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _tokenize(text: str) -> set:
    return {t for t in _normalize(text).split() if t and t not in _STOP}


def _load_entries() -> List[_Entry]:
    if not _CACHE_PATH.exists():
        return []
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data["entries"]
    except Exception:
        pass
    return []


# Loaded once at import. Each entry pre-normalized for fast lookup.
_ENTRIES: List[Dict] = []
_EXACT_INDEX: Dict[str, int] = {}


def _build_index():
    global _ENTRIES, _EXACT_INDEX
    _ENTRIES = []
    _EXACT_INDEX = {}
    for raw in _load_entries():
        q = (raw.get("question") or "").strip()
        if not q or not raw.get("answer"):
            continue
        keywords = raw.get("keywords") or []
        # If keywords missing, derive from question tokens.
        kw_set = set(k.lower().strip() for k in keywords if k.strip())
        if not kw_set:
            kw_set = _tokenize(q)
        entry = {
            "question": q,
            "answer": raw["answer"].strip(),
            "sources": raw.get("sources") or [],
            "_norm": _normalize(q),
            "_kw": kw_set,
        }
        _ENTRIES.append(entry)
        _EXACT_INDEX[entry["_norm"]] = len(_ENTRIES) - 1


_build_index()


def reload() -> int:
    """Reload from disk. Useful when editing demo_cache.json while the server runs."""
    _build_index()
    return len(_ENTRIES)


def size() -> int:
    return len(_ENTRIES)


def all_questions() -> List[str]:
    """Return the list of canonical questions — for /api/voice/demo-cache UI listing."""
    return [e["question"] for e in _ENTRIES]


def lookup(question: str) -> Optional[CacheEntry]:
    """
    Return the best matching cache entry, or None if no match crosses the threshold.

    Match order:
      1. Exact normalized match → similarity = 1.0
      2. Best Jaccard overlap >= MATCH_THRESHOLD with >= MIN_OVERLAP keywords
    """
    if not _ENTRIES or not question:
        return None

    norm = _normalize(question)
    if not norm:
        return None

    # 1. Exact match
    idx = _EXACT_INDEX.get(norm)
    if idx is not None:
        e = _ENTRIES[idx]
        return CacheEntry(
            answer=e["answer"],
            sources=list(e["sources"]),
            matched_keywords=sorted(e["_kw"]),
            similarity=1.0,
        )

    # 2. Hybrid fuzzy match — Dice for longer questions, containment for
    #    short ones (≤3 meaningful tokens). Short questions can't generate
    #    a high Dice score against keyword sets of 5-7 items even when every
    #    word they used is in those keywords, so we fall back to containment
    #    (overlap / |q_tokens|) which is fairer. MIN_OVERLAP still applies
    #    so 1-word questions like "loan" can never trigger a match.
    q_tokens = _tokenize(question)
    if not q_tokens:
        return None

    short_question = len(q_tokens) <= 3

    best: Optional[Dict] = None
    best_score = 0.0
    for e in _ENTRIES:
        kw = e["_kw"]
        if not kw:
            continue
        overlap = q_tokens & kw
        if len(overlap) < MIN_OVERLAP:
            continue
        dice = (2.0 * len(overlap)) / (len(q_tokens) + len(kw))
        if short_question:
            containment = len(overlap) / len(q_tokens)
            score = max(dice, containment)
        else:
            score = dice
        if score > best_score:
            best_score = score
            best = e

    if best is None or best_score < MATCH_THRESHOLD:
        return None

    return CacheEntry(
        answer=best["answer"],
        sources=list(best["sources"]),
        matched_keywords=sorted((q_tokens & best["_kw"])),
        similarity=round(best_score, 3),
    )
