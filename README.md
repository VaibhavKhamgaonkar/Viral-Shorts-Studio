# Viral Shorts Studio

A local video clipping workspace for YouTube links and video files. Select a precise range or let Ollama rank transcript highlights, then export vertical MP4s with captions, an opening hook, and copy for your posts.

## Run locally

Install Python 3.10 or newer and an FFmpeg build with the libass subtitle filter, then install the Python dependencies. On Windows, FFmpeg can be on PATH or at `bin/ffmpeg.exe`.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

Open [Viral Shorts Studio](http://127.0.0.1:8000). The app listens on loopback by default and stores exports on this machine. For a Windows launcher, use `start_web.bat`.

### Enable local AI

Install [Ollama](https://ollama.com/download) and pull the default model.

```powershell
ollama pull qwen2.5:7b
```

Keep Ollama running while the studio is open. The sidebar reports whether the model configured in **Settings** is available. Ollama uses its [OpenAI-compatible local API](https://docs.ollama.com/api/openai-compatibility). Whisper runs locally for sources without usable captions and may download its model on first use.

Without Ollama, automatic selection uses available timeline ranges. When speech is available, posting copy can fall back to transcript excerpts. Library cards identify timeline picks and the posting copy panel identifies its source.

## Create and review clips

1. Paste a YouTube link or upload an MP4, MOV, MKV, WebM, or M4V.
2. Choose automatic highlights or enter exact start and end times in seconds or `hh:mm:ss`. Automatic presets target **20–60 seconds**, **1–3 minutes**, or **3–10 minutes**, subject to the available footage. Exact ranges can run from **1 second to 10 minutes** per clip.
3. Choose blurred background, center crop, or a gameplay split screen. Put a video loop you can use in `assets/gameplay/` for split screen. The renderer uses a blurred background when that folder has no supported clips.
4. Generate and review the exports in **Clip library**. Each batch stores 1080 × 1920 MP4s, ASS subtitle files and JSON metadata under `output_clips/`. Open **Posting copy** for title options, opening text, a description, hashtags, a pinned comment, and a music brief when available.

For YouTube videos with usable captions, the studio scans the transcript in overlapping windows and asks yt-dlp for selected ranges. If a range request fails it can use a full download. Highlights depend on spoken content and the available transcript. Use an exact range for a moment you already know.

## Studio settings

- **Brand and captions** set the on-video channel label, caption style, accent color, and three-second opening hook for new exports.
- **Clip defaults** set the Ollama model, default crop and length, clip count, and source-copy cleanup preference. The **Download cache** menu provides manual cleanup.
- **Audio and music** can keep or mute the source audio, replace it with a selected local track, or apply a track Qwen recommends. Upload tracks through Settings or place usable audio files under `assets/music/`. If Qwen finds no match, AI replacement preserves the source audio. Replacement removes the entire original soundtrack, including speech. Music briefs describe mood, tempo, and instruments for sourcing or using a separate music generator.
- **Publishing destinations** saves channel, profile, and Page details for YouTube, Instagram, and Facebook. Account authorization and one-click uploads are planned. Audio synthesis, isolated music removal, and audio mixing are outside the web studio's current feature set.

Preferences are saved in `config/settings.json` when you use **Save settings**. Use footage, gameplay, and music you own or have permission to republish.

## Other entry points

```powershell
.\venv\Scripts\python.exe cli.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --mode smart_blur --model qwen2.5:7b
```

The CLI also supports `--help`. Browser files live in `web/`, the FastAPI entry point is `app.py`, and the FFmpeg, Whisper, Ollama, and metadata pipeline lives in `core/`.

Run the highlight and publishing-copy regression tests with `python -m unittest discover -s tests -v`.
