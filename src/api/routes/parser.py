"""Parser API routes — trend ingestion, signal extraction, pipeline execution."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_config, get_db, get_job_manager, get_seed_dir, get_store
from trend_parser.ingest import TrendIngestService
from trend_parser.runner import PipelineRunnerService
from trend_parser.schemas import PipelineRunRequest

logger = logging.getLogger(__name__)

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


@router.get("/defaults")
async def get_defaults() -> dict:
    """Return default parser settings for the frontend."""
    config = get_config()
    return {
        "default_sources": config.default_sources,
    }


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
    enriched = []
    for run in runs:
        enriched.append(await _enrich_run(run, store.data_dir))
    return enriched


@router.get("/runs/{run_id}")
async def get_run(influencer_id: str, run_id: str) -> dict:
    """Get a specific pipeline run, enriched with video file lists."""
    store = get_store()
    run = store.load_pipeline_run(influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return await _enrich_run(run, store.data_dir)


@router.post("/runs/{run_id}/review")
async def submit_review(run_id: str, influencer_id: str, body: ReviewSubmission) -> dict:
    """Submit human review decisions for a pipeline run.

    Writes to both DB (canonical) and filesystem (audit trail).
    """
    store = get_store()
    run = store.load_pipeline_run(influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    db = get_db()
    manifest = {
        "completed": True,
        "videos": [v.model_dump() for v in body.videos],
    }

    # Write to DB atomically
    try:
        # Ensure pipeline run exists in DB (synced from filesystem on first access)
        await _ensure_run_in_db(run)

        # Upsert review record
        existing = await db.fetchone(
            "SELECT id FROM reviews WHERE run_id = ?", [run_id]
        )
        if existing:
            review_id = existing["id"]
            await db.execute(
                "UPDATE reviews SET completed = 1, updated_at = ? WHERE id = ?",
                [_now(), review_id],
            )
            # Replace all review videos
            await db.execute("DELETE FROM review_videos WHERE review_id = ?", [review_id])
        else:
            review_id = await db.execute_insert(
                "INSERT INTO reviews (run_id, completed, created_at, updated_at) VALUES (?, 1, ?, ?)",
                [run_id, _now(), _now()],
            )

        for v in body.videos:
            await db.execute(
                "INSERT INTO review_videos (review_id, file_name, approved, prompt) VALUES (?, ?, ?, ?)",
                [review_id, v.file_name, int(v.approved), v.prompt],
            )
    except Exception:
        logger.warning("Failed to save review to DB", exc_info=True)

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


# --- Helpers ---


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


# --- Run enrichment ---


async def _ensure_run_in_db(run: dict[str, Any]) -> None:
    """Ensure a filesystem-based pipeline run exists in the DB.

    The pipeline runner writes to filesystem only (sync thread).
    This syncs it to DB on first access so FK references work.
    """
    run_id = run.get("run_id", "")
    if not run_id:
        return
    db = get_db()
    existing = await db.fetchone("SELECT 1 FROM pipeline_runs WHERE run_id = ?", [run_id])
    if existing:
        return
    influencer_id = run.get("influencer_id", "")
    # Ensure influencer exists too
    inf_exists = await db.fetchone("SELECT 1 FROM influencers WHERE influencer_id = ?", [influencer_id])
    if not inf_exists and influencer_id:
        await db.execute(
            "INSERT OR IGNORE INTO influencers (influencer_id, name, created_at, updated_at) "
            "VALUES (?, 'Influencer', ?, ?)",
            [influencer_id, _now(), _now()],
        )
    await db.execute(
        "INSERT OR IGNORE INTO pipeline_runs "
        "(run_id, influencer_id, started_at, base_dir, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'running', ?, ?)",
        [run_id, influencer_id, run.get("started_at", _now()),
         run.get("base_dir", ""), _now(), _now()],
    )


def _fs_to_url(abs_path: str, data_dir: Path) -> str:
    """Convert an absolute filesystem path to a /files/ URL."""
    try:
        rel = Path(abs_path).relative_to(data_dir)
        return f"/files/{rel}"
    except ValueError:
        logger.warning("Path outside data_dir, refusing to expose: %s", abs_path)
        return ""


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
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load JSON from %s: %s", p, exc)
        return None


async def _enrich_run(run: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Enrich a pipeline run manifest with video lists and sub-report data."""
    # Ensure this run exists in DB (pipeline runner only writes filesystem)
    await _ensure_run_in_db(run)

    base_dir = run.get("base_dir", "")
    run_id = run.get("run_id", "")

    if base_dir:
        # Load review from DB
        review = await _load_review_from_db(run_id)
        if review:
            run["review"] = review

        # Load generation data from DB
        gen_data = await _load_generation_from_db(run_id, data_dir)
        if gen_data:
            run["generation"] = gen_data

    for plat in run.get("platforms", []):
        base_dir = run.get("base_dir", "")
        platform_name = plat.get("platform", "")
        plat_dir = Path(base_dir) / platform_name if base_dir else None

        # Downloads — new runs store directly in downloads/, old runs in downloads/{platform}/
        download_dir = str(plat_dir / "downloads") if plat_dir else None
        if download_dir and not Path(download_dir).is_dir():
            download_dir = None
        # Check for old nested structure: downloads/{platform}/
        if download_dir:
            nested = Path(download_dir) / platform_name
            if nested.is_dir() and any(nested.iterdir()):
                download_dir = str(nested)
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


async def _load_review_from_db(run_id: str) -> dict | None:
    """Load review data from the database."""
    if not run_id:
        return None
    try:
        db = get_db()
        review = await db.fetchone(
            "SELECT id, completed FROM reviews WHERE run_id = ?", [run_id]
        )
        if review is None:
            return None
        videos = await db.fetchall(
            "SELECT file_name, approved, prompt FROM review_videos WHERE review_id = ?",
            [review["id"]],
        )
        return {
            "completed": bool(review["completed"]),
            "videos": [
                {"file_name": v["file_name"], "approved": bool(v["approved"]), "prompt": v["prompt"]}
                for v in videos
            ],
        }
    except Exception:
        return None


async def _load_generation_from_db(run_id: str, data_dir: Path) -> dict | None:
    """Load generation data from the database, enriched with live job status."""
    if not run_id:
        return None
    try:
        db = get_db()
        rows = await db.fetchall(
            "SELECT gj.*, j.status as job_status, j.progress_json, j.error as job_error, "
            "j.result_json "
            "FROM generation_jobs gj "
            "LEFT JOIN jobs j ON j.job_id = gj.job_id "
            "WHERE gj.run_id = ? "
            "ORDER BY gj.started_at",
            [run_id],
        )
        if not rows:
            return None

        jm = get_job_manager()
        jobs_list = []
        for row in rows:
            entry: dict[str, Any] = {
                "file_name": row["file_name"],
                "job_id": row["job_id"],
                "started_at": row["started_at"],
            }

            # Overlay live job status if available
            live_info = jm.get(row["job_id"])
            if live_info:
                entry["status"] = live_info.status
                entry["progress"] = live_info.progress
                entry["error"] = live_info.error
                if live_info.status == "completed" and live_info.result:
                    result = live_info.result
                    if isinstance(result, dict):
                        entry["outputs"] = [
                            {"path": p, "url": _fs_to_url(p, data_dir), "name": Path(p).parent.name}
                            for p in result.get("outputs", [])
                            if Path(p).exists()
                        ]
            else:
                # Use DB data
                entry["status"] = row.get("job_status") or row.get("status", "unknown")
                try:
                    entry["progress"] = json.loads(row.get("progress_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    entry["progress"] = {}
                entry["error"] = row.get("job_error") or row.get("error")

                # Build outputs from DB or filesystem
                outputs_json = row.get("outputs_json")
                result_json = row.get("result_json")
                raw_outputs = None
                if outputs_json:
                    try:
                        raw_outputs = json.loads(outputs_json)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if not raw_outputs and result_json:
                    try:
                        result = json.loads(result_json)
                        raw_outputs = result.get("outputs", [])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if raw_outputs:
                    entry["outputs"] = [
                        {"path": p, "url": _fs_to_url(p, data_dir), "name": Path(p).parent.name}
                        for p in raw_outputs
                        if isinstance(p, str) and Path(p).exists()
                    ]

            jobs_list.append(entry)

        return {"jobs": jobs_list}
    except Exception:
        logger.debug("Failed to load generation data from DB", exc_info=True)
        return None


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


async def _run_pipeline(body: PipelineRunRequest, progress_fn=None) -> dict:
    config = get_config()
    store = get_store()
    seed_dir = get_seed_dir()
    runner = PipelineRunnerService(config=config, store=store, seed_dir=seed_dir)
    result = await asyncio.to_thread(runner.run, body, progress_callback=progress_fn)
    return result.model_dump(mode="json")
