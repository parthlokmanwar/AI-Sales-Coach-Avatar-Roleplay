"""
features/me.py
──────────────
Self-service account endpoints for the signed-in user (any role):

  GET   /api/me/profile           — own profile (name, email, role, company, …)
  PATCH /api/me/profile           — update own display name
  POST  /api/me/change-password   — change own password (verifies the old one)

All routes require a valid session (the gate middleware attaches the user).
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import auth
from core import activity
from core.rbac import get_current_user, tenant_scope

log = logging.getLogger("hdfc")
router = APIRouter(prefix="/api/me")


class ProfileUpdate(BaseModel):
    name: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


@router.get("/stats")
async def my_stats(scope=Depends(tenant_scope)):
    """The signed-in agent's own totals for the home dashboard."""
    return {"success": True, "stats": activity.my_counts(scope)}


@router.get("/profile")
async def get_profile(user: dict = Depends(get_current_user)):
    profile = auth.get_profile(user["id"])
    if not profile:
        return JSONResponse(status_code=404, content={"success": False, "detail": "Profile not found."})
    return {"success": True, "profile": profile}


@router.patch("/profile")
async def update_profile(body: ProfileUpdate, user: dict = Depends(get_current_user)):
    updated = auth.update_profile(user["id"], body.name)
    if not updated:
        return JSONResponse(status_code=400, content={"success": False, "detail": "Name cannot be empty."})
    return {"success": True, "user": updated}


@router.post("/tour-complete")
async def tour_complete(user: dict = Depends(get_current_user)):
    """Mark the product tour as done for this user (finished OR explicitly skipped).

    Idempotent — the frontend also mirrors the flag in localStorage, this is the
    cross-device source of truth surfaced as `tour_completed` on /api/auth/me.
    """
    updated = auth.mark_tour_complete(user["id"])
    if not updated:
        return JSONResponse(status_code=404, content={"success": False, "detail": "Account not found."})
    return {"success": True, "user": updated}


_PW_ERRORS = {
    "weak_password": f"Password must be at least {auth.MIN_PASSWORD_LEN} characters.",
    "invalid_credentials": "Your current password is incorrect.",
    "not_found": "Account not found.",
}


@router.post("/change-password")
async def change_password(body: ChangePassword, user: dict = Depends(get_current_user)):
    ok, reason = auth.change_password(user["id"], body.old_password, body.new_password)
    if not ok:
        return JSONResponse(status_code=400, content={"success": False, "detail": _PW_ERRORS.get(reason, "Could not change password.")})
    log.info(f"[AUTH] {user['email']} changed password.")
    return {"success": True}
