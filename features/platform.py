"""
features/platform.py
────────────────────
Super Admin console API (`/api/platform/*`). Every route requires the
`super_admin` role (enforced by Depends(require_super_admin)); the gate
middleware in main.py has already validated the session.

Covers: platform stats, company lifecycle (create/list/get/update/enable/
disable/delete), creating a company's admin accounts, viewing a company's
users, and the platform-wide audit trail.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import analytics as analytics_svc
from core import management as mgmt
from core.models import ROLE_COMPANY_ADMIN
from core.rbac import require_super_admin

log = logging.getLogger("hdfc")
router = APIRouter(prefix="/api/platform", dependencies=[Depends(require_super_admin)])


# ── Schemas ───────────────────────────────────────────────────────────────────
class CompanyCreate(BaseModel):
    name: str
    admin_email: str
    admin_name: str = ""
    plan_id: Optional[str] = None
    max_seats: int = 25
    admin_password: Optional[str] = None


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    plan_id: Optional[str] = None
    max_seats: Optional[int] = None
    settings: Optional[dict] = None


class AdminCreate(BaseModel):
    email: str
    name: str = ""
    password: Optional[str] = None


# ── Stats + analytics ─────────────────────────────────────────────────────────
@router.get("/stats")
async def stats(user=Depends(require_super_admin)):
    return {"success": True, "stats": mgmt.platform_stats()}


@router.get("/analytics")
async def analytics(days: int = 14, user=Depends(require_super_admin)):
    return {"success": True, "analytics": analytics_svc.platform_analytics(days=days)}


@router.get("/login-history")
async def login_history(company_id: Optional[str] = None, page: int = 1, page_size: int = 50,
                        user=Depends(require_super_admin)):
    return {"success": True, **mgmt.list_login_history(company_id=company_id, page=page, page_size=page_size)}


# ── Companies ─────────────────────────────────────────────────────────────────
@router.get("/companies")
async def companies(q: str = "", status: str = "", page: int = 1, page_size: int = 20,
                    user=Depends(require_super_admin)):
    return {"success": True, **mgmt.list_companies(q=q, status=status, page=page, page_size=page_size)}


@router.post("/companies")
async def create_company(body: CompanyCreate, user=Depends(require_super_admin)):
    company = mgmt.create_company(
        name=body.name, admin_email=body.admin_email, admin_name=body.admin_name,
        actor=user, plan_id=body.plan_id, max_seats=body.max_seats,
        admin_password=body.admin_password,
    )
    return {"success": True, "company": company}


@router.get("/companies/{company_id}")
async def get_company(company_id: str, user=Depends(require_super_admin)):
    return {"success": True, "company": mgmt.get_company(company_id)}


@router.patch("/companies/{company_id}")
async def update_company(company_id: str, body: CompanyUpdate, user=Depends(require_super_admin)):
    company = mgmt.update_company(company_id, actor=user, **body.model_dump(exclude_none=True))
    return {"success": True, "company": company}


@router.post("/companies/{company_id}/enable")
async def enable_company(company_id: str, user=Depends(require_super_admin)):
    return {"success": True, "company": mgmt.set_company_status(company_id, True, actor=user)}


@router.post("/companies/{company_id}/disable")
async def disable_company(company_id: str, user=Depends(require_super_admin)):
    return {"success": True, "company": mgmt.set_company_status(company_id, False, actor=user)}


@router.delete("/companies/{company_id}")
async def delete_company(company_id: str, user=Depends(require_super_admin)):
    mgmt.delete_company(company_id, actor=user)
    return {"success": True}


# ── A company's users (super admin can view/manage any company) ───────────────
@router.get("/companies/{company_id}/users")
async def company_users(company_id: str, role: str = "", q: str = "", status: str = "",
                        page: int = 1, page_size: int = 20, user=Depends(require_super_admin)):
    return {"success": True, **mgmt.list_users(
        company_id=company_id, role=role, q=q, status=status, page=page, page_size=page_size)}


@router.post("/companies/{company_id}/admins")
async def create_admin(company_id: str, body: AdminCreate, user=Depends(require_super_admin)):
    admin = mgmt.create_user(
        company_id=company_id, email=body.email, name=body.name,
        role=ROLE_COMPANY_ADMIN, actor=user, password=body.password)
    return {"success": True, "user": admin}


# ── Audit ─────────────────────────────────────────────────────────────────────
@router.get("/audit")
async def audit(company_id: Optional[str] = None, page: int = 1, page_size: int = 50,
                user=Depends(require_super_admin)):
    return {"success": True, **mgmt.list_audit(company_id=company_id, page=page, page_size=page_size)}
