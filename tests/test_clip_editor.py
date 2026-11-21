import json
import subprocess
import tempfile
import time
import unittest
import wave
from io import BytesIO
from pathlib import Path
from shutil import which
from unittest.mock import patch

import av
import numpy as np
from fastapi.testclient import TestClient

import app
from core.job_queue import JobQueue
from core.transformer import get_ffmpeg_cmd, inspect_media


class ClipEditorTests(unittest.TestCase):
    def wait_for_job(self, client, job_id):
        for _ in range(150):
            job = client.get("/api/job/" + job_id).json()
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(.05)
        self.fail(f"Edit {job_id} did not finish.")

    def test_sound_assets_match_the_audition_files(self):
        client = TestClient(app.app)
        self.addCleanup(client.close)
        effects = client.get("/api/editor-assets").json()["effects"]
        self.assertEqual(len(effects), 8)
        for effect in effects:
            response = client.get("/api/editor-effects/" + effect["id"])
            self.assertEqual(response.headers["content-type"], "audio/wav")
            with wave.open(BytesIO(response.content), "rb") as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), effect["duration"], places=2)
                self.assertNotEqual(len(set(audio.readframes(audio.getnframes()))), 1)
        self.assertEqual(client.get("/api/editor-effects/missing").status_code, 404)

    def test_edit_queues_and_renders_picture_and_three_audio_layers(self):
        ffmpeg = get_ffmpeg_cmd()
        if not which(ffmpeg):
            self.skipTest("FFmpeg is required to verify edited media")
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            exports = root / "exports"
            original = exports / "Workshop_[original]"
            library = root / "music"
            original.mkdir(parents=True)
            library.mkdir()
            source = original / "clip-01.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x284:rate=20:duration=3",
                            "-f", "lavfi", "-i", "sine=frequency=240:duration=3", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=0.55",
                            str(library / "music.wav")], check=True)
            saved = source.read_bytes()
            with patch.multiple(app, OUTPUT_DIR=exports, MUSIC_DIR=library, JOB_QUEUE=JobQueue(root / "queue.sqlite3")):
                with TestClient(app.app) as client:
                    route = "/api/edit/Workshop_[original]/clip-01.mp4"
                    edit = {
                        "start": .5, "end": 2.5, "zoom": 120, "look": "monochrome", "mirror": True,
                        "source_level": 35, "music_track": "music.wav", "music_level": 40,
                        "cues": [{"effect": "chime", "at": .8, "level": 60}],
                    }
                    self.assertEqual(client.post(route, json=edit | {"end": 3.5}).status_code, 422)
                    self.assertEqual(client.post(route, json=edit | {"end": 1}).status_code, 422)
                    self.assertEqual(client.post(route, json=edit | {"music_track": "missing.wav"}).status_code, 404)
                    queued = client.post(route, json=edit)
                    self.assertEqual(queued.status_code, 200, queued.text)
                    job_id = queued.json()["job_id"]
                    job = self.wait_for_job(client, job_id)
                    self.assertEqual(job["status"], "completed", job)
                    self.assertEqual(job["result_batch_id"], "original")
                    self.assertIn("Rendering edit", [step["stage"] for step in job["activity"]])
                    batch, = client.get("/api/library", params={"selection": "edited"}).json()["batches"]
                    rendered = exports / batch["folder_name"] / batch["clips"][0]["filename"]
                    self.assertEqual(rendered, original / "clip-01-v2.mp4")
                    self.assertEqual(source.read_bytes(), saved)
                    duration, has_audio = inspect_media(rendered)
                    self.assertAlmostEqual(duration, 2, delta=.08)
                    self.assertTrue(has_audio)
                    with av.open(str(rendered)) as file:
                        frame = next(file.decode(video=0)).to_ndarray(format="rgb24")
                    self.assertEqual(frame.shape, (284, 160, 3))
                    self.assertLess(abs(frame[:, :, 0].astype(int) - frame[:, :, 1]).max(), 3)
                    with av.open(str(rendered)) as file:
                        audio = np.concatenate([frame.to_ndarray()[0] for frame in file.decode(audio=0)])

                    def amplitude(frequency, start, end):
                        window = audio[round(start * 48000):round(end * 48000)]
                        phase = np.arange(len(window)) * (2j * np.pi * frequency / 48000)
                        return abs(np.dot(window, np.exp(phase))) / len(window)

                    self.assertGreater(amplitude(240, .2, .34), .005)
                    self.assertGreater(amplitude(500, 1.55, 1.7), .005)
                    self.assertLess(amplitude(880, .35, .48), .002)
                    self.assertGreater(amplitude(880, .83, .95), .01)

    def test_numbered_copies_and_confirmed_replacement_keep_the_batch_and_rollback(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            folder = root / "Workshop_[original]"
            folder.mkdir()
            original = folder / "clip-01.mp4"
            original.write_bytes(b"original data")
            metadata = original.with_suffix(".json")
            metadata.write_text(json.dumps({"title": "Workshop", "description": "Old posting copy", "copy_source": "ai",
                                            "posting_context": "Original speech"}), encoding="utf-8")

            def render(source, output, edit, music, progress):
                output.write_bytes(b"new data")

            with patch.multiple(app, OUTPUT_DIR=root, JOB_QUEUE=JobQueue(root / "queue.sqlite3"), render_edit=render), \
                    patch.object(app, "inspect_media", return_value=(3, True)):
                with TestClient(app.app) as client:
                    route = "/api/edit/Workshop_[original]/"
                    data = {"start": .4, "end": 2.4}
                    for version in (2, 3):
                        name = original.name if version == 2 else "clip-01-v2.mp4"
                        response = client.post(route + name, json=data)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(self.wait_for_job(client, response.json()["job_id"])["status"], "completed")
                        self.assertTrue((folder / f"clip-01-v{version}.mp4").is_file())
                    self.assertEqual(original.read_bytes(), b"original data")

                    actual_replace = Path.replace

                    def deny_media_commit(path, destination):
                        if path.name == "edit.rendering":
                            raise PermissionError("File is in use")
                        return actual_replace(path, destination)

                    with self.assertLogs("core.job_queue", level="ERROR"), patch.object(Path, "replace", deny_media_commit):
                        response = client.post(route + original.name, json=data | {"save_mode": "replace"})
                        self.assertEqual(self.wait_for_job(client, response.json()["job_id"])["status"], "failed")
                    self.assertEqual(original.read_bytes(), b"original data")
                    self.assertIn("Old posting copy", metadata.read_text(encoding="utf-8"))

                    response = client.post(route + original.name, json=data | {"save_mode": "replace"})
                    self.assertEqual(self.wait_for_job(client, response.json()["job_id"])["status"], "completed")
                    self.assertEqual(original.read_bytes(), b"new data")
                    updated = json.loads(metadata.read_text(encoding="utf-8"))
                    self.assertEqual(updated["description"], "Old posting copy")
                    self.assertTrue(updated["copy_needs_review"])
                    self.assertNotIn("posting_context", updated)
                    batch, = client.get("/api/library").json()["batches"]
                    self.assertEqual([clip["filename"] for clip in batch["clips"]], ["clip-01.mp4", "clip-01-v2.mp4", "clip-01-v3.mp4"])
                    self.assertEqual(client.post(route + original.name, json=data | {"source_revision": "1"}).status_code, 409)
