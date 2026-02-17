@echo off
REM ============================================================
REM Single script to build, start, and serve everything
REM ============================================================
REM Usage:
REM   start.bat           - Start everything (build + agent + API)
REM   start.bat --ngrok   - Also start ngrok tunnel for global URL
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   LTFS Survey Agent - Full Stack Launcher
echo ============================================

REM Check .env
if not exist .env (
    echo ERROR: .env file not found.
    exit /b 1
)

REM ---- Step 1: Build Frontend ----
echo.
echo [1/4] Building React frontend...
cd frontend
if not exist node_modules (
    echo Installing frontend dependencies...
    call npm install --silent
)
call npm run build
if errorlevel 1 (
    echo ERROR: Frontend build failed.
    cd ..
    exit /b 1
)
echo Frontend built successfully.
cd ..

REM ---- Step 2: Activate venv ----
echo.
echo [2/4] Activating Python virtual environment...
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo No venv found, using system Python.
)

REM ---- Step 3: Start LiveKit Agent ----
echo.
echo [3/4] Starting LiveKit Agent...
start "LiveKit Agent" /min cmd /c "cd agent && python web_rtc_server.py dev"
timeout /t 3 /nobreak >nul

REM ---- Step 4: Start API Server ----
echo.
echo [4/4] Starting API Server on port 8000...
start "API Server" /min cmd /c "python api/main.py"
timeout /t 2 /nobreak >nul

echo.
echo ============================================
echo   All services running!
echo ============================================
echo   Local URL:  http://localhost:8000
echo.

REM ---- Optional: Start ngrok ----
if "%1"=="--ngrok" (
    where ngrok >nul 2>nul
    if !errorlevel! equ 0 (
        echo Starting ngrok tunnel...
        start "ngrok" /min cmd /c "ngrok http 8000"
        timeout /t 4 /nobreak >nul
        echo Check ngrok URL at: http://localhost:4040
        echo Update SmartFlo dashboard WebSocket URL to the ngrok wss:// URL
    ) else (
        echo ngrok not found. Install: choco install ngrok
    )
)

echo Press any key to stop all services...
pause >nul

REM Cleanup
taskkill /fi "WINDOWTITLE eq LiveKit Agent" /f >nul 2>nul
taskkill /fi "WINDOWTITLE eq API Server" /f >nul 2>nul
taskkill /fi "WINDOWTITLE eq ngrok" /f >nul 2>nul
echo All services stopped.
