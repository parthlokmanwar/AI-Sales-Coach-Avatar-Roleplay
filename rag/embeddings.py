"""
Vector embeddings + cosine similarity.

Wraps sentence-transformers' all-MiniLM-L6-v2 (384-dim) and exposes:
  - generate_embedding(text)       → np.ndarray
  - calculate_similarity(e1, e2)   → float in [-1, 1]
  - find_most_similar(query, store, top_k, threshold)
      → ranked list of (filename, similarity_score)

CPU only — PyTorch CUDA wheels don't exist for Python 3.14 yet.
Ollama handles the actual GPU acceleration for the LLM calls.
"""

from typing import Dict, List, Tuple

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_VECTORS = True
except Exception:
    HAS_VECTORS = False
    print("⚠️  sentence-transformers not available. Install: pip install sentence-transformers scikit-learn")


# Process-wide shared matcher (see VectorMatcher.shared()).
_SHARED_MATCHER = None


class VectorMatcher:
    """Fast document matching using vector embeddings and cosine similarity."""

    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        if not HAS_VECTORS:
            raise ImportError("sentence-transformers required.")
        self.model = SentenceTransformer(model_name, device="cpu")
        print(f"[OK] Vector matcher initialized: {model_name} on CPU")

    def generate_embedding(self, text: str) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(384)
        return self.model.encode(text, convert_to_numpy=True)

    def calculate_similarity(self, e1: np.ndarray, e2: np.ndarray) -> float:
        return float(cosine_similarity(e1.reshape(1, -1), e2.reshape(1, -1))[0][0])

    @staticmethod
    def shared() -> "VectorMatcher | None":
        """Return a process-wide singleton matcher.

        The embedding model is stateless (text → vector), so every RAGEngine —
        including the per-company instances in the multi-tenant registry — can
        safely share ONE loaded SentenceTransformer instead of each paying the
        ~90 MB / load cost. Returns None when sentence-transformers is absent.
        """
        global _SHARED_MATCHER
        if not HAS_VECTORS:
            return None
        if _SHARED_MATCHER is None:
            _SHARED_MATCHER = VectorMatcher()
        return _SHARED_MATCHER

    def find_most_similar(
        self,
        query_embedding: np.ndarray,
        file_embeddings: Dict[str, np.ndarray],
        top_k: int = 3,
        threshold: float = 0.3,
    ) -> List[Tuple[str, float]]:
        if not file_embeddings:
            return []
        sims = [
            (fname, self.calculate_similarity(query_embedding, emb))
            for fname, emb in file_embeddings.items()
        ]
        sims = [(f, s) for f, s in sims if s >= threshold]
        sims.sort(key=lambda x: x[1], reverse=True)
        return sims[:top_k]
