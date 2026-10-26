"""
FEATURE: Call Assistant V2 — live Twilio phone-call sales copilot.

A NEW, parallel feature to the v1 Exotel Call Assistant (features/call_assist.py).
v1 is left untouched. V2 places a real Twilio phone call between the sales agent
and the customer, forks the live audio to our backend, transcribes the CUSTOMER
in real time (faster-whisper), grounds a suggestion in our RAG knowledge base,
and streams it to the agent's screen. The agent reads it and speaks — this is a
copilot, NOT an autonomous voice bot.

──────────────────────────────────────────────────────────────────────────────
Call topology (outbound dial → agent's phone):
  POST /api/call-assistant-v2/calls
    → Twilio calls.create(to=AGENT_PHONE, from=TWILIO_NUMBER,
          url=PUBLIC_BASE_URL/api/call-assistant-v2/twiml/{call_id}?customer=…,
          statusCallback=…/status?call_id={call_id})
  Agent answers → Twilio GETs the TwiML:
    <Response>
      <Start><Stream url="wss://PUBLIC/ws/call-assistant-v2/twilio/{call_id}"
                     track="both_tracks">
        <Parameter name="call_id" value="{call_id}"/></Stream></Start>
      <Dial callerId="TWILIO_NUMBER"><Number>{customer}</Number></Dial>
    </Response>
  Twilio opens the media WS and forks both tracks.

Track mapping (Stream is on the AGENT's leg):
  inbound  = agent's mic            → transcript-only (no LLM)
  outbound = customer (via Dial)    → full pipeline (drives suggestions)
Configurable via CALL_ASSIST_V2_CUSTOMER_TRACK (default "outbound").

Pipeline per CUSTOMER utterance (mirrors v1 patterns):
  μ-law chunk → UtteranceBuffer (VAD) → faster-whisper → RAG retrieval
  → call_assistant_v2 prompt → OpenRouter gpt-4o-mini (stream) → agent WS.

WebSockets:
  WS /ws/call-assistant-v2/twilio/{call_id}  — Twilio media fork (receive-only)
  WS /ws/call-assistant-v2/agent/{call_id}   — push to agent browser

Configuration (env, all have defaults):
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER  — Twilio creds
  AGENT_PHONE_NUMBER          — default agent phone (overridable per call)
  PUBLIC_BASE_URL             — public https host of this backend (tunnel)
  CALL_ASSIST_V2_CUSTOMER_TRACK  — "outbound" (default) | "inbound"
  CALL_ASSIST_V2_MODEL        — OpenRouter model (default gpt-4o-mini)
  CALL_ASSIST_V2_RAG_TOP_K    — RAG chunks per utterance (default 4)
  CALL_ASSIST_V2_TRANSCRIBE_AGENT — "true" (default) to transcribe agent track
  TWILIO_VALIDATE_SIGNATURE   — "true" to enforce X-Twilio-Signature (default false)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

import core.deps as deps
from core.deps import log, detect_language
from core.call_assist_vad import UtteranceBuffer
from core.twilio_stream import (
    parse as parse_twilio,
    ConnectedEvent, StartEvent, MediaEvent, MarkEvent, DtmfEvent, StopEvent,
)
from core.emi_calc import (
    is_calculation_question, parse_amount, parse_tenure, compute_emi,
    format_for_prompt as format_emi_for_prompt,
)
from prompts.call_assistant_v2 import (
    CALL_ASSISTANT_V2_SYSTEM, build_call_assistant_v2_user,
)


# ── Config ────────────────────────────────────────────────────────────────────

_MODEL          = os.environ.get("CALL_ASSIST_V2_MODEL", "gpt-4o-mini")
_RAG_TOP_K      = int(os.environ.get("CALL_ASSIST_V2_RAG_TOP_K", "4"))
_CUSTOMER_TRACK = os.environ.get("CALL_ASSIST_V2_CUSTOMER_TRACK", "outbound").lower()
_AGENT_TRACK    = "inbound" if _CUSTOMER_TRACK == "outbound" else "outbound"
_TRANSCRIBE_AGENT = os.environ.get("CALL_ASSIST_V2_TRANSCRIBE_AGENT", "true").lower() == "true"
_VALIDATE_SIG   = os.environ.get("TWILIO_VALIDATE_SIGNATURE", "false").lower() == "true"

_OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://localhost:11434")
_OLLAMA_MODEL   = os.environ.get("CALL_ASSIST_MODEL", os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:14b"))

_MAX_WINDOW     = 8     # rolling transcript turns kept for prompt context


# ── Session state ─────────────────────────────────────────────────────────────

@dataclass
class CallV2Session:
    call_id:         str
    twilio_call_sid: str = ""
    stream_sid:      str = ""
    customer_number: str = ""
    agent_number:    str = ""
    language:        str = "auto"
    status:          str = "created"   # created|dialing|ringing|in-progress|completed|failed
    encoding:        str = "audio/x-mulaw"
    sample_rate:     int = 8000

    # One VAD per track (customer drives the LLM; agent is transcript-only)
    vad_customer: UtteranceBuffer = field(default_factory=UtteranceBuffer)
    vad_agent:    UtteranceBuffer = field(default_factory=UtteranceBuffer)

    # Rolling labeled transcript window [{speaker, text}]
    transcript_window: List[dict] = field(default_factory=list)

    # Active agent browser WS connections (support multiple tabs)
    agent_ws_list: List[WebSocket] = field(default_factory=list)

    # Prevent overlapping customer pipelines on the same session
    pipeline_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    created_at:    float = field(default_factory=time.time)
    last_media_ts: float = field(default_factory=time.time)
    utterances_processed: int = 0


_sessions: Dict[str, CallV2Session] = {}

router = APIRouter()


def _get_or_create_session(call_id: str) -> CallV2Session:
    if call_id not in _sessions:
        _sessions[call_id] = CallV2Session(call_id=call_id)
        log.info(f"[CALL-V2] New session: call_id={call_id}")
    return _sessions[call_id]


def _drop_session(call_id: str):
    s = _sessions.pop(call_id, None)
    if s:
        log.info(f"[CALL-V2] Session dropped: call_id={call_id} utterances={s.utterances_processed}")


# ── Public-URL helpers ────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    """
    Best-effort E.164 normalization, India-default.
      +9198…        → unchanged (already E.164)
      9198765 43210 → +919876543210   (strip spaces/dashes)
      9876543210    → +919876543210   (bare 10-digit Indian mobile)
      09876543210   → +919876543210   (leading 0 trunk prefix)
    Anything already starting with '+' is kept as-is.
    """
    s = (raw or "").strip()
    if s.startswith("+"):
        return "+" + "".join(ch for ch in s[1:] if ch.isdigit())
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:                      # bare Indian mobile
        return "+91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return "+" + digits                        # fallback: assume already has CC


def _public_base() -> str:
    """Public https base used to build TwiML callback URLs. No trailing slash."""
    return os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _ws_base() -> str:
    """Public wss base derived from PUBLIC_BASE_URL."""
    base = _public_base()
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    return base


# ── Twilio REST client (lazy) ─────────────────────────────────────────────────

def _twilio_client():
    """Return an authenticated Twilio REST client, or raise a clear error."""
    sid   = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not (sid and token):
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")
    try:
        from twilio.rest import Client
    except ImportError as e:
        raise RuntimeError("twilio package not installed (pip install twilio)") from e
    return Client(sid, token)


# ── Push helpers (backend → agent browser) ────────────────────────────────────

async def _push_to_agents(session: CallV2Session, payload: dict):
    """Send a JSON payload to all active agent WS connections; prune dead ones."""
    if not session.agent_ws_list:
        return
    dead = []
    for ws in list(session.agent_ws_list):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            session.agent_ws_list.remove(ws)
        except ValueError:
            pass


def _append_to_window(session: CallV2Session, speaker: str, text: str):
    session.transcript_window.append({"speaker": speaker, "text": text})
    if len(session.transcript_window) > _MAX_WINDOW:
        session.transcript_window = session.transcript_window[-_MAX_WINDOW:]


def _labeled_transcript(session: CallV2Session) -> str:
    return "\n".join(f"{t['speaker']}: {t['text']}" for t in session.transcript_window)


# ── Output tail parsing (KEY FACTS / INTENT / NEXT ACTION) ─────────────────────

_TAIL_RE = re.compile(r"^\s*(KEY\s+FACTS|INTENT|NEXT\s+ACTION)\s*:", re.IGNORECASE)


def _tail_started(text: str) -> bool:
    """True once the structured tail begins (so we stop streaming spoken tokens)."""
    for line in text.splitlines():
        if _TAIL_RE.match(line):
            return True
    return False


def _split_output(raw: str) -> dict:
    """
    Split full LLM output into the spoken line + structured tail fields.
    Returns {"suggestion", "key_facts", "intent", "next_action"}.
    """
    spoken_lines: List[str] = []
    key_facts = "none"
    intent = ""
    next_action = ""
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^KEY\s+FACTS\s*:", stripped, re.IGNORECASE):
            key_facts = stripped.split(":", 1)[1].strip() or "none"
        elif re.match(r"^INTENT\s*:", stripped, re.IGNORECASE):
            intent = stripped.split(":", 1)[1].strip()
        elif re.match(r"^NEXT\s+ACTION\s*:", stripped, re.IGNORECASE):
            next_action = stripped.split(":", 1)[1].strip()
        else:
            spoken_lines.append(stripped)
    suggestion = " ".join(spoken_lines).strip()
    if next_action.lower() == "none":
        next_action = ""
    return {
        "suggestion": suggestion,
        "key_facts": key_facts,
        "intent": intent,
        "next_action": next_action,
    }


def _build_emi_block(transcript: str) -> Optional[str]:
    if not is_calculation_question(transcript):
        return None
    amount = parse_amount(transcript)
    tenure = parse_tenure(transcript)
    if amount is None or tenure is None:
        return None
    try:
        return format_emi_for_prompt(compute_emi(amount, tenure, annual_rate_pct=10.5))
    except Exception:
        return None


# ── Customer pipeline: transcribe → RAG → stream suggestion ────────────────────

async def _run_customer_pipeline(session: CallV2Session, wav_bytes: bytes, duration_s: float):
    """Full pipeline for one completed CUSTOMER utterance. Runs as a Task."""
    if session.pipeline_lock.locked():
        log.debug(f"[CALL-V2] {session.call_id} pipeline busy — utterance dropped")
        return

    async with session.pipeline_lock:
        t_start = time.time()
        loop = asyncio.get_event_loop()

        # 1. Whisper STT (CPU/GPU-bound → thread executor)
        # Use the LIVE engine (WHISPER_LIVE_MODEL_SIZE, default 'small') which is
        # pre-warmed at startup — NOT get_engine() (large-v3-turbo) which loads
        # lazily ~10-30s on first call and would drop the opening utterances.
        from core.whisper_stt import get_live_engine as get_whisper
        engine = get_whisper()
        log.info(f"[CALL-V2/DIAG] {session.call_id} STT start: wav_bytes={len(wav_bytes)} "
                 f"vad_dur={duration_s:.2f}s lang={session.language}")
        # ── TEMP DIAGNOSTIC: dump customer utterance WAVs to disk ─────────
        try:
            _dump_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "callv2-audio-dumps")
            os.makedirs(_dump_dir, exist_ok=True)
            _fname = f"{session.call_id}_customer_{int(time.time()*1000)}.wav"
            with open(os.path.join(_dump_dir, _fname), "wb") as _fh:
                _fh.write(wav_bytes)
            log.info(f"[CALL-V2/DIAG] dumped {_fname}")
        except Exception as _de:
            log.warning(f"[CALL-V2/DIAG] wav dump failed: {_de}")
        # Map the UI language toggle to a whisper hint. Hinglish → hi (Devanagari
        # out, the prompt handles it). auto → whisper auto-detect.
        _hint = {"english": "en", "hindi": "hi", "hinglish": "hi", "marathi": "mr"}.get(
            session.language, "auto")
        try:
            stt = await loop.run_in_executor(
                None, lambda: engine.transcribe(
                    wav_bytes, content_type="audio/wav", language_hint=_hint,
                    telephone=True)
            )
        except ValueError as exc:
            log.info(f"[CALL-V2/DIAG] {session.call_id} Whisper SKIP (ValueError): {exc}")
            return
        except Exception as exc:
            log.error(f"[CALL-V2/DIAG] {session.call_id} Whisper ERROR: {exc}", exc_info=True)
            return

        transcript = (stt.get("transcript") or "").strip()
        log.info(f"[CALL-V2/DIAG] {session.call_id} STT result: transcript='{transcript[:80]}' "
                 f"lang={stt.get('language','?')} stt_ms={stt.get('ms',0)}")
        if not transcript:
            log.info(f"[CALL-V2/DIAG] {session.call_id} EMPTY transcript — utterance dropped (silence/too-quiet?)")
            return
        stt_ms   = stt.get("ms", 0)
        stt_lang = stt.get("language", "?")
        log.info(f"[CALL-V2] {session.call_id} customer: '{transcript[:70]}' "
                 f"lang={stt_lang} stt={stt_ms}ms")

        # Push customer transcript turn immediately
        await _push_to_agents(session, {
            "type": "transcript", "speaker": "Customer", "text": transcript, "stt_ms": stt_ms,
        })
        _append_to_window(session, "Customer", transcript)

        # 2. RAG retrieval (+ deterministic EMI) concurrently
        if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
            log.warning("[CALL-V2] no documents loaded — sending wait")
            await _push_to_agents(session, {"type": "wait"})
            return

        t_rag = time.time()
        try:
            (context, sources), emi_block = await asyncio.gather(
                loop.run_in_executor(
                    None,
                    lambda: deps.rag_instance._retrieve_relevant_context(
                        deps.rag_instance._expand_query(transcript), top_k=_RAG_TOP_K
                    ),
                ),
                loop.run_in_executor(None, lambda: _build_emi_block(transcript)),
            )
        except Exception as exc:
            log.error(f"[CALL-V2] RAG failed: {exc}")
            await _push_to_agents(session, {"type": "wait"})
            return
        rag_ms = int((time.time() - t_rag) * 1000)

        # 3. Build prompt
        lang = session.language if session.language in (
            "english", "hinglish", "hindi", "marathi", "auto"
        ) else "auto"
        user_msg = build_call_assistant_v2_user(
            labeled_transcript=_labeled_transcript(session),
            context=context or "",
            language=lang,
            emi_block=emi_block,
            role_hint="The label 'Agent' is the HDFC agent; 'Customer' is the caller.",
        )

        # 4. Stream suggestion (OpenRouter → Ollama fallback)
        session.utterances_processed += 1
        await _stream_suggestion(session, user_msg, sources, t_start, stt_ms, rag_ms)


# ── Agent track: transcribe only (reference, no LLM) ──────────────────────────

async def _run_agent_transcribe(session: CallV2Session, wav_bytes: bytes):
    if not _TRANSCRIBE_AGENT:
        return
    loop = asyncio.get_event_loop()
    from core.whisper_stt import get_live_engine as get_whisper
    engine = get_whisper()
    _hint = {"english": "en", "hindi": "hi", "hinglish": "hi", "marathi": "mr"}.get(
        session.language, "auto")
    try:
        stt = await loop.run_in_executor(
            None, lambda: engine.transcribe(
                wav_bytes, content_type="audio/wav", language_hint=_hint,
                telephone=True)
        )
    except Exception:
        return
    text = (stt.get("transcript") or "").strip()
    if not text:
        return
    log.info(f"[CALL-V2] {session.call_id} agent: '{text[:60]}'")
    await _push_to_agents(session, {
        "type": "transcript", "speaker": "Agent", "text": text, "stt_ms": stt.get("ms", 0),
    })
    _append_to_window(session, "Agent", text)


# ── LLM streaming ─────────────────────────────────────────────────────────────

async def _stream_suggestion(
    session: CallV2Session, user_msg: str, sources: list,
    t_start: float, stt_ms: int, rag_ms: int,
):
    """Stream from OpenRouter gpt-4o-mini; fall back to local Ollama on failure."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        try:
            await _stream_openrouter(session, user_msg, sources, t_start, stt_ms, rag_ms, openrouter_key)
            return
        except Exception as exc:
            log.error(f"[CALL-V2] OpenRouter failed ({exc}); falling back to Ollama")
    await _stream_ollama(session, user_msg, sources, t_start, stt_ms, rag_ms)


async def _emit_stream(session: CallV2Session, token_iter, engine_name: str,
                       sources: list, t_start: float, t_llm: float,
                       stt_ms: int, rag_ms: int):
    """
    Shared consumer for a token async-iterator. Streams the SPOKEN part to the
    agent (stops at the KEY FACTS/INTENT/NEXT ACTION tail), then sends a parsed
    suggestion_done with structured insight fields. Handles WAIT early-exit.
    """
    full = ""
    visible_sent = 0
    first_token_ms: Optional[int] = None
    tail_seen = False

    async for token in token_iter:
        if not token:
            continue
        full += token
        if first_token_ms is None:
            first_token_ms = int((time.time() - t_llm) * 1000)
            if full.strip().upper().startswith("WAIT"):
                log.info(f"[CALL-V2] {session.call_id} WAIT on first token")
                await _push_to_agents(session, {"type": "wait"})
                return
        if not tail_seen and _tail_started(full):
            tail_seen = True
        if not tail_seen:
            # Stream the spoken portion only. Hold back the last 12 chars so a
            # partial "KEY FACTS:" label is never shown then retracted.
            safe = full[:-12] if len(full) > 12 else ""
            if len(safe) > visible_sent:
                delta = safe[visible_sent:]
                visible_sent = len(safe)
                await _push_to_agents(session, {"type": "token", "token": delta})

    parsed = _split_output(full)
    total_ms = int((time.time() - t_start) * 1000)

    if not parsed["suggestion"] or parsed["suggestion"].strip().upper() == "WAIT":
        await _push_to_agents(session, {"type": "wait"})
        return

    log.info(f"[CALL-V2] {session.call_id} suggestion ({engine_name}) "
             f"total={total_ms}ms first_token={first_token_ms}ms: "
             f"'{parsed['suggestion'][:60]}'")

    await _push_to_agents(session, {
        "type":        "suggestion_done",
        "suggestion":  parsed["suggestion"],
        "key_facts":   parsed["key_facts"],
        "intent":      parsed["intent"],
        "next_action": parsed["next_action"],
        "sources":     sources,
        "engine":      engine_name,
        "stt_ms":      stt_ms,
        "rag_ms":      rag_ms,
        "first_token_ms": first_token_ms,
        "total_ms":    total_ms,
    })


async def _stream_openrouter(session, user_msg, sources, t_start, stt_ms, rag_ms, key):
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
    t_llm = time.time()
    stream = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": CALL_ASSISTANT_V2_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        max_tokens=220,
        temperature=0,
        stream=True,
    )

    async def _tokens():
        async for chunk in stream:
            if chunk.choices:
                yield chunk.choices[0].delta.content or ""

    await _emit_stream(session, _tokens(), f"openrouter/{_MODEL}", sources, t_start, t_llm, stt_ms, rag_ms)


async def _stream_ollama(session, user_msg, sources, t_start, stt_ms, rag_ms):
    combined = f"{CALL_ASSISTANT_V2_SYSTEM}\n\n{user_msg}"
    t_llm = time.time()

    async def _tokens():
        async with httpx.AsyncClient(timeout=60) as hx:
            async with hx.stream(
                "POST", f"{_OLLAMA_URL}/api/generate",
                json={"model": _OLLAMA_MODEL, "prompt": combined, "stream": True,
                      "options": {"temperature": 0, "num_predict": 220}},
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("response"):
                        yield data["response"]
                    if data.get("done"):
                        break

    try:
        await _emit_stream(session, _tokens(), f"ollama/{_OLLAMA_MODEL}", sources, t_start, t_llm, stt_ms, rag_ms)
    except Exception as exc:
        log.error(f"[CALL-V2] Ollama stream error: {exc}")
        await _push_to_agents(session, {"type": "wait"})


# ── HTTP: originate / end / list ──────────────────────────────────────────────

@router.post("/api/call-assistant-v2/calls")
async def create_call(request: Request):
    """
    Originate an outbound copilot call.

    Body JSON: {"customer_number": "+91…", "agent_number": "+91…"?, "language": "auto"?}
    Dials the agent's phone; on answer Twilio bridges the customer and forks audio.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    customer = _normalize_phone(body.get("customer_number") or "")
    agent    = _normalize_phone(body.get("agent_number") or os.environ.get("AGENT_PHONE_NUMBER", ""))
    language = (body.get("language") or "auto").strip().lower()
    if not customer:
        return JSONResponse(status_code=400, content={"success": False, "error": "customer_number required"})
    if not agent:
        return JSONResponse(status_code=400, content={
            "success": False, "error": "agent_number required (or set AGENT_PHONE_NUMBER)"})
    if not _public_base():
        return JSONResponse(status_code=400, content={
            "success": False, "error": "PUBLIC_BASE_URL not set — Twilio needs a public callback host"})

    call_id = "callv2-" + uuid.uuid4().hex[:12]
    session = _get_or_create_session(call_id)
    session.customer_number = customer
    session.agent_number    = agent
    session.language        = language
    session.status          = "dialing"

    twiml_url = (f"{_public_base()}/api/call-assistant-v2/twiml/{call_id}"
                 f"?customer={quote(customer, safe='')}")
    status_url = f"{_public_base()}/api/call-assistant-v2/status?call_id={call_id}"

    try:
        client = _twilio_client()
        from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")
        call = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.calls.create(
                to=agent,
                from_=from_number,
                url=twiml_url,
                method="POST",
                status_callback=status_url,
                status_callback_method="POST",
                status_callback_event=["initiated", "ringing", "answered", "completed"],
            ),
        )
        session.twilio_call_sid = call.sid
        log.info(f"[CALL-V2] Originated call_id={call_id} twilio_sid={call.sid} "
                 f"agent={agent} customer={customer}")
    except Exception as exc:
        session.status = "failed"
        log.error(f"[CALL-V2] Originate failed: {exc}")
        return JSONResponse(status_code=502, content={"success": False, "error": str(exc)})

    return JSONResponse(content={
        "success": True, "call_id": call_id, "twilio_sid": session.twilio_call_sid,
        "status": session.status,
    })


@router.post("/api/call-assistant-v2/calls/{call_id}/end")
async def end_call(call_id: str):
    """Hang up the Twilio call and drop the session."""
    session = _sessions.get(call_id)
    if not session:
        return JSONResponse(status_code=404, content={"success": False, "error": "session not found"})
    if session.twilio_call_sid:
        try:
            client = _twilio_client()
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.calls(session.twilio_call_sid).update(status="completed"),
            )
        except Exception as exc:
            log.warning(f"[CALL-V2] hangup failed for {call_id}: {exc}")
    await _push_to_agents(session, {"type": "call_status", "status": "completed"})
    _drop_session(call_id)
    return JSONResponse(content={"success": True, "ended": call_id})


@router.get("/api/call-assistant-v2/calls")
async def list_calls():
    return JSONResponse(content={
        "success": True,
        "count": len(_sessions),
        "calls": [
            {
                "call_id":         s.call_id,
                "twilio_call_sid": s.twilio_call_sid,
                "customer_number": s.customer_number,
                "agent_number":    s.agent_number,
                "status":          s.status,
                "language":        s.language,
                "utterances":      s.utterances_processed,
                "agent_connections": len(s.agent_ws_list),
                "duration_s":      round(time.time() - s.created_at, 1),
            }
            for s in _sessions.values()
        ],
    })


# ── HTTP: TwiML + status callback ─────────────────────────────────────────────

def _maybe_validate_signature(request: Request, body_params: dict) -> bool:
    """Validate X-Twilio-Signature when TWILIO_VALIDATE_SIGNATURE=true."""
    if not _VALIDATE_SIG:
        return True
    try:
        from twilio.request_validator import RequestValidator
        token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        validator = RequestValidator(token)
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        return validator.validate(url, body_params, signature)
    except Exception as exc:
        log.warning(f"[CALL-V2] signature validation error: {exc}")
        return False


@router.api_route("/api/call-assistant-v2/twiml/{call_id}", methods=["GET", "POST"])
async def twiml(call_id: str, request: Request):
    """
    TwiML Twilio fetches when the agent answers. Forks both tracks to our media WS,
    then dials + bridges the customer.
    """
    # Re-normalize defensively: a literal '+' in a query string decodes to a
    # space, so coerce back to E.164 here regardless of how it arrived.
    customer = _normalize_phone(request.query_params.get("customer", ""))
    session = _get_or_create_session(call_id)
    if customer:
        session.customer_number = customer
    from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")
    stream_url  = f"{_ws_base()}/ws/call-assistant-v2/twilio/{call_id}"

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        '<Start>'
        f'<Stream url="{stream_url}" track="both_tracks">'
        f'<Parameter name="call_id" value="{call_id}"/>'
        '</Stream>'
        '</Start>'
        f'<Dial callerId="{from_number}"><Number>{customer}</Number></Dial>'
        '</Response>'
    )
    return Response(content=xml, media_type="application/xml")


@router.post("/api/call-assistant-v2/status")
async def status_callback(request: Request):
    """Twilio call-lifecycle webhook. Pushes status to the agent browser."""
    form = await request.form()
    params = dict(form)
    if not _maybe_validate_signature(request, params):
        return Response(status_code=403)
    call_id = request.query_params.get("call_id", "")
    call_status = params.get("CallStatus", "")
    session = _sessions.get(call_id)
    if session:
        session.status = call_status or session.status
        await _push_to_agents(session, {"type": "call_status", "status": session.status})
        log.info(f"[CALL-V2] status call_id={call_id} → {call_status}")
        if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
            await _push_to_agents(session, {"type": "call_status", "status": "completed"})
    return Response(status_code=204)


# ── WS: Twilio media fork → backend ───────────────────────────────────────────

@router.websocket("/ws/call-assistant-v2/twilio/{call_id}")
async def twilio_media_ws(websocket: WebSocket, call_id: str):
    """Twilio connects here (Start/Stream). Receive-only media fork."""
    await websocket.accept()
    log.info(f"[CALL-V2/IN] Twilio connected: call_id={call_id}")
    session = _get_or_create_session(call_id)
    encoding = session.encoding

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                continue

            event = parse_twilio(raw)
            if event is None:
                continue

            if isinstance(event, ConnectedEvent):
                log.info(f"[CALL-V2/IN] handshake protocol={event.protocol} v={event.version}")

            elif isinstance(event, StartEvent):
                session.stream_sid  = event.stream_sid
                session.encoding    = event.media_format.encoding or encoding
                session.sample_rate = event.media_format.sample_rate
                encoding            = session.encoding
                session.vad_customer = UtteranceBuffer(sample_rate=session.sample_rate)
                session.vad_agent    = UtteranceBuffer(sample_rate=session.sample_rate)
                if not session.twilio_call_sid:
                    session.twilio_call_sid = event.call_sid
                session.status = "in-progress"
                log.info(f"[CALL-V2/IN] start stream={event.stream_sid} "
                         f"encoding={session.encoding} rate={session.sample_rate} "
                         f"tracks={event.tracks} customer_track={_CUSTOMER_TRACK}")
                await _push_to_agents(session, {"type": "call_status", "status": "in-progress"})

            elif isinstance(event, MediaEvent):
                session.last_media_ts = time.time()
                # ── TEMP DIAGNOSTIC: count media frames per track ──────────
                _c = getattr(session, "_media_counts", None)
                if _c is None:
                    _c = {}
                    session._media_counts = _c
                _c[event.track] = _c.get(event.track, 0) + 1
                if _c[event.track] in (1, 50, 250, 500, 1000) or _c[event.track] % 1000 == 0:
                    log.info(f"[CALL-V2/DIAG] {session.call_id} media frames: "
                             f"{_c} (customer_track={_CUSTOMER_TRACK})")
                if event.track == _CUSTOMER_TRACK:
                    utt = session.vad_customer.feed(event.payload, encoding=encoding)
                    if utt:
                        log.info(f"[CALL-V2/DIAG] {session.call_id} CUSTOMER utterance "
                                 f"emitted dur={utt['duration_s']:.2f}s")
                        asyncio.create_task(
                            _run_customer_pipeline(session, utt["wav"], utt["duration_s"])
                        )
                elif _TRANSCRIBE_AGENT and event.track == _AGENT_TRACK:
                    utt = session.vad_agent.feed(event.payload, encoding=encoding)
                    if utt:
                        log.info(f"[CALL-V2/DIAG] {session.call_id} AGENT utterance emitted")
                        asyncio.create_task(_run_agent_transcribe(session, utt["wav"]))

            elif isinstance(event, StopEvent):
                log.info(f"[CALL-V2/IN] stop: call_id={call_id}")
                leftover = session.vad_customer.flush(encoding=encoding)
                if leftover:
                    await _run_customer_pipeline(session, leftover["wav"], leftover["duration_s"])
                break

    except WebSocketDisconnect:
        log.info(f"[CALL-V2/IN] Twilio disconnected: call_id={call_id}")
    except Exception as exc:
        log.error(f"[CALL-V2/IN] error for {call_id}: {exc}", exc_info=True)
    finally:
        session.status = "completed"
        await _push_to_agents(session, {"type": "call_status", "status": "completed"})


# ── WS: backend → agent browser ───────────────────────────────────────────────

@router.websocket("/ws/call-assistant-v2/agent/{call_id}")
async def agent_ws(websocket: WebSocket, call_id: str):
    """
    Agent browser connects here.

    SENDS to server:    {"type":"set_language","language":"auto"|"english"|…}
                        {"type":"ping"}
    RECEIVES:           {"type":"call_status","status":"…"}
                        {"type":"transcript","speaker":"Customer"|"Agent","text":"…"}
                        {"type":"token","token":"…"}
                        {"type":"suggestion_done","suggestion":"…","key_facts":"…",
                         "intent":"…","next_action":"…","sources":[…],"engine":"…",…}
                        {"type":"wait"}
                        {"type":"ping"}
    """
    await websocket.accept()
    session = _get_or_create_session(call_id)
    session.agent_ws_list.append(websocket)
    log.info(f"[CALL-V2/OUT] Agent connected: call_id={call_id} "
             f"connections={len(session.agent_ws_list)}")

    await websocket.send_json({
        "type": "session_info", "call_id": call_id, "status": session.status,
        "language": session.language,
    })

    async def _heartbeat():
        while True:
            await asyncio.sleep(15)
            if websocket not in session.agent_ws_list:
                break
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                break

    hb = asyncio.create_task(_heartbeat())

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "set_language":
                lang = (msg.get("language") or "auto").lower()
                session.language = lang
                await websocket.send_json({"type": "language_set", "language": lang})
            # "ping" silently ignored
    except WebSocketDisconnect:
        log.info(f"[CALL-V2/OUT] Agent disconnected: call_id={call_id}")
    except Exception as exc:
        log.error(f"[CALL-V2/OUT] error for {call_id}: {exc}")
    finally:
        hb.cancel()
        try:
            session.agent_ws_list.remove(websocket)
        except ValueError:
            pass
