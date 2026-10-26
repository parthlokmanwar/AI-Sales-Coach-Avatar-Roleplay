"""
core/report_builder.py
──────────────────────
RolePlay call-report builder.

Two responsibilities:
  1. fetch_tavus_transcript(session_id) — pull the finished transcript for a
     completed Tavus conversation and flatten it into a single labelled string.
  2. analyse_transcript(...)          — send that transcript to OpenRouter and
     return the structured report JSON the frontend renders.

Lives in core/ alongside tavus_client.py and llm.py so it shares the same
conventions (env-driven keys, httpx for the OpenRouter call, requests-in-thread
for Tavus). Nothing here touches the existing roleplay launch flow.
"""

import os
import json
import asyncio
import logging

import httpx
import requests

from core.tavus_accounts import get_accounts

log = logging.getLogger("hdfc")

TAVUS_BASE = "https://tavusapi.com/v2"

# Capable JSON model first, then free fallbacks. Each is tried in order until
# one returns parseable JSON — mirrors the resilience pattern used elsewhere.
OPENROUTER_MODELS = [
    os.getenv("REPORT_MODEL", "openai/gpt-4o-mini"),
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat:free",
]


ANALYSIS_PROMPT_TEMPLATE = """You are an expert sales trainer evaluating a practice sales call.
The salesperson ("Rep") is an HDFC Bank sales executive practicing a loan pitch.
The "Customer" is an AI avatar playing a simulated prospect.

Analyse the salesperson's (Rep) performance ONLY. Do not score or comment on the
Customer/avatar lines.

TRANSCRIPT:
{transcript}

PROSPECT PROFILE:
Name: {prospect_name}
Profession: {profession}
Loan Type: {loan_type}
Scenario: {scenario}

SCORING PARAMETERS (score each 0-10):
1. tone_professionalism  - calm, respectful, never rude or dismissive
2. product_knowledge     - correct HDFC facts (rates, EMI, tenor, fees, Xpressway, disbursal)
3. objection_handling    - acknowledge first, empathy, resolve not dodge
4. listening_empathy     - responds to what customer said, picks up on mood, personalises
5. pitch_structure       - proper opening, discovery before pitch, logical sequence
6. closing_technique     - clear next step, trial close, does not let call drift
7. language_communication- clear language, no unexplained jargon, adapts to customer type

MOMENT TAGS for each Rep line: GOOD | NEEDS WORK | CRITICAL ERROR | NEUTRAL

Return ONLY valid JSON. No preamble. No markdown fences.
{{
  "overall_score": <0-100 integer>,
  "overall_grade": "<A|B|C|D|F>",
  "call_duration": "<MM:SS>",
  "parameters": {{
    "tone_professionalism":   {{ "score": <0-10>, "verdict": "<one sentence>" }},
    "product_knowledge":      {{ "score": <0-10>, "verdict": "<one sentence>" }},
    "objection_handling":     {{ "score": <0-10>, "verdict": "<one sentence>" }},
    "listening_empathy":      {{ "score": <0-10>, "verdict": "<one sentence>" }},
    "pitch_structure":        {{ "score": <0-10>, "verdict": "<one sentence>" }},
    "closing_technique":      {{ "score": <0-10>, "verdict": "<one sentence>" }},
    "language_communication": {{ "score": <0-10>, "verdict": "<one sentence>" }}
  }},
  "lines": [
    {{
      "timestamp": "<HH:MM:SS>",
      "speaker": "<Rep|Customer>",
      "text": "<exact transcript line>",
      "tag": "<GOOD|NEEDS WORK|CRITICAL ERROR|NEUTRAL|null>",
      "parameter": "<parameter name or null>",
      "comment": "<one sentence or null>"
    }}
  ],
  "best_moments": [
    {{ "timestamp": "<HH:MM:SS>", "quote": "<rep line>", "reason": "<why good>" }}
  ],
  "worst_moments": [
    {{ "timestamp": "<HH:MM:SS>", "quote": "<rep line>", "reason": "<why bad>" }}
  ],
  "coaching_notes": ["<specific point>", "<specific point>", "<specific point>"],
  "overall_verdict": "<4-6 sentence manager assessment>"
}}

Rules:
- best_moments: exactly 3 items
- worst_moments: exactly 3 items
- coaching_notes: 3-5 items, specific to this call not generic
- Every Rep line must have a tag and comment
- Customer lines: tag=null, comment=null
- overall_grade: A=90+, B=75-89, C=60-74, D=45-59, F=below 45
- A factually wrong HDFC claim = always CRITICAL ERROR
- A rude or dismissive Rep line = always CRITICAL ERROR
"""


# ── Tavus transcript fetch ────────────────────────────────────────────────────

def _tavus_headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _format_turns(turns: list) -> str:
    """
    Turn a list of transcript entries into a single labelled string:

        [00:00:05] Rep: Hello, am I speaking with Rajesh?
        [00:00:08] Customer: Yes, who is this?

    In this roleplay the human sales executive speaks as the Tavus
    participant ("user") and the avatar customer is the persona ("assistant").
    """
    lines = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = (turn.get("role") or turn.get("speaker") or "").lower()
        text = (turn.get("content") or turn.get("text") or
                turn.get("transcript") or "").strip()
        if not text:
            continue
        if role in ("user", "rep", "human", "participant"):
            speaker = "Rep"
        elif role in ("assistant", "replica", "persona", "customer", "avatar", "bot"):
            speaker = "Customer"
        else:
            speaker = role.capitalize() or "Speaker"
        ts = turn.get("timestamp") or turn.get("time") or turn.get("start") or ""
        prefix = f"[{ts}] " if ts else ""
        lines.append(f"{prefix}{speaker}: {text}")
    return "\n".join(lines)


def _extract_transcript(data: dict) -> str:
    """
    Tavus has returned several transcript shapes over its API versions. Try the
    known ones in turn and return a flattened, labelled string. Returns "" when
    no transcript is present yet (call still processing).
    """
    if not isinstance(data, dict):
        return ""

    # 1) Plain string at top level.
    t = data.get("transcript")
    if isinstance(t, str) and t.strip():
        return t.strip()

    # 2) List of turn dicts at top level.
    if isinstance(t, list) and t:
        return _format_turns(t)

    # 3) Nested under "conversation".
    conv = data.get("conversation")
    if isinstance(conv, dict):
        nested = _extract_transcript(conv)
        if nested:
            return nested

    # 4) List of turns under common keys.
    for key in ("turns", "messages", "utterances"):
        turns = data.get(key)
        if isinstance(turns, list) and turns:
            return _format_turns(turns)

    # 5) Verbose conversations expose transcripts inside the events feed.
    events = data.get("events")
    if isinstance(events, list):
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("event_type") or ev.get("type") or ""
            if "transcript" not in str(etype).lower():
                continue
            props = ev.get("properties") or ev.get("data") or {}
            payload = props.get("transcript") if isinstance(props, dict) else None
            if isinstance(payload, str) and payload.strip():
                return payload.strip()
            if isinstance(payload, list) and payload:
                return _format_turns(payload)

    return ""


def _fetch_conversation_sync(api_key: str, session_id: str) -> "dict | None":
    """Fetch one conversation with ONE account's key.

    Returns the conversation dict, or None when this account doesn't own the
    conversation (Tavus answers 400/404 for an id belonging to another account,
    exactly as it does for an unknown id — the two are indistinguishable).
    """
    # verbose=true asks Tavus to include the events feed (where the transcript
    # lives for finished conversations).
    response = requests.get(
        f"{TAVUS_BASE}/conversations/{session_id}",
        headers=_tavus_headers(api_key),
        params={"verbose": "true"},
        timeout=30,
    )
    if response.status_code in (400, 404):
        return None
    response.raise_for_status()
    return response.json()


async def fetch_tavus_transcript(session_id: str, preferred_account: str = "") -> str:
    """Fetch and flatten the transcript for a completed Tavus conversation.

    ACCOUNT-AWARE (this is load-bearing, not a nicety): conversations are owned
    by the ACCOUNT that created them, and RolePlay launches fall over to the next
    configured account when the primary is at its concurrency/credit cap. Asking
    account A for a conversation created on account B gets a 400 — which reads
    exactly like "not ready yet", so the report silently died for every call
    served by a fallback account. We therefore try the owning account first and
    sweep the rest (the owner is unknown after a backend restart).

    preferred_account — name from the launch-time registry; tried first.
    """
    accounts = get_accounts()
    if not accounts:
        raise ValueError(
            "No Tavus account configured. Set TAVUS_ACCOUNTS (name:key,name:key) "
            "or TAVUS_API_KEY in your .env file."
        )
    if preferred_account:
        accounts.sort(key=lambda a: a[0] != preferred_account)

    found_on = ""
    for name, api_key in accounts:
        data = await asyncio.to_thread(_fetch_conversation_sync, api_key, session_id)
        if data is None:
            continue                      # not this account's conversation
        found_on = name
        transcript = _extract_transcript(data)
        if transcript:
            if name != preferred_account:
                log.info("[ROLEPLAY] transcript for %s found on account '%s'",
                         session_id, name)
            return transcript
        break                             # owner found, transcript not ready yet

    if found_on:
        raise ValueError(
            "Tavus has no transcript for this session yet. The call may still be "
            "in progress or Tavus is still processing it — wait a few seconds and "
            "try again."
        )
    raise ValueError(
        "Could not find a completed call for this session yet. The call may "
        "still be in progress, or Tavus has not finished processing it — wait "
        "a few seconds after the call ends and try again."
    )


# ── OpenRouter analysis ───────────────────────────────────────────────────────

def _strip_json_fences(content: str) -> str:
    """Drop ```json ... ``` fences some models wrap their output in."""
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
        content = content.strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    return content


async def analyse_transcript(
    transcript: str,
    prospect_name: str,
    profession: str,
    loan_type: str,
    scenario: str,
) -> dict:
    """
    Send the transcript to OpenRouter and return the structured report dict.
    Tries each model in OPENROUTER_MODELS until one returns parseable JSON.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set. Add it to your .env file.")

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        transcript=transcript,
        prospect_name=prospect_name or "—",
        profession=profession or "—",
        loan_type=loan_type or "—",
        scenario=scenario or "—",
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://posiviewedge.internal",
        "X-Title": "PitchFlow AI Report",
    }

    last_error = None
    async with httpx.AsyncClient(timeout=90) as client:
        for model in OPENROUTER_MODELS:
            if not model:
                continue
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 6000,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
                if response.status_code in (400, 404, 429):
                    # 400 can mean the model rejected response_format — retry it
                    # once without the json hint before moving on.
                    if response.status_code == 400:
                        response = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json={
                                "model": model,
                                "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": 6000,
                                "temperature": 0.2,
                            },
                        )
                        if response.status_code >= 400:
                            last_error = f"{model}: HTTP {response.status_code}"
                            continue
                    else:
                        last_error = f"{model}: HTTP {response.status_code}"
                        continue

                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                report = json.loads(_strip_json_fences(content))
                log.info(f"[ROLEPLAY] report generated via {model}")
                return report

            except (json.JSONDecodeError, KeyError) as e:
                last_error = f"{model}: {e}"
                continue
            except Exception as e:
                last_error = f"{model}: {e}"
                continue

    raise RuntimeError(
        f"All OpenRouter models failed for report generation. Last error: {last_error}"
    )
