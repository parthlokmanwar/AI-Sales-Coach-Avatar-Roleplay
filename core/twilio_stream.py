"""
Twilio Media Streams WebSocket event parser  (Call Assistant V2).

Twilio's <Start><Stream> / <Connect><Stream> verbs open a WebSocket to our
backend and send newline-free JSON frames:

  connected  — initial handshake, no audio
  start      — call metadata: streamSid, callSid, accountSid, tracks[],
               customParameters, mediaFormat (encoding, sampleRate, channels)
  media      — 20ms audio chunk: base64-encoded G.711 μ-law, 8 kHz mono.
               `track` is "inbound" or "outbound" on a forked (Start) stream.
  mark       — playback marker ack (bidirectional Connect streams only) — ignored
  dtmf       — DTMF key-press (bidirectional Connect streams only)
  stop       — stream closed / call ended

Audio format (Twilio Media Streams default):
  encoding:    audio/x-mulaw  (G.711 μ-law, 8-bit per sample)
  sampleRate:  8000 Hz
  channels:    1 (mono)

Track semantics on a <Start><Stream> attached to the AGENT's call leg:
  inbound  = audio coming FROM the agent's phone  (the agent speaking)
  outbound = audio Twilio plays TO the agent       (the customer, bridged via Dial)
So the CUSTOMER is the "outbound" track in the V2 topology. See
features/call_assistant_v2.py (CALL_ASSIST_V2_CUSTOMER_TRACK) for the mapping.

This module ONLY parses frames into typed dataclasses — no buffering, no
transcoding. Buffering/VAD lives in core/call_assist_vad.py (reused as-is).

Public API:
  event = parse(raw_text)              # typed dataclass or None
  isinstance(event, MediaEvent)        # dispatch on type
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Typed event dataclasses ───────────────────────────────────────────────────

@dataclass
class ConnectedEvent:
    event: str = "connected"
    protocol: str = ""
    version: str = ""


@dataclass
class MediaFormat:
    encoding: str = "audio/x-mulaw"
    sample_rate: int = 8000
    channels: int = 1


@dataclass
class StartEvent:
    event: str = "start"
    stream_sid: str = ""
    call_sid: str = ""
    account_sid: str = ""
    tracks: List[str] = field(default_factory=list)
    custom_parameters: Dict[str, Any] = field(default_factory=dict)
    media_format: MediaFormat = field(default_factory=MediaFormat)


@dataclass
class MediaEvent:
    event: str = "media"
    stream_sid: str = ""
    sequence_number: int = 0
    track: str = "inbound"          # "inbound" | "outbound"
    chunk: int = 0
    timestamp_ms: int = 0
    payload: bytes = b""            # already base64-decoded raw μ-law bytes


@dataclass
class MarkEvent:
    event: str = "mark"
    stream_sid: str = ""
    name: str = ""


@dataclass
class DtmfEvent:
    event: str = "dtmf"
    stream_sid: str = ""
    track: str = "inbound_track"
    digit: str = ""


@dataclass
class StopEvent:
    event: str = "stop"
    stream_sid: str = ""
    call_sid: str = ""
    account_sid: str = ""


# Convenience union type
TwilioEvent = (
    ConnectedEvent | StartEvent | MediaEvent | MarkEvent | DtmfEvent | StopEvent
)


# ── Parser ────────────────────────────────────────────────────────────────────

def parse(raw_text: str) -> Optional[TwilioEvent]:
    """
    Parse one raw JSON WebSocket frame from Twilio into a typed event.

    Returns None for unknown event types or malformed JSON.
    MediaEvent.payload is already base64-decoded to raw bytes.
    """
    try:
        msg = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None

    etype = msg.get("event")

    if etype == "connected":
        return ConnectedEvent(
            protocol=msg.get("protocol", ""),
            version=msg.get("version", ""),
        )

    if etype == "start":
        s = msg.get("start", {}) or {}
        mf = s.get("mediaFormat", {}) or {}
        return StartEvent(
            stream_sid=s.get("streamSid", msg.get("streamSid", "")),
            call_sid=s.get("callSid", ""),
            account_sid=s.get("accountSid", ""),
            tracks=list(s.get("tracks", []) or []),
            custom_parameters=s.get("customParameters", {}) or {},
            media_format=MediaFormat(
                encoding=mf.get("encoding", "audio/x-mulaw"),
                sample_rate=int(mf.get("sampleRate", 8000)),
                channels=int(mf.get("channels", 1)),
            ),
        )

    if etype == "media":
        media = msg.get("media", {}) or {}
        raw_b64 = media.get("payload", "")
        try:
            decoded = base64.b64decode(raw_b64)
        except Exception:
            decoded = b""
        return MediaEvent(
            stream_sid=msg.get("streamSid", ""),
            sequence_number=_to_int(msg.get("sequenceNumber", 0)),
            track=media.get("track", "inbound"),
            chunk=_to_int(media.get("chunk", 0)),
            timestamp_ms=_to_int(media.get("timestamp", 0)),
            payload=decoded,
        )

    if etype == "mark":
        mark = msg.get("mark", {}) or {}
        return MarkEvent(
            stream_sid=msg.get("streamSid", ""),
            name=mark.get("name", ""),
        )

    if etype == "dtmf":
        dtmf = msg.get("dtmf", {}) or {}
        return DtmfEvent(
            stream_sid=msg.get("streamSid", ""),
            track=dtmf.get("track", "inbound_track"),
            digit=dtmf.get("digit", ""),
        )

    if etype == "stop":
        stop = msg.get("stop", {}) or {}
        return StopEvent(
            stream_sid=msg.get("streamSid", stop.get("streamSid", "")),
            call_sid=stop.get("callSid", ""),
            account_sid=stop.get("accountSid", ""),
        )

    return None


def _to_int(v: Any) -> int:
    """Twilio sends numeric fields as strings; coerce safely."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
