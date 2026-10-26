"""
RAG Pipeline Inspector — transparent end-to-end trace (v2 hybrid).

Given a user query, run every stage of the RAG pipeline and return
a structured dict showing exactly what happened:

  1. Intent detection             → which classifier label was picked
  2. Query expansion              → which keywords got appended (and why)
  3. Speech analysis              → language / tone / entities (voice-style)
  4. Query embedding              → 384-dim vector
  5a. Dense retrieval             → top-N chunks by cosine
  5b. BM25 retrieval              → top-N chunks by sparse keyword score
  6. RRF fusion                   → both lists merged via reciprocal rank fusion
  7. Cross-encoder reranking      → final precision pass (optional)
  8. Context blocks               → chunks grouped by file, ready for LLM
  9. Prompt assembly              → the full prompt string sent to Ollama
  10. LLM call                    → if include_llm=true, actually run and
                                     return the raw + post-processed answer

`/api/rag/inspect` wraps this. The frontend renders each stage as a card.
"""

import time
from typing import Any, Dict, List, Optional

from rag.analysis import analyze_speech
from rag.intent import detect_intent, expand_query, expansion_phrases_used
from rag.post_process import post_process_chat, user_asked_for_list
from prompts.chat import build_chat_prompt


def _ms_since(start: float) -> int:
    return int((time.time() - start) * 1000)


def _chunk_preview(text: str, max_chars: int = 350) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= max_chars else text[:max_chars] + "…"


def _serialize_ranking(rag, ranking) -> List[Dict[str, Any]]:
    """Turn a [(global_id, score), ...] list into UI-friendly rows."""
    out = []
    for rank, (gid, score) in enumerate(ranking, start=1):
        rec = rag.chunk_store.get(gid)
        out.append({
            "rank": rank,
            "global_id": gid,
            "score": round(float(score), 4),
            "file": rec.filename,
            "chunk_id": rec.chunk_id,
            "total_chunks_in_file": rec.total_chunks,
            "char_count": len(rec.content),
            "preview": _chunk_preview(rec.content),
        })
    return out


def _serialize_fused(rag, fused) -> List[Dict[str, Any]]:
    """Fused entries carry per-retriever ranks — surface them for debugging."""
    out = []
    for rank, (gid, rrf_score, debug) in enumerate(fused, start=1):
        rec = rag.chunk_store.get(gid)
        out.append({
            "rank": rank,
            "global_id": gid,
            "rrf_score": round(float(rrf_score), 4),
            "dense_rank": debug.get("dense"),
            "bm25_rank": debug.get("bm25"),
            "file": rec.filename,
            "chunk_id": rec.chunk_id,
            "total_chunks_in_file": rec.total_chunks,
            "char_count": len(rec.content),
            "preview": _chunk_preview(rec.content),
        })
    return out


def inspect_pipeline(
    rag,
    query: str,
    *,
    top_k: int = 6,
    candidates: int = 20,
    include_llm: bool = False,
    use_reranker: Optional[bool] = None,
    max_files: int = 3,
    max_chunks_per_file: int = 2,
) -> Dict[str, Any]:
    """Run the full v2 RAG pipeline and return a transparent trace."""
    t0 = time.time()
    trace: Dict[str, Any] = {
        "query": query,
        "config": {
            "top_k": top_k,
            "candidates": candidates,
            "max_files": max_files,
            "max_chunks_per_file": max_chunks_per_file,
            "use_reranker": bool(use_reranker if use_reranker is not None else rag.use_reranker),
            "include_llm": include_llm,
            "files_indexed": rag.chunk_store.n_files,
            "chunks_indexed": len(rag.chunk_store),
            "documents_loaded": len(rag.documents),
            "structured_loaded": len(rag.structured_data),
        },
        "stages": {},
        "timings_ms": {},
    }

    # ── 1. Intent ───────────────────────────────────────────────────────────
    t = time.time()
    intent = detect_intent(query, rag.conversation_history)
    trace["stages"]["1_intent"] = {"intent": intent, "classifier": "rag.intent.detect_intent"}
    trace["timings_ms"]["intent_ms"] = _ms_since(t)

    # ── 2. Query expansion ─────────────────────────────────────────────────
    t = time.time()
    expanded = expand_query(query)
    expansions = expansion_phrases_used(query)
    trace["stages"]["2_query_expansion"] = {
        "original": query,
        "expanded": expanded,
        "expansions_added": expansions,
        "expansion_count": len(expansions),
    }
    trace["timings_ms"]["expansion_ms"] = _ms_since(t)

    # ── 3. Speech analysis ─────────────────────────────────────────────────
    t = time.time()
    analysis = analyze_speech(query, rag.conversation_history)
    trace["stages"]["3_speech_analysis"] = analysis
    trace["timings_ms"]["analysis_ms"] = _ms_since(t)

    # ── 4. Embed query ─────────────────────────────────────────────────────
    if rag.vector_matcher is None or len(rag.chunk_store) == 0:
        trace["stages"]["4_query_embedding"] = {"status": "skipped"}
        trace["stages"]["5a_dense"] = {"status": "skipped"}
        trace["stages"]["5b_bm25"] = {"status": "skipped"}
        trace["stages"]["6_fused"] = {"status": "skipped"}
        trace["stages"]["8_context"] = {"blocks": []}
        trace["timings_ms"]["total_ms"] = _ms_since(t0)
        return trace

    t = time.time()
    q_emb = rag.vector_matcher.generate_embedding(expanded)
    trace["stages"]["4_query_embedding"] = {
        "embedding_dim": int(q_emb.shape[0]),
        "embedding_norm": round(float((q_emb ** 2).sum() ** 0.5), 4),
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "device": "cpu",
    }
    trace["timings_ms"]["embedding_ms"] = _ms_since(t)

    # ── 5a. Dense retrieval ─────────────────────────────────────────────────
    t = time.time()
    dense = rag.chunk_store.search_dense(q_emb, k=candidates)
    trace["stages"]["5a_dense"] = {
        "method": "cosine over chunk embeddings",
        "candidates_requested": candidates,
        "candidates_returned": len(dense),
        "ranking": _serialize_ranking(rag, dense),
    }
    trace["timings_ms"]["dense_ms"] = _ms_since(t)

    # ── 5b. BM25 retrieval ──────────────────────────────────────────────────
    t = time.time()
    bm25 = rag.chunk_store.search_bm25(query, k=candidates)
    trace["stages"]["5b_bm25"] = {
        "method": "BM25Okapi over tokenized chunks",
        "candidates_requested": candidates,
        "candidates_returned": len(bm25),
        "ranking": _serialize_ranking(rag, bm25),
    }
    trace["timings_ms"]["bm25_ms"] = _ms_since(t)

    # ── 6 + 7. Fusion + (optional) rerank — done by retrieve_with_trace ────
    t = time.time()
    rt = rag.retrieve_with_trace(
        query,
        top_k=top_k,
        candidates=candidates,
        max_files=max_files,
        max_chunks_per_file=max_chunks_per_file,
        use_reranker=use_reranker,
    )
    trace["timings_ms"]["fusion_plus_rerank_ms"] = _ms_since(t)

    trace["stages"]["6_fused"] = {
        "method": "Reciprocal Rank Fusion (RRF, k=60)",
        "k": 60,
        "top_k": top_k,
        "ranking": _serialize_fused(rag, rt.fused),
    }

    if rt.reranked is not None:
        trace["stages"]["7_reranked"] = {
            "method": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "ranking": _serialize_fused(rag, rt.reranked),
        }
    else:
        trace["stages"]["7_reranked"] = {
            "status": "skipped",
            "reason": "use_reranker=false or reranker unavailable",
        }

    # ── 8. Context blocks (what actually goes to the LLM) ──────────────────
    trace["stages"]["8_context"] = {
        "block_count": len(rt.blocks),
        "total_context_chars": sum(b.char_count for b in rt.blocks),
        "blocks": [
            {
                "rank": idx + 1,
                "filename": b.filename,
                "best_rank_in_fusion": b.best_rank,
                "rrf_score": round(b.rrf_score, 4),
                "char_count": b.char_count,
                "chunks": [
                    {
                        "chunk_id": c.chunk_id,
                        "total_in_file": c.total_chunks,
                        "char_count": len(c.content),
                        "preview": _chunk_preview(c.content),
                        "content": c.content,
                    }
                    for c in b.chunks
                ],
            }
            for idx, b in enumerate(rt.blocks)
        ],
    }

    # ── 9. Build the chat prompt ───────────────────────────────────────────
    t = time.time()
    context_blob = "\n\n---\n\n".join(b.render() for b in rt.blocks)
    history_lines = ""
    if rag.conversation_history:
        for ex in rag.conversation_history[-10:]:
            history_lines += f"User: {ex['query']}\nArjun: {ex['answer']}\n\n"
    else:
        history_lines = "(no prior turns — this is the first message)\n"
    prompt = build_chat_prompt(
        question=query,
        intent=intent,
        history_lines=history_lines,
        context=context_blob,
    )
    trace["stages"]["9_prompt"] = {
        "model_target": rag.CHAT_MODEL,
        "prompt_chars": len(prompt),
        "context_chars": len(context_blob),
        "chunks_included": sum(len(b.chunks) for b in rt.blocks),
        "files_included": len(rt.blocks),
        "prompt_preview": prompt[:600] + ("…" if len(prompt) > 600 else ""),
        "prompt_full": prompt,
    }
    trace["timings_ms"]["prompt_build_ms"] = _ms_since(t)

    # ── 10. Optional LLM ───────────────────────────────────────────────────
    if include_llm:
        t = time.time()
        try:
            raw_answer = rag._call_ollama_chat(prompt)
            llm_ms = _ms_since(t)
            clean = post_process_chat(raw_answer, user_asked_for_list(query))
            trace["stages"]["10_llm"] = {
                "model": rag.CHAT_MODEL,
                "raw_answer": raw_answer,
                "raw_answer_chars": len(raw_answer),
                "post_processed_answer": clean,
                "post_processed_chars": len(clean),
                "post_processing_applied": raw_answer != clean,
            }
            trace["timings_ms"]["llm_ms"] = llm_ms
        except Exception as e:
            trace["stages"]["10_llm"] = {"status": "error", "error": str(e)}
            trace["timings_ms"]["llm_ms"] = _ms_since(t)

    trace["timings_ms"]["total_ms"] = _ms_since(t0)
    return trace
