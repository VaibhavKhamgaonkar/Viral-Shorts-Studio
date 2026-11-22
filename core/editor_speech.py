import base64
import json
import os
import shutil
import subprocess


SPEECH_SCRIPT = """
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$request = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($request.output) {
        if ($request.voice) { $speech.SelectVoice($request.voice) }
        $speech.Rate = $request.rate
        $speech.SetOutputToWaveFile($request.output)
        $speech.Speak([string]$request.text)
    } else {
        $voices = @($speech.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {
            @{ name = $_.VoiceInfo.Name; language = $_.VoiceInfo.Culture.Name }
        })
        ConvertTo-Json -InputObject $voices -Compress
    }
} finally {
    $speech.Dispose()
}
"""


def windows_speech(request):
    powershell = shutil.which("powershell.exe") if os.name == "nt" else None
    if not powershell:
        raise RuntimeError("Text-to-speech requires a Windows host with an installed desktop voice. You can import a recording.")
    command = base64.b64encode(SPEECH_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
            input=json.dumps(request).encode("ascii"), capture_output=True, check=True, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Windows could not synthesize speech. Check that a desktop voice is installed or import a recording.") from error
    return result.stdout


def installed_voices():
    return json.loads(windows_speech({}))


def synthesize_speech(output, text, voice, rate):
    windows_speech({"output": str(output.resolve()), "text": text, "voice": voice, "rate": rate})
