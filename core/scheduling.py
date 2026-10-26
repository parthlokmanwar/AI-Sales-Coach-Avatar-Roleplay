"""
core/scheduling.py
──────────────────
Slot-booking service for the three-mode redesign (Assistant + Practice).

One slot = one bookable hour with a hard seat capacity (default 25). Admin
creates slots (singly or as a day grid); agents self-book open seats or are
assigned by the admin. Every mutation goes through this module so the capacity
rule lives in exactly one place.

Capacity is enforced atomically: insert-then-count inside a single transaction,
serialised by a row lock on Postgres (SELECT ... FOR UPDATE) and by a process
lock on SQLite (single-process dev server). "Full" is never a stored flag —
it is always computed, so cancellations reopen seats automatically.

All datetimes are stored naive-UTC (repo convention) and serialised with an
explicit +00:00 offset so browsers render them in local (IST) time.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select

from core.db import DATABASE_URL, session_scope
from core.management import ManagementError
from core.models import (
    BOOKING_ACTIVE,
    BOOKING_ATTENDED,
    BOOKING_BOOKED,
    BOOKING_CANCELLED,
    BOOKING_NO_SHOW,
    ENROLL_ASSIGNED,
    ENROLL_OPEN,
    ENROLLMENTS,
    ROLE_SALES_AGENT,
    SLOT_CANCELLED,
    SLOT_COMPLETED,
    SLOT_KINDS,
    SLOT_OPEN,
    RoleplaySession,
    RoleplaySlot,
    SlotBooking,
    User,
)

log = logging.getLogger("hdfc")

_IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite has no row locks — serialise bookings with a process-wide lock instead.
# The dev server is single-process uvicorn, so this is airtight there; Postgres
# deployments rely on with_for_update() and can run many workers.
_BOOK_LOCK = threading.Lock()

# A booked call may join from 5 minutes before the slot starts until it ends.
JOIN_EARLY_S = 5 * 60


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Naive-UTC → ISO string with explicit offset (browsers show local time)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    """ISO string (any offset) → naive UTC datetime."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ManagementError(f"Invalid datetime: {value!r}", 400)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _active_count(db, slot_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(SlotBooking)
        .where(SlotBooking.slot_id == slot_id, SlotBooking.status.in_(BOOKING_ACTIVE))
    ) or 0


def _slot_dict(slot: RoleplaySlot, booked_count: int, my_booking: Optional[dict] = None) -> dict:
    return {
        "id": slot.id,
        "kind": slot.kind,
        "start_at": _iso(slot.start_at),
        "end_at": _iso(slot.end_at),
        "capacity": slot.capacity,
        "enrollment": slot.enrollment,
        "title": slot.title or "",
        "notes": slot.notes or "",
        "scenario_ref": slot.scenario_ref or "",
        "scenario_config": slot.scenario_config or {},
        "status": slot.status,
        "booked_count": booked_count,
        "is_full": booked_count >= slot.capacity,
        "my_booking": my_booking,
    }


def _booking_dict(b: SlotBooking) -> dict:
    return {
        "id": b.id,
        "slot_id": b.slot_id,
        "user_id": b.user_id,
        "status": b.status,
        "assigned": b.assigned_by is not None,
        "booked_at": _iso(b.booked_at),
        "attended_at": _iso(b.attended_at),
    }


def _get_slot(db, slot_id: str, company_id: Optional[str]) -> RoleplaySlot:
    slot = db.get(RoleplaySlot, slot_id)
    if not slot or (company_id and slot.company_id != company_id):
        raise ManagementError("Session slot not found.", 404)
    return slot


# ── Slot CRUD (admin) ────────────────────────────────────────────────────────

def create_slots(
    *, company_id: str, actor_id: str, kind: str, slots: list[dict],
    capacity: int = 25, enrollment: str = ENROLL_OPEN, title: str = "",
    notes: str = "", scenario_ref: str = "", scenario_config: Optional[dict] = None,
) -> list[dict]:
    """Create one or many slots (a day grid is just many). Each item in `slots`
    is {"start_at": iso, "end_at": iso}. Exact duplicates (same company, kind,
    start) are skipped, so re-submitting a day grid is idempotent."""
    if kind not in SLOT_KINDS:
        raise ManagementError(f"kind must be one of {SLOT_KINDS}.", 400)
    if enrollment not in ENROLLMENTS:
        raise ManagementError(f"enrollment must be one of {ENROLLMENTS}.", 400)
    if not slots:
        raise ManagementError("No time slots given.", 400)
    if not (1 <= int(capacity) <= 500):
        raise ManagementError("Capacity must be between 1 and 500.", 400)

    created: list[dict] = []
    with session_scope() as db:
        for item in slots:
            start = _parse_dt(item.get("start_at", ""))
            end = _parse_dt(item.get("end_at", ""))
            if end <= start:
                raise ManagementError("Slot end must be after its start.", 400)
            dup = db.scalar(
                select(RoleplaySlot).where(
                    RoleplaySlot.company_id == company_id,
                    RoleplaySlot.kind == kind,
                    RoleplaySlot.start_at == start,
                    RoleplaySlot.status == SLOT_OPEN,
                ).limit(1)
            )
            if dup:
                continue
            slot = RoleplaySlot(
                company_id=company_id, kind=kind, start_at=start, end_at=end,
                capacity=int(capacity), enrollment=enrollment, title=title or "",
                notes=notes or "", scenario_ref=scenario_ref or None,
                scenario_config=scenario_config or {}, created_by=actor_id,
            )
            db.add(slot)
            db.flush()
            created.append(_slot_dict(slot, 0))
    log.info(f"[SCHED] {actor_id} created {len(created)} {kind} slot(s) for {company_id}")
    return created


def list_slots(
    *, company_id: str, date_from: str, date_to: str, kind: str = "",
    for_user_id: str = "", include_cancelled: bool = False,
) -> list[dict]:
    """Slots in [date_from, date_to) with live booked counts.

    When `for_user_id` is set (agent view): only open slots the agent can see —
    self-bookable ones plus any they already hold a seat in."""
    start = _parse_dt(date_from)
    end = _parse_dt(date_to)
    with session_scope() as db:
        stmt = select(RoleplaySlot).where(
            RoleplaySlot.company_id == company_id,
            RoleplaySlot.start_at >= start,
            RoleplaySlot.start_at < end,
        ).order_by(RoleplaySlot.start_at)
        if kind:
            stmt = stmt.where(RoleplaySlot.kind == kind)
        if not include_cancelled:
            stmt = stmt.where(RoleplaySlot.status != SLOT_CANCELLED)
        slots = list(db.scalars(stmt))

        out = []
        for slot in slots:
            count = _active_count(db, slot.id)
            mine = None
            if for_user_id:
                b = db.scalar(
                    select(SlotBooking).where(
                        SlotBooking.slot_id == slot.id,
                        SlotBooking.user_id == for_user_id,
                        SlotBooking.status.in_(BOOKING_ACTIVE),
                    ).limit(1)
                )
                mine = _booking_dict(b) if b else None
                # Agents never see closed (assignment-only) sessions they're not in.
                if slot.enrollment == ENROLL_ASSIGNED and not mine:
                    continue
            out.append(_slot_dict(slot, count, mine))
        return out


def update_slot(*, slot_id: str, company_id: str, **fields) -> dict:
    with session_scope() as db:
        slot = _get_slot(db, slot_id, company_id)
        if slot.status != SLOT_OPEN:
            raise ManagementError("Only open sessions can be edited.", 409)
        count = _active_count(db, slot.id)
        if "capacity" in fields and fields["capacity"] is not None:
            cap = int(fields["capacity"])
            if cap < count:
                raise ManagementError(
                    f"Capacity can't drop below the {count} seats already booked.", 409)
            slot.capacity = cap
        for name in ("title", "notes", "scenario_ref"):
            if fields.get(name) is not None:
                setattr(slot, name, fields[name])
        if fields.get("enrollment") is not None:
            if fields["enrollment"] not in ENROLLMENTS:
                raise ManagementError(f"enrollment must be one of {ENROLLMENTS}.", 400)
            slot.enrollment = fields["enrollment"]
        if fields.get("scenario_config") is not None:
            slot.scenario_config = fields["scenario_config"]
        db.flush()
        return _slot_dict(slot, count)


def cancel_slot(*, slot_id: str, company_id: str, actor_id: str) -> dict:
    """Cancel a session; active bookings flip to cancelled (email best-effort)."""
    with session_scope() as db:
        slot = _get_slot(db, slot_id, company_id)
        if slot.status != SLOT_OPEN:
            raise ManagementError("Session is already cancelled or completed.", 409)
        slot.status = SLOT_CANCELLED
        bookings = list(db.scalars(
            select(SlotBooking).where(
                SlotBooking.slot_id == slot.id,
                SlotBooking.status.in_(BOOKING_ACTIVE),
            )
        ))
        now = datetime.utcnow()
        emails = []
        for b in bookings:
            b.status = BOOKING_CANCELLED
            b.cancelled_at = now
            u = db.get(User, b.user_id)
            if u:
                emails.append((u.email, u.name))
        db.flush()
        result = _slot_dict(slot, 0)
    _notify_cancelled(emails, result)
    log.info(f"[SCHED] {actor_id} cancelled slot {slot_id} ({len(emails)} bookings released)")
    return result


# ── Booking (agent self-book + admin assignment) ─────────────────────────────

def book_slot(
    *, slot_id: str, user: dict, target_user_id: str = "",
    assigned_by: str = "",
) -> dict:
    """Book one seat. Atomic against the capacity: insert-then-count inside one
    transaction; over-capacity rolls back with 409 slot_full.

    Self-booking: target is the caller. Assignment: `assigned_by` is the admin,
    `target_user_id` the agent receiving the seat."""
    target_id = target_user_id or user["id"]
    company_id = user.get("company_id")

    with _BOOK_LOCK:  # serialises SQLite; harmless double-guard on Postgres
        with session_scope() as db:
            if _IS_SQLITE:
                slot = db.get(RoleplaySlot, slot_id)
            else:
                slot = db.get(RoleplaySlot, slot_id, with_for_update=True)
            if not slot or (company_id and slot.company_id != company_id):
                raise ManagementError("Session slot not found.", 404)
            if slot.status != SLOT_OPEN:
                raise ManagementError("This session has been cancelled or completed.", 409)
            if datetime.utcnow() >= slot.end_at:
                raise ManagementError("This session has already ended.", 409)
            if not assigned_by and slot.enrollment == ENROLL_ASSIGNED:
                raise ManagementError(
                    "This session is assignment-only. Your admin adds participants.", 403)

            target = db.get(User, target_id)
            if not target or (company_id and target.company_id != company_id):
                raise ManagementError("Agent not found in this company.", 404)

            existing = db.scalar(
                select(SlotBooking).where(
                    SlotBooking.slot_id == slot.id,
                    SlotBooking.user_id == target_id,
                    SlotBooking.status.in_(BOOKING_ACTIVE),
                ).limit(1)
            )
            if existing:
                raise ManagementError("Already booked into this session.", 409)

            booking = SlotBooking(
                slot_id=slot.id, user_id=target_id, company_id=slot.company_id,
                status=BOOKING_BOOKED, assigned_by=assigned_by or None,
            )
            db.add(booking)
            db.flush()
            # Insert-then-count: the race loser sees count > capacity and rolls back.
            if _active_count(db, slot.id) > slot.capacity:
                raise ManagementError(
                    "This session is full — all seats are taken.", 409)
            result = _booking_dict(booking)
            result["slot"] = _slot_dict(slot, _active_count(db, slot.id))
            email_to = (target.email, target.name)

    _notify_booked(email_to, result["slot"], assigned=bool(assigned_by))
    return result


def cancel_booking(*, slot_id: str, user: dict, target_user_id: str = "",
                   by_admin: bool = False) -> dict:
    """Release a seat. Agents may cancel their own SELF-booked seat before the
    slot starts; assigned seats are mandatory — admin-releasable only."""
    target_id = target_user_id or user["id"]
    with session_scope() as db:
        slot = _get_slot(db, slot_id, user.get("company_id"))
        booking = db.scalar(
            select(SlotBooking).where(
                SlotBooking.slot_id == slot.id,
                SlotBooking.user_id == target_id,
                SlotBooking.status.in_(BOOKING_ACTIVE),
            ).limit(1)
        )
        if not booking:
            raise ManagementError("No active booking for this session.", 404)
        if not by_admin:
            if booking.assigned_by:
                raise ManagementError(
                    "This seat was assigned by your admin and can't be cancelled here.", 403)
            if datetime.utcnow() >= slot.start_at:
                raise ManagementError("The session has started — seats can no longer be cancelled.", 409)
        booking.status = BOOKING_CANCELLED
        booking.cancelled_at = datetime.utcnow()
        db.flush()
        return _booking_dict(booking)


def assign_agents(*, slot_id: str, user: dict, user_ids: list[str]) -> dict:
    """Bulk-assign agents into a slot. Partial success: reports who didn't fit."""
    assigned, skipped = [], []
    for uid in user_ids:
        try:
            b = book_slot(slot_id=slot_id, user=user, target_user_id=uid,
                          assigned_by=user["id"])
            assigned.append(b)
        except ManagementError as e:
            skipped.append({"user_id": uid, "reason": e.detail})
    return {"assigned": assigned, "skipped": skipped}


def my_bookings(*, user_id: str) -> list[dict]:
    """The agent's active bookings with their slot details, soonest first."""
    with session_scope() as db:
        rows = db.execute(
            select(SlotBooking, RoleplaySlot)
            .join(RoleplaySlot, SlotBooking.slot_id == RoleplaySlot.id)
            .where(SlotBooking.user_id == user_id,
                   SlotBooking.status.in_(BOOKING_ACTIVE),
                   RoleplaySlot.status == SLOT_OPEN)
            .order_by(RoleplaySlot.start_at)
        ).all()
        out = []
        for booking, slot in rows:
            d = _booking_dict(booking)
            d["slot"] = _slot_dict(slot, _active_count(db, slot.id))
            out.append(d)
        return out


# ── Launch gate + attendance ─────────────────────────────────────────────────

def access_state(*, user_id: str) -> dict:
    """The user's role-play access right now, derived from their bookings.

    live : slot dict (+booking_id) whose join window [start−5min, end) contains
           now — holding one means the simulation is open for this user.
    next : their soonest upcoming booked slot, for "come back at HH:MM" UI.
    """
    now = datetime.utcnow()
    with session_scope() as db:
        rows = db.execute(
            select(SlotBooking, RoleplaySlot)
            .join(RoleplaySlot, SlotBooking.slot_id == RoleplaySlot.id)
            .where(SlotBooking.user_id == user_id,
                   SlotBooking.status.in_(BOOKING_ACTIVE),
                   RoleplaySlot.status == SLOT_OPEN)
            .order_by(RoleplaySlot.start_at)
        ).all()
        live = nxt = None
        for booking, slot in rows:
            if slot.start_at - timedelta(seconds=JOIN_EARLY_S) <= now < slot.end_at:
                if live is None:
                    live = _slot_dict(slot, _active_count(db, slot.id))
                    live["booking_id"] = booking.id
            elif slot.start_at > now and nxt is None:
                nxt = _slot_dict(slot, _active_count(db, slot.id))
                nxt["booking_id"] = booking.id
        return {"allowed": live is not None, "live": live, "next": nxt}


def validate_booking_for_launch(*, booking_id: str, user: dict) -> dict:
    """A booked call may launch from (start − 5 min) until slot end. Returns the
    slot dict (incl. scenario snapshot) so the caller can pin the scenario."""
    with session_scope() as db:
        booking = db.get(SlotBooking, booking_id)
        if not booking or booking.user_id != user["id"]:
            raise ManagementError("Booking not found.", 404)
        if booking.status not in BOOKING_ACTIVE:
            raise ManagementError("This booking was cancelled.", 409)
        slot = db.get(RoleplaySlot, booking.slot_id)
        if not slot or slot.status != SLOT_OPEN:
            raise ManagementError("This session is no longer available.", 409)
        now = datetime.utcnow()
        if now < slot.start_at - timedelta(seconds=JOIN_EARLY_S):
            mins = int((slot.start_at - now).total_seconds() // 60)
            when = f"in about {mins} minutes" if mins < 120 else "later"
            raise ManagementError(
                f"This session hasn't opened yet — joining unlocks 5 minutes "
                f"before the start ({when}).", 409)
        if now >= slot.end_at:
            raise ManagementError("This session has ended.", 409)
        d = _slot_dict(slot, _active_count(db, slot.id))
        d["booking_id"] = booking.id
        return d


def mark_attended(*, booking_id: str, conversation_id: str) -> None:
    """First successful launch against a booking marks it attended."""
    with session_scope() as db:
        booking = db.get(SlotBooking, booking_id)
        if not booking:
            return
        booking.conversation_id = conversation_id
        if booking.status == BOOKING_BOOKED:
            booking.status = BOOKING_ATTENDED
            booking.attended_at = datetime.utcnow()


def link_for_report(conversation_id: str) -> tuple[Optional[str], Optional[str]]:
    """(slot_id, booking_id) for a conversation launched from a booked seat."""
    if not conversation_id:
        return None, None
    with session_scope() as db:
        booking = db.scalar(
            select(SlotBooking).where(
                SlotBooking.conversation_id == conversation_id).limit(1))
        if not booking:
            return None, None
        return booking.slot_id, booking.id


# ── Slot completion sweep (rides the roleplay reaper loop) ───────────────────

def complete_due_slots() -> int:
    """Mark ended slots completed + their unlaunched bookings no_show.
    Returns how many slots were closed. Cheap; safe to call often."""
    now = datetime.utcnow()
    closed = 0
    with session_scope() as db:
        due = list(db.scalars(
            select(RoleplaySlot).where(
                RoleplaySlot.status == SLOT_OPEN,
                RoleplaySlot.end_at <= now,
            )
        ))
        for slot in due:
            for b in db.scalars(
                select(SlotBooking).where(
                    SlotBooking.slot_id == slot.id,
                    SlotBooking.status == BOOKING_BOOKED)):
                b.status = BOOKING_NO_SHOW
            slot.status = SLOT_COMPLETED
            closed += 1
    if closed:
        log.info(f"[SCHED] completed {closed} due slot(s)")
    return closed


# ── Roster + session report (admin) ──────────────────────────────────────────

def roster(*, slot_id: str, company_id: str) -> dict:
    """Bookings joined with user identity + linked report scores."""
    with session_scope() as db:
        slot = _get_slot(db, slot_id, company_id)
        rows = db.execute(
            select(SlotBooking, User)
            .join(User, SlotBooking.user_id == User.id)
            .where(SlotBooking.slot_id == slot.id)
            .order_by(SlotBooking.booked_at)
        ).all()
        entries = []
        for booking, u in rows:
            report_row = db.scalar(
                select(RoleplaySession).where(
                    RoleplaySession.booking_id == booking.id).limit(1))
            d = _booking_dict(booking)
            d.update({
                "name": u.name, "email": u.email,
                "score": report_row.score if report_row else None,
                "grade": report_row.grade if report_row else None,
                "report_id": report_row.id if report_row else None,
            })
            entries.append(d)
        return {"slot": _slot_dict(slot, _active_count(db, slot.id)),
                "roster": entries}


def session_report(*, slot_id: str, company_id: str) -> dict:
    """Admin-facing aggregate for one slot: attendance, score stats, weak areas,
    per-agent rows with drill-down report ids."""
    data = roster(slot_id=slot_id, company_id=company_id)
    entries = data["roster"]

    def n(status):
        return sum(1 for e in entries if e["status"] == status)

    scores = [e["score"] for e in entries if e["score"] is not None]
    grades: dict[str, int] = {}
    for e in entries:
        if e["grade"]:
            grades[e["grade"]] = grades.get(e["grade"], 0) + 1

    # Weak areas: average every named/scored parameter found in the linked
    # reports (defensive — report shapes vary across avatar types).
    param_scores: dict[str, list[float]] = {}
    with session_scope() as db:
        for e in entries:
            if not e["report_id"]:
                continue
            row = db.get(RoleplaySession, e["report_id"])
            if not row or not isinstance(row.report, dict):
                continue
            for params in (row.report.get("parameters"),
                           (row.report.get("pitch_parameters") or {}).get("parameters")):
                if not isinstance(params, list):
                    continue
                for p in params:
                    if not isinstance(p, dict):
                        continue
                    name = p.get("name") or p.get("parameter") or ""
                    score = p.get("score")
                    if name and isinstance(score, (int, float)):
                        param_scores.setdefault(name, []).append(float(score))
    weak = sorted(
        ({"parameter": k, "avg_score": round(sum(v) / len(v), 1), "n": len(v)}
         for k, v in param_scores.items()),
        key=lambda x: x["avg_score"],
    )[:5]

    return {
        "slot": data["slot"],
        "summary": {
            "booked": len([e for e in entries if e["status"] != BOOKING_CANCELLED]),
            "attended": n(BOOKING_ATTENDED),
            "no_show": n(BOOKING_NO_SHOW),
            "cancelled": n(BOOKING_CANCELLED),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None,
            "grades": grades,
            "weak_areas": weak,
        },
        "roster": entries,
    }


# ── Email notifications (best-effort, log-only without SMTP) ─────────────────

def _fmt_window(slot: dict) -> str:
    return f"{slot['start_at']} – {slot['end_at']}"


def _notify_booked(email_to: tuple[str, str], slot: dict, assigned: bool) -> None:
    try:
        from core import email as email_svc
        to, name = email_to
        what = slot.get("title") or ("Practice session" if slot["kind"] == "practice"
                                     else "Role-play session")
        verb = "You've been assigned to" if assigned else "Your seat is confirmed for"
        email_svc.send_email(
            to, f"{'Assigned' if assigned else 'Booked'}: {what}",
            f"<p>Hi {name or 'there'},</p>"
            f"<p>{verb} <b>{what}</b>.</p>"
            f"<p><b>When:</b> {_fmt_window(slot)} (shown in UTC — your app displays local time)</p>"
            "<p>Join from the app up to 5 minutes before the start.</p>",
        )
    except Exception as e:
        log.warning(f"[SCHED] booking email failed: {e}")


def _notify_cancelled(emails: list[tuple[str, str]], slot: dict) -> None:
    try:
        from core import email as email_svc
        what = slot.get("title") or "Role-play session"
        for to, name in emails:
            email_svc.send_email(
                to, f"Cancelled: {what}",
                f"<p>Hi {name or 'there'},</p>"
                f"<p>The session <b>{what}</b> ({_fmt_window(slot)}) was cancelled "
                "by your administrator. Your seat has been released.</p>",
            )
    except Exception as e:
        log.warning(f"[SCHED] cancellation email failed: {e}")
