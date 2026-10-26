"""
features/tts.py
───────────────
Text-to-Speech endpoints.

Two endpoints:
  POST /api/tts/speak — edge-tts (Microsoft Edge neural TTS). Free, no key,
       high-quality Indian-language neural voices. Used by the pitch play button.

  POST /api/tts/eva   — Eva 2.0 voice assistant.
       DEFAULT engine: edge-tts, which has genuine Indian female neural voices
       (Neerja / Swara / Aarohi / …) in every UI language — a real Indian accent,
       and deterministic verbatim reading of Eva's already-grounded answer.

       OPTIONAL: gpt-4o-mini-tts via OpenRouter's OpenAI-compatible /audio/speech
       endpoint (warm, steerable voice). It is OFF by default because the model
       is not enabled on the current OpenRouter account (returns "model does not
       exist"), and the available `gpt-audio-mini` chat model paraphrases text
       rather than reading it verbatim — unsafe for financial answers. To turn it
       on when access is available, set EVA_TTS_ENGINE_LANGS (e.g. "english,hindi")
       and, if needed, EVA_TTS_MODEL to the correct slug. Any failure falls back
       to edge-tts so Eva never goes silent.

edge-tts is natively async and produces a single seekable MP3 (proper
pause/resume).
"""

import io
import os
import logging

import requests
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

log = logging.getLogger("hdfc")
router = APIRouter()

# Map UI language names → Microsoft Edge TTS neural voice names
VOICE_MAP = {
    "english":   "en-IN-NeerjaNeural",
    "hindi":     "hi-IN-SwaraNeural",
    # Hinglish output is romanised Hindi+English → the Indian-ENGLISH voice reads it
    # naturally with an Indian accent (the Devanagari Hindi voice mangles Roman text).
    "hinglish":  "en-IN-NeerjaNeural",
    "marathi":   "mr-IN-AarohiNeural",
    "tamil":     "ta-IN-PallaviNeural",
    "bengali":   "bn-IN-TanishaaNeural",
    "telugu":    "te-IN-ShrutiNeural",
    "gujarati":  "gu-IN-DhwaniNeural",
    "kannada":   "kn-IN-SapnaNeural",
    "punjabi":   "pa-IN-VaaniNeural",
    "odia":      "hi-IN-SwaraNeural",   # no dedicated Odia voice; fall back to Hindi
    "malayalam": "ml-IN-SobhanaNeural",
    "urdu":      "ur-IN-GulNeural",
}

# ── Eva (gpt-4o-mini-tts via OpenRouter) config ──────────────────────────────
# OpenRouter now serves OpenAI's TTS models on an OpenAI-compatible
# /audio/speech endpoint. Model slug is versioned (not bare "gpt-4o-mini-tts").
from core.llm import OPENROUTER_KEY, OPENROUTER_URL  # reuse the one configured key

EVA_TTS_MODEL = os.getenv("EVA_TTS_MODEL", "openai/gpt-4o-mini-tts-2025-12-15")
# edge-tts speaking rate for Eva — brisk and responsive, still clear.
EVA_TTS_RATE = os.getenv("EVA_TTS_RATE", "+16%")
# Eva's OWN voice (kept separate from VOICE_MAP so the pitch player is untouched).
# NeerjaExpressive is the natural, warmer Indian-English female voice (less robotic
# than the plain Neerja). English + Hinglish both use it; regional keep native voices.
EVA_EDGE_VOICE = os.getenv("EVA_EDGE_VOICE", "en-IN-NeerjaExpressiveNeural")

def _eva_voice_for(lang: str) -> str:
    lang = (lang or "english").strip().lower()
    if lang in ("english", "hinglish", "hindi"):
        return EVA_EDGE_VOICE
    return VOICE_MAP.get(lang, EVA_EDGE_VOICE)
# gpt-4o-mini-tts female voices: coral / nova / shimmer / sage. coral is warm+expressive.
EVA_TTS_VOICE = os.getenv("EVA_TTS_VOICE", "coral")
# Which UI languages are spoken by gpt-4o-mini-tts. Everything else → edge-tts.
# EMPTY by default → all languages use edge-tts (the dedicated OpenRouter TTS
# model is not enabled on the current account). Set e.g. "english,hindi" to
# route those languages through OpenRouter once access is available.
EVA_TTS_ENGINE_LANGS = {
    s.strip().lower()
    for s in os.getenv("EVA_TTS_ENGINE_LANGS", "").split(",")
    if s.strip()
}
# Steer the (natively American) voice toward Eva's persona + an Indian accent.
EVA_TTS_INSTRUCTIONS = os.getenv(
    "EVA_TTS_INSTRUCTIONS",
    "You are Eva, a warm, friendly and professional female banking assistant for "
    "HDFC sales agents. Speak with a natural, clear Indian-English accent, at an "
    "unhurried, encouraging pace. Sound helpful and confident, never robotic.",
)


class TTSRequest(BaseModel):
    text: str
    lang: str = "English"


async def _edge_tts_bytes_async(text: str, lang: str, rate: str = "+0%", voice: str = None) -> bytes:
    """Async edge-tts synthesis (used inside FastAPI's running loop).
    rate:  edge-tts speaking rate, e.g. "+6%" for a snappier delivery.
    voice: explicit voice short-name; falls back to VOICE_MAP by language."""
    import edge_tts

    voice = voice or VOICE_MAP.get((lang or "").strip().lower(), "en-IN-NeerjaNeural")
    communicate = edge_tts.Communicate(text.strip(), voice, rate=rate)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


def _openrouter_tts_bytes(text: str, lang: str) -> bytes:
    """Synthesize `text` to MP3 bytes via OpenRouter gpt-4o-mini-tts.
    Raises on any failure so the caller can fall back to edge-tts."""
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    resp = requests.post(
        f"{OPENROUTER_URL}/audio/speech",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": EVA_TTS_MODEL,
            "input": text.strip(),
            "voice": EVA_TTS_VOICE,
            "instructions": EVA_TTS_INSTRUCTIONS,
            "response_format": "mp3",
        },
        timeout=45,
    )
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype.lower():
        # OpenRouter returned an error object, not audio.
        raise RuntimeError(f"TTS provider error: {resp.text[:200]}")
    if not resp.content:
        raise RuntimeError("TTS provider returned empty audio")
    return resp.content


def _mp3_response(audio_bytes: bytes) -> Response:
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Length": str(len(audio_bytes)),
            "Content-Disposition": "inline; filename=speech.mp3",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@router.post("/api/tts/speak")
async def tts_speak(req: TTSRequest):
    """Convert text to speech using edge-tts and return the MP3 to the browser."""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    voice = VOICE_MAP.get(req.lang.strip().lower(), "en-IN-NeerjaNeural")
    try:
        audio_bytes = await _edge_tts_bytes_async(req.text, req.lang)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="No audio generated")
        log.info(f"[TTS] edge-tts: voice={voice}, chars={len(req.text)}, bytes={len(audio_bytes)}")
        return _mp3_response(audio_bytes)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[TTS] edge-tts error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")


@router.post("/api/tts/eva")
async def tts_eva(req: TTSRequest):
    """
    Eva's voice. English/Hindi → gpt-4o-mini-tts (warm female, Indian accent);
    regional languages → edge-tts native voice. Any gpt-4o-mini-tts failure
    falls back to edge-tts so Eva never goes silent.

    Called once per sentence by the frontend so Eva starts speaking within ~1s
    while the rest of the answer is still being synthesized.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    lang = (req.lang or "english").strip().lower()
    use_openai = lang in EVA_TTS_ENGINE_LANGS

    if use_openai:
        try:
            import anyio
            audio_bytes = await anyio.to_thread.run_sync(
                _openrouter_tts_bytes, req.text, lang
            )
            if audio_bytes:
                log.info(f"[TTS/eva] gpt-4o-mini-tts voice={EVA_TTS_VOICE} lang={lang} bytes={len(audio_bytes)}")
                return _mp3_response(audio_bytes)
        except Exception as e:
            log.warning(f"[TTS/eva] gpt-4o-mini-tts failed ({e}) — falling back to edge-tts")

    # edge-tts path (regional languages, or OpenRouter fallback)
    try:
        eva_voice = _eva_voice_for(lang)
        audio_bytes = await _edge_tts_bytes_async(req.text, lang, rate=EVA_TTS_RATE, voice=eva_voice)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="No audio generated")
        log.info(f"[TTS/eva] edge-tts voice={eva_voice} lang={lang} rate={EVA_TTS_RATE} bytes={len(audio_bytes)}")
        return _mp3_response(audio_bytes)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[TTS/eva] edge-tts error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")
