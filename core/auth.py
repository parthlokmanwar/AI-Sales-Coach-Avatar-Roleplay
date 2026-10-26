"""
core/auth.py
────────────
Authentication service, now backed by the SQLAlchemy data layer (Phase 0).

Replaces the original standalone-SQLite implementation. The public surface used
by main.py and features/auth.py is preserved (init_and_seed, get_session_user,
delete_session, SESSION_TTL_SECONDS) and extended for the role hierarchy:

  authenticate(email, password) -> (user_dict | None, reason)
  create_session(user_id, ip, user_agent) -> token
  get_session_user(token) -> user_dict | None      (role + company aware)
  record_login(...)                                 (login history / lockout)

User dicts now carry `id`, `role`, and `company_id` so the gate middleware and
the SPA can branch on the three-tier hierarchy (super_admin / company_admin /
sales_agent).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from core.db import init_db, session_scope
from core.models import (
    COMPANY_ACTIVE,
    ROLE_COMPANY_ADMIN,
    ROLE_SALES_AGENT,
    ROLE_SUPER_ADMIN,
    USER_ACTIVE,
    Company,
    LoginHistory,
    PasswordReset,
    Session,
    User,
)
from core.security import hash_password, new_token, verify_password

log = logging.getLogger("hdfc")

# Session lifetime: 7 days.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


# ── Seed definition ───────────────────────────────────────────────────────────
# A working three-tier hierarchy out of the box. The five sales agents keep the
# exact credentials from the original build so existing logins still work.
SEED_SUPER_ADMIN = {
    "email": "superadmin@posiviewtech.com",
    "password": "SuperAdmin@1",
    "name": "Platform Super Admin",
}
SEED_COMPANY = {"name": "Posiview Demo", "slug": "posiview-demo", "max_seats": 25}
SEED_COMPANY_ADMIN = {
    "email": "admin@posiviewtech.com",
    "password": "Admin@123",
    "name": "Demo Company Admin",
}
SEED_AGENTS = [
    {"email": "user1@posiviewtech.com", "password": "Posiview@1", "name": "Posiview User 1"},
    {"email": "user2@posiviewtech.com", "password": "Posiview@2", "name": "Posiview User 2"},
    {"email": "user3@posiviewtech.com", "password": "Posiview@3", "name": "Posiview User 3"},
    {"email": "user4@posiviewtech.com", "password": "Posiview@4", "name": "Posiview User 4"},
    {"email": "user5@posiviewtech.com", "password": "Posiview@5", "name": "Posiview User 5"},
]


def _make_user(email: str, password: str, name: str, role: str,
               company_id: Optional[str], created_by: Optional[str] = None) -> User:
    salt, pw_hash = hash_password(password)
    return User(
        email=email.strip().lower(), name=name, role=role, status=USER_ACTIVE,
        company_id=company_id, salt=salt, pw_hash=pw_hash, created_by=created_by,
    )


def init_and_seed() -> None:
    """Create the schema (idempotent) and seed the default hierarchy.

    Insert-if-missing on every record, so it is safe to run on each startup and
    never clobbers data an admin has since changed.
    """
    init_db()
    with session_scope() as db:
        # 1. Super admin (platform level, no company).
        if not db.scalar(select(User).where(User.email == SEED_SUPER_ADMIN["email"])):
            db.add(_make_user(
                SEED_SUPER_ADMIN["email"], SEED_SUPER_ADMIN["password"],
                SEED_SUPER_ADMIN["name"], ROLE_SUPER_ADMIN, company_id=None,
            ))

        # 2. Demo company.
        company = db.scalar(select(Company).where(Company.slug == SEED_COMPANY["slug"]))
        if not company:
            company = Company(
                name=SEED_COMPANY["name"], slug=SEED_COMPANY["slug"],
                status=COMPANY_ACTIVE, max_seats=SEED_COMPANY["max_seats"],
            )
            db.add(company)
            db.flush()  # assign company.id before we attach users

        # 3. Company admin.
        if not db.scalar(select(User).where(User.email == SEED_COMPANY_ADMIN["email"])):
            db.add(_make_user(
                SEED_COMPANY_ADMIN["email"], SEED_COMPANY_ADMIN["password"],
                SEED_COMPANY_ADMIN["name"], ROLE_COMPANY_ADMIN, company_id=company.id,
            ))

        # 4. Five sales agents (original credentials).
        for a in SEED_AGENTS:
            if not db.scalar(select(User).where(User.email == a["email"])):
                db.add(_make_user(
                    a["email"], a["password"], a["name"], ROLE_SALES_AGENT,
                    company_id=company.id,
                ))

    total = _count_users()
    log.info(f"[AUTH] DB seeded — {total} users (1 super admin, 1 company admin, "
             f"{len(SEED_AGENTS)} agents under '{SEED_COMPANY['slug']}').")


def _count_users() -> int:
    with session_scope() as db:
        return len(db.scalars(select(User.id)).all())


# ── Authentication ────────────────────────────────────────────────────────────

def authenticate(email: str, password: str) -> tuple[Optional[dict], str]:
    """Verify credentials and account/company status.

    Returns (user_dict, "ok") on success, else (None, reason) where reason is one
    of: invalid_credentials | user_disabled | company_disabled.
    """
    email = (email or "").strip().lower()
    if not email or not password:
        return None, "invalid_credentials"
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user or not verify_password(password, user.salt, user.pw_hash):
            return None, "invalid_credentials"
        if user.status != USER_ACTIVE:
            return None, "user_disabled"
        if user.company_id:
            company = db.get(Company, user.company_id)
            if not company or company.status != COMPANY_ACTIVE:
                return None, "company_disabled"
        return user.public_dict(), "ok"


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(user_id: str, ip: Optional[str] = None,
                   user_agent: Optional[str] = None) -> str:
    """Mint a session token for an already-authenticated user, and stamp login."""
    token = new_token(32)
    now = datetime.utcnow()
    with session_scope() as db:
        db.add(Session(
            token=token, user_id=user_id, ip=ip,
            user_agent=(user_agent or "")[:400],
            created_at=now, expires_at=now + timedelta(seconds=SESSION_TTL_SECONDS),
        ))
        user = db.get(User, user_id)
        if user:
            user.last_login_at = now
    return token


def get_session_user(token: Optional[str]) -> Optional[dict]:
    """Resolve a session token to its user (role + company aware), or None."""
    if not token:
        return None
    now = datetime.utcnow()
    with session_scope() as db:
        sess = db.get(Session, token)
        if not sess or sess.revoked_at is not None or sess.expires_at < now:
            return None
        user = db.get(User, sess.user_id)
        if not user or user.status != USER_ACTIVE:
            return None
        if user.company_id:
            company = db.get(Company, user.company_id)
            if not company or company.status != COMPANY_ACTIVE:
                return None
        return user.public_dict()


def delete_session(token: Optional[str]) -> None:
    """Revoke a session (soft delete keeps the row for audit)."""
    if not token:
        return
    with session_scope() as db:
        sess = db.get(Session, token)
        if sess and sess.revoked_at is None:
            sess.revoked_at = datetime.utcnow()


def record_login(email: str, success: bool, reason: str,
                 ip: Optional[str] = None, user_agent: Optional[str] = None,
                 user_id: Optional[str] = None, company_id: Optional[str] = None) -> None:
    """Append a login attempt to login_history (audit + future lockout)."""
    with session_scope() as db:
        db.add(LoginHistory(
            email=(email or "")[:255], success=success, reason=reason,
            ip=ip, user_agent=(user_agent or "")[:400],
            user_id=user_id, company_id=company_id,
        ))


def list_users() -> list[str]:
    with session_scope() as db:
        return [e for (e,) in db.execute(select(User.email).order_by(User.email)).all()]


# ── Account lifecycle (Phase 4): profile, change/forgot/reset password ────────
MIN_PASSWORD_LEN = 8
RESET_TTL_SECONDS = 60 * 60  # password-reset links live for 1 hour


def get_profile(user_id: str) -> Optional[dict]:
    with session_scope() as db:
        user = db.get(User, user_id)
        if not user:
            return None
        d = user.public_dict()
        company_name = None
        if user.company_id:
            c = db.get(Company, user.company_id)
            company_name = c.name if c else None
        d["company_name"] = company_name
        d["created_at"] = user.created_at.isoformat() if user.created_at else None
        d["last_login_at"] = user.last_login_at.isoformat() if user.last_login_at else None
        return d


def update_profile(user_id: str, name: str) -> Optional[dict]:
    name = (name or "").strip()
    if not name:
        return None
    with session_scope() as db:
        user = db.get(User, user_id)
        if not user:
            return None
        user.name = name
        return user.public_dict()


def mark_tour_complete(user_id: str) -> Optional[dict]:
    """Stamp the one-time product-tour completion (idempotent)."""
    with session_scope() as db:
        user = db.get(User, user_id)
        if not user:
            return None
        if user.tour_completed_at is None:
            user.tour_completed_at = datetime.utcnow()
        return user.public_dict()


def change_password(user_id: str, old_password: str, new_password: str) -> tuple[bool, str]:
    """Self-service password change. Verifies the current password first."""
    if len(new_password or "") < MIN_PASSWORD_LEN:
        return False, "weak_password"
    with session_scope() as db:
        user = db.get(User, user_id)
        if not user:
            return False, "not_found"
        if not verify_password(old_password, user.salt, user.pw_hash):
            return False, "invalid_credentials"
        user.salt, user.pw_hash = hash_password(new_password)
        user.must_reset_pw = False
    return True, "ok"


def create_password_reset(email: str) -> Optional[str]:
    """Create a one-hour reset token for the email, or None if no such user.
    The caller must NOT leak existence to the client — always show a generic
    'if the account exists, a link was sent' message."""
    email = (email or "").strip().lower()
    if not email:
        return None
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            return None
        token = new_token(24)
        db.add(PasswordReset(
            token=token, user_id=user.id,
            expires_at=datetime.utcnow() + timedelta(seconds=RESET_TTL_SECONDS),
        ))
        return token


def reset_password_with_token(token: str, new_password: str) -> tuple[bool, str]:
    """Consume a reset token and set a new password."""
    if len(new_password or "") < MIN_PASSWORD_LEN:
        return False, "weak_password"
    now = datetime.utcnow()
    with session_scope() as db:
        pr = db.get(PasswordReset, token)
        if not pr or pr.used_at is not None or pr.expires_at < now:
            return False, "invalid_token"
        user = db.get(User, pr.user_id)
        if not user:
            return False, "invalid_token"
        user.salt, user.pw_hash = hash_password(new_password)
        user.must_reset_pw = False
        pr.used_at = now
    return True, "ok"
