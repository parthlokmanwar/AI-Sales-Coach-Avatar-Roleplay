"""
features/admin.py
─────────────────
Company Admin console API (`/api/admin/*`). Requires the `company_admin` role
(super_admin is also allowed, but must pass `?company_id=` since they have no
home company). All data access is scoped to a single company.

Covers: company stats, sales-agent lifecycle (create/list/get/update/activate/
deactivate/reset-password), and the company's audit trail.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from core import analytics as analytics_svc
from core import management as mgmt
from core.models import ROLE_SALES_AGENT, ROLE_SUPER_ADMIN
from core.rbac import require_company_admin

log = logging.getLogger("hdfc")
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_company_admin)])


def _company_id(user: dict, explicit: Optional[str]) -> str:
    """Resolve the company this admin acts on.

    Company admins are bound to their own company; super admins must name one.
    """
    if user.get("role") == ROLE_SUPER_ADMIN:
        if not explicit:
            raise mgmt.ManagementError("Super admin must supply ?company_id=.", 400)
        return explicit
    cid = user.get("company_id")
    if not cid:
        raise mgmt.ManagementError("No company associated with this account.", 400)
    return cid


# ── Schemas ───────────────────────────────────────────────────────────────────
class AgentCreate(BaseModel):
    email: str
    name: str = ""
    password: Optional[str] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None


# ── Stats ─────────────────────────────────────────────────────────────────────
@router.get("/stats")
async def stats(company_id: Optional[str] = None, user=Depends(require_company_admin)):
    return {"success": True, "stats": mgmt.company_stats(_company_id(user, company_id))}


# ── Analytics + report exports ────────────────────────────────────────────────
@router.get("/analytics")
async def analytics(days: int = 14, company_id: Optional[str] = None, user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "analytics": analytics_svc.company_analytics(cid, days=days)}


@router.get("/export/agents.csv")
async def export_agents_csv(company_id: Optional[str] = None, user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return Response(
        content=analytics_svc.agents_csv(cid), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="agents.csv"'},
    )


@router.get("/export/agents.xlsx")
async def export_agents_xlsx(company_id: Optional[str] = None, user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    company = mgmt.get_company(cid)
    return Response(
        content=analytics_svc.agents_xlsx(cid, company_name=company.get("name", "Company")),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="agents.xlsx"'},
    )


@router.get("/export/agents.pdf")
async def export_agents_pdf(company_id: Optional[str] = None, user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    company = mgmt.get_company(cid)
    return Response(
        content=analytics_svc.agents_pdf(cid, company_name=company.get("name", "Company")),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="agents.pdf"'},
    )


# ── Agents ────────────────────────────────────────────────────────────────────
@router.get("/agents")
async def agents(q: str = "", status: str = "", page: int = 1, page_size: int = 20,
                 company_id: Optional[str] = None, user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, **mgmt.list_users(
        company_id=cid, role=ROLE_SALES_AGENT, q=q, status=status, page=page, page_size=page_size)}


@router.post("/agents")
async def create_agent(body: AgentCreate, company_id: Optional[str] = None,
                       user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    agent = mgmt.create_user(
        company_id=cid, email=body.email, name=body.name,
        role=ROLE_SALES_AGENT, actor=user, password=body.password)
    return {"success": True, "user": agent}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, company_id: Optional[str] = None,
                    user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "user": mgmt.get_user(agent_id, company_id=cid)}


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, company_id: Optional[str] = None,
                       user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "user": mgmt.update_user(
        agent_id, actor=user, company_id=cid, **body.model_dump(exclude_none=True))}


@router.post("/agents/{agent_id}/activate")
async def activate_agent(agent_id: str, company_id: Optional[str] = None,
                         user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "user": mgmt.set_user_status(agent_id, True, actor=user, company_id=cid)}


@router.post("/agents/{agent_id}/deactivate")
async def deactivate_agent(agent_id: str, company_id: Optional[str] = None,
                           user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "user": mgmt.set_user_status(agent_id, False, actor=user, company_id=cid)}


@router.post("/agents/{agent_id}/reset-password")
async def reset_agent_password(agent_id: str, company_id: Optional[str] = None,
                               user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, "user": mgmt.reset_password(agent_id, actor=user, company_id=cid)}


# ── Audit + login history ─────────────────────────────────────────────────────
@router.get("/audit")
async def audit(page: int = 1, page_size: int = 50, company_id: Optional[str] = None,
                user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, **mgmt.list_audit(company_id=cid, page=page, page_size=page_size)}


@router.get("/login-history")
async def login_history(page: int = 1, page_size: int = 50, company_id: Optional[str] = None,
                        user=Depends(require_company_admin)):
    cid = _company_id(user, company_id)
    return {"success": True, **mgmt.list_login_history(company_id=cid, page=page, page_size=page_size)}
