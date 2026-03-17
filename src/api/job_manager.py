"""Persistent async job manager backed by SQLite.

Drop-in replacement for the old in-memory ``JobManager``. All state
transitions are written to the ``jobs`` table immediately so nothing
is lost on server restart. Progress updates are buffered in memory
and flushed to DB once per second to avoid write amplification.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from api.database import Database
from api.events import EventBus

logger = logging.getLogger(__name__)


# Re-export models so existing code that imports from api.jobs still works.
class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: Any = None
    error: str | None = None
    progress: dict = Field(default_factory=dict)
    tags: dict = Field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_job(row: dict) -> JobInfo:
    """Convert a DB row to a JobInfo model."""
    tags: dict = {}
    if row.get("job_type"):
        tags["type"] = row["job_type"]
    if row.get("influencer_id"):
        tags["influencer_id"] = row["influencer_id"]
    if row.get("server_id"):
        tags["server_id"] = row["server_id"]

    result = None
    if row.get("result_json"):
        try:
            result = json.loads(row["result_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    progress = {}
    if row.get("progress_json"):
        try:
            progress = json.loads(row["progress_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    created_at = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.now(UTC)
    started_at = datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None
    completed_at = datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None

    return JobInfo(
        job_id=row["job_id"],
        status=JobStatus(row["status"]),
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        result=result,
        error=row.get("error"),
        progress=progress,
        tags=tags,
    )


class PersistentJobManager:
    """SQLite-backed job manager with real-time event publishing."""

    _MAX_COMPLETED_JOBS = 500
    _COMPLETED_JOB_TTL = timedelta(hours=24)

    def __init__(self, db: Database, event_bus: EventBus | None = None) -> None:
        self._db = db
        self._event_bus = event_bus
        self._tasks: dict[str, asyncio.Task] = {}
        # In-memory progress cache (flushed to DB periodically)
        self._progress: dict[str, dict] = {}
        self._flush_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Called on app startup. Mark orphaned jobs as failed and start flush loop."""
        now = _now()
        rows = await self._db.fetchall(
            "SELECT job_id, job_type, server_id FROM jobs WHERE status IN ('pending', 'running')"
        )
        if rows:
            logger.warning("Found %d orphaned jobs from previous run, marking failed", len(rows))
            for row in rows:
                await self._db.execute(
                    "UPDATE jobs SET status = 'failed', error = 'Server restarted during execution', "
                    "completed_at = ? WHERE job_id = ?",
                    [now, row["job_id"]],
                )
                # Also update generation_jobs table
                await self._db.execute(
                    "UPDATE generation_jobs SET status = 'failed', error = 'Server restarted', "
                    "completed_at = ? WHERE job_id = ?",
                    [now, row["job_id"]],
                )
        self._start_flush_loop()

    def _start_flush_loop(self) -> None:
        """Start background task that flushes progress to DB every second."""
        if self._flush_task is not None:
            return

        async def _flush() -> None:
            while True:
                await asyncio.sleep(1.0)
                await self._flush_progress()

        self._flush_task = asyncio.create_task(_flush())

    async def _flush_progress(self) -> None:
        """Write buffered progress dicts to DB."""
        if not self._progress:
            return
        batch = dict(self._progress)
        self._progress.clear()
        for job_id, data in batch.items():
            try:
                await self._db.execute(
                    "UPDATE jobs SET progress_json = ? WHERE job_id = ?",
                    [json.dumps(data), job_id],
                )
            except Exception:
                logger.debug("Failed to flush progress for %s", job_id, exc_info=True)

    async def shutdown(self) -> None:
        """Cancel background tasks."""
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        await self._flush_progress()

    # ------------------------------------------------------------------
    # Submit / Run
    # ------------------------------------------------------------------

    def _insert_job_sync(self, job_id: str, tags: dict) -> None:
        """Insert the jobs row synchronously so it exists before any FK references.

        This ensures that `generation_jobs` inserts (which happen via
        ``await _save_generation_job()`` right after ``submit_tagged``)
        never hit a FK violation.
        """
        import sqlite3 as _sqlite3
        now = _now()
        conn = _sqlite3.connect(str(self._db._db_path))
        try:
            conn.execute(
                "INSERT INTO jobs (job_id, job_type, status, created_at, influencer_id, server_id) "
                "VALUES (?, ?, 'pending', ?, ?, ?)",
                [job_id, tags.get("type", ""), now, tags.get("influencer_id"), tags.get("server_id")],
            )
            conn.commit()
        finally:
            conn.close()

    def submit(
        self,
        fn: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit a job. Returns job_id immediately."""
        job_id = uuid.uuid4().hex[:12]
        tags: dict = {}
        self._insert_job_sync(job_id, tags)
        self._publish("job_state", {"job_id": job_id, "status": "pending", "tags": tags})
        task = asyncio.create_task(self._run(job_id, tags, fn, *args, **kwargs))
        self._tasks[job_id] = task
        return job_id

    def submit_tagged(
        self,
        fn: Callable[..., Coroutine],
        tags: dict,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit a job with metadata tags for later lookup."""
        job_id = uuid.uuid4().hex[:12]
        self._insert_job_sync(job_id, tags)
        self._publish("job_state", {"job_id": job_id, "status": "pending", "tags": tags})
        task = asyncio.create_task(self._run(job_id, tags, fn, *args, **kwargs))
        self._tasks[job_id] = task
        return job_id

    async def _run(
        self,
        job_id: str,
        tags: dict | None,
        fn: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        tags = tags or {}

        # Transition to running (jobs row already inserted by submit/submit_tagged)
        started = _now()
        await self._db.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            [started, job_id],
        )
        self._publish("job_state", {"job_id": job_id, "status": "running"})

        # Inject progress callback if the function accepts it
        sig = inspect.signature(fn)
        if "progress_fn" in sig.parameters:
            kwargs["progress_fn"] = lambda data: self.update_progress(job_id, data)

        try:
            result = await fn(*args, **kwargs)
            completed = _now()
            result_json = json.dumps(result) if result is not None else None
            await self._db.execute(
                "UPDATE jobs SET status = 'completed', completed_at = ?, result_json = ? WHERE job_id = ?",
                [completed, result_json, job_id],
            )
            self._publish("job_state", {
                "job_id": job_id, "status": "completed", "result": result,
            })
        except Exception as exc:
            completed = _now()
            error_msg = f"{type(exc).__name__}: {exc}"
            await self._db.execute(
                "UPDATE jobs SET status = 'failed', completed_at = ?, error = ? WHERE job_id = ?",
                [completed, error_msg, job_id],
            )
            self._publish("job_state", {"job_id": job_id, "status": "failed", "error": error_msg})
        finally:
            # Flush this job's progress immediately
            if job_id in self._progress:
                try:
                    await self._db.execute(
                        "UPDATE jobs SET progress_json = ? WHERE job_id = ?",
                        [json.dumps(self._progress.pop(job_id)), job_id],
                    )
                except Exception:
                    pass

        # Cleanup old jobs periodically
        try:
            await self._cleanup_old_jobs()
        except Exception:
            logger.debug("Job cleanup failed", exc_info=True)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_async(self, job_id: str) -> JobInfo | None:
        """Async version of get()."""
        row = await self._db.fetchone("SELECT * FROM jobs WHERE job_id = ?", [job_id])
        if row is None:
            return None
        info = _row_to_job(row)
        # Overlay in-memory progress (more recent than DB)
        if job_id in self._progress:
            info.progress.update(self._progress[job_id])
        return info

    def get(self, job_id: str) -> JobInfo | None:
        """Synchronous get — runs the async query in the current event loop.

        Falls back to in-memory progress for running jobs. For callers
        that are already in an async context, prefer get_async().
        """
        try:
            loop = asyncio.get_running_loop()
            # We're inside an async context — can't block. Use a sync
            # DB query via the underlying sqlite3 connection.
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(str(self._db._db_path))
            conn.row_factory = _sqlite3.Row
            try:
                cursor = conn.execute("SELECT * FROM jobs WHERE job_id = ?", [job_id])
                row = cursor.fetchone()
                if row is None:
                    return None
                info = _row_to_job(dict(row))
                if job_id in self._progress:
                    info.progress.update(self._progress[job_id])
                return info
            finally:
                conn.close()
        except RuntimeError:
            # No event loop — shouldn't happen in our context
            return None

    async def find_jobs_async(self, **tag_filters: Any) -> list[JobInfo]:
        """Find jobs matching tag filters, newest first."""
        conditions = []
        params: list[Any] = []
        for key, value in tag_filters.items():
            if key == "type":
                conditions.append("job_type = ?")
                params.append(value)
            elif key in ("influencer_id", "server_id"):
                conditions.append(f"{key} = ?")
                params.append(value)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = await self._db.fetchall(
            f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC",
            params,
        )
        results = [_row_to_job(r) for r in rows]
        # Overlay in-memory progress
        for info in results:
            if info.job_id in self._progress:
                info.progress.update(self._progress[info.job_id])
        return results

    def find_jobs(self, **tag_filters: Any) -> list[JobInfo]:
        """Synchronous find_jobs — for code that can't await."""
        import sqlite3 as _sqlite3
        conditions = []
        params: list[Any] = []
        for key, value in tag_filters.items():
            if key == "type":
                conditions.append("job_type = ?")
                params.append(value)
            elif key in ("influencer_id", "server_id"):
                conditions.append(f"{key} = ?")
                params.append(value)
        where = " AND ".join(conditions) if conditions else "1=1"
        conn = _sqlite3.connect(str(self._db._db_path))
        conn.row_factory = _sqlite3.Row
        try:
            cursor = conn.execute(
                f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC", params
            )
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()
        results = [_row_to_job(r) for r in rows]
        for info in results:
            if info.job_id in self._progress:
                info.progress.update(self._progress[info.job_id])
        return results

    async def list_jobs_async(self, limit: int = 50) -> list[JobInfo]:
        rows = await self._db.fetchall(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", [limit]
        )
        return [_row_to_job(r) for r in rows]

    def list_jobs(self, limit: int = 50) -> list[JobInfo]:
        """Synchronous list_jobs."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(self._db._db_path))
        conn.row_factory = _sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", [limit]
            )
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()
        return [_row_to_job(r) for r in rows]

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def update_progress(self, job_id: str, data: dict) -> None:
        """Update progress dict for a running job.

        Stores in memory and publishes via SSE immediately.
        The DB is updated by the background flush loop (once per second).
        """
        if job_id not in self._progress:
            self._progress[job_id] = {}
        self._progress[job_id].update(data)
        self._publish("job_progress", {"job_id": job_id, "progress": data})

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _cleanup_old_jobs(self) -> None:
        """Remove old completed/failed jobs to bound DB size."""
        cutoff = (datetime.now(UTC) - self._COMPLETED_JOB_TTL).isoformat()
        # Delete generation_jobs first (child FK), then jobs.
        # ON DELETE CASCADE should handle this, but be explicit.
        await self._db.execute(
            "DELETE FROM generation_jobs WHERE job_id IN "
            "(SELECT job_id FROM jobs WHERE status IN ('completed', 'failed') AND created_at < ?)",
            [cutoff],
        )
        await self._db.execute(
            "DELETE FROM jobs WHERE status IN ('completed', 'failed') AND created_at < ?",
            [cutoff],
        )

    # ------------------------------------------------------------------
    # Event publishing
    # ------------------------------------------------------------------

    def _publish(self, event_type: str, data: dict) -> None:
        if self._event_bus:
            self._event_bus.publish("jobs", event_type, data)
