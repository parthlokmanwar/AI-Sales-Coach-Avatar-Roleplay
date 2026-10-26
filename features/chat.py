"""
FEATURE: Chat Bot (formerly "Ask Me").

Endpoints:
  POST   /api/chat                       — chat turn (creates new session or
                                            continues existing one if session_id sent)
  GET    /api/chat/sessions              — list saved chat sessions (preview shape)
  GET    /api/chat/sessions/{sid}        — full session (all messages)
  POST   /api/chat/sessions/{sid}/rename — rename a session
  DELETE /api/chat/sessions/{sid}        — delete one
  DELETE /api/chat/sessions              — clear all
  GET    /api/chat/history               — legacy in-memory history (kept for back-compat)
  DELETE /api/chat/history               — clear legacy in-memory history

AI logic lives in:
  rag/engine.py → RAGEngine.ask_question() (intent, RAG, post-processing)
  prompts/chat.py → build_chat_prompt() (Arjun persona)

Model: qwen2.5:14b (see rag/engine.py CHAT_MODEL).
Memory: last 10 turns of conversation history per session.

Persistence: chat_history.json (see core.deps.CHAT_HISTORY_FILE).
"""

import re
import json
import uuid
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

import core.deps as deps
from core import demo_cache
from core import activity
from core.deps import (
    iso_now,
    auto_name_chat_session,
    chat_session_preview,
)
from core.rbac import tenant_scope
from core.schemas import ChatRequest, KeyLearningsRequest, RenameRequest

router = APIRouter()


def _seed_rag_history_from_session(session: dict, rag):
    """Mirror the session's prior messages into the engine's conversation_history
    so the Arjun prompt sees the right past turns (last 10).
    Pairs each user message with the next assistant message into one history entry.
    Also rebuilds _conv_entities from the stored user messages so entity accumulation
    survives server restarts and interleaved requests."""
    from rag.analysis import analyze_speech

    rag.conversation_history = []
    msgs = session.get("messages", [])[-20:]  # last 20 messages ≈ 10 turns
    pending_user = None
    for m in msgs:
        if m.get("role") == "user":
            pending_user = m
        elif m.get("role") == "assistant" and pending_user:
            rag.conversation_history.append({
                "query": pending_user.get("content", ""),
                "answer": m.get("content", ""),
                "files": m.get("sources", []),
            })
            pending_user = None
    # Trim to engine's max so it doesn't grow unbounded
    if len(rag.conversation_history) > rag.max_history_length:
        rag.conversation_history = rag.conversation_history[-rag.max_history_length:]

    # Rebuild _conv_entities by replaying entity extraction over stored user messages.
    # This ensures accumulation survives server restarts and multi-process uvicorn workers.
    rag._conv_entities = {}
    for m in msgs:
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if not content:
            continue
        current = analyze_speech(content).get("key_entities", {})
        for key, val in current.items():
            if key == "loan_amount_raw" and "loan_amount" in rag._conv_entities:
                continue  # named-unit amount wins over bare digits
            if key == "loan_amount" and "loan_amount_raw" in rag._conv_entities:
                rag._conv_entities.pop("loan_amount_raw", None)
            rag._conv_entities[key] = val


# ── POST /api/chat/stream ────────────────────────────────────────────────────

@router.post("/api/chat/stream")
async def ask_question_stream(request: ChatRequest, scope=Depends(tenant_scope)):
    """
    Streaming chat endpoint. Returns Server-Sent Events so the first token
    appears in ~0.5s instead of waiting for the full response.

    SSE event types:
      {"type":"token",  "text":"<partial>"}
      {"type":"done",   "answer":"...", "sources":[...], "session_id":"...",
                        "session_name":"...", "success":true, "ms":N}
      {"type":"error",  "detail":"..."}
    """
    question = (request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    def _sync_stream():
        t0 = time.perf_counter()
        rag = deps.get_rag(scope.company_id)

        if not (rag.structured_data or rag.documents):
            yield _sse({"type": "error", "detail": "No documents loaded for your organization. Ask your admin to upload documents."})
            return

        # ── Session setup ────────────────────────────────────────────────
        session = None
        is_new = False
        if request.session_id:
            session = activity.chat_get(scope, request.session_id)
            if not session:
                deps.log.warning("[CHAT/STREAM] session_id=%s not found — new session", request.session_id)

        if session is None:
            ts = iso_now()
            session = {
                "id": str(uuid.uuid4()),
                "name": auto_name_chat_session(question),
                "created_at": ts,
                "updated_at": ts,
                "messages": [],
            }
            is_new = True

        _seed_rag_history_from_session(session, rag)
        session["messages"].append({
            "role": "user", "content": question, "type": "chat", "timestamp": iso_now(),
        })

        # ── Demo cache pre-check ─────────────────────────────────────────
        # Voice skips the cache: its canned answers are long, markdown-formatted
        # dumps meant for reading, not for Eva to speak aloud.
        cache_hit = None if request.voice else demo_cache.lookup(question)
        if cache_hit:
            deps.log.info("[CHAT/STREAM-CACHE] hit sim=%s", cache_hit.get("similarity"))
            full_answer = cache_hit["answer"]
            sources = cache_hit["sources"]
            yield _sse({"type": "token", "text": full_answer})
        else:
            # ── Full pipeline via prepare_chat + streaming LLM ───────────
            from core.llm import call_llm_stream
            from rag.post_process import post_process_chat

            try:
                prepared = rag.prepare_chat(question, request.language or "English", voice=request.voice)
            except Exception as e:
                yield _sse({"type": "error", "detail": f"Pipeline error: {e}"})
                return

            sources = prepared["sources"]
            full_tokens: list = []
            try:
                for tok in call_llm_stream(
                    prepared["prompt"],
                    model_role=prepared["model_role"],
                    temperature=prepared["temperature"],
                    max_tokens=prepared["max_tokens"],
                ):
                    full_tokens.append(tok)
                    yield _sse({"type": "token", "text": tok})
            except Exception as e:
                yield _sse({"type": "error", "detail": f"LLM error: {e}"})
                return

            full_answer = "".join(full_tokens)
            full_answer = re.sub(r"<think>.*?</think>", "", full_answer, flags=re.DOTALL).strip()
            if not request.voice:
                # Voice answers are already plain spoken text; markdown post-processing
                # (list/bold reformatting) would only re-introduce read-aloud clutter.
                full_answer = post_process_chat(full_answer, user_asked_for_list_flag=rag._user_asked_for_list(question))

        # ── Save to history + session ────────────────────────────────────
        rag._add_to_history(question, full_answer, sources)
        ts_ai = iso_now()
        session["messages"].append({
            "role": "assistant", "content": full_answer, "type": "chat",
            "timestamp": ts_ai, "sources": sources,
        })
        session["updated_at"] = ts_ai
        activity.chat_save(scope, session)

        ms = int((time.perf_counter() - t0) * 1000)
        yield _sse({
            "type": "done",
            "success": True,
            "answer": full_answer,
            "sources": sources,
            "session_id": session["id"],
            "session_name": session["name"],
            "session_created": is_new,
            "message_count": len(session["messages"]),
            "ms": ms,
        })

    return StreamingResponse(
        _sync_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── POST /api/chat/sessions/{session_id}/key-learnings ──────────────────────

@router.post("/api/chat/sessions/{session_id}/key-learnings")
async def key_learnings(session_id: str, request: KeyLearningsRequest, scope=Depends(tenant_scope)):
    """
    Analyze the FULL stored session and return a coaching debrief for the agent
    (Key Learnings button). Generated per session by the LLM via
    prompts/chat.build_key_learnings_prompt — no canned content.
    """
    from core.llm import call_llm
    from prompts.chat import build_key_learnings_prompt, _NO_LEARNINGS

    session = activity.chat_get(scope, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    msgs = session.get("messages", [])
    lines = []
    for m in msgs:
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        role = "Agent" if m.get("role") == "user" else "Arjun"
        lines.append(f"{role}: {content}")
    transcript = "\n".join(lines)

    if not transcript:
        return {"success": True, "learnings": _NO_LEARNINGS, "session_id": session_id}

    # Long sessions: keep the newest turns — they carry the live deal state.
    if len(transcript) > 12000:
        transcript = "…(earlier turns omitted)…\n" + transcript[-12000:]

    rag = deps.get_rag(scope.company_id)

    # Entities accumulated across the whole session (restart-safe replay).
    _seed_rag_history_from_session(session, rag)
    entities = dict(rag._conv_entities)

    # Retrieval context lets the debrief VERIFY figures quoted in the chat
    # (catches misquotes never corrected in-chat). Non-fatal if it fails.
    context = ""
    try:
        user_text = " ".join(m.get("content", "") for m in msgs if m.get("role") == "user")
        if user_text.strip() and (rag.documents or rag.structured_data):
            context, _ = rag._retrieve_relevant_context(user_text[:1000], top_k=6)
    except Exception as e:
        deps.log.warning(f"[KEY-LEARNINGS] verification retrieval failed: {e}")

    prompt = build_key_learnings_prompt(
        transcript=transcript,
        entities=entities,
        language=request.language or "English",
        context=context,
    )

    try:
        raw = call_llm(prompt, model_role="chat", temperature=0.3, max_tokens=1200, timeout=180)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not generate key learnings: {e}")

    learnings = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return {"success": True, "learnings": learnings, "session_id": session_id}


# ── POST /api/chat ───────────────────────────────────────────────────────────

@router.post("/api/chat")
async def ask_question(request: ChatRequest, scope=Depends(tenant_scope)):
    """
    Answer a chat turn, strictly grounded in loaded documents.

    Behaviour:
      - If session_id is provided and exists → append to that session
      - Otherwise → create a new session named from the first user question

    Always returns the session_id so the frontend can pin the conversation.
    """
    try:
        question = (request.question or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question cannot be empty.")

        rag = deps.get_rag(scope.company_id)
        if not (rag.structured_data or rag.documents):
            return JSONResponse(content={
                "success": False,
                "answer": "No documents are loaded for your organization. Ask your admin to upload documents.",
                "sources": [],
            })

        # ── Load or create session ─────────────────────────────────────────
        session = None
        is_new = False

        if request.session_id:
            session = activity.chat_get(scope, request.session_id)
            if not session:
                # Bad session_id from frontend — fall through to new session
                deps.log.warning(f"[CHAT] session_id={request.session_id} not found — starting new session")

        if session is None:
            ts = iso_now()
            session = {
                "id": str(uuid.uuid4()),
                "name": auto_name_chat_session(question),
                "created_at": ts,
                "updated_at": ts,
                "messages": [],
            }
            is_new = True

        # ── Seed rag.conversation_history from this session's past turns ───
        # (so the Arjun prompt's "last 10 turns" reflects THIS session, not whatever
        # the previous request left in memory)
        _seed_rag_history_from_session(session, rag)

        # ── Append user message ────────────────────────────────────────────
        ts_user = iso_now()
        session["messages"].append({
            "role": "user",
            "content": question,
            "type": "chat",
            "timestamp": ts_user,
        })

        # ── Demo cache pre-check (same vetted answers the voice path uses) ──
        # Saves a full RAG + qwen2.5:14b round-trip (~1.5–3s) on common Qs
        # like EMI, processing fee, docs, eligibility, interest rate.
        cache_hit = demo_cache.lookup(question)
        if cache_hit:
            deps.log.info(
                f"[CHAT-CACHE] hit sim={cache_hit['similarity']} "
                f"kw={cache_hit['matched_keywords']}"
            )
            result = {
                "success": True,
                "answer": cache_hit["answer"],
                "sources": cache_hit["sources"],
                "intent": "demo-cache",
                "engine": "demo-cache",
                "cache_similarity": cache_hit["similarity"],
            }
        else:
            # ── Run RAG ─────────────────────────────────────────────────────
            result = rag.ask_question(question=question, language=request.language or "English")

        # Replaced by native LLM generation in {language} — see language directive in prompts/pitch.py.
        # ── Translate answer if a non-English language was requested ────────
        # language = (request.language or "English").strip()
        # if language != "English" and result.get("success") and result.get("answer"):
        #     from deep_translator import GoogleTranslator
        #     from rag.engine import contextual_translate, LANGUAGE_CODES
        #     if language in LANGUAGE_CODES:
        #         translator = GoogleTranslator(source="auto", target=LANGUAGE_CODES[language])
        #         result["answer"] = contextual_translate(result["answer"], translator)

        # ── Append assistant message ────────────────────────────────────────
        ts_ai = iso_now()
        assistant_msg = {
            "role": "assistant",
            "content": result.get("answer", ""),
            "type": "chat",
            "timestamp": ts_ai,
            "sources": result.get("sources", []),
            "intent": result.get("intent"),
        }
        session["messages"].append(assistant_msg)
        session["updated_at"] = ts_ai

        # ── Persist ─────────────────────────────────────────────────────────
        activity.chat_save(scope, session)

        # ── Return ──────────────────────────────────────────────────────────
        return JSONResponse(content={
            **result,
            "session_id": session["id"],
            "session_name": session["name"],
            "session_created": is_new,
            "message_count": len(session["messages"]),
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/chat/sessions (list) ────────────────────────────────────────────

@router.get("/api/chat/sessions")
async def list_chat_sessions(scope=Depends(tenant_scope)):
    """Return the caller's chat sessions (updated_at desc), lightweight preview shape."""
    try:
        sessions = activity.chat_list(scope)
        preview = []
        for s in sessions:
            preview.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "message_count": len(s.get("messages", [])),
                "preview": chat_session_preview(s),
            })
        return JSONResponse(content={"success": True, "sessions": preview, "total": len(sessions)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/chat/sessions/{sid} ─────────────────────────────────────────────

@router.get("/api/chat/sessions/{sid}")
async def get_chat_session(sid: str, scope=Depends(tenant_scope)):
    """Return the full chat session (all messages)."""
    try:
        session = activity.chat_get(scope, sid)
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return JSONResponse(content={"success": True, "session": session})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /api/chat/sessions/{sid}/rename ─────────────────────────────────────

@router.post("/api/chat/sessions/{sid}/rename")
async def rename_chat_session(sid: str, body: RenameRequest, scope=Depends(tenant_scope)):
    """Rename a chat session. Empty name → re-auto-name from first user message."""
    try:
        session = activity.chat_get(scope, sid)
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        new_name = (body.name or "").strip()
        if not new_name:
            first_user = next(
                (m["content"] for m in session.get("messages", []) if m.get("role") == "user"),
                "New chat",
            )
            new_name = auto_name_chat_session(first_user)
        session["name"] = new_name
        session["updated_at"] = iso_now()
        activity.chat_save(scope, session)
        return JSONResponse(content={"success": True, "session": session})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── DELETE /api/chat/sessions/{sid} ──────────────────────────────────────────

@router.delete("/api/chat/sessions/{sid}")
async def delete_chat_session(sid: str, scope=Depends(tenant_scope)):
    """Delete one chat session by id."""
    try:
        if not activity.chat_delete(scope, sid):
            raise HTTPException(status_code=404, detail="Chat session not found")
        return JSONResponse(content={"success": True, "message": "Chat session deleted"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── DELETE /api/chat/sessions (clear all) ────────────────────────────────────

@router.delete("/api/chat/sessions")
async def clear_all_chat_sessions(scope=Depends(tenant_scope)):
    """Wipe the caller's chat sessions."""
    try:
        n = activity.chat_clear(scope)
        return JSONResponse(content={"success": True, "message": f"Cleared {n} chat sessions"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Legacy in-memory chat history (kept for back-compat) ─────────────────────

@router.get("/api/chat/history")
async def get_chat_history_legacy():
    """LEGACY: in-memory conversation history (last 10 turns).
    The new persistent path is /api/chat/sessions."""
    try:
        history = deps.rag_instance.get_chat_history()
        formatted = [
            {"query": h["query"], "answer": h["answer"], "files": h.get("files", [])}
            for h in history
        ]
        return JSONResponse(content={"success": True, "history": formatted, "total": len(formatted)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/chat/history")
async def clear_chat_history_legacy():
    """Clear in-memory conversation history only (doesn't touch saved sessions)."""
    try:
        deps.rag_instance.clear_chat_history()
        return JSONResponse(content={"success": True, "message": "In-memory chat history cleared"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
