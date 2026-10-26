"""
core/email.py
─────────────
Tiny, dependency-free email sender (stdlib smtplib). Production delivery is
driven entirely by env vars; when SMTP is not configured it logs the message
instead of failing, so dev/recovery flows keep working end-to-end.

Env:
  SMTP_HOST        — enable real sending (if unset → log-only mode)
  SMTP_PORT        — default 587
  SMTP_USER        — login user (optional for open relays)
  SMTP_PASSWORD    — login password
  SMTP_FROM        — From address (default SMTP_USER or no-reply@posiviewtech.com)
  SMTP_SSL         — "true" for implicit TLS (port 465); else STARTTLS
  APP_URL          — base URL used to build links in emails (e.g. https://edge.posiviewtech.com)
"""

import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("hdfc")

BRAND = "Posiview Edge"


def is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST"))


def app_url() -> str:
    return os.getenv("APP_URL", "").rstrip("/")


def _html_to_text(html: str) -> str:
    text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</\s*p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    """Send one email. Returns True if actually sent, False in log-only mode or
    on error (never raises — callers treat email as best-effort)."""
    host = os.getenv("SMTP_HOST")
    if not host:
        log.info(f"[EMAIL] (log-only — SMTP not configured) to={to!r} subject={subject!r}")
        return False

    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or user or "no-reply@posiviewtech.com"

    msg = EmailMessage()
    msg["From"] = f"{BRAND} <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or _html_to_text(html))
    msg.add_alternative(html, subtype="html")

    try:
        ctx = ssl.create_default_context()
        if os.getenv("SMTP_SSL", "").lower() in ("1", "true", "yes"):
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ctx)
                if user:
                    s.login(user, password)
                s.send_message(msg)
        log.info(f"[EMAIL] sent to {to}: {subject!r}")
        return True
    except Exception as e:
        log.warning(f"[EMAIL] send failed to {to}: {e}")
        return False


# ── Templated messages ────────────────────────────────────────────────────────
def _shell(body_html: str) -> str:
    return (
        '<div style="font-family:Inter,Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;'
        'color:#0f172a">'
        '<div style="background:linear-gradient(120deg,#0a1628,#e31837 175%);padding:22px 24px;'
        'border-radius:14px 14px 0 0;color:#fff;font-size:18px;font-weight:800">Posiview Edge</div>'
        f'<div style="border:1px solid #e8edf5;border-top:none;border-radius:0 0 14px 14px;'
        f'padding:24px">{body_html}</div></div>'
    )


def _button(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;background:#e31837;color:#fff;'
        'text-decoration:none;font-weight:700;padding:11px 20px;border-radius:10px;'
        f'margin:10px 0">{label}</a>'
    )


def send_password_reset(to: str, link: str) -> bool:
    body = (
        "<p>We received a request to reset your Posiview Edge password.</p>"
        f"<p>{_button(link, 'Reset your password')}</p>"
        "<p style='font-size:13px;color:#5b6479'>This link expires in 1 hour. "
        "If you didn't request this, you can safely ignore this email.</p>"
    )
    return send_email(to, "Reset your Posiview Edge password", _shell(body))


def send_credentials(to: str, name: str, temp_password: str, role: str = "") -> bool:
    role_label = {"company_admin": "Company Admin", "sales_agent": "Sales Agent"}.get(role, "user")
    url = app_url() or ""
    cta = _button(url, "Sign in to Posiview Edge") if url else ""
    body = (
        f"<p>Hi {name or 'there'},</p>"
        f"<p>An account has been created for you on Posiview Edge as a <b>{role_label}</b>.</p>"
        "<p style='background:#f6f8fc;border:1px solid #e8edf5;border-radius:10px;padding:12px 14px'>"
        f"<b>Email:</b> {to}<br><b>Temporary password:</b> "
        f"<code style='font-weight:700'>{temp_password}</code></p>"
        f"<p>{cta}</p>"
        "<p style='font-size:13px;color:#5b6479'>You'll be asked to set a new password on first sign-in.</p>"
    )
    return send_email(to, "Your Posiview Edge account", _shell(body))
