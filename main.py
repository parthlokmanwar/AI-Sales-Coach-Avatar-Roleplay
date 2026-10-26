"""
HDFC Sales Pitch Assistant — FastAPI bootstrap.

This file is intentionally short. All routes live in features/*.py,
all shared state lives in core/deps.py, all prompts live in prompts/<feature>/.

To work on ONE feature in isolation, open the matching pair:
  Pitch Generator  → features/pitch.py    +  prompts/pitch/
  Ask Me chat      → features/chat.py     +  prompts/chat/
  Live Voice       → features/voice.py    +  prompts/voice/
  Pitch History    → features/history.py  +  prompts/chat/ (continuation builder)
  RolePlay Sim     → features/roleplay.py +  prompts/roleplay/  +  core/tavus_client.py
  Files / health   → features/files.py    (no prompts)

The RAG engine itself stays in app.py (EnhancedRAGv4 class) — its methods
delegate to the prompts/ builders, so iterating on prompt quality means
editing ONLY prompts/<feature>/.
"""

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse

# Configure logging BEFORE importing core.deps (which uses logging.getLogger).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hdfc")

# Load .env so backend secrets (Twilio, OpenRouter, …) become env vars.
# Tiny built-in parser — no external dependency (avoids python-dotenv install
# issues across interpreters).
#
# Precedence: .env is the source of truth for this app's config and WINS over an
# inherited env var. This is deliberate — under `uvicorn --reload` the reloader
# parent process captures the launching shell's env once, and every reloaded
# child inherits it; with the old "shell wins" rule a stale `$env:CHAT_MODEL`
# (e.g. a deprecated `:free` model) silently shadowed .env and pinned every
# OpenRouter call to a 404. A *blank* value in .env never clobbers an existing
# env var, so optional shell overrides of unset keys still work.
def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not key:
                continue
            if not val and key in os.environ:
                continue  # blank .env value never wipes an inherited env var
            prev = os.environ.get(key)
            if prev is not None and prev != val:
                log.info(f"[ENV] .env overrides inherited {key}")
            os.environ[key] = val
    except Exception as exc:  # never let .env loading crash startup
        log.warning(f"[ENV] could not load .env: {exc}")


_load_env_file()

# Import after logging is configured so the logger is named.
from features.files import router as files_router, auto_load_files_from_folder  # noqa: E402
from features.pitch import router as pitch_router  # noqa: E402
from features.chat import router as chat_router  # noqa: E402
from features.history import router as history_router  # noqa: E402
from features.voice import router as voice_router  # noqa: E402
from features.rag import router as rag_router  # noqa: E402
from features.call_assist import router as call_assist_router  # noqa: E402
from features.live_call_ws import router as live_call_ws_router  # noqa: E402
from features.call_assistant_v2 import router as call_assistant_v2_router  # noqa: E402
from features.roleplay import router as roleplay_router  # noqa: E402
from features.tts import router as tts_router  # noqa: E402
from features.auth import router as auth_router  # noqa: E402
from features.auth import _extract_token  # noqa: E402
from features.platform import router as platform_router  # noqa: E402
from features.admin import router as admin_router  # noqa: E402
from features.scheduling import admin_router as sched_admin_router  # noqa: E402
from features.scheduling import agent_router as sched_agent_router  # noqa: E402
from features.documents import router as documents_router  # noqa: E402
from features.me import router as me_router  # noqa: E402
from core import auth as auth_backend  # noqa: E402
from core.management import ManagementError  # noqa: E402


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="HDFC Sales Pitch Assistant API",
    description="Generate AI-powered HDFC sales pitches from your documents",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth gate ────────────────────────────────────────────────────────────────
# Every /api/* route is locked behind a valid session — the only exception is
# the login endpoint itself. Public, non-/api routes (health check, "/", Swagger
# docs, and any future static SPA assets) stay open so the sign-in page can load.
#
# The token is read from `Authorization: Bearer <token>` (sent by the SPA) or
# the HttpOnly `pv_auth_token` cookie (for /docs + direct browser access).
# NOTE: WebSocket upgrades use the "websocket" ASGI scope and are NOT handled by
# this HTTP middleware; the live-call WS endpoints are reached only after an
# authenticated HTTP session has already been established in the UI.

_AUTH_PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/roleplay/health",   # readiness probe — no session, leaks nothing (see QUICKSTART/RUN_DEMO)
}

# Path PREFIXES that are public — checked with startswith() below.
# avatar-thumb is a static video proxy for custom Tavus replicas; the browser's
# <video src> tag cannot add auth headers, and the content (avatar preview clips)
# is not sensitive — it's the same video Tavus shows publicly.
_AUTH_PUBLIC_PREFIXES = (
    "/api/roleplay/avatar-thumb/",
    # Per-turn role-enforcing LLM proxy: hit by TAVUS'S servers (no user
    # session possible). The endpoint verifies its own HMAC bearer token
    # (features/roleplay.py::_proxy_token) — an unauthenticated caller gets 401
    # from the route itself, so nothing is exposed by making the prefix public.
    "/api/roleplay/llm/",
    # Telephony provider callbacks (Twilio) are hit by Twilio's servers with NO
    # user session, so they cannot pass the bearer/cookie gate. The status webhook
    # checks the Twilio signature when TWILIO_VALIDATE_SIGNATURE=true, and the TwiML
    # URL carries an unguessable call_id. (call-assistant-v2 routes are not yet in
    # this codebase — preserved from local working-tree changes.)
    "/api/call-assistant-v2/twiml/",
    "/api/call-assistant-v2/status",
)


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path
        if (
            request.method == "OPTIONS"           # CORS preflight
            or not path.startswith("/api/")       # health, root, docs, static SPA
            or path in _AUTH_PUBLIC_PATHS         # exact public paths (login etc.)
            or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES)
        ):
            return await call_next(request)

        user = auth_backend.get_session_user(_extract_token(request))
        if not user:
            return StarletteJSONResponse(
                status_code=401,
                content={"success": False, "detail": "Authentication required."},
            )
        request.state.user = user
        return await call_next(request)


app.add_middleware(AuthGateMiddleware)


# ── Management error → JSON ───────────────────────────────────────────────────
# Service-layer ManagementError (validation / not-found / conflict) becomes a
# clean JSON response instead of a 500, so the platform/admin routers stay thin.
@app.exception_handler(ManagementError)
async def _management_error_handler(request: StarletteRequest, exc: ManagementError):
    return StarletteJSONResponse(
        status_code=exc.status_code,
        content={"success": False, "detail": exc.detail},
    )


# ── Request logging middleware ───────────────────────────────────────────────

class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        start = time.time()
        log.info(f"-> {request.method} {request.url.path}")
        try:
            response = await call_next(request)
            ms = int((time.time() - start) * 1000)
            level = logging.WARNING if response.status_code >= 400 else logging.INFO
            log.log(level, f"<- {response.status_code} {request.url.path}  ({ms}ms)")
            return response
        except Exception as exc:
            ms = int((time.time() - start) * 1000)
            log.error(f"x EXCEPTION {request.url.path}  ({ms}ms)  {exc}")
            raise


app.add_middleware(RequestLogMiddleware)


# ── Mount per-feature routers ────────────────────────────────────────────────

app.include_router(auth_router,     tags=["auth"])
app.include_router(platform_router, tags=["platform"])
app.include_router(admin_router,    tags=["admin"])
app.include_router(sched_admin_router, tags=["scheduling-admin"])
app.include_router(sched_agent_router, tags=["scheduling"])
app.include_router(documents_router, tags=["documents"])
app.include_router(me_router,        tags=["me"])
app.include_router(files_router,   tags=["files"])
app.include_router(pitch_router,   tags=["pitch"])
app.include_router(chat_router,    tags=["chat"])
app.include_router(history_router, tags=["history"])
app.include_router(voice_router,       tags=["voice"])
app.include_router(rag_router,         tags=["rag"])
app.include_router(call_assist_router,    tags=["call-assist"])
app.include_router(live_call_ws_router,  tags=["live-ws"])
app.include_router(call_assistant_v2_router, tags=["call-assist-v2"])
app.include_router(roleplay_router, prefix="/api/roleplay", tags=["roleplay"])
app.include_router(tts_router, tags=["tts"])


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    log.info("[START] HDFC Sales Pitch Assistant API starting...")

    # Provision the auth DB + seed the predefined users before serving traffic.
    try:
        auth_backend.init_and_seed()
    except Exception as e:
        log.error(f"[AUTH] Could not initialize auth DB: {e}")

    # One-time migration of legacy global pitch/chat history into the tenant DB,
    # and of the global uploads/ corpus into the demo company's document folder.
    try:
        from core.activity import migrate_json_history, migrate_company_uploads
        migrate_json_history()
        migrate_company_uploads()
    except Exception as e:
        log.warning(f"[MIGRATE] migration skipped: {e}")

    auto_load_files_from_folder()

    # Load avatar prompt template for RolePlay Simulation feature
    from prompts.roleplay import load_prompt
    try:
        load_prompt()
        log.info("[START] Avatar prompt template loaded (RolePlay ready).")
    except Exception as e:
        log.warning(f"[START] Could not load avatar prompt template: {e}")

    # Start the RolePlay inactivity reaper — ends Tavus avatar sessions whose
    # user went silent or whose browser died, so no AI session keeps billing
    # with nobody on the call (ROLEPLAY_IDLE_TIMEOUT_S, default 40s).
    try:
        from features.roleplay import start_session_reaper
        start_session_reaper()
    except Exception as e:
        log.warning(f"[START] RolePlay session reaper not started: {e}")

    # Pre-load the live Whisper model so the first real call isn't penalised
    # by the 2-4s cold-load. Runs in a thread executor so startup stays async.
    async def warmup_whisper():
        import asyncio as _asyncio
        import time as _time
        loop = _asyncio.get_event_loop()
        def _load():
            try:
                from core.whisper_stt import get_live_engine
                t0 = _time.time()
                eng = get_live_engine()
                _ = eng.model   # triggers model load
                ms = int((_time.time() - t0) * 1000)
                log.info(f"[STT] loaded faster-whisper/{eng._model_size} on device in {ms}ms")
            except Exception as e:
                log.warning(f"[STT] warmup failed: {e}")
        await loop.run_in_executor(None, _load)

    asyncio.create_task(warmup_whisper())

    # Pre-warm the adaptive speaker-labeling MFCC backend. The very first
    # librosa.feature.mfcc/delta call triggers numba JIT compilation (~10-13s),
    # which otherwise lands on the FIRST utterance of a live call and makes the
    # first AI suggestion feel like it takes ~10s. Priming it here (one dummy
    # embed during boot) keeps every real live turn fast (~40ms label).
    async def warmup_speaker_embed():
        import asyncio as _asyncio
        import time as _time
        loop = _asyncio.get_event_loop()
        def _warm():
            try:
                import numpy as _np
                from core.adaptive_speaker import get_engine as _get_adaptive
                eng = _get_adaptive()
                t0 = _time.time()
                # 1.5s 220Hz tone — passes silence-trim and exercises the full
                # MFCC + delta + delta-delta path so numba compiles all of it.
                dummy = (0.3 * _np.sin(
                    2 * _np.pi * 220 * _np.arange(int(1.5 * 16000)) / 16000
                )).astype(_np.float32)
                eng._backend.embed(dummy, sr=16000)
                ms = int((_time.time() - t0) * 1000)
                log.info(f"[ADAPTIVE] warmup embed done in {ms}ms (numba JIT primed)")
            except Exception as e:
                log.warning(f"[ADAPTIVE] warmup failed: {e}")
        await loop.run_in_executor(None, _warm)

    asyncio.create_task(warmup_speaker_embed())

    # Pre-warm the cross-encoder reranker used in RAG retrieval. It lazy-loads
    # on the first query (~7s to construct the CrossEncoder), which otherwise
    # lands on the first live suggestion. Priming it here keeps first-query
    # retrieval fast (~0.5s).
    async def warmup_reranker():
        import asyncio as _asyncio
        import time as _time
        loop = _asyncio.get_event_loop()
        def _warm():
            try:
                from rag.reranker import get_reranker
                t0 = _time.time()
                rr = get_reranker()
                if rr:
                    rr.score([("warm up query", "warm up candidate passage")])
                    log.info(f"[RAG] reranker warmup done in {int((_time.time()-t0)*1000)}ms")
            except Exception as e:
                log.warning(f"[RAG] reranker warmup failed: {e}")
        await loop.run_in_executor(None, _warm)

    asyncio.create_task(warmup_reranker())

    # Background: ensure the local Ollama fast-path model is available.
    async def pull_ollama():
        try:
            log.info("[INFO] Checking Ollama fallback model (llama3.2:3b)...")
            check = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10, shell=False,
            )
            if "llama3.2" in check.stdout:
                log.info("[OK] Ollama fallback model (llama3.2:3b) already installed.")
                return
            proc = await asyncio.create_subprocess_exec(
                "ollama", "pull", "llama3.2:3b",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                log.info("[OK] Ollama fallback model ready.")
            else:
                log.warning("[WARN] Ollama pull failed or not installed.")
        except Exception as e:
            log.warning(f"[WARN] Failed to auto-pull Ollama: {e}")

    asyncio.create_task(pull_ollama())


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 8003))
    log.info(f"[START] Docs:   http://localhost:{port}/docs")
    log.info(f"[START] Health: http://localhost:{port}/health")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
