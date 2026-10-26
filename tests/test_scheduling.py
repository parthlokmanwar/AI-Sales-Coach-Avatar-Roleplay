"""
Unit tests for the slot-booking service (core/scheduling.py).

Covers: slot creation (bulk + duplicate skip), the atomic 25-seat capacity rule
under a real thread race, double-book rejection, cancel-then-rebook, the
assigned-seat cancel policy, the launch window gate, attendance marking, and
the no-show completion sweep.

Run:  python -m pytest tests/test_scheduling.py -q

The suite points DATABASE_URL at a throwaway SQLite file BEFORE importing
core.db (which freezes the URL at import time), so the dev database is never
touched.
"""

import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import pytest

# Isolate the DB before core.db is imported anywhere in this process.
_TMP = tempfile.mkdtemp(prefix="sched_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db').replace(os.sep, '/')}"

from core.db import init_db, session_scope           # noqa: E402
from core import scheduling as sched                 # noqa: E402
from core.management import ManagementError          # noqa: E402
from core.models import (                            # noqa: E402
    BOOKING_ATTENDED,
    BOOKING_NO_SHOW,
    ROLE_COMPANY_ADMIN,
    ROLE_SALES_AGENT,
    SLOT_COMPLETED,
    Company,
    RoleplaySlot,
    SlotBooking,
    User,
)

COMPANY = "co_test"


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _mk_user(db, uid: str, role: str = ROLE_SALES_AGENT) -> None:
    db.add(User(id=uid, company_id=COMPANY, email=f"{uid}@t.example",
                name=uid, role=role, salt="s", pw_hash="h"))


@pytest.fixture(scope="module", autouse=True)
def _setup():
    init_db()
    with session_scope() as db:
        db.add(Company(id=COMPANY, name="Test Co", slug="test-co"))
        db.flush()
        _mk_user(db, "admin1", ROLE_COMPANY_ADMIN)
        for i in range(30):
            _mk_user(db, f"agent{i:02d}")


def _admin() -> dict:
    return {"id": "admin1", "company_id": COMPANY, "role": ROLE_COMPANY_ADMIN}


def _agent(i: int) -> dict:
    return {"id": f"agent{i:02d}", "company_id": COMPANY, "role": ROLE_SALES_AGENT}


def _make_slot(capacity=25, start_in_min=30, kind="assistant", enrollment="open",
               scenario_config=None) -> dict:
    start = datetime.utcnow() + timedelta(minutes=start_in_min)
    return sched.create_slots(
        company_id=COMPANY, actor_id="admin1", kind=kind,
        slots=[{"start_at": _iso(start), "end_at": _iso(start + timedelta(hours=1))}],
        capacity=capacity, enrollment=enrollment, title="Test session",
        scenario_config=scenario_config or {},
    )[0]


# ── Slot creation ─────────────────────────────────────────────────────────────

def test_bulk_day_grid_and_duplicate_skip():
    base = datetime.utcnow() + timedelta(days=7)
    day = base.replace(hour=4, minute=30, second=0, microsecond=0)  # 10:00 IST
    grid = [{"start_at": _iso(day + timedelta(hours=h)),
             "end_at": _iso(day + timedelta(hours=h + 1))} for h in range(3)]
    first = sched.create_slots(company_id=COMPANY, actor_id="admin1",
                               kind="assistant", slots=grid)
    assert len(first) == 3
    # Re-submitting the same grid is idempotent — all duplicates skipped.
    again = sched.create_slots(company_id=COMPANY, actor_id="admin1",
                               kind="assistant", slots=grid)
    assert again == []


def test_end_before_start_rejected():
    start = datetime.utcnow() + timedelta(hours=2)
    with pytest.raises(ManagementError):
        sched.create_slots(
            company_id=COMPANY, actor_id="admin1", kind="practice",
            slots=[{"start_at": _iso(start), "end_at": _iso(start - timedelta(hours=1))}])


# ── Booking: capacity + duplicates ───────────────────────────────────────────

def test_capacity_full_and_double_book():
    slot = _make_slot(capacity=3)
    for i in range(3):
        sched.book_slot(slot_id=slot["id"], user=_agent(i))
    # Seat 4 → full.
    with pytest.raises(ManagementError) as e:
        sched.book_slot(slot_id=slot["id"], user=_agent(3))
    assert e.value.status_code == 409 and "full" in e.value.detail.lower()
    # Same agent twice → rejected.
    with pytest.raises(ManagementError) as e:
        sched.book_slot(slot_id=slot["id"], user=_agent(0))
    assert "already" in e.value.detail.lower()


def test_capacity_race_under_threads():
    """25 threads fight for 5 seats — exactly 5 must win."""
    slot = _make_slot(capacity=5)
    results = []

    def try_book(i):
        try:
            sched.book_slot(slot_id=slot["id"], user=_agent(i))
            results.append(("ok", i))
        except ManagementError:
            results.append(("full", i))

    threads = [threading.Thread(target=try_book, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r, _ in results if r == "ok") == 5
    with session_scope() as db:
        assert sched._active_count(db, slot["id"]) == 5


def test_cancel_reopens_seat_then_rebook():
    slot = _make_slot(capacity=1)
    sched.book_slot(slot_id=slot["id"], user=_agent(0))
    with pytest.raises(ManagementError):
        sched.book_slot(slot_id=slot["id"], user=_agent(1))
    sched.cancel_booking(slot_id=slot["id"], user=_agent(0))
    # "Full" is computed, never stored — the freed seat is immediately bookable.
    sched.book_slot(slot_id=slot["id"], user=_agent(1))


# ── Assignment policy ────────────────────────────────────────────────────────

def test_assign_partial_success_and_mandatory_seats():
    slot = _make_slot(capacity=2, enrollment="hybrid")
    result = sched.assign_agents(slot_id=slot["id"], user=_admin(),
                                 user_ids=["agent00", "agent01", "agent02"])
    assert len(result["assigned"]) == 2
    assert len(result["skipped"]) == 1          # third didn't fit — reported, not lost
    # An assigned seat is mandatory: the agent can't self-cancel it…
    with pytest.raises(ManagementError) as e:
        sched.cancel_booking(slot_id=slot["id"], user=_agent(0))
    assert e.value.status_code == 403
    # …but the admin can release it.
    sched.cancel_booking(slot_id=slot["id"], user=_admin(),
                         target_user_id="agent00", by_admin=True)


def test_assignment_only_slot_blocks_self_booking():
    slot = _make_slot(enrollment="assigned")
    with pytest.raises(ManagementError) as e:
        sched.book_slot(slot_id=slot["id"], user=_agent(5))
    assert e.value.status_code == 403
    # Invisible to agents browsing (not in their list) …
    day0 = datetime.utcnow() - timedelta(hours=1)
    visible = sched.list_slots(
        company_id=COMPANY, date_from=_iso(day0),
        date_to=_iso(day0 + timedelta(days=30)), for_user_id="agent05")
    assert slot["id"] not in [s["id"] for s in visible]
    # …until they hold an assigned seat in it.
    sched.assign_agents(slot_id=slot["id"], user=_admin(), user_ids=["agent05"])
    visible = sched.list_slots(
        company_id=COMPANY, date_from=_iso(day0),
        date_to=_iso(day0 + timedelta(days=30)), for_user_id="agent05")
    assert slot["id"] in [s["id"] for s in visible]


# ── Launch window + attendance ───────────────────────────────────────────────

def test_launch_window_gate_and_attendance():
    # Slot starting in 2 minutes → inside the 5-minute early-join window.
    slot = _make_slot(start_in_min=2, scenario_config={"prospect_name": "Pinned"})
    booking = sched.book_slot(slot_id=slot["id"], user=_agent(7))
    ok = sched.validate_booking_for_launch(booking_id=booking["id"], user=_agent(7))
    assert ok["scenario_config"]["prospect_name"] == "Pinned"
    # Another agent can't launch someone else's booking.
    with pytest.raises(ManagementError):
        sched.validate_booking_for_launch(booking_id=booking["id"], user=_agent(8))
    # Slot starting tomorrow → too early to join.
    far = _make_slot(start_in_min=60 * 24)
    b2 = sched.book_slot(slot_id=far["id"], user=_agent(9))
    with pytest.raises(ManagementError) as e:
        sched.validate_booking_for_launch(booking_id=b2["id"], user=_agent(9))
    assert e.value.status_code == 409
    # Launch marks attendance + records the conversation for report linkage.
    sched.mark_attended(booking_id=booking["id"], conversation_id="conv_xyz")
    assert sched.link_for_report("conv_xyz") == (slot["id"], booking["id"])
    with session_scope() as db:
        assert db.get(SlotBooking, booking["id"]).status == BOOKING_ATTENDED


def test_access_state_gates_by_live_window():
    """access_state drives the RolePlay tab gate: allowed only inside a booked
    slot's join window; otherwise the next booking is surfaced for the UI."""
    user = _agent(12)
    # No bookings at all → closed, nothing upcoming.
    state = sched.access_state(user_id=user["id"])
    assert state == {"allowed": False, "live": None, "next": None}
    # Only a future booking → still closed, but 'next' carries the slot time.
    future = _make_slot(start_in_min=90, kind="practice")
    b_future = sched.book_slot(slot_id=future["id"], user=user)
    state = sched.access_state(user_id=user["id"])
    assert not state["allowed"] and state["live"] is None
    assert state["next"]["id"] == future["id"]
    assert state["next"]["booking_id"] == b_future["id"]
    # A slot inside the join window (starts in 2 min) → open, booking attached.
    live = _make_slot(start_in_min=2, kind="practice")
    b_live = sched.book_slot(slot_id=live["id"], user=user)
    state = sched.access_state(user_id=user["id"])
    assert state["allowed"] and state["live"]["id"] == live["id"]
    assert state["live"]["booking_id"] == b_live["id"]


# ── Completion sweep ─────────────────────────────────────────────────────────

def test_complete_due_slots_marks_no_shows():
    # A slot fully in the past (create directly — service refuses ended slots).
    start = datetime.utcnow() - timedelta(hours=2)
    with session_scope() as db:
        slot = RoleplaySlot(company_id=COMPANY, kind="assistant", start_at=start,
                            end_at=start + timedelta(hours=1), capacity=25,
                            created_by="admin1")
        db.add(slot)
        db.flush()
        db.add(SlotBooking(slot_id=slot.id, user_id="agent10", company_id=COMPANY))
        db.add(SlotBooking(slot_id=slot.id, user_id="agent11", company_id=COMPANY,
                           status=BOOKING_ATTENDED))
        slot_id = slot.id

    assert sched.complete_due_slots() >= 1
    with session_scope() as db:
        assert db.get(RoleplaySlot, slot_id).status == SLOT_COMPLETED
        statuses = {b.user_id: b.status for b in db.query(SlotBooking).filter_by(slot_id=slot_id)}
    assert statuses["agent10"] == BOOKING_NO_SHOW      # never launched
    assert statuses["agent11"] == BOOKING_ATTENDED     # attended stays attended

    report = sched.session_report(slot_id=slot_id, company_id=COMPANY)
    assert report["summary"]["no_show"] == 1
    assert report["summary"]["attended"] == 1
