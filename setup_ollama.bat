@echo off
setlocal
title Ollama and Qwen Setup
cd /d "%~dp0"

set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
where ollama >nul 2>&1
if not errorlevel 1 goto CHECK_SERVICE

echo Installing Ollama with Windows Package Manager...
winget install --id Ollama.Ollama --exact --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto INSTALL_FAILED
where ollama >nul 2>&1
if errorlevel 1 goto INSTALL_FAILED

:CHECK_SERVICE
ollama list >nul 2>&1
if not errorlevel 1 goto SELECT_MODEL
echo Starting the local Ollama service...
start "" /b ollama serve >nul 2>&1
for /L %%i in (1,1,12) do (
    ollama list >nul 2>&1
    if not errorlevel 1 goto SELECT_MODEL
    timeout /t 1 /nobreak >nul
)
echo Ollama did not respond. Open Ollama from the Start menu and rerun this script.
pause
exit /b 1

:SELECT_MODEL
echo.
echo   1. Qwen 2.5 7B - studio default
echo   2. Qwen 2.5 3B - smaller model
echo   3. DeepSeek R1 7B
choice /c 123 /n /m "Choose a model [1, 2, or 3]: "
set "MODEL_CHOICE=%ERRORLEVEL%"
set "TARGET_MODEL="
if "%MODEL_CHOICE%"=="1" set "TARGET_MODEL=qwen2.5:7b"
if "%MODEL_CHOICE%"=="2" set "TARGET_MODEL=qwen2.5:3b"
if "%MODEL_CHOICE%"=="3" set "TARGET_MODEL=deepseek-r1:7b"
if not defined TARGET_MODEL exit /b 1

echo.
echo Downloading %TARGET_MODEL%...
ollama pull %TARGET_MODEL%
if errorlevel 1 (
    echo Could not download %TARGET_MODEL%. Check disk space and your connection, then retry.
    pause
    exit /b 1
)

set "STUDIO_PYTHON=python"
if exist "venv\Scripts\python.exe" set "STUDIO_PYTHON=venv\Scripts\python.exe"
"%STUDIO_PYTHON%" -c "import sys; from config.settings import load_settings, save_settings; settings = load_settings(); settings['llm_model'] = sys.argv[1]; save_settings(settings)" "%TARGET_MODEL%"
if errorlevel 1 (
    echo %TARGET_MODEL% was downloaded, but the studio could not save the model setting.
    echo Start the studio and enter %TARGET_MODEL% in Settings under Clip defaults.
    pause
    exit /b 1
)

echo %TARGET_MODEL% is installed and saved for this studio.
echo Launch the app with start_web.bat and check Local AI ready in the sidebar.
pause
exit /b 0

:INSTALL_FAILED
echo Ollama could not be installed or found. Install it from https://ollama.com/download/windows
echo Open a new terminal after installation, then rerun setup_ollama.bat.
pause
exit /b 1
