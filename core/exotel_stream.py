"""
Exotel Stream Applet WebSocket event parser.

Exotel's "Stream Applet" follows the Twilio Media Streams protocol.
It opens a WebSocket to our backend and sends JSON messages:

  connected  — initial handshake, no audio
  start      — call metadata: stream_sid, call_sid, custom_parameters,
                media_format (encoding, sampleRate, channels)
  media      — 20ms audio chunk: base64-encoded μ-law PCM, 8 kHz mono
  dtmf       — DTMF key-press from caller
  stop       — call ended / stream closed

Audio format defaults (Exotel Stream Applet):
  encoding:    audio/x-mulaw  (G.711 μ-law, 8-bit per sample)
  sampleRate:  8000 Hz
  channels:    1 (mono)

This module ONLY parses events into typed dataclasses — no buffering,
no transcoding.  See core/call_assist_vad.py for utterance buffering and
features/call_assist.py for the full pipeline.

Public API:
  event = parse(raw_text)   # returns typed dataclass or None
  isinstance(event, MediaEvent)  # dispatch on type
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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
    custom_parameters: Dict[str, Any] = field(default_factory=dict)
    media_format: MediaFormat = field(default_factory=MediaFormat)


@dataclass
class MediaEvent:
    event: str = "media"
    stream_sid: str = ""
    sequence_number: int = 0
    track: str = "inbound"   # "inbound" = caller / "outbound" = agent
    chunk: int = 0
    timestamp_ms: int = 0
    payload: bytes = b""     # already base64-decoded raw bytes


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
ExotelEvent = ConnectedEvent | StartEvent | MediaEvent | DtmfEvent | StopEvent


# ── Parser ────────────────────────────────────────────────────────────────────

def parse(raw_text: str) -> Optional[ExotelEvent]:
    """
    Parse one raw JSON WebSocket message from Exotel into a typed event.

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
        s = msg.get("start", {})
        mf = s.get("mediaFormat", {})
        return StartEvent(
            stream_sid=s.get("streamSid", msg.get("streamSid", "")),
            call_sid=s.get("callSid", ""),
            account_sid=s.get("accountSid", ""),
            custom_parameters=s.get("customParameters", {}),
            media_format=MediaFormat(
                encoding=mf.get("encoding", "audio/x-mulaw"),
                sample_rate=int(mf.get("sampleRate", 8000)),
                channels=int(mf.get("channels", 1)),
            ),
        )

    if etype == "media":
        media = msg.get("media", {})
        raw_b64 = media.get("payload", "")
        try:
            decoded = base64.b64decode(raw_b64)
        except Exception:
            decoded = b""
        return MediaEvent(
            stream_sid=msg.get("streamSid", ""),
            sequence_number=int(msg.get("sequenceNumber", 0)),
            track=media.get("track", "inbound"),
            chunk=int(media.get("chunk", 0)),
            timestamp_ms=int(media.get("timestamp", 0)),
            payload=decoded,
        )

    if etype == "dtmf":
        dtmf = msg.get("dtmf", {})
        return DtmfEvent(
            stream_sid=msg.get("streamSid", ""),
            track=dtmf.get("track", "inbound_track"),
            digit=dtmf.get("digit", ""),
        )

    if etype == "stop":
        stop = msg.get("stop", {})
        return StopEvent(
            stream_sid=msg.get("streamSid", stop.get("streamSid", "")),
            call_sid=stop.get("callSid", ""),
            account_sid=stop.get("accountSid", ""),
        )

    return None
