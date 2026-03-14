"""Job status polling routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import get_job_manager
from api.jobs import JobInfo

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(job_id: str) -> JobInfo:
    jm = get_job_manager()
    info = jm.get(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return info


@router.get("")
async def list_jobs(limit: int = 50) -> list[JobInfo]:
    jm = get_job_manager()
    return jm.list_jobs(limit=limit)
