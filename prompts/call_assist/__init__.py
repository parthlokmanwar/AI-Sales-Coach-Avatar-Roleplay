"""
PROMPT: Call Assistant — real-time agent co-pilot.

Called from features/call_assist.py immediately after Whisper transcribes the
customer's live utterance.  The agent reads the output on their screen while
still on the call — they then adapt the wording and speak it.

Design principles:
- NOT the Vikram persona and NOT the Arjun persona.  This is a neutral, fast
  factual co-pilot that whispers grounded facts to the agent.
- 2-3 sentences ONLY.  The agent cannot scroll; they need one scannable block.
- Grounded strictly in the retrieved HDFC documents (GROUNDING_RULE).
- Language-aware: match the customer's language so the agent can read and
  speak naturally without mentally translating.
- Separate KEY FACTS line for the quick-glance fact strip in the UI.

Output format (the LLM must follow this exactly):
  <2-3 sentence suggestion>

  KEY FACTS: <comma-separated list of 1-3 numbers/facts, or "none">

Inputs:
  transcript    — Whisper output (customer's last utterance)
  context       — top-k RAG chunks from loaded HDFC documents
  recent_turns  — last 3-4 turns as "Customer: ...\nSuggestion: ..."
  language      — "english" | "hinglish" | "hindi"
"""

from prompts.grounding import GROUNDING_RULE


_LANG_LINES = {
    "hindi":    "Reply in Hindi. Keep all numbers and product terms (EMI, CIBIL, HDFC, GST) in English.",
    "hinglish": "Reply in Hinglish — natural Hindi/English mix the agent can speak on the call. "
                "E.g. 'Processing fee 1% hoti hai, maximum Rs. 50,000.' No Devanagari script.",
    "english":  "Reply in clear, conversational Indian English.",
}


def build_call_assist_prompt(
    *,
    transcript: str,
    context: str,
    recent_turns: str = "",
    language: str = "english",
) -> str:
    """
    Build the call-assist suggestion prompt.

    transcript:    customer's last utterance (Whisper output)
    context:       top-k RAG chunks from loaded HDFC documents
    recent_turns:  formatted history string, e.g.:
                     "Customer: What is processing fee?
                      Suggestion: Processing fee 1% hoti hai..."
    language:      "english" | "hinglish" | "hindi"
    """
    lang_line = _LANG_LINES.get(language, _LANG_LINES["english"])

    recency_section = ""
    if recent_turns.strip():
        recency_section = f"\nRECENT CALL TURNS:\n{recent_turns}\n"

    return f"""You are a real-time co-pilot for an HDFC Bank sales agent on a live call.
The customer just said: "{transcript}"{recency_section}

{GROUNDING_RULE}

HDFC DOCUMENT CONTEXT:
{context}

TASK — give the agent a spoken hint they can read and say RIGHT NOW.

Rules:
- 2-3 sentences ONLY. No headers, no bullet points, no markdown.
- {lang_line}
- Use ONLY facts from DOCUMENT CONTEXT. Never invent rates, fees, percentages, or amounts.
- Do NOT start with "As per", "According to", "Based on", "Here is", or any preamble.
- Numbers naturally: "10 lakh" not "1000000", "10.5 percent" not "0.105".
- If the context has no relevant answer: reply exactly —
  "Not in documents — please confirm with the branch or product team."

After the suggestion, output exactly one blank line, then:
KEY FACTS: <1-3 specific numbers or facts from the answer, comma-separated — or "none">

Agent's suggestion:"""
