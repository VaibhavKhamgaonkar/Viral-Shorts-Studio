@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Run start_web.bat once to set up the studio, then run this file again.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m pip install "audio-separator[cpu]==0.44.2" "librosa==0.11.0" "ml-dtypes==0.5.4"
if errorlevel 1 (
    echo Audio separation setup failed. See the installation output above.
    pause
    exit /b 1
)
echo Setup complete. Restart the studio. The first separation downloads the model.
pause
