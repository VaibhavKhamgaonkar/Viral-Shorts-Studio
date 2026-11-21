# Viral Shorts Studio

A local video clipping workspace for YouTube links and video files. Select a precise range or let Ollama rank transcript highlights, then export vertical MP4s with captions, an opening hook, and copy for your posts.

## Start here on Windows

1. Clone the repository or download and extract its ZIP. Open the folder that contains `start_web.bat`.
2. Install Python 3.10 or newer and enable **Add Python to PATH** in the Windows installer. Use an FFmpeg build with libass for captions. Install it on PATH or place `ffmpeg.exe` in the project’s `bin` folder. The launcher attempts to install FFmpeg via WinGet if it cannot find it.
3. Double-click [`start_web.bat`](start_web.bat) and keep its terminal open. It creates a Python environment if needed, installs dependencies, and starts the app at [http://localhost:8000](http://localhost:8000). Refresh the browser if it opens before the server is ready.

The web interface uses HTML, CSS, and JavaScript served by the Python app. A regular browser runs the interface without Node.js, npm, or a frontend build.

### Set up Ollama and Qwen

**Windows shortcut.** After installing Python, double-click [`setup_ollama.bat`](setup_ollama.bat). It starts Ollama if needed and attempts an installation via WinGet if Ollama is missing. Choose **1** for `qwen2.5:7b` or **2** for the smaller `qwen2.5:3b`. Wait for the download and the confirmation that the model was saved. Then run `start_web.bat` for everyday use. [Ollama model sizes](https://ollama.com/library/qwen2.5)

**Manual setup.** Install and open Ollama, then run the following commands in a new PowerShell window. The last command should produce a short model reply. [Ollama for Windows](https://docs.ollama.com/windows) · [Ollama CLI](https://docs.ollama.com/cli)

```powershell
ollama pull qwen2.5:7b
ollama list
ollama run qwen2.5:7b "Reply with OK"
```

Run `start_web.bat`, open **Settings → Clip defaults**, enter `qwen2.5:7b` in **Ollama model**, and click **Save settings**. If you pulled the 3B model, enter `qwen2.5:3b` instead. Keep Ollama running and look for **Local AI ready** in the sidebar. The default connection is `http://localhost:11434/v1`. The studio creates `config/settings.json` when you save preferences. [Ollama’s local API](https://docs.ollama.com/api/openai-compatibility)

| What you see | What to do |
| --- | --- |
| `ollama` is unrecognized | Open a new PowerShell after installing Ollama from the Windows installer. |
| **Ollama offline** | Open Ollama from the Start menu, or run `ollama serve` in a separate terminal. Refresh the studio. |
| **Model missing** | Run `ollama list` and match the full model tag in Settings, including `:7b` or `:3b`. Pull the model if it is missing. |

The studio can still trim and export with Ollama offline. Automatic selection falls back to timeline picks. AI posting kits require usable speech or captions and a response from the configured model. Whisper handles sources without usable captions and may download its speech model on first use.

### Start from PowerShell

Run these commands from the project folder to launch the same app directly.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

## Create and review clips

1. Paste a YouTube link or upload an MP4, MOV, MKV, WebM, or M4V.
2. Choose automatic highlights or enter exact start and end times in seconds or `hh:mm:ss`. Automatic presets target **20–60 seconds**, **1–3 minutes**, or **3–10 minutes**, subject to the available footage. Exact ranges can run from **1 second to 10 minutes** per clip.
3. Choose blurred background, center crop, or a gameplay split screen. Put a video loop you can use in `assets/gameplay/` for split screen. The renderer uses a blurred background when that folder has no supported clips.
4. Use **Advanced clip options** to set the clip count, choose 1080 × 1920 or 720 × 1280, adjust speed, override source audio, or toggle captions and mirroring. You can choose a warm or black-and-white look, fade the picture and sound at both ends, and write your own opening text. These choices apply to the current job and are rendered into the exported MP4.
5. Click **Queue clips**. The form confirms the task ID, and you can submit more jobs right away. Open **Tasks** in the sidebar or click **View task** to see queue positions, current progress, remaining stages, and recent results. Percentages appear when a step reports measurable progress.
6. A notification appears in the bottom-right when a job finishes. Choose **View clips** to open the batch in **Clip library**, or **View details** if a job stops. Each batch stores vertical MP4s, ASS subtitle files, and JSON metadata under `output_clips/`. The **AI posting kit** contains model-generated titles, hooks, descriptions, hashtags, editing and posting suggestions, and optional sound cues when the model provides them.

For YouTube videos with usable captions, the studio scans the transcript in overlapping windows and asks yt-dlp for selected ranges. If a range request fails it can use a full download. Highlights depend on spoken content and the available transcript. Use an exact range for a moment you already know.

One job runs at a time to share local resources. Waiting jobs and recent status survive a studio restart. Resubmit a job that was interrupted while running. The queue database lives under the local `data/` folder.

## Studio settings

- **Brand and captions** set the on-video channel label, caption style, accent color, and three-second opening hook for new exports.
- **Clip defaults** set the Ollama model, default crop and length, clip count, and source-copy cleanup preference. The **Download cache** menu provides manual cleanup.
- **Audio and music** can keep or mute the source audio, replace it with a selected local track, or apply a track Qwen recommends. Upload tracks through Settings or place usable audio files under `assets/music/`. If Qwen finds no match, AI replacement preserves the source audio. Replacement removes the entire original soundtrack, including speech. Qwen 2.5 writes music briefs and sound-effect prompts as text. Generating the audio file requires an audio model such as [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small).

## Publish your clips

**Publishing destinations** saves YouTube channel, Instagram profile, and Facebook Page details on this machine. **Future default** records which accounts you want preselected when direct uploads are available. Account connection and automatic uploads are unavailable in this version.

For now, open **Clip library**, download the **MP4**, and use the **AI posting kit** to copy the title or caption. Then upload through [YouTube Studio](https://studio.youtube.com/), [Instagram](https://www.instagram.com/), or [Meta Business Suite](https://business.facebook.com/). Direct uploads will require the studio to implement YouTube authorization and the relevant Meta publishing permissions. [YouTube upload API](https://developers.google.com/youtube/data/guides/uploading_a_video) · [Instagram publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing) · [Facebook Page Reels](https://developers.facebook.com/documentation/video-api/guides/reels-publishing)

### Footage and music rights

Use footage, gameplay, and music you own or have permission to edit and republish on each destination. Keep a copy of the applicable license or permission. Cropping, filters, fades, and soundtrack changes edit the presentation. They cannot guarantee that a clip will avoid a Content ID claim or copyright strike. [YouTube copyright guidance](https://support.google.com/youtube/answer/2797466?hl=en)

Review the **Checks** step in YouTube Studio before publishing. It can flag issues before the video goes public, and claims can still appear later. [YouTube upload checks](https://support.google.com/youtube/answer/57407?hl=en#checks)

## Other entry points

```powershell
.\venv\Scripts\python.exe cli.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --mode smart_blur --model qwen2.5:7b
```

The CLI also supports `--help`. Browser files live in `web/`, the FastAPI entry point is `app.py`, and the FFmpeg, Whisper, Ollama, and metadata pipeline lives in `core/`.

Run the highlight and publishing-copy regression tests with `python -m unittest discover -s tests -v`.
