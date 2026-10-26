# start-backend.ps1
# ----------------------------------------------------------------------------
# ONE command to run everything the Vercel-hosted frontend needs from your laptop:
#   1. the backend  (FastAPI + Whisper STT, port 8003)
#   2. a STABLE ngrok tunnel that exposes it at a permanent https URL
#
# The frontend itself lives on Vercel - this script only powers the backend that
# Vercel's frontend calls. The ngrok URL never changes, so you set VITE_API_URL
# on Vercel exactly ONCE.
#
# -- First-time setup (do once, ever) ----------------------------------------
#   winget install ngrok.ngrok
#   ngrok update                              # winget ships an OLD build; need >= 3.20
#   ngrok config add-authtoken <token>        # from https://dashboard.ngrok.com
#   # then dashboard -> Domains -> Create Domain  (free, e.g. your-name.ngrok-free.dev)
#   setx NGROK_DOMAIN "your-name.ngrok-free.dev"   # persists across reboots
#
# -- Every run (the one command) ---------------------------------------------
#   .\start-backend.ps1
# or pass the domain inline:
#   .\start-backend.ps1 -Domain your-name.ngrok-free.dev
# ----------------------------------------------------------------------------

param(
  [string]$Domain = $env:NGROK_DOMAIN,
  [int]$Port = 8003
)

$Root = $PSScriptRoot

# Make sure ngrok (added to PATH by winget) is visible even in a shell that was
# open before it was installed.
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

# Python 3.10 is the interpreter that has faster-whisper (the live transcript /
# RolePlay checklist break without it). Fall back to PATH python if it moved.
$Py = "C:\Users\ADMIN\AppData\Local\Programs\Python\Python310\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host ""
Write-Host "=== HDFC backend + stable ngrok tunnel (for Vercel frontend) ===" -ForegroundColor Cyan

# -- Pre-flight: ngrok installed? --------------------------------------------
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
  Write-Host "ERROR: ngrok not found on PATH." -ForegroundColor Red
  Write-Host "  winget install ngrok.ngrok ; ngrok update" -ForegroundColor Yellow
  Write-Host "  ngrok config add-authtoken <token>   (from https://dashboard.ngrok.com)" -ForegroundColor Yellow
  exit 1
}

# -- Pre-flight: do we have a stable domain? ---------------------------------
if ([string]::IsNullOrWhiteSpace($Domain)) {
  Write-Host "ERROR: no ngrok domain set." -ForegroundColor Red
  Write-Host "  1) Create a free static domain: https://dashboard.ngrok.com -> Domains -> Create" -ForegroundColor Yellow
  Write-Host "  2) Then either:" -ForegroundColor Yellow
  Write-Host "       setx NGROK_DOMAIN your-name.ngrok-free.dev   (set once, reopen window)" -ForegroundColor Yellow
  Write-Host "       OR  .\start-backend.ps1 -Domain your-name.ngrok-free.dev" -ForegroundColor Yellow
  exit 1
}
$Domain = $Domain -replace '^https?://', ''   # accept either full URL or bare host

# -- Free any stale backend already holding the port -------------------------
$old = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
       Select-Object -First 1 -ExpandProperty OwningProcess
if ($old) {
  Write-Host "Stopping stale process on port $Port (PID $old)..." -ForegroundColor DarkYellow
  Stop-Process -Id $old -Force -ErrorAction SilentlyContinue
}

# -- 1) Backend in its own window (so you can watch the logs) ----------------
Write-Host "Starting backend (Python 3.10) on port $Port ..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root'; & '$Py' main.py"

# -- 2) Wait until it is actually listening -----------------------------------
Write-Host "Waiting for backend to come up..." -ForegroundColor Yellow
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Seconds 2
  if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
}
if ($ready) { Write-Host "Backend is up on port $Port." -ForegroundColor Green }
else { Write-Host "WARN: backend not listening yet - starting the tunnel anyway." -ForegroundColor DarkYellow }

# -- 3) Stable tunnel in THIS window (Ctrl+C stops the tunnel) ----------------
Write-Host ""
Write-Host "Public backend URL (set as VITE_API_URL on Vercel - ONCE, never changes):" -ForegroundColor Cyan
Write-Host "    https://$Domain" -ForegroundColor Green
Write-Host ""
Write-Host "Leave this window open while the demo is live. Ctrl+C stops the tunnel." -ForegroundColor DarkGray
Write-Host ""

ngrok http $Port --url="https://$Domain"
