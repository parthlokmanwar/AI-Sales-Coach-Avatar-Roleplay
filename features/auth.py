"""
features/auth.py
────────────────
Sign-in API for the production build. Guards the rest of the application: only
the five predefined Posiview accounts (see core/auth.py) can obtain a session.

Endpoints
  POST /api/auth/login    — exchange email + password for a session token
  POST /api/auth/logout   — revoke the current session
  GET  /api/auth/me       — return the signed-in user (used to validate a
                            stored token on app load)

The session token is returned in the JSON body (the SPA stores it and sends it
as `Authorization: Bearer <token>`) AND set as an HttpOnly cookie (so Swagger
"/docs" and direct API access work within the same browser). Either is accepted
by the gate middleware in main.py.
"""

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import auth

log = logging.getLogger("hdfc")
router = APIRouter()

COOKIE_NAME = "pv_auth_token"


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


_LOGIN_ERRORS = {
    "invalid_credentials": "Invalid email or password.",
    "user_disabled": "This account has been deactivated. Please contact your administrator.",
    "company_disabled": "Your organization's access has been disabled. Please contact your administrator.",
}


@router.post("/api/auth/login")
async def login(body: LoginRequest, request: Request):
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")

    user, reason = auth.authenticate(body.email, body.password)
    if not user:
        auth.record_login(body.email, success=False, reason=reason, ip=ip, user_agent=ua)
        log.warning(f"[AUTH] Failed login for {body.email!r} ({reason})")
        return JSONResponse(
            status_code=401,
            content={"success": False, "detail": _LOGIN_ERRORS.get(reason, _LOGIN_ERRORS["invalid_credentials"])},
        )

    token = auth.create_session(user["id"], ip=ip, user_agent=ua)
    auth.record_login(user["email"], success=True, reason="ok", ip=ip, user_agent=ua,
                      user_id=user["id"], company_id=user.get("company_id"))
    log.info(f"[AUTH] {user['email']} ({user['role']}) signed in.")
    response = JSONResponse(content={"success": True, "token": token, "user": user})
    _set_session_cookie(response, token)
    return response


@router.post("/api/auth/logout")
async def logout(request: Request):
    token = _extract_token(request)
    auth.delete_session(token)
    response = JSONResponse(content={"success": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/api/auth/me")
async def me(request: Request):
    # The gate middleware has already validated the token and attached the user.
    user = getattr(request.state, "user", None)
    if not user:
        # Belt-and-suspenders: resolve directly in case middleware is bypassed.
        user = auth.get_session_user(_extract_token(request))
    if not user:
        return JSONResponse(status_code=401, content={"success": False})
    return {"success": True, "user": user}


@router.post("/api/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """Begin account recovery. Always returns a generic success so the endpoint
    can't be used to probe which emails exist. The reset link is delivered out of
    band — until email is wired up, it's written to the server log for the admin.
    """
    token = auth.create_password_reset(body.email)
    if token:
        from core import email as email_svc
        base = request.headers.get("Origin") or email_svc.app_url()
        link = f"{base}/?reset_token={token}"
        log.info(f"[AUTH] Password reset for {body.email}: {link}")
        email_svc.send_password_reset(body.email, link)  # best-effort (logs if no SMTP)
    return {"success": True, "detail": "If an account exists for that email, a reset link has been sent."}


@router.post("/api/auth/reset-password")
async def reset_password(body: ResetPasswordRequest):
    ok, reason = auth.reset_password_with_token(body.token, body.new_password)
    if not ok:
        detail = ("Password must be at least 8 characters."
                  if reason == "weak_password"
                  else "This reset link is invalid or has expired.")
        return JSONResponse(status_code=400, content={"success": False, "detail": detail})
    return {"success": True}


def _extract_token(request: Request):
    """Pull the session token from the Authorization header or the cookie."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE_NAME)
