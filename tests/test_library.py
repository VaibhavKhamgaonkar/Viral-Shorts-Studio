import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from fastapi.testclient import TestClient

import app


class LibraryTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        queue = Mock()
        queue.running_id.return_value = "running"
        queue.gate = Lock()
        workspace = patch.multiple(app, OUTPUT_DIR=self.root, JOB_QUEUE=queue)
        workspace.start()
        self.addCleanup(workspace.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)

    def clip(self, batch, timestamp, filename="clip-01", **metadata):
        folder = self.root / batch
        folder.mkdir(exist_ok=True)
        video = folder / f"{filename}.mp4"
        video.write_bytes(b"export")
        os.utime(video, (timestamp, timestamp))
        video.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")
        return video

    def results(self, **params):
        response = self.client.get("/api/library", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_search_filters_clip_content_and_legacy_captions(self):
        first = self.clip("Cycle_[cyclist]", 1000, title="Stop the click", selection="ai",
                          tags=["#BikeRepair"], posting_context="The crank bolt is loose.")
        second = self.clip("Cycle_[cyclist]", 1010, filename="clip-02", title="Adjust the wheel", selection="manual")
        first.parent.joinpath("info.json").write_text(
            '{"source_title":"Bicycle workshop","youtube_url":"https://youtu.be/example"}', encoding="utf-8"
        )
        second.with_suffix(".ass").write_text(
            "Dialogue: 0,0:00:01.00,0:00:02.00,Caption,,0,0,0,,Tighten the wheel spokes\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Hook,,0,0,0,,Made up opening line",
            encoding="utf-8",
        )
        self.clip("Ignore_[running]", 1200, title="Bicycle workshop")
        result = self.results(q="#CYCLIST #bikerepair bolt", source="youtube", selection="ai", after=1010, before=1011)
        self.assertEqual(result["total_clips"], 2)
        self.assertEqual(result["matched_clips"], 1)
        self.assertNotIn("posting_context", result["batches"][0]["clips"][0])
        self.assertEqual(self.results(q="wheel spokes")["batches"][0]["clips"][0]["title"], "Adjust the wheel")
        self.assertEqual(self.results(q="made up")["matched_clips"], 0)
        self.assertEqual(self.results(q="Bicycle workshop")["matched_clips"], 2)
        self.assertEqual(self.results(before=1010)["matched_clips"], 0)
        self.assertEqual(self.results(source="upload")["matched_clips"], 0)
        self.assertEqual(self.client.get("/api/library", params={"after": 1020, "before": 1010}).status_code, 422)

    def test_pages_sort_and_jump_to_a_task_outside_page_one(self):
        for index in range(7):
            self.clip(f"Batch_[id{index}]", 1000 + index)
        data = self.results(size=3)
        self.assertEqual((data["page"], data["pages"], data["total_batches"]), (1, 3, 7))
        self.assertEqual([batch["task_id"] for batch in data["batches"]], ["id6", "id5", "id4"])
        data = self.results(size=3, focus="id0")
        self.assertEqual(data["page"], 3)
        self.assertEqual(data["batches"][0]["task_id"], "id0")
        data = self.results(size=3, page=99, sort="oldest")
        self.assertEqual((data["page"], data["batches"][0]["task_id"]), (3, "id6"))

    def test_reveal_selects_the_exact_export(self):
        batch = "Workshop, local_[bike]"
        self.clip(batch, 1010)
        selected = self.clip(batch, 1020, filename="clip-02, final")
        self.clip(batch, 1030, filename="source-original")
        url = "/api/reveal_clip/" + quote(batch) + "/"
        system_root = str(self.root / "Windows")
        with patch.dict(os.environ, {"SystemRoot": system_root}), patch.object(os, "startfile", create=True) as explorer:
            response = self.client.post(url + quote(selected.name))
            self.assertEqual(response.status_code, 200, response.text)
            explorer.assert_called_once_with(str(Path(system_root) / "explorer.exe"), arguments=f'/select,"{selected.resolve()}"')
            explorer.reset_mock()
            for filename in ("source-original.mp4", "clip-01.json", "missing.mp4"):
                self.assertEqual(self.client.post(url + filename).status_code, 404)
            explorer.assert_not_called()

    def test_regenerate_requires_context_and_preserves_media(self):
        video = self.clip("Workshop_[bike]", 1010, title="Before", hook="Burned into the export", copy_source="ai")
        caption = video.with_suffix(".ass")
        caption.write_text(
            "Dialogue: 0,0:00:01.00,0:00:02.00,Hook,,0,0,0,,An old opening line", encoding="utf-8"
        )
        url = "/api/posting-kit/Workshop_[bike]/clip-01.mp4"
        self.assertEqual(self.client.post(url, json={}).status_code, 422)
        draft = {
            "title": "Fix the loose crank", "hook": "Hear the click?", "description": "A practical bike repair.",
            "tags": ["BikeRepair"], "copy_source": "ai", "copy_ready": True, "posting_context": "Local only",
        }
        with patch.object(app, "generate_post_copy", return_value=draft) as model:
            refreshed = self.client.post(url, json={"notes": "A rider tightens the loose crank bolt."})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(model.call_args.kwargs["excerpt"], "")
        self.assertEqual(refreshed.json()["ai_hook"], "Hear the click?")
        self.assertNotIn("posting_context", refreshed.json())
        self.assertEqual(refreshed.json()["hook"], "Burned into the export")
        self.assertEqual(video.read_bytes(), b"export")
        self.assertEqual(video.stat().st_mtime, 1010)
        self.assertIn("An old opening line", caption.read_text(encoding="utf-8"))
        with patch.object(app, "generate_post_copy", return_value=draft | {"copy_ready": False, "title": "Bad draft"}):
            self.assertEqual(self.client.post(url, json={}).status_code, 502)
        self.assertEqual(self.results()["batches"][0]["clips"][0]["title"], "Fix the loose crank")

    def test_youtube_hashtags_refresh_independently_and_survive_draft_regeneration(self):
        self.clip("Workshop_[bike]", 1010, title="Before", tags=["Cycling"],
                  source_url="https://youtu.be/abcdefghijk", posting_context="Transcript stays local")
        url = "/api/posting-kit/Workshop_[bike]/clip-01.mp4"
        research = {"platform": "youtube", "entries": [{"tag": "BikeRepair", "video_id": "abcdefghij0"}]}
        with patch.object(app, "research_youtube_hashtags", return_value=research) as search:
            response = self.client.post(url + "/hashtags", json={"topic": "  Bicycle   repair  "})
        self.assertEqual(response.status_code, 200, response.text)
        search.assert_called_once_with("Bicycle repair", "abcdefghijk")

        with patch.object(app, "research_youtube_hashtags", side_effect=app.YouTubeSearchUnavailable):
            self.assertEqual(self.client.post(url + "/hashtags", json={"topic": "Bicycle repair"}).status_code, 503)
        saved = self.results()["batches"][0]["clips"][0]
        self.assertEqual(saved["hashtag_research"], research)
        self.assertEqual(saved["tags"], ["Cycling"])

        draft = {"title": "Fix the crank", "copy_source": "ai", "tags": ["CrankBolt"], "copy_ready": True}
        with patch.object(app, "generate_post_copy", return_value=draft) as model:
            refreshed = self.client.post(url, json={"notes": "The crank bolt is loose."})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(model.call_args.kwargs["researched_hashtags"], ("BikeRepair",))
        self.assertEqual(refreshed.json()["hashtag_research"], research)

    def test_share_regeneration_refreshes_only_the_saved_public_hashtag_topic(self):
        research = {"platform": "youtube", "topic": "Bicycle repair", "entries": [{"tag": "BikeRepair"}]}
        updated_research = research | {"entries": [{"tag": "FixYourBike"}]}
        self.clip("Workshop_[bike]", 1010, title="Before", hashtag_research=research,
                  source_url="https://youtu.be/abcdefghijk", posting_context="The crank was loose.")
        url = "/api/posting-kit/Workshop_[bike]/clip-01.mp4"
        draft = {"title": "Fix the crank", "copy_source": "ai", "tags": ["CrankBolt"], "copy_ready": True}
        with patch.object(app, "research_youtube_hashtags", return_value=updated_research) as search, \
                patch.object(app, "generate_post_copy", return_value=draft) as model:
            refreshed = self.client.post(url, json={"notes": "A private note.", "refresh_hashtags": True})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        search.assert_called_once_with("Bicycle repair", "abcdefghijk")
        self.assertEqual(model.call_args.kwargs["researched_hashtags"], ("FixYourBike",))
        self.assertEqual(refreshed.json()["hashtag_research"], updated_research)
