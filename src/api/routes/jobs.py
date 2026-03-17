"""Job status polling routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import get_job_manager
from api.job_manager import JobInfo

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/active")
async def active_jobs(type: str | None = None, influencer_id: str | None = None) -> list[JobInfo]:
    """Find active (pending/running) jobs, optionally filtered by tags."""
    jm = get_job_manager()
    filters: dict[str, str] = {}
    if type:
        filters["type"] = type
    if influencer_id:
        filters["influencer_id"] = influencer_id
    all_matching = await jm.find_jobs_async(**filters)
    return [j for j in all_matching if j.status in ("pending", "running")]


@router.get("/{job_id}")
async def get_job(job_id: str) -> JobInfo:
    jm = get_job_manager()
    info = await jm.get_async(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return info


@router.get("")
async def list_jobs(limit: int = 50) -> list[JobInfo]:
    jm = get_job_manager()
    return await jm.list_jobs_async(limit=limit)
