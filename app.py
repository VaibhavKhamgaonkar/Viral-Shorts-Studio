import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
import webbrowser
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse

import av
import numpy as np
import requests
import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.background import BackgroundTask

from config.settings import load_settings, save_settings
from config.studio import Destination, Layout, Platform, StudioSettings, studio_settings
from core.analyzer import generate_post_copy
from core.audio_breakdown import read_audio, write_audio
from core.clip_editor import (
    ClipEdit, EFFECTS, LOOKS, InsertAsset, edit_duration, effect_wave, frame_rate, kept_source_ranges,
    render_edit, validate_timeline,
)
from core.downloader import DOWNLOADS_DIR, clear_all_downloads_detailed, get_downloads_storage_info, sanitize_filename
from core.editor_speech import installed_voices, synthesize_speech
from core.editor_visuals import FRAMES, REACTIONS, REACTION_DURATION, frame_gif, reaction_gif
from core.hashtag_research import YouTubeSearchUnavailable, research_youtube_hashtags
from core.job_queue import DuplicateJob, JobQueue
from core.publisher import posting_metadata, read_caption_context, write_clip_metadata
from core.social_upload import FACEBOOK_REEL_MAX_SECONDS, PLATFORMS, PublishError, PublishStore
from core.studio_pipeline import run_job, transcribe_range
from core.transformer import AUDIO_EXTENSIONS, MUSIC_DIR, OUTPUT_DIR, asset_files, get_ffmpeg_cmd, inspect_media

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
UPLOADS_DIR = BASE_DIR / "uploads"
INSERTS_DIR = BASE_DIR / "assets" / "inserts"
VOICEOVERS_DIR = BASE_DIR / "assets" / "voiceovers"
VOICEOVER_MAX_SECONDS = 120
AUDIO_MODELS_DIR = BASE_DIR / "data" / "audio-models"
INSERT_EXTENSIONS = {".mp4", ".webm"}
UPLOAD_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
JOB_QUEUE = JobQueue(BASE_DIR / "data" / "jobs.sqlite3")
PUBLISHING = PublishStore(BASE_DIR / "data" / "publishing")
SETTINGS_LOCK = threading.Lock()
POSTING_KIT_LOCK = threading.Lock()
EDITOR_PREVIEW_LOCK = threading.Lock()
EDITOR_CAPTIONS_LOCK = threading.Lock()
EDITOR_STEMS_LOCK = threading.Lock()
EDITOR_TRACKS_LOCK = threading.Lock()
LOGGER = logging.getLogger(__name__)


class OAuthAccessLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3
                    and str(args[2]).startswith("/api/publishing/youtube/callback"))


logging.getLogger("uvicorn.access").addFilter(OAuthAccessLogFilter())


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
    refresh_hashtags: bool = False


class HashtagSearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=120)


class YouTubeClient(BaseModel):
    client_id: str = Field(max_length=250, pattern=r"^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$")
    client_secret: str


class MetaToken(BaseModel):
    user_token: str


class MetaConnection(MetaToken):
    page_id: str = Field(max_length=32, pattern=r"^[0-9]+$")


class PublishClip(BaseModel):
    filename: str = Field(min_length=5, max_length=250)
    revision: str = Field(max_length=32, pattern=r"^[0-9]+$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)


class PublishBatch(BaseModel):
    clips: list[PublishClip] = Field(min_length=1, max_length=25)
    destinations: dict[Platform, str] = Field(min_length=1, max_length=3)
    rights_confirmed: Literal[True]
    visibility: Literal["private", "unlisted", "public"] = "private"
    made_for_kids: bool | None = None
    synthetic_media: bool


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


def publishing_origin(request: Request):
    if request.url.scheme != "http" or request.url.hostname not in {"127.0.0.1", "localhost"}:
        raise HTTPException(403, "Open the studio at http://127.0.0.1:8000 to manage publishing.")
    origin = f"http://{request.url.netloc}"
    if request.method != "GET" and request.headers.get("origin") not in {None, origin}:
        raise HTTPException(403, "Open publishing from the local studio.")
    return origin


@app.get("/api/publishing")
def get_publishing(_origin: str = Depends(publishing_origin)):
    try:
        return {"accounts": PUBLISHING.public(), "instagram_setup": PUBLISHING.instagram_setup_status(),
                "defaults": {key: destination.enabled for key, destination in studio_settings(load_settings()).destinations.items()}}
    except PublishError as error:
        raise HTTPException(503, str(error)) from None


@app.post("/api/publishing/youtube/connect")
def connect_youtube(client: YouTubeClient, origin: str = Depends(publishing_origin)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,250}", client.client_secret):
        raise HTTPException(422, "Choose a valid Google Desktop app OAuth client JSON.")
    url = PUBLISHING.start_youtube(client.model_dump(), origin + "/api/publishing/youtube/callback")
    return {"authorization_url": url}


@app.get("/api/publishing/youtube/callback", include_in_schema=False)
def youtube_callback(state: str = "", code: str = "", origin: str = Depends(publishing_origin)):
    try:
        PUBLISHING.finish_youtube(state, code, origin + "/api/publishing/youtube/callback")
    except PublishError as error:
        target = "/settings?" + urlencode({"publishing_error": str(error)})
    else:
        target = "/settings?publishing_connected=youtube"
    return RedirectResponse(target, status_code=303, headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


def validate_meta_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_.|+-]{10,4096}", token):
        raise HTTPException(422, "Paste the User Token from Meta Graph API Explorer.")


@app.post("/api/publishing/meta/pages")
def list_meta_pages(credentials: MetaToken, _origin: str = Depends(publishing_origin)):
    validate_meta_token(credentials.user_token)
    try:
        return {"pages": PUBLISHING.meta_pages(credentials.user_token)}
    except PublishError as error:
        raise HTTPException(422, str(error)) from None


@app.post("/api/publishing/meta/connect")
def connect_meta(connection: MetaConnection, _origin: str = Depends(publishing_origin)):
    validate_meta_token(connection.user_token)
    try:
        PUBLISHING.connect_meta(connection.page_id, connection.user_token)
        return {"accounts": PUBLISHING.public()}
    except PublishError as error:
        raise HTTPException(422, str(error)) from None


@app.delete("/api/publishing/{service}")
def disconnect_publishing(service: Literal["youtube", "meta"], _origin: str = Depends(publishing_origin)):
    try:
        PUBLISHING.save_connection(service, None)
    except PublishError as error:
        raise HTTPException(503, str(error)) from None
    return {"accounts": PUBLISHING.public()}


@app.put("/api/publishing/defaults/{platform}")
def set_publishing_default(platform: Platform, enabled: bool, _origin: str = Depends(publishing_origin)):
    with SETTINGS_LOCK:
        settings = load_settings()
        destinations = studio_settings(settings).destinations
        entry = destinations.get(platform, Destination())
        entry.enabled = enabled
        destinations[platform] = entry
        save_settings(settings | {"destinations": {key: value.model_dump() for key, value in destinations.items()}})
    return {"enabled": enabled}


@app.post("/api/publishing/batch/{folder_name}")
def queue_publication(folder_name: str, batch: PublishBatch, _origin: str = Depends(publishing_origin)):
    if "youtube" in batch.destinations and batch.made_for_kids is None:
        raise HTTPException(422, "Choose the YouTube audience setting for these uploads.")
    with JOB_QUEUE.gate:
        try:
            return submit_publication(folder_name, batch)
        except PublishError as error:
            raise HTTPException(422, str(error)) from None


def submit_publication(folder_name, batch):
    accounts = PUBLISHING.public()
    for platform, account_id in batch.destinations.items():
        if accounts.get(platform, {}).get("id") != account_id:
            raise PublishError(f"The {PLATFORMS[platform]} connection changed. Reopen publishing and review the destination.")

    jobs, results, seen = [], [], set()
    for clip in batch.clips:
        video = exported_video(folder_name, clip.filename)
        if str(video.stat().st_mtime_ns) != clip.revision:
            raise PublishError(f"{video.name} changed. Refresh the clip library and select the current export.")
        if not clip.title.strip():
            raise PublishError("Enter a title for each selected clip.")
        if video.name in seen:
            continue
        seen.add(video.name)
        if "youtube" in batch.destinations and (len(clip.description.encode("utf-8")) > 5000
                or any(char in clip.title + clip.description for char in "<>")):
            raise PublishError("YouTube accepts up to 5,000 description bytes. Remove angle brackets from the title and description.")
        if "instagram" in batch.destinations and len(clip.description) > 2200:
            raise PublishError("Instagram captions can be up to 2,200 characters.")
        for platform, account_id in batch.destinations.items():
            key = hashlib.sha256(json.dumps((folder_name, video.name, clip.revision, platform, account_id)).encode()).hexdigest()
            receipt = PUBLISHING.receipt(key)
            if receipt.get("complete"):
                results.append({"platform": platform, "filename": video.name, "status": "uploaded", "url": receipt["url"]})
                continue
            submission = {"title": clip.title.strip(), "description": clip.description,
                          "synthetic_media": batch.synthetic_media}
            if platform == "youtube":
                submission.update(visibility=batch.visibility, made_for_kids=batch.made_for_kids)
            if any(receipt.get(field) for field in ("session", "video_id", "container")) and receipt["submission"] != submission:
                raise PublishError(f"{video.name} already has a {PLATFORMS[platform]} upload. Open Tasks to check or resume it with its original posting details.")
            jobs.append({"source_type": "publish", "key": key, "platform": platform, "account_id": account_id,
                         "folder": video.parent.name, "filename": video.name, "revision": clip.revision, "submission": submission})

    for payload in jobs:
        platform = payload["platform"]
        source = f"{PLATFORMS[platform]} · {payload['submission']['title']}"
        try:
            job_id = JOB_QUEUE.submit(source, payload, {}, identity=clip_job_identity)
            status = "queued"
        except DuplicateJob as error:
            job_id = error.job_id
            status = "active"
        results.append({"job_id": job_id, "platform": platform, "filename": payload["filename"], "status": status})
    return {"uploads": results}


@app.get("/api/settings")
def get_settings():
    return studio_settings(load_settings())


@app.put("/api/settings")
def update_settings(preferences: StudioSettings):
    if preferences.music_mode == "replace" and preferences.music_track not in list_music():
        raise HTTPException(422, "Select an available replacement track.")
    with SETTINGS_LOCK:
        fields = preferences.model_dump()
        if "destinations" not in preferences.model_fields_set:
            fields.pop("destinations")
        settings = load_settings() | fields
        save_settings(settings)
    return studio_settings(settings)


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
    inserts = [
        {"id": name, "label": values[0], "hint": values[1], "duration": REACTION_DURATION, "still": True}
        for name, values in REACTIONS.items()
    ]
    for file in asset_files(INSERTS_DIR, INSERT_EXTENSIONS):
        name = "file:" + file.name
        try:
            asset = editor_insert_asset(name)
        except HTTPException:
            continue
        inserts.append({"id": name, "label": file.stem, "hint": "Your library", "duration": asset.duration, "still": False})
    return {
        "music": list_music(),
        "voiceovers": list_voiceovers(),
        "effects": [{"id": name, "label": settings[0], "duration": settings[1]} for name, settings in EFFECTS.items()],
        "looks": [{"id": name, "label": values[0], "css": values[2]} for name, values in LOOKS.items()],
        "frames": [{"id": name, "label": label} for name, label in FRAMES.items()],
        "inserts": inserts,
    }


def editor_insert_asset(name):
    if name in REACTIONS:
        return InsertAsset(REACTION_DURATION)
    filename = name.removeprefix("file:")
    path = (INSERTS_DIR / filename).resolve()
    if (filename == name or path.parent != INSERTS_DIR.resolve() or path.name != filename
            or path.suffix.lower() not in INSERT_EXTENSIONS or not path.is_file()):
        raise HTTPException(404, "That insert is not available in the editor library.")
    try:
        duration, has_audio = inspect_media(path)
    except (av.error.FFmpegError, ValueError) as error:
        raise HTTPException(422, "Could not read the insert video.") from error
    if not .2 <= duration <= 15:
        raise HTTPException(422, "Insert videos must be between 0.2 and 15 seconds.")
    return InsertAsset(duration, has_audio, path)


@app.get("/api/editor-inserts/{name}")
def editor_insert(name: str):
    asset = editor_insert_asset(name)
    if asset.file:
        return FileResponse(asset.file)
    return Response(reaction_gif(name), media_type="image/gif", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/editor-frames/{name}")
def editor_frame(name: str):
    if name not in FRAMES:
        raise HTTPException(404, "Animated frame not found.")
    return Response(frame_gif(name), media_type="image/gif", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/editor-inserts")
def import_editor_insert(file: UploadFile = File(...)):
    name = Path(file.filename or "")
    suffix = name.suffix.lower()
    if suffix not in INSERT_EXTENSIONS:
        raise HTTPException(415, "Choose an MP4 or WebM animation.")
    if file.file.seek(0, os.SEEK_END) > 40 * 1024 * 1024:
        raise HTTPException(413, "Choose an insert smaller than 40 MB.")
    file.file.seek(0)
    INSERTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(name.stem)
    temporary = INSERTS_DIR / f"{uuid.uuid4().hex}.upload"
    try:
        with temporary.open("xb") as target:
            shutil.copyfileobj(file.file, target)
        try:
            duration, _ = inspect_media(temporary)
        except (av.error.FFmpegError, ValueError) as error:
            raise HTTPException(422, "Could not read the insert video.") from error
        if not .2 <= duration <= 15:
            raise HTTPException(422, "Insert videos must be between 0.2 and 15 seconds.")
        with SETTINGS_LOCK:
            destination = INSERTS_DIR / (stem + suffix)
            if destination.exists():
                destination = INSERTS_DIR / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"
            temporary.rename(destination)
        return {"id": "file:" + destination.name, "label": destination.stem, "hint": "Your library",
                "duration": duration, "still": False}
    finally:
        temporary.unlink(missing_ok=True)


@app.get("/api/editor-effects/{name}")
def editor_effect(name: str):
    if name not in EFFECTS:
        raise HTTPException(404, "Sound effect not found.")
    return Response(effect_wave(name), media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/editor-music/{filename}")
def editor_music(filename: str):
    return FileResponse(editor_music_file(filename))


def editor_voiceover_asset(filename):
    path = (VOICEOVERS_DIR / filename).resolve()
    if path.parent != VOICEOVERS_DIR.resolve() or path.name != filename or path.suffix != ".wav" or not path.is_file():
        raise HTTPException(404, "That audio clip is unavailable in your voice-over library.")
    try:
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
    except (OSError, EOFError, wave.Error) as error:
        raise HTTPException(422, "Could not read that audio clip.") from error
    return path, duration


def voiceover_info(path, duration):
    label = path.stem.rsplit("-", 1)[0].replace("_", " ")
    return {"id": path.name, "label": label, "duration": duration}


def list_voiceovers():
    library = []
    for file in asset_files(VOICEOVERS_DIR, {".wav"}):
        try:
            library.append(voiceover_info(*editor_voiceover_asset(file.name)))
        except HTTPException:
            continue
    return library


def save_voiceover(input_path, name, scratch, *, ident=None):
    output = scratch / "voiceover.wav"
    try:
        subprocess.run(
            [get_ffmpeg_cmd(), "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
             "-format_whitelist", "wav,mp3,mov,flac,ogg,aac", "-i", str(input_path), "-map", "0:a:0",
             "-t", str(VOICEOVER_MAX_SECONDS + .1), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)],
            check=True, capture_output=True, timeout=45,
        )
        with wave.open(str(output), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
    except (OSError, subprocess.SubprocessError, EOFError, wave.Error) as error:
        raise HTTPException(422, "Could not read that audio. Try exporting it as WAV or MP3.") from error
    if not .05 <= duration <= VOICEOVER_MAX_SECONDS:
        raise HTTPException(422, "Choose an audio clip between 0.05 seconds and 2 minutes.")
    destination = VOICEOVERS_DIR / f"{sanitize_filename(name)}-{ident or uuid.uuid4().hex[:12]}.wav"
    output.replace(destination)
    return voiceover_info(destination, duration)


@app.get("/api/editor-voiceovers/{filename}")
def editor_voiceover(filename: str):
    path, _ = editor_voiceover_asset(filename)
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/editor-voiceovers")
def import_voiceover(file: UploadFile = File(...)):
    name = Path(file.filename or "")
    if name.suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(415, "Choose an MP3, WAV, M4A, AAC, FLAC or OGG audio file.")
    if file.file.seek(0, os.SEEK_END) > 25 * 1024 * 1024:
        raise HTTPException(413, "Choose an audio file no larger than 25 MB.")
    file.file.seek(0)
    VOICEOVERS_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=VOICEOVERS_DIR) as directory:
        scratch = Path(directory)
        source = scratch / "upload"
        with source.open("xb") as target:
            shutil.copyfileobj(file.file, target)
        return save_voiceover(source, name.stem, scratch)


@app.get("/api/editor-voices")
def editor_voices():
    try:
        return {"voices": installed_voices()}
    except RuntimeError as error:
        return {"voices": [], "message": str(error)}


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=850, pattern=r"\S")
    voice: str = Field(default="", max_length=160)
    rate: int = Field(default=0, ge=-4, le=4)


class StemRequest(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    revision: str = Field(default="", pattern=r"^\d{0,24}$")


class TrackRequest(StemRequest):
    speakers: int = Field(default=0, ge=0, le=8)


class TrackMixRequest(BaseModel):
    keep: list[str] = Field(max_length=18)


@app.post("/api/editor-voices")
def generate_voiceover(request: SpeechRequest):
    VOICEOVERS_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=VOICEOVERS_DIR) as directory:
        scratch = Path(directory)
        source = scratch / "speech.wav"
        try:
            synthesize_speech(source, request.text, request.voice, request.rate)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
        return save_voiceover(source, "Voice-over", scratch)


@app.post("/api/edit/{folder_name}/{filename}/separate-audio")
def separate_clip_audio(folder_name: str, filename: str, request: StemRequest):
    video = exported_video(folder_name, filename)
    revision = str(video.stat().st_mtime_ns)
    if request.revision and request.revision != revision:
        raise HTTPException(409, "The clip has changed. Refresh the library and reopen it.")
    try:
        duration, has_audio = inspect_media(video)
    except (av.error.FFmpegError, ValueError) as error:
        raise HTTPException(422, "Could not read the exported video.") from error
    if not has_audio:
        raise HTTPException(422, "This clip has no audio to separate.")
    stop = min(duration, request.end)
    length = stop - request.start
    if not .5 <= length <= VOICEOVER_MAX_SECONDS or request.end > duration + .02:
        raise HTTPException(422, "Choose 0.5 seconds to 2 minutes inside the source clip.")
    key = hashlib.sha256(f"{video}|{revision}|{request.start:.6f}|{stop:.6f}".encode()).hexdigest()[:20]
    names = {"voice": "Separated voice", "background": "Separated background"}
    try:
        return {kind: voiceover_info(*editor_voiceover_asset(f"{sanitize_filename(name)}-{key}.wav"))
                for kind, name in names.items()}
    except HTTPException:
        pass
    if importlib.util.find_spec("audio_separator") is None:
        raise HTTPException(503, "Audio separation needs a one-time setup. Run setup_audio_separation.bat and restart the studio.")
    if not EDITOR_STEMS_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another audio separation is running. Try again after it finishes.")
    try:
        VOICEOVERS_DIR.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=VOICEOVERS_DIR) as directory:
            scratch = Path(directory)
            source = scratch / "source.wav"
            subprocess.run(
                [get_ffmpeg_cmd(), "-nostdin", "-v", "error", "-ss", str(request.start), "-i", str(video),
                 "-map", "0:a:0", "-t", str(length), "-af", "apad", "-ac", "2", "-ar", "44100", str(source)],
                check=True, capture_output=True, timeout=45,
            )
            subprocess.run(
                [sys.executable, "-m", "core.editor_stems", str(source), str(scratch), str(AUDIO_MODELS_DIR)],
                cwd=BASE_DIR, check=True, capture_output=True, timeout=600,
            )
            if str(video.stat().st_mtime_ns) != revision:
                raise HTTPException(409, "The clip changed during separation. Reopen it and try again.")
            return {kind: save_voiceover(scratch / f"{kind}.wav", name, scratch, ident=key)
                    for kind, name in names.items()}
    except subprocess.TimeoutExpired as error:
        raise HTTPException(504, "Audio separation took too long. Try a shorter range or check the model download.") from error
    except (OSError, subprocess.CalledProcessError) as error:
        details = (getattr(error, "stderr", None) or b"").decode("utf-8", errors="replace").strip()
        LOGGER.error("Audio separation failed: %s", details[-1800:] or type(error).__name__)
        raise HTTPException(503, "Could not separate this range. Check the model download and audio separation setup.") from error
    finally:
        EDITOR_STEMS_LOCK.release()


def audio_breakdown_folder(key):
    if not re.fullmatch(r"[a-f0-9]{24}", key):
        raise HTTPException(404, "Audio analysis is unavailable.")
    return VOICEOVERS_DIR / "track-analysis" / key


def read_audio_breakdown(key):
    folder = audio_breakdown_folder(key)
    manifest = read_json(folder / "tracks.json")
    if not manifest:
        raise HTTPException(404, "Audio analysis is unavailable.")
    return folder, manifest


@app.post("/api/edit/{folder_name}/{filename}/audio-tracks")
def analyze_audio_tracks(folder_name: str, filename: str, request: TrackRequest):
    video = exported_video(folder_name, filename)
    revision = str(video.stat().st_mtime_ns)
    if request.revision and request.revision != revision:
        raise HTTPException(409, "The clip changed. Refresh the library and reopen it.")
    key = hashlib.sha256(f"tracks:1|{video}|{revision}|{request.start}|{request.end}|{request.speakers}".encode()).hexdigest()[:24]
    destination = audio_breakdown_folder(key)
    if destination.is_dir():
        return {"id": key, **read_audio_breakdown(key)[1]}
    if importlib.util.find_spec("sherpa_onnx") is None:
        raise HTTPException(503, "Run setup_audio_separation.bat once to enable speaker and sound tracks, then restart the studio.")
    if not EDITOR_TRACKS_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another audio scan is running. Try again after it finishes.")
    try:
        if destination.is_dir():
            return {"id": key, **read_audio_breakdown(key)[1]}
        source_request = StemRequest(start=request.start, end=request.end, revision=revision)
        stems = separate_clip_audio(folder_name, filename, source_request)
        voice, _ = editor_voiceover_asset(stems["voice"]["id"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination.parent) as directory:
            scratch = Path(directory)
            source = scratch / "source.wav"
            tracks = scratch / "tracks"
            tracks.mkdir()
            subprocess.run(
                [get_ffmpeg_cmd(), "-nostdin", "-v", "error", "-ss", str(request.start), "-i", str(video), "-map", "0:a:0",
                 "-t", str(request.end - request.start), "-af", "apad", "-ar", "48000", "-ac", "2",
                 "-c:a", "pcm_s16le", str(source)], check=True, capture_output=True, timeout=45,
            )
            subprocess.run([sys.executable, "-m", "core.audio_breakdown", str(source), str(voice), str(tracks),
                            str(AUDIO_MODELS_DIR / "track-detection"), str(request.speakers)],
                           cwd=BASE_DIR, check=True, capture_output=True, timeout=180)
            if str(video.stat().st_mtime_ns) != revision:
                raise HTTPException(409, "The clip changed during analysis. Reopen it and try again.")
            tracks.rename(destination)
        return {"id": key, **read_audio_breakdown(key)[1]}
    except subprocess.TimeoutExpired as error:
        raise HTTPException(504, "The audio scan took too long. Try a shorter section.") from error
    except (OSError, subprocess.CalledProcessError) as error:
        details = (getattr(error, "stderr", None) or b"").decode("utf-8", errors="replace").strip()
        LOGGER.error("Audio track analysis failed: %s", details[-1800:] or type(error).__name__)
        raise HTTPException(503, "Could not scan this audio. Check audio separation setup and try a shorter selection.") from error
    finally:
        EDITOR_TRACKS_LOCK.release()


@app.get("/api/audio-tracks/{key}/{filename}")
def listen_audio_track(key: str, filename: str):
    folder, manifest = read_audio_breakdown(key)
    if filename not in {track["id"] for track in manifest["tracks"]} or not (folder / filename).is_file():
        raise HTTPException(404, "That audio track is unavailable.")
    return FileResponse(folder / filename, media_type="audio/wav")


@app.post("/api/audio-tracks/{key}/mix")
def mix_audio_tracks(key: str, request: TrackMixRequest):
    folder, manifest = read_audio_breakdown(key)
    keep = sorted(set(request.keep))
    known = {track["id"] for track in manifest["tracks"]}
    if not keep or not set(keep) <= known:
        raise HTTPException(422, "Choose the audio tracks to keep from this scan.")
    ident = hashlib.sha256(f"{key}|{','.join(keep)}".encode()).hexdigest()[:20]
    output = VOICEOVERS_DIR / f"Filtered-audio-{ident}.wav"
    if not output.is_file():
        samples = read_audio(folder / keep[0])
        for name in keep[1:]:
            samples += read_audio(folder / name)
        np.nan_to_num(samples, copy=False)
        with TemporaryDirectory(dir=VOICEOVERS_DIR) as directory:
            stage = Path(directory) / output.name
            write_audio(stage, samples)
            stage.replace(output)
    return voiceover_info(*editor_voiceover_asset(output.name))


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
        JOB_QUEUE.remove_finished(task_id, include_batch=True)
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


def clip_order(video):
    version = re.fullmatch(r"(.+)-v(\d+)", video.stem)
    return (version[1], int(version[2]), video.name) if version else (video.stem, 1, video.name)


def export_batches():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    running = JOB_QUEUE.running_id()
    for entry in OUTPUT_DIR.iterdir():
        if running and entry.name.endswith(f"_[{running}]"):
            continue
        videos = sorted((file for file in entry.glob("*.mp4") if not file.name.startswith(("source-", "slice_"))), key=clip_order)
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
    try:
        revision = str(video.stat().st_mtime_ns)
    except FileNotFoundError:
        revision = ""
    return {"title": video.stem} | read_json(video.with_suffix(".json")) | {"filename": video.name, "file_revision": revision}


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


@contextmanager
def posting_kit_update(video):
    if not POSTING_KIT_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another posting kit is being updated. Try again when it finishes.")

    try:
        with JOB_QUEUE.gate:
            if not video.is_file():
                raise HTTPException(404, "The clip was removed.")
            revision = video.stat().st_mtime_ns
            metadata = read_json(video.with_suffix(".json"))
        yield metadata
        with JOB_QUEUE.gate:
            if not video.is_file():
                raise HTTPException(404, "The clip was removed while the kit was updating.")
            if video.stat().st_mtime_ns != revision:
                raise HTTPException(409, "The clip changed while the kit was updating. Reopen it and try again.")
            write_clip_metadata(video.with_suffix(".json"), metadata)
    finally:
        POSTING_KIT_LOCK.release()


@app.post("/api/posting-kit/{folder_name}/{filename}/hashtags")
def refresh_posting_hashtags(folder_name: str, filename: str, request: HashtagSearchRequest):
    topic = " ".join(request.topic.split())
    if len(topic) < 2 or not any(char.isalnum() for char in topic):
        raise HTTPException(422, "Enter a short topic to search on YouTube.")
    video = exported_video(folder_name, filename)
    with posting_kit_update(video) as metadata:
        info = read_json(video.parent / "info.json")
        source_url = metadata.get("source_url") or info.get("youtube_url", "")
        try:
            research = research_youtube_hashtags(topic, youtube_video_id(urlparse(source_url)))
        except YouTubeSearchUnavailable:
            raise HTTPException(503, "YouTube search is unavailable. Your saved hashtags are unchanged.") from None
        if not research["entries"]:
            raise HTTPException(404, "No matching hashtags appeared in these YouTube results. Try a more specific topic.")
        metadata["hashtag_research"] = research
    return research


@app.post("/api/posting-kit/{folder_name}/{filename}")
def refresh_posting_kit(folder_name: str, filename: str, request: PostingKitRequest):
    video = exported_video(folder_name, filename)
    with posting_kit_update(video) as metadata:
        info = read_json(video.parent / "info.json")
        label = info.get("source_title") or video.stem
        source_url = metadata.get("source_url") or info.get("youtube_url", "")
        saved_research = metadata.get("hashtag_research", {})
        if request.refresh_hashtags and saved_research.get("topic"):
            try:
                research = research_youtube_hashtags(saved_research["topic"], youtube_video_id(urlparse(source_url)))
                if research["entries"]:
                    metadata["hashtag_research"] = research
            except YouTubeSearchUnavailable:
                pass  # Keep previously sourced tags when YouTube is unavailable.
        context = metadata.get("posting_context") or (
            "" if metadata.get("selection") == "edited" else read_caption_context(video.with_suffix(".ass"))
        )
        copy = generate_post_copy(
            {}, [], label, load_settings(), tuple(list_music()), excerpt=context, notes=request.notes,
            researched_hashtags=tuple(entry["tag"] for entry in metadata.get("hashtag_research", {}).get("entries", [])),
        )
        if copy["copy_source"] == "no_transcript":
            raise HTTPException(422, "Add a note describing what actually happens in this clip, then try again.")
        if copy["copy_source"] != "ai":
            raise HTTPException(503, "The local model did not return a posting kit. Check Ollama and try again.")
        if metadata.get("copy_ready") and not copy["copy_ready"]:
            raise HTTPException(502, "The new draft was incomplete. Your previous kit is still saved.")

        metadata.update(posting_metadata(copy, label, source_url))
        metadata["copy_needs_review"] = False
    return public_clip(metadata | {"filename": video.name})


def next_clip_version(video, base_name):
    pattern = re.compile(rf"{re.escape(base_name)}-v(\d+)", re.IGNORECASE)
    versions = (int(match[1]) for file in video.parent.iterdir() if (match := pattern.fullmatch(file.stem)))
    number = max(versions, default=1) + 1
    return video.with_name(f"{base_name}-v{number}.mp4"), number


def process_edit(payload, update):
    edit = ClipEdit.model_validate(payload)
    video = exported_video(payload["folder"], payload["filename"])
    if edit.source_revision and str(video.stat().st_mtime_ns) != edit.source_revision:
        raise ValueError("The original clip changed while this edit was queued. Reopen it and submit the edit again.")
    music = editor_music_file(edit.music_track) if edit.music_track else None
    source_info = read_json(video.parent / "info.json")
    source_url = source_info.get("youtube_url", "")
    existing = exported_clip(video)
    title = existing["title"]
    base_title = existing.get("version_title") or title
    base_name = existing.get("version_base") or video.stem
    inserts = {cue.asset: editor_insert_asset(cue.asset) for cue in edit.inserts}
    voiceovers = {region.asset: editor_voiceover_asset(region.asset) for region in edit.audio_regions if region.asset}
    duration = edit_duration(edit, inserts)
    detail = f"{duration:.2f} seconds · {len(edit.cues)} sound effects"
    with TemporaryDirectory(dir=video.parent) as scratch:
        staging = Path(scratch)
        temporary = staging / "edit.rendering"
        update("export", "Rendering edit", detail)
        render_edit(video, temporary, edit, music, lambda percent: update("export", "Rendering edit", detail, percent),
                    inserts=inserts, voiceovers=voiceovers)
        with JOB_QUEUE.gate:
            if str(video.stat().st_mtime_ns) != existing["file_revision"]:
                raise ValueError("The clip changed during rendering. Reopen it and try again.")
            if edit.save_mode == "replace":
                output = video
            else:
                output, version = next_clip_version(video, base_name)
                title = f"{base_title} · v{version}"
            update("finalize", "Saving edited clip", output.name)
            sidecar = output.with_suffix(".json")
            backup = staging / "previous.json"
            if sidecar.exists():
                shutil.copyfile(sidecar, backup)
            retained = read_json(sidecar) if edit.save_mode == "replace" else {}
            retained.pop("posting_context", None)
            if retained.get("copy_source") == "ai":
                retained["copy_needs_review"] = True
            kept = [(edit.start + start, edit.start + end) for start, end in kept_source_ranges(edit)]
            transcript = " ".join(
                line.text for line in edit.captions if any(line.end > start and line.start < end for start, end in kept)
            )
            if transcript:
                retained["posting_context"] = transcript[:6500]
            write_clip_metadata(sidecar, retained | {
                "title": title, "video_file": output.name, "selection": "edited", "start_time": 0,
                "end_time": duration, "source_url": source_url,
                "edited_from": {"folder": video.parent.name, "filename": video.name},
                "applied_music": edit.music_track if edit.music_level else "",
                "version_base": base_name, "version_title": base_title,
            })
            try:
                temporary.replace(output)
            except OSError:
                if backup.exists():
                    backup.replace(sidecar)
                else:
                    sidecar.unlink(missing_ok=True)
                raise
    return 1, batch_task_id(video.parent, source_info)


def process_job(job_id, payload, preferences):
    last_report = None
    last_update = 0

    def update(phase, stage, detail, progress=None, source=None):
        nonlocal last_report, last_update
        now = time.monotonic()
        report = (phase, stage, detail, progress, source)
        updating_percent = progress is not None and last_report and last_report[:2] == report[:2] and last_report[3] is not None
        if report == last_report or (updating_percent and now - last_update < 1):
            return
        JOB_QUEUE.report(job_id, phase, stage, detail, progress, source)
        last_report, last_update = report, now

    if payload["source_type"] == "edit":
        return process_edit(payload, update)
    if payload["source_type"] == "publish":
        try:
            video = exported_video(payload["folder"], payload["filename"])
        except HTTPException:
            raise PublishError("The clip was removed before its upload started.") from None
        if payload["platform"] == "facebook":
            duration, _ = inspect_media(video)
            payload["facebook_duration"] = duration
            payload["facebook_page_video"] = not 3 <= duration <= FACEBOOK_REEL_MAX_SECONDS
        receipt = PUBLISHING.upload(payload, video, update)
        return {"url": receipt["url"], "batch_id": batch_task_id(video.parent, read_json(video.parent / "info.json"))}

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


def youtube_video_id(url):
    route = url.path.strip("/").split("/")
    if url.hostname == "youtu.be":
        return route[0]
    if url.path == "/watch":
        return parse_qs(url.query).get("v", [""])[0]
    if len(route) == 2 and route[0] in {"shorts", "embed", "live"}:
        return route[1]
    return ""


def clip_job_identity(request, settings):
    if request["source_type"] == "publish":
        return "publish", request["key"]
    if request["source_type"] == "edit":
        return "edit", request, {}
    if request["source_type"] == "url":
        url = urlparse(request["url"])
        video_id = youtube_video_id(url)
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


def inspect_clip_edit(folder_name, filename, edit):
    video = exported_video(folder_name, filename)
    revision = str(video.stat().st_mtime_ns)
    if edit.source_revision and edit.source_revision != revision:
        raise HTTPException(409, "This clip has changed. Refresh the library and open the editor again.")
    edit.source_revision = revision
    try:
        duration, _ = inspect_media(video)
    except (av.error.FFmpegError, ValueError) as error:
        raise HTTPException(422, "Could not read the exported video.") from error
    if edit.end > duration + .12:
        raise HTTPException(422, "The trim range is outside the exported video.")
    edit.end = min(edit.end, duration)
    try:
        validate_timeline(edit, edit.end - edit.start)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    music = editor_music_file(edit.music_track) if edit.music_track else None
    inserts = {cue.asset: editor_insert_asset(cue.asset) for cue in edit.inserts}
    voiceovers = {region.asset: editor_voiceover_asset(region.asset) for region in edit.audio_regions if region.asset}
    return video, music, inserts, voiceovers


@app.post("/api/edit/{folder_name}/{filename}/preview")
def preview_clip_edit(folder_name: str, filename: str, edit: ClipEdit):
    if not EDITOR_PREVIEW_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another preview is rendering. Try again in a moment.")
    scratch = None
    try:
        video, music, inserts, voiceovers = inspect_clip_edit(folder_name, filename, edit)
        scratch = TemporaryDirectory(prefix="shorts-edit-preview-")
        output = Path(scratch.name) / "preview.mp4"
        render_edit(video, output, edit, music, None, inserts=inserts, voiceovers=voiceovers, preview=True)
        return FileResponse(output, media_type="video/mp4", background=BackgroundTask(scratch.cleanup),
                            headers={"Cache-Control": "no-store"})
    except Exception:
        if scratch:
            scratch.cleanup()
        raise
    finally:
        EDITOR_PREVIEW_LOCK.release()


@app.get("/api/edit/{folder_name}/{filename}/media")
def editor_source_media(folder_name: str, filename: str):
    video = exported_video(folder_name, filename)
    try:
        with av.open(str(video)) as media:
            stream = media.streams.video[0]
            return {"fps": frame_rate(stream)}
    except (av.error.FFmpegError, IndexError) as error:
        raise HTTPException(422, "Could not read the clip frame rate.") from error


@app.post("/api/edit/{folder_name}/{filename}/captions")
def generate_editor_captions(folder_name: str, filename: str, revision: str = ""):
    video = exported_video(folder_name, filename)
    source_revision = str(video.stat().st_mtime_ns)
    if revision and revision != source_revision:
        raise HTTPException(409, "This clip has changed. Refresh the library and open the editor again.")
    try:
        duration, has_audio = inspect_media(video)
    except (av.error.FFmpegError, ValueError) as error:
        raise HTTPException(422, "Could not read this video for captions.") from error
    if not has_audio:
        return {"captions": [], "message": "This clip has no audio track to transcribe."}
    if not EDITOR_CAPTIONS_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Whisper is already generating captions. Try again in a moment.")
    try:
        with TemporaryDirectory(prefix="shorts-captions-") as scratch:
            segments = transcribe_range(video, None, 0, 0, duration, Path(scratch) / "speech.wav", load_settings())
        if not video.is_file() or str(video.stat().st_mtime_ns) != source_revision:
            raise HTTPException(409, "The clip changed during transcription. Reopen it and try again.")
        captions = [{"start": segment["start"], "end": min(duration, segment["end"]), "text": segment["text"][:1000]}
                    for segment in segments[:1500] if segment["end"] > segment["start"] and segment["start"] < duration]
        return {"captions": captions, "message": "" if captions else "Whisper did not return speech. Check the audio and local model."}
    finally:
        EDITOR_CAPTIONS_LOCK.release()


@app.post("/api/edit/{folder_name}/{filename}")
def queue_clip_edit(folder_name: str, filename: str, edit: ClipEdit):
    with JOB_QUEUE.gate:
        video, *_ = inspect_clip_edit(folder_name, filename, edit)
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


@app.post("/api/job/{job_id}/resume")
def resume_upload(job_id: str, _origin: str = Depends(publishing_origin)):
    try:
        if JOB_QUEUE.retry_publish(job_id):
            return {"job_id": job_id}
    except DuplicateJob as error:
        return {"job_id": error.job_id}
    raise HTTPException(409, "This task has no saved upload to resume.")


def launch_studio(open_browser=False):
    host, port = "127.0.0.1", 8000
    url = f"http://{host}:{port}"
    try:
        response = requests.get(url + "/openapi.json", timeout=2)
    except requests.ConnectionError:
        response = None
    except requests.RequestException:
        print(f"Could not verify the service on {url}. Close that instance before launching this studio.")
        return 1

    if response is not None:
        try:
            schema = response.json()
        except ValueError:
            schema = {}
        paths = schema.get("paths", {}) if isinstance(schema, dict) else {}
        required_routes = {
            "/api/edit/{folder_name}/{filename}/audio-tracks",
            "/api/posting-kit/{folder_name}/{filename}/hashtags",
            "/api/publishing",
            "/api/publishing/meta/pages",
            "/api/job/{job_id}/resume",
        }
        if not response.ok or not required_routes.issubset(paths):
            print(f"Port {port} is in use by an older studio or another app. Close it before launching this version.")
            return 1
        print(f"The studio is already running at {url}. Using the existing instance.")
        if open_browser:
            webbrowser.open(url)
        return 0

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
    finished = threading.Event()

    def open_when_ready():
        while not finished.wait(.1):
            if server.started:
                webbrowser.open(url)
                return

    if open_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run()
        return 0 if server.started else 1
    finally:
        finished.set()


if __name__ == "__main__":
    sys.exit(launch_studio(open_browser="--open-browser" in sys.argv[1:]))
