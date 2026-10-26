"""
Query Rewriter — zero-latency, rule-based.

Pipeline position:
  User message
    → normalize_hinglish()     ← expand shorthand words
    → rewrite_query()          ← make standalone retrieval question from history
    → expand_query()           ← existing domain-keyword expansion
    → RAG retriever

Why this exists:
  Short conversational messages like "acha", "aur kuch", "who bat to acchi or kuch"
  have no keywords for the vector retriever. Without rewriting, retrieval returns
  random chunks unrelated to the ongoing conversation. The rewriter converts the
  short message into a full standalone question using the last few turns so the
  retriever gets something it can actually work with.

No LLM calls — pure Python dicts and regex. Adds < 1ms.
"""

from __future__ import annotations
import re
from typing import Optional


# ── Hinglish vocabulary ──────────────────────────────────────────────────────
# Maps common informal Hindi/Hinglish words to their English meaning.
# Used to enrich the retrieval query (not shown to the user in the response).
# Keep these unambiguous — short words with multiple meanings are excluded.

HINGLISH_VOCAB: dict[str, str] = {
    # Acknowledgements / "understood"
    "acha": "okay understood",
    "accha": "okay understood",
    "acchi": "good okay",
    "achha": "okay understood",
    "theek hai": "okay understood",
    "theek": "okay",
    "haan": "yes",
    "bilkul": "absolutely yes",
    "pakka": "confirmed sure",
    "sach mein": "really is that true",
    "sach": "really true",
    "okie": "okay",

    # Follow-up / more information
    "aur kuch": "anything else more information",
    "or kuch": "anything else more information",
    "kuch aur": "something more anything else",
    "aur batao": "tell me more explain further",
    "aur kya": "what else more details",
    "batao": "tell me explain",
    "bata": "tell me explain",
    "samjhao": "explain please",
    "samjha nahi": "did not understand please explain again simpler",
    "samajh nahi": "did not understand please explain again",
    "samjho": "explain",

    # Reference to previous topic
    "who bat": "that point that topic",
    "wo bat": "that point that topic",
    "woh bat": "that point that topic",
    "yeh wala": "this option this one",
    "woh wala": "that option that one",
    "uska": "its that one",
    "iska": "this one",

    # Question words
    "kya hai": "what is it",
    "kya hota hai": "what does it mean",
    "matlab": "meaning what does it mean",
    "matlab kya": "what does that mean",
    "matlab kya hai": "what does that mean",

    # Cost / amount
    "kitna lagega": "how much will it cost",
    "kitna milega": "how much will I get",
    "kitna hoga": "how much will it be",
    "kitna": "how much",

    # Process
    "kaise karu": "how do I do it",
    "kaise apply karu": "how do I apply",
    "kaise": "how",
    "kab": "when",
    "kyon": "why",
    "kyun": "why",

    # Documents
    "kya documents chahiye": "what documents are required",
    "documents kya kya": "what all documents are needed",
}

# Signals that the message is primarily a continuation / acknowledgement
# with a request for more information.
_MORE_INFO_SIGNALS = {
    "aur kuch", "or kuch", "kuch aur", "aur batao", "aur kya",
    "batao", "tell me more", "what else", "anything else", "more",
    "samjha nahi", "samajh nahi", "samjhao", "explain again",
}

_ACK_ONLY_SIGNALS = {"acha", "accha", "acchi", "achha", "okie", "ok", "okay",
                     "haan", "yes", "theek", "theek hai", "bilkul", "sach", "pakka"}


def normalize_hinglish(text: str) -> str:
    """
    Replace known Hinglish shorthand in `text` with their English equivalents.
    Multi-word phrases are matched first (longest match wins).
    Returns the enriched text — still readable by the retriever.
    """
    if not text:
        return text
    result = text.lower().strip()

    # Match longer phrases first to avoid partial replacements
    for phrase in sorted(HINGLISH_VOCAB, key=len, reverse=True):
        if phrase in result:
            result = result.replace(phrase, HINGLISH_VOCAB[phrase])

    return result


def _extract_last_topic(history: list) -> str:
    """
    Pull the subject of the last exchange from conversation history.
    Uses the last user question (most specific) with the last assistant
    answer's opening line as a fallback topic label.
    Returns empty string if history is empty.
    """
    if not history:
        return ""
    last = history[-1]
    last_q = (last.get("query") or "").strip()
    last_a = (last.get("answer") or "").strip()

    # Use last user question if it's substantive (> 4 words)
    if last_q and len(last_q.split()) > 4:
        return last_q

    # Fall back to the first sentence of the last assistant answer
    if last_a:
        first_sentence = re.split(r'[.!?]', last_a)[0].strip()
        if first_sentence:
            return first_sentence

    return last_q


def is_ambiguous(text: str) -> bool:
    """
    True when the message is too short or too vague to be a useful retrieval
    query on its own — i.e., it needs conversation context to make sense.
    """
    if not text:
        return True
    normalized = text.lower().strip()
    words = normalized.split()

    # Very short messages are almost always continuations
    if len(words) <= 3:
        return True

    # Messages that are ONLY acknowledgement words
    if all(w in _ACK_ONLY_SIGNALS for w in words):
        return True

    # Starts with an acknowledgement and asks for "more"
    if words[0] in _ACK_ONLY_SIGNALS and any(sig in normalized for sig in _MORE_INFO_SIGNALS):
        return True

    return False


def rewrite_query(question: str, history: Optional[list] = None) -> str:
    """
    Convert a short / ambiguous / Hinglish message into a standalone
    retrieval question using conversation history.

    Returns the rewritten query string (used only for retrieval — the
    original `question` is still what gets sent to the LLM prompt).

    Rules (in priority order):
      1. No history → return question as-is.
      2. Pure acknowledgement with no new content → "tell me more about [last topic]".
      3. Ambiguous message (short, vague) → prepend last topic to question.
      4. Message is clear enough → return as-is.
    """
    if not history:
        return question

    normalized = question.lower().strip()
    words = normalized.split()
    last_topic = _extract_last_topic(history)

    # Case 1: pure acknowledgement only — user said "acha" / "ok" / "haan"
    # with nothing else. They want continuation of the previous topic.
    if all(w in _ACK_ONLY_SIGNALS for w in words):
        if last_topic:
            return f"Tell me more about {last_topic}"
        return question

    # Case 2: acknowledgement + "more info" signal
    # e.g. "acha aur kuch", "okie who bat or kuch", "ok batao"
    has_ack = words[0] in _ACK_ONLY_SIGNALS or any(w in _ACK_ONLY_SIGNALS for w in words[:2])
    has_more = any(sig in normalized for sig in _MORE_INFO_SIGNALS)
    if has_ack and has_more and last_topic:
        return f"What else should I know about {last_topic}? Any additional rules, exceptions, or related policies?"

    # Case 3: ambiguous but not purely ack — short message or contains
    # topic-reference words ("who bat", "uska", etc.) without enough context
    if is_ambiguous(question) and last_topic:
        return f"{question} — related to {last_topic}"

    # Case 4: message is specific enough — use as-is
    return question
