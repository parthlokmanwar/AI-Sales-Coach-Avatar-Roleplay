"""
core/management.py
──────────────────
Service layer for the Super Admin + Company Admin consoles (Phase 1a):
company lifecycle, user (admin/agent) lifecycle, seat enforcement, credential
generation, audit logging, and list queries with search / filter / pagination.

Routers (features/platform.py, features/admin.py) stay thin — they handle HTTP
concerns and delegate every state change here, so tenant rules and audit writes
live in exactly one place. All functions return plain dicts (computed inside the
session) so nothing leaks a detached ORM instance.
"""

import logging
import re
import secrets
from typing import Optional

from sqlalchemy import func, or_, select

from core.db import session_scope
from core.models import (
    COMPANY_ACTIVE,
    COMPANY_DISABLED,
    ROLE_COMPANY_ADMIN,
    ROLE_SALES_AGENT,
    USER_ACTIVE,
    USER_DISABLED,
    AuditLog,
    Company,
    LoginHistory,
    User,
)
from core.security import hash_password

log = logging.getLogger("hdfc")


# ── Errors ────────────────────────────────────────────────────────────────────
class ManagementError(Exception):
    """Service-level error mapped to an HTTP status by the routers."""

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# ── Helpers ───────────────────────────────────────────────────────────────────
def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return base or "company"


def _unique_slug(db, name: str) -> str:
    base = _slugify(name)
    slug = base
    i = 2
    while db.scalar(select(Company.id).where(Company.slug == slug)):
        slug = f"{base}-{i}"
        i += 1
    return slug


def generate_password(length: int = 10) -> str:
    """Readable strong password: guarantees upper+lower+digit, no ambiguous chars."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isupper() for c in pw) and any(c.islower() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def _send_credentials_email(email: str, name: str, temp_password: str, role: str) -> None:
    """Best-effort welcome/credentials email — never blocks account creation."""
    try:
        from core import email as email_svc
        email_svc.send_credentials(email, name, temp_password, role)
    except Exception as e:  # pragma: no cover - email is non-critical
        log.warning(f"[EMAIL] credentials email skipped for {email}: {e}")


def _audit(db, actor: Optional[dict], action: str, entity_type: str,
           entity_id: Optional[str], company_id: Optional[str] = None, **meta) -> None:
    db.add(AuditLog(
        company_id=company_id,
        actor_user_id=(actor or {}).get("id"),
        action=action, entity_type=entity_type, entity_id=entity_id,
        meta=meta or {},
    ))


def _company_dict(c: Company, agents: Optional[int] = None, admins: Optional[int] = None) -> dict:
    return {
        "id": c.id, "name": c.name, "slug": c.slug, "status": c.status,
        "plan_id": c.plan_id, "max_seats": c.max_seats, "settings": c.settings or {},
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "agent_count": agents, "admin_count": admins,
    }


def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "name": u.name, "role": u.role,
        "status": u.status, "company_id": u.company_id, "must_reset_pw": u.must_reset_pw,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _paginate(page: int, page_size: int) -> tuple[int, int]:
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    return page, page_size


# ── Companies (Super Admin) ──────────────────────────────────────────────────
def create_company(name: str, admin_email: str, admin_name: str,
                   actor: dict, plan_id: Optional[str] = None, max_seats: int = 25,
                   admin_password: Optional[str] = None) -> dict:
    name = (name or "").strip()
    admin_email = (admin_email or "").strip().lower()
    if not name:
        raise ManagementError("Company name is required.")
    if not admin_email:
        raise ManagementError("An admin email is required to create a company.")

    pw = admin_password or generate_password()
    with session_scope() as db:
        if db.scalar(select(User.id).where(User.email == admin_email)):
            raise ManagementError("A user with that email already exists.", 409)

        company = Company(
            name=name, slug=_unique_slug(db, name), status=COMPANY_ACTIVE,
            plan_id=plan_id, max_seats=int(max_seats or 25),
            created_by=(actor or {}).get("id"),
        )
        db.add(company)
        db.flush()

        salt, pw_hash = hash_password(pw)
        admin = User(
            company_id=company.id, email=admin_email, name=(admin_name or admin_email).strip(),
            role=ROLE_COMPANY_ADMIN, status=USER_ACTIVE, salt=salt, pw_hash=pw_hash,
            must_reset_pw=True, created_by=(actor or {}).get("id"),
        )
        db.add(admin)
        _audit(db, actor, "company.create", "company", company.id, company_id=company.id, name=name)
        _audit(db, actor, "user.create", "user", admin.id, company_id=company.id,
               email=admin_email, role=ROLE_COMPANY_ADMIN)
        db.flush()
        result = _company_dict(company, agents=0, admins=1)
        result["admin"] = {**_user_dict(admin), "temp_password": pw}
    _send_credentials_email(admin_email, admin_name or admin_email, pw, ROLE_COMPANY_ADMIN)
    return result


def list_companies(q: str = "", status: str = "", page: int = 1, page_size: int = 20) -> dict:
    page, page_size = _paginate(page, page_size)
    with session_scope() as db:
        stmt = select(Company)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(func.lower(Company.name).like(like), Company.slug.like(like)))
        if status:
            stmt = stmt.where(Company.status == status)
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.scalars(
            stmt.order_by(Company.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        items = []
        for c in rows:
            agents = db.scalar(select(func.count()).select_from(User).where(
                User.company_id == c.id, User.role == ROLE_SALES_AGENT))
            admins = db.scalar(select(func.count()).select_from(User).where(
                User.company_id == c.id, User.role == ROLE_COMPANY_ADMIN))
            items.append(_company_dict(c, agents=agents, admins=admins))
        return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_company(company_id: str) -> dict:
    with session_scope() as db:
        c = db.get(Company, company_id)
        if not c:
            raise ManagementError("Company not found.", 404)
        agents = db.scalar(select(func.count()).select_from(User).where(
            User.company_id == c.id, User.role == ROLE_SALES_AGENT))
        admins = db.scalar(select(func.count()).select_from(User).where(
            User.company_id == c.id, User.role == ROLE_COMPANY_ADMIN))
        return _company_dict(c, agents=agents, admins=admins)


def update_company(company_id: str, actor: dict, **fields) -> dict:
    allowed = {"name", "plan_id", "max_seats", "settings"}
    with session_scope() as db:
        c = db.get(Company, company_id)
        if not c:
            raise ManagementError("Company not found.", 404)
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(c, k, v)
        _audit(db, actor, "company.update", "company", c.id, company_id=c.id,
               fields=[k for k in fields if k in allowed])
        db.flush()
        return _company_dict(c)


def set_company_status(company_id: str, enabled: bool, actor: dict) -> dict:
    with session_scope() as db:
        c = db.get(Company, company_id)
        if not c:
            raise ManagementError("Company not found.", 404)
        c.status = COMPANY_ACTIVE if enabled else COMPANY_DISABLED
        _audit(db, actor, "company.enable" if enabled else "company.disable",
               "company", c.id, company_id=c.id)
        db.flush()
        return _company_dict(c)


def delete_company(company_id: str, actor: dict) -> None:
    with session_scope() as db:
        c = db.get(Company, company_id)
        if not c:
            raise ManagementError("Company not found.", 404)
        _audit(db, actor, "company.delete", "company", c.id, company_id=c.id, name=c.name)
        db.delete(c)  # cascades to users + sessions via FK ondelete


# ── Users: admins + agents ───────────────────────────────────────────────────
def create_user(company_id: str, email: str, name: str, role: str, actor: dict,
                password: Optional[str] = None) -> dict:
    email = (email or "").strip().lower()
    if role not in (ROLE_COMPANY_ADMIN, ROLE_SALES_AGENT):
        raise ManagementError("Role must be company_admin or sales_agent.")
    if not email:
        raise ManagementError("Email is required.")

    pw = password or generate_password()
    with session_scope() as db:
        company = db.get(Company, company_id)
        if not company:
            raise ManagementError("Company not found.", 404)
        if db.scalar(select(User.id).where(User.email == email)):
            raise ManagementError("A user with that email already exists.", 409)

        # Seat enforcement (agents count against the company's seat limit).
        if role == ROLE_SALES_AGENT:
            used = db.scalar(select(func.count()).select_from(User).where(
                User.company_id == company_id, User.role == ROLE_SALES_AGENT,
                User.status == USER_ACTIVE))
            if used >= company.max_seats:
                raise ManagementError(
                    f"Seat limit reached ({company.max_seats}). Upgrade the plan or deactivate an agent.",
                    409)

        salt, pw_hash = hash_password(pw)
        user = User(
            company_id=company_id, email=email, name=(name or email).strip(),
            role=role, status=USER_ACTIVE, salt=salt, pw_hash=pw_hash,
            must_reset_pw=True, created_by=(actor or {}).get("id"),
        )
        db.add(user)
        _audit(db, actor, "user.create", "user", None, company_id=company_id,
               email=email, role=role)
        db.flush()
        result = {**_user_dict(user), "temp_password": pw}
    _send_credentials_email(email, name or email, pw, role)
    return result


def list_users(company_id: Optional[str] = None, role: str = "", q: str = "",
               status: str = "", page: int = 1, page_size: int = 20) -> dict:
    page, page_size = _paginate(page, page_size)
    with session_scope() as db:
        stmt = select(User)
        if company_id:
            stmt = stmt.where(User.company_id == company_id)
        if role:
            stmt = stmt.where(User.role == role)
        if status:
            stmt = stmt.where(User.status == status)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.scalars(
            stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return {"items": [_user_dict(u) for u in rows], "total": total,
                "page": page, "page_size": page_size}


def _get_scoped_user(db, user_id: str, company_id: Optional[str]) -> User:
    u = db.get(User, user_id)
    if not u or (company_id and u.company_id != company_id):
        raise ManagementError("User not found.", 404)
    return u


def get_user(user_id: str, company_id: Optional[str] = None) -> dict:
    with session_scope() as db:
        return _user_dict(_get_scoped_user(db, user_id, company_id))


def update_user(user_id: str, actor: dict, company_id: Optional[str] = None, **fields) -> dict:
    allowed = {"name"}
    with session_scope() as db:
        u = _get_scoped_user(db, user_id, company_id)
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(u, k, v)
        _audit(db, actor, "user.update", "user", u.id, company_id=u.company_id)
        db.flush()
        return _user_dict(u)


def set_user_status(user_id: str, enabled: bool, actor: dict,
                    company_id: Optional[str] = None) -> dict:
    with session_scope() as db:
        u = _get_scoped_user(db, user_id, company_id)
        if u.id == (actor or {}).get("id"):
            raise ManagementError("You cannot change your own account status.")
        u.status = USER_ACTIVE if enabled else USER_DISABLED
        _audit(db, actor, "user.activate" if enabled else "user.deactivate",
               "user", u.id, company_id=u.company_id)
        db.flush()
        return _user_dict(u)


def reset_password(user_id: str, actor: dict, company_id: Optional[str] = None,
                   password: Optional[str] = None) -> dict:
    pw = password or generate_password()
    with session_scope() as db:
        u = _get_scoped_user(db, user_id, company_id)
        u.salt, u.pw_hash = hash_password(pw)
        u.must_reset_pw = True
        _audit(db, actor, "user.reset_password", "user", u.id, company_id=u.company_id)
        db.flush()
        return {**_user_dict(u), "temp_password": pw}


# ── Stats ─────────────────────────────────────────────────────────────────────
def platform_stats() -> dict:
    with session_scope() as db:
        companies = db.scalar(select(func.count()).select_from(Company))
        active_companies = db.scalar(select(func.count()).select_from(Company).where(
            Company.status == COMPANY_ACTIVE))
        agents = db.scalar(select(func.count()).select_from(User).where(User.role == ROLE_SALES_AGENT))
        admins = db.scalar(select(func.count()).select_from(User).where(User.role == ROLE_COMPANY_ADMIN))
        seats = db.scalar(select(func.coalesce(func.sum(Company.max_seats), 0)))
        return {
            "companies": companies, "active_companies": active_companies,
            "agents": agents, "admins": admins, "total_seats": seats,
        }


def company_stats(company_id: str) -> dict:
    with session_scope() as db:
        c = db.get(Company, company_id)
        if not c:
            raise ManagementError("Company not found.", 404)
        agents = db.scalar(select(func.count()).select_from(User).where(
            User.company_id == company_id, User.role == ROLE_SALES_AGENT))
        active = db.scalar(select(func.count()).select_from(User).where(
            User.company_id == company_id, User.role == ROLE_SALES_AGENT,
            User.status == USER_ACTIVE))
        return {
            "company": _company_dict(c), "agents": agents, "active_agents": active,
            "seats_used": active, "max_seats": c.max_seats,
        }


def list_login_history(company_id: Optional[str] = None, page: int = 1, page_size: int = 50) -> dict:
    page, page_size = _paginate(page, page_size)
    with session_scope() as db:
        stmt = select(LoginHistory)
        if company_id:
            stmt = stmt.where(LoginHistory.company_id == company_id)
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.scalars(
            stmt.order_by(LoginHistory.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        items = [{
            "id": r.id, "email": r.email, "success": r.success, "reason": r.reason,
            "ip": r.ip, "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}


def list_audit(company_id: Optional[str] = None, page: int = 1, page_size: int = 50) -> dict:
    page, page_size = _paginate(page, page_size)
    with session_scope() as db:
        stmt = select(AuditLog)
        if company_id:
            stmt = stmt.where(AuditLog.company_id == company_id)
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.scalars(
            stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        items = [{
            "id": a.id, "company_id": a.company_id, "actor_user_id": a.actor_user_id,
            "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
            "meta": a.meta or {}, "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}
