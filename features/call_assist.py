"""
FEATURE: Call Assistant — real-time agent co-pilot via Exotel.

Architecture (two WebSockets per call):

  WS-IN  /ws/exotel/{call_sid}
    Receive-only.  Exotel's Stream Applet connects here and sends JSON events:
    connected | start | media (base64 μ-law PCM, 8 kHz) | dtmf | stop.
    Pipeline per utterance:
      decode base64 → UtteranceBuffer (VAD) → Whisper STT → RAG retrieval
      → call-assist prompt → Ollama LLM → push to agent queue.

  WS-OUT  /ws/call-assist/{call_sid}
    Push-only to the agent's browser.  Reads from an asyncio.Queue and sends:
      {"type":"suggestion","transcript":"...","suggestion":"...","key_facts":"...","sources":[...]}

Session state is held in the module-level _sessions dict, keyed by call_sid.
Both WebSocket handlers look up the same session so they share the queue.

HTTP endpoints (for ops / monitoring):
  GET  /api/call-assist/sessions          — list active sessions
  POST /api/call-assist/sessions/{call_sid}/end  — force-end a session

Configuration (all env vars have defaults):
  CALL_ASSIST_MODEL        Ollama model for suggestions (default: qwen2.5:14b)
  CALL_ASSIST_RAG_TOP_K    RAG chunks to retrieve per utterance (default: 4)
  VAD_*                    see core/call_assist_vad.py for VAD tuning vars
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import core.deps as deps
from core.deps import log, detect_language
from core.exotel_stream import (
    parse as parse_exotel,
    ConnectedEvent, StartEvent, MediaEvent, StopEvent,
)
from core.call_assist_vad import UtteranceBuffer
from prompts.call_assist import build_call_assist_prompt


# ── Config ────────────────────────────────────────────────────────────────────

_CALL_ASSIST_MODEL = os.environ.get("CALL_ASSIST_MODEL", "qwen2.5:14b")
_RAG_TOP_K         = int(os.environ.get("CALL_ASSIST_RAG_TOP_K", "4"))
_OLLAMA_URL        = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Maximum history turns kept per session (each turn = 2 lines: Customer + Suggestion)
_MAX_HISTORY_TURNS = 4


# ── Session dataclass ─────────────────────────────────────────────────────────

@dataclass
class CallSession:
    call_sid:   str = ""
    stream_sid: str = ""
    # Audio encoding from the Exotel start event
    encoding:    str = "audio/x-mulaw"
    sample_rate: int = 8000
    # VAD buffer — one per session
    vad: UtteranceBuffer = field(default_factory=UtteranceBuffer)
    # Queue from WS-IN pipeline → WS-OUT agent browser
    agent_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=30))
    # Recent turns for prompt context (list of "Customer: ..." / "Suggestion: ..." lines)
    history: list[str] = field(default_factory=list)
    # Track whether pipeline is currently running (prevent overlapping Whisper calls)
    pipeline_running: bool = False
    # Timestamp of last received media event (for idle cleanup)
    last_media_ts: float = field(default_factory=time.time)
    # Call-level stats
    utterances_processed: int = 0


# Module-level session registry — keyed by call_sid
_sessions: Dict[str, CallSession] = {}

router = APIRouter()


# ── Session helpers ───────────────────────────────────────────────────────────

def _get_or_create_session(call_sid: str) -> CallSession:
    if call_sid not in _sessions:
        _sessions[call_sid] = CallSession(call_sid=call_sid)
        log.info(f"[CALL-ASSIST] New session: call_sid={call_sid}")
    return _sessions[call_sid]


def _drop_session(call_sid: str):
    session = _sessions.pop(call_sid, None)
    if session:
        log.info(
            f"[CALL-ASSIST] Session dropped: call_sid={call_sid} "
            f"utterances={session.utterances_processed}"
        )


# ── LLM call (Ollama, non-streaming, async) ───────────────────────────────────

async def _ollama_suggest(prompt: str) -> str:
    """
    Call Ollama for a call-assist suggestion.  Non-streaming, short output.
    Returns empty string on any failure — callers substitute a graceful default.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{_OLLAMA_URL}/api/generate",
                json={
                    "model": _CALL_ASSIST_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 160,  # 2-3 short sentences + KEY FACTS line
                        "top_k": 10,
                    },
                },
            )
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as exc:
        log.warning(f"[CALL-ASSIST] Ollama call failed: {exc}")
        return ""


# ── Output parser ─────────────────────────────────────────────────────────────

def _split_suggestion(raw: str) -> tuple[str, str]:
    """
    Split LLM output into (suggestion_text, key_facts_string).

    Expected format:
        <2-3 sentence suggestion>

        KEY FACTS: fact1, fact2, fact3

    Returns ("", "none") if raw is empty.
    """
    if not raw.strip():
        return "Not in documents — please confirm with the branch or product team.", "none"

    suggestion_lines: list[str] = []
    key_facts = "none"
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if re.match(r"^KEY\s+FACTS\s*:", stripped, re.IGNORECASE):
            key_facts = stripped.split(":", 1)[1].strip() or "none"
        elif stripped:
            suggestion_lines.append(stripped)

    suggestion = " ".join(suggestion_lines).strip()
    if not suggestion:
        suggestion = "Not in documents — please confirm with the branch or product team."
    return suggestion, key_facts


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def _run_pipeline(session: CallSession, wav_bytes: bytes, duration_s: float):
    """
    Run one transcribe → RAG → LLM → push cycle for a completed utterance.
    Runs as an asyncio task so the WS-IN receive loop is never blocked.
    """
    if session.pipeline_running:
        # Previous utterance still processing — queue the WAV and skip.
        # (Very fast speech; rare in practice.)
        log.debug(f"[CALL-ASSIST] Pipeline busy for {session.call_sid} — utterance dropped")
        return

    session.pipeline_running = True
    t0 = time.time()

    try:
        # 1. Whisper STT (CPU-bound — thread executor)
        from core.whisper_stt import get_engine as get_whisper
        engine = get_whisper()
        loop   = asyncio.get_event_loop()

        try:
            stt_result = await loop.run_in_executor(
                None,
                lambda: engine.transcribe(wav_bytes, content_type="audio/wav"),
            )
        except ValueError as exc:
            log.warning(f"[CALL-ASSIST] Whisper skip ({exc})")
            return

        transcript = (stt_result.get("transcript") or "").strip()
        if not transcript:
            log.debug(f"[CALL-ASSIST] Empty transcript — skipping")
            return

        lang         = detect_language(transcript)
        stt_lang     = stt_result.get("language", "?")
        stt_conf     = stt_result.get("confidence", 0)
        stt_ms       = stt_result.get("ms", 0)

        log.info(
            f"[CALL-ASSIST] Whisper: '{transcript[:70]}' "
            f"lang={stt_lang} conf={stt_conf:.2f} in {stt_ms}ms"
        )

        # 2. RAG retrieval (CPU-bound — thread executor)
        if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
            context = ""
            sources: list[str] = []
            log.warning("[CALL-ASSIST] No documents loaded — suggestion will be off-topic")
        else:
            context, sources = await loop.run_in_executor(
                None,
                lambda: deps.rag_instance._retrieve_relevant_context(
                    deps.rag_instance._expand_query(transcript),
                    top_k=_RAG_TOP_K,
                ),
            )

        # 3. Build prompt
        recent_turns = "\n".join(session.history[-(_MAX_HISTORY_TURNS * 2):])
        prompt = build_call_assist_prompt(
            transcript=transcript,
            context=context or "",
            recent_turns=recent_turns,
            language=lang,
        )

        # 4. LLM suggestion
        raw_output = await _ollama_suggest(prompt)
        suggestion, key_facts = _split_suggestion(raw_output)

        elapsed_ms = int((time.time() - t0) * 1000)
        log.info(
            f"[CALL-ASSIST] Suggestion ready in {elapsed_ms}ms: "
            f"'{suggestion[:60]}' | facts='{key_facts}'"
        )

        # 5. Update session history (rolling window)
        session.history.append(f"Customer: {transcript}")
        session.history.append(f"Suggestion: {suggestion}")
        if len(session.history) > _MAX_HISTORY_TURNS * 2:
            session.history = session.history[-(_MAX_HISTORY_TURNS * 2):]
        session.utterances_processed += 1

        # 6. Push to agent queue (non-blocking put; drop if full)
        payload = {
            "type":        "suggestion",
            "call_sid":    session.call_sid,
            "transcript":  transcript,
            "suggestion":  suggestion,
            "key_facts":   key_facts,
            "sources":     sources,
            "language":    lang,
            "duration_s":  duration_s,
            "stt_ms":      stt_ms,
            "total_ms":    elapsed_ms,
        }
        try:
            session.agent_queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning(f"[CALL-ASSIST] Agent queue full for {session.call_sid} — dropped")

    finally:
        session.pipeline_running = False


# ── WS-IN: Exotel → backend ───────────────────────────────────────────────────

@router.websocket("/ws/exotel/{call_sid}")
async def exotel_stream_in(websocket: WebSocket, call_sid: str):
    """
    Exotel Stream Applet connects here. Receive-only WebSocket.

    Exotel events handled:
      connected — log handshake
      start     — capture stream_sid, encoding, sample_rate
      media     — decode base64 → VAD → pipeline when utterance complete
      stop      — flush VAD → final pipeline → close session
      dtmf      — logged, not processed
    """
    await websocket.accept()
    log.info(f"[CALL-ASSIST/IN] Exotel connected: call_sid={call_sid}")

    session  = _get_or_create_session(call_sid)
    encoding = session.encoding  # may be updated on start event

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # No media for 30s — Exotel may have stalled; keep alive
                log.debug(f"[CALL-ASSIST/IN] 30s no data for {call_sid}")
                continue

            event = parse_exotel(raw)
            if event is None:
                continue

            if isinstance(event, ConnectedEvent):
                log.info(
                    f"[CALL-ASSIST/IN] handshake: protocol={event.protocol} "
                    f"version={event.version}"
                )

            elif isinstance(event, StartEvent):
                session.stream_sid  = event.stream_sid
                session.call_sid    = event.call_sid or call_sid
                session.encoding    = event.media_format.encoding
                session.sample_rate = event.media_format.sample_rate
                encoding            = session.encoding
                session.vad         = UtteranceBuffer(sample_rate=session.sample_rate)
                log.info(
                    f"[CALL-ASSIST/IN] start: stream={event.stream_sid} "
                    f"encoding={session.encoding} rate={session.sample_rate} "
                    f"custom={event.custom_parameters}"
                )
                # Push a "call started" notification to the agent panel
                try:
                    session.agent_queue.put_nowait({
                        "type":     "call_start",
                        "call_sid": session.call_sid,
                        "stream_sid": event.stream_sid,
                        "custom_parameters": event.custom_parameters,
                    })
                except asyncio.QueueFull:
                    pass

            elif isinstance(event, MediaEvent):
                # Only process inbound (customer) track
                if event.track != "inbound":
                    continue
                session.last_media_ts = time.time()

                utterance = session.vad.feed(event.payload, encoding=encoding)
                if utterance:
                    asyncio.create_task(
                        _run_pipeline(session, utterance["wav"], utterance["duration_s"])
                    )

            elif isinstance(event, StopEvent):
                log.info(f"[CALL-ASSIST/IN] stop event: call_sid={call_sid}")
                # Flush any partial utterance
                leftover = session.vad.flush(encoding=encoding)
                if leftover:
                    await _run_pipeline(session, leftover["wav"], leftover["duration_s"])
                break

    except WebSocketDisconnect:
        log.info(f"[CALL-ASSIST/IN] Exotel disconnected: call_sid={call_sid}")
    except Exception as exc:
        log.error(f"[CALL-ASSIST/IN] Error for {call_sid}: {exc}", exc_info=True)
    finally:
        # Signal agent panel that the call ended
        try:
            session.agent_queue.put_nowait({"type": "call_end", "call_sid": call_sid})
        except asyncio.QueueFull:
            pass
        # Short grace period so WS-OUT can drain the queue before we drop the session
        await asyncio.sleep(2)
        _drop_session(call_sid)


# ── WS-OUT: backend → agent browser ──────────────────────────────────────────

@router.websocket("/ws/call-assist/{call_sid}")
async def call_assist_out(websocket: WebSocket, call_sid: str):
    """
    Agent browser connects here.  Push-only — backend sends suggestions.

    Messages sent to the browser:
      {"type":"call_start", "call_sid":"...", "stream_sid":"...", "custom_parameters":{}}
      {"type":"suggestion", "call_sid":"...", "transcript":"...", "suggestion":"...",
       "key_facts":"...", "sources":[...], "language":"...", "stt_ms":120, "total_ms":800}
      {"type":"call_end", "call_sid":"..."}
      {"type":"ping"}     — keepalive every 20s if no message from pipeline

    The browser MAY send {"type":"ping"} keepalives; these are silently ignored.
    """
    await websocket.accept()
    log.info(f"[CALL-ASSIST/OUT] Agent connected: call_sid={call_sid}")

    session = _get_or_create_session(call_sid)

    async def _receive_ignore():
        """Drain inbound messages (pings) so the browser WS doesn't stall."""
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    receive_task = asyncio.create_task(_receive_ignore())

    try:
        while True:
            try:
                msg = await asyncio.wait_for(session.agent_queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                # No pipeline output for 20s — send keepalive ping
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
                continue

            try:
                await websocket.send_json(msg)
            except Exception as exc:
                log.warning(f"[CALL-ASSIST/OUT] Send failed for {call_sid}: {exc}")
                break

            # Stop loop after call_end so we don't hang waiting for new messages
            if msg.get("type") == "call_end":
                break

    except WebSocketDisconnect:
        log.info(f"[CALL-ASSIST/OUT] Agent disconnected: call_sid={call_sid}")
    except Exception as exc:
        log.error(f"[CALL-ASSIST/OUT] Error for {call_sid}: {exc}")
    finally:
        receive_task.cancel()


# ── HTTP management endpoints ─────────────────────────────────────────────────

@router.get("/api/call-assist/sessions")
async def list_sessions():
    """List all active call sessions with basic stats."""
    return JSONResponse(content={
        "success": True,
        "count": len(_sessions),
        "sessions": [
            {
                "call_sid":             s.call_sid,
                "stream_sid":           s.stream_sid,
                "encoding":             s.encoding,
                "utterances_processed": s.utterances_processed,
                "pipeline_running":     s.pipeline_running,
                "queue_depth":          s.agent_queue.qsize(),
                "idle_s":               round(time.time() - s.last_media_ts, 1),
            }
            for s in _sessions.values()
        ],
    })


@router.post("/api/call-assist/sessions/{call_sid}/end")
async def end_session(call_sid: str):
    """Force-end a session (e.g. Exotel webhook didn't fire)."""
    if call_sid not in _sessions:
        return JSONResponse(status_code=404, content={
            "success": False, "error": "session not found"
        })
    session = _sessions[call_sid]
    try:
        session.agent_queue.put_nowait({"type": "call_end", "call_sid": call_sid})
    except asyncio.QueueFull:
        pass
    _drop_session(call_sid)
    return JSONResponse(content={"success": True, "dropped": call_sid})
