"""
core/models.py
──────────────
ORM models for the enterprise foundation (Phase 0): identity, tenancy, and the
audit/session/recovery tables that every later phase builds on.

Conventions
  * String UUID primary keys (`uuid4().hex`) — portable across SQLite & Postgres.
  * Every TENANT-SCOPED row carries `company_id` (indexed). Super-admin rows have
    `company_id = NULL` (they live above any single company).
  * Roles/statuses are plain strings with module constants (no native DB enums)
    so adding a role later is a data change, not a schema migration.

Feature/report tables (pitches, roleplay_sessions, training_progress, …) are
introduced in their own phases when those features are wired to the DB; this file
intentionally stays focused on the identity core.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

# ── Role + status constants ───────────────────────────────────────────────────
ROLE_SUPER_ADMIN = "super_admin"
ROLE_COMPANY_ADMIN = "company_admin"
ROLE_SALES_AGENT = "sales_agent"
ALL_ROLES = (ROLE_SUPER_ADMIN, ROLE_COMPANY_ADMIN, ROLE_SALES_AGENT)

USER_INVITED = "invited"
USER_ACTIVE = "active"
USER_DISABLED = "disabled"

COMPANY_ACTIVE = "active"
COMPANY_DISABLED = "disabled"


def _uuid() -> str:
    return uuid.uuid4().hex


# ── Company (tenant) ──────────────────────────────────────────────────────────
class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=COMPANY_ACTIVE, nullable=False)

    # Subscription/licensing hooks (fully wired in the billing phase).
    plan_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    max_seats: Mapped[int] = mapped_column(Integer, default=25, nullable=False)

    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    users: Mapped[list["User"]] = relationship(back_populates="company")

    @property
    def is_active(self) -> bool:
        return self.status == COMPANY_ACTIVE


# ── User ──────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # NULL for super_admin (platform-level); set for admins and agents.
    company_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    role: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=USER_ACTIVE, nullable=False)

    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    pw_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    must_reset_pw: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Set once when the agent finishes (or explicitly skips) the product tour;
    # NULL means the tour auto-runs on their next sign-in.
    tour_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    company: Mapped[Optional["Company"]] = relationship(back_populates="users")

    @property
    def is_active(self) -> bool:
        return self.status == USER_ACTIVE

    def public_dict(self) -> dict:
        """Safe representation returned to the client (never includes secrets)."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "company_id": self.company_id,
            "must_reset_pw": self.must_reset_pw,
            "tour_completed": self.tour_completed_at is not None,
        }


# ── Session (server-side, revocable) ─────────────────────────────────────────
class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ── Login history (powers lockout + login-history reports) ───────────────────
class LoginHistory(Base):
    __tablename__ = "login_history"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ── Audit log (every mutating action, per tenant) ────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ── Password reset tokens ─────────────────────────────────────────────────────
class PasswordReset(Base):
    __tablename__ = "password_resets"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ── Activity records (tenant-scoped) ─────────────────────────────────────────
# Pitch conversations and chat sessions keep their original rich nested shape in
# a JSON `data` blob (so the frontend is unchanged), plus scope + sort columns.
# `user_id` is the owner; `company_id` the tenant. Agents read only their own
# rows; company admins read all rows in their company.

class PitchConversation(Base):
    __tablename__ = "pitch_conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )


class RoleplaySession(Base):
    """A completed RolePlay simulation with its scored report (per agent)."""
    __tablename__ = "roleplay_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    prospect_name: Mapped[str] = mapped_column(String(200), default="")
    profession: Mapped[str] = mapped_column(String(120), default="")
    loan_type: Mapped[str] = mapped_column(String(120), default="")
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # overall_score 0-100
    grade: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set when the call was launched against a booked seat (Assistant/Practice
    # slots) — this link is the whole attendance/session-report mechanism.
    slot_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    booking_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )


# ── Scheduling: role-play slots + seat bookings ──────────────────────────────
# One slot = one bookable hour with a hard seat capacity. `kind` separates
# admin-planned Assistant sessions (scenario pinned by the admin) from open
# Practice hours (agent picks their own scenario at launch). Both kinds share
# the same capacity/booking machinery so every avatar call draws from one
# concurrency budget.

SLOT_KIND_ASSISTANT = "assistant"
SLOT_KIND_PRACTICE = "practice"
SLOT_KINDS = (SLOT_KIND_ASSISTANT, SLOT_KIND_PRACTICE)

SLOT_OPEN = "open"
SLOT_CANCELLED = "cancelled"
SLOT_COMPLETED = "completed"

ENROLL_OPEN = "open"          # agents self-book
ENROLL_ASSIGNED = "assigned"  # closed session — only admin-placed agents
ENROLL_HYBRID = "hybrid"      # admin pre-places some, rest self-bookable
ENROLLMENTS = (ENROLL_OPEN, ENROLL_ASSIGNED, ENROLL_HYBRID)

BOOKING_BOOKED = "booked"
BOOKING_ATTENDED = "attended"
BOOKING_NO_SHOW = "no_show"
BOOKING_CANCELLED = "cancelled"
# Statuses that occupy a seat (count toward capacity).
BOOKING_ACTIVE = (BOOKING_BOOKED, BOOKING_ATTENDED)


class RoleplaySlot(Base):
    __tablename__ = "roleplay_slots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default=SLOT_KIND_ASSISTANT, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    enrollment: Mapped[str] = mapped_column(String(16), default=ENROLL_OPEN, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(String(2000), default="")
    # Assistant slots: preset id + full launch-config snapshot. The snapshot is
    # what gets pinned at launch, so later preset edits never rewrite history.
    scenario_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scenario_config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=SLOT_OPEN, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SlotBooking(Base):
    __tablename__ = "slot_bookings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    slot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("roleplay_slots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    company_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=BOOKING_BOOKED, nullable=False)
    # NULL = the agent self-booked; an admin's user id when the seat was assigned.
    assigned_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Tavus conversation id of the launched call — links the scored report row.
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    booked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    attended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# One seat per user per slot (among non-cancelled rows this is enforced in the
# booking service transaction; the index accelerates the lookup).
Index("idx_booking_slot_user", SlotBooking.slot_id, SlotBooking.user_id)

# Composite index: recent login attempts per email (lockout window queries).
Index("idx_login_email_time", LoginHistory.email, LoginHistory.created_at)
