"""
Pydantic request/response schemas — one place for all wire models.

Grouped by feature for easy navigation:
- Pitch (PitchRequest, PitchDownloadRequest)
- Chat / Ask Me (ChatRequest)
- History / Conversations (RenameRequest, ContinueRequest)
- Voice (TranscriptCleanRequest, VoiceAnswerRequest, SessionSummaryRequest)
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel


# ── Pitch ────────────────────────────────────────────────────────────────────

class PitchRequest(BaseModel):
    scenario: str
    language: str = "English"


class ServiceCallPitchRequest(BaseModel):
    scenario: str
    language: str = "English"


class PitchDownloadRequest(BaseModel):
    pitch: Dict[str, Any]


# ── Ask Me Chat ──────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None  # if provided, continue an existing chat session
    language: str = "English"
    voice: bool = False  # Eva 2.0 voice assistant: short, plain-speech, Eva persona


class KeyLearningsRequest(BaseModel):
    language: str = "English"  # active language toggle from the Chat Bot screen


# ── Pitch History / Conversations ────────────────────────────────────────────

class RenameRequest(BaseModel):
    name: str


class ContinueRequest(BaseModel):
    role: str = "user"
    content: str
    type: str = "chat"


# ── Voice / Live Assistant ───────────────────────────────────────────────────

class TranscriptCleanRequest(BaseModel):
    raw_text: str
    language: str = "english"


class VoiceAnswerRequest(BaseModel):
    question: str
    question_type: str = "factual"
    session_context: dict = {}
    history: list = []
    language: str = "english"
    labeled_transcript: Optional[str] = None  # Speaker 1/2 labeled transcript for GPT-4o-mini path


class SessionSummaryRequest(BaseModel):
    conversation: list
    session_context: dict = {}
