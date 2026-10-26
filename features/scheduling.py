"""
features/scheduling.py
──────────────────────
Slot-booking API for the three-mode redesign.

Admin surface (`/api/admin/slots*`, requires company_admin like features/admin.py):
  POST   /api/admin/slots                — create one slot or a bulk day grid
  GET    /api/admin/slots?from&to        — calendar with booked counts
  PATCH  /api/admin/slots/{id}           — edit capacity/scenario/notes/enrollment
  POST   /api/admin/slots/{id}/cancel    — cancel session (+notify booked agents)
  POST   /api/admin/slots/{id}/assign    — bulk-assign agents (partial success)
  POST   /api/admin/slots/{id}/unassign  — release an assigned/booked seat
  GET    /api/admin/slots/{id}/roster    — bookings + attendance + report scores
  GET    /api/admin/slots/{id}/report    — session report (aggregates + drill-down)
  GET    /api/admin/roleplay-reports/{id}— one agent's full report (drill-down)

Agent surface (any signed-in role):
  GET    /api/slots?from&to&kind         — browsable slots (n/cap, is_full, mine)
  POST   /api/slots/{id}/book            — atomic self-book (409 when full)
  POST   /api/slots/{id}/cancel-booking  — release own self-booked seat
  GET    /api/me/bookings                — my active bookings, soonest first

All service logic lives in core/scheduling.py; these routes stay thin like the
other feature routers.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import scheduling as sched
from core.db import session_scope
from core.management import ManagementError
from core.models import ROLE_SUPER_ADMIN, RoleplaySession
from core.rbac import get_current_user, require_company_admin

log = logging.getLogger("hdfc")

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_company_admin)])
agent_router = APIRouter()


def _company_id(user: dict, explicit: Optional[str] = None) -> str:
    """Same resolution rule as features/admin.py — super admins must name one."""
    if user.get("role") == ROLE_SUPER_ADMIN:
        if not explicit:
            raise ManagementError("Super admin must supply ?company_id=.", 400)
        return explicit
    cid = user.get("company_id")
    if not cid:
        raise ManagementError("No company associated with this account.", 400)
    return cid


# ── Schemas ──────────────────────────────────────────────────────────────────

class SlotTime(BaseModel):
    start_at: str
    end_at: str


class SlotsCreate(BaseModel):
    kind: str = "assistant"                  # "assistant" | "practice"
    slots: list[SlotTime]
    capacity: int = 25
    enrollment: str = "open"                 # "open" | "assigned" | "hybrid"
    title: str = ""
    notes: str = ""
    scenario_ref: str = ""
    scenario_config: dict = {}


class SlotUpdate(BaseModel):
    capacity: Optional[int] = None
    enrollment: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    scenario_ref: Optional[str] = None
    scenario_config: Optional[dict] = None


class AssignRequest(BaseModel):
    user_ids: list[str]


class UnassignRequest(BaseModel):
    user_id: str


# ── Admin routes ─────────────────────────────────────────────────────────────

@admin_router.post("/slots")
async def create_slots(body: SlotsCreate, company_id: Optional[str] = None,
                       user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    created = sched.create_slots(
        company_id=cid, actor_id=user["id"], kind=body.kind,
        slots=[s.model_dump() for s in body.slots], capacity=body.capacity,
        enrollment=body.enrollment, title=body.title, notes=body.notes,
        scenario_ref=body.scenario_ref, scenario_config=body.scenario_config,
    )
    return {"success": True, "created": created}


@admin_router.get("/slots")
async def admin_list_slots(date_from: str, date_to: str, kind: str = "",
                           include_cancelled: bool = False,
                           company_id: Optional[str] = None,
                           user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "slots": sched.list_slots(
        company_id=cid, date_from=date_from, date_to=date_to, kind=kind,
        include_cancelled=include_cancelled)}


@admin_router.patch("/slots/{slot_id}")
async def update_slot(slot_id: str, body: SlotUpdate,
                      company_id: Optional[str] = None,
                      user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "slot": sched.update_slot(
        slot_id=slot_id, company_id=cid, **body.model_dump())}


@admin_router.post("/slots/{slot_id}/cancel")
async def cancel_slot(slot_id: str, company_id: Optional[str] = None,
                      user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "slot": sched.cancel_slot(
        slot_id=slot_id, company_id=cid, actor_id=user["id"])}


@admin_router.post("/slots/{slot_id}/assign")
async def assign_agents(slot_id: str, body: AssignRequest,
                        company_id: Optional[str] = None,
                        user=Depends(require_company_admin)):
    result = sched.assign_agents(slot_id=slot_id, user=user, user_ids=body.user_ids)
    return {"success": True, **result}


@admin_router.post("/slots/{slot_id}/unassign")
async def unassign_agent(slot_id: str, body: UnassignRequest,
                         company_id: Optional[str] = None,
                         user=Depends(require_company_admin)):
    booking = sched.cancel_booking(slot_id=slot_id, user=user,
                                   target_user_id=body.user_id, by_admin=True)
    return {"success": True, "booking": booking}


@admin_router.get("/slots/{slot_id}/roster")
async def slot_roster(slot_id: str, company_id: Optional[str] = None,
                      user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, **sched.roster(slot_id=slot_id, company_id=cid)}


@admin_router.get("/slots/{slot_id}/report")
async def slot_report(slot_id: str, company_id: Optional[str] = None,
                      user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, **sched.session_report(slot_id=slot_id, company_id=cid)}


@admin_router.get("/roleplay-reports/{report_id}")
async def full_agent_report(report_id: str, company_id: Optional[str] = None,
                            user=Depends(require_company_admin)):
    """Drill-down target from the session report — one agent's full report."""
    cid = _company_id(user, company_id)
    with session_scope() as db:
        row = db.get(RoleplaySession, report_id)
        if not row or (cid and row.company_id != cid):
            raise ManagementError("Report not found.", 404)
        return {"success": True, "session": {
            "id": row.id, "user_id": row.user_id,
            "prospect_name": row.prospect_name, "profession": row.profession,
            "loan_type": row.loan_type, "score": row.score, "grade": row.grade,
            "report": row.report, "created_at": str(row.created_at),
        }}


# ── Agent routes ─────────────────────────────────────────────────────────────

@agent_router.get("/api/slots")
async def list_slots(date_from: str, date_to: str, kind: str = "",
                     user: dict = Depends(get_current_user)):
    cid = user.get("company_id")
    if not cid:
        return {"success": True, "slots": []}   # super admin browsing: nothing tenant-scoped
    return {"success": True, "slots": sched.list_slots(
        company_id=cid, date_from=date_from, date_to=date_to, kind=kind,
        for_user_id=user["id"])}


@agent_router.post("/api/slots/{slot_id}/book")
async def book(slot_id: str, user: dict = Depends(get_current_user)):
    return {"success": True, "booking": sched.book_slot(slot_id=slot_id, user=user)}


@agent_router.post("/api/slots/{slot_id}/cancel-booking")
async def cancel_booking(slot_id: str, user: dict = Depends(get_current_user)):
    return {"success": True, "booking": sched.cancel_booking(slot_id=slot_id, user=user)}


@agent_router.get("/api/me/bookings")
async def me_bookings(user: dict = Depends(get_current_user)):
    return {"success": True, "bookings": sched.my_bookings(user_id=user["id"])}
