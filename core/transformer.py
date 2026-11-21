import os
import random
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryFile

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
CPU_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]
NVENC_ENCODER = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]


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


@lru_cache(maxsize=4)
def nvenc_available(ffmpeg):
    try:
        result = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:r=30",
            "-frames:v", "1", *NVENC_ENCODER, "-f", "null", "-",
        ], capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_encoder(command, target, duration, progress):
    if progress is None:
        result = subprocess.run(command, cwd=target, capture_output=True, encoding="utf-8", errors="replace")
        return result.returncode, result.stderr

    command = command[:1] + ["-progress", "pipe:1", "-nostats"] + command[1:]
    with TemporaryFile() as log, subprocess.Popen(
        command, cwd=target, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", errors="replace",
    ) as encoder:
        for line in encoder.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                progress(min(99, int(int(value) / duration / 10000)))
        exit_code = encoder.wait()
        log.seek(0, os.SEEK_END)
        log.seek(max(0, log.tell() - 2048))
        return exit_code, log.read().decode("utf-8", errors="replace")


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
    progress=None,
    prefer_hardware=False,
    encoder_status=None,
):
    input_video = Path(input_video).resolve()
    target = Path(output_folder or OUTPUT_DIR).resolve()
    target.mkdir(parents=True, exist_ok=True)
    output = target / output_filename
    temporary = output.with_suffix(".rendering")
    duration = end_time - start_time
    export_duration = duration / audio_tempo
    width = output_width
    height = width * 16 // 9
    half_height = height // 2

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
            f"[0:v]scale={width}:{half_height}:force_original_aspect_ratio=increase,crop={width}:{half_height},setsar=1{flip}[top]",
            f"[{next_input}:v]scale={width}:{half_height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{half_height},setsar=1[bottom]",
            "[top][bottom]vstack=inputs=2:shortest=1[layout]",
        ])
    elif mode == "vertical_crop":
        filters.append(f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1{flip}[layout]")
    else:
        # The background is intentionally soft. Blur fewer pixels, then enlarge it behind the full-size foreground.
        bg_width, bg_height = width // 4, height // 4
        radius = max(1, round(width / 1080 * 6))
        filters.extend([
            f"[0:v]scale={bg_width}:{bg_height}:force_original_aspect_ratio=increase,"
            f"crop={bg_width}:{bg_height},boxblur={radius}:5,scale={width}:{height},setsar=1[bg]",
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1{flip}[fg]",
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
    command += ["-t", str(export_duration), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    output_args = ["-f", "mp4", str(temporary)]

    try:
        use_nvenc = prefer_hardware and nvenc_available(command[0])
        if encoder_status:
            encoder_status("NVIDIA hardware" if use_nvenc else "CPU")
        ceiling = 8 if width == 1080 else 5
        encoder = NVENC_ENCODER + ["-maxrate", f"{ceiling}M", "-bufsize", f"{ceiling * 2}M"] if use_nvenc else CPU_ENCODER
        exit_code, log = run_encoder(command + encoder + output_args, target, export_duration, progress)
        if use_nvenc and exit_code:
            if encoder_status:
                encoder_status("CPU fallback")
            exit_code, log = run_encoder(command + CPU_ENCODER + output_args, target, export_duration, progress)
        if exit_code:
            raise RuntimeError(f"FFmpeg could not render this clip. {log.strip()[-600:]}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return str(output)
