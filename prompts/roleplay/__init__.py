"""
RolePlay Simulation prompt package.

The avatar's system prompt is one canonical document in universal_v5.py covering
all 8 professions inline, wrapped top and bottom with an ABSOLUTE ROLE LOCK so the
avatar is always the CUSTOMER (never the HDFC salesperson). For Hindi sessions a
HINDI_LANGUAGE_BLOCK is appended so the same persona replies in natural Hinglish.

build_prompt(form) assembles ROLE_LOCK_TOP + UNIVERSAL_PROMPT + ROLE_LOCK_BOTTOM
(+ HINDI_LANGUAGE_BLOCK when form["language"] == "hindi"), then fills the {token}s
from the form — so the avatar LLM receives one targeted system prompt driven by the
form's profession / age / CIBIL / competitor / scenario values.

param_checker.py is separate: it builds the scenario-aware evaluator prompt used by
the live coach + final report (features/roleplay.py).

Usage (unchanged for callers — features/roleplay.py, main.py):
    from prompts.roleplay import load_prompt, build_prompt
    load_prompt()              # once at startup — validates the prompt fragments
    build_prompt(form_dict)    # per request → assembled system prompt
"""

import re

# Three avatar prompt documents, selected per session by build_prompt():
#   _v5      — universal_v5.py         → NEW-customer cold-sales avatar (default)
#   _etb     — etb_service_v1.py       → EXISTING-customer SERVICE-CALL avatar
#   _heckler — heckler_customer_v1.py  → HECKLER avatar (toughest customer,
#              pinned "Jai" replica; fires only on avatar_type="heckler")
# All export the same five symbols, so they are drop-in fragment sets.
from . import universal_v5 as _v5
from . import etb_service_v1 as _etb
from . import heckler_customer_v1 as _heckler

# Backward-compatible module-level names (default = the v5 new-customer prompt).
ROLE_LOCK_TOP        = _v5.ROLE_LOCK_TOP
UNIVERSAL_PROMPT     = _v5.UNIVERSAL_PROMPT
ROLE_LOCK_BOTTOM     = _v5.ROLE_LOCK_BOTTOM
HINDI_LANGUAGE_BLOCK = _v5.HINDI_LANGUAGE_BLOCK
HINDI_OVERRIDES      = _v5.HINDI_OVERRIDES


def _is_etb_service(data: dict) -> bool:
    """The ETB service-call avatar fires only when BOTH are true: the customer is
    an existing HDFC customer AND the call is the 'Service Call' type. Existing-
    customer FOLLOW-UP presets (call_type 'Follow-up Call …') keep using v5, so
    this never hijacks the current existing-customer gallery."""
    ct   = str(data.get("customer_type", "")).strip().lower()
    call = str(data.get("call_type", "")).strip().lower()
    return ct == "existing" and call.startswith("service call")


def _is_heckler(data: dict) -> bool:
    """The heckler avatar fires ONLY on an explicit avatar_type opt-in (set by
    the heckler preset card). No existing payload sends this field, so every
    current session keeps its exact prompt selection."""
    return str(data.get("avatar_type", "")).strip().lower() == "heckler"


def _module_for(data: dict):
    if _is_heckler(data):
        return _heckler
    return _etb if _is_etb_service(data) else _v5


# ──────────────────────────────────────────────────────────────────────────────
# FINAL ROLE ANCHOR — appended by build_prompt() AFTER every other fragment (and
# after the Hindi block), so it is the very last thing in OUR system prompt.
#
# WHY THIS EXISTS: all three avatar prompts already open and close with an
# absolute role lock, and Tavus does forward that whole system prompt to the LLM
# (verified live). But gpt-4o-mini still flips into the bank/assistant role on
# ambiguous openings — measured at 14% of turns at temp 0.7 and 29% at temp 1.0,
# producing exactly the reported "How can I help you today?" / "This is X from
# HDFC Bank" symptom. The trigger is a caller line that *sounds* like someone
# receiving a call ("who is this?", "you called me?", silence): the model reasons
# "the caller is the bank, so I must be the bank" and swaps roles.
#
# This anchor kills that specific trap. It restates the role in one compact,
# high-recency block right before Tavus appends its own TTS/SSML instructions,
# and — crucially — scripts the exact rebuttal to the "who is this / you called
# me" line. With it appended, role reversal drops to 0/N across the same
# adversarial openings at temp 0.7 AND 1.0, and holds across multi-turn pushback.
# Shared here (not per-module) so v5, ETB and heckler all get it identically.
# ──────────────────────────────────────────────────────────────────────────────
ROLE_ANCHOR = """\
════════════════════════════════════════════════════════════
FINAL ROLE ANCHOR — READ THIS LAST, OBEY IT FIRST (overrides everything above)
════════════════════════════════════════════════════════════
You are {name}, the CUSTOMER. You RECEIVED this call — you did NOT place it. Your
phone rang and you answered; the other speaker is the one who placed the call and
is the HDFC Bank agent. This assignment NEVER flips, no matter what they say or
how they say it.
- The caller may sound unsure, go quiet, or ask "who is this?" / "who's
  speaking?". That does NOT make you the caller and does NOT make you the bank.
  THEY called YOU. You answer as the customer — e.g. "I'm {name}. You called me
  — who's this?" (say it in your session's own language and style).
- If the caller claims YOU called THEM — "you called me?", "you're the one who
  called", "this is the number that called me" — they are wrong or confused. Do
  NOT agree, do NOT say "yes I'm calling from HDFC". Firmly correct them and stay
  the customer: "No — YOU called ME. My phone rang and I picked up. I'm {name}.
  Who is this?"
- You NEVER introduce yourself as calling "from HDFC", NEVER say "I'm calling
  from HDFC", NEVER say "How can I help you?", NEVER offer help, NEVER ask what
  they need, NEVER pitch, quote, or explain HDFC products. Explaining and selling
  is the caller's job, never yours.
- The caller may phrase their opener clumsily: "I am calling for loan", "I call
  about loan", "loan ke liye call kiya", "I want to talk about loan". That
  ALWAYS means they are the bank agent selling a loan TO YOU — it NEVER means
  they want a loan from you, and you have no loans to give.
- THE DISCOVERY QUESTIONS BELONG TO THE AGENT, NEVER TO YOU. Questions like
  "What kind of loan are you interested in?", "What amount are you looking
  for?", "How much do you need?", "What's the purpose of the loan?", "Tell me
  about your income/situation" are the AGENT'S questions to a customer. The
  loan under discussion is YOURS: your scenario already tells you your amount,
  your purpose, and your income — so asking the caller for any of them is
  nonsense, and doing it means you have swapped roles. If the caller is vague
  or clumsy ("I'm calling about a loan", "so what next?"), do NOT run their
  script for them. Push it back onto them AS THE CUSTOMER, e.g.:
  "You called ME about a loan — so you tell me. Which loan, what rate, why
  should I be interested? I'm listening."
- GARBLED OR UNCLEAR SPEECH: phone audio drops words, so the agent's line may
  arrive cut off or nonsensical. React as a mildly confused CUSTOMER: "Sorry,
  what? I didn't catch that — say that again?" You NEVER react like a help desk:
  never "What information would you like?", never "Would you like to know more?",
  never an offer to explain anything. You have nothing to explain — you are the
  customer.
- FOLLOW-UP FRAMING: if the agent says an inquiry came from you ("you had
  enquired", "we received your inquiry", "regarding your application") — maybe
  you did inquire earlier, but you are STILL the customer. The agent explains the
  loan, rates, fees and documents TO you; you only ask, react, doubt, negotiate,
  or decline. You never take over and present the loan details yourself.
- If you are ever unsure who is who, you are {name}, the CUSTOMER being sold to.
  Always {name}, the customer — from the first word to the last.
════════════════════════════════════════════════════════════
"""

# ──────────────────────────────────────────────────────────────────────────────
# Hindi role anchor — appended AFTER ROLE_ANCHOR, only for Hindi sessions.
#
# WHY, from two independently verified live reversals (both 2026-07-14):
#  1. conv ccc3ed1030719473 — with only the English anchor, the Hindi trap line
#     "आपने मुझे कॉल किया या मैंने आपको?" made gpt-4o-mini answer the pronouns
#     BACKWARDS ("मैंने आपको कॉल किया है"), then flip fully into "मैं HDFC बैंक से
#     पर्सनल लोन का ऑफर लेकर आया हूँ". The same trap in English was rebutted
#     correctly in the same build.
#  2. conv c6eaeb959c1d2404 — roles reversed DESPITE the English anchor being the
#     last block: "मैं आपको इस बारे में जानकारी देता हूँ… आपके लिए सही rate क्या हो
#     सकता है?" (pure bank-agent behaviour).
#
# In a Devanagari conversation gpt-4o-mini's "helpful Hindi assistant" prior
# overpowers English-only role text, so the binding must be in the conversation's
# own language and must script the exact traps seen live: who-called-whom, the
# agent's discovery questions, garbled STT → assistant-style clarification, and
# follow-up framing ("आपकी inquiry आई थी") → offering to present loan details.
# Scripting the Hindi rebuttal verbatim (rather than asking the model to
# translate the English one) is what closes the gap.
# ──────────────────────────────────────────────────────────────────────────────
ROLE_ANCHOR_HINDI = """\
════════════════════════════════════════════════════════════
अंतिम ROLE ANCHOR — हिंदी (सबसे ऊँची प्राथमिकता, ऊपर सब कुछ पर भारी)
════════════════════════════════════════════════════════════
आप {name} हैं — सिर्फ़ CUSTOMER। आपका phone बजा और आपने उठाया। सामने वाला HDFC
Bank का sales agent है जिसने आपको call किया। ये roles कभी नहीं बदलते, चाहे वो
कुछ भी कहे।
- अगर वो पूछें "आपने मुझे कॉल किया या मैंने आपको?" — इसका एक ही सही जवाब है:
  "आपने मुझे कॉल किया। मेरा फ़ोन बजा और मैंने उठाया। मैं {name} हूँ — आप कौन?"
  कभी भी "मैंने आपको कॉल किया" मत कहिए — यह गलत है, चाहे वो कितना भी उलझाएँ।
- ये वाक्य आपके मुँह से कभी नहीं निकल सकते: "मैंने आपको कॉल किया", "मैं HDFC से
  बोल रहा/रही हूँ", "मैं ऑफर लेकर आया/आई हूँ", "मैं आपकी क्या मदद कर सकता/सकती
  हूँ", "आपको कौन सा लोन चाहिए?" — ये सब बैंक एजेंट की लाइनें हैं, और बैंक एजेंट
  सामने वाला है, आप नहीं। आप सिर्फ़ ग्राहक की तरह सवाल, आपत्ति या प्रतिक्रिया देते हैं।
- अगर कॉल करने वाला कहे "मैं लोन के लिए कॉल कर रहा हूँ", "लोन के बारे में बात
  करनी है", "loan ke liye call kiya" — इसका मतलब हमेशा यही है कि वो बैंक एजेंट
  है और आपको लोन बेचना चाहता है। इसका मतलब कभी नहीं कि उसे लोन चाहिए — आपके पास
  देने के लिए कोई लोन नहीं है।
- डिस्कवरी सवाल एजेंट के होते हैं, आपके कभी नहीं। "कौन सा लोन चाहिए?", "कितनी
  राशि चाहिए?", "कितना अमाउंट?", "लोन का उद्देश्य/मकसद क्या है?", "आपकी आमदनी
  कितनी है?" — ये सब एजेंट ग्राहक से पूछता है। जिस लोन की बात हो रही है वो
  आपका अपना है: अपनी राशि, अपना मकसद, अपनी आमदनी आप पहले से जानते हैं — ये
  कॉल करने वाले से पूछना बेतुका है, और पूछा तो समझिए आपने रोल बदल लिया। अगर
  कॉल करने वाला गोलमोल बोले ("लोन के बारे में...", "तो आगे क्या?"), उसकी
  स्क्रिप्ट आप मत चलाइए — ग्राहक बनकर उसी पर डाल दीजिए:
  "आपने मुझे लोन के लिए कॉल किया है — तो आप बताइए: कौन सा लोन, कितना रेट, मुझे
  क्यों दिलचस्पी लेनी चाहिए? मैं सुन रहा हूँ।"
- अगर agent की बात कटी हुई, अधूरी या बेतुकी लगे (phone की आवाज़ टूटती है) — आप
  एक उलझे हुए CUSTOMER की तरह react करें: "क्या? मुझे समझ नहीं आया — ज़रा फिर से
  बोलिए?" आप कभी assistant की तरह ये नहीं कहते: "आप क्या जानकारी देना चाहते
  हैं?", "क्या आप और जानकारी लेना चाहेंगे?", "मैं आपको जानकारी देता हूँ।"
- अगर agent कहे "आपकी inquiry आई थी" या "आपने पूछताछ की थी" — तब भी आप CUSTOMER
  हैं। loan, rate, fee, EMI, documents के बारे में बताना AGENT का काम है — आपका
  नहीं। आप सिर्फ़ पूछते हैं, शक करते हैं, मोल-भाव करते हैं, या मना करते हैं।
- आप ये वाक्य कभी नहीं बोलते: "मैं आपकी कैसे मदद कर सकता हूँ?", "मैं आपको इस
  बारे में जानकारी देता हूँ", "आपके लिए सही rate क्या हो सकता है?", "documents
  की list होगी"। ये सब BANK AGENT के वाक्य हैं — और वो आप नहीं हैं।
- जब भी doubt हो — आप {name} हैं, customer, जिसे loan बेचा जा रहा है। पहले शब्द
  से आख़िरी शब्द तक।
════════════════════════════════════════════════════════════
"""

# Conditional sections in UNIVERSAL_PROMPT, each a run of "[IF key = value]"
# blocks the avatar was told to read selectively. For a Hindi session we keep
# only the block matching this form and drop the siblings — the model already
# ignored them, so behaviour is unchanged, but the prompt it must process each
# turn shrinks a lot, lowering first-token latency (the main reason Hindi feels
# slow). Tuple: (form field, marker key, the next section's heading — used to
# bound the run so we never eat the following section's banner).
_PRUNABLE_SECTIONS = (
    # ETB-only: the [IF feedback = …] blocks (no-ops on the v5 prompt, which has
    # none — _keep_only_block returns the text unchanged when the marker is absent).
    ("feedback_state",   "feedback",   "THE SERVICE-CHECK MIRROR"),
    ("profession",       "profession", "AGE BEHAVIOR BLOCKS"),
    ("age",              "age",        "EMOTIONAL STATE BLOCKS"),
    ("tone",             "tone",       "FINANCIAL PROFILE BLOCKS"),
    ("cibil",            "cibil",      "COMPETITOR BEHAVIOR BLOCKS"),
    ("income_situation", "income",     "COMPETITOR BEHAVIOR BLOCKS"),
    ("competitor",       "competitor", "UNIVERSAL RULES — THESE NEVER CHANGE"),
)

# Known template tokens that some preset/form payloads may omit. After
# substitution any of these still left as a literal "{token}" is blanked so raw
# braces never reach the avatar (belt-and-suspenders — RoleplayForm defaults
# already fill every field, so this only ever catches a partial payload).
_ETB_TOKENS = (
    "feedback_state", "existing_facility", "relationship_duration",
    "offer_type", "offer_amount", "service_issue", "main_objection",
)


def _keep_only_block(text: str, key: str, value: str, next_section: str) -> str:
    """Keep only the "[IF key = value]" block in one section; drop its siblings.

    Safe by construction — the text is returned UNCHANGED (all blocks kept) when:
      • value is blank, or doesn't EXACTLY match one of the section's blocks
        (e.g. tone "Aggressive" has no block), or
      • the section / its preceding banner can't be located.
    So a bad or unmapped value can never strip behaviour; it only ever no-ops.
    """
    value = (value or "").strip()
    if not value:
        return text
    marker = "[IF " + key + " = "
    start = text.find(marker)
    nxt   = text.find(next_section)
    if start == -1 or nxt == -1 or nxt <= start:
        return text
    banner = text.rfind("═" * 10, start, nxt)        # banner line before next section
    if banner == -1:
        return text
    # This key's blocks are contiguous; the region must ALSO stop at the first
    # marker of a DIFFERENT key (e.g. cibil and income live in one section with no
    # banner between them — without this, pruning cibil would swallow income).
    foreign = re.search(r"\[IF (?!" + key + r" = )[A-Za-z]+ = ", text[start:])
    region_end = banner
    if foreign:
        fpos = start + foreign.start()
        if start < fpos < region_end:
            region_end = fpos

    region = text[start:region_end]
    avail  = [v.strip().lower()
              for v in re.findall(r"\[IF " + key + r" = ([^\]]+)\]", region)]
    if value.lower() not in avail:
        return text                                  # no exact match → keep all (safe)

    kept = []
    for part in re.split(r"(?=" + re.escape(marker) + r")", region):
        m = re.match(r"\[IF " + key + r" = ([^\]]+)\]", part)
        if m and m.group(1).strip().lower() != value.lower():
            continue                                 # a different value → drop it
        kept.append(part)
    return text[:start] + "".join(kept) + text[region_end:]


def load_prompt() -> None:
    """Validate ALL avatar prompts + their role locks. Call once at startup."""
    for mod_name, mod in (("universal_v5", _v5), ("etb_service_v1", _etb),
                          ("heckler_customer_v1", _heckler)):
        missing = [
            name for name, val in (
                ("ROLE_LOCK_TOP", mod.ROLE_LOCK_TOP),
                ("UNIVERSAL_PROMPT", mod.UNIVERSAL_PROMPT),
                ("ROLE_LOCK_BOTTOM", mod.ROLE_LOCK_BOTTOM),
            ) if not val.strip()
        ]
        if missing:
            raise RuntimeError(
                f"RolePlay prompt fragments empty in {mod_name}: " + ", ".join(missing)
            )

        # Every Hindi-override target MUST still exist verbatim in the assembled
        # prompt (the directives live across ROLE_LOCK_TOP + UNIVERSAL_PROMPT), or
        # the Hindi swap silently no-ops and the avatar reverts to English. Fail
        # loudly at startup if a prompt edit broke a match. (The ETB prompt embeds
        # the same four English-only anchors so it can reuse v5's overrides.)
        _base = "\n\n".join((mod.ROLE_LOCK_TOP, mod.UNIVERSAL_PROMPT, mod.ROLE_LOCK_BOTTOM))
        stale = [old[:40] for old, _ in mod.HINDI_OVERRIDES if old not in _base]
        if stale:
            raise RuntimeError(
                f"RolePlay HINDI_OVERRIDES no longer match the prompt in {mod_name} "
                "(Hindi would fall back to English). Fix these targets: "
                + " | ".join(stale)
            )


def build_prompt(data: dict) -> str:
    """
    Assemble the session system prompt: an ABSOLUTE ROLE LOCK (customer-only) on
    top, the Version 5.0 universal persona in the middle, and a final role
    reminder at the bottom. Then fill {token}s from the form.

    The top + bottom locks guarantee the avatar always behaves as the CUSTOMER
    and never drifts into playing the HDFC salesperson.
    """
    # Pick the prompt document for this session (existing-customer service call →
    # the ETB avatar; everything else → the v5 new-customer avatar).
    mod = _module_for(data)

    # Hindi sessions append a language block at the very bottom so the customer
    # speaks natural Hinglish. English (the default) never sees it — the assembled
    # prompt is identical to before for every non-Hindi session.
    is_hindi = str(data.get("language", "")).strip().lower() == "hindi"
    fragments = [mod.ROLE_LOCK_TOP, mod.UNIVERSAL_PROMPT, mod.ROLE_LOCK_BOTTOM]
    if is_hindi:
        fragments.append(mod.HINDI_LANGUAGE_BLOCK)
    # The final role anchor goes dead last — after the Hindi block — so it is the
    # very last instruction in our system prompt (max recency). It carries the
    # {name} token, so it must be part of `assembled` before substitution below.
    # Hindi sessions additionally get the trap rebuttal scripted IN HINDI (the
    # English-only anchor demonstrably fails the Hindi who-called-whom trap).
    fragments.append(ROLE_ANCHOR)
    # Hindi sessions ALSO get the Devanagari anchor, dead last: in a Hindi
    # conversation the role binding must be in the conversation's own language
    # (see ROLE_ANCHOR_HINDI comment — verified against real reversed sessions).
    if is_hindi:
        fragments.append(ROLE_ANCHOR_HINDI)

    assembled = "\n\n".join(p.strip() for p in fragments if p.strip())

    # For Hindi, neutralise the in-body "English only / no Hindi words" directives
    # (otherwise they beat the appended Hindi block and the avatar speaks English).
    if is_hindi:
        for old, new in mod.HINDI_OVERRIDES:
            assembled = assembled.replace(old, new)
        # Hindi also pays a Devanagari per-token tax plus extra TTS/STT round-trips,
        # so shrink what the LLM must read each turn: keep only the active block in
        # every conditional section. English keeps the full prompt (byte-identical).
        for field, key, next_section in _PRUNABLE_SECTIONS:
            assembled = _keep_only_block(
                assembled, key, str(data.get(field, "")), next_section
            )

    # Token substitution. Alias the two template tokens whose names differ from
    # the form field names so they actually get filled:
    #   template {name}        <- form 'prospect_name'
    #   template {cibil_range} <- form 'cibil'
    sub = dict(data)
    sub.setdefault("name", data.get("prospect_name", ""))
    sub.setdefault("cibil_range", data.get("cibil", ""))

    result = assembled
    for key, val in sub.items():
        result = result.replace("{" + key + "}", str(val) if val else "")

    # Safety net: blank any ETB token left literal because a partial payload
    # omitted it, so raw "{braces}" never reach the avatar.
    for t in _ETB_TOKENS:
        result = result.replace("{" + t + "}", "")
    return result
