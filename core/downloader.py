import os
import re
import sys
import json
import shutil
import subprocess
from collections import deque
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"

def sanitize_filename(title: str) -> str:
    return re.sub(r'[\\/*?:"<>| \x00-\x1f]', '_', title)[:60].strip(". ") or "Video"

def get_yt_dlp_cmd():
    # 1. Local bin
    local_bin = BASE_DIR / "bin" / "yt-dlp.exe"
    if local_bin.exists():
        return str(local_bin)
    
    # 2. Virtual environment Scripts directory
    venv_bin = BASE_DIR / "venv" / "Scripts" / "yt-dlp.exe"
    if venv_bin.exists():
        return str(venv_bin)
        
    # 3. Python executable sibling
    py_sibling = Path(sys.executable).parent / "yt-dlp.exe"
    if py_sibling.exists():
        return str(py_sibling)
        
    # 4. PATH lookup
    which_bin = shutil.which("yt-dlp")
    if which_bin:
        return which_bin
        
    return "yt-dlp"

def extract_video_info(url: str) -> dict:
    cmd = [
        get_yt_dlp_cmd(),
        "--dump-json",
        "--no-playlist",
        url
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
        info = json.loads(res.stdout)
        return {
            "id": info.get("id"),
            "title": info.get("title", "YouTube Video"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", "Unknown"),
            "thumbnail": info.get("thumbnail", ""),
            "description": info.get("description", "")
        }
    except Exception as e:
        return {
            "id": "video",
            "title": "YouTube Video",
            "duration": 0,
            "uploader": "Unknown",
            "thumbnail": "",
            "description": str(e)
        }

def get_downloads_storage_info() -> dict:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    files = []
    for f in DOWNLOADS_DIR.iterdir():
        if f.is_file():
            sz = f.stat().st_size
            total_bytes += sz
            files.append({
                "name": f.name,
                "size_mb": round(sz / (1024 * 1024), 2)
            })
    return {
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "count": len(files),
        "files": files
    }

def clear_all_downloads_detailed() -> dict:
    if not DOWNLOADS_DIR.exists():
        return {
            "count": 0,
            "freed_mb": 0.0,
            "file_names": [],
            "location": str(DOWNLOADS_DIR.resolve())
        }
    deleted_count = 0
    freed_bytes = 0
    names = []
    for f in DOWNLOADS_DIR.iterdir():
        if f.is_file():
            try:
                sz = f.stat().st_size
                f.unlink()
                deleted_count += 1
                freed_bytes += sz
                names.append(f.name)
            except Exception:
                pass
    return {
        "count": deleted_count,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "file_names": names,
        "location": str(DOWNLOADS_DIR.resolve())
    }

def clear_all_downloads() -> int:
    return clear_all_downloads_detailed()["count"]

def fetch_subtitles_only(url: str) -> dict:
    """Fetches English captions without downloading the video."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    info = extract_video_info(url)
    clean_title = sanitize_filename(info["title"])
    video_id = info.get("id", "video")
    
    output_template = str(DOWNLOADS_DIR / f"{clean_title}_{video_id}.%(ext)s")
    
    cmd = [
        get_yt_dlp_cmd(),
        "--skip-download",
        "--write-subs",
        "--write-auto-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "-o", output_template,
        "--no-playlist",
        url
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception as e:
        print(f"[!] Subtitle-only pull failed: {e}")
        
    sub_file = None
    for f in DOWNLOADS_DIR.glob(f"{clean_title}_{video_id}.*"):
        if f.suffix.lower() in [".vtt", ".srt"] and f.stat().st_size > 10:
            sub_file = str(f)
            break
            
    return {
        "info": info,
        "clean_title": clean_title,
        "video_id": video_id,
        "subtitle_path": sub_file
    }

def run_download(command, progress=None):
    if progress is None:
        subprocess.run(command, check=True)
        return

    prefix = "studio_progress:"
    command = command[:1] + [
        "--progress", "--newline", "--color", "never",
        "--progress-template", f"download:{prefix}%(progress._percent_str)s",
    ] + command[1:]
    messages = deque(maxlen=10)
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    ) as download:
        for line in download.stdout:
            line = line.strip()
            if line.startswith(prefix):
                try:
                    progress(max(0, min(100, int(float(line[len(prefix):].strip().rstrip("%"))))))
                except (ValueError, OverflowError):
                    continue
            elif line:
                messages.append(line)
        if download.wait():
            raise RuntimeError("YouTube download failed. " + " ".join(messages)[-750:])


def download_video_section(url: str, start_sec: float, end_sec: float, output_path: str, *, progress=None) -> bool:
    """Request the specified time range using yt-dlp and FFmpeg."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    from core.transformer import get_ffmpeg_cmd
    time_range = f"*{start_sec:.3f}-{end_sec:.3f}"
    cmd = [
        get_yt_dlp_cmd(),
        "--download-sections", time_range,
        "--ffmpeg-location", get_ffmpeg_cmd(),
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        url
    ]
    print(f"[*] Byte-range fetching {time_range} from YouTube...")
    try:
        run_download(cmd, progress)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1024
    except Exception as e:
        print(f"[!] Failed to fetch section: {e}")
        return False

def download_audio_only(url: str, *, info=None, progress=None) -> dict:
    """Download the compressed audio stream for Whisper."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    info = info if info is not None else extract_video_info(url)
    clean_title = sanitize_filename(info["title"])
    video_id = info.get("id", "video")
    stem = f"{clean_title}_{video_id}_audio"
    output_template = str(DOWNLOADS_DIR / f"{stem}.%(ext)s")
    from core.transformer import get_ffmpeg_cmd
    cmd = [
        get_yt_dlp_cmd(),
        "-f", "ba[ext=m4a]/ba/bestaudio",
        "-x", "--audio-format", "m4a",
        "--ffmpeg-location", get_ffmpeg_cmd(),
        "-N", "8",
        "--no-mtime",
        "-o", output_template,
        "--no-playlist",
        url
    ]
    try:
        run_download(cmd, progress)
    except Exception as e:
        print(f"[!] Audio-only download failed: {e}")

    audio_file = None
    for f in DOWNLOADS_DIR.glob(f"{stem}.*"):
        if f.suffix.lower() in {".m4a", ".mp3", ".wav", ".aac", ".webm", ".opus", ".ogg"} and f.stat().st_size > 1024:
            audio_file = str(f)
            break
            
    return {
        "info": info,
        "clean_title": clean_title,
        "video_id": video_id,
        "audio_path": audio_file
    }

def delete_source_media(video_path: str = None, audio_path: str = None) -> bool:
    """Deletes the raw source video and audio from disk."""
    deleted = False
    for p_str in [video_path, audio_path]:
        if p_str:
            try:
                p = Path(p_str)
                if p.exists() and p.is_file():
                    p.unlink()
                    deleted = True
            except Exception:
                pass
    return deleted

def delete_task_directory(folder_name: str, output_base_dir: Path) -> bool:
    """Deletes an entire task directory in output_clips."""
    import stat
    from urllib.parse import unquote
    if not folder_name:
        return True

    folder_clean = unquote(folder_name).strip()
    target = output_base_dir / folder_clean

    if not target.exists() and output_base_dir.exists():
        # Match by task_id in brackets if exact folder name altered
        parts = folder_clean.split("_[")
        task_id = parts[-1].replace("]", "").strip() if len(parts) > 1 else folder_clean
        for sub in output_base_dir.iterdir():
            if sub.is_dir() and (task_id in sub.name or folder_clean in sub.name):
                target = sub
                break

    if target.exists() and target.is_dir():
        for root, dirs, files in os.walk(target):
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), stat.S_IWRITE)
                except Exception:
                    pass
        def on_rm_error(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            shutil.rmtree(target, onerror=on_rm_error)
        except Exception:
            try:
                shutil.rmtree(target, ignore_errors=True)
            except Exception:
                pass

    return not target.exists()

def check_existing_download(clean_title: str, video_id: str) -> dict:
    video_file = None
    audio_file = None
    sub_file = None
    for f in DOWNLOADS_DIR.glob(f"{clean_title}_{video_id}.*"):
        ext = f.suffix.lower()
        if ext in [".mp4", ".mkv", ".webm"] and f.stat().st_size > 1024:
            video_file = str(f)
        elif ext in [".m4a", ".mp3", ".wav", ".aac"] and f.stat().st_size > 1024:
            audio_file = str(f)
        elif ext in [".vtt", ".srt"] and f.stat().st_size > 10:
            sub_file = str(f)
    return {
        "video_path": video_file,
        "audio_path": audio_file,
        "subtitle_path": sub_file
    }

def download_video(url: str, output_name: str = None, *, include_subtitles=True, progress=None) -> dict:
    from core.transformer import get_ffmpeg_cmd

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    info = extract_video_info(url)
    clean_title = sanitize_filename(info["title"])
    video_id = info.get("id", "video")
    
    # Check if existing download is available to save time
    existing = check_existing_download(clean_title, video_id)
    if existing["video_path"]:
        print(f"[+] Reusing existing downloaded media: {Path(existing['video_path']).name}")
        return {
            "info": info,
            "video_path": existing["video_path"],
            "audio_path": existing["audio_path"],
            "subtitle_path": existing["subtitle_path"]
        }

    output_template = str(DOWNLOADS_DIR / f"{clean_title}_{video_id}.%(ext)s")
    
    # Fast multi-threaded concurrent download (8 parallel connections)
    subtitle_args = ["--write-subs", "--write-auto-sub", "--sub-lang", "en", "--sub-format", "vtt"] if include_subtitles else []
    cmd = [
        get_yt_dlp_cmd(),
        "-f", "best[ext=mp4]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best",
        "-N", "8",
        "--concurrent-fragments", "8",
        "--no-mtime",
        "--ffmpeg-location", get_ffmpeg_cmd(),
        *subtitle_args,
        "-o", output_template,
        "--no-playlist",
        url
    ]
    
    print(f"[*] Downloading YouTube video (accelerated 8x): {info['title']}...")
    run_download(cmd, progress)
    
    video_file = None
    audio_file = None
    sub_file = None
    
    for f in DOWNLOADS_DIR.glob(f"{clean_title}_{video_id}.*"):
        ext = f.suffix.lower()
        if ext in [".mp4", ".mkv", ".webm"]:
            video_file = str(f)
        elif ext in [".m4a", ".mp3", ".wav", ".aac"]:
            audio_file = str(f)
        elif ext in [".vtt", ".srt"]:
            sub_file = str(f)
            
    return {
        "info": info,
        "video_path": video_file,
        "audio_path": audio_file,
        "subtitle_path": sub_file
    }
