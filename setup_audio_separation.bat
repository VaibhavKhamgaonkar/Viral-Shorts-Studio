@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Run start_web.bat once to set up the studio, then run this file again.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m pip install "audio-separator[cpu]==0.44.2" "librosa==0.11.0" "ml-dtypes==0.5.4" "sherpa-onnx==1.13.8"
if errorlevel 1 (
    echo Audio separation setup failed. See the installation output above.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m core.audio_breakdown --setup "data\audio-models\track-detection"
if errorlevel 1 (
    echo The audio models could not be downloaded. Check your internet connection and run setup again.
    pause
    exit /b 1
)
echo Setup complete. Restart the studio. The first separation may download the voice isolation model.
pause
