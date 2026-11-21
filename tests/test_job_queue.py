import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from core.job_queue import JobQueue


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
                first = queue.submit("First", {"source_type": "url", "mode": "hold"}, {})
                self.wait_for_status(queue, first, "running")
                second = queue.submit("Second", {"source_type": "url", "mode": "next"}, {"captions": True})
                third = queue.submit("Third", {"source_type": "url", "mode": "last"}, {"captions": True})
                active = queue.snapshot()
                self.assertEqual([job["job_id"] for job in active], [first, second, third])
                self.assertEqual([job["position"] for job in active], [None, 1, 2])
                self.assertTrue(queue.has_work())

                # A second app instance must not interrupt the in-flight job.
                with self.assertRaisesRegex(RuntimeError, "Another studio instance"):
                    JobQueue(queue.path).start(processor)
                self.assertEqual(queue.get(first)["status"], "running")

                with self.assertLogs("core.job_queue", level="ERROR"):
                    release.set()
                    self.assertEqual(self.wait_for_status(queue, first, "failed")["detail"], "Bad source")
                self.assertEqual(self.wait_for_status(queue, second, "completed")["clip_count"], 2)
                self.wait_for_status(queue, third, "completed")
                self.assertEqual(processed, [first, second, third])
                self.assertEqual(queue.get(first)["activity"][0]["stage"], "Rendering clip")
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


if __name__ == "__main__":
    unittest.main()
