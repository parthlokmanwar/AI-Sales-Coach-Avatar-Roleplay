"""
FEATURE: Pitch History — ChatGPT-style continuing conversations.

Now TENANT-SCOPED (Phase 1b): every endpoint resolves the caller's TenantScope
and reads/writes only the conversations they may access — a sales agent sees
only their own, a company admin sees the whole company. Storage moved from the
global pitch_history.json to the per-tenant DB (see core/activity.py).

Endpoints:
  GET    /api/pitch/history                  — list conversation previews (scoped)
  GET    /api/pitch/history/{conv_id}        — full conversation
  POST   /api/pitch/history/{conv_id}/rename — rename
  POST   /api/pitch/history/{conv_id}/continue — append a user msg + AI reply
  DELETE /api/pitch/history                  — clear all (scoped)
  DELETE /api/pitch/history/{conv_id}        — delete one

AI logic: app.py → EnhancedRAGv4.ask_with_messages() (continue endpoint).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import core.deps as deps
from core.deps import iso_now, conversation_preview, generate_default_name
from core import activity
from core.rbac import tenant_scope
from core.schemas import RenameRequest, ContinueRequest

router = APIRouter()


# ── LIST ─────────────────────────────────────────────────────────────────────
@router.get("/api/pitch/history")
async def get_pitch_history(scope=Depends(tenant_scope)):
    """Return the caller's conversations (most recent first) as lightweight previews."""
    try:
        history = activity.pitch_list(scope)  # already sorted updated_at desc
        preview = [{
            "id": c.get("id"),
            "name": c.get("name"),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "metadata": c.get("metadata", {}),
            "preview": conversation_preview(c),
            "message_count": len(c.get("messages", [])),
        } for c in history]
        return JSONResponse(content={"success": True, "history": preview, "total": len(history)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET ONE ──────────────────────────────────────────────────────────────────
@router.get("/api/pitch/history/{conv_id}")
async def get_conversation(conv_id: str, scope=Depends(tenant_scope)):
    """Return the full conversation entry (all messages + pitch_data)."""
    conv = activity.pitch_get(scope, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return JSONResponse(content={"success": True, "conversation": conv})


# ── RENAME ───────────────────────────────────────────────────────────────────
@router.post("/api/pitch/history/{conv_id}/rename")
async def rename_conversation(conv_id: str, body: RenameRequest, scope=Depends(tenant_scope)):
    """Rename a conversation. Empty name falls back to the auto-generated name."""
    conv = activity.pitch_get(scope, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    new_name = (body.name or "").strip()
    if not new_name:
        new_name = generate_default_name(conv.get("metadata", {}), conv.get("created_at", iso_now()))
    conv["name"] = new_name
    conv["updated_at"] = iso_now()
    activity.pitch_save(scope, conv)
    return JSONResponse(content={"success": True, "conversation": conv})


# ── CONTINUE (append user msg, generate AI reply) ────────────────────────────
@router.post("/api/pitch/history/{conv_id}/continue")
async def continue_conversation(conv_id: str, body: ContinueRequest, scope=Depends(tenant_scope)):
    """Append a user message and reply with an AI assistant message."""
    try:
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="Message content is required")

        conv = activity.pitch_get(scope, conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        ts_user = iso_now()
        conv["messages"].append({
            "role": body.role or "user",
            "content": body.content.strip(),
            "type": body.type or "chat",
            "timestamp": ts_user,
        })

        # Build pitch context blob from the stored pitch_data
        pitch = conv.get("pitch_data") or {}
        pitch_sections = []
        if pitch.get("opening_hook"):
            pitch_sections.append(f"OPENING HOOK: {pitch['opening_hook']}")
        if pitch.get("key_product_facts"):
            facts = pitch["key_product_facts"]
            facts_text = "\n".join(f"- {f}" for f in facts if f) if isinstance(facts, list) else str(facts)
            pitch_sections.append(f"KEY FACTS:\n{facts_text}")
        if pitch.get("objection_handling"):
            objs = pitch["objection_handling"]
            if isinstance(objs, list):
                obj_text = "\n".join(
                    f"- {o.get('objection','')}: {o.get('response','')}"
                    for o in objs if isinstance(o, dict)
                )
                pitch_sections.append(f"OBJECTIONS:\n{obj_text}")
        if pitch.get("narrative_summary"):
            pitch_sections.append(f"FULL SCRIPT: {pitch['narrative_summary']}")
        pitch_context = "\n\n".join(pitch_sections)

        # Hand off to RAG with the conversation's own message history
        loop = asyncio.get_event_loop()
        rag = deps.get_rag(scope.company_id)
        rag_result = await loop.run_in_executor(
            None,
            lambda: rag.ask_with_messages(
                question=body.content.strip(),
                messages=conv["messages"][:-1],  # exclude the message we just appended
                pitch_context=pitch_context,
            ),
        )

        answer = rag_result.get("answer", "")
        ts_ai = iso_now()
        assistant_msg = {
            "role": "assistant",
            "content": answer,
            "type": "chat",
            "timestamp": ts_ai,
            "sources": rag_result.get("sources", []),
        }
        conv["messages"].append(assistant_msg)
        conv["updated_at"] = ts_ai
        activity.pitch_save(scope, conv)

        return JSONResponse(content={
            "success": rag_result.get("success", True),
            "message": assistant_msg,
            "conversation": conv,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── DELETE ───────────────────────────────────────────────────────────────────
@router.delete("/api/pitch/history")
async def clear_pitch_history(scope=Depends(tenant_scope)):
    """Clear all of the caller's pitch history."""
    try:
        n = activity.pitch_clear(scope)
        return JSONResponse(content={"success": True, "message": f"Cleared {n} conversations"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/pitch/history/{conv_id}")
async def delete_pitch(conv_id: str, scope=Depends(tenant_scope)):
    """Delete a specific conversation from history."""
    if not activity.pitch_delete(scope, conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return JSONResponse(content={"success": True, "message": "Conversation deleted"})
