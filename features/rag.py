"""
FEATURE: RAG Inspector — transparent pipeline trace endpoint.

Endpoints:
  POST /api/rag/inspect      — walk every stage of the RAG pipeline for a
                                query and return everything: intent, expansion,
                                similarity scores, retrieved chunks, prompt,
                                and (optionally) the LLM answer.

This is the verification tool: use it to confirm that
  1. Retrieval returns the chunks you'd expect
  2. The top-K chunks actually make it into the prompt
  3. The LLM's answer is grounded in those chunks

Implementation lives in rag.inspector.inspect_pipeline().

The frontend "RAG Inspector" page calls this endpoint and renders each
stage as its own card.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import core.deps as deps
from rag.inspector import inspect_pipeline

router = APIRouter()


class InspectRequest(BaseModel):
    query: str
    top_k: int = 6                  # final chunks surfaced from RRF / rerank
    candidates: int = 20            # candidates per retriever before fusion
    max_files: int = 3              # at most N distinct files in final context
    max_chunks_per_file: int = 2    # at most M chunks from any one file
    use_reranker: Optional[bool] = None  # None = follow engine default
    include_llm: bool = False


@router.post("/api/rag/inspect")
async def inspect(request: InspectRequest):
    """
    Run the full v2 hybrid RAG pipeline transparently and return every
    intermediate result (dense, BM25, fused, reranked, blocks, prompt, answer).

    Body:
      query              — the user's question
      top_k              — final chunks after RRF / rerank (default 6)
      candidates         — candidates per retriever before fusion (default 20)
      max_files          — distinct source files in final context (default 3)
      max_chunks_per_file — chunks pulled from any one file (default 2)
      use_reranker       — cross-encoder rerank pass (default: engine default)
      include_llm        — if true, actually call the chat LLM (default false)
    """
    try:
        q = (request.query or "").strip()
        if not q:
            raise HTTPException(status_code=400, detail="query is required")

        if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
            raise HTTPException(
                status_code=400,
                detail="No documents loaded. Drop files in uploads/ and restart.",
            )

        trace = inspect_pipeline(
            deps.rag_instance,
            q,
            top_k=max(1, min(request.top_k, 20)),
            candidates=max(5, min(request.candidates, 50)),
            max_files=max(1, min(request.max_files, 8)),
            max_chunks_per_file=max(1, min(request.max_chunks_per_file, 5)),
            use_reranker=request.use_reranker,
            include_llm=bool(request.include_llm),
        )
        return JSONResponse(content={"success": True, "trace": trace})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
