import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path


logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    sequence INTEGER PRIMARY KEY,
    job_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    source_file TEXT NOT NULL,
    request TEXT NOT NULL,
    settings TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    stage TEXT NOT NULL,
    detail TEXT NOT NULL,
    progress INTEGER,
    activity TEXT NOT NULL,
    clip_count INTEGER NOT NULL DEFAULT 0,
    queued_at REAL,
    started_at REAL,
    updated_at REAL,
    finished_at REAL
)
"""

PUBLIC_FIELDS = (
    "job_id, source, status, phase, stage, detail, progress, activity, clip_count, queued_at, started_at, finished_at"
)


class DuplicateJob(Exception):
    def __init__(self, job_id):
        super().__init__(job_id)
        self.job_id = job_id


class JobQueue:
    """Persistent FIFO queue for the studio's single local worker."""

    def __init__(self, path):
        self.path = Path(path)
        self.gate = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker = None

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def start(self, processor):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        owner = self.path.with_suffix(".lock").open("a+b")
        try:
            try:
                owner.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("Another studio instance is already using the clip queue.") from error

            with self._db() as db:
                db.execute(SCHEMA)
                columns = {column["name"] for column in db.execute("PRAGMA table_info(jobs)")}
                for column in ("queued_at", "started_at", "updated_at"):
                    if column not in columns:
                        db.execute(f"ALTER TABLE jobs ADD COLUMN {column} REAL")
                db.execute(
                    "UPDATE jobs SET status='failed', detail=?, progress=NULL, finished_at=COALESCE(updated_at, ?), request='{}', "
                    "settings='{}', source_file='' WHERE status='running'",
                    ("The studio stopped before this job finished. Submit the source again to retry.", time.time()),
                )
            self._stop.clear()
            self._worker = threading.Thread(target=self._work, args=(processor, owner), daemon=True, name="studio-render")
            self._worker.start()
        except BaseException:
            owner.close()
            raise

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._worker:
            self._worker.join(timeout=1)

    def submit(self, source, request, settings, *, identity=None):
        job_id = uuid.uuid4().hex[:8]
        source_file = request["file_path"] if request["source_type"] == "upload" else ""
        with self.gate, self._db() as db:
            if identity:
                key = identity(request, settings)
                for active in db.execute(
                    "SELECT job_id, request, settings FROM jobs WHERE status IN ('queued','running') ORDER BY sequence"
                ):
                    if identity(json.loads(active["request"]), json.loads(active["settings"])) == key:
                        raise DuplicateJob(active["job_id"])
            if source_file and not Path(source_file).is_file():
                raise FileNotFoundError("That local source is gone. Upload the video again.")
            db.execute(
                "INSERT INTO jobs (job_id,source,source_file,request,settings,status,phase,stage,detail,activity,queued_at) "
                "VALUES (?,?,?,?,?,'queued','','Queued',?,'[]',?)",
                (job_id, source, source_file, json.dumps(request), json.dumps(settings),
                 "Waiting for the next available render slot.", time.time()),
            )
        self._wake.set()
        return job_id

    def _work(self, processor, owner):
        with owner:
            self._drain(processor)

    def _drain(self, processor):
        while not self._stop.is_set():
            self._wake.clear()
            with self.gate, self._db() as db:
                job = None
                if not self._stop.is_set():
                    job = db.execute(
                        "SELECT job_id, request, settings FROM jobs WHERE status='queued' ORDER BY sequence LIMIT 1"
                    ).fetchone()
                if job:
                    now = time.time()
                    event = {
                        "phase": "source", "stage": "Preparing source",
                        "detail": "Opening the video source.", "started_at": now,
                    }
                    db.execute(
                        "UPDATE jobs SET status='running',phase=?,stage=?,detail=?,activity=?,started_at=?,updated_at=? "
                        "WHERE job_id=?",
                        (event["phase"], event["stage"], event["detail"], json.dumps([event]), now, now, job["job_id"]),
                    )
            if not job:
                self._wake.wait()
                continue

            job_id = job["job_id"]
            try:
                count = processor(job_id, json.loads(job["request"]), json.loads(job["settings"]))
                detail = f"{count} clip{'s' if count != 1 else ''} ready in the library."
                self._finish(job_id, "completed", detail, count)
            except Exception as error:
                logger.exception("Clip job %s failed", job_id)
                self._finish(job_id, "failed", str(error) or type(error).__name__, 0)

    def report(self, job_id, phase, stage, detail, progress=None, source=None):
        with self._db() as db:
            row = db.execute("SELECT activity FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            activity = json.loads(row["activity"])
            previous = activity[-1] if activity else None
            now = time.time()
            if not previous or (previous.get("phase"), previous["stage"]) != (phase, stage):
                activity.append({"phase": phase, "stage": stage, "detail": detail, "started_at": now})
            elif progress is None:
                previous["detail"] = detail
            db.execute(
                "UPDATE jobs SET phase=?, stage=?, detail=?, progress=?, source=COALESCE(?, source), activity=?, updated_at=? "
                "WHERE job_id=?",
                (phase, stage, detail, progress, source, json.dumps(activity), now, job_id),
            )

    def _finish(self, job_id, status, detail, count):
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET status=?, detail=?, progress=NULL, clip_count=?, finished_at=?, request='{}', "
                "settings='{}', source_file='' WHERE job_id=?",
                (status, detail[:1200], count, time.time(), job_id),
            )

    @staticmethod
    def _public(row, position=None):
        return dict(row) | {"activity": json.loads(row["activity"]), "position": position}

    def snapshot(self):
        with self._db() as db:
            active = db.execute(
                f"SELECT {PUBLIC_FIELDS} FROM jobs WHERE status IN ('running','queued') "
                "ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, sequence"
            ).fetchall()
            recent = db.execute(
                f"SELECT {PUBLIC_FIELDS} FROM jobs WHERE status IN ('completed','failed') "
                "ORDER BY finished_at DESC, sequence DESC LIMIT 8"
            ).fetchall()
        jobs = []
        position = 0
        for row in active:
            if row["status"] == "queued":
                position += 1
            jobs.append(self._public(row, position if row["status"] == "queued" else None))
        return jobs + [self._public(row) for row in recent]

    def get(self, job_id):
        with self._db() as db:
            row = db.execute(f"SELECT {PUBLIC_FIELDS} FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def remove_finished(self, job_id):
        """Remove history for a finished job. Active jobs stay in the queue."""
        with self._db() as db:
            return db.execute(
                "DELETE FROM jobs WHERE job_id=? AND status IN ('completed','failed')", (job_id,)
            ).rowcount > 0

    def has_work(self):
        with self._db() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone())

    def running_id(self):
        with self._db() as db:
            row = db.execute("SELECT job_id FROM jobs WHERE status='running' LIMIT 1").fetchone()
            return row["job_id"] if row else None

    def source_is_queued(self, source_file):
        with self._db() as db:
            return bool(db.execute(
                "SELECT 1 FROM jobs WHERE status='queued' AND source_file=? LIMIT 1", (source_file,)
            ).fetchone())
