# Faceless Video CLI & Tooling Reference

This reference covers the commands, environment configuration, and options for running automated faceless video generation tools via CLI and Agent hooks.

---

## 1. Faceless CLI (`faceless-cli`)

Faceless.so provides a CLI for cloud-based script-to-video rendering, asset synthesis, and multi-platform publishing.

### Prerequisites
- Node.js installed (`node -v` $\ge$ 18.0)
- `FACELESS_API_KEY` environment variable set (obtain from [https://faceless.so/developers](https://faceless.so/developers))

```bash
# Set API Key in PowerShell
$env:FACELESS_API_KEY="your_api_key_here"
```

### Core Commands

| Action | Command | Description |
| :--- | :--- | :--- |
| **Verify Auth** | `npx -y faceless-cli whoami` | Checks connected account identity and remaining rendering credits |
| **Check Credits** | `npx -y faceless-cli credits` | Displays available credits for video creation |
| **Create Video** | `npx -y faceless-cli videos create --script "<script>" --voice "<voice_id>" --style "<style>"` | Submits a script to generate video assets and voiceover |
| **Check Status** | `npx -y faceless-cli videos status <video_id>` | Checks whether rendering is `processing`, `completed`, or `failed` |
| **Download Video**| `npx -y faceless-cli videos download <video_id> --output video.mp4` | Downloads the finished 1080x1920 MP4 video locally |
| **Publish Short** | `npx -y faceless-cli posts publish --video-id <video_id> --platform youtube --title "<title>"` | Uploads and schedules directly to YouTube Shorts |

### Common Styles & Voices
- **Styles**: `realistic`, `cinematic`, `paper_cutout`, `cyberpunk`, `minimalist_dark`
- **Voices**: `adam` (deep male), `george` (warm British), `rachel` (clear authoritative female), `charlotte` (documentary narrative)

---

## 2. Programmatic Rendering with Remotion (Local Engine)

For running local rendering on your own machine without third-party cloud API costs:

### Rendering Command
```bash
# Render 9:16 vertical MP4 via Remotion CLI
npx remotion render src/index.ts ShortExplainer out/short.mp4 --props='{"title":"Why GPS Doesn'\''t Track You"}' --gl=angle
```

### Key Render Settings
- `--width=1080 --height=1920`
- `--fps=30`
- `--codec=h264` (universal mobile compatibility)
- `--crf=18` (high visual fidelity)

---

## 3. Audio & Subtitle Generation Pipeline (Local Alternative)

```python
# Script structure for generating synchronized TTS with Edge-TTS / Whisper
# edge-tts --text "Hook text..." --write-media voice.mp3 --write-subtitles subs.vtt
```
- Duck background music: `-filter_complex "[1:a]volume=0.1[bg];[0:a][bg]amix=inputs=2:duration=first"`
- Burn styled subtitles with FFmpeg:
```bash
ffmpeg -i video.mp4 -vf "subtitles=subs.srt:force_style='FontName=Montserrat,FontSize=24,Bold=1,PrimaryColour=&H0000FFFF&,Outline=2'" output_subbed.mp4
```
