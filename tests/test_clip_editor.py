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

            def render(source, output, edit, music, progress, **options):
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

    def test_preview_combines_original_reaction_import_titles_and_captions(self):
        ffmpeg = get_ffmpeg_cmd()
        if not which(ffmpeg):
            self.skipTest("FFmpeg is required to verify composed media")
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            folder = root / "Sample_[original]"
            folder.mkdir()
            video = folder / "clip-01.mp4"
            local = root / "insert.mp4"
            for path, color, duration, tone in ((video, "blue", 3, 310), (local, "red", .6, 530)):
                subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=160x284:r=30:d={duration}",
                                "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={duration}",
                                "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
            caption = {"start": .25, "end": .85, "text": "Speech caption example"}
            with patch.multiple(app, OUTPUT_DIR=root, INSERTS_DIR=root / "inserts"), \
                    patch.object(app, "transcribe_range", return_value=[caption]) as whisper:
                client = TestClient(app.app)
                self.addCleanup(client.close)
                route = "/api/edit/Sample_[original]/clip-01.mp4/"
                captions = client.post(route + "captions").json()["captions"]
                self.assertEqual(captions, [caption])
                self.assertEqual(whisper.call_args.args[0], video)
                with local.open("rb") as handle:
                    upload = client.post("/api/editor-inserts", files={"file": ("My animation.mp4", handle, "video/mp4")})
                self.assertEqual(upload.status_code, 200, upload.text)
                asset = upload.json()
                self.assertEqual(client.get("/api/editor-inserts/" + asset["id"]).status_code, 200)
                self.assertIn(asset, client.get("/api/editor-assets").json()["inserts"])
                self.assertEqual(client.get("/api/editor-inserts/laugh").headers["content-type"], "image/gif")
                response = client.post(route + "preview", json={
                    "start": .1, "end": 2.6, "look": "cinema", "frame": "neon",
                    "titles": [{"text": "TEST TITLE", "at": .1, "end": .8, "position": "top"}],
                    "inserts": [{"asset": "laugh", "at": 1}, {"asset": asset["id"], "at": 2}],
                    "captions": captions,
                })
                self.assertEqual(response.status_code, 200, response.text[:300] if not response.is_success else "")
                with av.open(BytesIO(response.content)) as file:
                    self.assertTrue(file.streams.audio)
                    samples = [(frame.time, frame.to_ndarray(format="rgb24")) for frame in file.decode(video=0)]
                self.assertAlmostEqual(samples[-1][0], 2.5 + .96 + .6, delta=.12)

                def frame_at(seconds):
                    return min(samples, key=lambda sample: abs(sample[0] - seconds))[1]

                before = frame_at(.07)
                titled = frame_at(.5)
                laugh = frame_at(1.4)
                inserted = frame_at(3.2)
                self.assertGreater(titled[43:88, 28:130, 1].max(), 145)
                self.assertGreater(titled[180:237, 17:143, 0].max(), 160)
                self.assertGreater(laugh[86:210, 30:130, 0].mean(), before[86:210, 30:130, 0].mean() + 60)
                self.assertGreater(inserted[140, 80, 0], inserted[140, 80, 2] + 110)
                backdrop = client.post(route + "preview", json={"start": 0, "end": .8, "frame": "orbit"})
                self.assertEqual(backdrop.status_code, 200)
                with av.open(BytesIO(backdrop.content)) as file:
                    frame = next(file.decode(video=0)).to_ndarray(format="rgb24")
                self.assertGreater(frame[130, 80, 2], 210)
                self.assertLess(frame[130, 4, 2], 180)

                ranged = client.post(route + "preview", json={
                    "start": 0, "end": 2.6,
                    "ranges": [{"at": .5, "end": .533, "look": "monochrome"},
                               {"at": 1.1, "end": 1.7, "frame": "orbit"}],
                })
                self.assertEqual(ranged.status_code, 200)
                with av.open(BytesIO(ranged.content)) as file:
                    frames = [frame.to_ndarray(format="rgb24") for frame in file.decode(video=0)]
                self.assertEqual(len(frames), 78)
                self.assertGreater(frames[14][130, 80, 2], 200)
                self.assertLess(frames[15][130, 80, 2], 100)
                self.assertGreater(frames[16][130, 80, 2], 200)
                self.assertLess(frames[40][130, 3, 2], 180)
                self.assertGreater(frames[60][130, 3, 2], 200)

    def test_focus_crop_reframes_one_frame_and_clamps_to_the_source_edges(self):
        ffmpeg = get_ffmpeg_cmd()
        if not which(ffmpeg):
            self.skipTest("FFmpeg is required to verify the focus crop")
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            folder = root / "Focus_[original]"
            folder.mkdir()
            source = folder / "clip-01.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "color=blue:s=160x284:r=30:d=1,drawbox=x=100:y=170:w=60:h=114:c=red:t=fill",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
            with patch.multiple(app, OUTPUT_DIR=root, JOB_QUEUE=JobQueue(root / "queue.sqlite3")), \
                    TestClient(app.app) as client:
                route = "/api/edit/Focus_[original]/clip-01.mp4/preview"
                focus = {"at": .5, "end": .533, "zoom": 200, "focus_x": 100, "focus_y": 100}
                response = client.post(route, json={"start": 0, "end": .9, "ranges": [focus]})
                self.assertEqual(response.status_code, 200)
                with av.open(BytesIO(response.content)) as media:
                    frames = [frame.to_ndarray(format="rgb24").astype(int) for frame in media.decode(video=0)]
                self.assertEqual(len(frames), 27)
                self.assertGreater(frames[14][142, 80, 2], 200)
                self.assertGreater(frames[15][142, 80, 0], 200)
                self.assertGreater(frames[16][142, 80, 2], 200)
                self.assertGreater(frames[15][40, 12, 2], 200)
                invalid = client.post(route, json={"start": 0, "end": .9, "ranges": [focus | {"focus_x": 101}]})
                self.assertEqual(invalid.status_code, 422)
