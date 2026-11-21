import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import av

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
GAMEPLAY_DIR = ASSETS_DIR / "gameplay"
MUSIC_DIR = ASSETS_DIR / "music"
OUTPUT_DIR = BASE_DIR / "output_clips"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
COLOR_LOOKS = {
    "warm": "colorbalance=rs=0.035:bs=-0.035:rm=0.05:bm=-0.05:pl=1",
    "monochrome": "hue=s=0",
}


def get_ffmpeg_cmd():
    try:
        import imageio_ffmpeg
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(executable).is_file():
            return executable
    except (ImportError, RuntimeError):
        pass

    candidates = (
        BASE_DIR / "bin" / "ffmpeg.exe",
        BASE_DIR / "bin" / "ffmpeg" / "ffmpeg.exe",
        Path(sys.executable).with_name("ffmpeg.exe"),
    )
    for file in candidates:
        if file.is_file():
            return str(file)
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    winget = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    return str(next(winget.glob("**/ffmpeg.exe"), "ffmpeg"))


def asset_files(folder, extensions):
    if not folder.is_dir():
        return []
    return sorted(file for file in folder.iterdir() if file.is_file() and file.suffix.lower() in extensions)


def inspect_media(path):
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("This file does not contain a video track.")
        video = container.streams.video[0]
        duration = float(video.duration * video.time_base) if video.duration else (container.duration or 0) / av.time_base
        return duration, bool(container.streams.audio)


def find_companion_audio(video_path):
    path = Path(video_path)
    stem = path.stem.split(".f")[0]
    return next((str(file.resolve()) for file in asset_files(path.parent, AUDIO_EXTENSIONS)
                 if file.stem == stem or file.stem.startswith(stem + ".f")), None)


def render_viral_clip(
    input_video,
    start_time,
    end_time,
    output_filename,
    output_folder=None,
    subtitles_ass_path=None,
    mode="split_screen",
    enable_hflip=False,
    audio_tempo=1.04,
    color_contrast=1.05,
    color_saturation=1.08,
    bg_music_volume=0.12,
    *,
    audio_mode="legacy_mix",
    music_path=None,
    source_audio_path=None,
    output_width=1080,
    color_look="original",
    edge_fades=False,
):
    input_video = Path(input_video).resolve()
    target = Path(output_folder or OUTPUT_DIR).resolve()
    target.mkdir(parents=True, exist_ok=True)
    output = target / output_filename
    temporary = output.with_suffix(".rendering")
    duration = end_time - start_time
    export_duration = duration / audio_tempo

    gameplay = asset_files(GAMEPLAY_DIR, {".mp4", ".mkv", ".mov"}) if mode == "split_screen" else []
    music = asset_files(MUSIC_DIR, AUDIO_EXTENSIONS) if audio_mode == "legacy_mix" else []
    music_path = music_path or (random.choice(music) if music else None)

    command = [get_ffmpeg_cmd(), "-hide_banner", "-loglevel", "error", "-y"]
    command += ["-ss", str(start_time), "-t", str(duration), "-i", str(input_video)]
    next_input = 1
    audio = None
    if audio_mode in {"original", "legacy_mix"}:
        _, has_audio = inspect_media(input_video)
        if has_audio:
            audio = 0
        elif companion := source_audio_path or find_companion_audio(input_video):
            command += ["-ss", str(start_time), "-t", str(duration), "-i", str(companion)]
            audio = next_input
            next_input += 1

    music_input = None
    if music_path and audio_mode in {"replace", "legacy_mix"}:
        command += ["-stream_loop", "-1", "-t", str(export_duration), "-i", str(Path(music_path).resolve())]
        music_input = next_input
        next_input += 1

    flip = ",hflip" if enable_hflip else ""
    filters = []
    if gameplay:
        command += ["-stream_loop", "-1", "-t", str(duration), "-i", str(random.choice(gameplay))]
        filters.extend([
            f"[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1{flip}[top]",
            f"[{next_input}:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1[bottom]",
            "[top][bottom]vstack=inputs=2:shortest=1[layout]",
        ])
    elif mode == "vertical_crop":
        filters.append(f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1{flip}[layout]")
    else:
        filters.extend([
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,setsar=1[bg]",
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1{flip}[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[layout]",
        ])

    video_filters = [f"eq=contrast={color_contrast}:saturation={color_saturation}"]
    if look := COLOR_LOOKS.get(color_look):
        video_filters.append(look)
    if subtitles_ass_path:
        subtitles = Path(subtitles_ass_path).resolve()
        if subtitles.parent == target:
            escaped = subtitles.name
        else:
            escaped = subtitles.as_posix().replace(":", r"\:")
        escaped = escaped.replace("'", r"'\''")
        video_filters.append(f"subtitles=filename='{escaped}'")
    if output_width != 1080:
        video_filters.append(f"scale={output_width}:{output_width * 16 // 9}")
    if audio_tempo != 1:
        video_filters.append(f"setpts=PTS/{audio_tempo}")
    if edge_fades:
        fade_length = min(0.4, export_duration / 3)
        fade_out = export_duration - fade_length
        video_filters.extend([
            f"fade=t=in:st=0:d={fade_length:.6f}",
            f"fade=t=out:st={fade_out:.6f}:d={fade_length:.6f}",
        ])
    filters.append("[layout]" + ",".join(video_filters) + "[video]")

    audio_output = None
    if audio is not None:
        filters.append(f"[{audio}:a]atempo={audio_tempo}[voice]")
        audio_output = "[voice]"
    if music_input is not None:
        volume = 1 if audio_mode == "replace" else bg_music_volume
        filters.append(f"[{music_input}:a]volume={volume}[music]")
        if audio_output:
            filters.append("[voice][music]amix=inputs=2:duration=first[audio]")
            audio_output = "[audio]"
        else:
            audio_output = "[music]"

    if edge_fades and audio_output:
        filters.append(
            f"{audio_output}afade=t=in:st=0:d={fade_length:.6f},"
            f"afade=t=out:st={fade_out:.6f}:d={fade_length:.6f}[faded_audio]"
        )
        audio_output = "[faded_audio]"

    command += ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters), "-map", "[video]"]
    if audio_output:
        command += ["-map", audio_output, "-c:a", "aac", "-b:a", "192k"]
    command += [
        "-t", str(export_duration), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(temporary),
    ]

    try:
        result = subprocess.run(command, cwd=target, capture_output=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(f"FFmpeg could not render this clip. {result.stderr.strip()[-600:]}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return str(output)
