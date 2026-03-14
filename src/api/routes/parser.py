"""Parser API routes — trend ingestion, signal extraction, pipeline execution."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_config, get_job_manager, get_seed_dir, get_store
from trend_parser.ingest import TrendIngestService
from trend_parser.runner import PipelineRunnerService
from trend_parser.schemas import PipelineRunRequest

router = APIRouter(prefix="/parser", tags=["parser"])


# --- Request models ---


class ParseRequest(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "instagram"])
    limit: int = Field(default=10, ge=1, le=200)
    source: str | None = None
    sources_by_platform: dict[str, str] | None = None
    selectors: dict[str, dict] | None = None


# --- Routes ---


@router.post("/run")
async def start_parse(body: ParseRequest) -> dict:
    """Start a trend parsing job. Returns a job_id for polling."""
    jm = get_job_manager()
    job_id = jm.submit(_run_parse, body)
    return {"job_id": job_id}


@router.post("/pipeline")
async def start_pipeline(body: PipelineRunRequest) -> dict:
    """Start the full pipeline (ingest -> download -> filter -> VLM). Returns job_id."""
    jm = get_job_manager()
    job_id = jm.submit(_run_pipeline, body)
    return {"job_id": job_id}


@router.get("/runs")
async def list_runs(influencer_id: str, limit: int = 20) -> list[dict]:
    """List pipeline runs for an influencer."""
    store = get_store()
    return store.list_pipeline_runs(influencer_id, limit=limit)


@router.get("/runs/{run_id}")
async def get_run(influencer_id: str, run_id: str) -> dict:
    """Get a specific pipeline run."""
    store = get_store()
    run = store.load_pipeline_run(influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/signals")
async def get_signals(body: ParseRequest) -> dict:
    """Signal extraction (lightweight, no download)."""
    config = get_config()
    seed_dir = get_seed_dir()
    svc = TrendIngestService(config=config, seed_dir=seed_dir)
    platform_videos = await asyncio.to_thread(
        svc.collect_raw,
        platforms=body.platforms,
        limit_per_platform=body.limit,
        source=body.source,
        selectors=body.selectors,
    )
    signals = svc.extract_signals(platform_videos)
    summary = svc.build_summary(platform_videos, signals)
    return {
        "summary": summary,
        "signals": signals,
        "videos": {
            platform: [v.model_dump(mode="json") for v in videos]
            for platform, videos in platform_videos.items()
        },
    }


# --- Async job functions ---


async def _run_parse(body: ParseRequest) -> dict:
    config = get_config()
    seed_dir = get_seed_dir()
    svc = TrendIngestService(config=config, seed_dir=seed_dir)

    platform_videos = await asyncio.to_thread(
        svc.collect_raw,
        platforms=body.platforms,
        limit_per_platform=body.limit,
        source=body.source,
        sources_by_platform=body.sources_by_platform,
        selectors=body.selectors,
    )
    signals = svc.extract_signals(platform_videos)
    summary = svc.build_summary(platform_videos, signals)
    return {
        "summary": summary,
        "signals": signals,
        "videos": {
            platform: [v.model_dump(mode="json") for v in videos]
            for platform, videos in platform_videos.items()
        },
    }


async def _run_pipeline(body: PipelineRunRequest) -> dict:
    config = get_config()
    store = get_store()
    seed_dir = get_seed_dir()
    runner = PipelineRunnerService(config=config, store=store, seed_dir=seed_dir)
    result = await asyncio.to_thread(runner.run, body)
    return result.model_dump(mode="json")
