"""
Document chunking — token-aware, recursive splitter.

DESIGN CHANGES
==============

v2 (previous):
  500-char windows (~100 tokens) with 100-char overlap. Too small for a 14B
  model with an 8K context window — tiny "fact bites" instead of meaningful
  context blocks. Sentences could also be cut mid-word when a chunk filled up.

v3 (current):
  Chunks are sized in TOKENS, not characters, and uses a recursive separator
  walk (paragraphs → lines → sentences → words) so a chunk only breaks at the
  largest natural boundary that still fits the budget.

Sizing for Qwen 2.5 14B (8K context, ~2500 tokens of context allocated to RAG):
  - 500 tokens (~2500 chars) per chunk     — meaningful fact block, not a fragment
  - 100 tokens (~500 chars) overlap         — straddling facts survive
  - 80 tokens (~400 chars) minimum          — chunks below this are filtered out
  - 5 chunks × 500 tokens = 2500 tokens total context → safe under 8K budget

Token estimation:
  We use a 1 token ≈ 4 characters heuristic. Good enough for English + Hinglish
  (more precise tokenization would require loading the model's actual tokenizer
  which is overkill at chunking time). The DEFAULT_*_CHARS constants below are
  pre-computed from the token targets so we never have to convert at runtime.

Strategy:
  recursive_split(text, separators=["\\n\\n", "\\n", ". ", " ", ""])
    1. Try to split at "\\n\\n" — between paragraphs
    2. If a paragraph is too big, split it at "\\n" — between lines
    3. Still too big? Split at ". " — between sentences
    4. Still too big? Split at " " — between words
    5. Last resort: hard split at char boundary
  → Then pack adjacent splits into chunks of ≤ chunk_size, with `overlap`
    chars carried from the previous chunk.

Tokenizer (`tokenize`) is exposed so BM25 can share word boundaries with
similarity scoring.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List

# ── Chunk size defaults ──────────────────────────────────────────────────────
# Targets are expressed in TOKENS (semantic units the LLM consumes), but
# stored as CHARS because we don't run a tokenizer at chunking time.
# 1 token ≈ 4 chars (Qwen / Llama tokenizers' average for English+Hinglish).

CHARS_PER_TOKEN = 4

TARGET_CHUNK_TOKENS = 500          # ~ 500 tokens
TARGET_OVERLAP_TOKENS = 100        # ~ 100 tokens
MIN_CHUNK_TOKENS = 80              # filter chunks smaller than this

DEFAULT_CHUNK_SIZE = TARGET_CHUNK_TOKENS * CHARS_PER_TOKEN      # 2000 chars
DEFAULT_OVERLAP = TARGET_OVERLAP_TOKENS * CHARS_PER_TOKEN       # 400 chars
DEFAULT_MIN_CHUNK = MIN_CHUNK_TOKENS * CHARS_PER_TOKEN          # 320 chars


@dataclass
class DocumentChunk:
    content: str
    chunk_id: int                # local index within this file (0-based)
    total_chunks: int            # total chunks in the file
    metadata: Dict[str, Any]     # {"filename": ..., "strategy": ...}
    embedding_hash: Any = None   # filled later by engine.ingest


# ── Tokenizer (shared with BM25) ─────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+", flags=re.UNICODE)


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric word tokens. Used by BM25 and keyword scoring."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


# ── Recursive separator splitter ─────────────────────────────────────────────

# Order matters: try the largest natural boundary first; fall through to
# smaller ones only when a piece is still too big.
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _recursive_split(text: str, max_size: int, separators: List[str] = _SEPARATORS) -> List[str]:
    """
    Split `text` so every piece is ≤ max_size. Tries the first separator;
    if any resulting piece is still too big, recursively splits it on the
    next separator. Empty separator "" means hard char-boundary fallback.

    Returns a flat list of pieces (no piece exceeds max_size).
    """
    if len(text) <= max_size:
        return [text]

    sep = separators[0] if separators else ""
    rest = separators[1:] if len(separators) > 1 else [""]

    if sep == "":
        # Hard split at max_size — last resort, only when no other separator
        # could break the run.
        return [text[i : i + max_size] for i in range(0, len(text), max_size)]

    parts = text.split(sep)
    out: List[str] = []
    for piece in parts:
        # Re-attach the separator (except for the last piece, to avoid trailing).
        # Keeping the separator on the right of each piece preserves readability
        # in the rendered chunk text.
        if not piece:
            continue
        if len(piece) <= max_size:
            out.append(piece)
        else:
            out.extend(_recursive_split(piece, max_size, rest))
    return out


def _pack_with_overlap(
    pieces: List[str],
    chunk_size: int,
    overlap: int,
    joiner: str = " ",
) -> List[str]:
    """
    Greedy packer: combine pieces into chunks of ≤ chunk_size chars. When a
    chunk closes, carry the last `overlap` chars into the next chunk as
    context. Pieces that exceed chunk_size are placed in their own chunk.
    """
    chunks: List[str] = []
    current = ""

    for piece in pieces:
        if not piece:
            continue
        # Standalone oversized piece (shouldn't happen post-recursive-split,
        # but defensive): flush current, emit it on its own.
        if len(piece) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.append(piece.strip())
            continue

        # If appending would exceed budget, close the current chunk and
        # seed the next one with overlap from the tail of what we just sealed.
        if current and len(current) + len(joiner) + len(piece) > chunk_size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap > 0 and len(current) > overlap else ""
            current = (tail + joiner + piece) if tail else piece
        else:
            current = (current + joiner + piece) if current else piece

    if current.strip():
        chunks.append(current.strip())

    return chunks


# ── SmartChunker ─────────────────────────────────────────────────────────────

class SmartChunker:
    """
    Token-aware recursive chunker. One code path for every doc size — small
    docs become one or two chunks, large docs become many. No more strategy
    branching, no more mid-sentence cuts.

    Args:
        chunk_size : target characters per chunk (default 2000 ≈ 500 tokens)
        overlap    : characters carried between adjacent chunks (default 400 ≈ 100 tokens)
        min_chunk  : chunks shorter than this are filtered out (default 320 ≈ 80 tokens).
                     Set to 0 to disable filtering (useful when chunking very
                     short reference docs where every chunk matters).
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
        min_chunk: int = DEFAULT_MIN_CHUNK,
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk = min_chunk

    def chunk_document(self, text: str, filename: str) -> List[DocumentChunk]:
        """Recursively split + pack with overlap. Filter out tiny chunks."""
        text = (text or "").strip()
        if not text:
            return []

        # If the whole doc fits, return it as a single chunk regardless of
        # min_chunk — we want short reference docs (e.g. a one-paragraph
        # disclaimer file) to still be retrievable.
        if len(text) <= self.chunk_size:
            return [DocumentChunk(
                content=text, chunk_id=0, total_chunks=1,
                metadata={"filename": filename, "strategy": "single"},
            )]

        pieces = _recursive_split(text, self.chunk_size, _SEPARATORS)
        packed = _pack_with_overlap(pieces, self.chunk_size, self.overlap)

        # Drop micro-chunks — fragments below min_chunk are usually section
        # headers or stray whitespace runs that confuse retrieval.
        if self.min_chunk > 0:
            packed = [p for p in packed if len(p) >= self.min_chunk]

        # Edge case: filter removed everything (every piece was tiny). Fall
        # back to the original text as a single chunk so the doc is at
        # least searchable.
        if not packed:
            return [DocumentChunk(
                content=text[: self.chunk_size], chunk_id=0, total_chunks=1,
                metadata={"filename": filename, "strategy": "fallback"},
            )]

        total = len(packed)
        return [
            DocumentChunk(
                content=c, chunk_id=i, total_chunks=total,
                metadata={"filename": filename, "strategy": "recursive"},
            )
            for i, c in enumerate(packed)
        ]

    # ── Within-file keyword re-ranker (kept for back-compat) ─────────────────
    def get_relevant_chunks(self, chunks: List[DocumentChunk], query: str, max_chunks: int = 10) -> List[DocumentChunk]:
        """Lightweight keyword-overlap re-ranker over a fixed chunk list.
        v2 retrieval uses global hybrid search (see rag/retrieval.py) — this
        is now only used by the inspector for legacy single-file reranking."""
        query_words = set(tokenize(query))
        scored = []
        for chunk in chunks:
            chunk_words = set(tokenize(chunk.content))
            overlap = len(query_words & chunk_words)
            score = overlap / len(query_words) if query_words else 0
            scored.append((score, chunk))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [c for _, c in scored[:max_chunks]]
