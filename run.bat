@echo off
title Viral Shorts Factory CLI
cd /d "%~dp0"

set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"

echo =================================================================
echo             YOUTUBE VIRAL SHORTS AUTOMATED FACTORY
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
python -c "import yt_dlp" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing required packages: yt-dlp faster-whisper openai
    pip install yt-dlp faster-whisper openai
)

:: 4. Launch Ollama in background if installed
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
goto RUN_CLI

:OLLAMA_MISSING
echo [INFO] Ollama is not installed yet.
echo        The clipper will use the built-in smart viral detector.
echo        To install Ollama and Qwen 2.5, run setup_ollama.bat anytime.
echo.

:RUN_CLI
python run_auto.py

if errorlevel 1 (
    echo.
    echo [ERROR] Process stopped with an error.
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