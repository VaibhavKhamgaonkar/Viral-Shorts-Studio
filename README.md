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

The studio can still trim and export with Ollama offline. Automatic selection falls back to timeline picks. AI posting kits need usable speech, captions, or creator notes and a response from the configured model. Whisper handles sources without usable captions and may download its speech model on first use.

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
6. A notification appears in the bottom-right when a job finishes. Choose **View clips** to open the batch in **Clip library**, or **View details** if a job stops. Each batch stores vertical MP4s, ASS subtitle files, and JSON metadata under `output_clips/`. In the **AI posting kit**, select a title, review the caption and its viewer question, and copy the post with its topical hashtags. The kit also includes a separate pinned comment and any audience, editing, and audio suggestions from the model.

Use **Regenerate draft** to refresh an existing kit without re-rendering the MP4. Add a note about the clip's key visual moment when captions are sparse or the model missed something. Older exports can use their saved speech captions or your notes. Hashtags are local AI suggestions based on the clip. [YouTube hashtag guidance](https://support.google.com/youtube/answer/6390658?hl=en)

Use the search above the exports on **Create clips** or **Clip library** to find a task ID, clip title, caption, hashtag, or saved spoken phrase. **Filters** narrows results by source, clip selection method, or export date and time in your browser’s local time zone. Sort by export time or source title. The page controls show 3 batches by default and offer 6 and 12 when there are more results. When a keyword matches individual clips, the results show those clips under their batch.

Batches with more than five clips use a horizontal strip. Swipe, use the scrollbar, or select the arrow buttons below the clips to see the rest.

### Edit an exported clip

1. Click the pencil icon on the clip card to open the full-window editor. **Original** on the left shows the saved clip. **Edited** on the right shows your changes. Both use the same playhead. **Back to clips** returns to the library.
2. Set separate volume levels for the original audio and a looping background track. Pick a track from your library or add audio you have permission to use. Set **Original** to 0% to mute the original soundtrack.
3. Audition a built-in effect, then use **Add at playhead** to place it. Up to 12 cues can be timed and leveled independently. Cue times are measured from the start of the trim. The editor also shows the posting kit’s sound suggestion when one is saved.
4. Choose **Save as new version** to export a numbered copy such as `clip-01-v2.mp4` alongside the original. Choose **Replace existing file** and confirm if you want the edited MP4 to take its place. Follow the export in **Tasks**. Edits start from the rendered MP4, including its burned-in captions.

- **Animated text** places a title on the edited preview with a pop, slide, or fade. Edit its start and end times, position, and color. Select its timeline marker to jump back to it.
- **Trim & picture** sets the whole clip’s default color, zoom, mirror, and animated frame. Use **Effects by time range** to override those settings for up to eight non-overlapping spans. Move the playhead and set range boundaries, or use the frame buttons and **Select this frame** to target a single frame at the saved clip’s frame rate.
- **Reaction inserts** includes six built-in animations. You can import an MP4 or WebM from 0.2 to 15 seconds and under 40 MB. Each cutaway pauses the original and extends the edited clip. Built-in reactions are silent. Imported inserts can keep their audio, and the music track plays across each join.
- **Speech captions → Generate captions** uses `faster-whisper` to transcribe the saved clip and jumps to the first generated line inside the trim. Captions appear on the right during their spoken intervals. Select a caption from the menu or timeline to jump to it. This is useful for exports without burned-in captions. Whisper may download the configured model on first use.
- **Play rendered edit** produces a temporary preview with the final picture, captions, titles, cutaways, and audio mix. Watch it alongside the original before exporting.

For YouTube videos with usable captions, the studio scans the transcript in overlapping windows and asks yt-dlp for selected ranges. If a range request fails it can use a full download. Highlights depend on spoken content and the available transcript. Use an exact range for a moment you already know.

One job runs at a time to share local resources. The form blocks a repeat submission while an identical job is waiting or running. Change the clip options or source to queue a different job. The Tasks drawer shows queue wait, processing time, and individual stage timings. Jobs created before timing was added have no historical durations. If the studio is interrupted, a failed task's recorded time ends at its last recorded update.

Waiting jobs and recent status survive a studio restart. Resubmit a job that was interrupted while running. The queue database lives under the local `data/` folder.

The studio uses NVIDIA H.264 encoding when available and falls back to CPU encoding. The blurred background is processed at a lower resolution, and the foreground uses the selected export size. The rendering step identifies the encoding path in the Tasks drawer.

## Studio settings

- **Brand and captions** set the on-video channel label, caption style, accent color, and three-second opening hook for new exports.
- **Clip defaults** set the Ollama model, default crop and length, clip count, and source-copy cleanup preference. The **Download cache** menu provides manual cleanup.
- **Audio and music** can keep or mute the source audio, replace it with a selected local track, or apply a track Qwen recommends. Upload tracks through Settings or place usable audio files under `assets/music/`. If Qwen finds no match, AI replacement preserves the source audio. Replacement removes the entire original soundtrack, including speech. Qwen 2.5 writes music briefs and sound-effect prompts as text. Generating the audio file requires an audio model such as [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small).

## Publish your clips

**Publishing destinations** saves YouTube channel, Instagram profile, and Facebook Page details on this machine. **Future default** records which accounts you want preselected when direct uploads are available. Account connection and automatic uploads are unavailable in this version.

### Upload a YouTube Short manually

1. In **Clip library**, find the clip and select **Show in folder**. Windows File Explorer selects the MP4 named on that clip card. Use **MP4** on the card if you want a downloaded copy.
2. Sign in to the correct channel in [YouTube Studio](https://studio.youtube.com/), choose **Create → Upload videos**, and select that MP4. You can drag the selected file from File Explorer into the upload window. YouTube accepts square or vertical Shorts up to three minutes. [Upload Shorts from a computer](https://support.google.com/youtube/answer/12779649?hl=en)
3. Review and copy the title and description from **AI posting kit** into their separate fields. Set the audience, review **Checks**, and select **Private** if you want to review the result before making it public. [YouTube upload settings](https://support.google.com/youtube/answer/57407?hl=en)

Use the same exported MP4 for manual uploads through [Instagram](https://www.instagram.com/) or [Meta Business Suite](https://business.facebook.com/). [Instagram publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing) · [Facebook Page Reels](https://developers.facebook.com/documentation/video-api/guides/reels-publishing)

### YouTube direct-upload pilot

The first integration can upload one selected export as **Private**, then open it in YouTube Studio for review. It requires a Google Cloud project with **YouTube Data API v3** enabled, a **Desktop app** OAuth client, and browser consent from the channel owner. If the OAuth app is in testing, add the owner's Google account as a test user. Store the credentials and tokens locally. [Google desktop authorization](https://developers.google.com/identity/protocols/oauth2/native-app)

The app would show the authorized channel, let you review the MP4, title, description, and audience setting, and upload via `videos.insert` with privacy explicitly set to `private`. Use a resumable upload and save the returned video ID so you can open the same upload in Studio. Confirm the channel, playback, and visibility there before publishing. [Upload endpoint](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert) · [Resumable uploads](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol)

### Footage and music rights

Use footage, gameplay, and music you own or have permission to edit and republish on each destination. Keep a copy of the applicable license or permission. Cropping, filters, fades, and soundtrack changes edit the presentation. They cannot guarantee that a clip will avoid a Content ID claim or copyright strike. [YouTube copyright guidance](https://support.google.com/youtube/answer/2797466?hl=en)

Review the **Checks** step in YouTube Studio before publishing. It can flag issues before the video goes public, and claims can still appear later. [YouTube upload checks](https://support.google.com/youtube/answer/57407?hl=en#checks)

## Other entry points

```powershell
.\venv\Scripts\python.exe cli.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --mode smart_blur --model qwen2.5:7b
```

The CLI also supports `--help`. Browser files live in `web/`, the FastAPI entry point is `app.py`, and the FFmpeg, Whisper, Ollama, and metadata pipeline lives in `core/`.

Run the highlight and publishing-copy regression tests with `python -m unittest discover -s tests -v`.
