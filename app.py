import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

import av
import requests
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from config.settings import load_settings, save_settings
from config.studio import Layout, StudioSettings, studio_settings
from core.analyzer import generate_post_copy
from core.clip_editor import ClipEdit, EFFECTS, effect_wave, render_edit
from core.downloader import DOWNLOADS_DIR, clear_all_downloads_detailed, get_downloads_storage_info, sanitize_filename
from core.job_queue import DuplicateJob, JobQueue
from core.publisher import posting_metadata, read_caption_context, save_task_info, write_clip_metadata
from core.studio_pipeline import run_job
from core.transformer import AUDIO_EXTENSIONS, MUSIC_DIR, OUTPUT_DIR, asset_files, inspect_media

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
JOB_QUEUE = JobQueue(BASE_DIR / "data" / "jobs.sqlite3")
SETTINGS_LOCK = threading.Lock()
POSTING_KIT_LOCK = threading.Lock()


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


class PostingKitRequest(BaseModel):
    notes: str = Field(default="", max_length=800)


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


def exported_video(folder_name, filename):
    folder = task_folder(folder_name)
    name = Path(filename)
    if name.name != filename or name.suffix.lower() != ".mp4" or filename.startswith(("source-", "slice_")):
        raise HTTPException(404, "Exported clip not found.")
    video = (folder / name).resolve()
    if video.parent != folder or video.name != filename or not video.is_file():
        raise HTTPException(404, "Exported clip not found.")
    return video


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


def editor_music_file(filename):
    path = (MUSIC_DIR / filename).resolve()
    if path.parent != MUSIC_DIR.resolve() or path.name != filename or path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
        raise HTTPException(404, "That track is unavailable in your music library.")
    return path


@app.get("/api/editor-assets")
def editor_assets():
    return {
        "music": list_music(),
        "effects": [{"id": name, "label": settings[0], "duration": settings[1]} for name, settings in EFFECTS.items()],
    }


@app.get("/api/editor-effects/{name}")
def editor_effect(name: str):
    if name not in EFFECTS:
        raise HTTPException(404, "Sound effect not found.")
    return Response(effect_wave(name), media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/editor-music/{filename}")
def editor_music(filename: str):
    return FileResponse(editor_music_file(filename))


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


@app.post("/api/reveal_clip/{folder_name}/{filename}")
def reveal_clip(folder_name: str, filename: str):
    video = exported_video(folder_name, filename)
    try:
        explorer = Path(os.environ["SystemRoot"]) / "explorer.exe"
        os.startfile(str(explorer), arguments=f'/select,"{video}"')
    except (OSError, AttributeError, KeyError) as error:
        raise HTTPException(500, "Could not select the clip in Windows File Explorer.") from error
    return {"status": "ok"}


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


def export_batches():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    running = JOB_QUEUE.running_id()
    for entry in OUTPUT_DIR.iterdir():
        if running and entry.name.endswith(f"_[{running}]"):
            continue
        videos = sorted(file for file in entry.glob("*.mp4") if not file.name.startswith(("source-", "slice_")))
        try:
            exported_at = max((video.stat().st_mtime for video in videos), default=None)
        except FileNotFoundError:
            continue
        if exported_at is None:
            continue

        info = read_json(entry / "info.json")
        yield {
            "task_id": batch_task_id(entry, info),
            "youtube_url": info.get("youtube_url", ""),
            "source_title": info.get("source_title", entry.name),
            "folder_name": entry.name,
            "exported_at": exported_at,
        }, videos


def exported_clip(video):
    return {"title": video.stem} | read_json(video.with_suffix(".json")) | {"filename": video.name}


def public_clip(clip):
    clip.pop("posting_context", None)
    return clip


@app.get("/api/tasks")
def list_tasks():
    return [
        batch | {"clips": [public_clip(exported_clip(video)) for video in videos]}
        for batch, videos in sorted(export_batches(), key=lambda row: row[0]["exported_at"], reverse=True)
    ]


@app.get("/api/library")
def search_library(
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=3, ge=1, le=12),
    sort: Literal["newest", "oldest", "title"] = "newest",
    source: Literal["", "youtube", "upload"] = "",
    selection: Literal["", "ai", "timeline", "manual", "edited"] = "",
    after: int | None = None,
    before: int | None = None,
    focus: str = Query(default="", max_length=260),
):
    if after is not None and before is not None and after >= before:
        raise HTTPException(422, "Choose an end time at or after the start time.")

    words = [word.lstrip("#") for word in q.casefold().split() if word.lstrip("#")]
    matches = []
    exported_ids = []
    total_clips = matched_clips = 0
    for batch, videos in export_batches():
        exported_ids.append(batch["task_id"])
        total_clips += len(videos)
        exported = batch["exported_at"]
        if (after is not None and exported < after) or (before is not None and exported >= before):
            continue
        batch_source = "youtube" if batch["youtube_url"] else "upload"
        if source and source != batch_source:
            continue

        batch_text = " ".join(str(batch[field]) for field in ("task_id", "source_title", "youtube_url", "folder_name")).casefold()
        remaining = [word for word in words if word not in batch_text]
        visible = []
        for video in videos:
            if not remaining and not selection:
                visible.append(video)
                continue
            clip = exported_clip(video)
            if selection and clip.get("selection") != selection:
                continue
            values = (clip.get(field, "") for field in (
                "filename", "title", "title_variants", "description", "hook", "ai_hook", "reason",
                "tags", "pinned_comment", "posting_notes", "posting_context",
            ))
            clip_text = " ".join(str(value) for value in values).casefold()
            if not all(word in clip_text for word in remaining):
                if clip.get("posting_context"):
                    continue
                captions = read_caption_context(video.with_suffix(".ass")).casefold()
                if not all(word in clip_text or word in captions for word in remaining):
                    continue
            visible.append(clip)
        if visible:
            matches.append((batch, visible))
            matched_clips += len(visible)

    matches.sort(key=lambda row: (-row[0]["exported_at"], row[0]["folder_name"]))
    if sort == "title":
        matches.sort(key=lambda row: row[0]["source_title"].casefold())
    elif sort == "oldest":
        matches.sort(key=lambda row: row[0]["exported_at"])

    pages = max(1, math.ceil(len(matches) / size))
    target = next((index for index, (batch, _) in enumerate(matches) if batch["task_id"] == focus), None) if focus else None
    page = min(page, pages) if target is None else target // size + 1
    start = (page - 1) * size
    return {
        "batches": [
            batch | {"clips": [public_clip(exported_clip(clip) if isinstance(clip, Path) else clip) for clip in clips]}
            for batch, clips in matches[start:start + size]
        ],
        "page": page,
        "pages": pages,
        "total_batches": len(matches),
        "total_clips": total_clips,
        "matched_clips": matched_clips,
        "exported_ids": exported_ids,
    }


@app.post("/api/posting-kit/{folder_name}/{filename}")
def refresh_posting_kit(folder_name: str, filename: str, request: PostingKitRequest):
    video = exported_video(folder_name, filename)
    folder = video.parent
    if not POSTING_KIT_LOCK.acquire(blocking=False):
        raise HTTPException(409, "A posting kit is already generating. Try again when it finishes.")

    try:
        output = video.with_suffix(".json")
        metadata = read_json(output)
        info = read_json(folder / "info.json")
        label = info.get("source_title") or video.stem
        context = metadata.get("posting_context") or read_caption_context(video.with_suffix(".ass"))
        copy = generate_post_copy(
            {}, [], label, load_settings(), tuple(list_music()), excerpt=context, notes=request.notes,
        )
        if copy["copy_source"] == "no_transcript":
            raise HTTPException(422, "Add a note describing what actually happens in this clip, then try again.")
        if copy["copy_source"] != "ai":
            raise HTTPException(503, "The local model did not return a posting kit. Check Ollama and try again.")
        if metadata.get("copy_ready") and not copy["copy_ready"]:
            raise HTTPException(502, "The new draft was incomplete. Your previous kit is still saved.")

        updated = metadata | posting_metadata(copy, label, metadata.get("source_url") or info.get("youtube_url", ""))
        with JOB_QUEUE.gate:
            if not video.is_file():
                raise HTTPException(404, "The clip was removed while the kit was generating.")
            write_clip_metadata(output, updated)
        updated.pop("posting_context", None)
        return updated | {"filename": video.name}
    finally:
        POSTING_KIT_LOCK.release()


def process_edit(job_id, payload, update):
    edit = ClipEdit.model_validate(payload)
    video = exported_video(payload["folder"], payload["filename"])
    music = editor_music_file(edit.music_track) if edit.music_track else None
    source_info = read_json(video.parent / "info.json")
    source_url = source_info.get("youtube_url", "")
    title = payload["title"]
    folder = OUTPUT_DIR / f"{sanitize_filename(title)}_[{job_id}]"
    folder.mkdir(exist_ok=False)
    output = folder / "edited.mp4"
    temporary = output.with_suffix(".rendering")
    duration = edit.end - edit.start
    detail = f"{duration:.2f} seconds · {len(edit.cues)} sound effects"
    try:
        update("export", "Rendering edit", detail)
        render_edit(video, temporary, edit, music, lambda percent: update("export", "Rendering edit", detail, percent))
        update("finalize", "Saving edited clip", "The edited MP4 will appear as a new batch.")
        save_task_info(folder, job_id, source_url, title, duration=duration)
        write_clip_metadata(output.with_suffix(".json"), {
            "title": title, "video_file": output.name, "selection": "edited", "start_time": 0,
            "end_time": duration, "source_url": source_url,
            "edited_from": {"folder": video.parent.name, "filename": video.name},
            "applied_music": edit.music_track if edit.music_level else "",
        })
        temporary.replace(output)
    except Exception:
        shutil.rmtree(folder)
        raise
    return 1


def process_job(job_id, payload, preferences):
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

    if payload["source_type"] == "edit":
        return process_edit(job_id, payload, update)

    request = ClipRequest.model_validate(payload)
    settings = preferences | {"llm_api_key": load_settings()["llm_api_key"]}

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


def clip_job_identity(request, settings):
    if request["source_type"] == "edit":
        return "edit", request, {}
    if request["source_type"] == "url":
        url = urlparse(request["url"])
        route = url.path.strip("/").split("/")
        if url.hostname == "youtu.be":
            video_id = route[0]
        elif url.path == "/watch":
            video_id = parse_qs(url.query).get("v", [""])[0]
        elif len(route) == 2 and route[0] in {"shorts", "embed", "live"}:
            video_id = route[1]
        else:
            video_id = ""
        source = video_id if re.fullmatch(r"[\w-]{11}", video_id, flags=re.ASCII) else url._replace(fragment="").geturl()
    else:
        source = os.path.normcase(request["file_path"])

    exact_range = request["selection"] == "range"
    options = {name: request.get(name) for name in (
        "source_type", "selection", "mode", "output_resolution", "color_look", "edge_fades",
        "auto_delete_source", "mirror", "color_grade",
    )}
    options["playback_speed"] = request.get("playback_speed") or (settings.get("audio_tempo") if request.get("audio_speed") else 1)
    options["opening_text"] = " ".join(request.get("custom_opening_text", "").split()) if settings.get("opening_hook") else ""
    if exact_range:
        options.update(start=request["start_time"], end=request["end_time"])
        settings = {name: value for name, value in settings.items() if name not in {"clip_length", "max_clips_to_generate"}}
    else:
        options["length"] = request.get("clip_length")
    return source, options, settings


@app.post("/api/edit/{folder_name}/{filename}")
def queue_clip_edit(folder_name: str, filename: str, edit: ClipEdit):
    with JOB_QUEUE.gate:
        video = exported_video(folder_name, filename)
        try:
            duration, _ = inspect_media(video)
        except (av.error.FFmpegError, ValueError) as error:
            raise HTTPException(422, "Could not read the exported video.") from error
        if edit.end > duration + .12 or duration - edit.start < .24:
            raise HTTPException(422, "The trim range is outside the exported video.")
        edit.end = min(edit.end, duration)
        if any(cue.at >= edit.end - edit.start for cue in edit.cues):
            raise HTTPException(422, "Move sound effects inside the trim range.")
        if edit.music_track:
            editor_music_file(edit.music_track)
        title = read_json(video.with_suffix(".json")).get("title") or video.stem
        title = str(title).removesuffix(" · Edited") + " · Edited"
        payload = edit.model_dump() | {"folder": video.parent.name, "filename": video.name, "source_type": "edit", "title": title}
        try:
            job_id = JOB_QUEUE.submit(title, payload, {}, identity=clip_job_identity)
        except DuplicateJob as error:
            return {"job_id": error.job_id, "duplicate": True}
    return {"job_id": job_id}


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
        job_id = JOB_QUEUE.submit(source, request.model_dump(), effective, identity=clip_job_identity)
    except DuplicateJob as error:
        return {"job_id": error.job_id, "duplicate": True}
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
