import io
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import app
from core.job_queue import JobDeferred, JobQueue
from core.social_upload import (
    GOOGLE_TOKEN, META, META_VIDEO, YOUTUBE_SCOPES, ExpiredConnection, PublishError, PublishStore, facebook_page_video_upload,
    facebook_upload, instagram_upload, youtube_upload,
)


def response(status=200, body=None, headers=None):
    return Mock(status_code=status, headers=headers or {}, **{"json.return_value": body or {}})


class SocialUploadTests(unittest.TestCase):
    def test_meta_page_picker_only_exposes_publishable_pages_and_follows_cursor(self):
        store = PublishStore("unused")
        with patch("core.social_upload.graph", side_effect=[
            {"data": [{"id": "123", "name": "Read Only", "tasks": ["ANALYZE"]}],
             "paging": {"next": "https://graph.facebook.com", "cursors": {"after": "after-first"}}},
            {"data": [{"id": "456", "name": "SnapOdd", "tasks": ["PROFILE_PLUS_CREATE_CONTENT"],
                       "access_token": "must-not-be-returned"}]},
        ]) as graph:
            self.assertEqual(store.meta_pages("token"), [{"id": "456", "name": "SnapOdd"}])
            self.assertEqual(graph.call_args.args, ("GET", "me/accounts", "token"))
            self.assertEqual(graph.call_args.kwargs["params"]["after"], "after-first")

    def test_default_selection_survives_saving_unrelated_settings(self):
        settings = {"destinations": {"youtube": {"account": "@SnapOdd", "enabled": False}}}
        client = TestClient(app.app, base_url="http://127.0.0.1:8000")
        self.addCleanup(client.close)
        with patch.object(app, "load_settings", return_value=settings), patch.object(app, "save_settings") as save:
            response = client.put("/api/publishing/defaults/youtube", params={"enabled": "true"})
            self.assertEqual(response.status_code, 200, response.text)
            settings.update(save.call_args.args[0])
            response = client.put("/api/settings", json={"brand_name": "SnapOdd"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(save.call_args.args[0]["destinations"]["youtube"], {"account": "@SnapOdd", "enabled": True})

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

    def test_expired_meta_is_reported_for_both_destinations_without_exposing_credentials(self):
        with TemporaryDirectory() as tmp:
            store = PublishStore(tmp)
            store.save_connection("meta", {"token": "private-page-token", "facebook": {"id": "123", "name": "SnapOdd"},
                                          "instagram": {"id": "456", "name": "@snapodd"}})
            client = TestClient(app.app, base_url="http://127.0.0.1:8000")
            self.addCleanup(client.close)
            with patch.object(app, "PUBLISHING", store), patch("core.social_upload.http", return_value=response(
                401, {"error": {"code": 190, "error_subcode": 463, "message": "private-page-token"}},
            )) as call:
                status = client.get("/api/publishing?check_meta=true")
            self.assertEqual(status.json()["meta_connection"]["state"], "expired")
            self.assertEqual(set(status.json()["accounts"]), {"facebook", "instagram"})
            self.assertNotIn("private-page-token", status.text)
            self.assertEqual(call.call_args.args, ("GET", f"{META}/me"))

    def test_long_lived_exchange_uses_new_user_token_and_saves_only_page_credential(self):
        with TemporaryDirectory() as tmp:
            store = PublishStore(tmp)
            client = TestClient(app.app, base_url="http://127.0.0.1:8000")
            self.addCleanup(client.close)
            scopes = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")
            with patch.object(app, "PUBLISHING", store), patch("core.social_upload.http", side_effect=[
                response(body={"access_token": "extended-user-token"}),
                response(body={"data": [{"permission": scope, "status": "granted"} for scope in scopes]}),
                response(body={"id": "123", "name": "SnapOdd", "access_token": "page-token"}),
            ]) as call:
                result = client.post("/api/publishing/meta/connect", json={
                    "page_id": "123", "user_token": "fresh-user-token", "app_id": "456", "app_secret": "abcd1234abcd1234",
                })
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(call.call_args_list[0].args, ("GET", f"{META}/oauth/access_token"))
            self.assertEqual(call.call_args_list[1].kwargs["token"], "extended-user-token")
            self.assertEqual(call.call_args_list[2].kwargs["token"], "extended-user-token")
            saved = (Path(tmp) / "connections.json").read_text(encoding="utf-8")
            self.assertIn("page-token", saved)
            for secret in ("fresh-user-token", "extended-user-token", "abcd1234abcd1234"):
                self.assertNotIn(secret, saved)
                self.assertNotIn(secret, result.text)

    def test_meta_connection_reports_missing_and_declined_grants_for_each_platform(self):
        store = PublishStore("unused")
        with patch("core.social_upload.graph", return_value={"data": [
            {"permission": "pages_show_list", "status": "granted"},
            {"permission": "business_management", "status": "granted"},
            {"permission": "pages_manage_posts", "status": "declined"},
            {"permission": "instagram_basic", "status": "granted"},
        ]}) as graph:
            with self.assertRaises(PublishError) as error:
                store.connect_meta("123", "private-user-token")
        detail = str(error.exception)
        self.assertIn("Facebook needs pages_manage_posts, pages_read_engagement.", detail)
        self.assertIn("Instagram needs instagram_content_publish, pages_read_engagement.", detail)
        self.assertNotIn("private-user-token", detail)
        self.assertNotIn("pages_show_list", detail)
        graph.assert_called_once_with("GET", "me/permissions", "private-user-token")

    def test_meta_connection_distinguishes_instagram_scope_and_link_problems(self):
        facebook_grants = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
        for lookup, expected in [
            (None, {"state": "permissions", "missing": ["instagram_basic", "instagram_content_publish"]}),
            ({}, {"state": "unlinked"}),
            (PublishError("Meta rejected the lookup"), {"state": "lookup_failed"}),
        ]:
            with self.subTest(expected=expected), TemporaryDirectory() as tmp:
                store = PublishStore(tmp)
                grants = facebook_grants if lookup is None else facebook_grants + ["instagram_basic", "instagram_content_publish"]
                replies = [
                    {"data": [{"permission": name, "status": "granted"} for name in grants]},
                    {"id": "123", "name": "SnapOdd", "access_token": "page-token"},
                ]
                if lookup is not None:
                    replies.append(lookup)
                with patch("core.social_upload.graph", side_effect=replies):
                    store.connect_meta("123", "user-token")
                self.assertEqual(store.public(), {"facebook": {"id": "123", "name": "SnapOdd"}})
                self.assertEqual(store.instagram_setup_status(), expected)

    def test_adding_instagram_keeps_existing_facebook_when_new_token_lacks_page_post_access(self):
        with TemporaryDirectory() as tmp:
            store = PublishStore(tmp)
            original = {"token": "working-page-token", "facebook": {"id": "123", "name": "SnapOdd"}}
            store.save_connection("meta", original)
            grants = ["pages_show_list", "pages_read_engagement", "instagram_basic", "instagram_content_publish"]
            response = {"data": [{"permission": name, "status": "granted"} for name in grants]}
            with patch("core.social_upload.graph", return_value=response), self.assertRaisesRegex(PublishError, "pages_manage_posts"):
                store.connect_meta("123", "user-token")
            self.assertEqual(store.connections()["meta"], original)

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

    def test_long_facebook_upload_uses_page_video_and_transfers_the_complete_file(self):
        receipt = {"submission": {"title": "Title", "description": "Caption", "synthetic_media": False}}

        def checkpoint(**values):
            receipt.update(values)

        with patch("core.social_upload.http", side_effect=[
            response(body={"upload_session_id": "111", "video_id": "222", "start_offset": "0", "end_offset": "3"}),
            response(body={"start_offset": "3", "end_offset": "6"}),
            response(body={"start_offset": "6", "end_offset": "6"}),
            response(body={"success": True}),
            response(body={"status": {"video_status": "ready"}, "published": True}),
        ]) as call:
            result = facebook_page_video_upload(io.BytesIO(b"abcdef"), 6, "123", "page-token", receipt, checkpoint, Mock())
        self.assertEqual(call.call_args_list[0].args[1], META_VIDEO + "/123/videos")
        self.assertEqual(call.call_args_list[1].kwargs["files"]["video_file_chunk"][1], b"abc")
        self.assertEqual(call.call_args_list[2].kwargs["files"]["video_file_chunk"][1], b"def")
        self.assertEqual(call.call_args_list[3].kwargs["data"]["published"], "true")
        self.assertEqual(result, "https://www.facebook.com/watch/?v=222")

    def test_facebook_processing_rechecks_same_page_video_without_sending_bytes(self):
        receipt = {"page_video_session": "111", "video_id": "222", "offsets": [6, 6], "finish_accepted": True}
        with patch("core.social_upload.http", side_effect=[
            response(body={"status": {"video_status": "ready"}, "published": False}),
            response(body={"status": {"video_status": "ready"}, "published": True,
                           "permalink_url": "https://www.facebook.com/SnapOdd/videos/222/"}),
        ]) as call:
            with self.assertRaises(JobDeferred):
                facebook_page_video_upload(None, 6, "123", "page-token", receipt, Mock(), Mock())
            url = facebook_page_video_upload(None, 6, "123", "page-token", receipt, Mock(), Mock())
        self.assertEqual(url, "https://www.facebook.com/SnapOdd/videos/222/")
        self.assertEqual([request.args[0] for request in call.call_args_list], ["GET", "GET"])

    def test_facebook_retains_the_smaller_copy_until_the_upload_is_confirmed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PublishStore(root / "publishing")
            store.save_connection("meta", {"token": "page-token", "facebook": {"id": "123", "name": "SnapOdd"}})
            video = root / "clip.mp4"
            with video.open("wb") as media:
                media.truncate(30_000_000)
            payload = {"platform": "facebook", "key": "upload", "account_id": "123", "facebook_duration": 20,
                       "revision": str(video.stat().st_mtime_ns), "submission": {}}

            def encoder(command, *_):
                Path(command[-1]).write_bytes(b"smaller mp4")
                return 0, ""

            def upload(media, size, _page, _token, _receipt, checkpoint, _update):
                self.assertEqual(media.read(), b"smaller mp4")
                self.assertEqual(size, 11)
                if not _receipt.get("video_id"):
                    checkpoint(video_id="222", finish_accepted=True)
                    raise JobDeferred(30)
                return "https://www.facebook.com/reel/222"

            with patch("core.social_upload.run_encoder", side_effect=encoder) as encode, \
                    patch("core.social_upload.facebook_upload", side_effect=upload), \
                    patch("core.social_upload.graph", return_value={"id": "123"}):
                with self.assertRaises(JobDeferred):
                    store.upload(payload, video, Mock())
                self.assertTrue((store.directory / "uploads" / "upload.mp4").is_file())
                self.assertTrue(store.upload(payload, video, Mock())["complete"])
                encode.assert_called_once()
            self.assertEqual(video.stat().st_size, 30_000_000)
            self.assertFalse((store.directory / "uploads" / "upload.mp4").exists())

    def test_expired_meta_stops_before_facebook_compression_or_instagram_upload(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PublishStore(root / "publishing")
            store.save_connection("meta", {"token": "expired-token", "facebook": {"id": "123", "name": "SnapOdd"},
                                          "instagram": {"id": "456", "name": "@snapodd"}})
            video = root / "clip.mp4"
            video.write_bytes(b"fixture")
            with patch("core.social_upload.http", return_value=response(401, {"error": {"code": 190}})) as call, \
                    patch.object(store, "facebook_upload_copy") as prepare:
                for platform, account in (("facebook", "123"), ("instagram", "456")):
                    update = Mock()
                    with self.assertRaisesRegex(ExpiredConnection, "Reconnect the same Page"):
                        store.upload({"platform": platform, "key": platform, "account_id": account, "submission": {},
                                      "revision": str(video.stat().st_mtime_ns)}, video, update)
                    self.assertEqual(update.call_args.args[:2], ("source", "Authorizing destination"))
                prepare.assert_not_called()
                self.assertEqual([attempt.args for attempt in call.call_args_list], [("GET", f"{META}/me")] * 2)

    def test_worker_routes_an_export_over_ninety_seconds_to_page_video(self):
        payload = {"source_type": "publish", "platform": "facebook", "folder": "batch", "filename": "clip-01.mp4"}
        video = Path("batch/clip-01.mp4")
        with patch.object(app, "exported_video", return_value=video), \
                patch.object(app, "inspect_media", return_value=(115.365, True)), \
                patch.object(app.PUBLISHING, "upload", return_value={"url": "https://www.facebook.com/watch/?v=222"}) as upload:
            app.process_job("test", payload, {})
        self.assertTrue(upload.call_args.args[0]["facebook_page_video"])

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
                    response(body={"id": "567", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/567"}),
                    response(body={"success": True}),
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

    def test_instagram_replaces_incomplete_legacy_container_with_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PublishStore(root / "publishing")
            store.save_connection("meta", {"token": "page-token", "facebook": {"id": "123"},
                                          "instagram": {"id": "456", "name": "@snapodd"}})
            video = root / "clip.mp4"
            video.write_bytes(b"old MP4")
            submission = {"description": "Caption", "synthetic_media": False}
            store.save_receipt("upload", {"container": "777", "transferred": False, "submission": submission})
            payload = {"platform": "instagram", "key": "upload", "account_id": "456", "submission": submission,
                       "revision": str(video.stat().st_mtime_ns)}
            uri = "https://rupload.facebook.com/ig-api-upload/v27.0/888"

            def prepare(_source, output, _update):
                output.write_bytes(b"prepared MP4")

            with patch("core.social_upload.prepare_instagram_copy", side_effect=prepare) as encode, \
                    patch("core.social_upload.http", side_effect=[
                        response(body={"id": "123"}), response(body={"status_code": "IN_PROGRESS"}),
                        response(body={"offset": 0}), response(body={"id": "888", "uri": uri}),
                        response(body={"success": True}), response(body={"status_code": "FINISHED"}),
                        response(body={"id": "999"}), response(body={"permalink": "https://www.instagram.com/reel/posted/"}),
                    ]) as http:
                self.assertEqual(store.upload(payload, video, Mock())["url"], "https://www.instagram.com/reel/posted/")
            encode.assert_called_once()
            self.assertEqual(http.call_args_list[4].args, ("POST", uri))
            self.assertEqual(http.call_args_list[4].kwargs["headers"]["file_size"], "12")
            self.assertEqual(video.read_bytes(), b"old MP4")

    def test_instagram_resumes_at_server_offset_and_defers_processing(self):
        uri = "https://rupload.facebook.com/ig-api-upload/v27.0/888"
        receipt = {"container": "888", "instagram_uri": uri, "transferred": False}
        with patch("core.social_upload.http", side_effect=[
            response(body={"status_code": "IN_PROGRESS"}), response(body={"offset": 3}),
            response(body={"success": True}), response(body={"status_code": "IN_PROGRESS"}),
            response(body={"status_code": "FINISHED"}), response(body={"id": "999"}),
            response(body={"permalink": "https://www.instagram.com/reel/posted/"}),
        ]) as http:
            with self.assertRaises(JobDeferred) as waiting:
                instagram_upload(io.BytesIO(b"abcdef"), 6, "123", "token", receipt, receipt.update, Mock())
            self.assertEqual(waiting.exception.delay, 60)
            self.assertTrue(receipt["transferred"])
            self.assertEqual(instagram_upload(None, 6, "123", "token", receipt, receipt.update, Mock()),
                             "https://www.instagram.com/reel/posted/")
        transfer = http.call_args_list[2]
        self.assertEqual(transfer.args, ("POST", uri))
        self.assertEqual(transfer.kwargs["headers"]["offset"], "3")
        self.assertEqual(transfer.kwargs["headers"]["Content-Length"], "3")
        self.assertEqual([call.args[0] for call in http.call_args_list[4:]], ["GET", "POST", "GET"])

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
                facebook = next(item for item in queued.json()["uploads"] if item["platform"] == "facebook")
                resume_url = "/api/job/" + facebook["job_id"] + "/resume"
                self.assertEqual(client.post(resume_url, headers={"Origin": "https://elsewhere.test"}).status_code, 403)
                self.assertEqual(client.post(resume_url).json(), {"job_id": facebook["job_id"]})
                deadline = time.monotonic() + 5
                while queue.has_work() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(queue.get(facebook["job_id"])["status"], "failed")
                retry = client.post(url, json=body).json()["uploads"]
                self.assertEqual({item["platform"]: item["status"] for item in retry},
                                 {"youtube": "uploaded", "instagram": "uploaded", "facebook": "queued"})
