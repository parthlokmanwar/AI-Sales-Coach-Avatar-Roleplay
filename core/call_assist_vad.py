"""
Energy-based Voice Activity Detector for Exotel 8 kHz μ-law stream.

No external dependencies — pure numpy + stdlib.

Exotel delivers 20ms chunks: 160 bytes of G.711 μ-law (8-bit, 8 kHz mono).
This module buffers those chunks, computes RMS energy on each, and emits a
completed utterance WAV when trailing silence is detected.

The emitted WAV is a standard μ-law WAV file (or linear PCM WAV if Exotel is
configured to send raw PCM). Both are passed unchanged to the existing
core/whisper_stt.get_engine().transcribe(wav_bytes, content_type="audio/wav")
— ffmpeg (already in the pipeline) decodes + resamples to 16 kHz for Whisper.

State machine:
  SILENT  →  PRIMING   once RMS > SPEECH_RMS for any chunk
  PRIMING →  SPEECH    after _PRE_SPEECH_CHUNKS consecutive speech chunks
  PRIMING →  SILENT    if a below-threshold chunk resets the prime window
  SPEECH  →  (emit)    after _TRAILING_SILENCE_CHUNKS consecutive silent chunks
  SPEECH  →  (emit)    if utterance exceeds VAD_MAX_DURATION_S (hard cap)

Configuration via environment variables (all have usable defaults):
  VAD_SPEECH_RMS           int   RMS amplitude to enter speech (default 400)
  VAD_SILENCE_RMS          int   RMS amplitude below which = silence (default 250)
  VAD_TRAILING_SILENCE_MS  int   ms of silence to end utterance (default 300)
  VAD_PRE_SPEECH_CHUNKS    int   consecutive chunks needed to confirm speech (default 2)
  VAD_MAX_DURATION_S       float hard utterance cap in seconds (default 12.0)
  VAD_MIN_DURATION_S       float minimum utterance to bother transcribing (default 0.4)

Usage:
    vad = UtteranceBuffer()
    for raw_bytes in media_events:   # 20ms μ-law chunks
        result = vad.feed(raw_bytes, encoding="mulaw")
        if result:
            # transcribe result["wav"] with Whisper
            pass
    # On call end, flush any pending speech
    result = vad.flush(encoding="mulaw")
"""

from __future__ import annotations

import io
import os
import struct
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import numpy as np


# ── Config ────────────────────────────────────────────────────────────────────

_SPEECH_RMS  = int(os.environ.get("VAD_SPEECH_RMS",  "400"))
_SILENCE_RMS = int(os.environ.get("VAD_SILENCE_RMS", "250"))

_TRAILING_SILENCE_CHUNKS = max(1, int(os.environ.get("VAD_TRAILING_SILENCE_MS", "300")) // 20)
_PRE_SPEECH_CHUNKS       = max(1, int(os.environ.get("VAD_PRE_SPEECH_CHUNKS",   "2")))

_MAX_DURATION_S = float(os.environ.get("VAD_MAX_DURATION_S", "12.0"))
_MIN_DURATION_S = float(os.environ.get("VAD_MIN_DURATION_S",  "0.4"))

# Exotel sends 20ms per media event at 8 kHz
_CHUNK_DURATION_S = 0.020
_CHUNK_SAMPLES    = 160   # 8000 Hz × 0.020s = 160 samples


# ── G.711 μ-law decoder ───────────────────────────────────────────────────────

def _mulaw_decode(data: bytes) -> np.ndarray:
    """Decode G.711 μ-law bytes → linear int16 PCM (±32768 range)."""
    u = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
    u = (~u) & 0xFF          # invert all bits (G.711 spec)
    sign = np.where(u & 0x80, -1, 1)
    exp  = (u >> 4) & 0x07
    mant = u & 0x0F
    magnitude = ((mant + 0x21) << (exp + 2)) - 0x21
    return (sign * magnitude).astype(np.int16)


def _chunk_rms(data: bytes, encoding: str) -> float:
    """Compute RMS amplitude for one audio chunk."""
    if not data:
        return 0.0
    if "mulaw" in encoding:
        linear = _mulaw_decode(data).astype(np.float32)
    else:
        # Linear int16 LE (Exotel can also send raw PCM)
        if len(data) % 2 != 0:
            data = data[:-1]
        linear = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(linear ** 2))) if linear.size else 0.0


# ── WAV packaging (minimal headers, ffmpeg-compatible) ───────────────────────

def _wrap_mulaw_wav(payload: bytes, sample_rate: int = 8000) -> bytes:
    """Wrap raw μ-law bytes in a WAV header. ffmpeg decodes + resamples."""
    n = len(payload)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + n))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I",  16))           # fmt chunk size
    buf.write(struct.pack("<H",   7))           # AudioFormat: MULAW (7)
    buf.write(struct.pack("<H",   1))           # NumChannels: mono
    buf.write(struct.pack("<I", sample_rate))   # SampleRate
    buf.write(struct.pack("<I", sample_rate))   # ByteRate (8-bit mono: 1 byte/sample)
    buf.write(struct.pack("<H",   1))           # BlockAlign
    buf.write(struct.pack("<H",   8))           # BitsPerSample
    buf.write(b"data")
    buf.write(struct.pack("<I", n))
    buf.write(payload)
    return buf.getvalue()


def _wrap_linear_wav(payload: bytes, sample_rate: int = 8000) -> bytes:
    """Wrap raw signed-int16-LE bytes in a WAV header."""
    n = len(payload)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + n))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I",  16))
    buf.write(struct.pack("<H",   1))           # PCM
    buf.write(struct.pack("<H",   1))           # mono
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * 2))   # ByteRate (16-bit mono)
    buf.write(struct.pack("<H",   2))           # BlockAlign
    buf.write(struct.pack("<H",  16))           # BitsPerSample
    buf.write(b"data")
    buf.write(struct.pack("<I", n))
    buf.write(payload)
    return buf.getvalue()


# ── VAD state machine ─────────────────────────────────────────────────────────

class _State(Enum):
    SILENT  = auto()
    PRIMING = auto()   # collecting pre-speech window before committing
    SPEECH  = auto()


@dataclass
class _VADState:
    state:         _State    = _State.SILENT
    buffer:        bytearray = field(default_factory=bytearray)  # confirmed speech
    prime_buf:     bytearray = field(default_factory=bytearray)  # candidate pre-speech
    prime_count:   int       = 0   # consecutive above-threshold chunks in PRIMING
    silence_count: int       = 0   # consecutive silent chunks in SPEECH
    speech_chunks: int       = 0   # total chunks accumulated in buffer
    sample_rate:   int       = 8000


class UtteranceBuffer:
    """
    Buffer and VAD for one Exotel call stream.

    feed(raw_bytes, encoding) → Optional[dict]
        Returns None while accumulating.
        Returns {"wav": bytes, "duration_s": float} on utterance completion.

    flush(encoding) → Optional[dict]
        Emit any in-progress utterance (call ended / stream stopped).
    """

    def __init__(self, sample_rate: int = 8000):
        self._s = _VADState(sample_rate=sample_rate)

    # ── Public interface ──────────────────────────────────────────────────────

    def feed(self, raw_bytes: bytes, encoding: str = "mulaw") -> Optional[dict]:
        """Feed one 20ms audio chunk. Returns utterance dict or None."""
        if not raw_bytes:
            return None

        rms        = _chunk_rms(raw_bytes, encoding)
        is_speech  = rms > _SPEECH_RMS
        is_silence = rms < _SILENCE_RMS
        s          = self._s

        if s.state == _State.SILENT:
            if is_speech:
                s.state       = _State.PRIMING
                s.prime_buf   = bytearray(raw_bytes)
                s.prime_count = 1
            return None

        if s.state == _State.PRIMING:
            s.prime_buf.extend(raw_bytes)
            if is_speech:
                s.prime_count += 1
                if s.prime_count >= _PRE_SPEECH_CHUNKS:
                    s.state        = _State.SPEECH
                    s.buffer       = bytearray(s.prime_buf)
                    s.speech_chunks = len(s.prime_buf) // len(raw_bytes)
                    s.silence_count = 0
                    s.prime_buf    = bytearray()
                    s.prime_count  = 0
            else:
                # Noise burst — discard prime window
                s.state       = _State.SILENT
                s.prime_buf   = bytearray()
                s.prime_count = 0
            return None

        # ── SPEECH state ──────────────────────────────────────────────────────
        s.buffer.extend(raw_bytes)
        s.speech_chunks += 1
        duration_s = s.speech_chunks * _CHUNK_DURATION_S

        s.silence_count = (s.silence_count + 1) if is_silence else 0

        emit = (
            s.silence_count >= _TRAILING_SILENCE_CHUNKS  # natural end-of-turn
            or duration_s   >= _MAX_DURATION_S            # hard cap
        )
        if emit:
            return self._emit(bytes(s.buffer), encoding, s.sample_rate)
        return None

    def flush(self, encoding: str = "mulaw") -> Optional[dict]:
        """Emit buffered speech on call end. Returns None if nothing buffered."""
        s = self._s
        if s.state == _State.SPEECH and s.buffer:
            return self._emit(bytes(s.buffer), encoding, s.sample_rate)
        self._reset()
        return None

    def reset(self):
        """Hard reset — call this at the start of a new call."""
        self._reset()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _reset(self):
        s = self._s
        s.state         = _State.SILENT
        s.buffer        = bytearray()
        s.prime_buf     = bytearray()
        s.prime_count   = 0
        s.silence_count = 0
        s.speech_chunks = 0

    def _emit(self, raw: bytes, encoding: str, sample_rate: int) -> Optional[dict]:
        """Wrap raw bytes in WAV, check minimum duration, return payload."""
        self._reset()
        if "mulaw" in encoding:
            duration_s = len(raw) / sample_rate   # 1 byte/sample for 8-bit μ-law
            if duration_s < _MIN_DURATION_S:
                return None
            wav = _wrap_mulaw_wav(raw, sample_rate=sample_rate)
        else:
            duration_s = len(raw) / 2 / sample_rate  # 2 bytes/sample for 16-bit PCM
            if duration_s < _MIN_DURATION_S:
                return None
            wav = _wrap_linear_wav(raw, sample_rate=sample_rate)
        return {"wav": wav, "duration_s": round(duration_s, 2)}
