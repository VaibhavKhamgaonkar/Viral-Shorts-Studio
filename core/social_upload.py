import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode, urlsplit

import av
import requests

from core.job_queue import JobDeferred
from core.publisher import write_clip_metadata
from core.transformer import CPU_ENCODER, get_ffmpeg_cmd, run_encoder


PLATFORMS = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook"}
META_PAGE_PERMISSIONS = {"pages_show_list", "pages_read_engagement"}
META_REEL_PERMISSIONS = {
    "facebook": META_PAGE_PERMISSIONS | {"pages_manage_posts"},
    "instagram": META_PAGE_PERMISSIONS | {"instagram_basic", "instagram_content_publish"},
}
YOUTUBE = "https://www.googleapis.com"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
META_VERSION = "v27.0"
META = "https://graph.facebook.com/" + META_VERSION
META_VIDEO = "https://graph-video.facebook.com/" + META_VERSION
FACEBOOK_REEL_MAX_SECONDS = 90
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
CHUNK_SIZE = 8 * 1024 * 1024
FACEBOOK_CHECK_DELAY = 30
INSTAGRAM_MAX_BYTES = 300_000_000
META_RECONNECT = "The Facebook and Instagram connection has expired. Reconnect the same Page in Settings, then resume the upload."


class PublishError(Exception):
    pass


class ExpiredConnection(PublishError):
    pass


def http(method, url, *, token=None, scheme="Bearer", headers=None, timeout=(10, 120), **options):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"{scheme} {token}"
    try:
        return requests.request(method, url, headers=headers, timeout=timeout, allow_redirects=False, **options)
    except requests.RequestException:
        raise PublishError("The platform did not respond. Retry this upload from the clip library.") from None


def json_response(response):
    try:
        data = response.json()
    except ValueError:
        data = {}
    if 200 <= response.status_code < 300 and isinstance(data, dict) and not data.get("error"):
        return data
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if response.status_code == 401 or code in (102, 190) or error == "invalid_grant":
        raise ExpiredConnection("The connection has expired. Reconnect the account in Settings and retry.")
    if response.status_code == 403 or code in (10, 200):
        raise PublishError("The platform denied access. Check the account's publishing permissions and API setup.")
    suffix = f", platform error {code}" if isinstance(code, int) and code != response.status_code else ""
    raise PublishError(f"The platform rejected the request (HTTP {response.status_code}{suffix}).")


def read_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    raise PublishError("The local publishing file could not be read.")


def service_id(value, pattern=r"[0-9]+"):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise PublishError("The platform did not return a valid ID.")
    return value


def graph(method, path, token, **options):
    return json_response(http(method, f"{META}/{path}", token=token, **options))


class PublishStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.youtube_pending = {}

    def connections(self):
        return read_object(self.directory / "connections.json")

    def save_connection(self, platform, connection):
        with self.lock:
            saved = self.connections()
            if connection is None:
                saved.pop(platform, None)
            else:
                saved[platform] = connection
            self.directory.mkdir(parents=True, exist_ok=True)
            write_clip_metadata(self.directory / "connections.json", saved)

    def account(self, platform, saved=None):
        saved = self.connections() if saved is None else saved
        if platform == "youtube":
            return saved.get("youtube", {})
        return saved.get("meta", {}).get(platform, {})

    def public(self):
        saved = self.connections()
        return {
            platform: {"id": account["id"], "name": account["name"]}
            for platform in PLATFORMS if (account := self.account(platform, saved))
        }

    def instagram_setup_status(self):
        return self.connections().get("meta", {}).get("instagram_setup")

    def meta_connection_status(self):
        meta = self.connections().get("meta", {})
        if not meta.get("token"):
            return {"state": "disconnected"}
        try:
            self.verify_meta(meta)
        except ExpiredConnection as error:
            return {"state": "expired", "detail": str(error)}
        except PublishError:
            return {"state": "unverified", "detail": "Could not check the Meta connection. Try again in Settings or reopen Share."}
        return {"state": "ready"}

    @staticmethod
    def verify_meta(meta):
        if not meta.get("token"):
            raise ExpiredConnection(META_RECONNECT)
        try:
            profile = graph("GET", "me", meta["token"], params={"fields": "id"}, timeout=(5, 8))
        except ExpiredConnection:
            raise ExpiredConnection(META_RECONNECT) from None
        expected = meta.get("page_id") or meta.get("facebook", {}).get("id")
        if not profile.get("id") or (expected and profile["id"] != expected):
            raise ExpiredConnection(META_RECONNECT)

    @staticmethod
    def extend_meta_token(user_token, app_id, app_secret):
        try:
            result = graph("GET", "oauth/access_token", None, params={
                "grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": app_secret, "fb_exchange_token": user_token,
            }, timeout=(5, 15))
        except ExpiredConnection:
            raise PublishError("Generate a fresh User Access Token in Graph API Explorer before extending it.") from None
        except PublishError:
            raise PublishError("Meta could not extend the token. Check the App ID and App Secret for the app selected in Explorer.") from None
        if not result.get("access_token"):
            raise PublishError("Meta did not return a longer-lived token. Generate a fresh token and try again.")
        return result["access_token"]

    def start_youtube(self, client, redirect):
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        with self.lock:
            self.youtube_pending = {state: (client, redirect, verifier, time.monotonic() + 600)}
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(YOUTUBE_SCOPES), "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent select_account",
        })

    def finish_youtube(self, state, code, redirect):
        with self.lock:
            pending = self.youtube_pending.pop(state, None)
        if not pending or pending[1] != redirect or pending[3] < time.monotonic():
            raise PublishError("This sign-in expired. Start again in Settings.")
        if not code:
            raise PublishError("YouTube authorization was cancelled.")
        client, redirect, verifier, _ = pending
        token = json_response(http("POST", GOOGLE_TOKEN, data=client | {
            "code": code, "redirect_uri": redirect, "code_verifier": verifier, "grant_type": "authorization_code",
        }))
        if not set(YOUTUBE_SCOPES).issubset(token.get("scope", "").split()) or not token.get("refresh_token") or not token.get("access_token"):
            raise PublishError("Allow both requested permissions so the studio can identify the channel and upload.")
        channels = json_response(http("GET", YOUTUBE + "/youtube/v3/channels", token=token["access_token"],
                                      params={"part": "snippet", "mine": "true"})).get("items", [])
        if len(channels) != 1:
            raise PublishError("Sign in with an account that resolves to one YouTube channel.")
        channel = channels[0]
        self.save_connection("youtube", {
            "id": service_id(channel.get("id"), r"[A-Za-z0-9_-]{8,40}"),
            "name": str(channel.get("snippet", {}).get("title") or "YouTube channel"),
            "client": client, "refresh_token": token["refresh_token"],
        })

    def meta_pages(self, user_token):
        fields = {"fields": "id,name,tasks", "limit": 100}
        publish_tasks = {"CREATE_CONTENT", "MANAGE", "PROFILE_PLUS_CREATE_CONTENT", "PROFILE_PLUS_FULL_CONTROL", "PROFILE_PLUS_MANAGE"}
        pages = {}
        while True:
            result = graph("GET", "me/accounts", user_token, params=fields)
            for page in result.get("data", []):
                page_id = page.get("id", "")
                if re.fullmatch(r"[0-9]+", page_id) and publish_tasks.intersection(page.get("tasks") or []):
                    pages[page_id] = {"id": page_id, "name": page.get("name") or "Facebook Page"}
            paging = result.get("paging", {})
            cursor = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not cursor or cursor == fields.get("after"):
                break
            fields["after"] = cursor
        if not pages:
            raise PublishError("Meta did not return any Pages you can publish to. Generate a User Token with pages_show_list and grant access to your Page.")
        return sorted(pages.values(), key=lambda page: page["name"].casefold())

    def connect_meta(self, page_id, user_token):
        permissions = graph("GET", "me/permissions", user_token).get("data", [])
        granted = {item.get("permission") for item in permissions if item.get("status") == "granted"}
        missing = {platform: required - granted for platform, required in META_REEL_PERMISSIONS.items()}
        facebook = not missing["facebook"]
        instagram = not missing["instagram"]
        if not (facebook or instagram):
            details = "\n".join(
                f"{PLATFORMS[platform]} needs {', '.join(sorted(names))}." for platform, names in missing.items()
            )
            raise PublishError("This token is missing publishing permissions.\n"
                               + details
                               + "\nIn Graph API Explorer, add the permissions for the platform you want and generate a new User Access Token.")
        previous = self.connections().get("meta", {})
        if (previous.get("page_id") or previous.get("facebook", {}).get("id")) == page_id:
            for platform in ("facebook", "instagram"):
                if previous.get(platform) and missing[platform]:
                    name = PLATFORMS[platform]
                    names = ", ".join(sorted(missing[platform]))
                    raise PublishError(f"To keep {name} connected, include these permissions in the new token: {names}.")
        page = graph("GET", page_id, user_token, params={"fields": "id,name,access_token"})
        if page.get("id") != page_id or not page.get("access_token"):
            raise PublishError("Use a User token for an account that manages this Facebook Page.")

        saved = {"token": page["access_token"], "page_id": page_id}
        if facebook:
            saved["facebook"] = {"id": page_id, "name": page.get("name") or "Facebook Page"}
        if not instagram:
            saved["instagram_setup"] = {"state": "permissions", "missing": sorted(missing["instagram"])}
        else:
            try:
                account = graph("GET", page_id, user_token,
                                params={"fields": "instagram_business_account{id,username}"}).get("instagram_business_account")
                if account:
                    saved["instagram"] = {"id": service_id(account.get("id")), "name": "@" + account["username"]}
                elif facebook:
                    saved["instagram_setup"] = {"state": "unlinked"}
                else:
                    raise PublishError("Meta did not return a linked professional Instagram account for this Page.")
            except PublishError:
                if not facebook:
                    raise
                saved["instagram_setup"] = {"state": "lookup_failed"}
        self.save_connection("meta", saved)

    def receipt(self, key):
        return read_object(self.directory / "uploads" / (key + ".json"))

    def save_receipt(self, key, receipt):
        directory = self.directory / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        write_clip_metadata(directory / (key + ".json"), receipt)

    def facebook_upload_copy(self, video, duration, size, copy, report):
        if copy.is_file():
            return True
        if not duration or size < 25_000_000 or size * 8 / duration <= 7_000_000:
            return False
        return encode_upload_copy(video, duration, copy, size - 1, "Facebook", report, [
            "-maxrate", "4M", "-bufsize", "8M",
        ])

    def upload(self, payload, video, update):
        platform, key = payload["platform"], payload["key"]
        receipt = self.receipt(key)
        if receipt.get("complete"):
            return receipt
        saved = self.connections()
        account = self.account(platform, saved)
        if account.get("id") != payload["account_id"]:
            raise PublishError("Reconnect the destination shown on this task before retrying.")
        if not any(receipt.get(field) for field in ("session", "video_id", "container")):
            receipt["submission"] = payload["submission"]
            self.save_receipt(key, receipt)

        def checkpoint(**values):
            receipt.update(values)
            self.save_receipt(key, receipt)

        source = video
        info = video.stat()
        if str(info.st_mtime_ns) != payload["revision"] or not info.st_size:
            raise PublishError("This export changed. Reopen the clip library and select the current version.")
        copy = self.directory / "uploads" / (key + ".mp4")
        confirming = (platform == "facebook" and receipt.get("finish_accepted")) or (
            platform == "instagram" and receipt.get("transferred")
        )
        if not confirming:
            update("source", "Authorizing destination", account["name"])
            if platform in {"facebook", "instagram"}:
                self.verify_meta(saved["meta"])
        if platform == "facebook":
            if not receipt.get("video_id") and self.facebook_upload_copy(
                video, payload.get("facebook_duration"), info.st_size, copy, update,
            ):
                checkpoint(optimized=True)
            if receipt.get("optimized"):
                if not copy.is_file():
                    raise PublishError("The saved Facebook upload copy is missing. Export a new version to start a new upload.")
                source = copy
        elif platform == "instagram":
            token = saved["meta"]["token"]
            if not receipt.get("instagram_copy"):
                if instagram_original_received(receipt, info.st_size, token):
                    if not receipt.get("transferred"):
                        checkpoint(transferred=True)
                else:
                    prepare_instagram_copy(video, copy, update)
                    checkpoint(instagram_copy=True, container=None, instagram_uri=None, transferred=False)
            if receipt.get("instagram_copy"):
                if not copy.is_file():
                    raise PublishError("The saved Instagram upload copy is missing. Export a new version to start a new upload.")
                source = copy
        with source.open("rb") as media:
            opened = os.fstat(media.fileno())
            if source == video and str(opened.st_mtime_ns) != payload["revision"]:
                raise PublishError("This export changed. Reopen the clip library and select the current version.")
            size = opened.st_size
            if platform == "youtube":
                token = json_response(http("POST", GOOGLE_TOKEN, data=account["client"] | {
                    "refresh_token": account["refresh_token"], "grant_type": "refresh_token",
                })).get("access_token")
                if not token:
                    raise PublishError("Google did not return an upload token. Reconnect YouTube.")
                url = youtube_upload(media, size, token, receipt, checkpoint, update)
            elif platform == "facebook":
                uploader = facebook_page_video_upload if payload.get("facebook_page_video") else facebook_upload
                url = uploader(media, size, account["id"], saved["meta"]["token"], receipt, checkpoint, update)
            else:
                url = instagram_upload(media, size, account["id"], saved["meta"]["token"],
                                       receipt, checkpoint, update)
        completed = {"complete": True, "url": url}
        self.save_receipt(key, completed)
        if receipt.get("optimized") or receipt.get("instagram_copy"):
            try:
                copy.unlink(missing_ok=True)
            except OSError:
                pass
        return completed


def encode_upload_copy(video, duration, copy, size_limit, platform, report, video_options):
    stage = f"Preparing {platform} upload copy"
    detail = f"Encoding an MP4 for {platform}."
    report("export", stage, detail, 0)
    with NamedTemporaryFile(dir=copy.parent, suffix=".mp4", delete=False) as placeholder:
        temporary = Path(placeholder.name).resolve()
    try:
        command = [get_ffmpeg_cmd(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video.resolve()),
                   "-map", "0:v:0", "-map", "0:a:0?", *CPU_ENCODER, "-pix_fmt", "yuv420p", *video_options,
                   "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(temporary)]
        result, _ = run_encoder(command, copy.parent, duration, lambda pct: report("export", stage, detail, pct))
        if result == 0 and 0 < temporary.stat().st_size <= size_limit:
            temporary.replace(copy)
            return True
    except OSError:
        pass
    finally:
        temporary.unlink(missing_ok=True)
    return False


def prepare_instagram_copy(video, copy, report):
    if copy.is_file():
        return
    with av.open(str(video)) as media:
        stream = media.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else (media.duration or 0) / av.time_base
        rate = stream.average_rate or stream.base_rate or 30
    if not 3 <= duration <= 900:
        raise PublishError("Instagram Reels must be between 3 seconds and 15 minutes. Trim the clip in the editor.")
    rate = rate if 23 <= rate <= 60 else 60 if rate > 60 else 30
    # Leave room for AAC, muxing overhead and short bitrate bursts.
    bitrate = min(4_000_000, int(280_000_000 * 8 / duration) - 128_000)
    options = ["-vf", "scale='min(iw,1920)':-2", "-r", str(rate), "-g", "120", "-x264-params", "open-gop=0",
               "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2), "-ar", "48000", "-ac", "2", "-use_editlist", "0"]
    if not encode_upload_copy(video, duration, copy, INSTAGRAM_MAX_BYTES, "Instagram", report, options):
        raise PublishError("The studio could not prepare an Instagram-compatible MP4. Try a shorter export and resume.")


def upload_location(value, hostname):
    try:
        url = urlsplit(value or "")
        if url.scheme == "https" and url.netloc == hostname and url.path:
            return value
    except ValueError:
        pass
    raise PublishError("The platform did not return a usable upload URL.")


def youtube_upload(media, size, token, receipt, checkpoint, update):
    session = receipt.get("session")
    if session:
        session = upload_location(session, "www.googleapis.com")
        response = http("PUT", session, token=token, headers={"Content-Range": f"bytes */{size}"}, data=b"")
        if response.status_code in (404, 410) and not receipt.get("final_chunk_sent"):
            checkpoint(session=None)
            session = None
        elif response.status_code != 308:
            return youtube_result(response)
        else:
            offset = uploaded_offset(response)
    if not session:
        meta = receipt["submission"]
        response = http("POST", YOUTUBE + "/upload/youtube/v3/videos", token=token,
                        params={"uploadType": "resumable", "part": "snippet,status"},
                        headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                        json={"snippet": {"title": meta["title"], "description": meta["description"], "categoryId": "22"},
                              "status": {"privacyStatus": meta["visibility"], "selfDeclaredMadeForKids": meta["made_for_kids"],
                                         "containsSyntheticMedia": meta["synthetic_media"]}})
        json_response(response)
        session = upload_location(response.headers.get("Location"), "www.googleapis.com")
        checkpoint(session=session)
        offset = 0
    while 0 <= offset < size:
        media.seek(offset)
        chunk = media.read(CHUNK_SIZE)
        if not chunk:
            break
        end = offset + len(chunk) - 1
        update("export", "Uploading MP4", "Sending video to YouTube.", int(offset * 100 / size))
        if end + 1 == size:
            checkpoint(final_chunk_sent=True)
        response = http("PUT", session, token=token, data=chunk,
                        headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {offset}-{end}/{size}"})
        if response.status_code != 308:
            return youtube_result(response)
        if end + 1 == size:
            checkpoint(final_chunk_sent=False)
        next_offset = uploaded_offset(response)
        if next_offset <= offset:
            break
        offset = next_offset
    raise PublishError("YouTube did not confirm the upload. Retry to check the same upload session.")


def uploaded_offset(response):
    match = re.fullmatch(r"bytes=0-(\d+)", response.headers.get("Range", ""))
    return int(match[1]) + 1 if match else 0


def youtube_result(response):
    if response.status_code in (404, 410):
        raise PublishError("The upload session expired. Check YouTube Studio before submitting a new version.")
    video_id = service_id(json_response(response).get("id"), r"[A-Za-z0-9_-]{11}")
    return f"https://studio.youtube.com/video/{video_id}/edit"


def meta_transfer(media, size, url, token, update, offset=0):
    media.seek(offset)
    update("export", "Uploading MP4", f"Sending {size / 1_000_000:.1f} MB to Meta.", int(offset * 100 / size) if offset else None)
    response = http("POST", upload_location(url, "rupload.facebook.com"), token=token, scheme="OAuth",
                    headers={"offset": str(offset), "file_size": str(size), "Content-Type": "application/octet-stream",
                             "Content-Length": str(size - offset)}, data=media)
    if response.status_code == 400 and urlsplit(url).path.startswith("/ig-api-upload/"):
        try:
            debug = response.json().get("debug_info", {})
        except (AttributeError, ValueError):
            debug = None
        if isinstance(debug, dict) and debug.get("type") == "ProcessingFailedError":
            raise PublishError("Instagram rejected the MP4 during transfer. Open Tasks and resume the upload to retry the saved copy.")
    if json_response(response).get("success") is not True:
        raise PublishError("Meta did not confirm the video transfer. Retry this task from the clip library.")


def facebook_upload(media, size, page_id, token, receipt, checkpoint, update):
    endpoint = page_id + "/video_reels"
    video_id = receipt.get("video_id")
    if not video_id:
        session = graph("POST", endpoint, token, data={"upload_phase": "start"})
        video_id = service_id(session.get("video_id"))
        checkpoint(video_id=video_id, session=upload_location(session.get("upload_url"), "rupload.facebook.com"))
        status = {}
    elif receipt.get("finish_accepted"):
        return facebook_confirmation(video_id, token, update)
    else:
        status = graph("GET", video_id, token, params={"fields": "status"}).get("status", {})
    publishing = status.get("publishing_phase", {})
    if publishing.get("publish_status") != "published":
        if not receipt.get("transferred"):
            uploading = status.get("uploading_phase", {})
            offset = uploading.get("bytes_transfered", 0)
            offset = offset if isinstance(offset, int) and 0 <= offset <= size else 0
            if offset < size and uploading.get("status") not in {"complete", "completed"}:
                meta_transfer(media, size, receipt["session"], token, update, offset)
            checkpoint(transferred=True)
        update("finalize", "Confirming Facebook publication", "Waiting for Facebook to confirm publication.")
        if publishing.get("status") != "in_progress":
            meta = receipt["submission"]
            result = graph("POST", endpoint, token, data={"upload_phase": "finish", "video_id": video_id,
                           "video_state": "PUBLISHED", "title": meta["title"], "description": meta["description"],
                           "is_ai_generated": str(meta["synthetic_media"]).lower()})
            if result.get("success") is not True:
                raise PublishError("Facebook did not accept the publish request. Check the connected Page and retry.")
        checkpoint(finish_accepted=True)
        return facebook_confirmation(video_id, token, update)
    return f"https://www.facebook.com/reel/{video_id}"


def facebook_page_video_upload(media, size, page_id, token, receipt, checkpoint, update):
    endpoint = f"{META_VIDEO}/{page_id}/videos"
    session = receipt.get("page_video_session")
    if not session:
        result = json_response(http("POST", endpoint, token=token, data={"upload_phase": "start", "file_size": size}))
        session = service_id(result.get("upload_session_id"))
        checkpoint(page_video_session=session, video_id=service_id(result.get("video_id")),
                   offsets=facebook_video_offsets(result, size))

    start, end = receipt["offsets"]
    while start < end and not receipt.get("finish_accepted"):
        media.seek(start)
        chunk = media.read(end - start)
        if len(chunk) != end - start:
            raise PublishError("The local MP4 ended before Facebook received the complete file.")
        detail = f"{start / 1_000_000:.1f} of {size / 1_000_000:.1f} MB sent to Facebook."
        update("export", "Uploading MP4", detail, int(start * 100 / size))
        result = json_response(http("POST", endpoint, token=token, data={
            "upload_phase": "transfer", "upload_session_id": session, "start_offset": start,
        }, files={"video_file_chunk": ("chunk.mp4", chunk, "application/octet-stream")}))
        offsets = facebook_video_offsets(result, size)
        if offsets[0] <= start:
            raise PublishError("Facebook did not advance this upload. Retry to resume the same video.")
        start, end = offsets
        checkpoint(offsets=offsets)
    if start != size:
        raise PublishError("Facebook did not confirm the complete video transfer.")

    if not receipt.get("finish_accepted"):
        update("finalize", "Confirming Facebook publication", "Waiting for Facebook to confirm publication.")
        meta = receipt["submission"]
        description = meta["description"]
        if meta["synthetic_media"]:
            description = (description + "\n\nContains AI-generated or significantly altered media.").strip()
        result = json_response(http("POST", endpoint, token=token, data={
            "upload_phase": "finish", "upload_session_id": session, "published": "true",
            "title": meta["title"], "description": description,
        }))
        if result.get("success") is not True:
            raise PublishError("Facebook did not accept the Page video publish request.")
        checkpoint(finish_accepted=True)

    return facebook_confirmation(receipt["video_id"], token, update, page_video=True)


def facebook_confirmation(video_id, token, update, *, page_video=False):
    video = graph("GET", video_id, token, params={"fields": "status,published,permalink_url"})
    status = video.get("status", {})
    publishing = status.get("publishing_phase", {})
    if video.get("published") is True or publishing.get("publish_status") == "published":
        if page_video:
            try:
                return upload_location(video.get("permalink_url"), "www.facebook.com")
            except PublishError:
                return f"https://www.facebook.com/watch/?v={video_id}"
        return f"https://www.facebook.com/reel/{video_id}"
    if "error" in (status.get("video_status"), publishing.get("status"), publishing.get("publish_status")):
        raise PublishError("Facebook could not process this video. Check the upload in Facebook before sharing again.")
    percent = status.get("processing_progress")
    progress = percent if type(percent) is int and 0 <= percent <= 100 else None
    update("finalize", "Confirming Facebook publication", "The MP4 is on Facebook. The studio will check again automatically.", progress)
    raise JobDeferred(FACEBOOK_CHECK_DELAY)


def facebook_video_offsets(result, size):
    try:
        start, end = int(result["start_offset"]), int(result["end_offset"])
    except (KeyError, ValueError, TypeError):
        raise PublishError("Facebook did not return valid upload offsets.") from None
    if not 0 <= start <= end <= size:
        raise PublishError("Facebook did not return valid upload offsets.")
    return start, end


def instagram_upload(media, size, account_id, token, receipt, checkpoint, update):
    container = receipt.get("container")
    status = ""
    if container:
        status = graph("GET", container, token, params={"fields": "status_code"}).get("status_code")
        if status == "PUBLISHED":
            return ""
    if not container or status == "EXPIRED":
        result = graph("POST", account_id + "/media", token, data={"media_type": "REELS", "upload_type": "resumable",
                       "caption": receipt["submission"]["description"],
                       "is_ai_generated": str(receipt["submission"]["synthetic_media"]).lower()})
        container = service_id(result.get("id"))
        uri = upload_location(result.get("uri"), "rupload.facebook.com")
        checkpoint(container=container, instagram_uri=uri, transferred=False, processing_checks=0)
        offset = 0
        status = ""
    else:
        uri = instagram_upload_uri(receipt)
        offset = None
    if status == "ERROR":
        raise PublishError("Instagram could not process this video. Edit or re-export it and try the new version.")
    if status != "FINISHED" and not receipt.get("transferred"):
        if offset is None:
            offset = instagram_offset(uri, token, size)
        if offset < size:
            meta_transfer(media, size, uri, token, update, offset)
        checkpoint(transferred=True)
        status = graph("GET", container, token, params={"fields": "status_code"}).get("status_code")
    if status == "PUBLISHED":
        return ""
    update("finalize", "Publishing Instagram Reel", "Waiting for Instagram to process the video.")
    if status in {"ERROR", "EXPIRED"}:
        raise PublishError("Instagram could not process this Reel. Check the export and try again.")
    if status != "FINISHED":
        attempts = receipt.get("processing_checks", 0) + 1
        if attempts >= 5:
            checkpoint(processing_checks=0)
            raise PublishError("Instagram is still processing the Reel. Resume upload to check the same container.")
        checkpoint(processing_checks=attempts)
        raise JobDeferred(60)
    published = graph("POST", account_id + "/media_publish", token, data={"creation_id": container})
    media_id = service_id(published.get("id"))
    try:
        permalink = graph("GET", media_id, token, params={"fields": "permalink"}).get("permalink", "")
        return upload_location(permalink, "www.instagram.com")
    except PublishError:
        return ""


def instagram_upload_uri(receipt):
    uri = receipt.get("instagram_uri") or f"https://rupload.facebook.com/ig-api-upload/{META_VERSION}/{receipt['container']}"
    return upload_location(uri, "rupload.facebook.com")


def instagram_offset(uri, token, size):
    response = http("GET", uri, token=token, scheme="OAuth", timeout=(5, 15))
    offset = json_response(response).get("offset")
    if type(offset) is not int or not 0 <= offset <= size:
        raise PublishError("Instagram did not return a valid transfer position. Resume upload to check again.")
    return offset


def instagram_original_received(receipt, size, token):
    container = receipt.get("container")
    if not container:
        return False
    status = graph("GET", container, token, params={"fields": "status_code"}).get("status_code")
    if status in {"FINISHED", "PUBLISHED"}:
        return True
    return status == "IN_PROGRESS" and (
        receipt.get("transferred") or instagram_offset(instagram_upload_uri(receipt), token, size) == size
    )
