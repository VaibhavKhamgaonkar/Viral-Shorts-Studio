@echo off
title Ollama and Qwen 2.5 Setup
cd /d "%~dp0"

:: Add Ollama default directory to PATH for this session
set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"

echo =================================================================
echo             OLLAMA AND BEST AI MODEL CONFIGURATOR
echo =================================================================
echo  Recommended Model: Qwen 2.5 (7B) - Best Open-Weights for Clips
echo =================================================================
echo.

:: 1. Activate venv if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

:: Locate ollama executable
set "OLLAMA_EXE=ollama"
where ollama >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    ) else (
        goto INSTALL_OLLAMA
    )
)
echo [OK] Ollama found: %OLLAMA_EXE%
goto CHECK_SERVICE

:INSTALL_OLLAMA
echo [!] Ollama command not found in PATH.
echo [*] Installing Ollama automatically via Windows Package Manager (winget)...
winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo.
    echo [!] Winget auto-install could not complete.
    echo Please download and run the installer from: https://ollama.com/download
    pause
    exit /b 1
)
set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
echo [+] Ollama installed successfully!

:CHECK_SERVICE
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I /N "ollama.exe" >nul
if errorlevel 1 (
    echo [*] Starting Ollama background server...
    start /b "" "%OLLAMA_EXE%" serve >nul 2>&1
    timeout /t 3 >nul
) else (
    echo [OK] Ollama local service is running.
)

echo.
echo =================================================================
echo Select the model you want to download:
echo   1. Qwen 2.5 7B (RECOMMENDED: Best hooks and strict JSON) [~4.5GB]
echo   2. Qwen 2.5 3B (Fastest: Ultra-low RAM, 3x faster on CPU) [~2.0GB]
echo   3. DeepSeek R1 7B (Deep reasoning distillation) [~4.7GB]
echo =================================================================
set /p model_choice="Enter choice [1, 2, or 3] (default is 1): "

set TARGET_MODEL=qwen2.5:7b
if "%model_choice%"=="2" set TARGET_MODEL=qwen2.5:3b
if "%model_choice%"=="3" set TARGET_MODEL=deepseek-r1:7b

echo.
echo [*] Pulling %TARGET_MODEL% via Ollama (this will take a few minutes)...
echo [*] Running: "%OLLAMA_EXE%" pull %TARGET_MODEL%
"%OLLAMA_EXE%" pull %TARGET_MODEL%

echo.
echo [*] Updating configuration to use %TARGET_MODEL%...
python -c "import json; p='config/settings.json'; data=json.load(open(p)); data['llm_model']='%TARGET_MODEL%'; json.dump(data, open(p,'w'), indent=4); print('[OK] Config updated!')"

echo.
echo =================================================================
echo   ALL SET! %TARGET_MODEL% IS READY FOR VIRAL CLIPPING!
echo =================================================================
echo You can now double-click 'start_web.bat' to launch the studio.
echo.
pause