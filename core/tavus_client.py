"""
services/tavus_client.py
────────────────────────
Pure Tavus API client — no Streamlit, no UI.
Extracted from ava.py, API keys moved to environment variables.

This module exposes three async-friendly calls:
  - create_conversation(api_key, openrouter_key, system_prompt, persona_name, replica_id, model)
  - end_conversation(api_key, conversation_id)
  - fetch_replicas(api_key)

All Tavus HTTP calls are synchronous (requests) but wrapped with
asyncio.to_thread so they don't block the FastAPI event loop.
"""

import os
import asyncio
import requests

TAVUS_BASE = "https://tavusapi.com/v2"

MODEL_OPTIONS = {
    "Nvidia_Nemotron-3-ultra(free)":     "meta-llama/llama-3.2-3b-instruct",
    "DeepSeek V3 — FREE":               "deepseek/deepseek-chat:free",
    "Gemini 2.0 Flash Lite — cheapest": "google/gemini-2.0-flash-lite-001",
    "Gemini 2.0 Flash — fastest":       "google/gemini-2.0-flash-001",
}

DEFAULT_MODEL = os.getenv(
    "TAVUS_DEFAULT_MODEL",
    "google/gemini-2.0-flash-001"
)

# The avatar's first spoken line when the call connects. A bare "Hello?" is
# role-AMBIGUOUS — it reads equally as a customer answering or a call-centre agent
# opening — which lets the model slide into "How can I help you?" on turn one.
# This line makes the avatar unmistakably a CUSTOMER answering an unknown caller
# ("you tell me who you are"), and seeds the assistant history with a customer
# turn so the first generated reply is anchored in the customer role.
DEFAULT_GREETING = os.getenv("TAVUS_DEFAULT_GREETING", "Hello? Yes, who is this?")


# ── Internal helpers ─────────────────────────────────────────────────────────

def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _create_persona_sync(api_key: str, openrouter_key: str,
                          system_prompt: str, persona_name: str,
                          model: str,
                          language: str = "english",
                          elevenlabs_key: str = "",
                          elevenlabs_voice_id: str = "",
                          elevenlabs_model_id: str = "eleven_flash_v2_5",
                          llm_base_url: str = "",
                          llm_api_key: str = "") -> str:
    """Create a Tavus persona and return its persona_id.

    Both languages get the LLM layer PLUS the latency levers (speculative
    inference, a max_tokens reply cap, and eager turn-taking) so every turn is
    snappy. Hindi ADDITIONALLY wires an ElevenLabs TTS layer (so Hindi is
    pronounced correctly) and a Whisper STT layer (strongest Hindi recognition);
    English keeps Tavus's fast built-in voice + STT and adds neither.
    """
    # llm_base_url/llm_api_key point the persona at the backend's per-turn
    # role-enforcing proxy (features/roleplay.py) when it is publicly reachable;
    # blank → direct OpenRouter (the original wiring).
    layers: dict = {
        "llm": {
            "model":    model,
            "base_url": llm_base_url or "https://openrouter.ai/api/v1",
            "api_key":  llm_api_key or openrouter_key,
        }
    }

    # ── Latency levers — applied to BOTH English and Hindi ─────────────────────
    # Previously these were Hindi-only, which left every English turn slower than
    # it needed to be. They are voice/STT-agnostic (pure LLM + turn-taking knobs)
    # and shave reply lag on every turn, so the English (LLM-only) path gets them
    # too — no other behaviour changes:
    #   • speculative_inference — the LLM starts generating before the STT
    #     transcript is fully finalised, cutting first-token latency.
    #   • extra_body.max_tokens — hard backstop to the "one short sentence" prompt
    #     rule: caps reply length so the avatar can NEVER emit a long monologue
    #     (slow to generate AND slow to speak) even if it ignores the instruction.
    #     A customer answers in 1-2 short sentences; 200 tokens fits comfortably.
    #     Tavus forwards extra_body straight to the OpenRouter request. Lower
    #     TAVUS_MAX_TOKENS = faster but risks mid-sentence cutoff; higher = slower.
    #   • turn_taking_patience="low" — the avatar ends the salesperson's turn
    #     eagerly instead of sitting through their pauses, so it answers sooner.
    #     Raise via TAVUS_TURN_PATIENCE if it starts cutting the trainee off.
    layers["llm"]["speculative_inference"] = True
    extra_body: dict = {"max_tokens": int(os.getenv("TAVUS_MAX_TOKENS", "200"))}
    _temp = os.getenv("TAVUS_TEMPERATURE", "").strip()
    if _temp:                                    # only override Tavus's default if asked
        extra_body["temperature"] = float(_temp)
    layers["llm"]["extra_body"] = extra_body
    layers["conversational_flow"] = {
        "turn_taking_patience": os.getenv("TAVUS_TURN_PATIENCE", "low"),
    }

    if language == "hindi":
        # Hindi ALSO needs two media layers English never does: an external
        # ElevenLabs TTS layer (correct Hindi pronunciation) and a Whisper STT
        # layer (strongest Hindi recognition). English keeps Tavus's fast built-in
        # voice + STT, so it adds neither — only the latency levers above.
        layers["tts"] = {
            "tts_engine":        "elevenlabs",
            "api_key":           elevenlabs_key,
            "external_voice_id": elevenlabs_voice_id,
            "tts_model_name":    elevenlabs_model_id,
        }
        # tavus-whisper is the most ACCURATE Hindi STT, but it is BATCH: it waits
        # for the salesperson to stop talking, THEN transcribes the whole
        # utterance — the single biggest chunk of Hindi reply lag. Set
        # TAVUS_STT_ENGINE to a streaming engine your Tavus account supports to
        # transcribe as the trainee speaks (trading a little accuracy for a big
        # latency cut). Default stays tavus-whisper so Hindi accuracy never drops
        # silently — flip it in .env when you want the speed.
        layers["stt"] = {"stt_engine": os.getenv("TAVUS_STT_ENGINE", "tavus-whisper")}

    payload = {
        "persona_name": persona_name or "Avatar Session",
        "system_prompt": system_prompt,
        "pipeline_mode": "full",
        "layers": layers,
    }
    r = requests.post(
        f"{TAVUS_BASE}/personas",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["persona_id"]


def _create_conversation_sync(api_key: str, persona_id: str,
                               replica_id: str,
                               custom_greeting: str = DEFAULT_GREETING,
                               language: str = "english",
                               conversational_context: str = "") -> dict:
    """Create a Tavus conversation and return {conversation_id, conversation_url}.

    custom_greeting is the FIRST thing the avatar says when the call connects.
    Setting it is essential: without it Tavus makes the model generate an opening
    turn straight from the system prompt, so the avatar ends up reading its own
    instructions aloud instead of behaving like a customer. The default
    (DEFAULT_GREETING) is a short, unmistakably-CUSTOMER line — "Hello? Yes, who
    is this?" — so the avatar answers the phone as the person being called and its
    first generated reply is anchored in the customer role (a bare "Hello?" reads
    equally as a call-centre agent opening and invites "How can I help you?").

    conversational_context is Tavus's PER-CONVERSATION context field. It is
    composed into the LLM's context in addition to the persona system prompt, so
    it anchors the customer role in the two cases the persona prompt cannot:
    (1) a pinned persona_id — Tavus ignores our system_prompt for those, and
    (2) whatever instructions Tavus itself appends after our persona prompt.

    For Hindi we hand Tavus a Hindi greeting and set properties.language="hindi"
    so its STT transcribes the salesperson's Hindi speech correctly.
    """
    # Tavus-side timeouts are the LAST-RESORT backstop for the inactivity
    # cleanup (the frontend timer and the backend reaper act first): even if
    # our backend restarts and loses its session registry, Tavus itself closes
    # abandoned rooms. left/absent are deliberately short; max_call_duration
    # caps the one case nothing can observe (mic-denied client that never
    # sends audio chunks).
    properties: dict = {
        "participant_absent_timeout": int(os.getenv("TAVUS_ABSENT_TIMEOUT_S", "60")),
        "participant_left_timeout":   int(os.getenv("TAVUS_LEFT_TIMEOUT_S",   "45")),
        "max_call_duration":          int(os.getenv("TAVUS_MAX_CALL_S",       "900")),
    }
    if language == "hindi":
        properties["language"] = "hindi"
        if custom_greeting == DEFAULT_GREETING:   # only swap the default, in-character customer line
            custom_greeting = "हाँ जी, बोलिए — कौन बात कर रहा है?"

    payload = {
        "replica_id": replica_id,
        "persona_id": persona_id,
        "custom_greeting": custom_greeting,
        "properties": properties,
    }
    if conversational_context.strip():
        payload["conversational_context"] = conversational_context.strip()
    r = requests.post(
        f"{TAVUS_BASE}/conversations",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "conversation_id":  data["conversation_id"],
        "conversation_url": data["conversation_url"],
    }


def _end_conversation_sync(api_key: str, conversation_id: str) -> None:
    """End an active Tavus conversation. Silently ignores errors."""
    try:
        requests.post(
            f"{TAVUS_BASE}/conversations/{conversation_id}/end",
            headers=_headers(api_key),
            timeout=10,
        )
    except Exception:
        pass


def _fetch_replicas_sync(api_key: str) -> list[dict]:
    """Return list of available replicas [{label, id, status, video, image}].

    video/image are Tavus sample thumbnails (a short clip of the avatar speaking
    and a still). verbose=true asks Tavus to include them. They may be empty for
    some replicas — callers must treat them as optional.
    """
    r = requests.get(
        f"{TAVUS_BASE}/replicas",
        headers=_headers(api_key),
        params={"verbose": "true"},
        timeout=20,
    )
    r.raise_for_status()
    data  = r.json()
    items = data if isinstance(data, list) else (
        data.get("data") or data.get("replicas") or
        data.get("results") or data.get("items") or []
    )
    reps = []
    for rep in items:
        rid    = rep.get("replica_id") or rep.get("id", "")
        rname  = rep.get("replica_name") or rep.get("name") or rid
        status = rep.get("status", "ready")
        video  = (rep.get("thumbnail_video_url") or rep.get("video_url") or "")
        image  = (rep.get("thumbnail_image_url") or rep.get("image_url") or "")
        # Custom (user-trained) replicas live on cdn.replica.tavus.io which
        # requires the Tavus API key. The browser can't set that header on a
        # <video src> tag, so route those through the backend proxy instead.
        if video and "cdn.replica.tavus.io" in video:
            video = f"/api/roleplay/avatar-thumb/{rid}"
        if rid and status not in ("error", "failed", "deleted"):
            reps.append({
                "label": rname, "id": rid, "status": status,
                "video": video, "image": image,
            })
    return reps


# ── Public async API ─────────────────────────────────────────────────────────

async def create_conversation(
    api_key:        str,
    openrouter_key: str,
    system_prompt:  str,
    persona_name:   str,
    replica_id:     str,
    model:          str = DEFAULT_MODEL,
    custom_greeting: str = DEFAULT_GREETING,
    persona_id:     str = "",
    language:       str = "english",
    elevenlabs_key: str = "",
    elevenlabs_voice_id: str = "",
    elevenlabs_model_id: str = "eleven_flash_v2_5",
    conversational_context: str = "",
    llm_base_url:   str = "",
    llm_api_key:    str = "",
) -> dict:
    """
    Full pipeline: create persona → wait 1 s → create conversation.
    Returns {"conversation_id": ..., "conversation_url": ...}

    If persona_id is provided, that existing Tavus persona is reused as-is and no
    new persona is created (the system_prompt/model are ignored for that call) —
    conversational_context is the ONLY role anchor that still applies then, which
    is exactly why callers should always pass it.

    language="hindi" adds an ElevenLabs voice + Whisper STT to the persona and a
    Hindi greeting + STT language to the conversation. English is the default and
    is wired exactly as before (LLM-only persona, Tavus default voice).

    llm_base_url/llm_api_key (optional) route the persona's LLM through the
    backend's per-turn role-enforcing proxy instead of direct OpenRouter.
    """
    import time as _time

    pinned = (persona_id or "").strip()

    def _full():
        if pinned:
            pid = pinned
        else:
            pid = _create_persona_sync(
                api_key, openrouter_key, system_prompt, persona_name, model,
                language=language,
                elevenlabs_key=elevenlabs_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                elevenlabs_model_id=elevenlabs_model_id,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
            )
            _time.sleep(1)      # Tavus needs a moment before conversation creation
        return _create_conversation_sync(
            api_key, pid, replica_id, custom_greeting, language=language,
            conversational_context=conversational_context,
        )

    return await asyncio.to_thread(_full)


async def end_conversation(api_key: str, conversation_id: str) -> None:
    """Async wrapper for ending a conversation."""
    await asyncio.to_thread(_end_conversation_sync, api_key, conversation_id)


async def fetch_replicas(api_key: str) -> list[dict]:
    """Async wrapper for fetching available replicas."""
    return await asyncio.to_thread(_fetch_replicas_sync, api_key)
