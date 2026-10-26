# start-tunnels.ps1
# Opens Cloudflare Quick Tunnels for both backend (8003) and frontend (5173).
# Run this AFTER your backend and frontend dev servers are already running.

Write-Host ""
Write-Host "=== HDFC Cloudflare Tunnels ===" -ForegroundColor Cyan
Write-Host "Starting backend tunnel (port 8003)..." -ForegroundColor Yellow
Write-Host "Starting frontend tunnel (port 5173)..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Each tunnel URL will appear below once Cloudflare assigns it." -ForegroundColor Green
Write-Host "Share the FRONTEND url with the other laptop." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop both tunnels." -ForegroundColor Red
Write-Host ""

# Pre-flight: confirm cloudflared is installed
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  Write-Host "ERROR: cloudflared not found on PATH." -ForegroundColor Red
  Write-Host "Install: winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
  exit 1
}

# Pre-flight: confirm backend (8003) and frontend (5173) are listening
$backendUp  = Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction SilentlyContinue
$frontendUp = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
if (-not $backendUp)  { Write-Host "WARN: nothing listening on 8003 (backend). Start it first." -ForegroundColor Yellow }
if (-not $frontendUp) { Write-Host "WARN: nothing listening on 5173 (frontend). Start it first (npm run dev -- --host 127.0.0.1)." -ForegroundColor Yellow }

# Use 127.0.0.1 explicitly — 'localhost' on Windows can resolve to IPv6 (::1)
# while the dev server only binds IPv4, causing cloudflared to fail with
# "connection refused" or "dial tcp [::1]:PORT".

# Start backend tunnel in a new window
Start-Process powershell -ArgumentList "-NoExit", "-Command", `
  "Write-Host 'BACKEND TUNNEL (8003)' -ForegroundColor Cyan; cloudflared tunnel --url http://127.0.0.1:8003"

# Small delay so output is readable
Start-Sleep -Seconds 2

# Start frontend tunnel in a new window
Start-Process powershell -ArgumentList "-NoExit", "-Command", `
  "Write-Host 'FRONTEND TUNNEL (5173)' -ForegroundColor Green; cloudflared tunnel --url http://127.0.0.1:5173"

Write-Host "Two new terminal windows opened." -ForegroundColor White
Write-Host ""
Write-Host "IMPORTANT — Two-step after tunnels start:" -ForegroundColor Yellow
Write-Host "  1. Copy the BACKEND tunnel URL (looks like https://xxxx.trycloudflare.com)" -ForegroundColor White
Write-Host "  2. Set env var: `$env:VITE_API_URL = 'https://xxxx.trycloudflare.com'" -ForegroundColor White
Write-Host "  3. Restart the frontend dev server (npm run dev)" -ForegroundColor White
Write-Host "  4. Share the FRONTEND tunnel URL with the other laptop" -ForegroundColor White
Write-Host ""
