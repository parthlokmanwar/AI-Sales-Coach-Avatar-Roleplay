"""
FEATURE: Live Voice Assistant.

Endpoints:
  POST  /api/voice/clean-transcript     — clean raw STT output (Ollama-first, OpenRouter fallback)
  POST  /api/voice/enroll               — legacy: Deepgram-label-based RM ID
  POST  /api/voice/voiceprint/enroll    — NEW: acoustic fingerprint enrolment
  POST  /api/voice/voiceprint/verify    — NEW: real-time agent-voice detection per chunk
  GET   /api/voice/voiceprint/profiles  — NEW: list enrolled voices
  DELETE /api/voice/voiceprint/{id}     — NEW: remove a voiceprint
  POST  /api/voice/answer               — non-streaming answer (fallback)
  POST  /api/voice/answer/stream        — SSE token stream (the live path)
  POST  /api/voice/session-summary      — end-of-session CRM debrief (Ollama-first)
  WS    /ws/voice                       — WebSocket Q&A path (legacy)

AI logic lives in:
  app.py → EnhancedRAGv4._build_voice_prompt()      (Vikram persona)
         → EnhancedRAGv4._analyze_speech()          (lang/tone/entities)
         → EnhancedRAGv4._retrieve_relevant_context (RAG retrieval)
         → EnhancedRAGv4._call_ollama_fast          (greedy 80-token decoding)
  prompts/voice.py → build_voice_prompt()           (the actual prompt text)

  Voiceprint logic → core/voiceprint.py             (Resemblyzer / pyannote)

Model: llama3.2:3b (kept for streaming speed — first token in ~500ms).
"""

import asyncio
import json
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
import core.deps as deps
from core.deps import log, detect_language
from core import demo_cache
from core.emi_calc import (
    is_calculation_question,
    parse_amount,
    parse_tenure,
    compute_emi,
    format_for_prompt as format_emi_for_prompt,
)

# ── Persistent OpenRouter client ─────────────────────────────────────────────
# The live path streams a GPT-4o-mini reply on every utterance. Creating a fresh
# AsyncOpenAI client per request opens a new TLS connection to openrouter.ai each
# time (~200-400ms of handshake). Cache one client (keyed by api_key) so the
# HTTP/2 connection is reused across turns — shaves latency off every reply.
_gpt_client_cache: dict = {}


def _get_gpt_client(api_key: str):
    client = _gpt_client_cache.get(api_key)
    if client is None:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        _gpt_client_cache[api_key] = client
    return client


# ── Voice model routing ──────────────────────────────────────────────────────
# Fast default model handles factual / objection / general Qs in ~300ms-1.5s.
# Smart model is used when the question requires numeric reasoning (EMI/calc)
# OR when it's a multi-part question — the fast model frequently drops one half
# of a "X and Y?" question and the smart model reliably answers both parts.
VOICE_FAST_MODEL = "llama3.1:8b"
VOICE_SMART_MODEL = "phi4:14b"

# Multi-part trigger words: explicit conjunctions OR list/explain markers that
# signal the customer expects more than a one-liner. Kept short on purpose —
# false positives just route to the smart model (slightly slower, never worse).
_MULTIPART_MARKERS = (
    " and how ", " and what ", " and why ", " and which ", " and when ",
    " aur kaise", " aur kya", " aur kitna", " aur kaunsa",
    "list", "list out", "kaunse documents", "what documents", "which documents",
    "documents and", "formula", "calculation", "step by step",
)


def _is_multipart_question(q: str) -> bool:
    """Heuristic: does the question expect multiple distinct answers?
    Triggers: 2+ question marks, conjunction markers, or list/formula keywords."""
    if not q:
        return False
    ql = q.lower().strip()
    # 2+ question marks → definitely multi-part
    if ql.count("?") >= 2:
        return True
    # Trigger markers (substring match — fast and good enough)
    for marker in _MULTIPART_MARKERS:
        if marker in ql:
            return True
    return False


def _build_emi_calc_block(question: str, session_context: dict | None) -> str | None:
    """If the question is a calc question AND we can extract amount + tenure,
    return a deterministic COMPUTED EMI line for the prompt. Otherwise None."""
    if not is_calculation_question(question):
        return None
    amount = parse_amount(question)
    tenure = parse_tenure(question)
    # Fall back to session context (RM may have entered amount/tenure in UI)
    ctx = session_context or {}
    if amount is None and ctx.get("loan_amount"):
        amount = parse_amount(str(ctx["loan_amount"]))
    if tenure is None and ctx.get("tenure"):
        tenure = parse_tenure(str(ctx["tenure"]))
    if amount is None or tenure is None:
        return None
    try:
        calc = compute_emi(amount, tenure, annual_rate_pct=10.5)
        return format_emi_for_prompt(calc)
    except Exception:
        return None
from core.schemas import (
    TranscriptCleanRequest,
    VoiceAnswerRequest,
    SessionSummaryRequest,
)

router = APIRouter()


# ── Local-first text helpers (Ollama → OpenRouter fallback → raw passthrough) ──

async def _ollama_complete(prompt: str, max_tokens: int = 120, temperature: float = 0.0) -> str:
    """
    Hit local Ollama for a one-shot completion. Used by clean-transcript +
    session-summary so those endpoints don't depend on OpenRouter quota.
    Raises Exception on any failure — callers handle fallback.
    """
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{deps.rag_instance.base_url}/api/generate",
            json={
                "model": deps.rag_instance.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "top_k": 10,
                },
            },
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()


async def _openrouter_complete(prompt: str, max_tokens: int = 120) -> str:
    """
    Fallback: hit OpenRouter (used only if Ollama fails). Cycles through free
    models — same list as rag.engine._call_openrouter_fast but async + smaller.
    Raises Exception on total failure.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    models = [
        "meta-llama/llama-3.2-3b-instruct:free",
        "google/gemma-4-26b-a4b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ]
    last = "no models attempted"
    async with httpx.AsyncClient(timeout=15) as client:
        for m in models:
            try:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "HTTP-Referer": "http://localhost:8003",
                        "X-Title": "HDFC Live Assistant",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": m,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0,
                    },
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"].strip()
                if r.status_code in (429, 404):
                    last = f"HTTP {r.status_code} on {m}"
                    continue
                r.raise_for_status()
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                continue
    raise RuntimeError(f"All OpenRouter models failed: {last}")


# ── /api/voice/clean-transcript ──────────────────────────────────────────────

@router.post("/api/voice/clean-transcript")
async def clean_transcript(request: TranscriptCleanRequest):
    """
    Clean a raw spoken transcript into a proper searchable question.
    Removes filler words, fixes Hinglish, converts to clean English.

    Strategy (offline-first):
      1. Try local Ollama (llama3.2:3b)            — free, ~300-500ms, no rate limit
      2. Fall back to OpenRouter free models       — if Ollama is down
      3. Fall back to raw text                     — if both fail (graceful degrade)
    """
    prompt = f"""You receive a raw spoken sentence from an Indian customer
talking to a bank sales agent. The text may be in English, Hindi (Devanagari
script), or mixed.

Your job: convert it into ONE clean question.

OUTPUT FORMAT RULES (very important):
- If the input is pure English → keep it in English.
- If the input contains ANY Hindi (Devanagari or Hindi words) → output in
  ROMANIZED HINGLISH (Hindi written in English letters, mixed naturally with
  English banking terms). This is how Indians actually talk in chat.
- NEVER output Devanagari script.
- NEVER fully translate Hindi → formal English. Keep the natural Indian flavour.
- Remove fillers (matlab, na, bhai, uh, hmm, basically, like, ok).
- Fix obvious STT mistakes (paj→paanch, lag→lakh, intresh→interest, sal→saal).
- Keep ALL numbers, amounts, tenures exactly as said.
- Output ONLY the cleaned question. No explanation. No quotes.

Examples:

Input: "मुझे पाच लाख का लोन दस साल के लिए चाहिए, interest kitna lagega"
Output: Mujhe 5 lakh ka loan 10 saal ke liye chahiye, interest kitna lagega?

Input: "wo 20 lakh ka loan matlab processing bhi hoti hai kya"
Output: 20 lakh ka loan pe processing fee kitni hoti hai?

Input: "bhai EMI kitni hogi roughly 5 saal ke liye"
Output: 5 saal ke liye EMI kitni hogi?

Input: "documents kya kya chahiye personal loan ke liye"
Output: Personal loan ke liye kaunse documents chahiye?

Input: "what is the processing fee for a 20 lakh loan"
Output: What is the processing fee for a 20 lakh loan?

Input: "मुझे कैलकुलेट करके दो कि अगर मैंने पांच लाख का लोन दस साल के लिए लिया तो कितना इंटरेस्ट लगेगा"
Output: Agar maine 5 lakh ka loan 10 saal ke liye liya to kitna interest lagega?

Raw input: {request.raw_text}
Cleaned question:"""

    # 1) Local Ollama
    try:
        t0 = time.time()
        cleaned = await _ollama_complete(prompt, max_tokens=60, temperature=0.0)
        if cleaned:
            # Strip surrounding quotes / accidental "Output:" prefix the model may add.
            cleaned = cleaned.strip().strip('"').strip("'")
            if cleaned.lower().startswith("output:"):
                cleaned = cleaned[7:].strip()
            log.info(f"[CLEAN] ollama ok in {int((time.time()-t0)*1000)}ms")
            return JSONResponse(content={
                "success": True,
                "cleaned": cleaned,
                "engine": "ollama",
            })
    except Exception as e:
        log.warning(f"[CLEAN] ollama failed: {e} — trying OpenRouter")

    # 2) OpenRouter fallback
    try:
        cleaned = await _openrouter_complete(prompt, max_tokens=60)
        cleaned = cleaned.strip().strip('"').strip("'")
        return JSONResponse(content={
            "success": True,
            "cleaned": cleaned,
            "engine": "openrouter",
        })
    except Exception as e:
        log.warning(f"[CLEAN] openrouter failed too: {e} — returning raw")

    # 3) Graceful degrade
    return JSONResponse(content={
        "success": True,
        "cleaned": request.raw_text,
        "engine": "raw",
    })


# ── /api/voice/enroll ────────────────────────────────────────────────────────

@router.post("/api/voice/enroll")
async def voice_enroll(file: UploadFile = File(...)):
    """
    Enroll the salesperson's voice. Accepts a short audio clip (5–8 seconds
    of the RM speaking), sends it to Deepgram with diarisation, and returns
    the dominant speaker label in that clip. That label is then used by the
    frontend to filter out RM speech in the always-on Auto Filter mode.
    """
    deepgram_key = (
        os.environ.get("DEEPGRAM_API_KEY")
        or os.environ.get("VITE_DEEPGRAM_API_KEY")
        or ""
    )
    if not deepgram_key:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "DEEPGRAM_API_KEY not configured on backend."}
        )

    try:
        audio = await file.read()
        if len(audio) < 1000:
            return JSONResponse(content={
                "success": False,
                "error": "Enrollment clip is too short — please speak for at least 3 seconds."
            })

        content_type = file.content_type or "audio/webm"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/listen"
                "?language=en-IN&punctuate=true&smart_format=true&diarize=true",
                headers={
                    "Authorization": f"Token {deepgram_key}",
                    "Content-Type": content_type,
                },
                content=audio,
            )

        if resp.status_code != 200:
            log.warning(f"[ENROLL] Deepgram returned {resp.status_code}: {resp.text[:200]}")
            return JSONResponse(content={
                "success": False,
                "error": f"Deepgram error {resp.status_code}"
            })

        data = resp.json()
        alt = (
            data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
        )
        words = alt.get("words", [])
        transcript = alt.get("transcript", "")

        if not words:
            return JSONResponse(content={
                "success": False,
                "error": "No speech detected in enrollment clip — please speak louder and try again."
            })

        counts: dict = {}
        for w in words:
            speaker = int(w.get("speaker", 0))
            counts[speaker] = counts.get(speaker, 0) + 1
        rm_speaker_id = max(counts.items(), key=lambda x: x[1])[0]

        log.info(
            f"[ENROLL] rm_speaker_id={rm_speaker_id}  "
            f"speakers={len(counts)}  words={len(words)}  preview='{transcript[:80]}'"
        )
        return JSONResponse(content={
            "success": True,
            "rm_speaker_id": rm_speaker_id,
            "speakers_detected": len(counts),
            "word_count": len(words),
            "transcript_preview": transcript[:200],
        })

    except Exception as e:
        log.error(f"[ENROLL] Exception: {e}")
        return JSONResponse(content={"success": False, "error": str(e)})


# ── /api/voice/stt — local Whisper fallback (when Deepgram fails) ────────────

@router.post("/api/voice/stt")
async def voice_stt(
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    """
    Local speech-to-text via faster-whisper. Used by the frontend as a
    Deepgram fallback — when the Deepgram call fails (network, quota, key
    invalid), the same audio chunk is retried against this endpoint.

    Form fields:
      file:     the audio blob (webm/opus, mp4, wav, mp3, ogg — all OK)
      language: 'auto' | 'en' | 'hi'   (default: 'auto')

    Returns:
      {success, transcript, language, confidence, ms, duration_s, engine: "whisper"}
    """
    try:
        from core.whisper_stt import get_engine
    except ImportError as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Whisper STT not installed: {e}"},
        )

    try:
        audio = await file.read()
        content_type = file.content_type or "audio/webm"

        loop = asyncio.get_event_loop()
        engine = get_engine()
        t0 = time.time()
        # Whisper inference is CPU-heavy — wrap in thread executor so it
        # doesn't block the asyncio event loop.
        result = await loop.run_in_executor(
            None,
            lambda: engine.transcribe(
                audio, content_type=content_type, language_hint=language
            ),
        )
        log.info(
            f"[STT] dur={result['duration_s']}s lang={result['language']} "
            f"chars={len(result['transcript'])} in {int((time.time()-t0)*1000)}ms"
        )
        return JSONResponse(content={"success": True, "engine": "whisper", **result})

    except ValueError as e:
        return JSONResponse(content={"success": False, "error": str(e)})
    except Exception as e:
        log.error(f"[STT] {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# ── /api/voice/answer/stream ─────────────────────────────────────────────────

@router.post("/api/voice/answer/stream")
async def voice_answer_stream(request: VoiceAnswerRequest):
    """
    Streaming SSE endpoint — streams Ollama tokens word by word.
    Frontend receives 'data: {"token":"...","done":false}' lines.
    Falls back to one-shot response if Ollama streaming fails.

    Pipeline:
      1. Demo cache hit → return pre-canned answer instantly (no LLM call).
      2. RAG retrieve + Ollama stream (the normal path).
      3. Refined-answer hint: backend sets refinable=true on small-model
         answers so the frontend can call /api/voice/answer/refine to upgrade
         the answer in the background (two-stage UX).
    """
    q = request.question.strip()
    log.info(f"[STREAM] question='{q}' lang_hint={request.language}")

    if not q:
        log.warning("[STREAM] Empty question — skipping")
        async def empty_stream():
            yield f'data: {json.dumps({"token": "Empty question.", "done": True, "sources": []})}\n\n'
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    # ── Demo cache pre-check (instant 0ms answers for common questions) ────────
    cache_hit = demo_cache.lookup(q)
    if cache_hit:
        log.info(f"[CACHE] hit similarity={cache_hit['similarity']} kw={cache_hit['matched_keywords']}")
        answer_text = cache_hit["answer"]
        sources = cache_hit["sources"]

        async def cached_stream():
            # Emit the answer in one chunk — the frontend's token-append loop
            # treats this as a single render with no perceptible delay.
            yield f'data: {json.dumps({"token": answer_text, "done": False, "sources": []})}\n\n'
            yield f'data: {json.dumps({"token": "", "done": True, "sources": sources, "engine": "demo-cache", "refinable": False, "similarity": cache_hit["similarity"]})}\n\n'

        return StreamingResponse(
            cached_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
        log.warning("[STREAM] No documents loaded")
        async def no_docs():
            yield f'data: {json.dumps({"token": "No documents loaded.", "done": True, "sources": []})}\n\n'
        return StreamingResponse(no_docs(), media_type="text/event-stream")

    # Devanagari script → override to Hindi; Latin script → trust frontend hint
    language = detect_language(q, frontend_hint=request.language)
    log.info(f"[STREAM] language={language}  type={request.question_type}")

    # Retrieve context in a thread (same query expansion as Ask Me)
    loop = asyncio.get_event_loop()
    t0 = time.time()
    context, sources = await loop.run_in_executor(
        None,
        lambda: deps.rag_instance._retrieve_relevant_context(
            deps.rag_instance._expand_query(q), top_k=3
        ),
    )
    log.info(f"[STREAM] RAG retrieved {len(sources)} sources in {int((time.time()-t0)*1000)}ms")

    if not context or len(context.strip()) < 50:
        log.warning("[STREAM] Not enough context found in documents")
        async def not_found():
            yield f'data: {json.dumps({"token": "Not in documents.", "done": True, "sources": []})}\n\n'
        return StreamingResponse(not_found(), media_type="text/event-stream")

    # ── GPT-4o-mini path: labeled-transcript approach ─────────────────────
    if request.labeled_transcript:
        or_key_lt = os.environ.get("OPENROUTER_API_KEY", "")
        if or_key_lt:
            gpt_system = (
                "You are Vikram — HDFC Bank's live AI sales coach on an active call.\n"
                "The conversation has two speakers labeled Speaker 1 and Speaker 2.\n"
                "One is the HDFC agent (explains things, says 'Sir', quotes EMI/rates, uses 'Main batata hoon').\n"
                "One is the customer (asks questions, raises objections, has concerns).\n"
                "Read the conversation and identify who is the customer from content.\n\n"
                "YOUR JOB: give the AGENT a short spoken hint they can use RIGHT NOW.\n"
                "Rules:\n"
                "- 2-3 sentences max. Natural spoken language, not a document.\n"
                "- Match the customer's language (Hinglish / Hindi / English).\n"
                "- Use ONLY facts from the DOCUMENT CONTEXT. Never invent rates or amounts.\n"
                "- If the agent spoke last OR no clear customer question: reply exactly WAIT\n"
                "- Never start with 'As per', 'According to', 'Here is'."
            )
            gpt_user = (
                f"CONVERSATION (latest at bottom):\n{request.labeled_transcript}\n\n"
                f"HDFC DOCUMENT CONTEXT:\n{context}\n\n"
                "Agent's next line:"
            )

            async def gpt_stream():
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=or_key_lt, base_url="https://openrouter.ai/api/v1")
                try:
                    stream = await client.chat.completions.create(
                        model="openai/gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": gpt_system},
                            {"role": "user",   "content": gpt_user},
                        ],
                        max_tokens=200,
                        temperature=0,
                        stream=True,
                    )
                    full = ""
                    async for chunk in stream:
                        token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                        if token:
                            full += token
                            yield f'data: {json.dumps({"token": token, "done": False, "sources": []})}\n\n'
                    is_wait = full.strip().upper() == "WAIT"
                    yield f'data: {json.dumps({"token": "", "done": True, "sources": [] if is_wait else sources, "engine": "openrouter/gpt-4o-mini", "wait": is_wait, "refinable": False})}\n\n'
                except Exception as exc:
                    log.error(f"[GPT-STREAM] {exc}")
                    yield f'data: {json.dumps({"token": "", "done": True, "sources": [], "engine": "openrouter-error", "wait": True, "refinable": False})}\n\n'

            log.info("[STREAM] Using OpenRouter gpt-4o-mini (labeled-transcript path)")
            return StreamingResponse(
                gpt_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            log.warning("[STREAM] OPENROUTER_API_KEY not set — falling through to Ollama")

    # ── Calculation router: inject deterministic EMI + pick smarter model ─
    # If this is an EMI/calc Q and we can extract amount+tenure, compute the
    # numbers in Python (always correct) and prepend them to the RAG context
    # so the LLM has nothing left to do but phrase them naturally. Also swap
    # to the smarter model — calc Qs are infrequent enough that the +1-2s
    # latency is acceptable, and worth it for correct numbers.
    emi_block = _build_emi_calc_block(q, request.session_context)
    multipart = _is_multipart_question(q)
    chosen_model = VOICE_SMART_MODEL if (emi_block or multipart) else VOICE_FAST_MODEL
    if emi_block:
        log.info(f"[STREAM] calc-question detected → {chosen_model}; {emi_block}")
        context = f"{emi_block}\n\n{context}"
    elif multipart:
        log.info(f"[STREAM] multi-part question detected → {chosen_model}")

    # Tag question type as multi_part so the prompt picks the multi-part rule
    effective_qtype = "multi_part" if multipart and not emi_block else (request.question_type or "factual")

    prompt = deps.rag_instance._build_voice_prompt(
        q,
        effective_qtype,
        context,
        request.session_context or {},
        request.history,
        language=language,
    )

    t_llm = time.time()
    token_count = 0

    # Token budget: generous for factual questions, larger for multi-part / calc
    # questions so the answer is never cut off mid-sentence.
    num_predict = 360 if (emi_block or multipart) else 220

    async def token_stream():
        nonlocal token_count
        or_key_s = os.environ.get("OPENROUTER_API_KEY", "")

        if or_key_s:
            # ── OpenRouter gpt-4o-mini — primary path ─────────────────────
            # ~300-500ms first token vs ~1-2s for local Ollama.
            from openai import AsyncOpenAI
            log.info(f"[STREAM] OpenRouter gpt-4o-mini, max_tokens={num_predict}...")
            try:
                client = AsyncOpenAI(api_key=or_key_s, base_url="https://openrouter.ai/api/v1")
                stream = await client.chat.completions.create(
                    model="openai/gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=num_predict,
                    temperature=0,
                    stream=True,
                )
                async for chunk in stream:
                    token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                    if token:
                        token_count += 1
                        if token_count == 1:
                            log.info(f"[STREAM] First token in {int((time.time()-t_llm)*1000)}ms")
                        yield f'data: {json.dumps({"token": token, "done": False, "sources": []})}\n\n'
                log.info(f"[STREAM] Done — {token_count} tokens in {int((time.time()-t_llm)*1000)}ms")
                yield f'data: {json.dumps({"token": "", "done": True, "sources": sources, "engine": "openrouter/gpt-4o-mini", "refinable": False})}\n\n'
                return
            except Exception as e:
                log.error(f"[STREAM] OpenRouter failed: {e} — falling back to Ollama")

        # ── Ollama fallback ───────────────────────────────────────────────
        log.info(f"[STREAM] Ollama ({chosen_model}), num_predict={num_predict}...")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                async with client.stream(
                    "POST",
                    f"{deps.rag_instance.base_url}/api/generate",
                    json={
                        "model": chosen_model,
                        "prompt": prompt,
                        "stream": True,
                        "options": {"temperature": 0, "num_predict": num_predict, "top_k": 10},
                    },
                ) as response:
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            token = data.get("response", "")
                            if token:
                                token_count += 1
                                if token_count == 1:
                                    log.info(f"[STREAM] First token in {int((time.time()-t_llm)*1000)}ms")
                                yield f'data: {json.dumps({"token": token, "done": False, "sources": []})}\n\n'
                            if data.get("done"):
                                log.info(f"[STREAM] Done — {token_count} tokens in {int((time.time()-t_llm)*1000)}ms")
                                yield f'data: {json.dumps({"token": "", "done": True, "sources": sources, "engine": "ollama-fast", "refinable": False})}\n\n'
                                return
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            log.error(f"[STREAM] Ollama streaming failed: {e} — falling back to one-shot")
            try:
                answer = await loop.run_in_executor(
                    None, lambda: deps.rag_instance._call_ollama_fast(prompt)
                )
            except Exception as e2:
                log.error(f"[STREAM] Fallback also failed: {e2}")
                answer = f"Error: {str(e)}"
            yield f'data: {json.dumps({"token": answer, "done": True, "sources": sources})}\n\n'

    return StreamingResponse(
        token_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /api/voice/answer (non-streaming fallback) ───────────────────────────────

@router.post("/api/voice/answer")
async def voice_answer(request: VoiceAnswerRequest):
    """Non-streaming fast voice answer. Strictly grounded in loaded HDFC docs."""
    try:
        if not request.question.strip():
            return JSONResponse(content={"success": False, "answer": "Empty question received.", "sources": []})

        # Demo cache pre-check — instant 0ms answer for common questions.
        cache_hit = demo_cache.lookup(request.question)
        if cache_hit:
            return JSONResponse(content={
                "success": True,
                "answer": cache_hit["answer"],
                "sources": cache_hit["sources"],
                "engine": "demo-cache",
                "similarity": cache_hit["similarity"],
                "refinable": False,
            })

        if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
            return JSONResponse(content={"success": False, "answer": "No documents loaded.", "sources": []})

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: deps.rag_instance.answer_live_question(
                question=request.question,
                question_type=request.question_type,
                session_context=request.session_context,
                history=request.history,
            ),
        )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"success": False, "answer": f"Error: {str(e)}", "sources": []})


# ── /api/voice/answer/refine — two-stage upgrade (slower, better model) ──────

# Refine now routes through core/llm.call_llm() (model_role="chat") so it
# uses whatever the operator configured: OpenRouter gpt-4o-mini by default,
# or the local Ollama CHAT_MODEL when LLM_PROVIDER=ollama.
# The old hardcoded free-model list is gone — those models are removed/rate-limited.


@router.post("/api/voice/answer/refine")
async def voice_answer_refine(request: VoiceAnswerRequest):
    """
    Two-stage refinement endpoint.

    The frontend has already shown the user a fast first-stage answer
    (streamed from llama3.2:3b). It now calls this endpoint in the
    background. A larger model re-answers the same question with the same
    RAG context, and the frontend silently swaps the answer in if the
    refined version arrives within a few seconds.

    If refinement fails for any reason we return success=False — the
    frontend just keeps the original fast answer (graceful degrade).
    """
    q = request.question.strip()
    if not q:
        return JSONResponse(content={"success": False, "error": "Empty question."})

    # Never refine a cached demo answer — the vetted text IS the canonical answer.
    if demo_cache.lookup(q) is not None:
        return JSONResponse(content={
            "success": False,
            "skipped": True,
            "reason": "cached",
        })

    if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
        return JSONResponse(content={"success": False, "error": "No documents loaded."})

    language = detect_language(q, frontend_hint=request.language)

    loop = asyncio.get_event_loop()
    t_rag = time.time()
    context, sources = await loop.run_in_executor(
        None,
        lambda: deps.rag_instance._retrieve_relevant_context(
            deps.rag_instance._expand_query(q), top_k=5
        ),
    )
    log.info(f"[REFINE] RAG {len(sources)} sources in {int((time.time()-t_rag)*1000)}ms")

    if not context or len(context.strip()) < 50:
        return JSONResponse(content={
            "success": False,
            "skipped": True,
            "reason": "no-context",
        })

    prompt = deps.rag_instance._build_voice_prompt(
        q,
        request.question_type or "factual",
        context,
        request.session_context or {},
        request.history,
        language=language,
    )

    # Route through core/llm — uses the configured model (gpt-4o-mini via
    # OpenRouter, or OLLAMA_CHAT_MODEL when LLM_PROVIDER=ollama). No more
    # hardcoded free-model list.
    from core.llm import call_llm, resolved_model, LLM_PROVIDER as _LLM_PROVIDER
    engine_name = f"openrouter/{resolved_model('chat')}" if _LLM_PROVIDER != "ollama" else f"ollama/{resolved_model('chat')}"
    t_llm = time.time()
    try:
        answer = await loop.run_in_executor(
            None,
            lambda: call_llm(
                prompt,
                model_role="chat",
                temperature=0,
                max_tokens=300,
                timeout=25,
            ),
        )
        if not answer:
            raise ValueError("empty response from LLM")
        log.info(f"[REFINE] {engine_name} ok in {int((time.time()-t_llm)*1000)}ms len={len(answer)}")
        return JSONResponse(content={
            "success": True,
            "answer": answer,
            "sources": sources,
            "engine": engine_name,
            "model": resolved_model("chat"),
            "ms": int((time.time() - t_llm) * 1000),
        })
    except Exception as exc:
        log.warning(f"[REFINE] {engine_name} failed: {exc}")
        return JSONResponse(content={
            "success": False,
            "skipped": True,
            "reason": "llm-error",
            "error": str(exc),
        })


# ── /api/voice/demo-cache — list pre-cached questions (for demo UI) ──────────

@router.get("/api/voice/demo-cache")
async def list_demo_cache():
    """Return the list of pre-loaded demo cache questions + count."""
    return JSONResponse(content={
        "success": True,
        "count": demo_cache.size(),
        "questions": demo_cache.all_questions(),
    })


@router.post("/api/voice/demo-cache/reload")
async def reload_demo_cache():
    """Reload demo_cache.json from disk without restarting the server."""
    n = demo_cache.reload()
    return JSONResponse(content={"success": True, "count": n})


# ── /api/voice/session-summary ───────────────────────────────────────────────

@router.post("/api/voice/session-summary")
async def voice_session_summary(request: SessionSummaryRequest):
    """
    End-of-session CRM debrief: objections raised, competitors mentioned, NBA.
    Tries local Ollama first (free, unlimited), then OpenRouter as fallback.
    """
    try:
        if not request.conversation:
            return JSONResponse(content={"success": False, "summary": "Empty conversation."})

        transcript = ""
        for msg in request.conversation:
            role = msg.get("role", "Unknown")
            content = msg.get("content", "")
            transcript += f"{role}: {content}\n"

        prompt = f"""You are a CRM assistant for HDFC Bank. Summarize the following sales meeting transcript.

Provide a debrief containing:
1. Objections Raised: (List any concerns or objections the customer had)
2. Competitor Mentions: (Did they mention Bajaj, SBI, ICICI, etc.? Provide a heatmap summary)
3. Next Best Action (NBA): (Recommend exactly what the salesperson should do next to close the deal)

Transcript:
{transcript}

Generate CRM Debrief:"""

        # Local Ollama first — no quota, fully offline.
        try:
            summary = await _ollama_complete(prompt, max_tokens=400, temperature=0.2)
            if summary:
                return JSONResponse(content={"success": True, "summary": summary, "engine": "ollama"})
        except Exception as e:
            log.warning(f"[SUMMARY] ollama failed: {e} — trying OpenRouter")

        # OpenRouter fallback
        try:
            summary = await _openrouter_complete(prompt, max_tokens=400)
            return JSONResponse(content={"success": True, "summary": summary, "engine": "openrouter"})
        except Exception as e:
            return JSONResponse(content={"success": False, "error": str(e)})

    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)})


# ── /api/voice/voiceprint/* — real-time agent-voice filter ────────────────────

@router.post("/api/voice/voiceprint/enroll")
async def voiceprint_enroll(
    file: UploadFile = File(...),
    name: str = Form("RM"),
):
    """
    Acoustic fingerprint enrolment.
    Accepts a 5+ second audio clip, extracts a speaker embedding
    (Resemblyzer or pyannote), persists it to SQLite, and returns
    a profile_id that the frontend uses on every live chunk.
    """
    try:
        from core.voiceprint import get_engine
    except ImportError as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Voiceprint backend not installed: {e}"},
        )

    try:
        audio = await file.read()
        content_type = file.content_type or "audio/webm"

        loop = asyncio.get_event_loop()
        # Embedding model runs on CPU — wrap in thread so we don't block the loop.
        engine = get_engine()
        t0 = time.time()
        meta = await loop.run_in_executor(
            None, lambda: engine.enroll(audio, content_type=content_type, name=name)
        )
        log.info(
            f"[VPRINT-ENROLL] {meta['profile_id']} backend={meta['backend']} "
            f"dur={meta['duration_s']}s in {int((time.time()-t0)*1000)}ms"
        )
        return JSONResponse(content={"success": True, **meta})

    except ValueError as e:
        return JSONResponse(content={"success": False, "error": str(e)})
    except Exception as e:
        log.error(f"[VPRINT-ENROLL] {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/api/voice/voiceprint/enroll-sample")
async def voiceprint_enroll_sample(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
):
    """
    Append another voice sample to an existing profile. Used by the multi-step
    guided enrolment (English → Hindi → Hinglish) to build a fingerprint that
    captures the user's voice across all the languages they speak in real calls.
    The stored embedding is updated via incremental running mean.
    """
    try:
        from core.voiceprint import get_engine
    except ImportError as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Voiceprint backend not installed: {e}"},
        )

    try:
        audio = await file.read()
        content_type = file.content_type or "audio/webm"

        loop = asyncio.get_event_loop()
        engine = get_engine()
        t0 = time.time()
        meta = await loop.run_in_executor(
            None,
            lambda: engine.add_sample(
                audio, profile_id=profile_id, content_type=content_type
            ),
        )
        log.info(
            f"[VPRINT-SAMPLE] {meta['profile_id']} samples={meta['sample_count']} "
            f"dur={meta['duration_s']}s in {int((time.time()-t0)*1000)}ms"
        )
        return JSONResponse(content={"success": True, **meta})

    except KeyError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except ValueError as e:
        return JSONResponse(content={"success": False, "error": str(e)})
    except Exception as e:
        log.error(f"[VPRINT-SAMPLE] {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/api/voice/voiceprint/verify")
async def voiceprint_verify(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
):
    """
    Score one audio chunk against an enrolled voiceprint.
    Returns {is_agent, similarity, threshold} — frontend skips chunks
    where is_agent=true so only customer audio reaches the LLM.
    """
    if not profile_id:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "profile_id is required"},
        )
    try:
        from core.voiceprint import get_engine
    except ImportError as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Voiceprint backend not installed: {e}"},
        )

    try:
        audio = await file.read()
        content_type = file.content_type or "audio/webm"

        loop = asyncio.get_event_loop()
        engine = get_engine()
        t0 = time.time()
        result = await loop.run_in_executor(
            None,
            lambda: engine.verify(audio, profile_id=profile_id, content_type=content_type),
        )
        result["ms"] = int((time.time() - t0) * 1000)
        log.info(
            f"[VPRINT-VERIFY] profile={profile_id[:8]} "
            f"sim={result.get('similarity')}/{result.get('threshold')} "
            f"hf={result.get('hf_ratio')}/<{result.get('hf_customer_max')} "
            f"rms={result.get('rms')}/<{result.get('rms_threshold')} "
            f"decision={result.get('decision')} "
            f"(phone_customer={result.get('phone_customer')} emb_vote={result.get('vote_embedding')}) "
            f"is_agent={result.get('is_agent')} dur={result.get('duration_s')}s in {result['ms']}ms"
            + (f" reason={result['reason']}" if result.get('reason') else "")
        )
        return JSONResponse(content={"success": True, **result})

    except KeyError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except ValueError as e:
        return JSONResponse(content={"success": False, "error": str(e)})
    except Exception as e:
        log.error(f"[VPRINT-VERIFY] {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get("/api/voice/voiceprint/profiles")
async def voiceprint_list():
    """List all enrolled voiceprints (metadata only — no embeddings exposed)."""
    try:
        from core.voiceprint import get_engine, SIMILARITY_THRESHOLD, _BACKEND
        engine = get_engine()
        return JSONResponse(content={
            "success": True,
            "backend": _BACKEND,
            "threshold": SIMILARITY_THRESHOLD,
            "profiles": engine.list_profiles(),
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.delete("/api/voice/voiceprint/{profile_id}")
async def voiceprint_delete(profile_id: str):
    """Remove a voiceprint by id."""
    try:
        from core.voiceprint import get_engine
        engine = get_engine()
        deleted = engine.delete(profile_id)
        return JSONResponse(content={"success": deleted, "deleted": deleted})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# ── /api/voice/adaptive/* — online speaker clustering (parallel to voiceprint) ─
# These endpoints are wired to core/adaptive_speaker.py. They are FULLY
# ADDITIVE — the legacy voiceprint flow above is untouched, so the frontend
# can pick which approach to test without risking the working pre-enrolment
# flow. See the design notes at the top of core/adaptive_speaker.py.


@router.post("/api/voice/adaptive/start")
async def adaptive_start():
    """Begin a new adaptive clustering session.

    Returns: {success, session_id, calib_min_chunks}
    The frontend stores session_id and sends it with every /verify call.
    """
    try:
        from core.adaptive_speaker import get_engine, CALIB_MIN_CHUNKS
        sid = get_engine().start_session()
        return JSONResponse(content={
            "success": True,
            "session_id": sid,
            "calib_min_chunks": CALIB_MIN_CHUNKS,
        })
    except Exception as e:
        log.error(f"[ADAPTIVE-START] {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/api/voice/adaptive/verify")
async def adaptive_verify(
    file: UploadFile = File(...),
    session_id: str = Form(...),
):
    """Score one audio chunk inside an ongoing adaptive session.

    First N chunks: phase="calibrating", is_agent always False (everything
    is passed through to the LLM while the system learns the two speakers).
    After lock: phase="locked", is_agent reflects whether this chunk was
    assigned to the agent cluster.
    """
    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "session_id is required"},
        )
    try:
        from core.adaptive_speaker import get_engine
    except ImportError as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Adaptive engine not available: {e}"},
        )

    try:
        audio = await file.read()
        content_type = file.content_type or "audio/webm"

        loop = asyncio.get_event_loop()
        engine = get_engine()
        t0 = time.time()
        result = await loop.run_in_executor(
            None,
            lambda: engine.process(session_id, audio, content_type=content_type),
        )
        result["ms"] = int((time.time() - t0) * 1000)
        log.info(
            f"[ADAPTIVE-VERIFY] session={session_id[:8]} phase={result.get('phase')} "
            f"is_agent={result.get('is_agent')} reason={result.get('reason')} "
            f"chunks={result.get('chunks_seen')} dur={result.get('duration_s')}s "
            f"in {result['ms']}ms"
            + (f" sim_a={result['sim_agent']} sim_c={result['sim_customer']}"
               if 'sim_agent' in result else "")
        )
        return JSONResponse(content={"success": True, **result})

    except KeyError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    except ValueError as e:
        return JSONResponse(content={"success": False, "error": str(e)})
    except Exception as e:
        log.error(f"[ADAPTIVE-VERIFY] {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get("/api/voice/adaptive/{session_id}/stats")
async def adaptive_stats(session_id: str):
    """Inspect the current state of a session (useful for live debugging)."""
    try:
        from core.adaptive_speaker import get_engine
        stats = get_engine().stats(session_id)
        if stats is None:
            return JSONResponse(status_code=404, content={
                "success": False, "error": "session not found"
            })
        return JSONResponse(content={"success": True, **stats})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/api/voice/adaptive/{session_id}/reset")
async def adaptive_reset(session_id: str):
    """Drop a session (e.g. on Stop / End-of-call)."""
    try:
        from core.adaptive_speaker import get_engine
        dropped = get_engine().reset(session_id)
        return JSONResponse(content={"success": dropped, "dropped": dropped})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# ── WebSocket /ws/voice ──────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════════
# NEW JUNE 2026 — Two-speaker live path
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design overview:
#   1. VAD fires → frontend sends one audio blob + session_id
#   2. Backend: Whisper STT + MFCC embedding run CONCURRENTLY on the same bytes
#   3. Speaker gets labeled Person 1 / Person 2 (adaptive K-means, no enrolment)
#   4. Filler pre-gate: ≤2-word ack → instant WAIT, no LLM, no RAG
#   5. RAG retrieve (top_k=4) + EMI calc run CONCURRENTLY (need transcript)
#   6. GPT-4o-mini streams the reply (or "WAIT" if agent spoke last)
#
# The voice FILTER (is_agent drop) is no longer active in this path. Both
# speakers flow through — GPT infers roles from content.
#
# Hard constraints upheld:
#   - Whisper-only STT  (core/whisper_stt.py)
#   - gpt-4o-mini for decisions  (OPENAI_API_KEY)
#   - EMI computed in Python (_build_emi_calc_block), never by the model
#   - GROUNDING_RULE via prompts/live_assistant.py

# Known filler / acknowledgement words (single tokens or short phrases) that
# carry no question the agent needs to answer. Pre-gate hit → instant WAIT
# with zero LLM calls. Add more as you encounter them in Hindi/English calls.
_FILLER_WORDS: frozenset[str] = frozenset({
    # Hindi/Hinglish acks (1-word)
    "haan", "han", "ha", "ji", "theek", "thik",
    "achha", "acha", "accha", "okay", "ok", "fine",
    "hmm", "hm", "uh", "ah", "yeah", "yes", "no", "na", "nahi", "nhi",
    "shukriya", "dhanyawad", "thankyou", "bilkul", "zaroor", "sure",
    # 2-word acks
    "ji haan", "ji han", "theek hai", "thik hai", "thank you",
    "of course", "haan ji", "han ji", "acha ji",
    # 3-word acks
    "haan theek hai", "han theek hai", "acha theek hai",
    "ji bilkul haan", "yes of course", "that is fine",
})


def _is_filler_utterance(text: str) -> bool:
    """True when the utterance is pure filler — pre-gate to skip LLM + RAG."""
    import re as _re
    # Strip trailing punctuation so "Thank you." matches "thank you"
    cleaned = _re.sub(r'[^\w\s]', '', text.strip().lower())
    words = cleaned.split()
    if not words:
        return True
    phrase = " ".join(words)
    if phrase in _FILLER_WORDS:
        return True
    # Short single-word utterances (≤3 chars) are almost always filler
    if len(words) == 1 and len(words[0]) <= 3:
        return True
    return False


# ── Whisper hallucination detection ─────────────────────────────────────────
# Whisper (especially large-v3-turbo) produces these phrases on near-silent
# audio instead of an empty string. Catching them here prevents junk turns
# appearing in the transcript pane and avoids a wasted RAG + LLM call.
_HALLUCINATION_PHRASES: frozenset[str] = frozenset({
    "it's a very good thing to do",
    "it is a very good thing to do",
    "it's a great thing to do",
    "thank you for watching",
    "thank you very much for joining us",
    "thanks for watching",
    "please subscribe",
    "don't forget to subscribe",
    "like and subscribe",
    "see you in the next video",
    "i'll see you in the next one",
    # NOTE: never add single words here — substring match will suppress real speech.
    # e.g. "you" would kill "What do you need?" "Can you explain?" etc.
})


def _is_whisper_hallucination(text: str) -> bool:
    """
    Return True when the Whisper transcript looks like a hallucination on
    near-silence. Three signals:
      1. Known hallucination phrase (substring match)
      2. Word repetition: one word ≥ 5 times AND ≥ 45 % of total words
      3. Numeric repetition: same number repeated 5+ times (e.g. "500, 500, …")
    """
    import re as _re
    from collections import Counter as _Counter

    if not text:
        return False
    lowered = text.lower().strip()

    # 1. Known phrases
    for phrase in _HALLUCINATION_PHRASES:
        if phrase in lowered:
            return True

    words = lowered.split()
    if not words:
        return False

    # 2. Repetition of any single word
    if len(words) >= 5:
        most_common_word, count = _Counter(words).most_common(1)[0]
        if count >= 5 and count / len(words) >= 0.45:
            return True

    # 3. Numeric repetition: "500, 500, 500, ..." or "500 500 500 ..."
    if _re.search(r'\b(\w+)\b[,\s]+(?:\1[,\s]+){4,}', lowered):
        return True

    return False


# ── /api/voice/live/session/start ────────────────────────────────────────────

@router.post("/api/voice/live/session/start")
async def live_session_start():
    """
    Create a fresh adaptive-clustering session for the new two-speaker live path.
    Returns {success, session_id, calib_min_chunks, whisper_model, whisper_device}.
    The frontend stores session_id and sends it with every /live/process call.
    """
    try:
        from core.adaptive_speaker import get_engine as get_adaptive, CALIB_MIN_CHUNKS
        from core.whisper_stt import _LIVE_MODEL_SIZE, _DEVICE
        sid = get_adaptive().start_session()
        log.info(f"[LIVE] new session={sid}")
        return JSONResponse(content={
            "success": True,
            "session_id": sid,
            "calib_min_chunks": CALIB_MIN_CHUNKS,
            "whisper_model": _LIVE_MODEL_SIZE,
            "whisper_device": _DEVICE,
        })
    except Exception as exc:
        log.error(f"[LIVE] session start failed: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={
            "success": False,
            "error": str(exc),
            "hint": "Restart the backend server if this follows a code change.",
        })


# ── /api/voice/live/process ──────────────────────────────────────────────────

@router.post("/api/voice/live/process")
async def live_process(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    language: str = Form("auto"),
    prev_transcript: str = Form("[]"),    # JSON: [{speaker, text}, …] last 9 turns
    session_context: str = Form("{}"),   # JSON: customer/loan context
    role_hint: str = Form(""),           # e.g. "Person 1 is the HDFC agent"
    two_speaker_mode: str = Form("false"),  # "true" = always-on two-speaker path
):
    """
    All-in-one SSE endpoint for the new two-speaker live path.

    SSE event shapes
    ─────────────────
    {"type":"turn",  "speaker":"Person 1|2", "transcript":"…", "stt_ms":N, "label_ms":N}
        ↳ emitted FIRST so the frontend can update the transcript pane immediately

    {"type":"wait",  "done":true,  "reason":"…", "stt_ms":N, "label_ms":N, "total_ms":N}
        ↳ emitted on filler gate / agent-spoke-last / no docs / no key

    {"token":"…",    "done":false, "sources":[]}
        ↳ streaming reply tokens (N events)

    {"token":"",     "done":true,  "wait":false,
     "sources":[…], "engine":"gpt-4o-mini",
     "stt_ms":N, "label_ms":N, "rag_ms":N, "first_token_ms":N, "total_ms":N}
        ↳ final event when streaming reply completes

    {"token":"",     "done":true,  "wait":true, …}
        ↳ final event when GPT outputs WAIT after seeing full transcript
    """
    import json as _json

    t_start = time.time()
    audio_bytes = await file.read()
    content_type = file.content_type or "audio/webm"

    # Two-speaker mode: GATE A threshold drops to 2 words (fragmented speech is
    # normal when both participants talk), and GATE C is skipped entirely (we
    # can't reliably trust speaker labels to decide "agent spoke last").
    is_two_speaker = two_speaker_mode.lower() in ("1", "true", "yes")

    try:
        ctx_dict = _json.loads(session_context) if session_context else {}
    except Exception:
        ctx_dict = {}

    try:
        prev_turns: list[dict] = _json.loads(prev_transcript) if prev_transcript else []
        if not isinstance(prev_turns, list):
            prev_turns = []
    except Exception:
        prev_turns = []

    async def _stream():
        loop = asyncio.get_event_loop()

        try:
            from core.whisper_stt import get_live_engine as get_whisper
        except ImportError as exc:
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":f"whisper_not_installed:{exc}","total_ms":0})}\n\n'
            return
        try:
            from core.adaptive_speaker import get_engine as get_adaptive
        except ImportError as exc:
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":f"adaptive_not_installed:{exc}","total_ms":0})}\n\n'
            return

        whisper = get_whisper()
        adaptive = get_adaptive()

        # ── Step 1: Whisper STT  +  MFCC embedding  (CONCURRENT) ──────────
        t_stt = time.time()
        try:
            stt_fut = loop.run_in_executor(
                None,
                lambda: whisper.transcribe(
                    audio_bytes,
                    content_type=content_type,
                    language_hint=language,
                ),
            )
            label_fut = loop.run_in_executor(
                None,
                lambda: adaptive.label_utterance(
                    session_id,
                    audio_bytes,
                    content_type=content_type,
                ),
            )
            stt_result, label_result = await asyncio.gather(stt_fut, label_fut)
        except Exception as exc:
            log.error(f"[LIVE] STT/label error: {exc}")
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":f"stt_error:{exc}","total_ms":int((time.time()-t_start)*1000)})}\n\n'
            return

        stt_ms   = int((time.time() - t_stt) * 1000)
        label_ms = label_result.get("ms", 0)
        transcript = (stt_result.get("transcript") or "").strip()
        speaker    = label_result.get("label", "Unknown")
        stt_lang   = stt_result.get("language", "?")

        log.info(
            f"[LIVE] session={session_id[:8]} speaker={speaker} stt={stt_ms}ms "
            f"label={label_ms}ms lang={stt_lang} text='{transcript[:60]}'"
        )

        if not transcript:
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"empty_transcript","stt_ms":stt_ms,"label_ms":label_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
            return

        # ── Hallucination guard ────────────────────────────────────────────
        if _is_whisper_hallucination(transcript):
            log.warning(f"[LIVE] Whisper hallucination suppressed: '{transcript[:80]}'")
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"hallucination","stt_ms":stt_ms,"label_ms":label_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
            return

        # ── Step 2: Emit turn metadata — frontend updates transcript pane ──
        yield f'data: {json.dumps({"type":"turn","speaker":speaker,"transcript":transcript,"stt_ms":stt_ms,"label_ms":label_ms})}\n\n'

        # ── Step 3: Pre-gates (instant WAIT, no LLM cost) ─────────────────
        # GATE A — too short: background noise / partial utterance.
        # Two-speaker mode lowers the threshold to 2 words because natural
        # conversation produces more fragmented turns than push-to-talk use.
        word_count = len(transcript.split())
        gate_a_min = 2 if is_two_speaker else 3
        if word_count <= gate_a_min:
            log.info(f"[LIVE] word-count gate ({word_count} words, min={gate_a_min+1}) → WAIT: '{transcript}'")
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"too_short","stt_ms":stt_ms,"label_ms":label_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
            return

        # GATE B — pure filler
        if _is_filler_utterance(transcript):
            log.info(f"[LIVE] filler gate → WAIT (no LLM): '{transcript}'")
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"filler","stt_ms":stt_ms,"label_ms":label_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
            return

        # GATE C — two consecutive agent turns (agent explaining, not customer asking).
        # Skipped in two-speaker mode: speaker labels can be imperfect and we must
        # never suppress a suggestion just because the label happened to be wrong.
        if not is_two_speaker and prev_turns and role_hint:
            last_prev_speaker = prev_turns[-1].get("speaker", "") if prev_turns else ""
            agent_person = role_hint.split(" ")[0] if role_hint else ""
            if (last_prev_speaker == speaker == agent_person):
                log.info(f"[LIVE] agent-spoke-last gate → WAIT: '{transcript[:40]}'")
                yield f'data: {json.dumps({"type":"wait","done":True,"reason":"agent_spoke_last","stt_ms":stt_ms,"label_ms":label_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
                return

        # ── Step 4: Build rolling labeled transcript (max 8 turns) ─────────
        # Sliding window: keep last 7 prev + current = 8 total.
        # If we're dropping older turns, prepend a context hint from session entities.
        window = list(prev_turns[-7:])
        dropped_count = max(0, len(prev_turns) - 7)
        context_prefix = ""
        if dropped_count > 0 and ctx_dict:
            parts = []
            if ctx_dict.get("loan_amount"): parts.append(f"loan ₹{ctx_dict['loan_amount']}")
            if ctx_dict.get("tenure"):      parts.append(f"{ctx_dict['tenure']} months")
            if ctx_dict.get("loanType"):    parts.append(ctx_dict["loanType"])
            if parts:
                context_prefix = f"CONTEXT: earlier discussed {', '.join(parts)}.\n"
        window.append({"speaker": speaker, "text": transcript})
        labeled = context_prefix + "\n".join(f"{t['speaker']}: {t['text']}" for t in window)

        # ── Step 5: RAG + EMI calc (CONCURRENT; need transcript first) ─────
        if not (deps.rag_instance.structured_data or deps.rag_instance.documents):
            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"no_docs","stt_ms":stt_ms,"label_ms":label_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
            return

        t_rag = time.time()
        rag_fut = loop.run_in_executor(
            None,
            lambda: deps.rag_instance._retrieve_relevant_context(
                deps.rag_instance._expand_query(transcript), top_k=4
            ),
        )
        emi_fut = loop.run_in_executor(
            None,
            lambda: _build_emi_calc_block(transcript, ctx_dict),
        )
        (context, sources), emi_block = await asyncio.gather(rag_fut, emi_fut)
        rag_ms = int((time.time() - t_rag) * 1000)

        # Two-speaker coaching mode allows minimal RAG context (the transcript
        # itself carries enough signal for the LLM to coach on).  Push-to-talk
        # mode keeps the strict 30-char floor to avoid empty-context hallucination.
        min_context_len = 10 if is_two_speaker else 30
        if not context or len(context.strip()) < min_context_len:
            if is_two_speaker:
                # Proceed with empty context — the coaching prompt handles this
                context = ""
                sources = []
            else:
                log.warning("[LIVE] RAG returned insufficient context")
                yield f'data: {json.dumps({"type":"wait","done":True,"reason":"no_context","stt_ms":stt_ms,"label_ms":label_ms,"rag_ms":rag_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
                return

        # ── Step 6: LLM stream ────────────────────────────────────────────────
        # Two-speaker coaching mode uses LIVE_COACH_SYSTEM which ALWAYS generates
        # guidance — it never outputs WAIT.  Push-to-talk mode keeps the original
        # LIVE_ASSIST_SYSTEM which gates on speaker identity.
        from prompts.live_assistant import (
            LIVE_ASSIST_SYSTEM, build_live_assist_user,
            LIVE_COACH_SYSTEM,  build_live_coach_user,
        )

        _whisper_to_lang = {"en": "english", "hi": "hindi", "mr": "marathi"}
        # User-selected language always governs the pipeline — Whisper's detected
        # language (stt_lang) is only used as a fallback when no explicit hint is set.
        if language in ("en", "hi", "mr"):
            lang_directive = _whisper_to_lang[language]
        else:
            lang_directive = _whisper_to_lang.get(stt_lang, "english")

        if is_two_speaker:
            system_prompt = LIVE_COACH_SYSTEM
            user_msg = build_live_coach_user(
                labeled_transcript=labeled,
                context=context,
                language=lang_directive,
                emi_block=emi_block,
                role_hint=role_hint or None,
            )
            max_tok = 220   # coaching note: up to 4 sentences
        else:
            system_prompt = LIVE_ASSIST_SYSTEM
            user_msg = build_live_assist_user(
                labeled_transcript=labeled,
                context=context,
                language=lang_directive,
                emi_block=emi_block,
                role_hint=role_hint or None,
            )
            max_tok = 250   # PTT reply: up to 5 sentences for complex questions

        t_llm = time.time()
        full = ""
        first_token_ms: int | None = None

        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

        if openrouter_key:
            # ── GPT-4o-mini via OpenRouter (reused, connection-pooled client) ──
            engine_name = "openrouter/gpt-4o-mini"
            gpt_client = _get_gpt_client(openrouter_key)
            try:
                stream = await gpt_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_msg},
                    ],
                    max_tokens=max_tok,
                    temperature=0,
                    stream=True,
                )
                async for chunk in stream:
                    token = (
                        (chunk.choices[0].delta.content or "")
                        if chunk.choices else ""
                    )
                    if not token:
                        continue
                    full += token
                    if first_token_ms is None:
                        first_token_ms = int((time.time() - t_llm) * 1000)
                        log.info(
                            f"[LIVE] first token {first_token_ms}ms "
                            f"(stt={stt_ms} label={label_ms} rag={rag_ms} "
                            f"two_speaker={is_two_speaker})"
                        )
                        # In push-to-talk mode: early WAIT exit (first token only).
                        # In two-speaker coaching mode: never check — LIVE_COACH_SYSTEM
                        # is instructed never to output WAIT.
                        if not is_two_speaker and full.strip().upper() == "WAIT":
                            log.info("[LIVE] LLM WAIT on first token → skipping stream")
                            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"llm_wait","stt_ms":stt_ms,"label_ms":label_ms,"rag_ms":rag_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
                            return
                    yield f'data: {json.dumps({"token":token,"done":False,"sources":[]})}\n\n'

            except Exception as exc:
                log.error(f"[LIVE] GPT stream error: {exc}")
                yield f'data: {json.dumps({"token":"","done":True,"wait":True,"sources":[],"engine":"gpt-4o-mini-error","total_ms":int((time.time()-t_start)*1000)})}\n\n'
                return

        else:
            # ── Ollama fallback (no OpenRouter key) ───────────────────────
            ollama_url   = os.environ.get("OLLAMA_URL", "http://localhost:11434")
            ollama_model = os.environ.get("CALL_ASSIST_MODEL",
                           os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:14b"))
            engine_name  = ollama_model
            log.info(f"[LIVE] No OpenRouter key — Ollama fallback ({ollama_model})")

            combined_prompt = f"{system_prompt}\n\n{user_msg}"
            try:
                async with httpx.AsyncClient(timeout=60) as hx:
                    async with hx.stream(
                        "POST",
                        f"{ollama_url}/api/generate",
                        json={
                            "model": ollama_model,
                            "prompt": combined_prompt,
                            "stream": True,
                            "options": {"temperature": 0, "num_predict": max_tok},
                        },
                    ) as resp:
                        async for line in resp.aiter_lines():
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                token = data.get("response", "")
                                if token:
                                    full += token
                                    if first_token_ms is None:
                                        first_token_ms = int((time.time() - t_llm) * 1000)
                                        log.info(
                                            f"[LIVE] Ollama first token {first_token_ms}ms"
                                        )
                                        if not is_two_speaker and full.strip().upper() == "WAIT":
                                            log.info("[LIVE] Ollama WAIT on first token")
                                            yield f'data: {json.dumps({"type":"wait","done":True,"reason":"llm_wait","stt_ms":stt_ms,"label_ms":label_ms,"rag_ms":rag_ms,"total_ms":int((time.time()-t_start)*1000)})}\n\n'
                                            return
                                    yield f'data: {json.dumps({"token":token,"done":False,"sources":[]})}\n\n'
                                if data.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
            except Exception as exc:
                log.error(f"[LIVE] Ollama stream error: {exc}")
                yield f'data: {json.dumps({"token":"","done":True,"wait":True,"sources":[],"engine":f"ollama-error:{exc}","total_ms":int((time.time()-t_start)*1000)})}\n\n'
                return

        # Two-speaker coaching mode: full is always a coaching note, never WAIT.
        # Push-to-talk mode: full may be WAIT if GPT decided nothing needs saying.
        is_wait = (not is_two_speaker) and full.strip().upper() == "WAIT"
        total_ms = int((time.time() - t_start) * 1000)
        log.info(
            f"[LIVE] done coaching={is_two_speaker} wait={is_wait} total={total_ms}ms "
            f"stt={stt_ms} label={label_ms} rag={rag_ms} "
            f"first_token={first_token_ms} speaker={speaker} engine={engine_name}"
        )
        yield (
            f'data: {json.dumps({"token":"","done":True,"wait":is_wait,"sources":[] if is_wait else sources,"engine":engine_name,"stt_ms":stt_ms,"label_ms":label_ms,"rag_ms":rag_ms,"first_token_ms":first_token_ms,"total_ms":total_ms})}\n\n'
        )

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────────────────


@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """
    Legacy WebSocket path.
    Receives: {"type": "question", "text": "..."}
    Sends back: {"type": "answer", "text": "...", "sources": [...]}
    """
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "question":
                question = data.get("text", "").strip()
                if question:
                    result = deps.rag_instance.answer_live_question(question)
                    await websocket.send_json({
                        "type": "answer",
                        "text": result.get("answer", "Not in documents."),
                        "sources": result.get("sources", []),
                        "success": result.get("success", False),
                    })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass
