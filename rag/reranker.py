"""
Cross-encoder reranker — the precision lever.

After hybrid retrieval surfaces ~20 candidates, this re-scores each
(query, chunk) pair with a small cross-encoder. Cross-encoders see
both the query and the candidate together, so they catch nuance that
bi-encoders (which embed query and chunks separately) miss.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2  (~80MB, CPU-friendly)
Latency: ~5–15ms per pair × 20 candidates ≈ 100–300ms total
Downloads on first use — happens once per machine.

The reranker is optional. Toggle via the inspector UI / engine config.
When disabled, retrieval still uses dense + BM25 + RRF (good baseline).
"""

from typing import List, Tuple

try:
    from sentence_transformers import CrossEncoder
    HAS_CROSS_ENCODER = True
except Exception:
    HAS_CROSS_ENCODER = False


DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Wraps a sentence-transformers CrossEncoder. Singleton-friendly."""

    def __init__(self, model_name: str = DEFAULT_RERANKER):
        if not HAS_CROSS_ENCODER:
            raise ImportError(
                "sentence-transformers not installed — cannot use cross-encoder reranker."
            )
        self.model_name = model_name
        self.model = CrossEncoder(model_name, device="cpu")
        print(f"[OK] Cross-encoder reranker loaded: {model_name}")

    def score(self, pairs: List[Tuple[str, str]]) -> List[float]:
        """Score a list of (query, candidate_text) pairs. Higher = more relevant."""
        if not pairs:
            return []
        scores = self.model.predict(pairs, show_progress_bar=False)
        return [float(s) for s in scores]


_singleton: CrossEncoderReranker | None = None


def get_reranker() -> CrossEncoderReranker | None:
    """Lazy singleton — first call downloads the model (~80MB).
    Returns None if sentence-transformers is unavailable."""
    global _singleton
    if _singleton is None and HAS_CROSS_ENCODER:
        try:
            _singleton = CrossEncoderReranker()
        except Exception as e:
            print(f"[WARN] Could not load cross-encoder reranker: {e}")
            return None
    return _singleton
