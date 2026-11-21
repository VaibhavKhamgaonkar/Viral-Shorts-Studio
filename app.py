import json
import os
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import av
import requests
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from config.settings import load_settings, save_settings
from config.studio import Layout, StudioSettings, studio_settings
from core.downloader import DOWNLOADS_DIR, clear_all_downloads_detailed, get_downloads_storage_info, sanitize_filename
from core.job_queue import JobQueue
from core.studio_pipeline import run_job
from core.transformer import AUDIO_EXTENSIONS, MUSIC_DIR, OUTPUT_DIR, asset_files

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
JOB_QUEUE = JobQueue(BASE_DIR / "data" / "jobs.sqlite3")
SETTINGS_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(_app):
    JOB_QUEUE.start(process_job)
    try:
        yield
    finally:
        JOB_QUEUE.stop()


app = FastAPI(title="Viral Shorts Studio", lifespan=lifespan)
app.mount("/studio", StaticFiles(directory=WEB_DIR, check_dir=False), name="studio")


class ClipRequest(BaseModel):
    source_type: Literal["url", "upload"] = "url"
    url: str = Field(default="", max_length=2048)
    file_path: str = Field(default="", max_length=512)
    selection: Literal["highlights", "range"] = "highlights"
    start_time: float = Field(default=0, ge=0, allow_inf_nan=False)
    end_time: float = Field(default=0, ge=0, allow_inf_nan=False)
    clip_length: Literal["short", "extended", "long"] | None = None
    mode: Layout | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)
    clip_count: int | None = Field(default=None, ge=1, le=6)
    output_resolution: Literal[720, 1080] = 1080
    playback_speed: Literal[0.9, 1, 1.04, 1.15] | None = None
    captions: bool | None = None
    opening_hook: bool | None = None
    custom_opening_text: str = Field(default="", max_length=90)
    color_look: Literal["original", "warm", "monochrome"] = "original"
    edge_fades: bool = False
    audio_mode: Literal["original", "mute"] | None = None
    auto_delete_source: bool | None = None
    mirror: bool = False
    audio_speed: bool = False
    color_grade: bool = False

    @model_validator(mode="after")
    def validate_source_and_range(self):
        if self.source_type == "url":
            parsed = urlparse(self.url.strip())
            allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed or not parsed.path:
                raise ValueError("Enter a valid YouTube video URL.")
            self.url = parsed.geturl()
        else:
            file = Path(self.file_path).resolve()
            if file.parent != UPLOADS_DIR.resolve() or file.suffix.lower() not in UPLOAD_EXTENSIONS or not file.is_file():
                raise ValueError("Upload a supported local video first.")
            self.file_path = str(file)
        if self.selection == "range" and not 1 <= self.end_time - self.start_time <= 600:
            raise ValueError("Choose a time range between 1 second and 10 minutes.")
        return self


@contextmanager
def idle_workspace():
    with JOB_QUEUE.gate:
        if JOB_QUEUE.has_work():
            raise HTTPException(409, "Wait for the clip queue to finish before deleting studio files.")
        yield


def task_folder(name):
    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR.resolve() or not path.is_dir():
        raise HTTPException(404, "Clip batch not found.")
    return path


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def batch_task_id(folder, info):
    return info.get("task_id") or folder.name.rsplit("_[", 1)[-1].rstrip("]")


@app.get("/", include_in_schema=False)
@app.get("/settings", include_in_schema=False)
@app.get("/library", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/settings")
def get_settings():
    return studio_settings(load_settings())


@app.put("/api/settings")
def update_settings(preferences: StudioSettings):
    if preferences.music_mode == "replace" and preferences.music_track not in list_music():
        raise HTTPException(422, "Select an available replacement track.")
    with SETTINGS_LOCK:
        settings = load_settings() | preferences.model_dump()
        save_settings(settings)
    return preferences


@app.get("/api/music")
def list_music():
    return [file.name for file in asset_files(MUSIC_DIR, AUDIO_EXTENSIONS)]


@app.post("/api/music")
def add_music(file: UploadFile = File(...)):
    name = Path(file.filename or "")
    extension = name.suffix.lower()
    if extension not in AUDIO_EXTENSIONS:
        raise HTTPException(415, "Choose an MP3, WAV, M4A, AAC, FLAC or OGG audio file.")
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(name.stem).replace("_", "-")
    temporary = MUSIC_DIR / f"{uuid.uuid4().hex}.upload"
    try:
        with temporary.open("xb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        try:
            with av.open(str(temporary)) as container:
                if not container.streams.audio:
                    raise HTTPException(422, "This file does not contain an audio track.")
        except av.error.FFmpegError as error:
            raise HTTPException(422, "Could not read that audio file.") from error
        with SETTINGS_LOCK:
            destination = MUSIC_DIR / f"{stem}{extension}"
            if destination.exists():
                destination = MUSIC_DIR / f"{stem}-{uuid.uuid4().hex[:6]}{extension}"
            temporary.rename(destination)
        return {"filename": destination.name}
    finally:
        temporary.unlink(missing_ok=True)


@app.get("/api/ai")
def ai_status():
    settings = load_settings()
    model = settings["llm_model"]
    try:
        response = requests.get(
            settings["llm_base_url"].rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {settings['llm_api_key']}"},
            timeout=(2, 2),
        )
        response.raise_for_status()
        models = [item["id"] for item in response.json()["data"]]
        return {"status": "ready" if model in models else "missing_model", "model": model}
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return {"status": "offline", "model": model}


@app.get("/clips/{folder_name}/{filename}")
def serve_clip_file(folder_name: str, filename: str):
    if Path(filename).name != filename or Path(filename).suffix.lower() not in {".mp4", ".json", ".ass"}:
        raise HTTPException(404, "Clip file not found.")
    candidates = (OUTPUT_DIR / folder_name / filename, OUTPUT_DIR / filename)
    for candidate in candidates:
        path = candidate.resolve()
        if OUTPUT_DIR.resolve() in path.parents and path.is_file():
            return FileResponse(path)
    raise HTTPException(404, "Clip file not found.")


@app.get("/api/storage")
def storage_info():
    return get_downloads_storage_info()


@app.post("/api/clear_downloads")
def clear_downloads_endpoint():
    with idle_workspace():
        return clear_all_downloads_detailed()


@app.post("/api/delete_task/{folder_name}")
def delete_task_endpoint(folder_name: str):
    with idle_workspace():
        folder = task_folder(folder_name)
        task_id = batch_task_id(folder, read_json(folder / "info.json"))
        shutil.rmtree(folder)
        JOB_QUEUE.remove_finished(task_id)
        return {"status": "ok"}


@app.post("/api/delete_source/{folder_name}")
def delete_source_endpoint(folder_name: str):
    with idle_workspace():
        folder = task_folder(folder_name)
        info = read_json(folder / "info.json")
        paths = [Path(file).resolve() for file in info.get("source_files", [])]
        if "source_files" not in info:
            prefix = folder.name.split("_[", 1)[0]
            paths = [
                path.resolve() for root in (DOWNLOADS_DIR, UPLOADS_DIR) if root.is_dir()
                for path in root.iterdir() if path.name.startswith(prefix + "_")
            ]
        deleted = 0
        for file in set(paths):
            if file.parent in {DOWNLOADS_DIR.resolve(), UPLOADS_DIR.resolve()} and file.is_file():
                file.unlink()
                deleted += 1
        return {"status": "ok", "deleted_count": deleted}


def open_in_explorer(path):
    try:
        os.startfile(str(path.resolve()))
    except (OSError, AttributeError) as error:
        raise HTTPException(500, f"Could not open the folder: {error}") from error
    return {"status": "ok"}


@app.get("/api/open_downloads_folder")
def open_downloads_folder():
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return open_in_explorer(DOWNLOADS_DIR)


@app.get("/api/open_folder/{folder_name}")
def open_folder(folder_name: str):
    return open_in_explorer(task_folder(folder_name))


@app.post("/api/upload_video")
def upload_video_endpoint(file: UploadFile = File(...)):
    name = Path(file.filename or "")
    if name.suffix.lower() not in UPLOAD_EXTENSIONS:
        raise HTTPException(415, "Choose an MP4, MOV, MKV, WebM or M4V video.")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / f"{sanitize_filename(name.stem)}_{uuid.uuid4().hex[:8]}{name.suffix.lower()}"
    try:
        with dest.open("xb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        size = dest.stat().st_size
        if not size:
            raise HTTPException(400, "The video file is empty.")
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return {"status": "ok", "file_path": str(dest), "filename": dest.name, "size_mb": round(size / 2**20, 2)}


@app.get("/api/tasks")
def list_tasks():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    running = JOB_QUEUE.running_id()
    entries = sorted(OUTPUT_DIR.iterdir(), key=lambda file: file.stat().st_mtime, reverse=True)
    batches = []
    for entry in entries:
        if running and entry.name.endswith(f"_[{running}]"):
            continue
        videos = sorted(entry.glob("*.mp4")) if entry.is_dir() else []
        clips = [
            {"title": video.stem} | read_json(video.with_suffix(".json")) | {"filename": video.name}
            for video in videos if not video.name.startswith(("source-", "slice_"))
        ]
        if not clips:
            continue
        info = read_json(entry / "info.json")
        batches.append({
            "task_id": batch_task_id(entry, info),
            "youtube_url": info.get("youtube_url", ""),
            "source_title": info.get("source_title", entry.name),
            "folder_name": entry.name,
            "clips": clips,
        })
    return batches


def process_job(job_id, payload, preferences):
    request = ClipRequest.model_validate(payload)
    settings = preferences | {"llm_api_key": load_settings()["llm_api_key"]}
    last_report = None
    last_update = 0

    def update(phase, stage, detail, progress=None, source=None):
        nonlocal last_report, last_update
        now = time.monotonic()
        report = (phase, stage, detail, progress, source)
        updating_percent = progress is not None and last_report and last_report[3] is not None
        if report == last_report or (updating_percent and now - last_update < 1):
            return
        JOB_QUEUE.report(job_id, phase, stage, detail, progress, source)
        last_report, last_update = report, now

    def cleanup(paths):
        with JOB_QUEUE.gate:
            if request.file_path and JOB_QUEUE.source_is_queued(request.file_path):
                return
            for file in filter(None, paths):
                try:
                    Path(file).unlink(missing_ok=True)
                except OSError as error:
                    update("finalize", "Source copy kept", f"Could not remove the local source copy: {error}")

    return run_job(job_id, request, settings, update, cleanup)


@app.post("/api/generate")
def create_job(request: ClipRequest):
    settings = load_settings()
    preferences = studio_settings(settings)
    request.clip_length = request.clip_length or preferences.clip_length
    request.mode = request.mode or preferences.default_mode
    if request.auto_delete_source is None:
        request.auto_delete_source = preferences.auto_delete_source
    pipeline_keys = (
        "llm_base_url", "whisper_model_size", "whisper_device", "audio_tempo",
        "color_contrast", "color_saturation",
    )
    effective = preferences.model_dump(exclude={"destinations"}) | {key: settings[key] for key in pipeline_keys}
    if request.model:
        effective["llm_model"] = request.model
    for value, key in (
        (request.clip_count, "max_clips_to_generate"),
        (request.captions, "captions"),
        (request.opening_hook, "opening_hook"),
        (request.audio_mode, "music_mode"),
    ):
        if value is not None:
            effective[key] = value

    source = request.url if request.source_type == "url" else Path(request.file_path).stem.rsplit("_", 1)[0]
    try:
        job_id = JOB_QUEUE.submit(source, request.model_dump(), effective)
    except FileNotFoundError as error:
        raise HTTPException(422, str(error)) from error
    return {"job_id": job_id}


@app.get("/api/jobs")
def list_jobs():
    return JOB_QUEUE.snapshot()


@app.get("/api/job/{job_id}")
def get_job(job_id: str):
    job = JOB_QUEUE.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job


@app.delete("/api/job/{job_id}")
def dismiss_job(job_id: str):
    if not JOB_QUEUE.remove_finished(job_id) and JOB_QUEUE.get(job_id):
        raise HTTPException(409, "Only finished tasks can be dismissed.")
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
