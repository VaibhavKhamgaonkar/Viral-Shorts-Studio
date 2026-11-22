@echo off
title Viral Shorts Studio
cd /d "%~dp0"

:: Add project bin, Ollama, and WinGet links to PATH for this session
set "PATH=%~dp0bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%LOCALAPPDATA%\Programs\Ollama;%PATH%"

echo =================================================================
echo             YOUTUBE VIRAL SHORTS WEB STUDIO
echo =================================================================
echo.

:: 1. Verify Python
where python >nul 2>&1
if errorlevel 1 goto NO_PYTHON

:: 2. Setup Virtual Environment if missing
if exist "venv\Scripts\activate.bat" goto ACTIVATE_VENV
echo [*] Setting up isolated Python virtual environment...
python -m venv --system-site-packages venv
if errorlevel 1 goto VENV_FAIL
echo [+] Virtual environment created.

:ACTIVATE_VENV
call venv\Scripts\activate.bat

:: 3. Check & Install Required Packages
python -c "import fastapi, uvicorn, yt_dlp, faster_whisper, requests, pydantic, python_multipart" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing required packages...
    python -m pip install -r requirements.txt
    if errorlevel 1 exit /b 1
)

:: 4. Check FFmpeg video engine
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [*] Checking for FFmpeg rendering engine...
    winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements --silent >nul 2>&1
)

:: 5. Launch Ollama in background if installed
set "OLLAMA_EXE=ollama"
where ollama >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    ) else (
        goto OLLAMA_MISSING
    )
)

tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I /N "ollama.exe" >nul
if errorlevel 1 (
    echo [*] Starting Ollama local engine in background...
    start /b "" "%OLLAMA_EXE%" serve >nul 2>&1
    timeout /t 2 >nul
) else (
    echo [OK] Ollama local service is active.
)
goto START_STUDIO

:OLLAMA_MISSING
echo [INFO] Ollama is not installed yet.
echo        The studio will use timeline selection and transcript-based copy.
echo        To install Ollama and Qwen 2.5, run setup_ollama.bat anytime.
echo.

:START_STUDIO
echo =================================================================
echo [*] Opening Studio Web Dashboard at: http://127.0.0.1:8000
echo =================================================================
echo [NOTE] Keep this window open while using the studio.
echo [NOTE] A running studio is reused. After an update, let its jobs finish before restarting it.
echo.

python app.py --open-browser

if errorlevel 1 (
    echo.
    echo [ERROR] Application stopped with an error.
    pause
)
goto :EOF

:NO_PYTHON
echo [ERROR] Python is not installed or not in system PATH!
echo Please install Python 3.10+ from https://python.org
echo.
pause
exit /b 1

:VENV_FAIL
echo [ERROR] Failed to create virtual environment!
echo.
pause
exit /b 1
