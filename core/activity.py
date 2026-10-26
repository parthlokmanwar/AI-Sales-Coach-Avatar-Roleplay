"""
core/activity.py
────────────────
Tenant-scoped persistence for agent activity: Pitch conversations and Chat
sessions. Replaces the global pitch_history.json / chat_history.json stores.

Each record keeps its original rich dict shape in a JSON `data` column (so the
frontend is untouched) plus scope columns. Reads are filtered through the
caller's TenantScope:
  * sales agent  → only rows where user_id == them (within their company)
  * company admin→ all rows in their company
  * super admin  → unrestricted

Creates stamp company_id = scope.company_id and user_id = scope.actor_id, so an
admin's own activity is owned by them while still visible company-wide.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select

from core.db import session_scope
from core.models import ChatSession, PitchConversation, RoleplaySession

log = logging.getLogger("hdfc")


def _parse_dt(s) -> datetime:
    if not s:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _apply_scope(stmt, model, scope):
    """Add tenant filters. None company_id (super admin) = no restriction."""
    if scope.company_id is not None:
        stmt = stmt.where(model.company_id == scope.company_id)
    if scope.user_id is not None:
        stmt = stmt.where(model.user_id == scope.user_id)
    return stmt


# ── Generic helpers (shared by pitch + chat) ─────────────────────────────────
def _list(model, scope, limit: int = 200) -> list[dict]:
    with session_scope() as db:
        stmt = _apply_scope(select(model), model, scope).order_by(model.updated_at.desc()).limit(limit)
        return [row.data for row in db.scalars(stmt).all()]


def _get(model, scope, rec_id: str) -> Optional[dict]:
    with session_scope() as db:
        stmt = _apply_scope(select(model).where(model.id == rec_id), model, scope)
        row = db.scalar(stmt)
        return row.data if row else None


def _save(model, scope, record: dict) -> dict:
    rec_id = record.get("id")
    if not rec_id:
        raise ValueError("record must have an 'id'")
    now = datetime.utcnow()
    with session_scope() as db:
        row = db.get(model, rec_id)
        if row is not None:
            # Guard: never let one tenant overwrite another's record.
            if scope.company_id is not None and row.company_id != scope.company_id:
                raise PermissionError("cross-tenant write blocked")
            row.name = record.get("name", row.name)
            row.data = record
            row.updated_at = now
        else:
            db.add(model(
                id=rec_id,
                company_id=scope.company_id,
                user_id=scope.actor_id,
                name=record.get("name", ""),
                data=record,
                created_at=_parse_dt(record.get("created_at")),
                updated_at=now,
            ))
    return record


def _delete(model, scope, rec_id: str) -> bool:
    with session_scope() as db:
        row = db.scalar(_apply_scope(select(model).where(model.id == rec_id), model, scope))
        if not row:
            return False
        db.delete(row)
        return True


def _clear(model, scope) -> int:
    with session_scope() as db:
        ids = db.scalars(_apply_scope(select(model.id), model, scope)).all()
        if ids:
            db.execute(delete(model).where(model.id.in_(ids)))
        return len(ids)


# ── Pitch conversations ───────────────────────────────────────────────────────
def pitch_list(scope) -> list[dict]:
    return _list(PitchConversation, scope)


def pitch_get(scope, conv_id: str) -> Optional[dict]:
    return _get(PitchConversation, scope, conv_id)


def pitch_save(scope, conv: dict) -> dict:
    return _save(PitchConversation, scope, conv)


def pitch_delete(scope, conv_id: str) -> bool:
    return _delete(PitchConversation, scope, conv_id)


def pitch_clear(scope) -> int:
    return _clear(PitchConversation, scope)


# ── Chat sessions ─────────────────────────────────────────────────────────────
def chat_list(scope) -> list[dict]:
    return _list(ChatSession, scope)


def chat_get(scope, sid: str) -> Optional[dict]:
    return _get(ChatSession, scope, sid)


def chat_save(scope, session: dict) -> dict:
    return _save(ChatSession, scope, session)


def chat_delete(scope, sid: str) -> bool:
    return _delete(ChatSession, scope, sid)


def chat_clear(scope) -> int:
    return _clear(ChatSession, scope)


# ── RolePlay sessions (scored simulations) ────────────────────────────────────
import uuid as _uuid


def roleplay_record(scope, prospect_name="", profession="", loan_type="",
                    score=None, grade=None, report=None,
                    slot_id=None, booking_id=None) -> str:
    """Persist one completed, scored roleplay session owned by the agent."""
    rid = _uuid.uuid4().hex
    with session_scope() as db:
        db.add(RoleplaySession(
            id=rid, company_id=scope.company_id, user_id=scope.actor_id,
            prospect_name=prospect_name or "", profession=profession or "",
            loan_type=loan_type or "", score=score, grade=grade, report=report or {},
            slot_id=slot_id, booking_id=booking_id,
        ))
    return rid


def roleplay_list(scope, limit: int = 200) -> list[dict]:
    with session_scope() as db:
        stmt = (_apply_scope(select(RoleplaySession), RoleplaySession, scope)
                .order_by(RoleplaySession.created_at.desc()).limit(limit))
        return [{
            "id": r.id, "prospect_name": r.prospect_name, "profession": r.profession,
            "loan_type": r.loan_type, "score": r.score, "grade": r.grade, "user_id": r.user_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in db.scalars(stmt).all()]


def my_counts(scope) -> dict:
    """Quick self-service totals for the agent home dashboard."""
    with session_scope() as db:
        def cnt(model):
            return db.scalar(_apply_scope(select(func.count()).select_from(model), model, scope)) or 0
        pitches = cnt(PitchConversation)
        chats = cnt(ChatSession)
        roleplays = cnt(RoleplaySession)
        avg = db.scalar(
            _apply_scope(select(func.avg(RoleplaySession.score)), RoleplaySession, scope)
            .where(RoleplaySession.score.isnot(None))
        )
    return {
        "pitches": pitches, "chats": chats, "roleplays": roleplays,
        "avg_score": round(avg) if avg is not None else None,
    }


# ── One-time migration of the legacy global JSON stores ──────────────────────
def migrate_json_history() -> None:
    """Import pitch_history.json + chat_history.json into the DB under the demo
    company (company-wide, owner unset). Idempotent: runs only while the target
    table is empty, then renames the source file to *.migrated.
    """
    from pathlib import Path

    import core.deps as deps
    from core.models import Company

    with session_scope() as db:
        demo = db.scalar(select(Company).where(Company.slug == "posiview-demo"))
        demo_id = demo.id if demo else None

    def _migrate(model, loader, source: Path, label: str):
        with session_scope() as db:
            if db.scalar(select(func.count()).select_from(model)) > 0:
                return  # already has data — never re-import
        if not source.exists():
            return
        try:
            records = loader()
        except Exception as e:
            log.warning(f"[MIGRATE] could not read {label}: {e}")
            return
        if not records:
            return
        with session_scope() as db:
            for rec in records:
                rec_id = rec.get("id")
                if not rec_id:
                    continue
                db.add(model(
                    id=rec_id, company_id=demo_id, user_id=None,
                    name=rec.get("name", ""), data=rec,
                    created_at=_parse_dt(rec.get("created_at")),
                    updated_at=_parse_dt(rec.get("updated_at") or rec.get("created_at")),
                ))
        try:
            source.rename(source.with_suffix(source.suffix + ".migrated"))
        except OSError:
            pass
        log.info(f"[MIGRATE] imported {len(records)} {label} records into the DB (company={demo_id}).")

    _migrate(PitchConversation, deps.load_conversations, deps.HISTORY_FILE, "pitch")
    _migrate(ChatSession, deps.load_chat_sessions, deps.CHAT_HISTORY_FILE, "chat")


def migrate_company_uploads() -> None:
    """Copy the existing global uploads/* documents into the demo company's
    folder (uploads/<demo_id>/) so it owns the current corpus. One-time:
    skips if the destination folder already exists. The originals stay in
    uploads/ root for the legacy/global instance (live-call + super admin).
    """
    import shutil

    import core.deps as deps
    from core.models import Company

    with session_scope() as db:
        demo = db.scalar(select(Company).where(Company.slug == "posiview-demo"))
        if not demo:
            return
        demo_id = demo.id

    dest = deps.company_uploads_dir(demo_id)
    if dest.exists() or not deps.UPLOADS_FOLDER.exists():
        return  # already migrated, or nothing to copy

    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for fp in deps.UPLOADS_FOLDER.iterdir():
        if fp.is_file() and fp.suffix.lower() in deps.SUPPORTED_DOC_EXTS:
            try:
                shutil.copy2(fp, dest / fp.name)
                copied += 1
            except OSError as e:
                log.warning(f"[MIGRATE] could not copy {fp.name}: {e}")
    log.info(f"[MIGRATE] seeded demo company corpus with {copied} documents.")
