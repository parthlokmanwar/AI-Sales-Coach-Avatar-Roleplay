"""
Shared backend dependencies.

Imported by every feature router. Single source of truth for:
- The RAG instance (`rag_instance`) — singleton wrapping EnhancedRAGv4
- Logging (`log`) — used by middleware + endpoints
- File-system paths (uploads folder, pitch history file)
- Helpers that don't belong to a single feature: language detection,
  metadata extraction, pitch-history JSON file I/O, conversation migration.

Feature routers should NEVER instantiate their own EnhancedRAGv4 —
always import `rag_instance` from here.
"""

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from app import EnhancedRAGv4

# ── Logging (configured once in main.py; we just expose the logger) ───────────

log = logging.getLogger("hdfc")

# ── Singletons ───────────────────────────────────────────────────────────────

rag_instance = EnhancedRAGv4(model="llama3.2:3b", base_url="http://localhost:11434")

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
UPLOADS_FOLDER = ROOT / "uploads"
HISTORY_FILE = ROOT / "pitch_history.json"
CHAT_HISTORY_FILE = ROOT / "chat_history.json"  # Chat Bot sessions (ChatGPT-style)

# ── Per-company RAG registry (multi-tenant document isolation) ────────────────
# Each company gets its OWN RAGEngine loading only uploads/<company_id>/, so a
# company's pitch/chat answers are grounded strictly in its own documents. The
# embedder is shared (see VectorMatcher.shared()), so extra instances are cheap.
#
# The global `rag_instance` above remains the legacy/shared corpus (uploads/
# root) used by super admins and the live-call/voice features, which are not yet
# tenant-scoped.

import threading as _threading

SUPPORTED_DOC_EXTS = {".csv", ".xlsx", ".xls", ".pdf", ".docx", ".pptx", ".txt"}

_rag_registry: Dict[str, "EnhancedRAGv4"] = {}
_rag_lock = _threading.Lock()


def company_uploads_dir(company_id: str) -> Path:
    return UPLOADS_FOLDER / company_id


def _load_folder_into(rag, folder: Path) -> int:
    """Ingest every supported file in `folder` into the given RAG engine."""
    if not folder.exists():
        return 0
    loaded = 0
    for fp in sorted(folder.iterdir()):
        if fp.is_file() and fp.suffix.lower() in SUPPORTED_DOC_EXTS:
            try:
                if rag.add_file(str(fp), fp.name).get("success"):
                    loaded += 1
            except Exception as e:  # never let one bad file break the load
                log.warning(f"[RAG] {folder.name}/{fp.name} failed: {e}")
    return loaded


def get_rag(company_id: Optional[str]):
    """Return the RAGEngine for a company, lazily building + loading it once.

    company_id None (super admin / non-tenant live features) → global instance.
    """
    if not company_id:
        return rag_instance
    with _rag_lock:
        rag = _rag_registry.get(company_id)
        if rag is None:
            from app import EnhancedRAGv4
            rag = EnhancedRAGv4(model="llama3.2:3b", base_url="http://localhost:11434")
            n = _load_folder_into(rag, company_uploads_dir(company_id))
            log.info(f"[RAG] built corpus for company {company_id}: {n} docs")
            _rag_registry[company_id] = rag
        return rag


def reload_company_rag(company_id: str):
    """Drop a company's cached engine so the next get_rag rebuilds from disk
    (call after an admin uploads or deletes a document)."""
    with _rag_lock:
        _rag_registry.pop(company_id, None)
    return get_rag(company_id)

# ── Language detection ───────────────────────────────────────────────────────

_KNOWN_LANGUAGES = {"english", "hinglish", "marathi_mix", "hindi", "marathi"}


def detect_language(text: str, frontend_hint: str = "english") -> str:
    """Return the language key that downstream prompts use.

    The agent's selected language always governs the reply — no auto-switching.
    All recognized language hints are returned as-is so Devanagari input never
    silently overrides an explicit 'marathi' or 'hindi' selection.
    """
    if frontend_hint in _KNOWN_LANGUAGES:
        return frontend_hint
    # Fallback for unrecognized hints: Devanagari script → Hindi
    if re.search(r'[ऀ-ॿ]', text or ""):
        return 'hindi'
    return frontend_hint


def sanitize_meta(meta: dict) -> dict:
    """Make a Pydantic-safe copy of file metadata (no dtype objects, etc.)."""
    clean: dict = {}
    for k, v in meta.items():
        if isinstance(v, dict):
            clean[k] = {str(dk): str(dv) for dk, dv in v.items()}
        elif isinstance(v, (list, str, int, float, bool)) or v is None:
            clean[k] = v
        else:
            clean[k] = str(v)
    return clean


# ── Pitch-history JSON I/O ───────────────────────────────────────────────────

def load_history() -> List[Dict]:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_history(history: List[Dict]):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ── Time helpers ─────────────────────────────────────────────────────────────

def iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def short_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %b")
    except Exception:
        return datetime.utcnow().strftime("%d %b")


# ── Scenario parsing + conversation-name generation ──────────────────────────

def extract_metadata_from_scenario(scenario: str) -> Dict[str, str]:
    """Parse the structured scenario string produced by the Quick Form.
    Falls back gracefully for Free Write scenarios (free text)."""
    if not scenario:
        return {}
    meta: Dict[str, str] = {}
    patterns = {
        "customerName": r"Customer\s*:\s*([^\n]+)",
        "product":      r"Product(?:/Service)?\s*:\s*([^\n]+)",
        "meetingType":  r"Meeting\s*Type\s*:\s*([^\n]+)",
        "customerProfile": r"Customer\s*Profile\s*:\s*([^\n]+)",
        "goal":         r"Goal\s*:\s*([^\n]+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, scenario, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                meta[key] = val
    if not meta.get("product"):
        lower = scenario.lower()
        for prod_kw, prod_name in [
            ("home loan", "Home Loan"),
            ("business loan", "Business Loan"),
            ("personal loan", "Personal Loan"),
            ("loan against property", "LAP"),
            ("lap ", "LAP"),
            ("credit card", "Credit Card"),
        ]:
            if prod_kw in lower:
                meta["product"] = prod_name
                break
    return meta


def generate_default_name(metadata: Dict[str, str], iso_ts: str) -> str:
    """Auto-generate a conversation name: {Product} — {Customer} — {Date}."""
    product = metadata.get("product") or "Pitch"
    customer = metadata.get("customerName")
    meeting = metadata.get("meetingType")
    date_str = short_date(iso_ts)
    if customer:
        return f"{product} — {customer} — {date_str}"
    if meeting:
        return f"{product} — {meeting} — {date_str}"
    return f"{product} — {date_str}"


# ── Conversation migration (legacy → new schema) ─────────────────────────────

def migrate_entry(e: Dict) -> Dict:
    """Convert an old pitch-history entry into the new conversation format.
    Idempotent: a fully-migrated entry is returned unchanged."""
    if isinstance(e.get("messages"), list) and "pitch_data" in e:
        e.setdefault("created_at", e.get("timestamp") or iso_now())
        e.setdefault("updated_at", e.get("created_at"))
        e.setdefault("metadata", {})
        e.setdefault("documents_used", [])
        if not e.get("name"):
            e["name"] = generate_default_name(e.get("metadata", {}), e["created_at"])
        return e

    ts = e.get("timestamp") or iso_now()
    scenario = e.get("scenario", "")
    pitch_data = e.get("pitch", {}) or {}
    metadata = extract_metadata_from_scenario(scenario)
    metadata["language"] = e.get("language", "English")
    opening = pitch_data.get("opening_hook") or pitch_data.get("narrative_summary") or ""

    return {
        "id": e.get("id") or str(uuid.uuid4()),
        "name": e.get("name") or generate_default_name(metadata, ts),
        "created_at": ts,
        "updated_at": ts,
        "messages": [
            {"role": "user",      "content": scenario, "type": "scenario", "timestamp": ts},
            {"role": "assistant", "content": opening,  "type": "pitch",    "timestamp": ts},
        ],
        "pitch_data": pitch_data,
        "metadata": metadata,
        "documents_used": e.get("documents_used", []),
    }


def load_conversations() -> List[Dict]:
    """Load history and migrate any legacy entries to the conversation format."""
    raw = load_history()
    migrated = [migrate_entry(e) for e in raw]
    if migrated and (raw != migrated):
        save_history(migrated)
    return migrated


def find_conversation(history: List[Dict], conv_id: str) -> Optional[Dict]:
    for c in history:
        if c.get("id") == conv_id:
            return c
    return None


def upsert_conversation(history: List[Dict], conv: Dict) -> List[Dict]:
    """Replace by id if exists, else prepend. Returns the new list."""
    out = [c for c in history if c.get("id") != conv.get("id")]
    return [conv] + out


def conversation_preview(conv: Dict) -> str:
    """First 100 chars of the pitch opening hook (or first assistant message)."""
    pitch = conv.get("pitch_data") or {}
    hook = pitch.get("opening_hook") or ""
    if not hook:
        for m in conv.get("messages", []):
            if m.get("role") == "assistant" and m.get("content"):
                hook = m["content"]
                break
    return (hook[:100] + "…") if len(hook) > 100 else hook


# ── Chat Bot sessions (parallel to pitch history, but for /api/chat) ─────────

def load_chat_sessions() -> List[Dict]:
    """Load all Chat Bot sessions from chat_history.json."""
    if CHAT_HISTORY_FILE.exists():
        try:
            with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_chat_sessions(sessions: List[Dict]):
    with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def find_chat_session(sessions: List[Dict], sid: str) -> Optional[Dict]:
    for s in sessions:
        if s.get("id") == sid:
            return s
    return None


def upsert_chat_session(sessions: List[Dict], session: Dict) -> List[Dict]:
    """Replace by id if exists, else prepend."""
    out = [s for s in sessions if s.get("id") != session.get("id")]
    return [session] + out


def auto_name_chat_session(first_user_msg: str) -> str:
    """Auto-name a chat session from its first user question."""
    text = (first_user_msg or "").strip().replace("\n", " ")
    return text or "New chat"


def chat_session_preview(session: Dict) -> str:
    """Preview = first 80 chars of the most-recent assistant message (or first user message)."""
    msgs = session.get("messages", []) or []
    for m in reversed(msgs):
        if m.get("role") == "assistant" and m.get("content"):
            txt = m["content"].strip().replace("\n", " ")
            return (txt[:80] + "…") if len(txt) > 80 else txt
    if msgs and msgs[0].get("content"):
        txt = msgs[0]["content"].strip().replace("\n", " ")
        return (txt[:80] + "…") if len(txt) > 80 else txt
    return ""
