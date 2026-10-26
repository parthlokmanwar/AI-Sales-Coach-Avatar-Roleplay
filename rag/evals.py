"""
RAG Eval Harness — golden queries with expected facts.

Goal: quantify retrieval quality with a fixed test set. Run after any
change to chunker / store / retrieval / rerankerand see whether the
metrics moved up or down.

Each `GoldenQuery` declares:
  q             — the user question
  expected      — a list of fact phrases that the answer MUST contain
                  (or that at least ONE retrieved chunk MUST contain)
  forbidden     — phrases that must NOT appear (false-positive guards)
  notes         — what the query is testing

Metrics we report:
  recall@k          — for each query: did any retrieved chunk contain
                      at least one expected fact phrase? (binary)
  precision_fact    — across all queries: % of retrieved chunks that
                      contained at least one expected fact phrase
  mrr_first_hit     — mean reciprocal rank of the first chunk that
                      contained an expected fact phrase

Usage from the repo root:
  python -m rag.evals
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class GoldenQuery:
    q: str
    expected: List[str]
    forbidden: List[str] = field(default_factory=list)
    notes: str = ""


# ── Golden set (HDFC sales corpus) ───────────────────────────────────────────
# Phrases are matched case-insensitively as substrings.

GOLDEN: List[GoldenQuery] = [
    GoldenQuery(
        q="What is the minimum CIBIL score for HDFC personal loan?",
        expected=["700", "cibil", "score"],
        notes="Direct factual lookup — small number embedded in policy doc.",
    ),
    GoldenQuery(
        q="What is the maximum personal loan amount?",
        expected=["40", "lakh"],
        notes="Numeric upper bound. Tests dense recall on numbers.",
    ),
    GoldenQuery(
        q="What is the maximum tenure for a personal loan?",
        expected=["60", "month"],
        notes="Numeric range — tenure spans both 'months' and '5 years' forms.",
    ),
    GoldenQuery(
        q="Foreclosure charges for personal loan",
        expected=["2%", "foreclosure"],
        notes="Specific fee + keyword. Good for BM25 contribution.",
    ),
    GoldenQuery(
        q="What documents do I need for a salaried personal loan?",
        expected=["pan", "aadhaar", "salary slip"],
        notes="Multi-fact: should retrieve a chunk that lists docs together.",
    ),
    GoldenQuery(
        q="What is the minimum monthly income for a salaried customer?",
        expected=["25,000", "30,000", "metro"],
        notes="Either 25k or 30k (metro) is acceptable; tests numeric variants.",
    ),
    GoldenQuery(
        q="EMI calculation for 5 lakh over 3 years",
        expected=["emi", "interest"],
        forbidden=["50 lakh"],
        notes="Should NOT confuse with maximum 50 lakh fact.",
    ),
    GoldenQuery(
        q="Processing fee for personal loan",
        expected=["processing fee", "%"],
        notes="Banking term + percentage. Tests dense+BM25 together.",
    ),
    GoldenQuery(
        q="Self-employed income requirement",
        expected=["self-employed", "itr"],
        notes="Distinct segment — must NOT default to salaried doc.",
    ),
    GoldenQuery(
        q="What is FOIR and how does it affect eligibility?",
        expected=["foir", "obligation"],
        notes="Acronym — tests query expansion adding 'Fixed Obligation Income Ratio'.",
    ),
    GoldenQuery(
        q="Prepayment lock-in period",
        expected=["12 emi", "prepayment"],
        notes="Should retrieve the foreclosure section that mentions 12 EMIs.",
    ),
    GoldenQuery(
        q="What customer segments does HDFC have?",
        expected=["segment"],
        notes="Tests profiling guide retrieval.",
    ),
]


# ── Metrics ──────────────────────────────────────────────────────────────────

def _chunk_contains_any(content: str, phrases: List[str]) -> bool:
    lower = content.lower()
    return any(p.lower() in lower for p in phrases if p)


def evaluate(rag, *, top_k: int = 6, use_reranker: bool | None = None) -> dict:
    """Run the golden set against the live engine and return per-query +
    aggregate metrics. Pure (no mutation, no LLM call)."""
    results = []
    total_chunks_seen = 0
    chunks_with_fact = 0
    first_hit_ranks: List[int | None] = []
    hits = 0
    forbidden_hits = 0

    started = time.time()
    for gq in GOLDEN:
        trace = rag.retrieve_with_trace(
            gq.q, top_k=top_k, use_reranker=use_reranker,
        )
        # Iterate chunks in fused order so MRR is meaningful
        ranking = trace.reranked if trace.reranked else trace.fused
        first_hit = None
        any_hit = False
        any_forbidden = False
        chunks_per_query = 0
        for rank, (gid, _score, _dbg) in enumerate(ranking, start=1):
            rec = rag.chunk_store.get(gid)
            chunks_per_query += 1
            if _chunk_contains_any(rec.content, gq.expected):
                chunks_with_fact += 1
                if first_hit is None:
                    first_hit = rank
                any_hit = True
            if gq.forbidden and _chunk_contains_any(rec.content, gq.forbidden):
                any_forbidden = True

        total_chunks_seen += chunks_per_query
        if any_hit:
            hits += 1
        if any_forbidden:
            forbidden_hits += 1
        first_hit_ranks.append(first_hit)

        results.append({
            "q": gq.q,
            "expected": gq.expected,
            "forbidden": gq.forbidden,
            "hit": any_hit,
            "first_hit_rank": first_hit,
            "forbidden_seen": any_forbidden,
            "files": [rag.chunk_store.get(gid).filename for gid, _, _ in ranking[:3]],
        })

    elapsed = time.time() - started
    n = len(GOLDEN)

    def _mrr() -> float:
        rrs = [(1.0 / r) for r in first_hit_ranks if r is not None]
        return sum(rrs) / n if n else 0.0

    return {
        "summary": {
            "queries": n,
            "recall_at_k": round(hits / n, 4) if n else 0.0,
            "precision_fact": round(chunks_with_fact / total_chunks_seen, 4) if total_chunks_seen else 0.0,
            "mrr_first_hit": round(_mrr(), 4),
            "forbidden_leak_rate": round(forbidden_hits / n, 4) if n else 0.0,
            "top_k": top_k,
            "use_reranker": bool(use_reranker if use_reranker is not None else rag.use_reranker),
            "elapsed_s": round(elapsed, 2),
        },
        "per_query": results,
    }


# ── CLI entry — `python -m rag.evals` ────────────────────────────────────────

def _cli():
    """Bootstrap a RAGEngine from uploads/ and run the eval set twice:
    once with the reranker on, once off."""
    print("[EVAL] Bootstrapping engine…")
    from pathlib import Path
    from rag.engine import RAGEngine

    rag = RAGEngine()
    uploads = Path(__file__).resolve().parent.parent / "uploads"
    if uploads.exists():
        supported = {".csv", ".xlsx", ".xls", ".pdf", ".docx", ".pptx", ".txt"}
        for fp in uploads.iterdir():
            if fp.is_file() and fp.suffix.lower() in supported:
                rag.add_file(str(fp), fp.name)
    if len(rag.chunk_store) == 0:
        print("[EVAL] No chunks indexed — aborting.")
        sys.exit(1)
    print(f"[EVAL] Indexed {len(rag.chunk_store)} chunks across {rag.chunk_store.n_files} files.\n")

    for label, flag in (("hybrid (no rerank)", False), ("hybrid + cross-encoder rerank", True)):
        print("=" * 70)
        print(f"  {label}")
        print("=" * 70)
        report = evaluate(rag, top_k=6, use_reranker=flag)
        s = report["summary"]
        print(f"  recall@k     : {s['recall_at_k']:.2%}    (>= 1 expected fact in top-{s['top_k']})")
        print(f"  precision    : {s['precision_fact']:.2%}    (chunks with an expected fact)")
        print(f"  MRR(first hit): {s['mrr_first_hit']:.4f}    (mean reciprocal rank)")
        print(f"  forbidden    : {s['forbidden_leak_rate']:.2%}    (false-positive bound)")
        print(f"  elapsed      : {s['elapsed_s']}s")
        print()
        for r in report["per_query"]:
            mark = "OK " if r["hit"] else "MISS"
            rank = f"r{r['first_hit_rank']}" if r["first_hit_rank"] else "  --"
            print(f"  [{mark}] {rank:>4}  {r['q']}")
        print()


if __name__ == "__main__":
    _cli()
