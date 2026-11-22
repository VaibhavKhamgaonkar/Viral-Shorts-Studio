import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

from core.publisher import write_clip_metadata


PLATFORMS = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook"}
YOUTUBE = "https://www.googleapis.com"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
META_VERSION = "v27.0"
META = "https://graph.facebook.com/" + META_VERSION
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
CHUNK_SIZE = 8 * 1024 * 1024


class PublishError(Exception):
    pass


def http(method, url, *, token=None, scheme="Bearer", headers=None, **options):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"{scheme} {token}"
    try:
        return requests.request(method, url, headers=headers, timeout=(10, 120), allow_redirects=False, **options)
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
    if response.status_code == 401 or code == 190 or error == "invalid_grant":
        raise PublishError("The connection has expired. Reconnect the account in Settings and retry.")
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

    def connect_meta(self, page_id, user_token):
        permissions = graph("GET", "me/permissions", user_token).get("data", [])
        granted = {item.get("permission") for item in permissions if item.get("status") == "granted"}
        facebook = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}.issubset(granted)
        instagram = {"pages_read_engagement", "instagram_basic", "instagram_content_publish"}.issubset(granted)
        if not (facebook or instagram):
            raise PublishError("Grant Facebook or Instagram publishing permissions in your Meta app.")
        page = graph("GET", page_id, user_token, params={"fields": "id,name,access_token"})
        if page.get("id") != page_id or not page.get("access_token"):
            raise PublishError("Use a User token for an account that manages this Facebook Page.")

        saved = {"token": page["access_token"]}
        if facebook:
            saved["facebook"] = {"id": page_id, "name": page.get("name") or "Facebook Page"}
        if instagram:
            account = graph("GET", page_id, user_token,
                            params={"fields": "instagram_business_account{id,username}"}).get("instagram_business_account")
            if account:
                saved["instagram"] = {"id": service_id(account.get("id")), "name": "@" + account["username"]}
            elif not facebook:
                raise PublishError("Link an Instagram professional account to this Facebook Page first.")
        if len(saved) == 1:
            raise PublishError("No publishing destination could be connected.")
        self.save_connection("meta", saved)

    def receipt(self, key):
        return read_object(self.directory / "uploads" / (key + ".json"))

    def save_receipt(self, key, receipt):
        directory = self.directory / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        write_clip_metadata(directory / (key + ".json"), receipt)

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

        with video.open("rb") as media:
            info = os.fstat(media.fileno())
            if str(info.st_mtime_ns) != payload["revision"] or not info.st_size:
                raise PublishError("This export changed. Reopen the clip library and select the current version.")
            update("source", "Authorizing destination", account["name"])
            if platform == "youtube":
                token = json_response(http("POST", GOOGLE_TOKEN, data=account["client"] | {
                    "refresh_token": account["refresh_token"], "grant_type": "refresh_token",
                })).get("access_token")
                if not token:
                    raise PublishError("Google did not return an upload token. Reconnect YouTube.")
                url = youtube_upload(media, info.st_size, token, receipt, checkpoint, update)
            elif platform == "facebook":
                url = facebook_upload(media, info.st_size, account["id"], saved["meta"]["token"],
                                      receipt, checkpoint, update)
            else:
                url = instagram_upload(media, info.st_size, account["id"], saved["meta"]["token"],
                                       receipt, checkpoint, update)
        completed = {"complete": True, "url": url}
        self.save_receipt(key, completed)
        return completed


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
    update("export", "Uploading MP4", "Sending video to Meta.", int(offset * 100 / size) if offset else None)
    response = http("POST", upload_location(url, "rupload.facebook.com"), token=token, scheme="OAuth",
                    headers={"offset": str(offset), "file_size": str(size), "Content-Type": "application/octet-stream",
                             "Content-Length": str(size - offset)}, data=media)
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
        update("finalize", "Publishing Reel", "Waiting for Facebook to confirm publication.")
        if publishing.get("status") != "in_progress" and not receipt.get("finish_accepted"):
            meta = receipt["submission"]
            result = graph("POST", endpoint, token, data={"upload_phase": "finish", "video_id": video_id,
                           "video_state": "PUBLISHED", "title": meta["title"], "description": meta["description"],
                           "is_ai_generated": str(meta["synthetic_media"]).lower()})
            if result.get("success") is not True:
                raise PublishError("Facebook did not accept the publish request. Check the connected Page and retry.")
            checkpoint(finish_accepted=True)
        for _ in range(15):
            status = graph("GET", video_id, token, params={"fields": "status"}).get("status", {})
            publishing = status.get("publishing_phase", {})
            if publishing.get("publish_status") == "published":
                break
            if "error" in (status.get("video_status"), publishing.get("status"), publishing.get("publish_status")):
                raise PublishError("Facebook could not process this Reel. Check its format and Page settings.")
            time.sleep(4)
        else:
            raise PublishError("Facebook is still processing the Reel. Retry to check its status.")
    return f"https://www.facebook.com/reel/{video_id}"


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
        checkpoint(container=container, transferred=False)
        status = ""
    if status == "ERROR":
        raise PublishError("Instagram could not process this video. Edit or re-export it and try the new version.")
    if status != "FINISHED" and not receipt.get("transferred"):
        meta_transfer(media, size, f"https://rupload.facebook.com/ig-api-upload/{META_VERSION}/{container}", token, update)
        checkpoint(transferred=True)
    update("finalize", "Publishing Reel", "Waiting for Instagram to process the video.")
    for attempt in range(5):
        if status == "FINISHED":
            break
        if attempt:
            time.sleep(60)
        status = graph("GET", container, token, params={"fields": "status_code"}).get("status_code")
        if status == "PUBLISHED":
            return ""
        if status in {"ERROR", "EXPIRED"}:
            raise PublishError("Instagram could not process this Reel. Check the export and try again.")
    if status != "FINISHED":
        raise PublishError("Instagram is still processing the Reel. Retry to check the same upload.")
    published = graph("POST", account_id + "/media_publish", token, data={"creation_id": container})
    media_id = service_id(published.get("id"))
    try:
        permalink = graph("GET", media_id, token, params={"fields": "permalink"}).get("permalink", "")
        return upload_location(permalink, "www.instagram.com")
    except PublishError:
        return ""
