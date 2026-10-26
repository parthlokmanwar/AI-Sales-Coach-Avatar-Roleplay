"""
Hybrid retrieval — dense + BM25 fused via Reciprocal Rank Fusion (RRF).

Why hybrid:
  Dense (sentence-transformers) is great at semantic similarity but blind
  to specific lexical matches (e.g. "CIBIL 700" exact number).
  BM25 is great at exact lexical matches but blind to paraphrases
  (e.g. "credit score" ≠ "CIBIL bureau rating").
  Together via RRF they cover each other's blind spots.

  RRF formula:  score(d) = Σ  1 / (k + rank_i(d))     where k=60 (standard)
                          i

  Each retriever votes for its top-K candidates by RANK (not raw score),
  so we don't need to normalise scales — dense cosine in [-1,1] and BM25
  unbounded play nicely together.

Pipeline stages we expose to the inspector:
  1. retrieve_dense   — top-N candidates by cosine on chunk embeddings
  2. retrieve_bm25    — top-N candidates by BM25 on chunk tokens
  3. fuse_rrf         — combine both via RRF → top-K
  4. group_by_file    — collapse multiple chunks from same file into
                        one ContextBlock for the LLM
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from rag.store import ChunkRecord, ChunkStore


# ── Reciprocal Rank Fusion ───────────────────────────────────────────────────

RRF_K = 60  # Standard RRF constant from Cormack et al. 2009


def reciprocal_rank_fusion(
    *rankings: List[Tuple[int, float]],
    k: int = RRF_K,
    top_k: int = 10,
) -> List[Tuple[int, float, Dict[str, int]]]:
    """
    Each input is a ranked list [(global_id, score), ...].
    Returns merged ranking [(global_id, rrf_score, debug_ranks), ...].
    debug_ranks records the per-retriever rank of each chunk so the inspector
    can show which retriever surfaced what.
    """
    rrf: Dict[int, float] = {}
    debug: Dict[int, Dict[str, int]] = {}
    names = ["dense", "bm25", "extra"]
    for i, ranking in enumerate(rankings):
        name = names[i] if i < len(names) else f"r{i}"
        for rank, (gid, _) in enumerate(ranking):
            rrf[gid] = rrf.get(gid, 0.0) + 1.0 / (k + rank + 1)
            debug.setdefault(gid, {})[name] = rank + 1  # 1-based
    merged = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [(gid, score, debug.get(gid, {})) for gid, score in merged]


# ── ContextBlock — grouped chunks for the LLM ────────────────────────────────

@dataclass
class ContextBlock:
    filename: str
    chunks: List[ChunkRecord]
    rrf_score: float
    best_rank: int

    @property
    def char_count(self) -> int:
        return sum(len(c.content) for c in self.chunks)

    def render(self) -> str:
        """Format this file's chunks as a single block for the LLM context."""
        chunk_count = self.chunks[0].total_chunks if self.chunks else 1
        parts = [f"[Source: {self.filename} · rrf_rank #{self.best_rank}]"]
        for c in self.chunks:
            parts.append(f"(chunk {c.chunk_id + 1}/{chunk_count})\n{c.content}")
        return "\n\n".join(parts)


def group_by_file(
    store: ChunkStore,
    fused: List[Tuple[int, float, Dict[str, int]]],
    *,
    max_files: int = 4,
    max_chunks_per_file: int = 2,
) -> List[ContextBlock]:
    """Walk the fused ranking, group consecutive same-file chunks, cap output.

    The result is what actually goes into the LLM prompt — at most `max_files`
    distinct source files, each contributing at most `max_chunks_per_file`
    chunks. Chunks within a file are sorted by their natural chunk_id so the
    LLM sees them in document order.
    """
    by_file: Dict[str, List[Tuple[ChunkRecord, float, int]]] = {}
    seen_files_order: List[str] = []
    for rank_pos, (gid, rrf_score, _debug) in enumerate(fused):
        rec = store.get(gid)
        if rec.filename not in by_file:
            if len(by_file) >= max_files:
                continue
            seen_files_order.append(rec.filename)
        by_file.setdefault(rec.filename, []).append((rec, rrf_score, rank_pos + 1))

    blocks: List[ContextBlock] = []
    for filename in seen_files_order:
        entries = by_file[filename][:max_chunks_per_file]
        chunks = sorted([e[0] for e in entries], key=lambda c: c.chunk_id)
        best_rrf = max(e[1] for e in entries)
        best_rank = min(e[2] for e in entries)
        blocks.append(ContextBlock(
            filename=filename,
            chunks=chunks,
            rrf_score=best_rrf,
            best_rank=best_rank,
        ))
    return blocks


# ── Top-level retrieve() — one call does it all ──────────────────────────────

@dataclass
class RetrievalTrace:
    """All intermediate ranks — for the inspector + eval harness."""
    dense: List[Tuple[int, float]]
    bm25: List[Tuple[int, float]]
    fused: List[Tuple[int, float, Dict[str, int]]]
    reranked: Optional[List[Tuple[int, float, Dict[str, int]]]]
    blocks: List[ContextBlock]


def retrieve(
    store: ChunkStore,
    query_text: str,
    query_embedding: np.ndarray,
    *,
    candidates: int = 20,
    top_k: int = 6,
    max_files: int = 4,
    max_chunks_per_file: int = 2,
    reranker=None,
) -> RetrievalTrace:
    """
    Run the full hybrid retrieval pipeline and return every intermediate
    list so the inspector can visualise.

    Args:
        store           — populated ChunkStore
        query_text      — original user question (for BM25 + rerank)
        query_embedding — 384-dim vector (for dense)
        candidates      — how many candidates each retriever produces
        top_k           — how many chunks survive RRF + rerank
        max_files       — cap of distinct files in the final context
        max_chunks_per_file — cap of chunks pulled from each file
        reranker        — optional CrossEncoderReranker instance (None = skip)
    """
    dense = store.search_dense(query_embedding, k=candidates)
    bm25 = store.search_bm25(query_text, k=candidates)
    fused = reciprocal_rank_fusion(dense, bm25, top_k=top_k * 3)

    reranked = None
    final = fused
    if reranker is not None and fused:
        pairs = [(query_text, store.get(gid).content) for gid, _, _ in fused]
        scores = reranker.score(pairs)
        merged = list(zip(fused, scores))
        merged.sort(key=lambda x: x[1], reverse=True)
        reranked = [(gid, float(s), dbg) for (gid, _, dbg), s in merged[:top_k]]
        final = reranked
    else:
        final = fused[:top_k]

    blocks = group_by_file(
        store, final,
        max_files=max_files,
        max_chunks_per_file=max_chunks_per_file,
    )

    return RetrievalTrace(
        dense=dense,
        bm25=bm25,
        fused=fused[:top_k],
        reranked=reranked,
        blocks=blocks,
    )
