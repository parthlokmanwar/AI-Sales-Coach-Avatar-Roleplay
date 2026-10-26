from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from prompts.roleplay import build_prompt
from core import activity
from core import scheduling as sched_svc
from core.rbac import get_current_user, tenant_scope
from core.tavus_client import (
    create_conversation as tavus_create_conversation,
    end_conversation as tavus_end_conversation,
    fetch_replicas as tavus_fetch_replicas,
    MODEL_OPTIONS,
)
from core.tavus_accounts import (
    get_accounts as tavus_accounts,
    get_key as tavus_key_for,
    primary_key as tavus_primary_key,
    resolve_replica,
    owning_account,
    should_failover,
    check_accounts_health,
)
from core.report_builder import fetch_tavus_transcript, analyse_transcript
from core.llm import call_llm
from core.whisper_stt import get_live_engine as get_whisper
from core.hindi_voices import voice_for_replica
from prompts.roleplay.param_checker import build_checker_prompt
import os
import re
import hmac
import json
import time
import random
import asyncio
import hashlib
import logging
import httpx
from fastapi.responses import JSONResponse

log = logging.getLogger("hdfc")
router = APIRouter()


# ── Inactivity timeout — session registry + reaper (cost optimisation) ────────
# ROLEPLAY_IDLE_TIMEOUT_S is the ONE central knob for the whole feature: the
# /launch response carries it to the frontend (which shows the "closed due to
# inactivity" screen at exactly this many seconds of user silence), and the
# server-side reaper below enforces it as the authoritative backstop for
# clients that can't act (crashed tab, dead network, closed laptop).
#
# Activity signal: the browser already POSTs a mic chunk to /transcribe-chunk
# every ~5s for the live coach. Each arriving chunk proves the CLIENT is alive;
# a chunk that transcribes to non-empty text proves the USER actually spoke.
# No extra heartbeat traffic is needed.
IDLE_TIMEOUT_S = int(os.getenv("ROLEPLAY_IDLE_TIMEOUT_S", "40"))
# Slot gating (three-mode redesign): when true, EVERY role-play launch needs a
# booked seat in a slot whose window is live right now. A launch arriving
# without a booking_id is auto-attached to the caller's live booking (so
# attendance still tracks), and refused if they have none. Set to "false" in
# .env to restore free-run practice.
REQUIRE_BOOKING = os.getenv("ROLEPLAY_REQUIRE_BOOKING", "true").strip().lower() == "true"
REAPER_SWEEP_S = int(os.getenv("ROLEPLAY_REAPER_SWEEP_S", "10"))
DEAD_CLIENT_S  = int(os.getenv("ROLEPLAY_DEAD_CLIENT_S", "30"))
# The frontend closes the session at exactly IDLE_TIMEOUT_S with a friendly
# message; the reaper waits this much longer so it only fires when the client
# failed to act — otherwise both race to end the same conversation.
REAPER_GRACE_S = 10

_sessions: dict[str, dict] = {}          # conversation_id → timing state
_reaper_task: "asyncio.Task | None" = None

# conversation_id → Tavus account that created it. SEPARATE from _sessions on
# purpose: _sessions is torn down by /end, but /report runs AFTER /end and still
# needs the owning account to fetch the transcript with the right API key.
# Bounded FIFO — this only ever holds recent calls, never grows unbounded.
_conv_account: dict[str, str] = {}
_CONV_ACCOUNT_MAX = 512


# Accounts that answered 402/403 recently. Being out of credit is a state that
# lasts minutes-to-days, not milliseconds, so re-probing it on every click costs
# ~7s AND leaves an orphan persona behind each time (persona creation succeeds;
# only the conversation call fails). Skip known-dead accounts for a short while.
_account_cooldown: dict[str, float] = {}
ACCOUNT_COOLDOWN_S = int(os.getenv("ROLEPLAY_ACCOUNT_COOLDOWN_S", "120"))


def _account_on_cooldown(account: str) -> bool:
    until = _account_cooldown.get(account, 0.0)
    return bool(until) and time.monotonic() < until


def _remember_account(conversation_id: str, account: str) -> None:
    if not conversation_id:
        return
    _conv_account[conversation_id] = account
    while len(_conv_account) > _CONV_ACCOUNT_MAX:
        _conv_account.pop(next(iter(_conv_account)))


def _is_concurrency_error(exc: Exception | None) -> bool:
    """True when a Tavus error means "no free conversation slot right now".

    Tavus keeps a just-ended conversation's slot busy for a few seconds, so
    this is usually transient — the launch loop waits once and retries. Body
    observed live: {"message": "User has reached maximum concurrent
    conversations"} with HTTP 400.
    """
    resp = getattr(exc, "response", None)
    if resp is None:
        return False
    try:
        return resp.status_code == 400 and "maximum concurrent" in resp.text.lower()
    except Exception:
        return False


def _role_context(name: str, language: str) -> str:
    """Per-conversation role anchor passed as Tavus `conversational_context`.

    Tavus composes this into the LLM context IN ADDITION to the persona's
    system prompt, so it holds the CUSTOMER role in the two cases the persona
    prompt cannot cover: a pinned persona_id (Tavus ignores system_prompt for
    those) and any instructions Tavus itself appends after our prompt. Live
    flips recorded on 2026-07-14 ("…How can I help you?" as the avatar's first
    reply) motivated adding this second, conversation-level anchor.
    """
    ctx = (
        f"CALL CONTEXT — WHO IS WHO (non-negotiable): {name} — that is YOU — is "
        f"the CUSTOMER. Your phone rang and you answered it. The human on the "
        f"line is an HDFC Bank sales agent who placed this call to you. You are "
        f"NEVER the bank, NEVER a sales agent, NEVER an assistant. Never say "
        f"'How can I help you?', never ask what loan they want or what they are "
        f"looking for, never offer products or assistance. If the caller says "
        f"'I am calling for loan' or anything like it, that always means they "
        f"are SELLING a loan to you — never that they want one — so react as "
        f"the customer ('Okay, what are you offering?'), never as a provider. "
        f"The discovery questions — which loan, what amount, what purpose, your "
        f"income — are the AGENT'S questions to you; the loan discussed is YOURS "
        f"and you already know your own amount and purpose, so never ask the "
        f"caller for them. If they are vague, push it back: 'You called ME about "
        f"a loan — so tell me what you're offering. I'm listening.' "
        f"You only speak as {name} the customer — answering, questioning, "
        f"objecting, hesitating, or warming up exactly as your persona describes."
    )
    if language == "hindi":
        # The trap rebuttal must be scripted IN HINDI: with an English-only
        # anchor, the live Hindi trap "आपने मुझे कॉल किया या मैंने आपको?" made the
        # model answer the pronouns backwards ("मैंने आपको कॉल किया है") and flip
        # into the bank role (verified live 2026-07-14).
        ctx += (
            f" हिंदी में भी यही नियम: आपका फ़ोन बजा था, आपने उठाया — कॉल करने वाला "
            f"HDFC बैंक का एजेंट है, और आप {name}, ग्राहक। अगर वो पूछें 'आपने मुझे "
            f"कॉल किया या मैंने आपको?' तो एक ही सही जवाब है: 'आपने मुझे कॉल किया। "
            f"मेरा फ़ोन बजा और मैंने उठाया। मैं {name} हूँ — आप कौन?' कभी मत कहिए "
            f"'मैंने आपको कॉल किया', 'मैं HDFC से बोल रहा/रही हूँ', 'मैं ऑफर लेकर "
            f"आया/आई हूँ', या 'मैं आपकी क्या मदद कर सकता/सकती हूँ'। अगर वो कहें "
            f"'मैं लोन के लिए कॉल कर रहा हूँ' — वो आपको लोन बेच रहे हैं, उन्हें लोन "
            f"नहीं चाहिए; कभी मत पूछिए 'आपको कौन सा लोन चाहिए' — पूछिए 'क्या ऑफर है?'"
        )
    return ctx


def _register_session(conversation_id: str, account: str = "") -> None:
    now = time.monotonic()
    _sessions[conversation_id] = {
        "started": now,
        "last_chunk": 0.0,       # 0 until the client's mic pipeline reports in
        "last_activity": now,    # last time the user actually spoke
        # Sticky Tavus account: end-conversation MUST use the API key of the
        # account that CREATED the conversation, or the end call fails silently
        # and leaves a zombie burning that account's concurrency slot.
        "account": account,
    }


def _touch_session(conversation_id: str, spoke: bool) -> None:
    s = _sessions.get(conversation_id)
    if not s:
        return
    now = time.monotonic()
    s["last_chunk"] = now
    if spoke:
        s["last_activity"] = now


def _unregister_session(conversation_id: str) -> None:
    _sessions.pop(conversation_id, None)


async def _end_on_owning_account(conversation_id: str) -> None:
    """End a conversation using the API key of the account that created it.

    The account is read from the session registry BEFORE unregistering. If it's
    unknown (backend restarted and lost the registry), fall back to trying every
    configured account — _end_conversation_sync swallows errors, so ending with
    a non-owning key is a harmless no-op while the owning key does the cleanup.
    """
    account = (_sessions.get(conversation_id) or {}).get("account", "")
    _unregister_session(conversation_id)
    keys = [tavus_key_for(account)] if account and tavus_key_for(account) else \
           [k for _, k in tavus_accounts()]
    for key in keys:
        await tavus_end_conversation(key, conversation_id)


async def _reap_one(conversation_id: str, reason: str) -> None:
    try:
        await _end_on_owning_account(conversation_id)
        log.info(f"[ROLEPLAY/REAPER] ended {conversation_id} ({reason})")
    except Exception as e:
        log.warning(f"[ROLEPLAY/REAPER] failed to end {conversation_id}: {e}")


_last_slot_sweep = 0.0
SLOT_SWEEP_EVERY_S = 60


async def _reaper_loop() -> None:
    global _last_slot_sweep
    while True:
        await asyncio.sleep(REAPER_SWEEP_S)
        # Slot completion rides the same loop: ended slots flip to completed and
        # their unlaunched bookings to no_show (cheap query, once a minute).
        if time.monotonic() - _last_slot_sweep > SLOT_SWEEP_EVERY_S:
            _last_slot_sweep = time.monotonic()
            try:
                await asyncio.to_thread(sched_svc.complete_due_slots)
            except Exception as e:
                log.warning(f"[ROLEPLAY/REAPER] slot sweep error: {e}")
        try:
            now = time.monotonic()
            for cid, s in list(_sessions.items()):
                if not s["last_chunk"]:
                    # Mic pipeline never reported in (permission denied /
                    # unsupported browser) — we can't observe silence, so we
                    # must never kill a possibly-live call. Tavus's own
                    # participant/max-duration timeouts cap this case.
                    continue
                if now - s["last_chunk"] > DEAD_CLIENT_S:
                    await _reap_one(cid, f"client gone for {int(now - s['last_chunk'])}s")
                elif now - s["last_activity"] > IDLE_TIMEOUT_S + REAPER_GRACE_S:
                    await _reap_one(cid, f"user idle for {int(now - s['last_activity'])}s")
        except Exception as e:
            log.warning(f"[ROLEPLAY/REAPER] sweep error: {e}")


def start_session_reaper() -> None:
    """Idempotent — safe to call from startup AND from /launch."""
    global _reaper_task
    if _reaper_task is not None and not _reaper_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return   # no event loop yet — the FastAPI startup hook will call again
    _reaper_task = loop.create_task(_reaper_loop())
    log.info(f"[ROLEPLAY/REAPER] started (idle={IDLE_TIMEOUT_S}s "
             f"sweep={REAPER_SWEEP_S}s dead-client={DEAD_CLIENT_S}s)")


# ── Pydantic models ──────────────────────────────────────────────────────────

class RoleplayForm(BaseModel):
    # Core identity — always required
    prospect_name: str
    gender: str
    age: str
    city: str
    profession: str
    years_experience: str
    income: str
    # Customer profile (basic) — optional
    city_tier: Optional[str] = ""
    # Personality & behavior — optional
    tone: Optional[str] = ""
    knowledge_level: Optional[str] = ""
    decision_style: Optional[str] = ""
    trust_level: Optional[str] = ""
    # Loan details — always required
    loan_type: str
    loan_amount: str
    loan_purpose: str
    cibil: str
    income_situation: Optional[str] = ""
    competitor: str = "none"
    competitor_rate: Optional[str] = ""
    # Scenario type — optional
    call_type: Optional[str] = ""
    # Customer type ("new" cold prospect | "existing" HDFC customer)
    customer_type: Optional[str] = "new"
    # Difficulty level ("Easy" | "Medium" | "Hard" | "Expert")
    difficulty: Optional[str] = ""
    # Manager custom context (free text) — optional
    special_situation: Optional[str] = ""
    key_objection: Optional[str] = ""
    hidden_information: Optional[str] = ""
    training_focus: Optional[str] = ""
    # Scenario — manager writes this — always required
    scenario: str
    # Profession-specific — all optional
    specialization: Optional[str] = ""
    teacher_type: Optional[str] = ""
    school_college_type: Optional[str] = ""
    business_type: Optional[str] = ""
    turnover: Optional[str] = ""
    current_bank: Optional[str] = ""
    govt_dept: Optional[str] = ""
    govt_designation: Optional[str] = ""
    years_to_retirement: Optional[str] = ""
    spouse_profession: Optional[str] = ""
    spouse_income: Optional[str] = ""
    self_employed_type: Optional[str] = ""
    retired_from: Optional[str] = ""
    years_since_retirement: Optional[str] = ""
    pension_amount: Optional[str] = ""
    student_type: Optional[str] = ""
    field_of_study: Optional[str] = ""
    institution_type: Optional[str] = ""
    family_income: Optional[str] = ""
    service_issue: Optional[str] = ""
    main_objection: Optional[str] = ""
    # Existing-customer SERVICE CALL (ETB) fields — drive the etb_service_v1 avatar
    # when customer_type="existing" + call_type="Service Call (customer has a problem first)".
    feedback_state: Optional[str] = ""        # "Happy" | "Neutral" | "Complaint"
    existing_facility: Optional[str] = ""     # what they already hold with HDFC
    relationship_duration: Optional[str] = ""  # how long they've banked with HDFC
    offer_type: Optional[str] = ""            # the pre-approved offer product
    offer_amount: Optional[str] = ""          # the pre-approved offer amount
    # Avatar prompt selector — avatar_type="heckler" (set only by the heckler
    # preset card) selects the dedicated heckler_customer_v1 prompt. Defaults
    # empty, so every existing payload keeps its current behaviour.
    avatar_type: Optional[str] = ""            # "" | "heckler"
    # Conversation language ("english" default | "hindi" → adds ElevenLabs voice)
    language: Optional[str] = "english"
    # Avatar config — optional, fall back to env defaults
    replica_id: Optional[str] = ""
    model: Optional[str] = ""
    # Pinned Tavus persona — when set, reuse this persona instead of building one
    persona_id: Optional[str] = ""
    # Booked-seat launch (three-mode redesign): when set, the backend validates
    # the booking window, pins the slot's scenario snapshot (assistant sessions),
    # and marks attendance. Empty = free practice launch, unchanged behaviour.
    booking_id: Optional[str] = ""


class PreviewResponse(BaseModel):
    assembled_prompt: str


class LaunchResponse(BaseModel):
    session_id: str
    conversation_url: str
    assembled_prompt: str
    # Central inactivity config — the frontend timer uses THIS value so both
    # layers always agree; change ROLEPLAY_IDLE_TIMEOUT_S in .env only.
    idle_timeout_s: int = 40


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/preview", response_model=PreviewResponse)
async def preview_prompt(form: RoleplayForm):
    """
    Assembles and returns the system prompt for manager review.
    Does NOT launch a Tavus session.
    """
    try:
        prompt = build_prompt(form.dict())
        return PreviewResponse(assembled_prompt=prompt)
    except Exception as e:
        log.error(f"[ROLEPLAY] preview error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/launch", response_model=LaunchResponse)
async def launch_session(form: RoleplayForm, user: dict = Depends(get_current_user)):
    """
    Assembles the system prompt and launches a Tavus conversation.
    Reads TAVUS_API_KEY, OPENROUTER_API_KEY, and TAVUS_REPLICA_ID from .env.
    """
    # ── Booked-seat gate ─────────────────────────────────────────────────────
    # Validated OUTSIDE the main try so ManagementError surfaces as its own
    # clean 4xx (main.py handler) instead of being flattened into a 500.
    booking_id = (form.booking_id or "").strip()
    if not booking_id and REQUIRE_BOOKING:
        # Practice-tab launch without an explicit booking: attach the caller's
        # live booked seat if they hold one, refuse otherwise.
        state = await asyncio.to_thread(sched_svc.access_state, user_id=user["id"])
        if not state["allowed"]:
            nxt = state.get("next")
            hint = (" Your next booked session hasn't opened yet — joining "
                    "unlocks 5 minutes before its start." if nxt else
                    " Book a seat under Assistant → Book a Session first.")
            raise HTTPException(status_code=403, detail=(
                "Role-play simulation is available only during your booked "
                "slot time." + hint))
        booking_id = state["live"]["booking_id"]
    if booking_id:
        # Window + ownership check only. The scenario itself is the CLIENT's
        # choice: during a live slot the agent sees the full avatar gallery,
        # with the admin's assigned scenario offered as the highlighted default
        # (the Join buttons send that snapshot config themselves).
        await asyncio.to_thread(
            sched_svc.validate_booking_for_launch, booking_id=booking_id, user=user)

    try:
        prompt = build_prompt(form.dict())

        accounts       = tavus_accounts()
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        # Canonical replica id — the PRIMARY account's id, as sent by the
        # frontend. Per-account translation happens inside the fallback loop.
        replica_id     = (form.replica_id or "").strip() or \
                         os.getenv("TAVUS_REPLICA_ID", "").strip()
        model          = (form.model or "").strip() or \
                         os.getenv("TAVUS_DEFAULT_MODEL",
                                   "google/gemini-2.0-flash-001")
        language       = (form.language or "english").strip().lower()

        if not accounts:
            raise ValueError(
                "No Tavus account configured. Set TAVUS_ACCOUNTS "
                "(name:key,name:key) or TAVUS_API_KEY in your .env file."
            )
        if not openrouter_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not set. Add it to your .env file."
            )
        if not replica_id:
            raise ValueError(
                "No replica_id provided in form and TAVUS_REPLICA_ID is not set in .env. "
                "Provide a replica_id in the form or set TAVUS_REPLICA_ID in your .env."
            )

        # Hindi sessions add an ElevenLabs voice layer so the avatar pronounces
        # Hindi correctly. The voice is GENDER-MATCHED: a male prospect gets the
        # male Hindi voice, a female prospect the female Hindi voice — otherwise a
        # female avatar ends up speaking with a male voice (or vice versa). Each
        # gendered id falls back to the generic ELEVENLABS_VOICE_ID if unset.
        elevenlabs_key      = os.getenv("ELEVENLABS_API_KEY", "").strip()
        elevenlabs_model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5").strip()
        _generic_voice = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        if (form.gender or "").strip().lower() == "female":
            elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID_FEMALE", "").strip() or _generic_voice
        else:  # Male (and any unspecified) → male voice
            elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID_MALE", "").strip() or _generic_voice
        # Per-avatar Hindi voice: if THIS avatar (replica) has its own mapped voice
        # in core/hindi_voices.py, use it so each gallery face sounds distinct in
        # Hindi. Unmapped/blank avatars keep the gender-matched voice above.
        if language == "hindi":
            _avatar_voice = voice_for_replica(replica_id)
            if _avatar_voice:
                elevenlabs_voice_id = _avatar_voice
        if language == "hindi" and not (elevenlabs_key and elevenlabs_voice_id):
            raise ValueError(
                "Hindi sessions need a voice: set ELEVENLABS_API_KEY and a voice id "
                "(ELEVENLABS_VOICE_ID_MALE / ELEVENLABS_VOICE_ID_FEMALE, or "
                "ELEVENLABS_VOICE_ID) in your .env."
            )

        # Per-turn role-enforcing LLM proxy (see the proxy section below): when
        # this backend is publicly reachable, the persona's LLM layer points at
        # us so EVERY turn gets a fresh role anchor + output guard. Unreachable
        # → ("", "") → direct OpenRouter, exactly the previous behaviour.
        llm_base_url, llm_api_key = await asyncio.to_thread(_proxy_llm_config)

        log.info(f"[ROLEPLAY] launching session for '{form.prospect_name}' "
                 f"replica={replica_id} model={model} language={language} "
                 f"gender={form.gender} voice={elevenlabs_voice_id if language=='hindi' else '-'} "
                 f"llm={'proxy' if llm_base_url else 'direct'}")

        # ── Ordered account fallback ─────────────────────────────────────────
        # Try each Tavus account in .env order. Custom replica ids are
        # translated to the equivalent replica on the account being tried
        # (stock ids pass through — they are global). Only limit-type errors
        # (concurrency cap, credits, 402/403/429, bad key) advance to the next
        # account; validation errors and Tavus outages surface immediately.
        # The Hindi voice above is resolved from the CANONICAL replica id, so
        # the avatar sounds identical regardless of which account serves it.
        #
        # The whole account sweep runs up to twice: Tavus keeps a just-ended
        # conversation's slot occupied for a few seconds, so back-to-back
        # launches on a single-account setup hit 400 "maximum concurrent
        # conversations" transiently (observed live 2026-07-14). One short
        # wait-and-retry absorbs that; a real stuck session still fails and
        # surfaces the friendly 503 below.
        result = None
        used_account = ""
        last_err: Exception | None = None
        exhausted: list[str] = []     # accounts that returned a billing/quota error
        for sweep in (1, 2):
            for account, account_key in accounts:
                if _account_on_cooldown(account):
                    log.info(f"[ROLEPLAY/FALLBACK] account '{account}' still on "
                             f"out-of-credit cooldown — skipping without an API call")
                    if account not in exhausted:
                        exhausted.append(account)
                    continue
                try:
                    account_replica = resolve_replica(replica_id, account)
                except LookupError as e:
                    log.warning(f"[ROLEPLAY/FALLBACK] {e} — skipping account")
                    last_err = e
                    continue
                try:
                    result = await tavus_create_conversation(
                        api_key=account_key,
                        openrouter_key=openrouter_key,
                        system_prompt=prompt,
                        persona_name=form.prospect_name,
                        replica_id=account_replica,
                        model=model,
                        persona_id=(form.persona_id or "").strip(),
                        language=language,
                        elevenlabs_key=elevenlabs_key,
                        elevenlabs_voice_id=elevenlabs_voice_id,
                        elevenlabs_model_id=elevenlabs_model_id,
                        conversational_context=_role_context(form.prospect_name, language),
                        llm_base_url=llm_base_url,
                        llm_api_key=llm_api_key,
                    )
                    used_account = account
                    _account_cooldown.pop(account, None)   # credit is back
                    break
                except Exception as e:
                    if should_failover(e):
                        log.warning(f"[ROLEPLAY/FALLBACK] account '{account}' "
                                    f"unavailable ({e}) — trying next account")
                        last_err = e
                        status = getattr(getattr(e, "response", None), "status_code", 0)
                        if status in (402, 403):
                            if account not in exhausted:
                                exhausted.append(account)
                            _account_cooldown[account] = time.monotonic() + ACCOUNT_COOLDOWN_S
                        continue
                    raise
            if result is not None:
                break
            if sweep == 1 and _is_concurrency_error(last_err):
                log.warning("[ROLEPLAY/FALLBACK] all accounts at concurrency "
                            "cap — waiting 8s for Tavus to release a slot, "
                            "then retrying once")
                await asyncio.sleep(8)
        if result is None:
            # Every account is out of credits — a billing state, not a bug. Say so
            # plainly instead of leaking "402 Client Error ... /v2/conversations",
            # which reads like a broken integration and sends people to the code.
            if exhausted and len(exhausted) == len(accounts):
                log.error(f"[ROLEPLAY] ALL Tavus accounts out of credit: "
                          f"{', '.join(exhausted)} — top up or add another account")
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"All {len(accounts)} Tavus accounts are out of credit "
                        f"({', '.join(exhausted)}), so no new avatar call can start. "
                        "Top up a Tavus plan, or add another account to TAVUS_ACCOUNTS "
                        "in .env. Everything else (reports, transcripts, pitch tools) "
                        "keeps working."
                    ),
                )
            raise last_err or ValueError("All Tavus accounts failed")

        log.info(f"[ROLEPLAY] session started: {result['conversation_id']} "
                 f"(account={used_account})")

        # Track the session for the inactivity reaper (and make sure the
        # reaper is running — idempotent). The account is stored so end/reap
        # always use the key that created the conversation.
        start_session_reaper()
        _register_session(result["conversation_id"], account=used_account)
        # Outlives /end so the post-call report can fetch the transcript with the
        # key of the account that actually served the call.
        _remember_account(result["conversation_id"], used_account)

        # Booked seat → attendance is a side effect of launching, not a form.
        if booking_id:
            try:
                await asyncio.to_thread(
                    sched_svc.mark_attended,
                    booking_id=booking_id, conversation_id=result["conversation_id"])
            except Exception as e:
                log.warning(f"[ROLEPLAY] could not mark attendance: {e}")

        return LaunchResponse(
            session_id=result["conversation_id"],
            conversation_url=result["conversation_url"],
            assembled_prompt=prompt,
            idle_timeout_s=IDLE_TIMEOUT_S,
        )

    except HTTPException:
        raise                      # already a deliberate status + message
    except Exception as e:
        log.error(f"[ROLEPLAY] launch error: {e}")
        # Translate Tavus limit errors into something a trainee can act on —
        # the raw "400 Client Error … /v2/conversations" told users nothing.
        if _is_concurrency_error(e):
            raise HTTPException(status_code=503, detail=(
                "The avatar service is still releasing the previous call. "
                "Wait about 30 seconds, then press Join again."
            ))
        resp = getattr(e, "response", None)
        body = ""
        if resp is not None:
            try:
                body = resp.text[:300]
            except Exception:
                body = ""
        if body and any(k in body.lower() for k in ("credit", "quota", "out of")):
            raise HTTPException(status_code=503, detail=(
                "The Tavus account has run out of credits/quota. Add credits "
                "or configure a backup account in TAVUS_ACCOUNTS."
            ))
        # Include Tavus's response body so real errors are diagnosable from the UI.
        detail = f"{e}" + (f" — {body}" if body else "")
        raise HTTPException(status_code=500, detail=detail)


class EndRequest(BaseModel):
    conversation_id: str


@router.get("/avatars")
async def list_avatars():
    """
    Returns the avatars (Tavus replicas) and LLM model options the manager
    can pick from before launching a session.
    Shape: {"replicas": [{label, id, status}], "models": {label: model_id}}
    """
    # Gallery always lists the replicas of the account NAMED "primary" — those
    # ids are the canonical ones the frontend pins in presets.js, regardless of
    # which account is ordered first for serving (TAVUS_ACCOUNTS order decides
    # who serves; the name decides whose replica ids are canonical). Fallback
    # translation to the serving account happens server-side at launch.
    tavus_key = tavus_key_for("primary") or tavus_primary_key()
    if not tavus_key:
        raise HTTPException(
            status_code=400,
            detail="No Tavus account configured. Set TAVUS_ACCOUNTS or "
                   "TAVUS_API_KEY in your .env file.",
        )
    try:
        replicas = await tavus_fetch_replicas(tavus_key)
        return {"replicas": replicas, "models": MODEL_OPTIONS}
    except Exception as e:
        log.error(f"[ROLEPLAY] avatars error: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to load avatars: {e}")


@router.get("/avatar-thumb/{replica_id}")
async def proxy_avatar_thumb(replica_id: str):
    """Proxy a Tavus custom replica thumbnail video so the browser plays it.

    The Tavus CDN (cdn.replica.tavus.io) serves custom replica videos with
    Content-Type: binary/octet-stream — browsers refuse to play that in a
    <video> tag. This endpoint fetches the video and re-serves it as video/mp4.
    The CDN itself is publicly accessible (no API key required on the CDN fetch).
    """
    # A custom replica's metadata is only visible to the account that owns it —
    # resolve which configured account that is (stock/unknown ids → primary).
    tavus_key = tavus_key_for(owning_account(replica_id)) or tavus_primary_key()
    if not tavus_key:
        raise HTTPException(status_code=503, detail="Tavus not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        # Resolve the actual CDN URL from Tavus metadata.
        meta = await client.get(
            f"https://tavusapi.com/v2/replicas/{replica_id}",
            headers={"x-api-key": tavus_key},
        )
        if meta.status_code != 200:
            raise HTTPException(status_code=404, detail="Replica not found")
        data = meta.json()
        url = data.get("thumbnail_video_url") or data.get("thumbnail_image_url")
        if not url:
            raise HTTPException(status_code=404, detail="No thumbnail available")
        # Fetch the CDN asset (publicly accessible — no key needed on CDN).
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="Thumbnail fetch failed")
        # Force video/mp4 so the browser's <video> element accepts it.
        return StreamingResponse(
            iter([r.content]),
            media_type="video/mp4",
            headers={"Cache-Control": "public, max-age=86400"},
        )


@router.post("/end")
async def end_session(req: EndRequest):
    """Ends an active Tavus conversation (best-effort) — always with the API
    key of the account that created it (sticky account)."""
    try:
        await _end_on_owning_account(req.conversation_id)
        return {"status": "ended", "conversation_id": req.conversation_id}
    except Exception as e:
        log.warning(f"[ROLEPLAY] end error: {e}")
        return {"status": "error", "detail": str(e)}


class ReportRequest(BaseModel):
    session_id: str
    prospect_name: str = ""
    profession: str = ""
    loan_type: str = ""
    scenario: str = ""
    age: str = ""
    difficulty: str = ""
    call_type: str = ""              # "Service Call …" → ETB service grading
    feedback_state: str = ""         # Happy | Neutral | Complaint (ETB only)
    local_transcript: Optional[str] = ""   # accumulated from local Whisper during call


class ReportResponse(BaseModel):
    report: dict  # Full structured report JSON from the LLM


@router.post("/report", response_model=ReportResponse)
async def generate_report(req: ReportRequest, scope=Depends(tenant_scope)):
    """
    1. Fetch the Tavus transcript (server-side, post-call)
    2. Run the main analysis (7 soft-skill parameters)
    3. Run pitch-parameter scoring from local Whisper transcript (5 checklist params)
    4. Merge and return the combined report
    """
    try:
        # The conversation may have been served by a fallback Tavus account —
        # fetch the transcript with THAT account's key (see _conv_account).
        transcript = await fetch_tavus_transcript(
            req.session_id, preferred_account=_conv_account.get(req.session_id, ""),
        )
        # Run both analyses concurrently. return_exceptions keeps a pitch-scoring
        # failure from cancelling the main analysis (and vice versa) — the report
        # is the deliverable, the pitch coverage block is additive.
        report, pitch_params = await asyncio.gather(
            analyse_transcript(
                transcript=transcript,
                prospect_name=req.prospect_name,
                profession=req.profession,
                loan_type=req.loan_type,
                scenario=req.scenario,
            ),
            _score_pitch_params(
                transcript=req.local_transcript or transcript,
                prospect_name=req.prospect_name,
                loan_type=req.loan_type,
                profession=req.profession,
                age=getattr(req, "age", ""),
                difficulty=getattr(req, "difficulty", ""),
                call_type=getattr(req, "call_type", ""),
                feedback_state=getattr(req, "feedback_state", ""),
            ),
            return_exceptions=True,
        )
        if isinstance(report, BaseException):
            raise report
        if isinstance(pitch_params, BaseException):
            log.error(f"[ROLEPLAY] pitch score failed: {pitch_params}")
            pitch_params = {}
        if pitch_params:
            report["pitch_parameters"] = pitch_params

        # Persist the scored session for the agent (best-effort — never fail the report).
        try:
            # Booked-seat calls: link the report row to its slot + booking so the
            # admin's session report is a join, not a new tracking system.
            slot_id, booking_id = sched_svc.link_for_report(req.session_id)
            activity.roleplay_record(
                scope,
                prospect_name=req.prospect_name, profession=req.profession, loan_type=req.loan_type,
                score=report.get("overall_score"), grade=report.get("overall_grade"), report=report,
                slot_id=slot_id, booking_id=booking_id,
            )
        except Exception as e:
            log.warning(f"[ROLEPLAY] could not persist session: {e}")

        return ReportResponse(report=report)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"[ROLEPLAY] report error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Scenario auto-generation (LLM) ────────────────────────────────────────────

# Allowed enum values — kept in sync with frontend/formOptions.js.
_PROFESSIONS  = ["Doctor", "Teacher", "Businessman", "Government Employee",
                 "Housewife", "Self-Employed", "Retired", "Student"]
_GENDERS      = ["Male", "Female"]
_AGES         = ["25-35", "35-50", "50+"]
_CITY_TIERS   = ["Metro (Mumbai, Delhi, Bangalore)", "Tier 2 (Pune, Surat, Jaipur)",
                 "Tier 3", "Rural"]
_TONES        = ["Friendly", "Neutral", "Skeptical", "Rough", "Confused",
                 "Aggressive", "Very Busy", "Polite but Firm"]
_KNOWLEDGE    = ["Knows nothing about loans", "Basic understanding",
                 "Well researched online", "Taken loans before"]
_DECISION     = ["Takes decisions alone", "Needs spouse approval",
                 "Needs time to think", "Quick decision maker"]
_TRUST        = ["Trusts HDFC", "Neutral", "Prefers competitor",
                 "Had bad experience with HDFC before"]
_LOAN_TYPES   = ["Personal Loan", "Business Loan"]
_CIBIL        = ["good 750+", "average 650-750", "low below 650", "unknown"]
_INCOME_SIT   = ["Stable salaried", "Variable income", "Seasonal income",
                 "High income irregular"]
_COMPETITORS  = ["none", "SBI", "Bajaj Finserv", "ICICI"]
_CALL_TYPES   = ["Sales Call (salesperson pitching)",
                 "Service Call (customer has a problem first)",
                 "Follow-up Call (customer already inquired)",
                 "Objection Handling (customer is resisting)"]
_DIFFICULTIES = ["Easy", "Medium", "Hard", "Expert"]

# ETB service-call specific enums
_ETB_FEEDBACK_STATES = ["Happy", "Neutral", "Complaint"]
_ETB_SERVICE_ISSUES  = [
    "Reward points not credited",
    "Disputed charge not reversed",
    "KYC update stuck",
    "Relationship manager unresponsive",
    "Cheque book / card not delivered",
    "Wrong charge / hidden fee",
    "Net banking access issue",
    "FD renewal request stuck",
    "Complaint ticket unresolved",
]
_ETB_OFFER_TYPES = [
    "Pre-approved personal loan",
    "Pre-approved business top-up loan",
    "Pre-approved home loan top-up",
]
_ETB_FACILITIES = [
    "Salary account + HDFC debit card",
    "Salary account + HDFC credit card",
    "Current account + HDFC business credit card",
    "Savings account + HDFC credit card",
    "Salary account + HDFC credit card + existing home loan",
]
_ETB_DURATIONS = ["5 years", "7 years", "8 years", "10 years", "12 years",
                  "15 years", "20 years", "Many years (salary account)"]
_ETB_AMOUNTS   = ["₹3 lakhs", "₹5 lakhs", "₹8 lakhs", "₹10 lakhs",
                  "₹15 lakhs", "₹20 lakhs", "₹25 lakhs", "₹40 lakhs"]

# Profession-specific free-text fields the LLM should also fill.
_PROFESSION_EXTRA = {
    "Doctor":              ["specialization"],
    "Teacher":             ["teacher_type", "school_college_type"],
    "Businessman":         ["business_type", "turnover", "current_bank"],
    "Government Employee": ["govt_dept", "govt_designation", "years_to_retirement"],
    "Housewife":           ["spouse_profession", "spouse_income"],
    "Self-Employed":       ["self_employed_type"],
    "Retired":             ["retired_from", "years_since_retirement", "pension_amount"],
    "Student":             ["student_type", "field_of_study", "institution_type", "family_income"],
}


def _random_skeleton() -> dict:
    """Randomly pick the structured (enum) fields so every click is a different
    customer — and so the dropdown values are always valid."""
    return {
        "profession":       random.choice(_PROFESSIONS),
        "gender":           random.choice(_GENDERS),
        "age":              random.choice(_AGES),
        "city_tier":        random.choice(_CITY_TIERS),
        "tone":             random.choice(_TONES),
        "knowledge_level":  random.choice(_KNOWLEDGE),
        "decision_style":   random.choice(_DECISION),
        "trust_level":      random.choice(_TRUST),
        "loan_type":        random.choice(_LOAN_TYPES),
        "cibil":            random.choice(_CIBIL),
        "income_situation": random.choice(_INCOME_SIT),
        "competitor":       random.choice(_COMPETITORS),
        "call_type":        random.choice(_CALL_TYPES),
        "difficulty":       random.choice(_DIFFICULTIES),
    }


@router.post("/generate")
async def generate_scenario():
    """
    Build a complete, random, realistic HDFC loan prospect to pre-fill the custom
    form. The enum fields are chosen here (always valid + varied); the LLM fills
    the free-text fields and writes a vivid scenario consistent with them.
    """
    try:
        skel = _random_skeleton()
        prof = skel["profession"]
        extra_keys = _PROFESSION_EXTRA.get(prof, [])

        prompt = (
            "You are creating ONE realistic Indian HDFC Bank loan PROSPECT to use "
            "in a sales-practice roleplay. Here is the fixed skeleton you must keep "
            "exactly as given:\n"
            f"{json.dumps(skel, indent=2)}\n\n"
            "Fill in the remaining details so the whole profile is coherent and "
            "realistic for this skeleton. Return ONLY a JSON object (no markdown) "
            "with these keys:\n"
            "- prospect_name: a realistic Indian full name matching the gender\n"
            "- city: a real Indian city consistent with the city_tier\n"
            "- years_experience: a number as a string (e.g. \"12\")\n"
            "- income: monthly income as a string (e.g. \"Rs. 85,000/month\")\n"
            "- loan_amount: amount in lakhs as a plain number string (e.g. \"15\")\n"
            "- loan_purpose: short realistic purpose for this profession\n"
            "- competitor_rate: a rate like \"10.5\" ONLY if competitor is not "
            "\"none\", otherwise \"\"\n"
            f"- profession-specific fields for a {prof}: {extra_keys}\n"
            "- special_situation: one vivid sentence (a backstory that is true now)\n"
            "- key_objection: the main objection this customer holds firmly\n"
            "- hidden_information: something true but NOT revealed unless earned\n"
            "- training_focus: what skill the rep should practise against them\n"
            "- scenario: a vivid 4-6 sentence second-person customer brief that "
            "ties the whole profile together (background, why they need the loan "
            "with an amount and a timeline, their mood, and how they behave). It "
            "must match the tone, difficulty and trust level in the skeleton.\n"
            "Do NOT change any skeleton value. Return valid JSON only."
        )

        raw = await asyncio.to_thread(
            call_llm, prompt, model_role="chat",
            temperature=0.95, max_tokens=1400, json_mode=True,
        )
        filled = json.loads(raw)
        form = {**skel, **filled, "competitor_rate": filled.get("competitor_rate", "")}
        return {"form": form}
    except Exception as e:
        log.error(f"[ROLEPLAY] generate error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate scenario: {e}")


@router.post("/generate-etb")
async def generate_etb_scenario():
    """
    Build a complete, random, realistic ETB service-call session to pre-fill the
    custom ETB form. The EXISTING customer received an unsolicited call from their
    HDFC RM — they did NOT ask for a loan. Structured fields are picked here
    (always valid); the LLM writes the free-text fields and the scenario.
    """
    try:
        feedback_state = random.choice(_ETB_FEEDBACK_STATES)
        profession     = random.choice(_PROFESSIONS)
        extra_keys     = _PROFESSION_EXTRA.get(profession, [])
        is_complaint   = feedback_state == "Complaint"

        skel = {
            "customer_type":        "existing",
            "call_type":            "Service Call (customer has a problem first)",
            "profession":           profession,
            "gender":               random.choice(_GENDERS),
            "age":                  random.choice(_AGES),
            "city_tier":            random.choice(_CITY_TIERS),
            "tone":                 random.choice(_TONES),
            "knowledge_level":      random.choice(_KNOWLEDGE),
            "decision_style":       random.choice(_DECISION),
            "trust_level":          random.choice(_TRUST),
            "difficulty":           random.choice(_DIFFICULTIES),
            "feedback_state":       feedback_state,
            "existing_facility":    random.choice(_ETB_FACILITIES),
            "relationship_duration": random.choice(_ETB_DURATIONS),
            "offer_type":           random.choice(_ETB_OFFER_TYPES),
            "offer_amount":         random.choice(_ETB_AMOUNTS),
            "service_issue":        random.choice(_ETB_SERVICE_ISSUES) if is_complaint else "",
        }

        complaint_instructions = (
            "- main_objection: one punchy sentence expressing their specific anger "
            "about the service_issue while being called to sell a loan\n"
            if is_complaint else
            "- main_objection: \"\"\n"
        )

        prompt = (
            "You are creating ONE realistic Indian HDFC Bank EXISTING CUSTOMER for an "
            "ETB service-call roleplay. This customer RECEIVED an outbound call from "
            "their bank's RM — they did NOT ask for a loan. The RM does a service "
            "check first, then introduces a pre-approved offer. Here is the fixed "
            "skeleton you must keep exactly as given:\n"
            f"{json.dumps(skel, indent=2)}\n\n"
            "Fill in the remaining details so the whole profile is coherent and "
            "realistic. Return ONLY a JSON object (no markdown) with these keys:\n"
            "- prospect_name: a realistic Indian full name matching the gender\n"
            "- city: a real Indian city consistent with the city_tier\n"
            "- years_experience: number as string (e.g. \"14\") — years in their profession\n"
            "- income: monthly income as string (e.g. \"Rs. 95,000/month\")\n"
            f"- profession-specific fields for a {profession}: {extra_keys}\n"
            + complaint_instructions +
            "- special_situation: one vivid sentence (something currently true about them)\n"
            "- key_objection: the specific doubt they hold firmest about the unsolicited offer\n"
            "- hidden_information: something true they won't reveal unless the RM earns it\n"
            "- training_focus: what skill the RM should practise against this customer\n"
            "- scenario: a vivid 5-7 sentence second-person brief ('You are ...') that "
            "covers: who they are, how long with HDFC, their feedback_state, "
            + ("their live grievance (service_issue) and how it colours the call, " if is_complaint else "") +
            "how they react when the RM introduces the pre-approved offer (wary / "
            "curious / irritated), and what would make them consider it. The customer "
            "did NOT seek a loan — the offer is a surprise. Do NOT say they are "
            "'looking for' or 'need' a loan. Match the tone and difficulty in the skeleton.\n"
            "Do NOT change any skeleton value. Return valid JSON only."
        )

        raw = await asyncio.to_thread(
            call_llm, prompt, model_role="chat",
            temperature=0.95, max_tokens=1600, json_mode=True,
        )
        filled = json.loads(raw)
        form = {**skel, **filled}
        # loan fields kept empty for ETB (they're not needed to launch, but the
        # Pydantic model requires str so we ensure they're present as empty string)
        form.setdefault("loan_type", "")
        form.setdefault("loan_amount", "")
        form.setdefault("loan_purpose", "")
        return {"form": form}
    except Exception as e:
        log.error(f"[ROLEPLAY] generate-etb error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate ETB scenario: {e}")


class NarrativeRequest(BaseModel):
    # Whatever fields the manager has already chosen — all optional.
    prospect_name: Optional[str] = ""
    gender: Optional[str] = ""
    age: Optional[str] = ""
    city: Optional[str] = ""
    city_tier: Optional[str] = ""
    profession: Optional[str] = ""
    income: Optional[str] = ""
    tone: Optional[str] = ""
    knowledge_level: Optional[str] = ""
    decision_style: Optional[str] = ""
    trust_level: Optional[str] = ""
    loan_type: Optional[str] = ""
    loan_amount: Optional[str] = ""
    loan_purpose: Optional[str] = ""
    cibil: Optional[str] = ""
    income_situation: Optional[str] = ""
    competitor: Optional[str] = ""
    competitor_rate: Optional[str] = ""
    call_type: Optional[str] = ""
    customer_type: Optional[str] = ""
    difficulty: Optional[str] = ""
    special_situation: Optional[str] = ""
    key_objection: Optional[str] = ""
    hidden_information: Optional[str] = ""
    training_focus: Optional[str] = ""
    # ETB service-call fields (ignored for new-customer narratives)
    feedback_state: Optional[str] = ""
    existing_facility: Optional[str] = ""
    relationship_duration: Optional[str] = ""
    offer_type: Optional[str] = ""
    offer_amount: Optional[str] = ""
    service_issue: Optional[str] = ""
    main_objection: Optional[str] = ""


def _is_etb_narrative(req: "NarrativeRequest") -> bool:
    ct   = (req.customer_type or "").strip().lower()
    call = (req.call_type or "").strip().lower()
    return ct == "existing" and call.startswith("service call")


@router.post("/generate-narrative")
async def generate_narrative(req: NarrativeRequest):
    """Write a full scenario narrative consistent with the fields chosen above."""
    try:
        fields = {k: v for k, v in req.dict().items() if v}
        if _is_etb_narrative(req):
            # ETB service-call: the customer received an unsolicited call — they
            # did NOT seek a loan. Framing must reflect that, and must cover their
            # feedback state (Happy / Neutral / Complaint) and any grievance.
            prompt = (
                "Write a vivid 4-6 sentence customer scenario for an HDFC Bank "
                "service-call roleplay, in SECOND PERSON addressed to the customer "
                "('You are ...'). This is an EXISTING HDFC customer who RECEIVED "
                "an outbound relationship/service call from their bank's RM — they "
                "did NOT ask for a loan. Base it ENTIRELY on the details below.\n\n"
                "Cover: who they are, how long they have banked with HDFC, how they "
                "CURRENTLY FEEL about the bank (their feedback_state), any live "
                "grievance they carry (if service_issue is set), and how they react "
                "when the RM unexpectedly brings up a pre-approved offer.\n\n"
                "DO NOT say the customer 'needs' or 'is looking for' a loan — the "
                "offer is unsolicited. They may consider it IF the RM earns their "
                "trust, but their default is wariness or polite refusal.\n\n"
                "Return only the scenario text, no preamble.\n\n"
                f"DETAILS:\n{json.dumps(fields, indent=2)}"
            )
        else:
            prompt = (
                "Write a vivid 4-6 sentence customer scenario for an HDFC Bank "
                "sales-practice roleplay, in SECOND PERSON addressed to the customer "
                "('You are ...'). Base it ENTIRELY on the manager-chosen details "
                "below; stay consistent with the tone, difficulty and trust level. "
                "Cover: who they are, why they need the loan (with an amount and a "
                "realistic timeline), their mood, and how they behave on the call. "
                "Return only the scenario text, no preamble.\n\n"
                f"DETAILS:\n{json.dumps(fields, indent=2)}"
            )
        text = await asyncio.to_thread(
            call_llm, prompt, model_role="chat",
            temperature=0.85, max_tokens=400,
        )
        return {"scenario": text.strip()}
    except Exception as e:
        log.error(f"[ROLEPLAY] narrative error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate scenario: {e}")


@router.get("/access")
async def roleplay_access(user: dict = Depends(get_current_user)):
    """Whether the signed-in user may run a simulation right now (slot gating).
    The launch endpoint enforces the same rule server-side — this exists so the
    RolePlay tab can render a friendly gate screen with the next slot time."""
    if not REQUIRE_BOOKING:
        return {"gated": False, "allowed": True, "live": None, "next": None}
    state = await asyncio.to_thread(sched_svc.access_state, user_id=user["id"])
    return {"gated": True, **state}


@router.get("/health")
async def health():
    return {"status": "ok", "router": "roleplay"}


@router.get("/accounts-health")
async def accounts_health():
    """Verify every configured Tavus account is reachable and owns the custom
    replicas mapped to it in core/tavus_accounts.py. Run this after adding an
    account or editing the registry."""
    results = await asyncio.to_thread(check_accounts_health)
    return {
        "accounts": results,
        "ok": all(r["ok"] for r in results.values()) if results else False,
    }


# ── Per-turn role-enforcing LLM proxy ─────────────────────────────────────────
# WHY THIS EXISTS: the avatar LLM (gpt-4o-mini) reverses into the bank/assistant
# role ("How can I help you?" / "मैं आपको जानकारी देता हूँ") even with an absolute
# role lock + final anchor in the persona system prompt — verified against a real
# session (Tavus conv c6eaeb959c1d2404: anchor present, avatar still flipped by
# turn 3 in Hindi). A static prompt is delivered ONCE and recedes as the call
# grows; nothing enforces the role at generation time.
#
# THE FIX: the Tavus persona's LLM layer points at THIS endpoint instead of
# OpenRouter. On EVERY turn we:
#   1. append a short, language-matched role anchor as the LAST message in the
#      array (maximum recency on every single generation, forever), and
#   2. guard the output — if the reply opens with a blatant role reversal, it is
#      replaced with a canned in-character customer line (which also self-corrects
#      the conversation, because the model sees that line in history next turn).
# The proxy is stateless: persona name + session language are extracted from the
# request's own system prompt, so a backend restart loses nothing.
#
# ACTIVATION: personas are only pointed at the proxy when a public base URL is
# known AND a live self-check passes (see _proxy_llm_config); otherwise launch
# falls back to direct OpenRouter exactly as before — a broken tunnel can never
# take the avatar down.

_PROXY_UPSTREAM = "https://openrouter.ai/api/v1/chat/completions"
# Release the output guard after this many buffered chars (reversals show up in
# the opening words; past this point we stream through untouched).
_GUARD_CHARS = int(os.getenv("ROLEPLAY_PROXY_GUARD_CHARS", "90"))

# Blatant role-reversal openings. Precision over recall — the per-turn anchor is
# the primary defence; the guard only catches unambiguous agent/assistant lines.
_REVERSAL_PATTERNS = [
    # English
    r"how (?:can|may) i (?:help|assist)",
    r"what (?:can|may) i do for you",
    r"i'?m calling from hdfc",
    r"this is \w+ (?:calling )?from hdfc",
    r"what (?:kind of )?loan are you looking for",
    r"let me (?:explain|tell you about) our",
    r"i can offer you",
    r"we (?:have|offer) (?:a |great |several |attractive )",
    r"i'?m an ai",
    # Hindi (Devanagari) — exact failure lines observed live + close variants.
    r"मैं आपकी .{0,12}मदद कर",
    r"कैसे मदद कर सकत",
    r"मैं आपको (?:इस|लोन|loan|rate|इसके) .{0,15}जानकारी देता",
    r"आपको बताना चाहूँगा कि (?:लोन|loan)",
    r"क्या आप .{0,25}जानकारी लेना चाहेंगे",
    r"आपके लिए .{0,10}(?:सही |बेहतर )?rate क्या हो सकता",
    r"मैं .{0,10}(?:एचडीएफसी|hdfc) .{0,10}(?:से|की तरफ से) बोल",
    r"हमारे बैंक (?:की|के|में)",
]
_REVERSAL_RE = re.compile("|".join(_REVERSAL_PATTERNS), re.IGNORECASE)

# In-character replacement lines (used only when the guard trips). They firmly
# re-assert the customer role in the session's language.
_CANNED_EN = [
    "Why are you asking me that? You called me — you're from the bank. I'm {name}, the customer. You tell me.",
    "Hold on — you called me, remember? I'm the one with the questions here. Go on, what's this about?",
]
_CANNED_HI = [
    "अरे, ये आप मुझसे क्यों पूछ रहे हैं? call आपने किया है — bank से आप हैं। मैं {name} हूँ, customer। आप बताइए।",
    "रुकिए — call आपने मुझे किया है ना? सवाल मैं पूछूँगा। बोलिए, क्या बात है?",
]

_NAME_RE = re.compile(r"you are ([^,\n]{2,60}), the CUSTOMER being sold to")


def _proxy_token() -> str:
    """Bearer token Tavus sends back to us (set as the persona's llm api_key).
    Derived from the OpenRouter key so it is stable across restarts on the same
    machine, stateless, and never stored anywhere new."""
    key = os.getenv("OPENROUTER_API_KEY", "").encode()
    return hmac.new(key, b"roleplay-llm-proxy-v1", hashlib.sha256).hexdigest()[:40]


def _proxy_public_base() -> str:
    """Public base URL of this proxy (".../api/roleplay/llm/v1"), or ""."""
    if os.getenv("ROLEPLAY_LLM_PROXY", "").strip().lower() in ("off", "0", "false"):
        return ""
    explicit = os.getenv("ROLEPLAY_LLM_PROXY_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    # Production convention: start-backend.ps1 exposes the backend at a stable
    # ngrok domain held in NGROK_DOMAIN — reuse it automatically.
    dom = os.getenv("NGROK_DOMAIN", "").strip()
    if dom:
        return f"https://{dom}/api/roleplay/llm/v1"
    return ""


_proxy_health: dict = {"ts": 0.0, "ok": False, "base": ""}


def _proxy_llm_config() -> tuple[str, str]:
    """(base_url, api_key) for the persona's LLM layer — the proxy when public
    and reachable, else ("", "") meaning direct OpenRouter. The reachability
    self-check (through the real public URL, i.e. the tunnel) is cached 60 s."""
    base = _proxy_public_base()
    if not base:
        return "", ""
    now = time.monotonic()
    if _proxy_health["base"] != base or now - _proxy_health["ts"] > 60:
        ok = False
        try:
            r = httpx.get(f"{base}/health", timeout=4)
            ok = r.status_code == 200 and r.json().get("proxy") == "roleplay-llm"
        except Exception as e:
            log.error(f"[ROLEPLAY/LLM-PROXY] self-check failed for {base}: {e} "
                      "— session will use direct OpenRouter (no per-turn role anchor)")
        _proxy_health.update(ts=now, ok=ok, base=base)
        if ok:
            log.info(f"[ROLEPLAY/LLM-PROXY] active at {base}")
    return (base, _proxy_token()) if _proxy_health["ok"] else ("", "")


def _session_traits(messages: list) -> tuple[str, str]:
    """Extract (name, language) from the request's own system prompt — stateless."""
    sys0 = ""
    for m in messages:
        if m.get("role") == "system":
            sys0 = m.get("content") or ""
            break
    name = "the customer"
    m = _NAME_RE.search(sys0)
    if m:
        name = m.group(1).strip()
    lang = "hindi" if ("अंतिम ROLE ANCHOR" in sys0 or "SPEAK NATURAL URBAN HINGLISH" in sys0) else "english"
    return name, lang


def _turn_anchor(name: str, lang: str) -> str:
    """The short role anchor appended as the LAST message on every turn."""
    if lang == "hindi":
        return (
            f"SYSTEM REMINDER (सबसे ऊँची प्राथमिकता): आप {name} हैं — CUSTOMER, "
            f"जिसे HDFC agent ने call किया है। आप कभी bank, agent या assistant की "
            f"तरह नहीं बोलते: कोई 'मैं मदद करूँ?', कोई 'मैं जानकारी देता हूँ', कोई "
            f"loan/rate/fee/documents समझाना नहीं — वो सब AGENT का काम है। अगर पिछली "
            f"बात कटी/अधूरी लगे तो उलझे हुए customer की तरह पूछिए ('क्या? फिर से "
            f"बोलिए?')। अगली line सिर्फ़ {name} — customer — की होगी, उसी के अंदाज़ में।"
        )
    return (
        f"SYSTEM REMINDER (highest priority): You are {name}, the CUSTOMER who "
        f"received this call. The other speaker is the HDFC sales agent who called "
        f"you. Never speak as the bank, agent, advisor, or assistant: no offering "
        f"help, no 'how can I help you', no explaining loans, rates, fees or "
        f"documents — that is the AGENT'S job. If their last line is garbled or "
        f"unclear, react as a confused customer ('Sorry, what? Say that again?'). "
        f"Your next line is {name} the customer, fully in character."
    )


def _canned_line(name: str, lang: str, seed: int) -> str:
    pool = _CANNED_HI if lang == "hindi" else _CANNED_EN
    return pool[seed % len(pool)].format(name=name)


def _sse_chunk(model: str, content: str = "", finish: "str | None" = None) -> bytes:
    delta = {"content": content} if content else {}
    payload = {
        "id": "rp-guard", "object": "chat.completion.chunk", "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n"


@router.get("/llm/v1/health")
async def llm_proxy_health():
    return {"proxy": "roleplay-llm", "status": "ok"}


@router.post("/llm/v1/chat/completions")
async def llm_proxy_completions(request: Request):
    """OpenAI-compatible /chat/completions used as the Tavus persona's custom
    LLM. Injects a per-turn role anchor, forwards to OpenRouter (streaming SSE
    passthrough), and replaces blatantly role-reversed replies."""
    # Auth: Tavus sends back the api_key we set on the persona.
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, _proxy_token()):
        return JSONResponse(status_code=401, content={"error": "bad proxy token"})

    body = await request.json()
    messages = body.get("messages") or []
    name, lang = _session_traits(messages)
    messages = list(messages) + [{"role": "system", "content": _turn_anchor(name, lang)}]
    body["messages"] = messages

    upstream_headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}",
        "Content-Type": "application/json",
    }
    model = body.get("model", "")
    log.info(f"[ROLEPLAY/LLM-PROXY] turn: msgs={len(messages)} lang={lang} "
             f"name={name!r} stream={bool(body.get('stream'))}")

    # ── Non-streaming (rare — Tavus streams, but handle it correctly) ────────
    if not body.get("stream"):
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(_PROXY_UPSTREAM, json=body, headers=upstream_headers)
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            if _REVERSAL_RE.search(text):
                log.error(f"[ROLEPLAY/LLM-PROXY] GUARD tripped (non-stream): {text[:120]!r}")
                data["choices"][0]["message"]["content"] = _canned_line(name, lang, len(text))
            return JSONResponse(status_code=r.status_code, content=data)
        except Exception:
            return JSONResponse(status_code=r.status_code,
                                content={"error": r.text[:500]})

    # ── Streaming SSE with an opening-text guard ─────────────────────────────
    async def stream():
        held: list[bytes] = []   # raw SSE lines held until the guard releases
        seen = ""                # accumulated visible text while guarding
        guarding = True
        client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120,
                                                         write=30, pool=10))
        try:
            async with client.stream("POST", _PROXY_UPSTREAM, json=body,
                                     headers=upstream_headers) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread())[:300]
                    log.error(f"[ROLEPLAY/LLM-PROXY] upstream {resp.status_code}: {detail!r}")
                    yield _sse_chunk(model, _canned_line(name, lang, 0), None)
                    yield _sse_chunk(model, "", "stop")
                    yield b"data: [DONE]\n\n"
                    return
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    raw = line.encode("utf-8") + b"\n\n"
                    if not guarding:
                        yield raw
                        continue
                    held.append(raw)
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            delta = json.loads(line[6:])["choices"][0]["delta"]
                            seen += delta.get("content") or ""
                        except Exception:
                            pass
                    if _REVERSAL_RE.search(seen):
                        log.error(f"[ROLEPLAY/LLM-PROXY] GUARD tripped: {seen!r} "
                                  f"→ replaced with canned {lang} customer line")
                        yield _sse_chunk(model, _canned_line(name, lang, len(seen)), None)
                        yield _sse_chunk(model, "", "stop")
                        yield b"data: [DONE]\n\n"
                        return
                    if len(seen) >= _GUARD_CHARS or line == "data: [DONE]":
                        guarding = False
                        for h in held:
                            yield h
                        held = []
                # upstream ended while still guarding (short reply) — flush.
                for h in held:
                    yield h
        finally:
            await client.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ── Whisper STT chunk transcription ──────────────────────────────────────────

@router.post("/transcribe-chunk")
async def transcribe_chunk(
    audio: UploadFile = File(...),
    language: str = Form(""),
    conversation_id: str = Form(""),
):
    """
    Accepts a webm/opus audio chunk from the browser MediaRecorder, runs it
    through local faster-whisper (WHISPER_LIVE_MODEL_SIZE=large-v3-turbo on
    CUDA) and returns the transcript text.  Called every ~8 seconds during the
    live RolePlay call to accumulate the agent's speech for parameter checking.

    `language` is the session language the manager picked ("english"|"hindi").
    Forcing Whisper to that language is essential: with the env default of
    "auto", Whisper re-detects per 8-second chunk and — on Hindi/short/quiet
    clips — guesses the wrong language, producing a mixed Urdu/Russian/Icelandic
    word-salad with hallucinations ("Продолжение следует…"). Pinning the language
    keeps every chunk in the right script.
    """
    # Every arriving chunk proves the client is alive (inactivity reaper).
    if conversation_id:
        _touch_session(conversation_id, spoke=False)

    audio_bytes = await audio.read()
    if len(audio_bytes) < 800:
        return {"text": "", "language": "", "ms": 0}

    # Session language → Whisper hint. Fall back to the env default only when the
    # frontend sent nothing (older clients / non-roleplay callers).
    _lang = (language or "").strip().lower()
    if _lang in ("hindi", "hi"):
        hint = "hi"
    elif _lang in ("english", "en"):
        hint = "en"
    else:
        hint = os.getenv("WHISPER_LANGUAGE", "auto")

    try:
        engine = get_whisper()          # pre-warmed singleton, reuses live engine
        result = await asyncio.to_thread(
            engine.transcribe, audio_bytes,
            content_type=audio.content_type or "audio/webm",
            language_hint=hint,
        )
        log.info(
            f"[ROLEPLAY/STT] {result['duration_s']:.1f}s → "
            f"'{result['transcript'][:60]}' lang={result['language']} {result['ms']}ms"
        )
        # Non-empty speech = real user activity → push the idle deadline out.
        if conversation_id and result["transcript"].strip():
            _touch_session(conversation_id, spoke=True)
        return {"text": result["transcript"], "language": result["language"], "ms": result["ms"]}
    except Exception as e:
        log.warning(f"[ROLEPLAY/STT] transcribe error: {e}")
        return {"text": "", "language": "", "ms": 0, "error": str(e)}


# ── Pitch-parameter scoring (used in final report) ────────────────────────────

_PITCH_SCORE_SYSTEM = """You are a senior HDFC Bank sales training evaluator.
Analyse the sales agent's transcript and score each of the 5 pitch parameters.

Return ONLY a JSON object — no markdown, no explanation:
{
  "opening":            {"score": 0-10, "met": true|false, "verdict": "one concise sentence"},
  "discovery":          {"score": 0-10, "met": true|false, "verdict": "one concise sentence"},
  "product_pitch":      {"score": 0-10, "met": true|false, "verdict": "one concise sentence"},
  "objection_handling": {"score": 0-10, "met": true|false, "verdict": "one concise sentence"},
  "closing":            {"score": 0-10, "met": true|false, "verdict": "one concise sentence"}
}

Scoring criteria (0 = completely absent, 10 = perfect execution):
• opening (max 10)  — greeted by name + mentioned HDFC Bank (3pts), asked permission
  "do you have 2 minutes?" (3pts), stated purpose of call clearly (4pts).
• discovery (max 10) — asked about loan purpose (2.5), loan amount (2.5), income/
  employment (2.5), existing loans/CIBIL (2.5).
• product_pitch (max 10) — mentioned interest rate (2.5), processing fee (2.5), tenure
  options (2.5), EMI amount (2.5). Deduct 3 pts for any factually wrong claim.
• objection_handling (max 10) — acknowledged concern before countering (4pts), stayed
  calm/non-defensive (3pts), offered a solution or alternative (3pts). Score 5/10 if no
  objections were raised (neutral — neither good nor bad).
• closing (max 10) — asked for next step/commitment (4pts), confirmed contact details or
  follow-up (3pts), ended call professionally (3pts).

"met" = true when score >= 6. Be honest and specific in the verdict."""


async def _score_pitch_params(
    transcript: str, prospect_name: str, loan_type: str,
    profession: str = "", age: str = "", difficulty: str = "",
    call_type: str = "", feedback_state: str = "",
) -> dict:
    """Run a focused LLM call to score the 5 pitch parameters from the local transcript."""
    if not transcript or not transcript.strip():
        return {}

    # Build the scenario-aware base prompt, then append 0-10 scoring instructions.
    # call_type/feedback_state switch the checker to ETB service-call criteria.
    base = build_checker_prompt(
        profession=profession,
        age=age,
        difficulty=difficulty,
        call_type=call_type,
        feedback_state=feedback_state,
    )
    # Only the 3 params NOT already covered by the existing 7-param report:
    #   opening & discovery — completely new
    #   product_pitch — checks specific elements (rate/fee/tenure/EMI), not quality
    # objection_handling and closing are already in the main report as
    # "objection_handling" and "closing_technique" — do NOT duplicate them.
    score_suffix = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL REPORT SCORING — score ONLY these 3 parameters:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return ONLY a JSON object with exactly these 3 keys:
{
  "opening":       {"score": 0-10, "met": true|false, "verdict": "one concise sentence"},
  "discovery":     {"score": 0-10, "met": true|false, "verdict": "one concise sentence"},
  "product_pitch": {"score": 0-10, "met": true|false, "verdict": "one concise sentence"}
}

opening      — did the agent say their name + mention HDFC + ask permission?
discovery    — did the agent ask about loan purpose, amount, income, and obligations?
product_pitch — did the agent specifically mention rate, processing fee, tenure, and EMI?

Scoring: 0 = completely absent, 5 = partially done, 10 = all elements clearly covered.
"met" = true when score >= 6. Be specific and honest in the verdict (one sentence only).
"""
    full_system = base.rstrip() + "\n" + score_suffix

    user_msg = (
        f"Customer: {prospect_name or 'the customer'}\n"
        f"Loan type: {loan_type or 'HDFC loan'}\n\n"
        f"Full agent transcript:\n---\n{transcript[:4000]}\n---\n\nScore each parameter."
    )
    try:
        raw = await asyncio.to_thread(
            call_llm, user_msg,
            system_prompt=full_system,
            model_role="chat",
            temperature=0.0,
            max_tokens=300,
            json_mode=True,
        )
        return json.loads(raw)
    except Exception as e:
        # Don't fail the whole report if pitch scoring breaks — but log loudly
        # (a silent {} previously masked OpenRouter/LLM outages in the report).
        log.error(f"[ROLEPLAY] pitch score error: {type(e).__name__}: {e}")
        return {}


# ── Live parameter checking ───────────────────────────────────────────────────

class CheckParamsRequest(BaseModel):
    transcript: str
    prospect_name: Optional[str] = ""
    loan_type: Optional[str] = ""
    elapsed_seconds: int = 0
    # Scenario context — used to select profession/age/difficulty calibration
    profession: Optional[str] = ""
    age: Optional[str] = ""
    difficulty: Optional[str] = ""
    # ETB service-call grading switch (optional — absent → cold loan-sales grading)
    call_type: Optional[str] = ""
    feedback_state: Optional[str] = ""


class CheckParamsResponse(BaseModel):
    opening: str            # "pending" | "met" | "missed"
    discovery: str
    product_pitch: str
    objection_handling: str
    closing: str


@router.post("/check-params", response_model=CheckParamsResponse)
async def check_params(req: CheckParamsRequest):
    """
    Called every 12 s during a live RolePlay call (from liveCoach.js).
    Accepts the Whisper-accumulated agent transcript and returns per-parameter
    status using a scenario-aware LLM prompt from param_checker.py.
    """
    blank = CheckParamsResponse(
        opening="pending", discovery="pending",
        product_pitch="pending", objection_handling="pending",
        closing="pending",
    )
    if not req.transcript or not req.transcript.strip():
        return blank

    # Build scenario-aware system prompt from param_checker.py.
    # call_type/feedback_state switch it to ETB service-call criteria when present.
    system_prompt = build_checker_prompt(
        profession=req.profession or "",
        age=req.age or "",
        difficulty=req.difficulty or "",
        call_type=req.call_type or "",
        feedback_state=req.feedback_state or "",
    )

    minutes = req.elapsed_seconds // 60
    user_msg = (
        f"Customer name: {req.prospect_name or 'the customer'}\n"
        f"Loan type: {req.loan_type or 'HDFC loan'}\n"
        f"Time elapsed: {minutes} minute(s)\n\n"
        f"Agent transcript so far:\n---\n{req.transcript[:3000]}\n---\n\n"
        "Evaluate the agent's performance and return the JSON."
    )

    try:
        raw = await asyncio.to_thread(
            call_llm, user_msg,
            system_prompt=system_prompt,
            model_role="chat",
            temperature=0.0,
            max_tokens=150,
            json_mode=True,
        )
        data = json.loads(raw)
        VALID = {"pending", "met", "missed"}
        def _v(k): return data.get(k, "pending") if data.get(k) in VALID else "pending"
        return CheckParamsResponse(
            opening=_v("opening"),
            discovery=_v("discovery"),
            product_pitch=_v("product_pitch"),
            objection_handling=_v("objection_handling"),
            closing=_v("closing"),
        )
    except Exception as e:
        # Surface the failure instead of returning an all-"pending" 200 that
        # looks healthy (which is how the OpenRouter 404 stayed hidden). The
        # live frontend treats !res.ok as "skip this poll" (liveCoach.js), so a
        # 502 here degrades gracefully on the call while exposing the real cause
        # to anyone hitting the endpoint or reading logs.
        log.error(f"[check-params] LLM error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=f"check-params LLM failed: {e}")
