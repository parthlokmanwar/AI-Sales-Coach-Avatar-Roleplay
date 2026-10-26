# HDFC RAG System — PitchFlow AI

> **An AI-powered sales coach for HDFC Bank relationship managers.** Generates document-grounded sales pitches, answers customer questions in real time, and coaches reps through live voice conversations — all built on a custom RAG (Retrieval-Augmented Generation) engine over HDFC's internal product documents.

![Status](https://img.shields.io/badge/Status-Production%20Ready-success)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![React](https://img.shields.io/badge/React-18-61dafb)
![FastAPI](https://img.shields.io/badge/FastAPI-0.x-009688)
![Vite](https://img.shields.io/badge/Vite-5-646cff)

---

## Table of Contents

1. [What this is](#what-this-is)
2. [Live demo flows](#live-demo-flows)
3. [System architecture](#system-architecture)
4. [Tech stack](#tech-stack)
5. [Project structure](#project-structure)
6. [Backend deep dive](#backend-deep-dive)
7. [Frontend deep dive](#frontend-deep-dive)
8. [HDFC source documents](#hdfc-source-documents)
9. [API reference](#api-reference)
10. [Prerequisites](#prerequisites)
11. [Local setup](#local-setup)
12. [Environment variables](#environment-variables)
13. [How RAG works in this project](#how-rag-works-in-this-project)
14. [Pitch generation prompt design](#pitch-generation-prompt-design)
15. [Testing & validation scripts](#testing--validation-scripts)
16. [Production notes](#production-notes)
17. [Troubleshooting](#troubleshooting)
18. [Roadmap](#roadmap)
19. [License](#license)

---

## What this is

**PitchFlow AI** is an internal sales-enablement tool built for HDFC Bank loan officers. It ingests HDFC's own product PDFs, SOPs, customer-profiling guides, and pitch decks, then exposes four interfaces over them:

| Mode | What it does | Backed by |
|---|---|---|
| **Quick Form Pitch** | Structured form (customer, product, meeting type) → 9-section sales pitch JSON | Ollama (`llama3.2:3b`) |
| **Free-Write Pitch** | Type the scenario in plain English/Hinglish → same 9-section pitch | Ollama |
| **Ask Me (Chat)** | Ask any question about HDFC products → document-grounded answer with citations | Ollama |
| **Live Assistant (Voice)** | Listens to a live sales call via mic, cleans transcript, answers customer questions in Hinglish in 2–3 lines | Deepgram (STT) + OpenRouter (LLM) with Ollama fallback |

Every generated pitch is **strictly grounded in the uploaded HDFC documents** — the system explicitly refuses to use external knowledge, and falls back to curated HDFC-spec defaults if the LLM output is incomplete.

---

## Live demo flows

### Flow 1 — Generating a sales pitch
1. Rep opens the app, sees the 5 HDFC documents auto-loaded in the sidebar.
2. Fills the Quick Form (`Mr. Sharma`, `Personal Loan`, `First Meeting`, customer profile, goal).
3. Backend runs vector search over the HDFC docs → builds a `Vikram-the-RM` system prompt → calls Ollama with `format: json` → parses 9-section pitch.
4. Frontend renders expandable cards: Opening Hook, SPIN Questions, What NOT to Say, Anticipated Q&A, Objection Handling (Feel/Felt/Found), Key Product Facts, Closing Strategy, Full Script, Follow-Up Email.
5. Rep can **Copy**, **Download as DOCX**, **Regenerate**, or auto-save to history. Optional translation to Hindi/Marathi/Tamil/Bengali/Telugu/Gujarati/Kannada/Malayalam via `deep-translator`.

### Flow 2 — Live voice assistance during a customer meeting
1. Rep clicks **Live Assistant**, picks customer type (salaried / doctor / business owner / etc.), loan type, customer status, pre-approved flag.
2. Browser captures mic audio → sends 5-second chunks to **Deepgram** for transcription with speaker diarisation (`diarize=true`).
3. Customer voice is detected (vs. rep's voice via speaker tag heuristic).
4. Raw transcript is sent to `/api/voice/clean-transcript` → OpenRouter cleans Hinglish filler words into a proper question.
5. Cleaned question hits `/api/voice/answer` → RAG retrieves top-3 chunks → OpenRouter generates a 2–3 line Hinglish answer.
6. Answer is displayed instantly, optionally spoken back via browser TTS.
7. At session end, `/api/voice/session-summary` produces a CRM debrief (objections, competitor heatmap, next best action).

---

## System architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          BROWSER (React)                            │
│  Quick Form │ Free Write │ Ask Me Chat │ Live Voice Assistant       │
└────────────┬───────────────────────────────────────┬────────────────┘
             │ HTTPS (REST + WebSocket)              │ Browser MediaRecorder
             │                                       │
             ▼                                       ▼
┌────────────────────────────────┐    ┌──────────────────────────────┐
│   FastAPI Backend (main.py)    │    │  Deepgram API (STT,          │
│   :8002                        │    │  diarisation, en-IN)         │
│                                │    └──────────────────────────────┘
│  /api/pitch                    │
│  /api/chat                     │    ┌──────────────────────────────┐
│  /api/voice/clean-transcript   ├───►│  OpenRouter API              │
│  /api/voice/answer             │    │  (free-tier multi-model      │
│  /api/voice/session-summary    │    │   fallback chain)            │
│  /api/files                    │    └──────────────────────────────┘
│  /api/pitch/history            │
│  /api/pitch/download (DOCX)    │    ┌──────────────────────────────┐
│  /ws/voice (WebSocket)         ├───►│  Ollama (llama3.2:3b)        │
│  /health                       │    │  http://localhost:11434       │
└──────────┬─────────────────────┘    │  (local fallback / pitches)  │
           │                          └──────────────────────────────┘
           ▼
┌─────────────────────────────────────┐
│  EnhancedRAGv4 (app.py, 1300 LOC)   │
│  ─────────────────────────────────  │
│  • Document loader (PDF/DOCX/PPTX/  │
│    TXT/CSV/XLSX with table extract) │
│  • SmartChunker (paragraph/         │
│    semantic, adaptive by size)      │
│  • VectorMatcher                    │
│    (all-MiniLM-L6-v2 embeddings)    │
│  • Cosine similarity file routing   │
│  • Conversation memory (last 5)     │
│  • Pitch JSON normaliser +          │
│    HDFC-grounded fallbacks          │
│  • Multi-lang translation           │
└─────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│  uploads/  (auto-loaded on startup) │
│  • HDFC_Personal_Loan_Policy.pdf    │
│  • HDFC_Personal_Loan_SOP_Internal  │
│    .docx                            │
│  • HDFC_Customer_Profiling_Guide    │
│    .docx                            │
│  • HDFC_Personal_Loan_Pitch_Deck    │
│    .pptx                            │
│  • HDFC_Personal_Loan_Sales_Guide   │
│    .txt                             │
└─────────────────────────────────────┘
```

---

## Tech stack

### Backend
| Layer | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** | Async, automatic OpenAPI docs at `/docs`, Pydantic validation |
| LLM (slow, high quality) | **Ollama** `llama3.2:3b` | Local, free, no API limits — used for pitch generation & document summaries |
| LLM (fast, short answers) | **OpenRouter** free tier with 6-model fallback chain | Sub-second response for live voice; resilient against rate limits |
| Embeddings | **sentence-transformers** `all-MiniLM-L6-v2` (384-dim) | Lightweight, runs on CPU, good multilingual coverage |
| Similarity | **scikit-learn** cosine similarity | Simple, no vector DB overhead for ~5 docs |
| PDF parsing | **pdfplumber** | Best-in-class table extraction (critical for loan eligibility tables) |
| DOCX parsing | **python-docx** + custom XML walker | Preserves paragraph/table order |
| PPTX parsing | **python-pptx** | Slide-by-slide with embedded tables |
| Translation | **deep-translator** (Google Translate) | 9 Indian languages post-processed on the pitch JSON |
| DOCX export | **python-docx** | Generates downloadable `HDFC_Sales_Pitch.docx` |
| Persistence | **JSON file** (`pitch_history.json`) | No DB needed — keeps last 200 pitches |

### Frontend
| Layer | Choice |
|---|---|
| Framework | **React 18** (functional components, hooks) |
| Build tool | **Vite 5** with proxy to backend on `:8002` |
| Styling | Hand-written **CSS variables** in `index.css` + **Tailwind** configured but mostly inline styles |
| Icons | **lucide-react** |
| Speech-to-text | **Deepgram Web API** (browser-side, `en-IN`, diarisation) |
| Text-to-speech | Browser `SpeechSynthesis` API |
| Audio capture | `MediaRecorder` (5-second chunks, `audio/webm`) |

---

## Project structure

```
HDFC-RAG-SYSTEM/
│
├── app.py                          # Core RAG engine — EnhancedRAGv4 class (1311 LOC)
├── main.py                         # FastAPI backend — all endpoints (638 LOC)
├── requirements.txt                # Python dependencies
├── .env.example                    # Template for API keys
├── .gitignore                      # Excludes .env, .venv, node_modules, __pycache__
├── pitch_history.json              # Persisted pitch history (last 200)
│
├── uploads/                        # HDFC source documents (auto-loaded on startup)
│   ├── HDFC_Personal_Loan_Policy.pdf
│   ├── HDFC_Personal_Loan_SOP_Internal.docx
│   ├── HDFC_Customer_Profiling_Guide.docx
│   ├── HDFC_Personal_Loan_Pitch_Deck.pptx
│   └── HDFC_Personal_Loan_Sales_Guide.txt
│
├── frontend/frontend/              # React + Vite SPA (note: nested folder)
│   ├── index.html
│   ├── package.json                # react, react-dom, lucide-react, vite, tailwind
│   ├── vite.config.js              # Proxies /api → http://127.0.0.1:8002
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── tsconfig.json
│   └── src/
│       ├── main.jsx                # React entry point
│       ├── App.jsx                 # Main SPA (717 LOC) — all UI except live voice
│       ├── index.css               # Design system, ~400 LOC of CSS vars + utility classes
│       └── components/
│           └── LiveAssistant.tsx   # Voice assistant (679 LOC) — mic, Deepgram, TTS, CRM debrief
│
├── test_pitch.py                   # Smoke test for /api/pitch generation
├── test_vectors.py                 # Confirms sentence-transformers loads correctly
├── test_edge_cases.py              # Stress test: noisy Hinglish, off-topic, empty input
├── test_live_assistant.py          # Full integration test of live voice pipeline
├── test_mock.txt                   # Tiny upload sample
├── simulate_live_session.py        # Scripted single-customer voice session simulator
├── simulate_live_session_multi.py  # Multiple back-to-back simulated sessions
├── verify_features.py              # Quick HTTP test of all endpoints
├── verify_features_direct.py       # Same but bypasses HTTP (direct Python calls)
├── validate_final_live_assistant.py # Production validation suite
├── reproduce_issue.py              # Minimal repro for past bug fixes
├── fix_merge.py                    # One-off script that patched app.py
├── test_results.md                 # Output log from validation runs
├── test_results_2.md               # Output log from validation runs
└── test_results_3.md               # Output log from validation runs
```

---

## Backend deep dive

### `app.py` — `EnhancedRAGv4` class

The heart of the system. Single class (~1100 LOC) covering:

#### 1. **Document ingestion** — `add_file(path, name)`
Dispatches by extension:
- `.csv` → pandas DataFrame stored in `self.structured_data`
- `.xlsx` / `.xls` → each sheet stored separately as `filename:sheet`
- `.pdf` → pdfplumber extracts **text + tables** per page (tables get Markdown-grid formatting so the LLM can read them)
- `.docx` → walks XML body, preserves paragraph & table order, formats tables as Markdown
- `.pptx` → slide-by-slide, includes embedded tables
- `.txt` → UTF-8 decode

After extraction, `_process_document()` runs `_generate_document_summary()` (an Ollama call producing a 4–5 sentence summary including topic, entities, dates, numbers, and "what questions this can answer"). The summary is what gets embedded for vector routing — not the raw text — so file matching is semantic, not lexical.

#### 2. **Chunking** — `SmartChunker`
Adaptive strategy based on document size:
- `< 28,000 chars` → no chunking (passed whole)
- `28k–80k chars` → **paragraph chunking** (split on `\n\n`, target 1000 chars/chunk)
- `> 80k chars` → **semantic chunking** (sentence-aware splits with 200-char overlap)

At query time, `get_relevant_chunks()` scores each chunk by word overlap with the query and returns top-K.

#### 3. **Vector matcher** — `VectorMatcher`
Uses `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~80 MB model, CPU-fast). Each file gets ONE embedding generated from a structured "file signature" string:

```
Filename: HDFC_Personal_Loan_Policy.pdf
Type: Document (PDF)
Summary: <LLM-generated 4-5 sentence summary>
```

For queries, the query string is embedded and cosine similarity routes to the top-K most relevant files (`threshold=0.15`, `top_k=8` for pitches, `top_k=3` for live voice for speed).

#### 4. **Pitch generation** — `generate_pitch(scenario, language)`
The flagship feature:
1. Retrieves top-8 most relevant chunks via vector search.
2. Builds a massive system prompt as **"Vikram, top-performing HDFC RM with 12 years experience"** including SPIN selling, Feel/Felt/Found framework, document-grounding rules.
3. Specifies a strict 9-key JSON schema.
4. Calls Ollama with `format=json`, `temperature=0.75`, `num_predict=6000` (richer outputs).
5. Parses JSON with multi-tier fallback (`_parse_pitch_json`):
   - Direct `json.loads`
   - Strip markdown fences and retry
   - Regex-extract first `{...}` block
   - Last resort: stuff raw text into `narrative_summary`
6. `_normalize_pitch()` enforces structure — every key must exist with the right type. If the LLM returns placeholders (e.g. `"string (a question)"`) or empty arrays, **HDFC-spec hardcoded fallbacks** kick in (real product facts: ₹50k–₹40L range, 12–60 months, 10.50% p.a., 4-hour disbursal, 2% prepayment fee, etc.).
7. Optional translation to 9 Indian languages via `deep-translator`.

#### 5. **Ask-Me chat** — `ask_question(question)`
1. Query expansion: if question contains `approve`/`eligib`/`emi`/`loan`, injects banking terms (`FOIR`, `Fixed Obligation Income Ratio`, `Segment`, `Maximum Loan Table`, `Eligibility`) into the search query to pull eligibility rules + tables.
2. Retrieves top-12 chunks.
3. Builds a strict prompt: "Answer ONLY from the document context. If not found, say exactly: 'I could not find this information in the loaded documents.'"
4. Calls Ollama (chat-tuned: `temp=0.1`, `num_predict=2000`).
5. Strips any leaked `<think>` tags from reasoning models.
6. Maintains last-5-turn conversation memory for follow-ups.

#### 6. **Live voice answer** — `answer_live_question(question, question_type, session_context, history)`
Optimised for sub-second latency:
- `top_k=3` only (small context).
- Uses **OpenRouter** primary (free models, 120-token max).
- Falls back to **Ollama** (`num_predict=120`) if OpenRouter fails.
- Two prompt modes: `factual` (3-line direct answer) or `objection` (Feel/Felt/Found script).
- Outputs Hinglish directly (no post-translation).
- Personalises by `customerType` (doctor → references doctor-specific terms, self-employed → ITR rules, etc.).

#### 7. **OpenRouter multi-model fallback** — `_call_openrouter_fast()`
Tries 6 free models in order until one works:
1. `google/gemma-4-26b-a4b-it:free`
2. `meta-llama/llama-3.2-3b-instruct:free`
3. `deepseek/deepseek-v4-flash:free`
4. `nvidia/nemotron-nano-9b-v2:free`
5. `meta-llama/llama-3.3-70b-instruct:free`
6. `openai/gpt-oss-20b:free`

Handles HTTP 429 (rate limit) and 404 (model removed) gracefully by skipping to the next model.

---

### `main.py` — FastAPI app

**Server**: `uvicorn main:app --host 0.0.0.0 --port 8002 --reload`

**Startup behaviour**:
- Instantiates one global `EnhancedRAGv4`.
- Auto-loads every supported file from `uploads/` and creates embeddings.
- Spawns a background task to `ollama pull llama3.2:3b` so the fallback model is ready.

**CORS**: allows `localhost:5173`, `5174`, `3000` for dev.

**Pitch history**: JSON file (`pitch_history.json`), capped at 200 entries, newest-first.

---

## Frontend deep dive

### `App.jsx` (717 LOC)

A single-file React SPA with no router — uses local state to switch between 4 tabs:

| Component | Purpose |
|---|---|
| `QuickForm` | Structured pitch input (name, product, meeting type, profile, goal, language) |
| `FreeWriteForm` | Free-text scenario textarea |
| `ChatPanel` | Streaming chat UI with source citations |
| `LiveAssistant` (separate file) | Voice mode |
| `PitchOutput` | Renders 9 expandable cards (`PitchCard`) for the generated pitch |
| `PitchCard` | Collapsible section with emoji + title (Opening Hook, Questions to Ask, etc.) |
| `HistoryView` | Read-only view of a previously saved pitch |

**Sidebar**:
- **Loaded Documents** (auto-fetched from `/api/files` on mount, shows file icons by extension)
- **Pitch History** (last 50 from `/api/pitch/history`, clickable + deletable)

**Pitch actions**: Copy Full Pitch (clipboard), Download DOCX (`/api/pitch/download`), Save (auto-saved), Regenerate (re-runs with same scenario/language).

### `LiveAssistant.tsx` (679 LOC)

The voice pipeline:

1. **`startListening()`** — requests mic, starts `MediaRecorder` chunking every 5 seconds.
2. **`transcribeWithDeepgram(blob)`** — POSTs audio to Deepgram with `language=en-IN`, `diarize=true`, returns transcript + speaker tag.
3. **`isSalespersonSpeaking()`** — fingerprints the first speaker; subsequent matching speakers are ignored (only customer questions are processed).
4. **`detectLanguage()`** — counts Hinglish indicator words (`kya`, `hai`, `matlab`, `bhai`, etc.) to switch UI language.
5. **`cleanTranscript()`** — POSTs raw text to `/api/voice/clean-transcript` (OpenRouter cleans filler words).
6. **`answerQuestion()`** — POSTs cleaned question to `/api/voice/answer` with full session context.
7. **`speakAnswer()`** — uses browser `SpeechSynthesis` to read the answer back in `en-IN`.
8. **`generateSessionSummary()`** — at session end, POSTs full transcript to `/api/voice/session-summary` for CRM debrief.

**Session context UI**: 4 dropdowns (customer type, loan type, customer status, pre-approved) configure the prompt before mic starts.

**Caching**: `answerCacheRef` (Map) caches identical questions to avoid redundant LLM calls.

### `index.css`

Design system with CSS variables (`--navy`, `--primary`, `--bg`, `--border`, `--text`, etc.), utility classes (`.btn`, `.card`, `.pitch-card`, `.qa-pair`, `.obj-pair`, `.chat-msg`, `.spinner`, `.fade-in`, `.slide-up`), and component-specific styles. Mostly hand-rolled; Tailwind is configured but only lightly used.

---

## HDFC source documents

All in `uploads/`, auto-loaded on every server start.

| File | Format | Purpose |
|---|---|---|
| `HDFC_Personal_Loan_Policy.pdf` | PDF | Official policy: eligibility tables, FOIR rules, interest rate slabs, tenure, fees |
| `HDFC_Personal_Loan_SOP_Internal.docx` | DOCX | Internal SOP for RMs: process flow, KYC checks, escalation matrix |
| `HDFC_Customer_Profiling_Guide.docx` | DOCX | Customer segments (salaried, self-employed, doctor, business owner, etc.) and pitch angles for each |
| `HDFC_Personal_Loan_Pitch_Deck.pptx` | PPTX | Marketing slides with product USPs, comparison vs competitors, customer testimonials |
| `HDFC_Personal_Loan_Sales_Guide.txt` | TXT | Quick-reference cheat sheet for sales conversations |

> **All facts in generated pitches (rates, EMI, tenure, eligibility) are pulled from these documents.** Prompts explicitly forbid the LLM from inventing numbers.

---

## API reference

Base URL: `http://localhost:8002`

OpenAPI/Swagger docs auto-generated at: **http://localhost:8002/docs**

### Pitch generation

#### `POST /api/pitch`
Generate a structured 9-section sales pitch.

```jsonc
// Request
{
  "scenario": "Customer: Mr. Sharma\nProduct: Personal Loan\nMeeting Type: First Meeting\nProfile: 45yr business owner, current bank SBI\nGoal: Pitch home renovation loan",
  "language": "English"  // or Hindi, Marathi, Tamil, Bengali, Telugu, Kannada, Gujarati, Malayalam
}

// Response
{
  "success": true,
  "pitch": {
    "opening_hook": "...",
    "questions_to_ask": ["...", "..."],
    "what_not_to_say": ["...", "..."],
    "anticipated_customer_questions": [{"question": "...", "answer": "..."}],
    "objection_handling": [{"objection": "...", "response": "..."}],
    "key_product_facts": ["...", "..."],
    "closing_strategy": "...",
    "narrative_summary": "...",  // 400-600 word full script
    "follow_up_email": "..."
  },
  "documents_used": ["HDFC_Personal_Loan_Policy.pdf", "..."],
  "history_id": "uuid"
}
```

#### `POST /api/pitch/download`
Body: `{ "pitch": {...} }` → returns a `HDFC_Sales_Pitch.docx` file stream.

#### `GET /api/pitch/history`
Returns the last 50 pitches, newest first.

#### `DELETE /api/pitch/history`
Clear all pitch history.

#### `DELETE /api/pitch/history/{pitch_id}`
Delete one specific pitch by UUID.

### Chat (Ask Me)

#### `POST /api/chat`
```jsonc
// Request
{ "question": "What is the FOIR limit for self-employed customers?" }

// Response
{
  "success": true,
  "answer": "The FOIR limit for self-employed customers is 50%, calculated as...",
  "sources": ["HDFC_Personal_Loan_Policy.pdf", "HDFC_Customer_Profiling_Guide.docx"]
}
```

#### `GET /api/chat/history` — in-memory chat history.
#### `DELETE /api/chat/history` — clear chat history (keeps documents loaded).

### Voice

#### `POST /api/voice/clean-transcript`
Cleans Hinglish/spoken filler into a clean question via OpenRouter.

#### `POST /api/voice/answer`
```jsonc
// Request
{
  "question": "What is the processing fee for a 20 lakh loan?",
  "question_type": "factual",  // or "objection"
  "session_context": {
    "customerType": "doctor",
    "loanType": "personal-loan",
    "customerStatus": "new",
    "preApproved": "yes"
  },
  "history": [{"role": "Customer", "content": "..."}]
}

// Response
{ "success": true, "answer": "Sir, 20 lakh loan ka processing fee 2.50% + GST hai. Approx ₹59,000.", "sources": [...] }
```

#### `POST /api/voice/session-summary`
Generates a CRM debrief (objections raised, competitor mentions, next best action) from full transcript.

#### `WebSocket /ws/voice`
Real-time bidirectional channel for live voice. Send `{"type":"question","text":"..."}`, receive `{"type":"answer","text":"...","sources":[...]}`.

### Files & utility

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/files` | List loaded documents with metadata (rows/columns for CSV, chunking strategy for docs) |
| `DELETE` | `/api/files/clear` | Reset RAG instance and reload from `uploads/` |
| `DELETE` | `/api/context/clear` | Clear conversation memory (keeps files) |
| `GET` | `/` | Health check |
| `GET` | `/health` | Detailed status (loaded counts, history size) |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.8+ | Backend, RAG engine, sentence-transformers |
| **Node.js** | 18+ | Frontend, Vite dev server |
| **Ollama** | latest | Local LLM runtime — auto-installs `llama3.2:3b` on first run |
| **OpenRouter account** | free tier | For fast live-voice answers — sign up at https://openrouter.ai |
| **Deepgram account** | free tier ($200 credit) | Speech-to-text — sign up at https://deepgram.com |
| **Sarvam account** | optional | Indian-language TTS alternative — sign up at https://sarvam.ai |

### Install Ollama
```bash
# https://ollama.ai/download — install, then:
ollama pull llama3.2:3b
ollama serve   # runs on http://localhost:11434
```

---

## Local setup

### 1. Clone & enter
```bash
git clone https://github.com/vp-017/HDFC-RAG-SYSTEM.git
cd HDFC-RAG-SYSTEM
```

### 2. Backend
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt

# Add your API keys
copy .env.example .env          # Windows
# cp .env.example .env          # macOS/Linux
# then edit .env and fill in real keys

python main.py
# → API on http://localhost:8002
# → Swagger docs on http://localhost:8002/docs
```

### 3. Frontend
```bash
cd frontend/frontend
npm install
npm run dev
# → UI on http://localhost:5173
```

The Vite dev server proxies `/api/*` → `http://127.0.0.1:8002`, so you don't need to configure CORS or absolute URLs in dev.

### 4. Open the app
http://localhost:5173 — the 5 HDFC documents are already auto-loaded.

---

## Environment variables

Create `.env` in the project root. **Minimum viable setup** is one line —
Deepgram is the only external API you actually need. Everything else has
a working fallback or is purely optional.

```bash
# REQUIRED — the only key you really need
VITE_DEEPGRAM_API_KEY=...                # Speech-to-text in browser

# OPTIONAL — all have fallbacks
OPENROUTER_API_KEY=...                   # Only used if Ollama fails (rare)
VITE_SARVAM_API_KEY=...                  # Only used for better Hindi STT
VITE_USE_SARVAM=true                     # Set false to skip Sarvam entirely
```

| Variable | Used by | Required? | Fallback when missing/failing |
|---|---|---|---|
| `VITE_DEEPGRAM_API_KEY` | `LiveAssistant.tsx` (browser, `import.meta.env`) | **Yes** | Local Whisper via `/api/voice/stt` (~80–150ms/chunk on GPU with `large-v3-turbo`, ~1.5–3s on CPU) |
| `OPENROUTER_API_KEY` | `rag/engine.py` + `features/voice.py` | No | Local Ollama is the primary path; OpenRouter only runs if Ollama dies |
| `VITE_SARVAM_API_KEY` | `LiveAssistant.tsx` (browser, only on Hindi chunks) | No | Deepgram's Hindi result is used instead |
| `VITE_USE_SARVAM` | `LiveAssistant.tsx` | No | Defaults to `true`; set `false` to skip Sarvam call entirely |
| `VOICEPRINT_BACKEND` | `core/voiceprint.py` | No | Defaults to `mfcc` (librosa-only, works on any Python). Options: `mfcc` / `resemblyzer` / `pyannote` |
| `VOICEPRINT_THRESHOLD` | `core/voiceprint.py` | No | Per-backend default — `mfcc:0.88`, `resemblyzer:0.75`, `pyannote:0.80` |
| `WHISPER_MODEL_SIZE` | `core/whisper_stt.py` | No | `large-v3-turbo` (~809 MB int8). Options: `tiny`/`base`/`small`/`medium`/`large-v3-turbo` |
| `WHISPER_DEVICE` | `core/whisper_stt.py` | No | `cuda` (recommended; auto-falls back to `cpu` if no GPU). Options: `cuda`/`cpu` |
| `WHISPER_COMPUTE_TYPE` | `core/whisper_stt.py` | No | `int8` (smallest VRAM, near-`float16` accuracy). Options: `int8`/`int8_float16`/`float16`/`float32` |
| `WHISPER_LANGUAGE` | `core/whisper_stt.py` | No | `auto` — set to `en` or `hi` to skip language detection and speed up inference |

### STT fallback chain (what happens when an API fails)

```
Browser captures audio chunk
        │
        ├── voiceprint pre-filter (skip if agent's voice)
        │
        ▼
┌────────────────────────┐
│ try Deepgram (primary) │ ← needs VITE_DEEPGRAM_API_KEY
└─────────┬──────────────┘
          │ fail (network/quota/key)
          ▼
┌──────────────────────────────────┐
│ try local Whisper (POST /stt)    │ ← faster-whisper on backend, offline
└─────────┬────────────────────────┘
          │ also fail
          ▼
    chunk dropped (logged to console)
```

For Hindi chunks specifically, Sarvam is tried after Deepgram succeeds (it
re-transcribes with better Hinglish accuracy). If Sarvam fails or is disabled,
Deepgram's Hindi transcript is used unchanged.

### LLM fallback chain (pitch / chat / voice answer / clean-transcript / summary)

```
local Ollama (llama3.2:3b for voice, qwen2.5:32b for pitch)
        │ fail
        ▼
OpenRouter free tier (multi-model rotation)
        │ fail
        ▼
graceful error / raw passthrough
```

> **Important:** `.env` is gitignored. Never commit it. Use `.env.example` as the template.

---

## How RAG works in this project

```
USER QUERY
   │
   ▼
┌─────────────────────────────────────┐
│ 1. Query expansion (if banking      │
│    keywords detected, inject FOIR/  │
│    Segment/Eligibility terms)       │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│ 2. Embed query → 384-dim vector     │
│    (sentence-transformers MiniLM)   │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│ 3. Cosine-similarity match against  │
│    pre-computed file embeddings     │
│    (top_k=3-12, threshold=0.15)     │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│ 4. For each matched doc, pick the   │
│    top relevant chunks via word-    │
│    overlap scoring (SmartChunker)   │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│ 5. Build prompt with: system role + │
│    retrieved context + conversation │
│    history + user question          │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│ 6. LLM call (Ollama for slow/rich,  │
│    OpenRouter for fast/short)       │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│ 7. Parse, normalise, validate       │
│    (fallback to HDFC-spec defaults  │
│    if output is incomplete)         │
└─────────────────────────────────────┘
   │
   ▼
ANSWER + SOURCES
```

**Why file-level embeddings (not chunk-level)?** With only ~5 documents, file-level routing is faster, more interpretable, and surfaces source citations naturally. Chunk-level retrieval happens *within* the selected file via word-overlap scoring.

---

## Pitch generation prompt design

The `generate_pitch()` prompt is the most important piece of prompt engineering in the project. Key elements:

1. **Persona**: "Vikram, top-performing HDFC RM with 12 years experience, renowned for turning sceptical customers into loyal clients."
2. **Coaching style guardrails**:
   - **Empathy first**: Always acknowledge before countering.
   - **Story-driven**: "I had a customer just like you last month..."
   - **SPIN selling**: Situation, Problem, Implication, Need-Payoff.
   - **Feel/Felt/Found**: "I understand how you FEEL... Many of our best clients FELT the same way... What they FOUND was..."
   - **Document-grounded**: Every fact MUST come from HDFC context. Never invent.
   - **Multiple trial closes**: Embed soft closes ("Does that sound like it could work?").
3. **9-section JSON schema** with explicit length requirements per section (400–600 words for narrative, 4–6 sentences per objection response).
4. **Specific objection coverage**: Rates too high, past rejection, "I'll wait", competitor offer, low CIBIL, can't afford EMI.
5. **`format=json`** flag on Ollama to force structured output.
6. **`temperature=0.75`** for human, persuasive language (vs `0.1` for the chat endpoint which needs factual precision).
7. **Post-LLM normalisation**: `_normalize_pitch()` ensures all 9 keys exist; if the LLM returned placeholders like `"string (a question)"`, hardcoded HDFC-spec fallbacks fill the gap.

---

## Testing & validation scripts

All run from the project root:

| Script | What it does |
|---|---|
| `test_pitch.py` | End-to-end smoke test for `/api/pitch` |
| `test_vectors.py` | Verifies `sentence-transformers` loads and produces valid embeddings |
| `test_edge_cases.py` | Stress test: noisy Hinglish, off-topic questions, empty input, very long input |
| `test_live_assistant.py` | Full pipeline test with rate-limit-aware retries |
| `simulate_live_session.py` | Scripted single-customer voice session simulator |
| `simulate_live_session_multi.py` | Multiple back-to-back simulated sessions |
| `verify_features.py` | HTTP-level test of every endpoint |
| `verify_features_direct.py` | Same checks but via direct Python imports (no HTTP) |
| `validate_final_live_assistant.py` | Production validation suite — used as the release gate |
| `reproduce_issue.py` | Minimal repro for a past bug — kept for regression reference |
| `fix_merge.py` | One-off patcher (already applied; safe to ignore) |

Run a smoke test:
```bash
python test_pitch.py
```

Sample outputs from past runs are in `test_results.md`, `test_results_2.md`, `test_results_3.md`.

---

## Production notes

### Backend
```bash
pip install gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8002
```
- Run Ollama as a systemd service.
- Persist `pitch_history.json` on a real volume.
- Tighten CORS in `main.py` (`allow_origins=["https://your-domain.com"]`).
- Reverse-proxy via nginx with TLS.

### Frontend
```bash
cd frontend/frontend
npm run build
# serve the dist/ folder via nginx or any static host
```
- Set `VITE_DEEPGRAM_API_KEY` at build time (it gets baked into the bundle — **note: this exposes the key to the browser**; for production consider proxying Deepgram calls through the backend instead).

### Scaling considerations
- Single `EnhancedRAGv4` instance lives in process memory. For multi-worker setups (gunicorn `-w 4`), each worker holds its own copy — fine for read-only document state, but `conversation_history` and `query_cache` won't be shared. Move them to Redis if multi-worker.
- For >100 documents, swap the in-memory cosine-similarity dict for a real vector DB (Qdrant, Weaviate, pgvector).

### Cost notes
- Ollama: free (local CPU/GPU).
- OpenRouter free models: free but rate-limited (the 6-model fallback chain mitigates this).
- Deepgram: $200 free credit ≈ 4,500 minutes of speech.
- Sentence-transformers: free, runs on CPU.

---

## Troubleshooting

### "No module named 'app'" when running `main.py`
Run from the project root, not from inside a subfolder.

### Ollama connection refused
```bash
ollama serve     # ensure it's running on :11434
curl http://localhost:11434/api/tags
```

### Frontend can't reach backend
- Backend must be on `:8002` (not 8000).
- Check `frontend/frontend/vite.config.js` proxy target matches.

### Pitch generation returns placeholders like `"string (...)"` or empty arrays
The LLM is confused by the schema. The `_normalize_pitch()` fallback will fill in HDFC-spec defaults, but check the prompt isn't being truncated (`num_predict=6000` should be enough).

### "sentence-transformers not available"
```bash
pip install sentence-transformers scikit-learn
```
First load downloads ~80 MB of the MiniLM model — needs internet on first run.

### Live voice: "Deepgram error 401"
Check `VITE_DEEPGRAM_API_KEY` is set correctly in `.env` before running `npm run dev` (Vite bakes env vars at startup).

### Live voice: all OpenRouter models fail
Check `OPENROUTER_API_KEY` and that your free quota isn't exhausted. The system will automatically fall back to local Ollama for live answers.

### CSV/Excel upload fails silently
Check the file has UTF-8 encoding and no merged cells. pandas reads it; corrupted files raise exceptions logged at startup.

---

## Roadmap

Possible improvements:

- [ ] Swap file-level cosine similarity for chunk-level retrieval with a real vector DB (Qdrant) when doc count grows.
- [ ] Move `OPENROUTER_API_KEY` server-side only — proxy Deepgram through the backend so browser doesn't see the key.
- [ ] Add streaming token output for `/api/pitch` to reduce perceived latency.
- [ ] Persist pitch history in SQLite/Postgres instead of JSON file.
- [ ] Multi-user mode with per-RM history and shared document corpus.
- [ ] Authentication (currently open; assumes trusted internal network).
- [ ] Move from free OpenRouter models to a paid SLA tier for production.
- [ ] Add unit tests for `EnhancedRAGv4` (current tests are integration-level).
- [ ] Audit log of every pitch generated, for compliance review.

---

## License

Internal use only — provided as-is for HDFC Bank sales enablement. No external distribution.

---

## Acknowledgements

- **HDFC Bank** for the source product documents.
- **Ollama** for local LLM inference.
- **OpenRouter** for the free-tier model gateway.
- **Deepgram** for Indian-English speech recognition.
- **sentence-transformers / Hugging Face** for embeddings.
- **FastAPI**, **React**, **Vite** for the application stack.

---

**Built for HDFC Bank Relationship Managers.** Pitch smarter, close faster, stay grounded in policy.
