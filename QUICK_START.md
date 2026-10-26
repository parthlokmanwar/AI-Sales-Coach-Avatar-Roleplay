# Quick Start — HDFC RAG

> **Critical:** start the frontend with `--host 127.0.0.1`. Without it Vite binds IPv6-only (`[::1]:5173`) and cloudflared can't reach it.

## 1. Ollama
```powershell
ollama serve
```

## 2. Backend (FastAPI on 8003)
```powershell
cd "c:\AI\hdfc ai\llm-rag-hdfc"
.\.venv\Scripts\Activate.ps1
python -m uvicorn main:app --host 127.0.0.1 --port 8003 --reload
```

## 3. Frontend (Vite on 5173) — IPv4
```powershell
cd "c:\AI\hdfc ai\llm-rag-hdfc\frontend\frontend"
npm run dev -- --host 127.0.0.1
```

Local app: **http://localhost:5173**
imp imp

---cloudflared tunnel --url http://127.0.0.1:5173


## 4. Cloudflare Tunnels (to share with another laptop)

### Step A — start the backend tunnel
```powershell
cloudflared tunnel --url http://127.0.0.1:8003
```
Copy the `https://<words>.trycloudflare.com` URL it prints. This is the **backend** URL (API only — do **not** share this).

### Step B — restart frontend pointing at the backend tunnel
In the frontend window: **Ctrl+C**, then:
```powershell
$env:VITE_API_URL = 'https://<backend>.trycloudflare.com'
npm run dev -- --host 127.0.0.1
```

### Step C — start the frontend tunnel (in a new window)
```powershell
cloudflared tunnel --url http://127.0.0.1:5173
```
**This URL is what you share** with the other laptop.

### Or run both tunnels at once
```powershell
cd "c:\AI\hdfc ai\llm-rag-hdfc"
.\start-tunnels.ps1
```
Opens two windows with both tunnel URLs. Still do Step B between them.

---

## Troubleshooting cloudflared
- **`cloudflared: command not found`** → `winget install --id Cloudflare.cloudflared`, reopen PowerShell.
- **`failed to dial 127.0.0.1:8003`** → backend not running. Start Window 2 first.
- **`failed to dial 127.0.0.1:5173`** → Vite is IPv6-only. Restart with `--host 127.0.0.1`.
- **Tunnel loads but app errors** → forgot Step B (`VITE_API_URL` + restart Vite).
- **Tunnel URL changes every restart** → expected. Quick tunnels are ephemeral. Repeat Step B with the new URL.

---

## Stop everything
```powershell
foreach ($port in 8003, 5173, 11434) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
}
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
```
 https://oct-webpage-drive-neo.trycloudflare.com


  https://oct-webpage-drive-neo.trycloudflare.com

  https://online-announcement-chem-institute.trycloudflare.com/





  Get your shareable link (2 commands)
1. Restart the frontend so it picks up the new config (Window 3):
Ctrl+C, then:


npm run dev
2. Open a frontend tunnel (a 5th window) — this gives the link you share:


cloudflared tunnel --url http://127.0.0.1:5173
It prints a URL like https://something-words.trycloudflare.com — that is your share link. 👈 Send it to anyone; they open it and get the Call Assistant UI.

How the other person uses it
On the shared page, in the call setup row:

Customer number → the number to call
Agent phone (optional) → their own phone (so it rings them, not you) — this field overrides your default
Start Call






Terminal 1 — Backend


cd "C:\AI\hdfc ai\llm-rag-hdfc"
uvicorn main:app --host 0.0.0.0 --port 8003 --reload
Terminal 2 — Frontend


cd "C:\AI\hdfc ai\llm-rag-hdfc\frontend\frontend"
npm run dev -- --host
Terminal 3 — Tunnel (run LAST, after T1 and T2 are ready)


cloudflared tunnel --url http://127.0.0.1:5173
The shareable link will appear in Terminal 3 output — look for the line:



| https://xxxx-xxxx-xxxx.trycloudflare.com |







he 3 commands running right now:

Terminal 1 — Backend


cd "C:\AI\hdfc ai\llm-rag-hdfc"
uvicorn main:app --host 0.0.0.0 --port 8003 --reload
Terminal 2 — Frontend


cd "C:\AI\hdfc ai\llm-rag-hdfc\frontend\frontend"
npm run dev -- --host
Terminal 3 — Tunnel


cloudflared tunnel --url http://127.0.0.1:5173








cd C:\ai\hdfc; .\start-backend.ps1 -Domain heritage-destruct-gestation.ngrok-free.dev
