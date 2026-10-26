"""
features/documents.py
─────────────────────
Per-company document library management (Company Admin console).

Each company's documents live under uploads/<company_id>/ and feed ONLY that
company's RAG engine (see core/deps.get_rag), so pitch/chat answers are grounded
strictly in the company's own corpus. Uploading or deleting reloads the
company's engine from disk.

Endpoints (require company_admin; super admin must pass ?company_id=):
  GET    /api/admin/documents          — list the company's documents
  POST   /api/admin/documents          — upload a document (multipart)
  DELETE /api/admin/documents/{name}   — remove a document
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

import core.deps as deps
from core.deps import SUPPORTED_DOC_EXTS, company_uploads_dir, reload_company_rag, sanitize_meta
from core.management import ManagementError
from core.models import ROLE_SUPER_ADMIN
from core.rbac import require_company_admin

log = logging.getLogger("hdfc")
router = APIRouter(prefix="/api/admin/documents", dependencies=[Depends(require_company_admin)])


def _company_id(user: dict, explicit: Optional[str]) -> str:
    if user.get("role") == ROLE_SUPER_ADMIN:
        if not explicit:
            raise ManagementError("Super admin must supply ?company_id=.", 400)
        return explicit
    cid = user.get("company_id")
    if not cid:
        raise ManagementError("No company associated with this account.", 400)
    return cid


def _list_docs(cid: str) -> list:
    rag = deps.get_rag(cid)
    files = []
    for name, meta in rag.metadata.items():
        files.append({
            "name": name,
            "type": meta.get("type"),
            "format": meta.get("format"),
            "chunks": meta.get("chunks", 0),
            "rows": meta.get("rows"),
            "metadata": sanitize_meta(meta),
        })
    return files


@router.get("")
async def list_documents(company_id: Optional[str] = None, user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    files = _list_docs(cid)
    return JSONResponse(content={"success": True, "files": files, "total": len(files)})


@router.post("")
async def upload_document(file: UploadFile = File(...), company_id: Optional[str] = None,
                         user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    safe_name = Path(file.filename or "").name  # strip any path components
    ext = ("." + safe_name.rsplit(".", 1)[-1].lower()) if "." in safe_name else ""
    if ext not in SUPPORTED_DOC_EXTS:
        raise ManagementError(
            f"Unsupported file type '{ext or '?'}'. Allowed: {', '.join(sorted(SUPPORTED_DOC_EXTS))}.", 415)

    dest_dir = company_uploads_dir(cid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name

    contents = await file.read()
    if not contents:
        raise ManagementError("Uploaded file is empty.", 400)
    dest.write_bytes(contents)

    # Rebuild the company's corpus from disk so the new doc is searchable.
    reload_company_rag(cid)
    log.info(f"[DOCS] {cid}: uploaded {safe_name} ({len(contents)} bytes)")
    return JSONResponse(content={"success": True, "name": safe_name, "files": _list_docs(cid)})


@router.delete("/{name}")
async def delete_document(name: str, company_id: Optional[str] = None,
                         user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    safe_name = Path(name).name
    target = company_uploads_dir(cid) / safe_name
    if not target.exists():
        raise ManagementError("Document not found.", 404)
    try:
        target.unlink()
    except OSError as e:
        raise ManagementError(f"Could not delete document: {e}", 500)
    reload_company_rag(cid)
    log.info(f"[DOCS] {cid}: deleted {safe_name}")
    return JSONResponse(content={"success": True, "files": _list_docs(cid)})
