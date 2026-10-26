"""
Ask Me chat post-processing — pure functions.

The Arjun prompt forbids robotic openers, but smaller models still leak them.
This is the safety net applied to every chat answer before it goes back to
the frontend.

Single stage:
  1. Strip openers like "As per the document", "Based on the policy", etc.

Bullets, numbered steps, and Markdown tables are now the DESIRED format
(prompt RULE 5 asks for ChatGPT-style structure and the frontend renders
Markdown), so the old bullet→prose rewrite and the 6-sentence trim were
removed on purpose.
"""

import re


def user_asked_for_list(question: str) -> bool:
    """Detect whether the user explicitly asked for a list/checklist."""
    q = (question or "").lower()
    list_markers = [
        "list", "checklist", "step by step", "steps", "bullets", "in points",
        "point wise", "pointwise", "summarise as", "in a table",
        "documents needed", "documents required",  # naturally itemised
    ]
    return any(m in q for m in list_markers)


_OPENER_PATTERN = re.compile(
    r'^(as per[^,.\n]*[,.]?\s*|'
    r'according to[^,.\n]*[,.]?\s*|'
    r'based on (the )?document[s]?[^,.\n]*[,.]?\s*|'
    r'(the )?document(s)? (state[s]?|say[s]?|mention[s]?|show[s]?)[^,.\n]*[,.]?\s*|'
    r'per (the )?policy[^,.\n]*[,.]?\s*|'
    r'as mentioned in[^,.\n]*[,.]?\s*)',
    re.IGNORECASE,
)


def post_process_chat(answer: str, user_asked_for_list_flag: bool = False) -> str:
    """Strip robotic openers. `user_asked_for_list_flag` is kept for API
    compatibility with existing callers; structure is no longer rewritten."""
    if not answer:
        return answer
    text = answer.strip()

    text = _OPENER_PATTERN.sub('', text).strip()
    if text:
        text = text[0].upper() + text[1:]

    return text.strip()
