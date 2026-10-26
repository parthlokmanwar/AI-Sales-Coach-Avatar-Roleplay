"""
core/analytics.py
─────────────────
Tenant-scoped analytics for the Company Admin dashboard + report exports.

Aggregates the real activity tables (pitch_conversations, chat_sessions) and the
user roster into per-company metrics: totals, a daily activity timeseries, and a
per-agent breakdown (pitches, chats, last sign-in). Aggregation is done in
Python after windowed queries so it stays portable across SQLite and Postgres.

Roleplay / call / training metrics are intentionally absent until those features
persist per-agent results — the UI shows honest placeholders rather than fakes.
"""

import csv
import io
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from core.db import session_scope
from core.models import (
    COMPANY_ACTIVE,
    ROLE_SALES_AGENT,
    ChatSession,
    Company,
    PitchConversation,
    RoleplaySession,
    User,
)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def company_analytics(company_id: str, days: int = 14) -> dict:
    days = max(1, min(90, int(days or 14)))
    since = datetime.utcnow() - timedelta(days=days - 1)
    since_day = since.date()

    with session_scope() as db:
        agents = db.scalars(
            select(User).where(User.company_id == company_id, User.role == ROLE_SALES_AGENT)
        ).all()
        pitches = db.execute(
            select(PitchConversation.user_id, PitchConversation.created_at)
            .where(PitchConversation.company_id == company_id)
        ).all()
        chats = db.execute(
            select(ChatSession.user_id, ChatSession.created_at)
            .where(ChatSession.company_id == company_id)
        ).all()
        roleplays = db.execute(
            select(RoleplaySession.user_id, RoleplaySession.score)
            .where(RoleplaySession.company_id == company_id)
        ).all()

        per_agent = {a.id: {
            "id": a.id, "name": a.name, "email": a.email, "status": a.status,
            "last_login_at": _iso(a.last_login_at), "pitches": 0, "chats": 0,
            "roleplays": 0, "_ssum": 0, "_sn": 0,
        } for a in agents}
        active_agents = sum(1 for a in agents if a.status == "active")

    for uid, _ in pitches:
        if uid in per_agent:
            per_agent[uid]["pitches"] += 1
    for uid, _ in chats:
        if uid in per_agent:
            per_agent[uid]["chats"] += 1
    score_sum, score_n = 0, 0
    for uid, sc in roleplays:
        if uid in per_agent:
            per_agent[uid]["roleplays"] += 1
            if sc is not None:
                per_agent[uid]["_ssum"] += sc
                per_agent[uid]["_sn"] += 1
        if sc is not None:
            score_sum += sc
            score_n += 1
    for a in per_agent.values():
        n = a.pop("_sn", 0)
        s = a.pop("_ssum", 0)
        a["avg_score"] = round(s / n) if n else None

    # Daily activity buckets for the window.
    labels = [(since_day + timedelta(days=i)) for i in range(days)]
    buckets = {d: {"pitches": 0, "chats": 0} for d in labels}
    for _, ts in pitches:
        if ts and ts.date() in buckets:
            buckets[ts.date()]["pitches"] += 1
    for _, ts in chats:
        if ts and ts.date() in buckets:
            buckets[ts.date()]["chats"] += 1
    timeseries = [{
        "date": d.isoformat(),
        "label": d.strftime("%d %b"),
        "pitches": buckets[d]["pitches"],
        "chats": buckets[d]["chats"],
    } for d in labels]

    agent_rows = sorted(
        per_agent.values(), key=lambda a: (a["pitches"] + a["chats"] + a["roleplays"]), reverse=True
    )

    return {
        "totals": {
            "agents": len(agents),
            "active_agents": active_agents,
            "pitches": len(pitches),
            "chats": len(chats),
            "roleplays": len(roleplays),
            "avg_score": round(score_sum / score_n) if score_n else None,
        },
        "timeseries": timeseries,
        "agents": agent_rows,
        "window_days": days,
    }


def platform_analytics(days: int = 14) -> dict:
    """Cross-company analytics for the Super Admin console."""
    days = max(1, min(90, int(days or 14)))
    since_day = (datetime.utcnow() - timedelta(days=days - 1)).date()

    with session_scope() as db:
        companies = db.scalars(select(Company)).all()
        users = db.execute(select(User.company_id, User.role)).all()
        pitches = db.execute(select(PitchConversation.company_id, PitchConversation.created_at)).all()
        chats = db.execute(select(ChatSession.company_id, ChatSession.created_at)).all()
        roleplays = db.execute(select(RoleplaySession.company_id, RoleplaySession.score)).all()
        comp_rows = [{"id": c.id, "name": c.name, "slug": c.slug, "status": c.status,
                      "agents": 0, "pitches": 0, "chats": 0, "roleplays": 0} for c in companies]
        active_companies = sum(1 for c in companies if c.status == COMPANY_ACTIVE)

    by_id = {c["id"]: c for c in comp_rows}
    total_agents = 0
    for cid, role in users:
        if role == ROLE_SALES_AGENT:
            total_agents += 1
            if cid in by_id:
                by_id[cid]["agents"] += 1
    for cid, _ in pitches:
        if cid in by_id:
            by_id[cid]["pitches"] += 1
    for cid, _ in chats:
        if cid in by_id:
            by_id[cid]["chats"] += 1
    rp_sum, rp_n = 0, 0
    for cid, sc in roleplays:
        if cid in by_id:
            by_id[cid]["roleplays"] += 1
        if sc is not None:
            rp_sum += sc
            rp_n += 1

    labels = [(since_day + timedelta(days=i)) for i in range(days)]
    buckets = {d: {"pitches": 0, "chats": 0} for d in labels}
    for _, ts in pitches:
        if ts and ts.date() in buckets:
            buckets[ts.date()]["pitches"] += 1
    for _, ts in chats:
        if ts and ts.date() in buckets:
            buckets[ts.date()]["chats"] += 1
    timeseries = [{"date": d.isoformat(), "label": d.strftime("%d %b"),
                   "pitches": buckets[d]["pitches"], "chats": buckets[d]["chats"]} for d in labels]

    comp_rows.sort(key=lambda c: (c["pitches"] + c["chats"] + c["roleplays"]), reverse=True)
    return {
        "totals": {
            "companies": len(companies), "active_companies": active_companies,
            "agents": total_agents, "pitches": len(pitches), "chats": len(chats),
            "roleplays": len(roleplays), "avg_score": round(rp_sum / rp_n) if rp_n else None,
        },
        "timeseries": timeseries,
        "companies": comp_rows,
        "window_days": days,
    }


# ── Exports ───────────────────────────────────────────────────────────────────
_EXPORT_HEADERS = ["Name", "Email", "Status", "Pitches", "Chats", "Roleplays", "Avg score", "Last sign-in"]


def _agent_export_rows(company_id: str) -> list[list]:
    data = company_analytics(company_id, days=30)
    rows = []
    for a in data["agents"]:
        rows.append([
            a["name"], a["email"], a["status"], a["pitches"], a["chats"],
            a.get("roleplays", 0),
            (a.get("avg_score") if a.get("avg_score") is not None else "—"),
            (a["last_login_at"] or "")[:19].replace("T", " "),
        ])
    return rows


def agents_csv(company_id: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_EXPORT_HEADERS)
    w.writerows(_agent_export_rows(company_id))
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel reads UTF-8 correctly


def agents_pdf(company_id: str, company_name: str = "Company") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=18 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm, title="Agent report")
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#0a1628")
    red = colors.HexColor("#e31837")

    title = Paragraph(f'<font color="#0a1628"><b>Posiview Edge</b></font> &nbsp; — Agent report', styles["Title"])
    sub = Paragraph(
        f'<font color="#5b6479">{company_name} · generated {datetime.utcnow().strftime("%d %b %Y %H:%M UTC")}</font>',
        styles["Normal"])

    data = [_EXPORT_HEADERS] + _agent_export_rows(company_id)
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fc")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e8edf5")),
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, red),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))

    doc.build([title, sub, Spacer(1, 10 * mm), table])
    return buf.getvalue()


def agents_xlsx(company_id: str, company_name: str = "Company") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Agents"
    header_fill = PatternFill("solid", fgColor="0A1628")
    header_font = Font(color="FFFFFF", bold=True)
    ws.append(_EXPORT_HEADERS)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
    for row in _agent_export_rows(company_id):
        ws.append(row)
    widths = [24, 28, 11, 9, 8, 10, 10, 18]
    for i, wd in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = wd
    ws.freeze_panes = "A2"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
