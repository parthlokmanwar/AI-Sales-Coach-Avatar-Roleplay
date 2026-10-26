# Call Assistant V2 — Complete Technical Documentation & Handoff Guide

> **Feature:** Live Twilio phone-call sales copilot for HDFC loan agents
> **Status:** Working end-to-end (verified 13 Jun 2026)
> **Audience:** Any developer maintaining, modifying, or deploying this feature

---

## 1. Feature Overview

### What it does
Call Assistant V2 places a **real phone call** between a sales agent and a customer, listens to the **customer's voice in real time**, transcribes it locally, grounds an answer in the loaded HDFC documents (RAG), and streams a **"Say This Now"** suggestion to the agent's browser — all while the call is live.

**It is a copilot, NOT a voice bot.** The AI never speaks on the call. The agent reads the suggestion on screen and speaks it themselves.

### Business purpose
- Junior agents get senior-RM-quality answers (rates, eligibility, documents, objection handling) in ~2 seconds, mid-call.
- Every figure comes from the loaded HDFC policy documents — no invented rates.
- Customer intent + recommended next action are surfaced live, helping agents steer toward closing.

### End-to-end workflow (what the user experiences)
1. Agent opens the **Call Assistant** tab in the web UI, enters the customer's number, picks a language, presses **Start Call**.
2. The **agent's phone rings first** (Twilio dials the agent).
3. Agent answers → Twilio automatically dials the **customer** and bridges the two.
4. As the customer speaks, their words appear in the **Live Transcript** panel within ~1–2 s.
5. The **Say This Now** panel fills with a spoken line the agent can read out, plus **Customer Insight** (intent) and **Suggested Next Action**.
6. Agent presses **End Call** (or either party hangs up). The call appears in **Recent Calls** history.

---

## 2. Architecture & Processing Flow

### System flow diagram

```
┌──────────────┐  POST /api/call-assistant-v2/calls   ┌──────────────────┐
│ Agent browser │ ───────────────────────────────────► │ FastAPI backend  │
│  (React UI)   │ ◄─────── WS /ws/call-assistant-v2/   │  (port 8003)     │
└──────┬───────┘            agent/{call_id}            └────┬─────────────┘
       │ UI served via Vite (5173)                          │ calls.create()
       │ through Cloudflare tunnel (https)                  ▼
       │                                            ┌──────────────┐
       │                                            │  Twilio API  │
       │                                            └──────┬───────┘
       │                              1. dials AGENT phone │
       │                              2. agent answers     ▼
       │                              3. Twilio fetches TwiML from
       │                                 PUBLIC_BASE_URL/api/call-assistant-v2/twiml/{id}
       │                              4. TwiML says: <Start><Stream> + <Dial>customer
       │                                                   │
       │                                                   ▼
       │                      ┌────────────────────────────────────────┐
       │                      │ Twilio media fork (WS, μ-law 8kHz)     │
       │                      │ WS /ws/call-assistant-v2/twilio/{id}   │
       │                      │ track=both_tracks:                     │
       │                      │   inbound  = AGENT voice               │
       │                      │   outbound = CUSTOMER voice            │
       │                      └───────────────┬────────────────────────┘
       │                                      ▼
       │                      ┌────────────────────────────────────────┐
       │                      │ UtteranceBuffer (VAD per track)        │
       │                      │ RMS-threshold speech detection,        │
       │                      │ emits WAV on trailing silence          │
       │                      └───────────────┬────────────────────────┘
       │                                      ▼
       │                      ┌────────────────────────────────────────┐
       │                      │ faster-whisper STT (LOCAL, GPU)        │
       │                      │ large-v3-turbo, int8, CUDA             │
       │                      │ telephone=True (no Devanagari prompt)  │
       │                      └───────────────┬────────────────────────┘
       │                                      ▼
       │                      ┌────────────────────────────────────────┐
       │                      │ RAG retrieval (top-4 chunks from       │
       │                      │ uploads/ docs) + deterministic EMI calc│
       │                      └───────────────┬────────────────────────┘
       │                                      ▼
       │                      ┌────────────────────────────────────────┐
       │                      │ OpenRouter gpt-4o-mini (streaming)     │
       │                      │ fallback: local Ollama                 │
       │                      └───────────────┬────────────────────────┘
       │   transcript / token / suggestion_done / wait                 │
       └◄─────────────────────────────────────────────────────────────┘
```

### Request processing detail

| Step | Component | File | What happens |
|---|---|---|---|
| 1 | `create_call()` | `features/call_assistant_v2.py` | Validates numbers, creates session, calls Twilio `calls.create(to=AGENT)` with TwiML + status-callback URLs built from `PUBLIC_BASE_URL` |
| 2 | `twiml()` | same | Twilio fetches this when agent answers. Returns `<Start><Stream track="both_tracks">` (forks audio to our WS) + `<Dial>` (bridges customer) |
| 3 | `twilio_media_ws()` | same | Receives interleaved 20 ms μ-law frames per track. Feeds each track to its own `UtteranceBuffer` |
| 4 | `UtteranceBuffer.feed()` | `core/call_assist_vad.py` | RMS-based state machine (SILENT→PRIMING→SPEECH). Emits a μ-law WAV on trailing silence or 12 s cap |
| 5 | `_run_customer_pipeline()` | `features/call_assistant_v2.py` | Whisper STT (live engine, `telephone=True`) → push transcript to browser → RAG + EMI in parallel → build prompt → stream LLM |
| 6 | `_emit_stream()` | same | Streams spoken tokens to browser; parses `KEY FACTS / INTENT / NEXT ACTION` tail; sends `suggestion_done` |
| 7 | `agent_ws()` | same | Browser WS. Receives `session_info`, `call_status`, `transcript`, `token`, `suggestion_done`, `wait`, heartbeat `ping` |

### Track mapping (critical to understand)
The Twilio `<Stream>` is attached to the **agent's call leg**:
- **`inbound`** = audio the agent's phone sends in = **agent's voice** (transcript-only, no LLM)
- **`outbound`** = audio Twilio plays to the agent = **customer's voice** (drives the LLM pipeline)

Configurable via `CALL_ASSIST_V2_CUSTOMER_TRACK` (default `outbound`). Do not change unless the topology changes.

### Component communication summary
- Browser ↔ backend REST: through Vite proxy `/api` → `http://127.0.0.1:8003`
- Browser ↔ backend WS: through Vite proxy `/ws` → `ws://127.0.0.1:8003` (page-origin based, works over the https tunnel as `wss://`)
- Twilio → backend: over the **backend Cloudflare tunnel** (`PUBLIC_BASE_URL`) — webhooks (TwiML, status) and the media WebSocket
- Backend → OpenRouter: HTTPS streaming (gpt-4o-mini)
- Backend → Whisper: in-process (faster-whisper on local GPU)

---

## 3. Limitations & Constraints

### Why you CANNOT call just any number (most important)
The Twilio account is a **TRIAL account**. Twilio trial accounts can only place calls **to phone numbers that have been verified** in the Twilio console (Verified Caller IDs). Calling an unverified number fails or is blocked.

- To call a new customer number: **Twilio Console → Phone Numbers → Verified Caller IDs → Add** the number (Twilio calls/texts it with a code).
- Trial calls also play a **"press any key to execute"** trial message to the agent before connecting.
- Removing both restrictions requires **upgrading the Twilio account** (adding payment).

### Other current limitations
| Limitation | Reason |
|---|---|
| One suggestion at a time per call | `pipeline_lock` drops new customer utterances while one is being processed — prevents overlapping/contradicting suggestions |
| Customer audio quality is 8 kHz μ-law | Telephone standard; STT accuracy is inherently lower than studio audio. Whisper `large-v3-turbo` + `telephone=True` mode mitigates |
| Tunnel URLs change on every restart | Cloudflare **quick tunnels** are ephemeral. Every `cloudflared` restart = new URL = must update `PUBLIC_BASE_URL` + restart backend |
| In-memory sessions | `_sessions` dict — a backend restart mid-call kills the session. No DB persistence for live calls |
| Single uvicorn worker required | Sessions are in-process; multiple workers would split WS and REST across processes |
| Whisper hallucination on noise | Quiet/noisy phone audio can still produce repeated-word junk; the prompt instructs the LLM to interpret noisy STT generously |
| ~2–4 s end-to-end latency | VAD trailing silence (~300 ms) + STT (~0.3–2 s) + LLM first token (~0.5–1 s) |
| No call recording | Only utterance-level WAVs were dumped during debugging (see §10); recording requires Twilio recording APIs (paid feature considerations) |

### Scalability considerations
- Concurrent calls: each call = 1 Twilio media WS + GPU STT per utterance. The RTX 5060 Ti handles a handful of concurrent calls; beyond that, queue STT or scale horizontally with sticky sessions.
- The OpenRouter dependency is per-utterance; rate limits on the key apply.

---

## 4. Free Tier vs Paid Tier

### Twilio (the binding constraint)
| | Trial (current) | Paid |
|---|---|---|
| Call any number | ❌ verified numbers only | ✅ any number |
| Trial voice message on answer | ❌ plays "press any key…" | ✅ none |
| Credit | one-time ~$15 | pay-as-you-go |
| Caller ID | the one Twilio number | multiple numbers, geographic numbers |
| Cost after upgrade | — | ~$0.013–0.02/min US leg; India outbound legs cost more (check current pricing); 2 legs per copilot call (agent + customer) |

### OpenRouter (LLM)
- Uses `gpt-4o-mini` (`CALL_ASSIST_V2_MODEL`) — paid per token via the `OPENROUTER_API_KEY`, very cheap (~$0.15/M input tokens). Each suggestion ≈ 1–2 k input + 220 output tokens.
- Fallback: **local Ollama** (free, no network) — automatic if OpenRouter fails.

### Whisper STT
- **100 % local and free** (faster-whisper on the local GPU). No per-minute STT charges. This was a deliberate choice — OpenRouter cannot do realtime audio transcription.

### Cloudflare tunnels
- **Quick tunnels (current):** free, no account, but random URL each start + no uptime guarantee.
- **Named tunnels (recommended for production):** free with a Cloudflare account + your own domain → **stable URL** so `PUBLIC_BASE_URL` never changes.

### Cost optimizations already in place
- STT local (zero API cost) • RAG top-k = 4 (small prompts) • `max_tokens=220` per suggestion • WAIT short-circuit stops streaming instantly • Ollama fallback prevents paid-API retry storms.

---

## 5. Project Setup & Startup Guide

### Prerequisites
- Windows with NVIDIA GPU (CUDA) — tested on RTX 5060 Ti
- Miniconda Python (backend runs on `C:\Users\POSIVIEW\miniconda3\python.exe`)
- Node.js + npm (frontend)
- `cloudflared.exe` on PATH
- Ollama installed (fallback LLM) — optional but recommended

### Python dependencies (already installed; for a fresh machine)
```bash
pip install -r requirements.txt
pip install twilio imageio-ffmpeg faster-whisper
```
> **Gotcha:** install into the SAME Python that runs uvicorn. On this machine that is the conda base env, NOT the `.venv` in Downloads.

### ⭐ THE 5 COMMANDS TO RUN THE PROJECT

Open **5 separate terminals** (PowerShell), run in this order:

```bash
# ── Terminal 1: BACKEND (FastAPI, port 8003) ────────────────────────────
cd "C:\AI\hdfc ai\llm-rag-hdfc"
uvicorn main:app --host 0.0.0.0 --port 8003
```

```bash
# ── Terminal 2: FRONTEND (Vite dev server, port 5173) ───────────────────
cd "C:\AI\hdfc ai\llm-rag-hdfc\frontend\frontend"
npm run dev -- --host
```
npm run dev -- --host 127.0.0.1
```bash
# ── Terminal 3: BACKEND TUNNEL (for Twilio webhooks + media WS) ─────────
cloudflared tunnel --url http://127.0.0.1:8003
```

```bash
# ── Terminal 4: FRONTEND TUNNEL (the shareable UI link) ─────────────────
cloudflared tunnel --url http://127.0.0.1:5173
```

```bash
# ── Terminal 5 (run ONCE after Terminal 3 starts): update PUBLIC_BASE_URL
# 1. Copy the https URL printed by Terminal 3 (backend tunnel)
# 2. Put it in .env:   PUBLIC_BASE_URL=https://<terminal-3-url>
# 3. RESTART Terminal 1 (Ctrl+C, run uvicorn again) so it loads the new URL
```

> **CRITICAL:** every time Terminal 3 restarts, the URL changes → update `.env` `PUBLIC_BASE_URL` → restart the backend. Skipping this = Twilio "application error" + dead media WS.

- **Share link** (give to anyone): the URL printed by **Terminal 4** (frontend tunnel).
- The frontend proxies `/api` and `/ws` to the backend automatically (see `vite.config.js`), so only ONE link is shared.

### Health checks & verification
```bash
# Backend alive?
curl http://127.0.0.1:8003/health
# → {"status":"healthy","files_loaded":{...}}

# Backend reachable through its tunnel (what Twilio sees)?
curl https://<backend-tunnel-url>/health

# TwiML serving the CORRECT tunnel URL? (must show the CURRENT backend tunnel)
curl "http://127.0.0.1:8003/api/call-assistant-v2/twiml/test?customer=9999999999"
```

### Avoid `--reload` in this setup
`uvicorn --reload` spawns `multiprocessing-fork` worker processes that **survive a normal kill** and keep serving the OLD environment (stale `PUBLIC_BASE_URL`). This caused hours of confusion. Run without `--reload`; restart manually after code changes. If you must kill orphans:
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'uvicorn|main:app|multiprocessing-fork' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## 6. Environment Variables & Configuration

All in **`.env`** at the project root (loaded by `rag/engine.py` at import). Frontend vars in **`frontend/frontend/.env`**.

### Mandatory for Call Assistant V2
| Variable | Purpose | Example |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio account | `ACxxxxxxxx…` |
| `TWILIO_AUTH_TOKEN` | Twilio auth | (secret) |
| `TWILIO_PHONE_NUMBER` | Caller-ID number Twilio dials FROM | `+13367992930` |
| `AGENT_PHONE_NUMBER` | Default agent phone (must be Twilio-verified on trial) | `+917020222387` |
| `PUBLIC_BASE_URL` | **Backend tunnel URL** — Twilio fetches TwiML + opens the media WS here. No trailing slash. **Changes on every quick-tunnel restart!** | `https://xxx.trycloudflare.com` |
| `OPENROUTER_API_KEY` | LLM suggestions (gpt-4o-mini) | `sk-or-v1-…` |

### Important with good defaults
| Variable | Default | Purpose |
|---|---|---|
| `WHISPER_MODEL_SIZE` | `large-v3-turbo` | Main STT model (push-to-talk path) |
| `WHISPER_LIVE_MODEL_SIZE` | **set to `large-v3-turbo`** (default would be `small`) | Live-call STT model — `small` misdetected Hindi as Punjabi/Malayalam; keep large on GPU |
| `WHISPER_DEVICE` | `cuda` | GPU STT (~150 ms/clip); falls back to CPU automatically |
| `CALL_ASSIST_V2_MODEL` | `gpt-4o-mini` | OpenRouter model for suggestions |
| `CALL_ASSIST_V2_RAG_TOP_K` | `4` | RAG chunks per utterance |
| `CALL_ASSIST_V2_CUSTOMER_TRACK` | `outbound` | Which forked track is the customer |
| `CALL_ASSIST_V2_TRANSCRIBE_AGENT` | `true` | Also transcribe agent voice (reference only) |
| `TWILIO_VALIDATE_SIGNATURE` | `false` | Enforce X-Twilio-Signature on webhooks (enable in production) |

### Frontend (`frontend/frontend/.env`)
| Variable | Current value | Purpose |
|---|---|---|
| `VITE_API_URL` | **empty (keep it empty!)** | Empty = browser uses relative paths + page origin → everything rides the Vite proxy → works on localhost AND through https tunnels. Setting it to `http://127.0.0.1:8003` breaks tunnel access (mixed content) |
| `VITE_DEEPGRAM_API_KEY` | (set) | Used by OTHER features (Live Assistant), not Call Assistant V2 |

### When changing environments, touch these files
1. `.env` → `PUBLIC_BASE_URL` (every tunnel restart), Twilio creds, keys
2. `frontend/frontend/vite.config.js` → proxy targets if the backend port changes
3. Restart backend after ANY `.env` change (env is read at startup only)

---

## 7. Cloudflare & Shareable Links

### How it works
This project uses **Cloudflare Quick Tunnels** — no account, no config file. `cloudflared tunnel --url http://127.0.0.1:<port>` creates a random public `https://*.trycloudflare.com` URL that forwards to the local port. There is **no Cloudflare config in the repo**; the only place a tunnel URL is persisted is `PUBLIC_BASE_URL` in `.env`.

### The two tunnels and their jobs
| Tunnel | Local target | Who uses it | Where configured |
|---|---|---|---|
| Frontend tunnel | `127.0.0.1:5173` | **Humans** — this is the shareable link | nowhere (just share the printed URL) |
| Backend tunnel | `127.0.0.1:8003` | **Twilio only** — TwiML webhook, status callbacks, media WebSocket | `.env` → `PUBLIC_BASE_URL` |

### Routing chain for the browser
`https://frontend-tunnel` → cloudflared → Vite (5173) → Vite proxy:
- `/api/*` → `http://127.0.0.1:8003`
- `/ws/*` → `ws://127.0.0.1:8003` (WebSocket-aware, `ws: true`)

So the browser only ever talks to ONE origin — no mixed-content issues. This is configured in **`frontend/frontend/vite.config.js`**.

### If links need to change
- New backend tunnel URL → update `PUBLIC_BASE_URL` in `.env` → **restart backend**.
- New frontend tunnel URL → nothing to update, just share the new link.
- **Production recommendation:** replace quick tunnels with a **named Cloudflare tunnel** (`cloudflared tunnel create…` + your domain) so URLs never change.

---

## 8. File Structure & Code Locations

```
llm-rag-hdfc/
├── main.py                        # FastAPI app entry — routers registered here (line ~123)
├── .env                           # ALL backend config/secrets (PUBLIC_BASE_URL lives here)
├── features/
│   └── call_assistant_v2.py       # ★ THE FEATURE — REST endpoints, both WebSockets,
│                                  #   session state, customer/agent pipelines, LLM streaming
├── prompts/
│   └── call_assistant_v2.py       # ★ System prompt + user-message builder
│                                  #   (WAIT rules, language hints, output contract)
├── core/
│   ├── twilio_stream.py           # Twilio media WS event parser (connected/start/media/stop)
│   ├── call_assist_vad.py         # UtteranceBuffer VAD + μ-law/linear WAV wrappers
│   ├── whisper_stt.py             # faster-whisper engine (get_engine / get_live_engine,
│   │                              #   telephone=True mode, ffmpeg decode)
│   ├── emi_calc.py                # Deterministic EMI math (model never computes)
│   └── deps.py                    # Shared singletons (rag_instance, log)
├── rag/
│   └── engine.py                  # RAG: retrieval, embeddings, .env loader
├── uploads/                       # HDFC documents indexed at startup (the knowledge base)
└── frontend/frontend/
    ├── vite.config.js             # ★ /api and /ws proxies — tunnel routing depends on this
    ├── .env                       # VITE_API_URL (keep EMPTY)
    └── src/components/
        └── CallAssistantV2.tsx    # ★ The UI — call controls, WS client, transcript,
                                   #   suggestion panels, history
```

### Which file for which change
| Change | File |
|---|---|
| Suggestion style/length/behavior, WAIT rules | `prompts/call_assistant_v2.py` |
| Call flow, endpoints, track mapping, session shape | `features/call_assistant_v2.py` |
| VAD sensitivity (cuts off speech / triggers on noise) | `core/call_assist_vad.py` (+ `VAD_*` env vars) |
| STT model/params/language | `core/whisper_stt.py` + `WHISPER_*` env vars |
| UI panels, badge logic, WS reconnect | `frontend/frontend/src/components/CallAssistantV2.tsx` |
| LLM model | `.env` → `CALL_ASSIST_V2_MODEL` |

---

## 9. Deployment Guide

### Current state: local-dev style deployment
There is **no CI/CD pipeline**. Deployment = run the 5 commands in §5 on a machine with a GPU.

### Production hardening checklist
1. **Stable URLs:** named Cloudflare tunnel (or a server with a real domain + TLS) → `PUBLIC_BASE_URL` becomes permanent.
2. **Frontend build:** `npm run build` → serve `dist/` via the backend or any static host (then `/api`,`/ws` must be reverse-proxied to the backend — same shape as the Vite proxy, e.g. nginx).
3. **Process manager:** run uvicorn under NSSM/Task Scheduler (Windows) or systemd (Linux) with auto-restart. **Single worker** (in-memory sessions).
4. **Enable** `TWILIO_VALIDATE_SIGNATURE=true` so only Twilio can hit the webhooks.
5. **Upgrade Twilio** account (removes verified-number + trial-message limits).
6. Rotate any secrets that were used during development.

### Rollback
Git-based: `git log` → `git checkout <commit> -- <file>` or `git revert`. The feature is isolated to the files in §8, so reverting those files rolls back the feature without touching the rest of the app.

---

## 10. Debugging & Troubleshooting

### Log locations
- Backend (current dev setup): console of the uvicorn terminal. During debugging we redirected to `callv2-debug.log` (stdout) + `callv2-debug.err.log` (uvicorn/app logs).
- Look for tags: `[CALL-V2]`, `[CALL-V2/IN]` (Twilio WS), `[CALL-V2/OUT]` (browser WS), `[CALL-V2/DIAG]` (diagnostics), `[WHISPER]`.
- Audio dumps (diagnostic): `callv2-audio-dumps/*.wav` — every customer utterance WAV (remove this in production; see the TEMP DIAGNOSTIC block in `_run_customer_pipeline`).

### How to trace one call end-to-end
A healthy call log reads exactly like this — find the first missing line and you've found the broken stage:
```
[CALL-V2] Originated call_id=…                  ← Twilio accepted the call
[CALL-V2] status … → ringing → in-progress      ← status webhooks arriving (tunnel OK)
[CALL-V2/IN] Twilio connected                   ← media WS opened (tunnel WS OK)
[CALL-V2/IN] start … tracks=['inbound','outbound']
[CALL-V2/OUT] Agent connected                   ← BROWSER connected (if missing → UI shows nothing!)
[CALL-V2/DIAG] media frames: {'inbound':…,'outbound':…}  ← audio flowing per track
[CALL-V2/DIAG] CUSTOMER utterance emitted       ← VAD detected speech
[WHISPER] 3.0s audio → 'actual words…'          ← STT produced text
[CALL-V2] … customer: '…'                       ← transcript pushed
[CALL-V2] … suggestion (openrouter/gpt-4o-mini) ← LLM answered (or "WAIT on first token")
```

### Issues we actually hit (and their fixes) — read before debugging anything
| Symptom | Root cause | Fix |
|---|---|---|
| `Failed to fetch` on Start Call | UI loaded over https tunnel but API hardcoded to `http://127.0.0.1:8003` (mixed content) | `VITE_API_URL` empty + Vite `/api` proxy |
| `Unexpected token '<' … not valid JSON` | API request answered by Vite's index.html (proxy not loaded — frontend not restarted) | Restart `npm run dev` after vite.config changes |
| 502 from call endpoint: "twilio package not installed" | `twilio` pip package missing **in the Python that runs uvicorn** (was installed into a different venv) | `miniconda3\python.exe -m pip install twilio` |
| Call answers then "application error" + hangup | TwiML pointed at a **dead tunnel URL** — a zombie `--reload` worker was serving the old `PUBLIC_BASE_URL` | Update `.env`, kill ALL python/uvicorn/fork orphans, restart without `--reload` |
| Transcript empty, `utterances=0`, no errors | STT used `get_engine()` = cold `large-v3-turbo` that was still lazy-loading when calls ended | Use `get_live_engine()` (pre-warmed at startup) |
| `Whisper SKIP: No module named 'librosa'` | No audio decoder — ffmpeg missing | `pip install imageio-ffmpeg` |
| Transcript = garbage repetition ("लेक्ष लेक्ष लेक्ष…") | Devanagari `initial_prompt` causes whisper hallucination loops on quiet 8 kHz phone audio (proven by transcribing dumped WAVs with/without it) | `telephone=True` flag skips the initial prompt on the call path |
| Hindi detected as Punjabi/Malayalam/Tamil | `small` whisper model + auto language on phone audio | `WHISPER_LIVE_MODEL_SIZE=large-v3-turbo` + pass UI language as hint |
| UI shows nothing although backend transcribes | Browser WS hardcoded `ws://localhost:8003` — blocked on https pages; **zero "Agent connected" in logs** is the tell | `WS_BASE` = page origin + Vite `/ws` proxy |
| Transcripts visible but "Say This Now" always empty | LLM answered `WAIT` to everything — prompt was too strict for noisy phone STT | Rewrote prompt: help by default, interpret garbled loan-speak generously, WAIT only for pure filler |

### Quick diagnostic commands
```powershell
# Anything on 8003? Which process?
Get-NetTCPConnection -LocalPort 8003 -State Listen

# Twilio-eye view of TwiML (URL inside MUST match current backend tunnel):
curl "http://127.0.0.1:8003/api/call-assistant-v2/twiml/test?customer=9999999999"

# WS handshake works locally?
# (404 on plain GET to a WS route is NORMAL for Starlette — do a real WS connect)
```

---

## 11. Development Notes

### Already implemented
- Outbound two-leg Twilio call (agent-first dial) with both-track media fork
- Per-track VAD → local GPU whisper STT (telephone-tuned) → RAG-grounded streaming suggestions
- Live browser UI: connection badge, live transcript, streaming "Say This Now", intent, next action, sources, call history (localStorage)
- Deterministic EMI calculator injected into the prompt (the model never does math)
- OpenRouter→Ollama fallback chain; WAIT short-circuit; heartbeat + exponential-backoff WS reconnect
- Demo-safe diagnostics: media-frame counters, per-stage DIAG logs, utterance WAV dumps

### Key technical decisions & why
| Decision | Why |
|---|---|
| Local faster-whisper instead of a cloud STT | OpenRouter cannot do realtime transcription; local GPU = zero per-minute cost + no network latency |
| Copilot (agent speaks) instead of voice bot | Trust + compliance: a human stays in control of what is said to the customer |
| Stream attached to the agent leg, `outbound`=customer | One stream covers both speakers; survives customer-side carrier quirks |
| `telephone=True` whisper mode | Devanagari initial_prompt provably destroys quiet μ-law phone audio (hallucination loops) |
| `gpt-4o-mini` for suggestions | ~1 s first token, cheap, good enough with strict grounding + EMI pre-computation |
| Prompt: "help by default, WAIT is exception" | Phone STT is noisy; a strict prompt WAITed on everything and the copilot looked dead |
| In-memory sessions, single worker | Simplicity for demo scale; documented as a production constraint |

### Recommended next steps
1. **Upgrade Twilio** → call any number, no trial message (the single biggest UX unlock)
2. **Named Cloudflare tunnel** → stable `PUBLIC_BASE_URL`, kill the restart-and-update dance
3. Remove the TEMP DIAGNOSTIC blocks (media counters + WAV dumps) from `features/call_assistant_v2.py` for production
4. Persist call sessions + transcripts to disk/DB (post-call review, coaching analytics)
5. Speaker-aware suggestion gating (use agent-track transcripts as context, not just customer)
6. Barge-in handling: cancel an in-flight LLM stream when the customer starts a new utterance instead of dropping the new one
7. Hindi/Marathi STT fine-tuning or a telephony-tuned model for better low-bandwidth accuracy
8. `TWILIO_VALIDATE_SIGNATURE=true` + secret rotation before any public deployment
