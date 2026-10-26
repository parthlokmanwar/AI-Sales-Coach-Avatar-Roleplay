"""
FEATURE: File ingestion + health endpoints.

Endpoints:
  GET    /api/files               — list loaded docs (structured + documents)
  DELETE /api/files/clear         — reset RAG instance, reload from uploads/
  DELETE /api/context/clear       — clear in-memory conversation context
  GET    /                        — service ping
  GET    /health                  — health + load stats

Helpers:
  auto_load_files_from_folder()   — called from main.py startup
                                    to ingest every supported file under uploads/

AI logic: none — pure file management.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import core.deps as deps
from core.deps import (
    log,
    rag_instance,
    UPLOADS_FOLDER,
    HISTORY_FILE,
    sanitize_meta,
    load_history,
)
from core.rbac import tenant_scope

router = APIRouter()


# ── Startup helper ───────────────────────────────────────────────────────────

def auto_load_files_from_folder():
    """Walk uploads/ and ingest every supported file into the RAG store."""
    if not UPLOADS_FOLDER.exists():
        log.warning(f"[FILES] Uploads folder not found: {UPLOADS_FOLDER}")
        return

    log.info(f"[FILES] Loading from {UPLOADS_FOLDER}")
    supported = {'.csv', '.xlsx', '.xls', '.pdf', '.docx', '.pptx', '.txt'}
    loaded, failed = 0, 0
    for file_path in UPLOADS_FOLDER.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in supported:
            try:
                result = deps.rag_instance.add_file(str(file_path), file_path.name)
                if result.get("success"):
                    log.info(f"[FILES] OK   {file_path.name}")
                    loaded += 1
                else:
                    log.warning(f"[FILES] FAIL {file_path.name}  {result.get('error')}")
                    failed += 1
            except Exception as e:
                log.error(f"[FILES] ERROR {file_path.name}  {e}")
                failed += 1
    log.info(f"[FILES] Auto-load complete: {loaded} loaded, {failed} failed")


# ── /api/files ───────────────────────────────────────────────────────────────

@router.get("/api/files")
async def list_files(scope=Depends(tenant_scope)):
    """List the caller's organization's loaded documents with metadata."""
    try:
        rag = deps.get_rag(scope.company_id)
        files = []
        for name, meta in rag.metadata.items():
            if meta.get("type") == "structured":
                files.append({
                    "name": name,
                    "type": "structured",
                    "format": meta.get("format"),
                    "rows": meta.get("rows"),
                    "columns": meta.get("columns"),
                    "metadata": sanitize_meta(meta),
                })
        for name, meta in rag.metadata.items():
            if meta.get("type") == "document":
                files.append({
                    "name": name,
                    "type": "document",
                    "format": meta.get("format"),
                    "units": meta.get("units"),
                    "chunking_strategy": meta.get("chunking_strategy"),
                    "chunks": meta.get("chunks", 0),
                    "metadata": sanitize_meta(meta),
                })
        return JSONResponse(content={"success": True, "files": files, "total": len(files)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/files/clear")
async def clear_all_files(scope=Depends(tenant_scope)):
    """Rebuild the caller's organization's RAG corpus from its document folder."""
    try:
        deps.reload_company_rag(scope.company_id)
        return JSONResponse(content={"success": True, "message": "Documents reloaded"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/context/clear")
async def clear_context(scope=Depends(tenant_scope)):
    """Clear in-memory conversation context (keep files loaded)."""
    try:
        deps.get_rag(scope.company_id).clear_context()
        return JSONResponse(content={"success": True, "message": "Context cleared"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Health ───────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "HDFC Sales Pitch Assistant API",
        "version": "2.0.0",
    }


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "files_loaded": {
            "structured": len(deps.rag_instance.structured_data),
            "documents": len(deps.rag_instance.documents),
        },
        "history_entries": len(load_history()),
    }
