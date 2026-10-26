"""
WebSocket-based Live Call Assistant — two-speaker diarization + real-time coaching.

Two WebSocket endpoints:
  WS /ws/agent/{call_sid}
      Agent browser connects here.
      RECEIVES: {"type":"set_agent","person":"Person 1"|"Person 2"}
      SENDS:    {"type":"transcript","speaker":"Person 1","text":"..."}
                {"type":"token","token":"..."}
                {"type":"suggestion_done","sources":[...]}
                {"type":"wait"}
                {"type":"ping"}   (heartbeat every 15s)

  WS /ws/call/audio/{call_sid}
      Agent browser sends audio blobs here (binary webm/opus, one per VAD utterance).
      RECEIVES: bytes  — complete utterance audio blob
                JSON   — {"type":"stop"} signals end of session
      The browser does VAD and sends complete utterances — server processes immediately.

Pipeline per utterance (target <1.5s end-to-end):
  1. [optional] noise reduce via noisereduce (~20ms)
  2. concurrent: STT (faster-whisper) + speaker embed (adaptive_speaker) + RAG (prev turn text)
  3. pre-gate: GATE A (≤3 words) | GATE B (filler) | GATE C (agent spoke last)
  4. build sliding window transcript (max 8 turns)
  5. LLM stream (GPT-4o-mini via OpenRouter; Ollama fallback)
     — first-token WAIT early-exit (<5ms after token arrives)
  6. stream tokens to ALL active agent WS connections

Session state is keyed by call_sid and shared between both WS handlers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import core.deps as deps
from core.deps import log

# ── Noise reduction (optional dep) ───────────────────────────────────────────

try:
    import noisereduce as nr
    _HAS_NOISEREDUCE = True
    log.info("[NOISE] noisereduce available — noise reduction active")
except ImportError:
    _HAS_NOISEREDUCE = False
    log.warning("[NOISE] noisereduce not installed — skipping noise reduction (pip install noisereduce)")


def _reduce_noise(pcm: np.ndarray, sr: int = 16000) -> tuple[np.ndarray, int]:
    """Apply spectral noise reduction. Returns (cleaned_pcm, elapsed_ms).
    Only applied when audio is >= 0.5s (too short = waste of time)."""
    if not _HAS_NOISEREDUCE or pcm.size / sr < 0.5:
        return pcm, 0
    t0 = time.time()
    try:
        cleaned = nr.reduce_noise(y=pcm, sr=sr)
        ms = int((time.time() - t0) * 1000)
        log.debug(f"[NOISE] reduced in {ms}ms")
        return cleaned.astype(np.float32), ms
    except Exception as exc:
        log.warning(f"[NOISE] noisereduce failed ({exc}) — using raw audio")
        return pcm, 0


# ── Filler + hallucination guards (reuse from voice.py) ──────────────────────

from features.voice import _is_filler_utterance, _is_whisper_hallucination  # noqa: E402


# ── Prompts ───────────────────────────────────────────────────────────────────

from prompts.live_assistant import LIVE_ASSIST_SYSTEM, build_live_assist_user  # noqa: E402
from core.emi_calc import (  # noqa: E402
    is_calculation_question, parse_amount, parse_tenure, compute_emi,
    format_for_prompt as format_emi_for_prompt,
)


def _build_emi_block(transcript: str, ctx: dict) -> str | None:
    if not is_calculation_question(transcript):
        return None
    amount = parse_amount(transcript) or parse_amount(str(ctx.get("loan_amount", "")))
    tenure = parse_tenure(transcript) or parse_tenure(str(ctx.get("tenure", "")))
    if amount is None or tenure is None:
        return None
    try:
        return format_emi_for_prompt(compute_emi(amount, tenure, annual_rate_pct=10.5))
    except Exception:
        return None


# ── Session state ─────────────────────────────────────────────────────────────

@dataclass
class LiveWSSession:
    call_sid:            str
    created_at:          float = field(default_factory=time.time)
    last_audio_ts:       float = field(default_factory=time.time)

    # Agent identity — set via {"type":"set_agent","person":"Person 1"}
    agent_person:        Optional[str] = None        # "Person 1" | "Person 2"

    # Adaptive speaker session (per call_sid)
    adaptive_session_id: Optional[str] = None

    # Rolling transcript window — last 8 turns [{speaker, text}]
    transcript_window:   List[dict] = field(default_factory=list)

    # Active agent WebSocket connections (support multiple tabs)
    agent_ws_list:       List[WebSocket] = field(default_factory=list)

    # Prevent concurrent pipelines on the same session
    pipeline_lock:       asyncio.Lock = field(default_factory=asyncio.Lock)

    # Duration counter
    call_start_ts:       float = field(default_factory=time.time)


# Module-level session registry
_sessions: Dict[str, LiveWSSession] = {}


def _get_or_create_session(call_sid: str) -> LiveWSSession:
    if call_sid not in _sessions:
        _sessions[call_sid] = LiveWSSession(call_sid=call_sid)
        log.info(f"[LIVE-WS] New session: call_sid={call_sid}")
    return _sessions[call_sid]


router = APIRouter()


# ── Heartbeat helper ──────────────────────────────────────────────────────────

async def _heartbeat_loop(session: LiveWSSession):
    """Send a ping to all agent WS connections every 15s. Remove dead connections."""
    while True:
        await asyncio.sleep(15)
        if not session.agent_ws_list:
            break
        dead = []
        for ws in list(session.agent_ws_list):
            try:
                await ws.send_json({"type": "ping"})
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                session.agent_ws_list.remove(ws)
            except ValueError:
                pass
        if dead:
            log.info(f"[LIVE-WS] {session.call_sid} pruned {len(dead)} dead agent WS, {len(session.agent_ws_list)} remain")


# ── Push helpers ──────────────────────────────────────────────────────────────

async def _push_to_agents(session: LiveWSSession, payload: dict):
    """Send a JSON payload to all active agent WS connections. Log delivery."""
    if not session.agent_ws_list:
        log.debug(f"[WS-OUT] {session.call_sid} → no agent connections, dropping payload type={payload.get('type','?')}")
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
    n_sent = len(session.agent_ws_list)
    log.debug(f"[WS-OUT] {session.call_sid} → {n_sent} agent connections, type={payload.get('type','?')}")


# ── Audio pipeline ────────────────────────────────────────────────────────────

async def _run_pipeline(session: LiveWSSession, audio_bytes: bytes, content_type: str = "audio/webm"):
    """
    Full pipeline for one completed utterance.
    Runs as an asyncio Task so the audio WS receive loop stays unblocked.
    """
    if session.pipeline_lock.locked():
        log.debug(f"[LIVE-WS] {session.call_sid} pipeline busy — utterance dropped")
        return

    async with session.pipeline_lock:
        t_start = time.time()
        loop    = asyncio.get_event_loop()

        # ── Step 1: Decode + noise reduce ─────────────────────────────────
        from core.whisper_stt import _decode_audio_to_pcm
        try:
            pcm = await loop.run_in_executor(
                None,
                lambda: _decode_audio_to_pcm(audio_bytes, target_sr=16000),
            )
        except Exception as exc:
            log.warning(f"[LIVE-WS] audio decode failed: {exc}")
            return

        pcm, noise_ms = await loop.run_in_executor(None, lambda: _reduce_noise(pcm))

        # Pack back to bytes for Whisper (it can accept numpy arrays directly)
        # We'll pass pcm (float32 array) directly to the WhisperSTT engine.

        # ── Step 2: Concurrent STT + speaker_embed + RAG (prev turn text) ─
        from core.whisper_stt import get_live_engine as get_whisper
        from core.adaptive_speaker import get_engine as get_adaptive

        whisper  = get_whisper()
        adaptive = get_adaptive()

        # Ensure adaptive session exists
        if session.adaptive_session_id is None:
            session.adaptive_session_id = adaptive.start_session()
            log.info(f"[LIVE-WS] {session.call_sid} adaptive_session={session.adaptive_session_id}")

        # RAG runs on the PREVIOUS turn text (already transcribed) while
        # STT processes the current chunk — saves ~100ms.
        prev_text = session.transcript_window[-1]["text"] if session.transcript_window else ""

        t_conc = time.time()
        try:
            # STT: transcribe using the pre-decoded pcm array
            def _transcribe():
                # faster-whisper accepts numpy float32 arrays directly
                t0 = time.time()
                segments, info = whisper.model.transcribe(
                    pcm,
                    language=None,  # auto-detect
                    beam_size=1,    # greedy for speed on live chunks
                    temperature=0.0,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500),
                    condition_on_previous_text=False,
                )
                parts = []
                for seg in segments:
                    txt = (seg.text or "").strip()
                    if txt:
                        parts.append(txt)
                transcript_text = " ".join(parts).strip()
                ms = int((time.time() - t0) * 1000)
                return {"transcript": transcript_text, "language": info.language, "ms": ms}

            def _embed():
                return adaptive.label_utterance(
                    session.adaptive_session_id,
                    audio_bytes,
                    content_type=content_type,
                )

            def _rag():
                if not prev_text or not (deps.rag_instance.structured_data or deps.rag_instance.documents):
                    return ("", [])
                try:
                    return deps.rag_instance._retrieve_relevant_context(
                        deps.rag_instance._expand_query(prev_text), top_k=4
                    )
                except Exception:
                    return ("", [])

            stt_result, label_result, (prev_context, prev_sources) = await asyncio.gather(
                loop.run_in_executor(None, _transcribe),
                loop.run_in_executor(None, _embed),
                loop.run_in_executor(None, _rag),
            )
        except Exception as exc:
            log.error(f"[LIVE-WS] concurrent step failed: {exc}", exc_info=True)
            return

        conc_ms    = int((time.time() - t_conc) * 1000)
        stt_ms     = stt_result.get("ms", 0)
        label_ms   = label_result.get("ms", 0)
        transcript = (stt_result.get("transcript") or "").strip()
        speaker    = label_result.get("label", "Unknown")
        stt_lang   = stt_result.get("language", "?")
        is_overlap = label_result.get("is_overlap", False)

        log.info(
            f"[LIVE-WS] {session.call_sid[:8]} speaker={speaker} overlap={is_overlap} "
            f"stt={stt_ms}ms label={label_ms}ms noise={noise_ms}ms conc={conc_ms}ms "
            f"text='{transcript[:60]}'"
        )

        # Discard cross-talk (overlapping speech — unreliable, skip silently)
        if is_overlap:
            log.info(f"[LIVE-WS] overlap chunk discarded")
            return

        if not transcript:
            return
        if _is_whisper_hallucination(transcript):
            log.warning(f"[LIVE-WS] hallucination suppressed: '{transcript[:60]}'")
            return

        # ── Push transcript turn to agent screen immediately ───────────────
        await _push_to_agents(session, {
            "type":       "transcript",
            "speaker":    speaker,
            "text":       transcript,
            "stt_ms":     stt_ms,
            "label_ms":   label_ms,
        })

        # ── Step 3: Pre-gates ──────────────────────────────────────────────
        # GATE A — too short
        word_count = len(transcript.split())
        if word_count <= 3:
            log.info(f"[LIVE-WS] word-count gate ({word_count} words) → WAIT")
            await _push_to_agents(session, {"type": "wait"})
            return

        # GATE B — pure filler
        if _is_filler_utterance(transcript):
            log.info(f"[LIVE-WS] filler gate → WAIT: '{transcript}'")
            await _push_to_agents(session, {"type": "wait"})
            return

        # GATE C — agent-spoke-last (two consecutive agent turns)
        if session.agent_person and session.transcript_window:
            last_spk = session.transcript_window[-1].get("speaker", "")
            if last_spk == speaker == session.agent_person:
                log.info(f"[LIVE-WS] agent-spoke-last gate → WAIT")
                await _push_to_agents(session, {"type": "wait"})
                _append_to_window(session, speaker, transcript)
                return

        # ── Step 4: Update sliding window ─────────────────────────────────
        _append_to_window(session, speaker, transcript)

        # ── Step 5: RAG retrieval for CURRENT turn ─────────────────────────
        if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
            log.warning("[LIVE-WS] no docs loaded — skipping LLM")
            await _push_to_agents(session, {"type": "wait"})
            return

        t_rag = time.time()
        try:
            (context, sources), emi_block = await asyncio.gather(
                loop.run_in_executor(
                    None,
                    lambda: deps.rag_instance._retrieve_relevant_context(
                        deps.rag_instance._expand_query(transcript), top_k=4
                    ),
                ),
                loop.run_in_executor(None, lambda: _build_emi_block(transcript, {})),
            )
        except Exception as exc:
            log.error(f"[LIVE-WS] RAG failed: {exc}")
            await _push_to_agents(session, {"type": "wait"})
            return

        rag_ms = int((time.time() - t_rag) * 1000)

        if not context or len(context.strip()) < 30:
            log.warning("[LIVE-WS] insufficient RAG context")
            await _push_to_agents(session, {"type": "wait"})
            return

        # ── Step 6: Build prompt ───────────────────────────────────────────
        _whisper_to_lang = {"en": "english", "hi": "hindi", "mr": "marathi"}
        lang_directive = _whisper_to_lang.get(stt_lang, "auto")

        role_hint = None
        if session.agent_person:
            role_hint = f"{session.agent_person} is the HDFC agent"

        labeled = _build_labeled_transcript(session)
        user_msg = build_live_assist_user(
            labeled_transcript=labeled,
            context=context,
            language=lang_directive,
            emi_block=emi_block,
            role_hint=role_hint,
        )

        # ── Step 7: LLM stream → push tokens to agent WS ──────────────────
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        t_llm = time.time()
        first_token_ms: int | None = None
        full_reply = ""

        if openrouter_key:
            await _stream_openrouter(
                session, user_msg, t_llm, t_start,
                stt_ms, label_ms, rag_ms, sources,
            )
        else:
            await _stream_ollama(
                session, user_msg, t_llm, t_start,
                stt_ms, label_ms, rag_ms, sources,
            )


def _append_to_window(session: LiveWSSession, speaker: str, text: str):
    """Add a turn to the sliding transcript window (max 8)."""
    session.transcript_window.append({"speaker": speaker, "text": text})
    if len(session.transcript_window) > 8:
        session.transcript_window = session.transcript_window[-8:]


def _build_labeled_transcript(session: LiveWSSession) -> str:
    """Build labeled transcript string from the session window."""
    return "\n".join(f"{t['speaker']}: {t['text']}" for t in session.transcript_window)


async def _stream_openrouter(
    session: LiveWSSession, user_msg: str, t_llm: float, t_start: float,
    stt_ms: int, label_ms: int, rag_ms: int, sources: list,
):
    """Stream from GPT-4o-mini via OpenRouter, push tokens to agent WS."""
    from openai import AsyncOpenAI
    key = os.environ.get("OPENROUTER_API_KEY", "")
    client = AsyncOpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
    try:
        stream = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": LIVE_ASSIST_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=80,
            temperature=0,
            stream=True,
        )
        full = ""
        first_token_ms: int | None = None
        async for chunk in stream:
            token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if not token:
                continue
            full += token
            if first_token_ms is None:
                first_token_ms = int((time.time() - t_llm) * 1000)
                log.info(
                    f"[LIVE-WS] GPT first token {first_token_ms}ms "
                    f"(stt={stt_ms} label={label_ms} rag={rag_ms})"
                )
                # Early WAIT detection on first meaningful token
                if full.strip().upper() == "WAIT":
                    log.info("[LIVE-WS] GPT WAIT on first token → no card")
                    await _push_to_agents(session, {"type": "wait"})
                    return
            await _push_to_agents(session, {"type": "token", "token": token})

        is_wait = full.strip().upper() == "WAIT"
        total_ms = int((time.time() - t_start) * 1000)
        log.info(
            f"[LIVE-WS] done wait={is_wait} total={total_ms}ms "
            f"first_token={first_token_ms} engine=openrouter/gpt-4o-mini"
        )
        if is_wait:
            await _push_to_agents(session, {"type": "wait"})
        else:
            await _push_to_agents(session, {
                "type":         "suggestion_done",
                "sources":      sources,
                "engine":       "gpt-4o-mini",
                "stt_ms":       stt_ms,
                "label_ms":     label_ms,
                "rag_ms":       rag_ms,
                "first_token_ms": first_token_ms,
                "total_ms":     total_ms,
            })
    except Exception as exc:
        log.error(f"[LIVE-WS] OpenRouter stream error: {exc}")
        # Fallback to Ollama
        log.info("[LIVE-WS] Falling back to Ollama after OpenRouter failure")
        await _stream_ollama(session, user_msg, t_llm, t_start, stt_ms, label_ms, rag_ms, sources)


async def _stream_ollama(
    session: LiveWSSession, user_msg: str, t_llm: float, t_start: float,
    stt_ms: int, label_ms: int, rag_ms: int, sources: list,
):
    """Stream from local Ollama, push tokens to agent WS."""
    import httpx
    ollama_url   = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.environ.get("CALL_ASSIST_MODEL",
                   os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:14b"))
    combined     = f"{LIVE_ASSIST_SYSTEM}\n\n{user_msg}"
    log.info(f"[LIVE-WS] Ollama fallback ({ollama_model})")

    full = ""
    first_token_ms: int | None = None
    try:
        async with httpx.AsyncClient(timeout=60) as hx:
            async with hx.stream(
                "POST", f"{ollama_url}/api/generate",
                json={"model": ollama_model, "prompt": combined, "stream": True,
                      "options": {"temperature": 0, "num_predict": 80}},
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data  = json.loads(line)
                        token = data.get("response", "")
                        if token:
                            full += token
                            if first_token_ms is None:
                                first_token_ms = int((time.time() - t_llm) * 1000)
                                if full.strip().upper() == "WAIT":
                                    await _push_to_agents(session, {"type": "wait"})
                                    return
                            await _push_to_agents(session, {"type": "token", "token": token})
                        if data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        log.error(f"[LIVE-WS] Ollama stream error: {exc}")
        await _push_to_agents(session, {"type": "wait"})
        return

    is_wait  = full.strip().upper() == "WAIT"
    total_ms = int((time.time() - t_start) * 1000)
    if is_wait:
        await _push_to_agents(session, {"type": "wait"})
    else:
        await _push_to_agents(session, {
            "type":           "suggestion_done",
            "sources":        sources,
            "engine":         ollama_model,
            "stt_ms":         stt_ms,
            "label_ms":       label_ms,
            "rag_ms":         rag_ms,
            "first_token_ms": first_token_ms,
            "total_ms":       total_ms,
        })


# ── WebSocket: Agent browser ──────────────────────────────────────────────────

@router.websocket("/ws/agent/{call_sid}")
async def ws_agent(websocket: WebSocket, call_sid: str):
    """
    Agent browser connects here to receive coaching suggestions and send identity.

    Messages the agent SENDS:
      {"type":"set_agent","person":"Person 1"|"Person 2"}   — one-tap identity
      {"type":"ping"}   — keepalive (silently ignored)

    Messages the agent RECEIVES:
      {"type":"transcript","speaker":"Person 1","text":"...","stt_ms":N,"label_ms":N}
      {"type":"token","token":"..."}              — streaming suggestion token
      {"type":"suggestion_done","sources":[...],"engine":"gpt-4o-mini",...}
      {"type":"wait"}                             — idle state (no suggestion)
      {"type":"ping"}                             — heartbeat from server
      {"type":"session_info","call_sid":"...","duration_s":N}  — on connect
    """
    await websocket.accept()
    session = _get_or_create_session(call_sid)
    session.agent_ws_list.append(websocket)
    n_total = len(session.agent_ws_list)

    log.info(f"[LIVE-WS] Agent connected: call_sid={call_sid} total_connections={n_total}")

    # Send session info immediately on connect
    try:
        await websocket.send_json({
            "type":      "session_info",
            "call_sid":  call_sid,
            "duration_s": round(time.time() - session.call_start_ts),
        })
    except Exception:
        pass

    # Start heartbeat task for this session (one shared task is enough;
    # extra connections just piggyback on the same session heartbeat).
    if n_total == 1:
        asyncio.create_task(_heartbeat_loop(session))

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except asyncio.TimeoutError:
                # No message from agent for 60s — browser is probably gone
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if msg.get("type") == "set_agent":
                person = msg.get("person", "")
                if person in ("Person 1", "Person 2"):
                    session.agent_person = person
                    log.info(f"[LIVE-WS] {call_sid} agent_person set to '{person}'")
                    await websocket.send_json({"type": "agent_set", "person": person})
            # "ping" messages are silently ignored (heartbeat from browser)

    except WebSocketDisconnect:
        log.info(f"[LIVE-WS] Agent disconnected: call_sid={call_sid}")
    except Exception as exc:
        log.error(f"[LIVE-WS] Agent WS error for {call_sid}: {exc}")
    finally:
        try:
            session.agent_ws_list.remove(websocket)
        except ValueError:
            pass
        log.info(f"[LIVE-WS] Agent WS removed, {len(session.agent_ws_list)} remain for {call_sid}")


# ── WebSocket: Audio stream ───────────────────────────────────────────────────

@router.websocket("/ws/call/audio/{call_sid}")
async def ws_audio(websocket: WebSocket, call_sid: str):
    """
    Agent browser sends audio blobs here (binary WebM/opus chunks after browser VAD).

    Protocol:
      - Binary message: complete utterance audio blob → triggers pipeline
      - Text message:   JSON {"type":"stop"} → flush + close
    """
    await websocket.accept()
    session = _get_or_create_session(call_sid)
    log.info(f"[LIVE-WS] Audio WS connected: call_sid={call_sid}")

    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=30.0)
            except asyncio.TimeoutError:
                # No audio for 30s — keep alive
                continue

            if msg["type"] == "websocket.disconnect":
                break

            # Binary message: audio blob → run pipeline
            if msg["type"] == "websocket.receive":
                if msg.get("bytes"):
                    audio_bytes = msg["bytes"]
                    if len(audio_bytes) < 800:
                        continue   # too small — VAD noise
                    session.last_audio_ts = time.time()
                    asyncio.create_task(
                        _run_pipeline(session, audio_bytes, "audio/webm")
                    )
                elif msg.get("text"):
                    try:
                        data = json.loads(msg["text"])
                    except Exception:
                        continue
                    if data.get("type") == "stop":
                        log.info(f"[LIVE-WS] Audio stop signal for {call_sid}")
                        break

    except WebSocketDisconnect:
        log.info(f"[LIVE-WS] Audio WS disconnected: call_sid={call_sid}")
    except Exception as exc:
        log.error(f"[LIVE-WS] Audio WS error for {call_sid}: {exc}")
    finally:
        log.info(f"[LIVE-WS] Audio WS closed for {call_sid}")


# ── HTTP management ───────────────────────────────────────────────────────────

@router.get("/api/live-ws/sessions")
async def list_live_ws_sessions():
    return JSONResponse(content={
        "success": True,
        "count": len(_sessions),
        "sessions": [
            {
                "call_sid":         s.call_sid,
                "agent_person":     s.agent_person,
                "agent_connections": len(s.agent_ws_list),
                "turns_in_window":  len(s.transcript_window),
                "duration_s":       round(time.time() - s.call_start_ts, 1),
                "idle_audio_s":     round(time.time() - s.last_audio_ts, 1),
            }
            for s in _sessions.values()
        ],
    })


@router.delete("/api/live-ws/sessions/{call_sid}")
async def end_live_ws_session(call_sid: str):
    if call_sid not in _sessions:
        return JSONResponse(status_code=404, content={"success": False, "error": "not found"})
    session = _sessions.pop(call_sid)
    for ws in session.agent_ws_list:
        try:
            await ws.close()
        except Exception:
            pass
    return JSONResponse(content={"success": True, "dropped": call_sid})
