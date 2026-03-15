"""Parser API routes — trend ingestion, signal extraction, pipeline execution."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

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


class ReviewVideoItem(BaseModel):
    file_name: str
    approved: bool
    prompt: str = ""


class ReviewSubmission(BaseModel):
    videos: list[ReviewVideoItem]


# --- Routes ---


@router.post("/run")
async def start_parse(body: ParseRequest) -> dict:
    """Start a trend parsing job. Returns a job_id for polling."""
    jm = get_job_manager()
    job_id = jm.submit_tagged(_run_parse, {"type": "parse"}, body)
    return {"job_id": job_id}


@router.post("/pipeline")
async def start_pipeline(body: PipelineRunRequest) -> dict:
    """Start the full pipeline (ingest -> download -> filter -> VLM). Returns job_id."""
    jm = get_job_manager()
    job_id = jm.submit_tagged(
        _run_pipeline,
        {"type": "pipeline", "influencer_id": body.influencer_id},
        body,
    )
    return {"job_id": job_id}


@router.get("/runs")
async def list_runs(influencer_id: str, limit: int = 20) -> list[dict]:
    """List pipeline runs for an influencer, enriched with video file lists."""
    store = get_store()
    runs = store.list_pipeline_runs(influencer_id, limit=limit)
    return [_enrich_run(run, store.data_dir) for run in runs]


@router.get("/runs/{run_id}")
async def get_run(influencer_id: str, run_id: str) -> dict:
    """Get a specific pipeline run, enriched with video file lists."""
    store = get_store()
    run = store.load_pipeline_run(influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _enrich_run(run, store.data_dir)


@router.post("/runs/{run_id}/review")
async def submit_review(run_id: str, influencer_id: str, body: ReviewSubmission) -> dict:
    """Submit human review decisions for a pipeline run."""
    store = get_store()
    run = store.load_pipeline_run(influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    review_path = (
        store.influencer_pipeline_runs_dir(influencer_id) / run_id / "review_manifest.json"
    )
    manifest = {
        "completed": True,
        "videos": [v.model_dump() for v in body.videos],
    }
    review_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


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


# --- Run enrichment ---


def _fs_to_url(abs_path: str, data_dir: Path) -> str:
    """Convert an absolute filesystem path to a /files/ URL."""
    try:
        rel = Path(abs_path).relative_to(data_dir)
        return f"/files/{rel}"
    except ValueError:
        return abs_path


def _list_videos(directory: str | None, data_dir: Path) -> list[dict[str, str]]:
    """List video files in a directory, returning name + URL."""
    if not directory:
        return []
    d = Path(directory)
    if not d.is_dir():
        return []
    videos = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov"):
            videos.append({
                "file_name": f.name,
                "url": _fs_to_url(str(f), data_dir),
            })
    return videos


def _load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _enrich_run(run: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Enrich a pipeline run manifest with video lists and sub-report data."""
    # Load review manifest (lives at run level, not per-platform)
    base_dir = run.get("base_dir", "")
    if base_dir:
        review_path = Path(base_dir) / "review_manifest.json"
        review = _load_json(str(review_path))
        if review:
            run["review"] = review

        # Load generation manifest and enrich with live job status
        gen_path = Path(base_dir) / "generation_manifest.json"
        gen_data = _load_json(str(gen_path))
        if gen_data:
            jm = get_job_manager()
            for entry in gen_data.get("jobs", []):
                job_info = jm.get(entry.get("job_id", ""))
                if job_info:
                    entry["status"] = job_info.status
                    entry["progress"] = job_info.progress
                    entry["error"] = job_info.error
            run["generation"] = gen_data

    for plat in run.get("platforms", []):
        base_dir = run.get("base_dir", "")
        platform_name = plat.get("platform", "")
        plat_dir = Path(base_dir) / platform_name if base_dir else None

        # Downloads
        download_dir = str(plat_dir / "downloads" / platform_name) if plat_dir else None
        # Also check downloads/ directly (some runs put files there)
        if download_dir and not Path(download_dir).is_dir() and plat_dir:
            download_dir = str(plat_dir / "downloads")
        plat["download_videos"] = _list_videos(download_dir, data_dir)

        # Filtered
        plat["filtered_videos"] = _list_videos(plat.get("filtered_dir"), data_dir)

        # Selected (VLM-approved)
        plat["selected_videos"] = _list_videos(plat.get("selected_dir"), data_dir)

        # Candidate filter report
        report = _load_json(plat.get("candidate_report_path"))
        if report:
            plat["filter_report"] = {
                "total_candidates": report.get("total_candidates"),
                "accepted": report.get("accepted"),
                "rejected": report.get("rejected"),
                "top_k": report.get("top_k"),
                "top_candidates": [
                    {
                        "file_name": c.get("file_name"),
                        "platform": c.get("platform"),
                        "views": c.get("views"),
                        "metrics": c.get("metrics"),
                        "scores": c.get("scores"),
                    }
                    for c in report.get("top_candidates", [])
                ],
            }

        # VLM summary
        vlm = _load_json(plat.get("vlm_summary_path"))
        if vlm:
            plat["vlm_report"] = {
                "model": vlm.get("model"),
                "total": vlm.get("total"),
                "accepted": vlm.get("accepted"),
                "rejected": vlm.get("rejected"),
                "accepted_top": [
                    {
                        "file_name": v.get("file_name"),
                        "readiness": v.get("readiness"),
                        "persona_fit": v.get("persona_fit"),
                        "confidence": v.get("confidence"),
                        "reasons": v.get("reasons"),
                    }
                    for v in vlm.get("accepted_top", [])
                ],
            }

        # Platform manifest (ingested items with URLs, captions, views)
        pm_path = str(plat_dir / "platform_manifest.json") if plat_dir else None
        pm = _load_json(pm_path)
        if pm:
            plat["ingested_details"] = [
                {
                    "video_url": item.get("video_url"),
                    "caption": item.get("caption"),
                    "views": item.get("views"),
                    "likes": item.get("likes"),
                    "hashtags": item.get("hashtags"),
                    "platform": item.get("platform"),
                }
                for item in pm.get("ingested_items", [])
            ]

    return run


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
