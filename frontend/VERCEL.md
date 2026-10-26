# Deploying the frontend to Vercel (backend stays local)

The frontend is a static Vite/React SPA → Vercel hosts it fine. The backend
(FastAPI + faster-whisper + Ollama + WebSockets) **cannot** run on Vercel; it
keeps running on your machine and is reached over a public **https tunnel**.

```
Browser ──https──> Vercel (this SPA)
   └──https/wss──> https://xxxx.trycloudflare.com ──> your localhost:8003 backend
```

> You cannot point the site at `http://localhost:8003`: in a visitor's browser
> `localhost` is *their* machine, and an https page may not call http/ws (mixed
> content). The tunnel gives you a public https/wss URL that fixes both.

## 1. Expose the local backend with a STABLE url (one-time setup)

Use an **ngrok static domain** — free, no domain to buy, and the URL never
changes, so you set `VITE_API_URL` on Vercel exactly once.

One-time setup:

```powershell
winget install ngrok.ngrok                    # or: choco install ngrok
ngrok update                                  # winget ships an OLD build (3.3.1);
                                              # accounts require agent >= 3.20 (ERR_NGROK_121)
ngrok config add-authtoken <token>            # from dashboard.ngrok.com (free signup)
```

Claim your free static domain at https://dashboard.ngrok.com → **Domains** →
**Create Domain** (you get one free, e.g. `hdfc-demo.ngrok-free.app`).

Tell the start script your domain once (persists across reboots):

```powershell
setx NGROK_DOMAIN "hdfc-demo.ngrok-free.app"   # reopen the window after this
```

Then, every time you run the demo, **one command** from the repo root:

```powershell
.\start-backend.ps1
```

This launches the backend (Python 3.10, the one with faster-whisper) in its own
window, waits for it to be ready, and opens the stable ngrok tunnel. Your backend
is permanently at `https://hdfc-demo.ngrok-free.app` (same URL every run). The
ngrok "browser warning" interstitial is handled in code (`auth.js` sends
`ngrok-skip-browser-warning`), so the API/WS just work.

> Alternative (needs a domain you own): a **named Cloudflare tunnel** gives
> `api.yourdomain.com` with no warning header. The free `trycloudflare.com`
> QUICK tunnel is NOT suitable here — its URL changes every restart.

## 2. Deploy on Vercel

- **Root Directory:** `frontend`
- Framework/build/output are read from `frontend/vercel.json` (Vite → `dist`).
- **Environment Variable:** `VITE_API_URL = https://hdfc-demo.ngrok-free.app`
  (your static domain, no trailing slash). Then **Redeploy** so the value is
  baked into the build. You only ever do this ONCE — the URL never changes.

CORS already allows all origins (`main.py`), so the backend accepts the Vercel
domain with no change.

## 3. Local dev is unchanged

Leave `VITE_API_URL` unset locally. `npm run dev` keeps proxying `/api` and `/ws`
to `127.0.0.1:8003` via `vite.config.js`.

## How it works in code

- `src/config.js` — single source of truth: `API_BASE` / `WS_BASE` from `VITE_API_URL`.
- `src/auth.js` — the global `fetch` interceptor re-points every relative `/api`
  and `/ws` path onto `API_BASE` in prod (no-op in dev), and attaches the auth
  token for absolute backend URLs too.
- WebSocket components default to the page origin (dev) / `VITE_API_URL` (prod),
  never to `http://localhost` (which is mixed-content-blocked).

## Caveats

- Your laptop must stay awake with the backend **and** tunnel running, or the
  live site breaks.
- `VITE_*` vars are public (shipped in the bundle). Don't ship real secrets;
  rotate/restrict any third-party keys or proxy them through the backend.
