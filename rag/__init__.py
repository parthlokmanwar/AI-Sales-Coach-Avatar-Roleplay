"""
RAG package — production layout for the retrieval-augmented generation pipeline.

Files:
  chunker.py       — SmartChunker + DocumentChunk dataclass
  embeddings.py    — VectorMatcher (sentence-transformers + cosine sim)
  intent.py        — expand_query() + detect_intent() (pure helpers)
  analysis.py      — analyze_speech() (pure structured speech analysis)
  post_process.py  — post_process_chat() (strips robotic openers, fixes bullets)
  engine.py        — RAGEngine — main class composing everything
  inspector.py     — inspect_pipeline() — transparent end-to-end trace

The class formerly known as `EnhancedRAGv4` is now `RAGEngine` —
exported here so code that imports `from rag import RAGEngine` works.
"""

from .engine import RAGEngine

# Back-compat alias
EnhancedRAGv4 = RAGEngine

__all__ = ["RAGEngine", "EnhancedRAGv4"]
