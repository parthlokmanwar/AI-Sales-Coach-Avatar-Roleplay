"""
FEATURE: Pitch Generator.

Endpoints:
  POST /api/pitch            — monolithic pitch (1 LLM call, all 9 sections at once)
                                Slow (~2 min on 32B) but returns full pitch in one response.
  POST /api/pitch/stream     — section-by-section SSE pitch
                                Streams 9 events as each section finishes.
                                ~5s to first section, ~35-50s total.
                                User watches progress instead of waiting blind.
  POST /api/pitch/download   — render the pitch JSON as a DOCX file

AI logic lives in:
  rag/engine.py → RAGEngine.generate_pitch()         (monolithic orchestration)
  rag/engine.py → RAGEngine.generate_pitch_section() (one section at a time)
  prompts/pitch.py → build_pitch_prompt()            (monolithic prompt)
  prompts/pitch.py → PITCH_SECTIONS + SECTION_BUILDERS (per-section prompts)

Models:
  Both monolithic and streaming use the same model as Chat Bot
  (CHAT_MODEL = qwen2.5:14b). See rag/engine.py.
"""

import asyncio
import io
import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from docx import Document

from fastapi import Depends

import core.deps as deps
from core.deps import iso_now, extract_metadata_from_scenario, generate_default_name
from core import activity
from core.rbac import tenant_scope
from core.schemas import PitchRequest, PitchDownloadRequest, ServiceCallPitchRequest

router = APIRouter()


@router.post("/api/pitch")
async def generate_pitch(request: PitchRequest, scope=Depends(tenant_scope)):
    """
    Generate a structured HDFC sales pitch for the given scenario.
    Creates a new conversation entry with the pitch as the first assistant message.
    """
    try:
        if not request.scenario.strip():
            raise HTTPException(status_code=400, detail="Scenario cannot be empty.")

        rag = deps.get_rag(scope.company_id)
        if not (rag.structured_data or rag.documents):
            raise HTTPException(
                status_code=400,
                detail="No documents loaded for your organization. Ask your admin to upload documents.",
            )

        result = rag.generate_pitch(
            scenario=request.scenario,
            language=request.language,
        )

        if result.get("success"):
            pitch = result["pitch"]
            ts = iso_now()
            metadata = extract_metadata_from_scenario(request.scenario)
            metadata["language"] = request.language

            entry = {
                "id": str(uuid.uuid4()),
                "name": generate_default_name(metadata, ts),
                "created_at": ts,
                "updated_at": ts,
                "messages": [
                    {"role": "user",      "content": request.scenario,           "type": "scenario", "timestamp": ts},
                    {"role": "assistant", "content": pitch.get("opening_hook", ""), "type": "pitch", "timestamp": ts},
                ],
                "pitch_data": pitch,
                "enrichment": pitch.get("_enrichment"),
                "metadata": metadata,
                "documents_used": result.get("documents_used", []),
            }

            activity.pitch_save(scope, entry)

            return JSONResponse(content={
                "success": True,
                "pitch": pitch,
                "documents_used": result.get("documents_used", []),
                "history_id": entry["id"],
                "conversation": entry,
            })

        return JSONResponse(content={
            "success": False,
            "error": result.get("error", "Unknown error"),
            "documents_used": result.get("documents_used", []),
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /api/pitch/stream — section-by-section SSE ────────────────────────

def _post_process_section(section_id: str, raw: str) -> dict:
    """Convert a section's raw text output into the structured form the
    frontend's `PitchOutput` already knows how to render.

    For text sections → just the cleaned string.
    For list sections → parsed array of items.
    For Q/A or objection pairs → array of dicts.
    """
    import re as _re
    raw = (raw or "").strip()
    if not raw:
        return {"raw": "", "structured": None}

    if section_id in (
        "opening_hook", "closing_strategy", "narrative_summary", "follow_up_email",
        # new 5 additions (text)
        "reengagement_script", "cross_sell",
        # service-call text sections
        "opening_script", "happy_customer_escalation", "transition_bridge",
        "loan_pitch_lines", "full_script", "follow_up_template",
    ):
        return {"raw": raw, "structured": raw}

    # red_flags → structured as list of flag blocks (split on 🚩 marker)
    if section_id == "red_flags":
        flags = []
        current = []
        for line in raw.splitlines():
            if line.strip().startswith("🚩") and current:
                flags.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        if current:
            flags.append("\n".join(current).strip())
        flags = [f for f in flags if f.strip()]
        return {"raw": raw, "structured": flags if flags else [raw]}

    # competitor_handling → objection pairs using "Competitor:" / "Objection:" / "Response:" keys
    if section_id == "competitor_handling":
        pairs, current_obj, current_comp = [], None, None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("competitor:"):
                current_comp = line[len("competitor:"):].strip()
            elif line.lower().startswith("objection:"):
                current_obj = line[len("objection:"):].strip()
            elif line.lower().startswith("response:") and current_obj is not None:
                pairs.append({
                    "objection": f"[{current_comp}] {current_obj}" if current_comp else current_obj,
                    "response": line[len("response:"):].strip(),
                })
                current_obj = None
        return {"raw": raw, "structured": pairs if pairs else []}

    if section_id in (
        "questions_to_ask", "key_product_facts", "what_not_to_say", "documents_checklist",
        # service-call list sections
        "service_check_questions",
    ):
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _re.sub(r"^\s*(?:\d+[.)]|[-*•·])\s*", "", line).strip()
            if line:
                items.append(line)
        return {"raw": raw, "structured": items}

    if section_id == "anticipated_customer_questions":
        pairs, current_q = [], None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("q:"):
                current_q = line[2:].strip()
            elif line.lower().startswith("a:") and current_q is not None:
                pairs.append({"question": current_q, "answer": line[2:].strip()})
                current_q = None
        return {"raw": raw, "structured": pairs}

    if section_id == "objection_handling":
        pairs, current_obj = [], None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("objection:"):
                current_obj = line[len("objection:"):].strip()
            elif line.lower().startswith("response:") and current_obj is not None:
                pairs.append({"objection": current_obj, "response": line[len("response:"):].strip()})
                current_obj = None
        return {"raw": raw, "structured": pairs}

    return {"raw": raw, "structured": raw}


@router.post("/api/pitch/stream")
async def generate_pitch_stream(request: PitchRequest, scope=Depends(tenant_scope)):
    """
    Stream the 9 pitch sections as Server-Sent Events.

    Event types (each emitted as `data: {json}\\n\\n`):
      session_start  → {scenario, language, sections: [PITCH_SECTIONS spec], total_sections}
      section_start  → {section_id, label, index, total}
      section_done   → {section_id, content, structured, word_count, ms, model, index, total}
      section_error  → {section_id, error, index, total}
      pitch_complete → {pitch: {full assembled pitch}, history_id, ms_total}
    """
    from prompts.pitch import PITCH_SECTIONS
    from rag.engine import RAGEngine  # noqa: F401
    # Replaced by native LLM generation in {language} — see language directive in prompts/pitch.py.
    # from rag.engine import contextual_translate, LANGUAGE_CODES
    # from deep_translator import GoogleTranslator

    scenario = (request.scenario or "").strip()
    if not scenario:
        raise HTTPException(status_code=400, detail="Scenario cannot be empty.")

    rag = deps.get_rag(scope.company_id)
    if not (rag.structured_data or rag.documents):
        raise HTTPException(
            status_code=400,
            detail="No documents loaded for your organization. Ask your admin to upload documents.",
        )

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def stream():
        loop = asyncio.get_event_loop()
        t_start = time.time()
        history_id = str(uuid.uuid4())  # pre-generate so history save can run after pitch_complete

        # ── Step 0: enrich + retrieve in parallel ─────────────────────────
        # Enrichment and retrieval are independent. Retrieval with the raw
        # scenario finds the same top-4 chunks as with the enriched narrative
        # for all but the most terse (1-2 word) briefs. Running them together
        # saves the full retrieval + distillation sequential wait (~5-8 s).
        yield _sse({"type": "enrichment_start"})

        # Emit session_start immediately so the frontend renders the pitch
        # progress tracker (all sections in "pending" state) within ~100 ms
        # of the click — without waiting for the enrichment LLM call (~5-10 s).
        # documents_used is [] here; the real list arrives in pitch_complete.
        yield _sse({
            "type": "session_start",
            "scenario": scenario,
            "raw_scenario": scenario,
            "language": request.language,
            "sections": [{"id": s["id"], "label": s["label"]} for s in PITCH_SECTIONS],
            "total_sections": len(PITCH_SECTIONS),
            "documents_used": [],
            "model": rag.PITCH_SECTION_MODEL,
        })

        enrich_future  = loop.run_in_executor(None, rag.enrich_scenario, scenario)
        retrieve_future = loop.run_in_executor(
            None, lambda: rag._retrieve_relevant_context(scenario, top_k=4)
        )

        # Retrieval is pure vector math (~100-500 ms) — await it first so
        # distillation can start while the heavier enrichment LLM is still running.
        retrieved_chunks, documents_used = await retrieve_future
        distil_future = loop.run_in_executor(None, lambda: rag.distil_chunks(retrieved_chunks))

        # Wait for enrichment (needed for fact_lock_block + enriched_narrative)
        enrichment = await enrich_future
        enriched_narrative = enrichment.get("scenario_narrative") or scenario

        import re as _re

        # ── Extract locked facts — raw scenario FIRST, enrichment as fallback ──
        # Raw scenario (Quick Form / Free Write) is authoritative for name + product.
        # Enrichment adds context (profession, income, urgency) but often mis-extracts
        # simple facts when the model drifts.

        def _from_enrichment(field):
            val = enrichment.get(field)
            if isinstance(val, dict):
                val = val.get("value")
            return str(val).strip() if val and str(val).lower() not in ("unknown", "") else None

        # Customer name — Quick Form always has "Customer: <name>" on line 1
        customer_name = None
        m = _re.search(r"(?:^|\n)Customer\s*[:/]\s*([^\n,]+)", scenario, _re.IGNORECASE)
        if m:
            customer_name = m.group(1).strip()
        if not customer_name:
            customer_name = _from_enrichment("customer_name")
        # Also try free-write patterns like "I am meeting Vedant today"
        if not customer_name:
            m = _re.search(r"(?:meeting|visiting|client is|customer is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", scenario)
            if m:
                customer_name = m.group(1).strip()
        is_name_provided = bool(customer_name)

        # Loan type — keyword scan of raw scenario is most reliable
        loan_type = None
        raw_lower = scenario.lower()
        for kw, label in [
            ("home loan", "Home Loan"),
            ("business loan", "Business Loan"),
            ("personal loan", "Personal Loan"),
            ("loan against property", "LAP"),
            (" lap ", "LAP"),
            ("working capital", "Business Loan"),
        ]:
            if kw in raw_lower:
                loan_type = label
                break
        if not loan_type:
            loan_type = _from_enrichment("loan_type")

        # Amount, profession, goal from enrichment (harder to scan reliably from raw text)
        loan_amount = _from_enrichment("loan_amount")
        if not loan_amount:
            m = _re.search(r"[₹Rs\.]+\s*(\d[\d,\.]*\s*(?:lakh|crore|L\b|Cr\b))", scenario, _re.IGNORECASE)
            if m:
                loan_amount = m.group(1).strip()
        profession  = _from_enrichment("profession")
        stated_goal = _from_enrichment("stated_goal")

        # ── Build SCENARIO FACT LOCK block ───────────────────────────────
        lock_lines = [
            "━━━ SCENARIO FACT LOCK ━━━",
            "Values locked from agent's brief. These CANNOT be changed by document context.",
            "Invalid: substituting Sharma/Gupta for customer name, or switching to a different product.",
            "",
        ]
        if is_name_provided and customer_name:
            lock_lines.append(f"Customer Name  : {customer_name}   ← use ONLY this name throughout every sentence.")
        else:
            lock_lines.append("Customer Name  : not provided — say 'the customer'. Do NOT invent any name.")
        lock_lines.append(f"Product        : {loan_type or 'not specified'}   ← ONLY this product. Never switch to Personal Loan, Business Loan, or any other product.")
        if loan_amount:
            lock_lines.append(f"Loan Amount    : {loan_amount}   ← use this exact figure.")
        if profession:
            lock_lines.append(f"Profession     : {profession}")
        if stated_goal:
            lock_lines.append(f"Goal           : {stated_goal}")
        lock_lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━━", ""]

        fact_lock_block = "\n".join(lock_lines)
        # fact_lock_block is passed separately to each section builder and injected
        # at the END of every prompt (just before the answer marker) so truncation
        # cannot remove it. Previously it was prepended to the scenario, which put
        # it at the start — exactly the part cut first when num_ctx was exceeded.

        # ── Common default names the model hallucinates ───────────────────
        # Used by post-processing after each section is generated.
        _DEFAULT_NAMES = [
            "Sharma", "Gupta", "Kumar", "Singh", "Patel", "Mehta",
            "Joshi", "Verma", "Rao", "Nair", "Iyer", "Reddy", "Shah",
            "Khanna", "Chopra", "Malhotra", "Kapoor", "Agarwal",
        ]

        def _fix_name(text: str) -> str:
            """Replace hallucinated names with the actual customer name.

            Two passes:
            1. Known defaults (_DEFAULT_NAMES): replace both bare occurrences and
               salutation patterns (Mr./Ms./Mrs. <default>).
            2. Catch-all: any remaining salutation pattern "Mr./Ms./Mrs. <Word>"
               where <Word> is NOT part of the customer's actual name gets replaced
               with the correct salutation + customer surname. This catches
               hallucinated first names and uncommon surnames not in _DEFAULT_NAMES.
               Scoped to salutation patterns only — avoids clobbering ordinary words
               or document-quoted names that happen to be capitalised.
            """
            if not text or not is_name_provided or not customer_name:
                return text
            name_parts = customer_name.strip().split()
            last_name  = name_parts[-1] if name_parts else customer_name
            first_name = name_parts[0]  if name_parts else customer_name
            name_parts_lower = {p.lower() for p in name_parts}

            # Pass 1 — known hallucinated defaults
            for dn in _DEFAULT_NAMES:
                if dn.lower() in name_parts_lower:
                    continue  # this IS the customer's name — don't replace
                text = _re.sub(
                    rf'\b(Mr\.|Ms\.|Mrs\.)\s+{dn}\b',
                    rf'\1 {last_name}',
                    text, flags=_re.IGNORECASE,
                )
                text = _re.sub(
                    rf'(?<![A-Za-z]){dn}(?![A-Za-z])',
                    first_name,
                    text,
                )

            # Pass 2 — catch-all: salutation + any capitalised word that isn't the customer's name
            def _fix_salutation(m: _re.Match) -> str:
                sal = m.group(1)
                found = m.group(2)
                if found.lower() in name_parts_lower:
                    return m.group(0)  # already correct
                return f"{sal} {last_name}"

            text = _re.sub(
                r'\b(Mr\.|Ms\.|Mrs\.)\s+([A-Z][a-z]+)\b',
                _fix_salutation,
                text,
            )
            return text
        yield _sse({
            "type": "scenario_enriched",
            "enrichment": enrichment,
            "enriched_narrative": enriched_narrative,
            "raw_scenario": scenario,
        })

        # ── Layer 1: await distillation (already running since retrieval finished) ──
        # distil_future was launched immediately after retrieve_future completed,
        # so by the time enrichment finishes it is often already done.
        try:
            document_facts = await distil_future
        except Exception as _de:
            deps.log.warning("[PITCH DISTIL] distillation failed (%s) — using raw chunks", _de)
            document_facts = retrieved_chunks

        language = request.language or "English"

        # ── Layer 2: build shared prefix (byte-identical for all 10 section calls) ─
        # Contains: persona + DOCUMENT_FACTS + grounding rule + SCENARIO FACT LOCK
        # + language override for non-English pitches.
        # Sent as the OpenAI system message → prefix caching kicks in after section 1.
        from prompts.pitch import build_shared_prefix
        shared_prefix = build_shared_prefix(document_facts, fact_lock_block, language=language)

        # Replaced by native LLM generation in {language} — see language directive in prompts/pitch.py.
        # translator = None
        # if language != "English" and language in LANGUAGE_CODES:
        #     translator = GoogleTranslator(source="auto", target=LANGUAGE_CODES[language])
        #     deps.log.info(f"[PITCH TRANSLATE] translator created for {language} → {LANGUAGE_CODES[language]}")
        # else:
        #     deps.log.info(f"[PITCH TRANSLATE] no translation — language={language}")
        #
        # def _translate_structured(value):
        #     """Recursively translate strings inside structured section output."""
        #     if isinstance(value, str) and value:
        #         return contextual_translate(value, translator)
        #     if isinstance(value, list):
        #         return [_translate_structured(x) for x in value]
        #     if isinstance(value, dict):
        #         return {k: _translate_structured(v) for k, v in value.items()}
        #     return value

        prior_sections: dict = {}   # rendered text per section_id (for context to later sections)
        pitch_data: dict = {}       # structured form per section_id (final pitch JSON)
        total = len(PITCH_SECTIONS)

        # ── Build a lookup: section_id → 1-based list index ──────────────
        _section_index = {s["id"]: i + 1 for i, s in enumerate(PITCH_SECTIONS)}

        # ── Compute dependency wave for each section ─────────────────────
        # Wave 0: no deps (opening_hook, key_product_facts, documents_checklist, what_not_to_say)
        # Wave 1: deps in wave 0 (questions_to_ask, anticipated_customer_questions, closing_strategy)
        # Wave 2: deps in wave 1 (objection_handling, follow_up_email)
        # Wave 3: deps in wave 2 (narrative_summary)
        _wave: dict = {}
        for spec in PITCH_SECTIONS:
            _wave[spec["id"]] = (
                0 if not spec["depends_on"]
                else max(_wave.get(d, 0) for d in spec["depends_on"]) + 1
            )

        from collections import defaultdict
        _wave_groups: dict = defaultdict(list)
        for spec in PITCH_SECTIONS:
            _wave_groups[_wave[spec["id"]]].append(spec)

        # ── Emit section_start for ALL sections up front ─────────────────
        for idx, spec in enumerate(PITCH_SECTIONS, start=1):
            yield _sse({
                "type": "section_start",
                "section_id": spec["id"],
                "label": spec["label"],
                "index": idx,
                "total": total,
            })

        # ── Wave executor — runs one wave at a time, sections within a
        #    wave in parallel; emits section_done out-of-order as they land ─
        async def _run_section(spec, prior_snap: dict):
            """Run one section in the thread-pool with backoff on rate-limit errors."""
            backoff = 2.0
            for attempt in range(3):
                try:
                    result = await loop.run_in_executor(
                        None,
                        lambda s=spec, p=prior_snap: rag.generate_pitch_section(
                            section_id=s["id"],
                            scenario=enriched_narrative,
                            prior_sections=p,
                            shared_prefix=shared_prefix,
                            max_tokens=s["max_tokens"],
                            min_words=s["min_words"],
                            language=language,
                        ),
                    )
                    return spec, result, None
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    return spec, None, str(e)
            return spec, None, "max retries exceeded"

        for wave_num in sorted(_wave_groups.keys()):
            wave_specs = _wave_groups[wave_num]
            # Snapshot prior_sections once for the whole wave (all sections
            # in the same wave share the same view — no intra-wave deps)
            prior_snap = dict(prior_sections)

            tasks = [
                asyncio.ensure_future(_run_section(spec, prior_snap))
                for spec in wave_specs
            ]

            for coro in asyncio.as_completed(tasks):
                spec, result, error = await coro
                sid = spec["id"]
                idx = _section_index[sid]

                if error or result is None:
                    deps.log.error("[PITCH STREAM] %s failed: %s", sid, error)
                    yield _sse({
                        "type": "section_error",
                        "section_id": sid,
                        "label": spec["label"],
                        "error": error or "unknown error",
                        "index": idx,
                        "total": total,
                    })
                    continue

                content = result["content"]
                content = _fix_name(content)
                prior_sections[sid] = content
                processed = _post_process_section(sid, content)
                pitch_data[sid] = processed["structured"]

                yield _sse({
                    "type": "section_done",
                    "section_id": sid,
                    "label": spec["label"],
                    "content": content,
                    "structured": processed["structured"],
                    "word_count": result["word_count"],
                    "ms": result["ms"],
                    "model": result["model"],
                    "retried": result["retried"],
                    "index": idx,
                    "total": total,
                })

        # ── Assemble final pitch + persist as conversation ────────────────
        ts = iso_now()
        metadata = extract_metadata_from_scenario(scenario)
        metadata["language"] = request.language

        # Attach enrichment to pitch_data so it survives in history and the
        # DOCX export / continue-as-chat flow can reference it later.
        pitch_data["_enrichment"] = enrichment
        pitch_data["_raw_scenario"] = scenario

        entry = {
            "id": history_id,  # pre-generated at stream() start
            "name": generate_default_name(metadata, ts),
            "created_at": ts,
            "updated_at": ts,
            "messages": [
                {"role": "user",      "content": scenario,                          "type": "scenario", "timestamp": ts},
                {"role": "assistant", "content": pitch_data.get("opening_hook", ""), "type": "pitch",    "timestamp": ts},
            ],
            "pitch_data": pitch_data,
            "enrichment": enrichment,
            "metadata": metadata,
            "documents_used": documents_used,
        }
        ms_total = int((time.time() - t_start) * 1000)
        yield _sse({
            "type": "pitch_complete",
            "pitch": pitch_data,
            "documents_used": documents_used,
            "history_id": history_id,
            "conversation": entry,
            "ms_total": ms_total,
            "model": rag.PITCH_SECTION_MODEL,
        })
        # Persist after yielding pitch_complete — client already has all data.
        # DB write no longer blocks the user-perceived end of the stream.
        activity.pitch_save(scope, entry)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/pitch/service-call/stream")
async def generate_service_call_pitch_stream(request: ServiceCallPitchRequest, scope=Depends(tenant_scope)):
    """
    Stream the 11 service-call pitch sections as Server-Sent Events.
    Same SSE event types as /api/pitch/stream but uses SERVICE_CALL_SECTIONS
    and SERVICE_CALL_SECTION_BUILDERS for ETB service-cum-sales call coaching.
    """
    from prompts.pitch import SERVICE_CALL_SECTIONS, SERVICE_CALL_SECTION_BUILDERS
    from prompts.pitch import build_shared_prefix, build_distillation_prompt

    scenario = (request.scenario or "").strip()
    if not scenario:
        raise HTTPException(status_code=400, detail="Scenario cannot be empty.")

    rag = deps.get_rag(scope.company_id)
    if not (rag.structured_data or rag.documents):
        raise HTTPException(
            status_code=400,
            detail="No documents loaded for your organization. Ask your admin to upload documents.",
        )

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def stream():
        import re as _re
        loop = asyncio.get_event_loop()
        t_start = time.time()
        history_id = str(uuid.uuid4())

        yield _sse({"type": "enrichment_start"})
        yield _sse({
            "type": "session_start",
            "scenario": scenario,
            "raw_scenario": scenario,
            "language": request.language,
            "sections": [{"id": s["id"], "label": s["label"]} for s in SERVICE_CALL_SECTIONS],
            "total_sections": len(SERVICE_CALL_SECTIONS),
            "documents_used": [],
            "model": rag.PITCH_SECTION_MODEL,
            "pitch_type": "service_call",
        })

        enrich_future = loop.run_in_executor(None, rag.enrich_scenario, scenario)
        retrieve_future = loop.run_in_executor(
            None, lambda: rag._retrieve_relevant_context(scenario, top_k=4)
        )

        retrieved_chunks, documents_used = await retrieve_future
        distil_future = loop.run_in_executor(None, lambda: rag.distil_chunks(retrieved_chunks))

        enrichment = await enrich_future
        enriched_narrative = enrichment.get("scenario_narrative") or scenario

        # Extract customer name for name-fixing
        customer_name = None
        m = _re.search(r"(?:^|\n)Customer\s*[:/]\s*([^\n,]+)", scenario, _re.IGNORECASE)
        if m:
            customer_name = m.group(1).strip()
        if not customer_name:
            val = enrichment.get("customer_name")
            if val and str(val).lower() not in ("unknown", ""):
                customer_name = str(val).strip()
        is_name_provided = bool(customer_name)

        _DEFAULT_NAMES = [
            "Sharma", "Gupta", "Kumar", "Singh", "Patel", "Mehta",
            "Joshi", "Verma", "Rao", "Nair", "Iyer", "Reddy", "Shah",
        ]

        def _fix_name(text: str) -> str:
            if not text or not is_name_provided or not customer_name:
                return text
            name_parts = customer_name.strip().split()
            last_name = name_parts[-1] if name_parts else customer_name
            first_name = name_parts[0] if name_parts else customer_name
            name_parts_lower = {p.lower() for p in name_parts}
            for dn in _DEFAULT_NAMES:
                if dn.lower() in name_parts_lower:
                    continue
                text = _re.sub(rf'\b(Mr\.|Ms\.|Mrs\.)\s+{dn}\b', rf'\1 {last_name}', text, flags=_re.IGNORECASE)
                text = _re.sub(rf'(?<![A-Za-z]){dn}(?![A-Za-z])', first_name, text)
            def _fix_sal(mo):
                sal, found = mo.group(1), mo.group(2)
                return mo.group(0) if found.lower() in name_parts_lower else f"{sal} {last_name}"
            text = _re.sub(r'\b(Mr\.|Ms\.|Mrs\.)\s+([A-Z][a-z]+)\b', _fix_sal, text)
            return text

        yield _sse({"type": "scenario_enriched", "enrichment": enrichment})

        try:
            document_facts = await distil_future
        except Exception as _de:
            deps.log.warning("[SC PITCH DISTIL] failed (%s) — using raw chunks", _de)
            document_facts = retrieved_chunks

        language = request.language or "English"

        # Build fact lock block for service call
        lock_lines = [
            "━━━ SERVICE CALL FACT LOCK ━━━",
            "ETB (Existing To Bank) customer. This is a service-cum-sales call.",
            "",
        ]
        if is_name_provided and customer_name:
            lock_lines.append(f"Customer Name  : {customer_name}   ← use ONLY this name. Never invent another name.")
        else:
            lock_lines.append("Customer Name  : not provided — say 'the customer'. Do NOT invent any name.")

        # Extract offer details from scenario
        offer_product = None
        for kw, label in [("personal loan", "Personal Loan"), ("business loan", "Business Loan")]:
            if kw in scenario.lower():
                offer_product = label
                break
        offer_amount = None
        am = _re.search(r"[₹Rs\.]*\s*(\d[\d,.]*\s*(?:lakh|crore|L\b|Cr\b|,\d{2,3}))", scenario, _re.IGNORECASE)
        if am:
            offer_amount = am.group(1).strip()

        if offer_product:
            lock_lines.append(f"Offer Product  : {offer_product}   ← reference ONLY this product.")
        if offer_amount:
            lock_lines.append(f"Offer Amount   : {offer_amount}   ← use this exact figure.")
        lock_lines += ["", "CALL TYPE: Service-cum-Sales (Phase 1 = service, Phase 2 = offer)", "━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
        fact_lock_block = "\n".join(lock_lines)

        shared_prefix = build_shared_prefix(document_facts, fact_lock_block, language=language)

        prior_sections: dict = {}
        pitch_data: dict = {}
        total = len(SERVICE_CALL_SECTIONS)

        _section_index = {s["id"]: i + 1 for i, s in enumerate(SERVICE_CALL_SECTIONS)}

        from collections import defaultdict
        _wave: dict = {}
        for spec in SERVICE_CALL_SECTIONS:
            _wave[spec["id"]] = (
                0 if not spec["depends_on"]
                else max(_wave.get(d, 0) for d in spec["depends_on"]) + 1
            )
        _wave_groups: dict = defaultdict(list)
        for spec in SERVICE_CALL_SECTIONS:
            _wave_groups[_wave[spec["id"]]].append(spec)

        for idx, spec in enumerate(SERVICE_CALL_SECTIONS, start=1):
            yield _sse({
                "type": "section_start",
                "section_id": spec["id"],
                "label": spec["label"],
                "index": idx,
                "total": total,
            })

        async def _run_sc_section(spec, prior_snap: dict):
            if spec["id"] not in SERVICE_CALL_SECTION_BUILDERS:
                return spec, None, f"no builder for {spec['id']}"
            backoff = 2.0
            for attempt in range(3):
                try:
                    result = await loop.run_in_executor(
                        None,
                        lambda s=spec, p=prior_snap: rag.generate_pitch_section(
                            section_id=s["id"],
                            scenario=enriched_narrative,
                            prior_sections=p,
                            shared_prefix=shared_prefix,
                            max_tokens=s["max_tokens"],
                            min_words=s["min_words"],
                            language=language,
                            builders=SERVICE_CALL_SECTION_BUILDERS,
                        ),
                    )
                    return spec, result, None
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    return spec, None, str(e)
            return spec, None, "max retries exceeded"

        for wave_num in sorted(_wave_groups.keys()):
            wave_specs = _wave_groups[wave_num]
            prior_snap = dict(prior_sections)
            tasks = [asyncio.ensure_future(_run_sc_section(spec, prior_snap)) for spec in wave_specs]
            for coro in asyncio.as_completed(tasks):
                spec, result, error = await coro
                sid = spec["id"]
                idx = _section_index[sid]
                if error or result is None:
                    deps.log.error("[SC PITCH STREAM] %s failed: %s", sid, error)
                    yield _sse({"type": "section_error", "section_id": sid, "label": spec["label"], "error": error or "unknown", "index": idx, "total": total})
                    continue
                content = result["content"]
                content = _fix_name(content)
                prior_sections[sid] = content
                processed = _post_process_section(sid, content)
                pitch_data[sid] = processed["structured"]
                yield _sse({
                    "type": "section_done",
                    "section_id": sid,
                    "label": spec["label"],
                    "content": content,
                    "structured": processed["structured"],
                    "word_count": result["word_count"],
                    "ms": result["ms"],
                    "model": result["model"],
                    "retried": result["retried"],
                    "index": idx,
                    "total": total,
                })

        ts = iso_now()
        metadata = extract_metadata_from_scenario(scenario)
        metadata["language"] = request.language
        metadata["pitch_type"] = "service_call"

        pitch_data["_enrichment"] = enrichment
        pitch_data["_raw_scenario"] = scenario

        entry = {
            "id": history_id,
            "name": f"[Service Call] {generate_default_name(metadata, ts)}",
            "created_at": ts,
            "updated_at": ts,
            "messages": [
                {"role": "user", "content": scenario, "type": "scenario", "timestamp": ts},
                {"role": "assistant", "content": pitch_data.get("opening_script", ""), "type": "pitch", "timestamp": ts},
            ],
            "pitch_data": pitch_data,
            "enrichment": enrichment,
            "metadata": metadata,
            "documents_used": documents_used,
        }
        ms_total = int((time.time() - t_start) * 1000)
        yield _sse({
            "type": "pitch_complete",
            "pitch": pitch_data,
            "documents_used": documents_used,
            "history_id": history_id,
            "conversation": entry,
            "ms_total": ms_total,
            "model": rag.PITCH_SECTION_MODEL,
            "pitch_type": "service_call",
        })
        activity.pitch_save(scope, entry)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/pitch/download")
async def download_pitch_docx(request: PitchDownloadRequest):
    """Render the pitch JSON as a DOCX file and stream it back."""
    try:
        pitch = request.pitch
        doc = Document()

        title = doc.add_heading('HDFC Sales Pitch Kit', 0)
        title.alignment = 1  # Center
        doc.add_paragraph('Generated by HDFC Sales Pitch Assistant', style='Subtitle')
        doc.add_page_break()

        def add_section(heading: str, text_or_list, is_qa=False):
            if not text_or_list:
                return
            doc.add_heading(heading, level=1)
            if isinstance(text_or_list, str):
                doc.add_paragraph(text_or_list)
            elif isinstance(text_or_list, list):
                for item in text_or_list:
                    if is_qa and isinstance(item, dict):
                        q = item.get("question") or item.get("objection")
                        a = item.get("answer") or item.get("response")
                        if q and a:
                            p = doc.add_paragraph(style='List Bullet')
                            p.add_run(f"Q/Objection: {q}\n").bold = True
                            p.add_run(f"A/Response: {a}")
                    elif isinstance(item, str):
                        doc.add_paragraph(item, style='List Bullet')

        add_section('Opening Hook', pitch.get('opening_hook', ''))
        add_section('Questions to Ask', pitch.get('questions_to_ask', []))
        add_section('What NOT to Say', pitch.get('what_not_to_say', []))
        add_section('Anticipated Customer Questions', pitch.get('anticipated_customer_questions', []), is_qa=True)
        add_section('Objection Handling', pitch.get('objection_handling', []), is_qa=True)
        add_section('Key Product Facts', pitch.get('key_product_facts', []))
        add_section('Documents Checklist', pitch.get('documents_checklist', []))
        add_section('Closing Strategy', pitch.get('closing_strategy', ''))

        doc.add_page_break()
        doc.add_heading('Full Pitch Script', level=1)
        doc.add_paragraph(pitch.get('narrative_summary', ''))

        if pitch.get('follow_up_email'):
            doc.add_page_break()
            doc.add_heading('Follow-Up Email Template', level=1)
            doc.add_paragraph(pitch.get('follow_up_email'))

        byte_io = io.BytesIO()
        doc.save(byte_io)
        byte_io.seek(0)

        return StreamingResponse(
            byte_io,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="HDFC_Sales_Pitch.docx"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
