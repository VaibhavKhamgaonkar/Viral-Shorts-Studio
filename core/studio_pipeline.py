import subprocess
from pathlib import Path

from config.studio import CLIP_LENGTHS
from core.analyzer import analyze_transcript_for_viral_clips, generate_post_copy
from core.downloader import download_video, download_video_section, fetch_subtitles_only, sanitize_filename
from core.publisher import generate_clip_metadata, save_task_info
from core.subtitles import generate_ass_subtitles
from core.transcriber import get_transcript, parse_vtt_subtitles
from core.transformer import (
    AUDIO_EXTENSIONS, MUSIC_DIR, OUTPUT_DIR, asset_files, find_companion_audio, get_ffmpeg_cmd, inspect_media, render_viral_clip,
)


def transcribe_range(source, audio, offset, start, end, scratch_audio, settings, *, progress=None):
    _, has_audio = inspect_media(source)
    if not (has_audio or audio):
        return []
    try:
        subprocess.run([
            get_ffmpeg_cmd(), "-v", "error", "-y", "-ss", str(offset), "-t", str(end - start),
            "-i", str(source if has_audio else audio), "-vn", "-ac", "1", "-ar", "16000", str(scratch_audio),
        ], check=True, capture_output=True)
        segments = get_transcript(
            str(scratch_audio), model_size=settings["whisper_model_size"], device=settings["whisper_device"],
            progress=progress,
        )
        return [segment | {"start": segment["start"] + start, "end": segment["end"] + start} for segment in segments]
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Could not transcribe chosen range: {error}")
        return []
    finally:
        scratch_audio.unlink(missing_ok=True)


def run_job(job_id, request, settings, update, cleanup):
    source_url = request.url if request.source_type == "url" else ""
    transcript = []
    source = None
    audio = None

    def full_download(phase="source"):
        stage = "Downloading source"
        detail = "Fetching the full video. Transfer progress is for the current stream."
        update(phase, stage, detail)
        result = download_video(source_url, progress=lambda percent: update(phase, stage, detail, percent))
        if not result["video_path"]:
            raise RuntimeError("The downloaded source does not contain a video file.")
        return Path(result["video_path"]), result.get("audio_path")

    if source_url:
        update("source", "Reading YouTube source", "Checking duration and looking for captions.")
        captions = fetch_subtitles_only(source_url)
        info = captions["info"]
        source_title = info["title"]
        update("source", "Source details ready", "Checking available captions and media.", source=source_title)
        duration = info.get("duration") or 0
        if captions["subtitle_path"]:
            transcript = parse_vtt_subtitles(captions["subtitle_path"])
        if not duration or (not transcript and request.selection == "highlights"):
            source, audio = full_download()
    else:
        source = Path(request.file_path)
        source_title = source.stem.rsplit("_", 1)[0]
        info = {"uploader": "Local upload"}
        duration = 0
        update("source", "Inspecting local video", "Checking the video and audio streams.", source=source_title)

    if source:
        duration, has_audio = inspect_media(source)
        audio = audio or (find_companion_audio(source) if not has_audio else None)
        if request.selection == "highlights" and not transcript and (has_audio or audio):
            stage = "Transcribing speech"
            detail = "Whisper is reading the audio locally."
            update("transcript", stage, detail)
            transcript = get_transcript(
                str(source), audio_path=audio, model_size=settings["whisper_model_size"],
                device=settings["whisper_device"],
                progress=lambda percent: update("transcript", stage, detail, percent),
            )
    if duration <= 0:
        raise ValueError("The video duration could not be determined.")

    if request.selection == "range":
        update("transcript", "Checking speech for this range",
               "Using source captions if available. Otherwise Whisper reads the selected range during export.")
        update("selection", "Checking exact range", "Validating your start and end times.")
        if request.start_time >= duration or request.end_time > duration + 0.05:
            raise ValueError(f"The requested range exceeds the source duration ({duration:.1f}s).")
        clips = [{"start_time": request.start_time, "end_time": min(request.end_time, duration), "selection": "manual"}]
    else:
        if transcript:
            update("transcript", "Transcript ready", f"{len(transcript)} caption or speech segments available.")
        else:
            update("transcript", "No usable speech found", "The studio will use timeline picks for this source.")
        clip_min, clip_max = CLIP_LENGTHS[request.clip_length]
        update("selection", "Finding highlights", f"Reviewing a {duration / 60:.1f}-minute source.")
        clips = analyze_transcript_for_viral_clips(
            transcript, duration, settings["llm_base_url"], settings["llm_api_key"], settings["llm_model"],
            min_dur=clip_min, max_dur=clip_max, max_clips=settings["max_clips_to_generate"],
            progress=lambda window, start, end: update(
                "selection", "Finding highlights", f"Scanning transcript window {window} ({start / 60:.1f}–{end / 60:.1f} min).",
            ),
        )
    if not clips:
        raise ValueError("No usable clip ranges were found.")

    task_dir = OUTPUT_DIR / f"{sanitize_filename(source_title)}_[{job_id}]"
    music = {path.name: path for path in asset_files(MUSIC_DIR, AUDIO_EXTENSIONS)}
    source_files = []
    if source:
        source_files.extend([str(source), audio])

    save_task_info(task_dir, job_id, source_url, source_title, info.get("uploader", "Unknown"), duration, filter(None, source_files))
    for index, clip in enumerate(clips, 1):
        start, end = clip["start_time"], clip["end_time"]
        stem = f"clip-{index:02}"
        slice_path = task_dir / f"source-{index:02}.mp4"
        try:
            input_path, offset, input_audio = source, start, audio
            if not input_path:
                stage = "Fetching clip range"
                detail = f"Downloading selected range {index} of {len(clips)}."
                update("export", stage, detail)
                if download_video_section(
                    source_url, start, end, str(slice_path),
                    progress=lambda percent: update("export", stage, detail, percent),
                ):
                    input_path, offset, input_audio = slice_path, 0, None
                else:
                    source, audio = full_download("export")
                    source_files.extend([str(source), audio])
                    save_task_info(task_dir, job_id, source_url, source_title, info.get("uploader", "Unknown"),
                                   duration, filter(None, source_files))
                    input_path, offset, input_audio = source, start, audio

            excerpt = transcript
            if request.selection == "range" and not any(s["end"] > start and s["start"] < end for s in transcript):
                stage = "Transcribing selected range"
                detail = "Whisper is reading only the selected section."
                update("export", stage, detail)
                excerpt = transcribe_range(
                    input_path, input_audio, offset, start, end, task_dir / f"{stem}.wav", settings,
                    progress=lambda percent: update("export", stage, detail, percent),
                )
            update("export", "Generating AI posting kit", f"Checking speech and drafting copy for clip {index} of {len(clips)}.")
            copy = generate_post_copy(clip, excerpt, source_title, settings, tuple(music))
            if copy["copy_source"] != "ai":
                reason = ("No usable transcript for this clip." if copy["copy_source"] == "no_transcript"
                          else "The configured model did not return usable posting copy.")
                update("export", "AI posting kit unavailable", f"Clip {index} of {len(clips)}. {reason}")
            audio_mode = settings["music_mode"]
            track = None
            if audio_mode == "replace":
                track = music.get(settings["music_track"])
                if not track:
                    raise ValueError("The selected replacement track is missing from the music library.")
            elif audio_mode == "ai_replace":
                track = music.get(copy["music_track"])
                audio_mode = "replace" if track else "original"

            opening_text = " ".join(request.custom_opening_text.split()) or copy["hook"]
            if not settings["opening_hook"]:
                opening_text = ""
            ass_path = task_dir / f"{stem}.ass"
            generate_ass_subtitles(
                excerpt, start, end, ass_path, captions=settings["captions"], caption_style=settings["caption_style"],
                caption_color=settings["caption_color"], brand_name=settings["brand_name"],
                opening_hook=opening_text,
            )
            width = request.output_resolution
            stage = "Rendering clip"
            detail = f"Encoding clip {index} of {len(clips)} as a {width} × {width * 16 // 9} MP4."
            update("export", stage, detail)
            output = render_viral_clip(
                input_path, offset, offset + end - start, f"{stem}.mp4", output_folder=task_dir,
                subtitles_ass_path=ass_path, mode=request.mode, enable_hflip=request.mirror,
                audio_tempo=request.playback_speed or (settings["audio_tempo"] if request.audio_speed else 1),
                color_contrast=settings["color_contrast"] if request.color_grade else 1,
                color_saturation=settings["color_saturation"] if request.color_grade else 1,
                color_look=request.color_look, edge_fades=request.edge_fades,
                audio_mode=audio_mode, music_path=track, source_audio_path=input_audio, output_width=width,
                progress=lambda percent: update("export", stage, detail, percent),
            )
            metadata = clip | copy | {
                "hook": opening_text or copy["hook"],
                "ai_hook": copy["hook"] if copy["copy_source"] == "ai" else "",
                "applied_music": track.name if track else "",
            }
            generate_clip_metadata(metadata, source_title, output, source_url)
        finally:
            slice_path.unlink(missing_ok=True)

    update("finalize", "Saving batch", "Saving the clip batch and posting kits to the library.")
    if request.auto_delete_source:
        cleanup(source_files)
    return len(clips)
