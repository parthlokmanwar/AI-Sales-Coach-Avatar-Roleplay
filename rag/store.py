"""
ChunkStore — flat global store of every chunk + its embedding + BM25 corpus.

This replaces the v1 file-level approach (one embedding per file). With
chunk-level embeddings, retrieval is precise: each chunk is a focused
fact block and we find the BEST chunk across ALL files, not just the
best chunk inside a file we already short-listed.

Stored data:
  records:  List[ChunkRecord]   — flat, in insertion order
  matrix:   np.ndarray (N, 384) — stacked embeddings for fast cosine
  bm25:     BM25Okapi           — sparse keyword index over tokenized chunks
  by_file:  Dict[str, List[int]] — global ids per filename (for grouping)

Use `.add_file(filename, chunks, embeddings)` during ingestion.
Use `.search_dense(query_emb, k)` and `.search_bm25(query, k)` for retrieval.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from rag.chunker import DocumentChunk, tokenize

try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except Exception:
    HAS_BM25 = False


@dataclass
class ChunkRecord:
    global_id: int            # position in store.records
    filename: str             # source file
    chunk_id: int             # local within file
    total_chunks: int         # total in that file
    strategy: str             # "single" | "paragraph" | "semantic"
    content: str              # the actual text passed to LLM
    embedding: np.ndarray     # 384-dim vector


class ChunkStore:
    def __init__(self):
        self.records: List[ChunkRecord] = []
        self.by_file: Dict[str, List[int]] = {}
        self._matrix: Optional[np.ndarray] = None
        self._bm25: Optional["BM25Okapi"] = None
        self._tokens_corpus: List[List[str]] = []
        self._dirty = True  # set when records change so we rebuild indices

    # ── Public API ───────────────────────────────────────────────────────────

    def add_file(
        self,
        filename: str,
        chunks: List[DocumentChunk],
        embeddings: List[np.ndarray],
    ) -> None:
        """Add every chunk from one file. embeddings must align with chunks."""
        assert len(chunks) == len(embeddings), "chunks/embeddings length mismatch"
        global_ids: List[int] = []
        for chunk, emb in zip(chunks, embeddings):
            gid = len(self.records)
            rec = ChunkRecord(
                global_id=gid,
                filename=filename,
                chunk_id=chunk.chunk_id,
                total_chunks=chunk.total_chunks,
                strategy=chunk.metadata.get("strategy", "single"),
                content=chunk.content,
                embedding=emb,
            )
            self.records.append(rec)
            self._tokens_corpus.append(tokenize(chunk.content))
            global_ids.append(gid)
        self.by_file.setdefault(filename, []).extend(global_ids)
        self._dirty = True

    def remove_file(self, filename: str) -> None:
        """Drop every chunk belonging to one file (used by /api/files/clear)."""
        ids = set(self.by_file.pop(filename, []))
        if not ids:
            return
        new_records: List[ChunkRecord] = []
        new_corpus: List[List[str]] = []
        new_by_file: Dict[str, List[int]] = {}
        for rec in self.records:
            if rec.global_id in ids:
                continue
            new_gid = len(new_records)
            new_record = ChunkRecord(
                global_id=new_gid,
                filename=rec.filename,
                chunk_id=rec.chunk_id,
                total_chunks=rec.total_chunks,
                strategy=rec.strategy,
                content=rec.content,
                embedding=rec.embedding,
            )
            new_records.append(new_record)
            new_corpus.append(self._tokens_corpus[rec.global_id])
            new_by_file.setdefault(new_record.filename, []).append(new_gid)
        self.records = new_records
        self._tokens_corpus = new_corpus
        self.by_file = new_by_file
        self._dirty = True

    def clear(self) -> None:
        self.records = []
        self.by_file = {}
        self._tokens_corpus = []
        self._matrix = None
        self._bm25 = None
        self._dirty = True

    def __len__(self) -> int:
        return len(self.records)

    @property
    def n_files(self) -> int:
        return len(self.by_file)

    # ── Index build (lazy) ───────────────────────────────────────────────────

    def _ensure_indices(self) -> None:
        if not self._dirty:
            return
        if self.records:
            self._matrix = np.vstack([r.embedding for r in self.records])
            if HAS_BM25:
                self._bm25 = BM25Okapi(self._tokens_corpus)
            else:
                self._bm25 = None
        else:
            self._matrix = None
            self._bm25 = None
        self._dirty = False

    # ── Retrieval ────────────────────────────────────────────────────────────

    def search_dense(self, query_emb: np.ndarray, k: int = 20) -> List[Tuple[int, float]]:
        """Top-k by cosine similarity. Returns [(global_id, score), ...]."""
        self._ensure_indices()
        if self._matrix is None or len(self.records) == 0:
            return []
        q = query_emb / (np.linalg.norm(query_emb) + 1e-12)
        norms = np.linalg.norm(self._matrix, axis=1) + 1e-12
        sims = (self._matrix @ q) / norms
        top_idx = np.argsort(sims)[::-1][:k]
        return [(int(i), float(sims[i])) for i in top_idx]

    def search_bm25(self, query_text: str, k: int = 20) -> List[Tuple[int, float]]:
        """Top-k by BM25 score. Returns [(global_id, score), ...]."""
        self._ensure_indices()
        if self._bm25 is None or len(self.records) == 0:
            return []
        scores = self._bm25.get_scores(tokenize(query_text))
        if len(scores) == 0:
            return []
        top_idx = np.argsort(scores)[::-1][:k]
        return [(int(i), float(scores[i])) for i in top_idx if scores[i] > 0]

    def get(self, global_id: int) -> ChunkRecord:
        return self.records[global_id]
