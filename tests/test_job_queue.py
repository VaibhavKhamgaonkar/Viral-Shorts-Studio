import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from core.job_queue import DuplicateJob, JobQueue


class JobQueueTests(unittest.TestCase):
    def wait_for_status(self, queue, job_id, status):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = queue.get(job_id)
            if job and job["status"] == status:
                return job
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not reach {status}: {queue.get(job_id)}")

    def test_accepts_more_jobs_while_running_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            queue = JobQueue(Path(temp) / "jobs.sqlite3")
            release = threading.Event()
            processed = []

            def processor(job_id, request, settings):
                processed.append(job_id)
                if request["mode"] == "hold":
                    queue.report(job_id, "export", "Rendering clip", "Encoding the clip.", 45)
                    release.wait(5)
                    raise ValueError("Bad source")
                self.assertEqual(settings["captions"], True)
                return 2

            queue.start(processor)
            try:
                def identity(payload, settings):
                    return payload, settings

                request = {"source_type": "url", "mode": "hold"}
                first = queue.submit("First", request, {}, identity=identity)
                running = self.wait_for_status(queue, first, "running")
                self.assertGreaterEqual(running["started_at"], running["queued_at"])
                with self.assertRaises(DuplicateJob) as duplicate:
                    queue.submit("Same task", request, {}, identity=identity)
                self.assertEqual(duplicate.exception.job_id, first)
                second = queue.submit("Second", {"source_type": "url", "mode": "next"}, {"captions": True})
                third = queue.submit("Third", {"source_type": "url", "mode": "last"}, {"captions": True})
                active = queue.snapshot()
                self.assertEqual([job["job_id"] for job in active], [first, second, third])
                self.assertEqual([job["position"] for job in active], [None, 1, 2])
                self.assertTrue(queue.has_work())
                self.assertFalse(queue.remove_finished(first))
                self.assertFalse(queue.remove_finished(second))

                # A second app instance must not interrupt the in-flight job.
                with self.assertRaisesRegex(RuntimeError, "Another studio instance"):
                    JobQueue(queue.path).start(processor)
                self.assertEqual(queue.get(first)["status"], "running")

                with self.assertLogs("core.job_queue", level="ERROR"):
                    release.set()
                    self.assertEqual(self.wait_for_status(queue, first, "failed")["detail"], "Bad source")
                completed = self.wait_for_status(queue, second, "completed")
                self.assertEqual(completed["clip_count"], 2)
                self.assertGreater(completed["finished_at"], 0)
                self.wait_for_status(queue, third, "completed")
                self.assertEqual(processed, [first, second, third])
                failed = queue.get(first)
                self.assertEqual(failed["activity"][-1]["stage"], "Rendering clip")
                self.assertGreaterEqual(failed["finished_at"], failed["activity"][-1]["started_at"])
                self.assertEqual(failed["activity"][-1]["phase"], "export")
                self.assertTrue(queue.remove_finished(first))
                self.assertTrue(queue.remove_finished(second))
                self.assertFalse(queue.remove_finished(first))
                self.assertIsNone(queue.get(first))
                self.assertEqual([job["job_id"] for job in queue.snapshot()], [third])
            finally:
                release.set()
                queue.stop()

    def test_running_jobs_are_marked_interrupted_and_waiting_jobs_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            queue = JobQueue(Path(temp) / "jobs.sqlite3")
            queue.start(lambda *_: 1)
            queue.stop()
            interrupted = queue.submit("Interrupted", {"source_type": "url"}, {})
            waiting = queue.submit("Waiting", {"source_type": "url"}, {})
            with closing(sqlite3.connect(queue.path)) as db, db:
                db.execute("UPDATE jobs SET status='running' WHERE job_id=?", (interrupted,))
            restored = JobQueue(queue.path)
            restored.start(lambda *_: 1)
            try:
                self.assertIn("stopped", self.wait_for_status(restored, interrupted, "failed")["detail"])
                self.assertEqual(self.wait_for_status(restored, waiting, "completed")["clip_count"], 1)
            finally:
                restored.stop()

    def test_starts_with_pre_timing_queue_database(self):
        with tempfile.TemporaryDirectory() as temp:
            queue = JobQueue(Path(temp) / "jobs.sqlite3")
            queue.start(lambda *_: 1)
            queue.stop()
            job_id = queue.submit("Older entry", {"source_type": "url"}, {})
            with closing(sqlite3.connect(queue.path)) as db, db:
                for name in ("queued_at", "started_at", "updated_at"):
                    db.execute(f"ALTER TABLE jobs DROP COLUMN {name}")
            queue.start(lambda *_: 1)
            try:
                stored = self.wait_for_status(queue, job_id, "completed")
                self.assertIsNone(stored["queued_at"])
                self.assertGreaterEqual(stored["finished_at"], stored["started_at"])
            finally:
                queue.stop()


if __name__ == "__main__":
    unittest.main()
