"""In-memory async job manager for long-running operations."""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field


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


class JobManager:
    _MAX_COMPLETED_JOBS = 500
    _COMPLETED_JOB_TTL = timedelta(hours=24)

    def __init__(self) -> None:
        self._jobs: dict[str, JobInfo] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _cleanup_old_jobs(self) -> None:
        """Remove old completed/failed jobs to bound memory usage.

        Keeps at most ``_MAX_COMPLETED_JOBS`` finished jobs, preferring those
        created within the last 24 hours.  Active (pending/running) jobs are
        never removed.
        """
        now = datetime.now(UTC)
        active_statuses = {JobStatus.pending, JobStatus.running}

        finished: list[tuple[str, JobInfo]] = [
            (jid, info)
            for jid, info in self._jobs.items()
            if info.status not in active_statuses
        ]

        if len(finished) <= self._MAX_COMPLETED_JOBS:
            return

        # Sort oldest-first so we can drop from the front
        finished.sort(key=lambda pair: pair[1].created_at)

        # First pass: remove all jobs older than TTL
        for jid, info in finished:
            if (now - info.created_at) > self._COMPLETED_JOB_TTL:
                del self._jobs[jid]
                self._tasks.pop(jid, None)

        # Second pass: enforce hard cap on remaining finished jobs
        remaining = [
            (jid, info) for jid, info in self._jobs.items()
            if info.status not in active_statuses
        ]
        if len(remaining) > self._MAX_COMPLETED_JOBS:
            remaining.sort(key=lambda pair: pair[1].created_at)
            for jid, _ in remaining[:len(remaining) - self._MAX_COMPLETED_JOBS]:
                del self._jobs[jid]
                self._tasks.pop(jid, None)

    def submit(
        self,
        fn: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        self._cleanup_old_jobs()
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC)
        self._jobs[job_id] = JobInfo(
            job_id=job_id,
            status=JobStatus.pending,
            created_at=now,
        )
        task = asyncio.create_task(self._run(job_id, fn, *args, **kwargs))
        self._tasks[job_id] = task
        return job_id

    def get(self, job_id: str) -> JobInfo | None:
        return self._jobs.get(job_id)

    def update_progress(self, job_id: str, data: dict) -> None:
        """Update the progress dict for a running job (thread-safe under GIL)."""
        info = self._jobs.get(job_id)
        if info:
            info.progress.update(data)

    def submit_tagged(
        self,
        fn: Callable[..., Coroutine],
        tags: dict,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit a job with metadata tags for later lookup."""
        job_id = self.submit(fn, *args, **kwargs)
        self._jobs[job_id].tags = tags
        return job_id

    def find_jobs(self, **tag_filters: Any) -> list[JobInfo]:
        """Find jobs matching all tag filters, newest first."""
        results = []
        for job in self._jobs.values():
            if all(job.tags.get(k) == v for k, v in tag_filters.items()):
                results.append(job)
        return sorted(results, key=lambda j: j.created_at, reverse=True)

    def list_jobs(self, limit: int = 50) -> list[JobInfo]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    async def _run(
        self,
        job_id: str,
        fn: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        info = self._jobs[job_id]
        info.status = JobStatus.running
        info.started_at = datetime.now(UTC)
        # Inject progress callback if the function accepts it
        sig = inspect.signature(fn)
        if "progress_fn" in sig.parameters:
            kwargs["progress_fn"] = lambda data: self.update_progress(job_id, data)
        try:
            result = await fn(*args, **kwargs)
            info.status = JobStatus.completed
            info.result = result
        except Exception as exc:
            info.status = JobStatus.failed
            info.error = f"{type(exc).__name__}: {exc}"
        finally:
            info.completed_at = datetime.now(UTC)
