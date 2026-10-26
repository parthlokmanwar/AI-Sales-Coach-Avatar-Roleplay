@echo off
echo Starting HDFC AI Project...

echo [1/2] Starting Backend on port 8003...
start "HDFC Backend" powershell -NoExit -ExecutionPolicy Bypass -File "C:\AI\hdfc ai\llm-rag-hdfc\start_backend.ps1"

echo [2/2] Starting Frontend on port 5173...
start "HDFC Frontend" powershell -NoExit -ExecutionPolicy Bypass -File "C:\AI\hdfc ai\llm-rag-hdfc\start_frontend.ps1"

echo.
echo Project starting up:
echo   Backend  -^> http://localhost:8003
echo   Frontend -^> http://localhost:5173  (open this in browser)
echo.
echo Backend takes ~45s to load. Wait for both windows to show "ready" before opening the browser.
pause
