# Viral Shorts Studio

A local video clipping workspace for YouTube links and video files. Select a precise range or let Ollama rank transcript highlights, then export vertical MP4s with captions, an opening hook, and copy for your posts.

## Start here on Windows

1. Clone the repository or download and extract its ZIP. Open the folder that contains `start_web.bat`.
2. Install Python 3.10 or newer and enable **Add Python to PATH** in the Windows installer. Use an FFmpeg build with libass for captions. Install it on PATH or place `ffmpeg.exe` in the project’s `bin` folder. The launcher attempts to install FFmpeg via WinGet if it cannot find it.
3. Double-click [`start_web.bat`](start_web.bat) and keep its terminal open. It creates a Python environment if needed, installs dependencies, and opens the app at [http://127.0.0.1:8000](http://127.0.0.1:8000) when it is ready. Opening the launcher again reuses a running studio. After pulling an update, stop the existing studio after its jobs finish, then launch it again.

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

In **AI posting kit → Hashtags**, enter a short search topic such as `GTA V police car` and click **Find hashtags**. The studio sends the topic to YouTube and checks a sample of short public videos for hashtags in their titles and search descriptions. It ranks matches by how often they appear in that sample, using public video views to break ties. The kit saves the linked hashtags and includes them when you copy the post. Unlinked tags are local AI suggestions. No sign-in or API key is required. Each tag's source link shows where it appeared. This lookup covers YouTube, and its sample cannot predict how your post will perform. [YouTube hashtag guidance](https://support.google.com/youtube/answer/6390658?hl=en)

Use **Regenerate draft** to rewrite an existing kit without re-rendering the MP4. The local model uses saved YouTube hashtags as suggestions alongside the clip's existing speech captions and your notes. Add a short note about the key visual moment or payoff when captions miss it. Your notes and transcript stay out of the YouTube search.

Use the search above the exports on **Create clips** or **Clip library** to find a task ID, clip title, caption, hashtag, or saved spoken phrase. **Filters** narrows results by source, clip selection method, or export date and time in your browser’s local time zone. Sort by export time or source title. The page controls show 3 batches by default and offer 6 and 12 when there are more results. When a keyword matches individual clips, the results show those clips under their batch.

Batches with more than five clips use a horizontal strip. Swipe, use the scrollbar, or select the arrow buttons below the clips to see the rest.

### Edit an exported clip

Click the pencil on a clip card. The editor shows the saved MP4 on the left and your edit on the right. The timeline below both previews shows source sections, cuts, captions, picture effects, inserts, and sound cues as you add them. **Back to clips** returns to the library.

- **Cut** trims either end or removes a middle section. Scrub to each boundary and choose **Split at playhead**, select the section on the **Source** track, then choose **Remove section**. You can also enter the cut times directly. Removed sections close the gap in the edit. Use **Restore section** to bring one back, or select **Short dip to black** to soften the joins.
- **Picture** sets the whole clip’s color, zoom, mirror, and animated frame. **Effects by time range** applies separate settings to up to eight spans. Choose **Select this frame** for a single frame. For a punch-in, click **Pick point on original**, choose the subject, adjust the zoom up to 250%, and select **Apply range effect**.
- **Text** lets you generate speech captions with Whisper or add a line manually. Select a timeline caption or choose it from the list to edit its text and timing. Choose **Clean** or **Bold with keyword highlight**. **Animated titles** adds a separate title at the playhead with a pop, slide, or fade. Whisper may download its speech model on first use.
- **Audio** controls source and music levels, sound effects, filters, timed replacements, and local narration. See [Audio editing](#audio-editing).
- **Inserts** includes six built-in animations and accepts MP4 or WebM cutaways from 0.2 to 15 seconds and under 40 MB. A cutaway pauses the original and extends the edit. Imported inserts can keep their audio.

Use **Play rendered edit** to check the final result alongside the original. Choose a save mode next to **Export MP4** in the editor header. **Save as new version** exports a numbered copy such as `clip-01-v2.mp4`. **Replace existing file** asks for confirmation before replacing the saved MP4. Follow the export in **Tasks**. The editor opens the saved MP4, including any captions that were already burned in.

### Audio editing

1. Open a clip and choose **Audio**. Under **Background music**, pick a library track or click **Import track**. Selecting a track reveals its volume, fades, and automatic volume reduction.
2. To replace sound for part of a clip, open **Voice-over and timed audio**. Enter the start and end in seconds from the start of the trim, or mark them with the playhead. Set **Original in this range** to 0% for silence or another percentage to lower it. The original returns to its regular level after the range.
3. Use **Import audio** for a recording, or open **Generate voice-over**, type a script, and choose a voice and speaking pace. Generation uses installed Windows desktop voices and creates a reusable audio file locally. Click **Fit range to audio** to match its length to the range. Audition the result and click **Apply audio to range**. A shorter range trims the new audio at its end.
4. Under **Original volume and filters → Audio filters**, enable steady-noise reduction, rumble reduction, hiss reduction, or a 50/60 Hz hum filter. Use **Play rendered edit** to hear these filters together with the music and timed audio.

**Mute individual tracks.** Run [setup_audio_separation.bat](setup_audio_separation.bat) once, then restart the studio. Select **Edit clip → Audio → Scan audio**. The editor header shows a scan timer while you work in other tabs. The scan uses the entire trim by default. Under **Scan settings**, set a shorter range for trims over two minutes or enter the number of speakers if you know it. Use **Listen** to identify a speaker or sound, rename the row if helpful, click **Mute**, and choose **Apply track changes**. Click **Keep** and apply again to restore a track.

The scan combines vocal isolation, speaker timing, and dominant sound recognition locally. It groups detected speech by voice, so a machine announcement can appear as a speaker if the model recognizes it as speech. The **Overlapping voices** track holds sections where speakers talk together. Sound tracks are time-based estimates, and simultaneous sounds can share a track. Use short selections and listen before removing anything. [Audio Separator](https://github.com/nomadkaraoke/python-audio-separator) · [Sherpa speaker diarization](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html) · [YAMNet sound classifier](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet)

For YouTube videos with usable captions, the studio scans the transcript in overlapping windows and asks yt-dlp for selected ranges. If a range request fails it can use a full download. Highlights depend on spoken content and the available transcript. Use an exact range for a moment you already know.

One job runs at a time to share local resources. The form blocks a repeat submission while an identical job is waiting or running. Change the clip options or source to queue a different job. The Tasks drawer shows queue wait, processing time, and individual stage timings. Jobs created before timing was added have no historical durations. If the studio is interrupted, a failed task's recorded time ends at its last recorded update.

Waiting jobs and recent status survive a studio restart. Resubmit a job that was interrupted while running. The queue database lives under the local `data/` folder.

The studio uses NVIDIA H.264 encoding when available and falls back to CPU encoding. The blurred background is processed at a lower resolution, and the foreground uses the selected export size. The rendering step identifies the encoding path in the Tasks drawer.

## Studio settings

- **Brand and captions** set the on-video channel label, caption style, accent color, and three-second opening hook for new exports.
- **Clip defaults** set the Ollama model, default crop and length, clip count, and source-copy cleanup preference. The **Download cache** menu provides manual cleanup.
- **Audio and music** can keep or mute the source audio, replace it with a selected local track, or apply a track Qwen recommends. Upload tracks through Settings or place usable audio files under `assets/music/`. If Qwen finds no match, AI replacement preserves the source audio. Replacement removes the entire original soundtrack, including speech. Qwen 2.5 writes music briefs and sound-effect prompts as text. Generating the audio file requires an audio model such as [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small).

## Publish your clips

Use **Settings → Publishing destinations** to connect an account. **Select by default** saves immediately and controls which connected accounts start checked when you open Share.

### Upload a YouTube Short manually

1. In **Clip library**, find the clip and select **Show in folder**. Windows File Explorer selects the MP4 named on that clip card. Use **MP4** on the card if you want a downloaded copy.
2. Sign in to the correct channel in [YouTube Studio](https://studio.youtube.com/), choose **Create → Upload videos**, and select that MP4. You can drag the selected file from File Explorer into the upload window. YouTube accepts square or vertical Shorts up to three minutes. [Upload Shorts from a computer](https://support.google.com/youtube/answer/12779649?hl=en)
3. Review and copy the title and description from **AI posting kit** into their separate fields. Set the audience, review **Checks**, and select **Private** if you want to review the result before making it public. [YouTube upload settings](https://support.google.com/youtube/answer/57407?hl=en)

Use the same exported MP4 for manual uploads through [Instagram](https://www.instagram.com/) or [Meta Business Suite](https://business.facebook.com/). [Instagram publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing) · [Facebook Page Reels](https://developers.facebook.com/documentation/video-api/guides/reels-publishing)

### Direct upload from a local studio

Open [Publishing destinations in your local studio](http://127.0.0.1:8000/settings#publishing). Each account has its own setup. You can start with YouTube and add the others later.

#### Connect YouTube for your own channel

1. Sign in with the Google account that manages your YouTube channel. Open [Google’s personal setup](https://console.cloud.google.com/auth/overview/personal), choose or create a project, select **Desktop app**, and continue. In the confirmation, click **Download JSON** before closing it. This is the sign-in file you’ll use in the studio.
2. Open [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com). Select the **same project** in the top bar and click **Enable**. If the page shows **Manage**, the API is already enabled.
3. Back in the studio, open **Set up YouTube**, select the downloaded file under **Sign-in file from Google**, and click **Continue with Google**. Grant both requested permissions. When you return to Settings, check that the card shows the right channel name.

**Using an existing External project?** In [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients), choose **Create client → Desktop app** and download the new JSON file. If the project is in Testing, first go to [Audience](https://console.cloud.google.com/auth/audience), select **Add users** under **Test users**, and add the account that manages the channel. Testing tokens can expire after seven days. Google Personal mode works for the single Google account that created the client. [Google setup and Personal mode](https://support.google.com/cloud/answer/15544987) · [Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)

#### Connect Facebook and Instagram

##### Check your Meta app permissions

In **Meta My Apps → your app → Use cases → Manage everything on your Page → Customize → Permissions and features**, check the rows for `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`. When all three show **Ready for testing** with an **Actions** menu, your app is set up for the Facebook token step. An administrator can test with the app **In development**. **Ready for testing** applies to the app. The next steps grant the permissions to a new token. If a required row shows **+ Add**, click it first. [Meta app setup](https://developers.facebook.com/documentation/development/create-an-app) · [Meta Explorer guide](https://developers.facebook.com/docs/graph-api/guides/explorer/)

##### Get your access token in Graph API Explorer

1. Open or reload [Graph API Explorer](https://developers.facebook.com/tools/explorer/). In the right-hand panel under **Meta App**, select the exact app whose permissions you just checked.
2. Under **User or Page**, click the dropdown that initially says **Get Token**. Choose **Get User Access Token**.
3. Select the **Permissions** tab. Open **Add a Permission** and add each entry needed from the table below.
4. Click **Generate Access Token** in the same panel. Continue as the Facebook account that manages your Page. If Meta shows a list of Pages, allow access to your Page and finish the prompt.
5. Verify the fresh token before copying it. In the query field at the top, replace `me?fields=id,name` with `me/permissions`, leave **GET** selected, and click **Submit**. For Facebook, confirm all three `pages_…` permissions from the table show `"status": "granted"`. For Instagram, check its additional permissions too. Then click the copy icon beside the **Access Token** field at the top of the right-hand panel. [Meta permission reference](https://developers.facebook.com/docs/graph-api/reference/permission/)
6. Back in [the studio’s Facebook and Instagram setup](http://127.0.0.1:8000/settings#meta-setup), paste it into **User Access Token from Meta**. Click **Find my Pages**, choose your Page, then click **Connect this Page**. The studio uses the User Access Token to find your Pages and obtains the selected Page’s token during connection.

| What you want to connect | Permissions to select in Graph API Explorer |
| --- | --- |
| Connect either platform | `pages_show_list`, `pages_read_engagement` |
| Publish Facebook Reels | Add `pages_manage_posts` |
| Publish Instagram Reels | Add `instagram_basic` and `instagram_content_publish` |

##### If you’re missing a control

- **Explorer shows only `business_management` and `pages_show_list`, even though the dashboard says Ready for testing.** Reload Explorer and reselect the exact app, then choose **Get User Access Token** and reopen **Add a Permission**. Check that you are signed into the Facebook account listed as an administrator or tester for that app. If the two permissions still do not appear, check the app’s Page use case and any **Required actions** in Meta. [Meta’s Explorer guide](https://developers.facebook.com/docs/graph-api/guides/explorer/)
- **Your app isn’t listed under Meta App.** Open [Meta My Apps](https://developers.facebook.com/apps/) and sign in with the account that has a role on the app. If you haven’t created one, choose **Create App** and look under **Content management** for **Manage everything on your Page**.
- **Generate Access Token is greyed out.** Open **Get Token** and choose the user-token option first. Check that the intended app is selected. If the dropdown offers no user-token option, check the app’s use cases and your app role in Meta My Apps.
- **A publishing permission is missing.** Check the selected app’s **Use cases** in its dashboard. For this studio’s Instagram connection, configure **Instagram API with Facebook Login**. It uses `instagram_basic` and `instagram_content_publish`. Reopen Explorer, select your app, and generate a new token after changing permissions.
- **Find my Pages shows an empty list.** Generate a fresh User Access Token and grant access to the Page in the Facebook prompt. The signed-in Facebook account must be able to publish to the Page. For Instagram to appear, link a professional Instagram account to that Page.
- **Your Page appears, but Connect this Page reports missing permissions.** Meta granted enough access to list the Page. Click the information icon beside **Access Token** in Explorer to check what was actually granted. Add the permissions listed by the studio, generate a new User Access Token, and paste the new token into the studio.

##### Add Instagram after connecting Facebook

1. In your Meta app, open **Use cases → Manage messaging & content on Instagram → Customize → API setup with Facebook login**. In **Add required permissions**, click **Add required content permissions** under **Manage content on Instagram**. [Meta’s Instagram publishing permissions](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
2. On Facebook, switch into the Page using **See all profiles**. Open **Settings & privacy → Settings → Linked accounts → Instagram → Connect account** and sign in to your professional Instagram account. [Meta’s Page linking guide](https://www.facebook.com/business/help/connect-instagram-to-page)
3. In Explorer, generate a fresh User Access Token with the three Facebook permissions plus `instagram_basic` and `instagram_content_publish`. Use **GET** `me/permissions` to verify the granted names. In the studio, use **Manage Meta connection → Find my Pages → Connect this Page** for the same Page. The Instagram card shows which permissions are missing or whether Meta returned a linked account.

Keep the token private. [Meta token selection](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-pages) · [Instagram publishing permissions](https://developers.facebook.com/documentation/instagram-platform/content-publishing/)

#### Upload a clip

1. Use **Share clip** on an export or **Share clips** for a batch. The studio starts with the saved posting kit, then refreshes the title, description, and hashtags. Clips with a saved YouTube search topic refresh that research too. Review the fresh draft, make edits, or click **Use current copy** while generation runs.
2. For YouTube, select visibility and audience. Visibility starts at **Private**. Instagram follows the account’s visibility. Facebook posts are public. Clips outside the Reels API’s 3–90 second range use the Page Video API and keep their original duration. Set the AI media disclosure where applicable and confirm you have permission to publish. [Facebook Reels API](https://developers.facebook.com/documentation/video-api/guides/reels-publishing) · [Facebook Page Video API](https://developers.facebook.com/docs/graph-api/reference/page/videos)
3. Click **Upload selected**. Each clip and destination gets a task. **View upload** opens the platform result when its URL is available. For a stopped task, share the same export again with the same settings to resume supported sessions. Confirmed uploads for the same export and account are skipped.

Credentials and upload records are saved locally under `data/publishing/`, which Git ignores. Keep the OAuth JSON and Meta tokens private. **Disconnect** removes the local connection. Revoke app access on the platform itself to invalidate a grant there.

### Footage and music rights

Use footage, gameplay, and music you own or have permission to edit and republish on each destination. Keep a copy of the applicable license or permission. Cropping, filters, fades, and soundtrack changes edit the presentation. They cannot guarantee that a clip will avoid a Content ID claim or copyright strike. [YouTube copyright guidance](https://support.google.com/youtube/answer/2797466?hl=en)

Review the **Checks** step in YouTube Studio before publishing. It can flag issues before the video goes public, and claims can still appear later. [YouTube upload checks](https://support.google.com/youtube/answer/57407?hl=en#checks)

## Other entry points

```powershell
.\venv\Scripts\python.exe cli.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --mode smart_blur --model qwen2.5:7b
```

The CLI also supports `--help`. Browser files live in `web/`, the FastAPI entry point is `app.py`, and the FFmpeg, Whisper, Ollama, and metadata pipeline lives in `core/`.

Run the highlight and publishing-copy regression tests with `python -m unittest discover -s tests -v`.
