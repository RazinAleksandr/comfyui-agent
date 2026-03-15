"""In-memory async job manager for long-running operations."""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime
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
    def __init__(self) -> None:
        self._jobs: dict[str, JobInfo] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(
        self,
        fn: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> str:
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
