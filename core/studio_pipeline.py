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


def transcribe_range(source, audio, offset, start, end, scratch_audio, settings):
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
        )
        return [segment | {"start": segment["start"] + start, "end": segment["end"] + start} for segment in segments]
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Could not transcribe chosen range: {error}")
        return []
    finally:
        scratch_audio.unlink(missing_ok=True)


def run_job(job_id, request, settings, update):
    source_url = request.url if request.source_type == "url" else ""
    transcript = []
    source = None
    audio = None

    def full_download():
        update("Downloading source", "The full video is needed to process this source.")
        result = download_video(source_url)
        if not result["video_path"]:
            raise RuntimeError("The downloaded source does not contain a video file.")
        return Path(result["video_path"]), result.get("audio_path")

    if source_url:
        update("Reading YouTube source", "Checking duration and looking for captions.")
        captions = fetch_subtitles_only(source_url)
        info = captions["info"]
        source_title = info["title"]
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

    if source:
        duration, has_audio = inspect_media(source)
        audio = audio or (find_companion_audio(source) if not has_audio else None)
        if request.selection == "highlights" and not transcript and (has_audio or audio):
            update("Transcribing speech", "Whisper is reading the audio locally.")
            transcript = get_transcript(
                str(source), audio_path=audio, model_size=settings["whisper_model_size"],
                device=settings["whisper_device"],
            )
    if duration <= 0:
        raise ValueError("The video duration could not be determined.")

    if request.selection == "range":
        if request.start_time >= duration or request.end_time > duration + 0.05:
            raise ValueError(f"The requested range exceeds the source duration ({duration:.1f}s).")
        clips = [{"start_time": request.start_time, "end_time": min(request.end_time, duration), "selection": "manual"}]
    else:
        clip_min, clip_max = CLIP_LENGTHS[request.clip_length]
        update("Finding highlights", f"Reviewing a {duration / 60:.1f}-minute source.")
        clips = analyze_transcript_for_viral_clips(
            transcript, duration, settings["llm_base_url"], settings["llm_api_key"], settings["llm_model"],
            min_dur=clip_min, max_dur=clip_max, max_clips=settings["max_clips_to_generate"],
            progress=lambda window, start, end: update(
                "Finding highlights", f"Scanning transcript window {window} ({start / 60:.1f}–{end / 60:.1f} min).",
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
                update("Fetching clip range", f"Downloading selected range {index} of {len(clips)}.")
                if download_video_section(source_url, start, end, str(slice_path)):
                    input_path, offset, input_audio = slice_path, 0, None
                else:
                    source, audio = full_download()
                    source_files.extend([str(source), audio])
                    save_task_info(task_dir, job_id, source_url, source_title, info.get("uploader", "Unknown"),
                                   duration, filter(None, source_files))
                    input_path, offset, input_audio = source, start, audio

            excerpt = transcript
            if request.selection == "range" and not any(s["end"] > start and s["start"] < end for s in transcript):
                update("Transcribing selected range", "Whisper is reading only the selected section.")
                excerpt = transcribe_range(input_path, input_audio, offset, start, end, task_dir / f"{stem}.wav", settings)
            update("Drafting post copy", f"Writing titles, opening text and a music brief for clip {index} of {len(clips)}.")
            copy = generate_post_copy(clip, excerpt, source_title, settings, tuple(music))
            audio_mode = settings["music_mode"]
            track = None
            if audio_mode == "replace":
                track = music.get(settings["music_track"])
                if not track:
                    raise ValueError("The selected replacement track is missing from the music library.")
            elif audio_mode == "ai_replace":
                track = music.get(copy["music_track"])
                audio_mode = "replace" if track else "original"

            ass_path = task_dir / f"{stem}.ass"
            generate_ass_subtitles(
                excerpt, start, end, ass_path, captions=settings["captions"], caption_style=settings["caption_style"],
                caption_color=settings["caption_color"], brand_name=settings["brand_name"],
                opening_hook=copy["hook"] if settings["opening_hook"] else "",
            )
            update("Rendering clip", f"Exporting clip {index} of {len(clips)} as a 1080 × 1920 MP4.")
            output = render_viral_clip(
                input_path, offset, offset + end - start, f"{stem}.mp4", output_folder=task_dir,
                subtitles_ass_path=ass_path, mode=request.mode, enable_hflip=request.mirror,
                audio_tempo=settings["audio_tempo"] if request.audio_speed else 1,
                color_contrast=settings["color_contrast"] if request.color_grade else 1,
                color_saturation=settings["color_saturation"] if request.color_grade else 1,
                audio_mode=audio_mode, music_path=track, source_audio_path=input_audio,
            )
            generate_clip_metadata(clip | copy | {"applied_music": track.name if track else ""}, source_title, output, source_url)
        finally:
            slice_path.unlink(missing_ok=True)

    if request.auto_delete_source:
        for file in filter(None, source_files):
            Path(file).unlink(missing_ok=True)
    return len(clips)
