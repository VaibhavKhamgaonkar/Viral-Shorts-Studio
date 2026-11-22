import io
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import app
from core.job_queue import JobQueue
from core.social_upload import (
    GOOGLE_TOKEN, META, YOUTUBE_SCOPES, PublishError, PublishStore, facebook_upload, instagram_upload, youtube_upload,
)


def response(status=200, body=None, headers=None):
    return Mock(status_code=status, headers=headers or {}, **{"json.return_value": body or {}})


class SocialUploadTests(unittest.TestCase):
    def test_google_pkce_and_state_identify_the_authorized_channel(self):
        with TemporaryDirectory() as tmp:
            store = PublishStore(tmp)
            redirect = "http://127.0.0.1:8000/api/publishing/youtube/callback"
            link = store.start_youtube({"client_id": "client", "client_secret": "secret"}, redirect)
            params = parse_qs(urlparse(link).query)
            self.assertEqual(params["code_challenge_method"], ["S256"])
            with patch("core.social_upload.http", side_effect=[
                response(body={"access_token": "access", "refresh_token": "refresh", "scope": " ".join(YOUTUBE_SCOPES)}),
                response(body={"items": [{"id": "UC_channel_1", "snippet": {"title": "SnapOdd"}}]}),
            ]) as call:
                store.finish_youtube(params["state"][0], "authorization-code", redirect)
                self.assertEqual(call.call_args_list[0].args, ("POST", GOOGLE_TOKEN))
                self.assertIn("code_verifier", call.call_args_list[0].kwargs["data"])
                with self.assertRaises(PublishError):
                    store.finish_youtube(params["state"][0], "authorization-code", redirect)
            self.assertEqual(store.public(), {"youtube": {"id": "UC_channel_1", "name": "SnapOdd"}})

    def test_meta_connection_returns_account_names_without_tokens(self):
        with TemporaryDirectory() as tmp:
            store = PublishStore(tmp)
            permissions = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
                           "instagram_basic", "instagram_content_publish"]
            with patch("core.social_upload.graph", side_effect=[
                {"data": [{"permission": name, "status": "granted"} for name in permissions]},
                {"id": "123", "name": "SnapOdd Page", "access_token": "page-token"},
                {"instagram_business_account": {"id": "456", "username": "snapodd"}},
            ]):
                store.connect_meta("123", "user-token")
            self.assertEqual(store.public(), {
                "facebook": {"id": "123", "name": "SnapOdd Page"},
                "instagram": {"id": "456", "name": "@snapodd"},
            })

    def test_youtube_resume_checks_the_same_session_and_remaining_bytes(self):
        url = "https://www.googleapis.com/upload/session"
        with patch("core.social_upload.http", side_effect=[
            response(308, headers={"Range": "bytes=0-2"}), response(201, {"id": "abcdefghijk"}),
        ]) as call:
            result = youtube_upload(io.BytesIO(b"abcdef"), 6, "token", {"session": url}, Mock(), Mock())
        self.assertEqual(call.call_args_list[0].kwargs["headers"]["Content-Range"], "bytes */6")
        self.assertEqual(call.call_args_list[1].kwargs["headers"]["Content-Range"], "bytes 3-5/6")
        self.assertEqual(call.call_args_list[1].kwargs["data"], b"def")
        self.assertEqual(result, "https://studio.youtube.com/video/abcdefghijk/edit")

    def test_meta_upload_uses_local_bytes_and_confirms_before_success(self):
        for platform in ("facebook", "instagram"):
            with self.subTest(platform=platform):
                receipt = {"submission": {"title": "Title", "description": "Caption", "synthetic_media": False}}
                def checkpoint(**values):
                    receipt.update(values)

                requests = [
                    response(body={"video_id": "567", "upload_url": "https://rupload.facebook.com/video-upload/567"}),
                    response(body={"success": True}), response(body={"success": True}),
                    response(body={"status": {"publishing_phase": {"publish_status": "published"}}}),
                ] if platform == "facebook" else [
                    response(body={"id": "567"}), response(body={"success": True}),
                    response(body={"status_code": "FINISHED"}), response(body={"id": "678"}),
                    response(body={"permalink": "https://www.instagram.com/reel/example/"}),
                ]
                upload = facebook_upload if platform == "facebook" else instagram_upload
                with patch("core.social_upload.http", side_effect=requests) as call:
                    result = upload(io.BytesIO(b"video"), 5, "123", "token", receipt, checkpoint, Mock())
                self.assertEqual(call.call_args_list[1].kwargs["headers"]["file_size"], "5")
                self.assertEqual(call.call_args_list[1].kwargs["data"].getvalue(), b"video")
                self.assertTrue(receipt["transferred"])
                self.assertTrue(result.startswith(f"https://www.{platform}.com/reel/"))
                self.assertTrue(call.call_args_list[0].args[1].startswith(META))

    def test_batch_queues_independent_destinations_and_skips_completed_uploads(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PublishStore(root / "publishing")
            store.save_connection("youtube", {"id": "UC_channel_1", "name": "SnapOdd"})
            store.save_connection("meta", {"token": "not-public", "instagram": {"id": "123", "name": "@snapodd"},
                                          "facebook": {"id": "456", "name": "SnapOdd Page"}})
            folder = root / "batch_[test]"
            folder.mkdir()
            video = folder / "clip-01.mp4"
            video.write_bytes(b"video")
            queue = JobQueue(root / "jobs.sqlite3")
            def upload(payload, path, update):
                if payload["platform"] == "facebook":
                    raise PublishError("Page token expired")
                result = {"complete": True, "url": "https://studio.youtube.com/video/abcdefghijk/edit"}
                store.save_receipt(payload["key"], result)
                return result

            body = {
                "clips": [{"filename": video.name, "revision": str(video.stat().st_mtime_ns), "title": "Title"}],
                "destinations": {"youtube": "UC_channel_1", "instagram": "123", "facebook": "456"},
                "rights_confirmed": True, "made_for_kids": False, "synthetic_media": False,
            }
            url = "/api/publishing/batch/" + folder.name
            with patch.multiple(app, OUTPUT_DIR=root, PUBLISHING=store, JOB_QUEUE=queue), \
                    patch.object(store, "upload", side_effect=upload), patch.object(app, "inspect_media", return_value=(30, True)), \
                    TestClient(app.app, base_url="http://127.0.0.1:8000") as client:
                self.assertEqual(client.post(url, json=body, headers={"Origin": "https://elsewhere.test"}).status_code, 403)
                self.assertEqual(client.post(url, json=body | {"rights_confirmed": False}).status_code, 422)
                queued = client.post(url, json=body)
                self.assertEqual(queued.status_code, 200, queued.text)
                self.assertEqual(len(queued.json()["uploads"]), 3)
                deadline = time.monotonic() + 5
                while queue.has_work() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(queue.has_work())
                self.assertCountEqual([(job["kind"], job["status"]) for job in queue.snapshot()],
                                      [("publish", "completed"), ("publish", "completed"), ("publish", "failed")])
                retry = client.post(url, json=body).json()["uploads"]
                self.assertEqual({item["platform"]: item["status"] for item in retry},
                                 {"youtube": "uploaded", "instagram": "uploaded", "facebook": "queued"})
