"""Parser API routes — trend ingestion, signal extraction, pipeline execution."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_config, get_db, get_job_manager, get_seed_dir, get_store
from trend_parser.config import GEMINI_DEFAULT_MODEL
from trend_parser.filter import CandidateFilterConfig, run_candidate_filter
from trend_parser.ingest import TrendIngestService
from trend_parser.persona import PersonaProfile
from trend_parser.runner import PipelineRunnerService
from trend_parser.schemas import PipelineRunRequest, VlmThresholdsIn
from trend_parser.vlm import SelectorRunConfig, SelectorThresholds, run_selector

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
    draft: bool = False


class RerunVlmRequest(BaseModel):
    influencer_id: str
    theme: str = "influencer channel"
    model: str = GEMINI_DEFAULT_MODEL
    max_videos: int = Field(default=30, ge=1, le=200)
    thresholds: VlmThresholdsIn | None = None
    custom_persona_description: str | None = None
    custom_video_requirements: str | None = None


class RerunDownloadRequest(BaseModel):
    influencer_id: str


class RerunFilterRequest(BaseModel):
    influencer_id: str
    top_k: int = Field(default=15, ge=1, le=200)
    probe_seconds: int = Field(default=8, ge=3, le=120)


class PromoteVideoRequest(BaseModel):
    influencer_id: str
    file_name: str
    prompt: str


class RegenerateCaptionRequest(BaseModel):
    influencer_id: str
    file_name: str
    current_prompt: str
    feedback: str


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
    completed = 0 if body.draft else 1
    manifest = {
        "completed": not body.draft,
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
                "UPDATE reviews SET completed = ?, updated_at = ? WHERE id = ?",
                [completed, _now(), review_id],
            )
            # Replace all review videos
            await db.execute("DELETE FROM review_videos WHERE review_id = ?", [review_id])
        else:
            review_id = await db.execute_insert(
                "INSERT INTO reviews (run_id, completed, created_at, updated_at) VALUES (?, ?, ?, ?)",
                [run_id, completed, _now(), _now()],
            )

        for v in body.videos:
            await db.execute(
                "INSERT INTO review_videos (review_id, file_name, approved, prompt) VALUES (?, ?, ?, ?)",
                [review_id, v.file_name, int(v.approved), v.prompt],
            )
    except Exception:
        logger.warning("Failed to save review to DB", exc_info=True)

    # For approved videos not yet in selected/, copy from rejected/filtered/downloads/
    # (handles the case where the user promoted a VLM-rejected video via review UI)
    from api.path_utils import to_absolute
    raw_base = run.get("base_dir", "")
    abs_base_dir = to_absolute(raw_base, store.data_dir) if raw_base else None
    approved_names = {v.file_name for v in body.videos if v.approved}
    if approved_names and abs_base_dir:
        for plat in run.get("platforms", []):
            platform_name = plat.get("platform", "")
            plat_dir = abs_base_dir / platform_name
            sel_dir = plat_dir / "selected"
            for file_name in approved_names:
                if (sel_dir / file_name).exists():
                    continue
                for search_dir in [plat_dir / "rejected", plat_dir / "filtered", plat_dir / "downloads"]:
                    src = search_dir / file_name
                    if src.is_file():
                        sel_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, sel_dir / file_name)
                        logger.info("Copied promoted video %s → selected/", file_name)
                        break

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


@router.post("/runs/{run_id}/rerun-vlm")
async def rerun_vlm(run_id: str, body: RerunVlmRequest) -> dict:
    """Re-run VLM scoring on existing filtered videos with new params."""
    store = get_store()
    run = store.load_pipeline_run(body.influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    jm = get_job_manager()
    job_id = jm.submit_tagged(
        _rerun_vlm,
        {"type": "rerun_vlm", "influencer_id": body.influencer_id},
        run, body,
    )
    return {"job_id": job_id}


@router.post("/runs/{run_id}/rerun-download")
async def rerun_download(run_id: str, body: RerunDownloadRequest) -> dict:
    """Re-run downloads for failed videos in a pipeline run."""
    store = get_store()
    run = store.load_pipeline_run(body.influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    jm = get_job_manager()
    job_id = jm.submit_tagged(
        _rerun_download,
        {"type": "rerun_download", "influencer_id": body.influencer_id},
        run,
    )
    return {"job_id": job_id}


@router.post("/runs/{run_id}/rerun-filter")
async def rerun_filter(run_id: str, body: RerunFilterRequest) -> dict:
    """Re-run candidate filter on existing downloaded videos."""
    store = get_store()
    run = store.load_pipeline_run(body.influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    jm = get_job_manager()
    job_id = jm.submit_tagged(
        _rerun_filter,
        {"type": "rerun_filter", "influencer_id": body.influencer_id},
        run, body,
    )
    return {"job_id": job_id}


@router.post("/runs/{run_id}/promote")
async def promote_video(run_id: str, body: PromoteVideoRequest) -> dict:
    """Promote a rejected video to the approved review list for generation."""
    store = get_store()
    run = store.load_pipeline_run(body.influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    await _ensure_run_in_db(run)
    db = get_db()

    # Get or create review record
    existing = await db.fetchone("SELECT id FROM reviews WHERE run_id = ?", [run_id])
    if existing:
        review_id = existing["id"]
    else:
        review_id = await db.execute_insert(
            "INSERT INTO reviews (run_id, completed, created_at, updated_at) VALUES (?, 1, ?, ?)",
            [run_id, _now(), _now()],
        )

    # Upsert the video as approved
    existing_video = await db.fetchone(
        "SELECT id FROM review_videos WHERE review_id = ? AND file_name = ?",
        [review_id, body.file_name],
    )
    if existing_video:
        await db.execute(
            "UPDATE review_videos SET approved = 1, prompt = ? WHERE id = ?",
            [body.prompt, existing_video["id"]],
        )
    else:
        await db.execute(
            "INSERT INTO review_videos (review_id, file_name, approved, prompt) VALUES (?, ?, 1, ?)",
            [review_id, body.file_name, body.prompt],
        )

    # Mark review as completed
    await db.execute(
        "UPDATE reviews SET completed = 1, updated_at = ? WHERE id = ?",
        [_now(), review_id],
    )

    # Copy the video file into selected/ so generation can find it
    from api.path_utils import to_absolute
    raw_base = run.get("base_dir", "")
    abs_base_dir = to_absolute(raw_base, store.data_dir) if raw_base else None
    for plat in run.get("platforms", []):
        platform_name = plat.get("platform", "")
        plat_dir = abs_base_dir / platform_name if abs_base_dir else Path(raw_base) / platform_name
        for search_dir in [plat_dir / "rejected", plat_dir / "filtered", plat_dir / "downloads"]:
            src = search_dir / body.file_name
            if src.is_file():
                sel_dir = plat_dir / "selected"
                sel_dir.mkdir(parents=True, exist_ok=True)
                dst = sel_dir / body.file_name
                if not dst.exists():
                    shutil.copy2(src, dst)
                break

    return {"status": "promoted", "file_name": body.file_name}


@router.post("/runs/{run_id}/regenerate-caption")
async def regenerate_caption(run_id: str, body: RegenerateCaptionRequest) -> dict:
    """Regenerate a video caption using Gemini with user feedback."""
    import os
    from trend_parser.gemini import call_gemini, sanitize_error_message

    store = get_store()
    run = store.load_pipeline_run(body.influencer_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    # Find the video file
    from api.path_utils import to_absolute
    raw_base = run.get("base_dir", "")
    abs_base_dir = to_absolute(raw_base, store.data_dir) if raw_base else None
    video_path = None
    for plat in run.get("platforms", []):
        platform_name = plat.get("platform", "")
        for sub in ["selected", "filtered", "downloads"]:
            candidate = abs_base_dir / platform_name / sub / body.file_name if abs_base_dir else Path(raw_base) / platform_name / sub / body.file_name
            if candidate.is_file():
                video_path = candidate
                break
        if video_path:
            break

    if not video_path:
        raise HTTPException(status_code=404, detail="Video file not found")

    # Load appearance description
    appearance_desc = None
    try:
        from api.deps import get_db_store
        db_store = get_db_store()
        inf_record = await db_store.load_influencer(body.influencer_id)
        if inf_record:
            appearance_desc = inf_record.get("appearance_description")
    except Exception:
        pass

    appearance_block = f"\nThe person we are generating looks like this: {appearance_desc}" if appearance_desc else ""

    # Check if this influencer has a LoRA configured
    has_lora = False
    try:
        from comfy_pipeline.config import WorkflowConfig
        wf_config = WorkflowConfig.from_yaml(
            Path(__file__).resolve().parents[3] / "configs" / "wan_animate.yaml"
        )
        has_lora = wf_config.characters.get(body.influencer_id) is not None
    except Exception:
        pass

    lora_instruction = (
        'Start the caption with "sks woman" or "sks girl" as the very first words.'
        if has_lora
        else ""
    )

    prompt = f"""
You are a video description expert for AI video generation prompts.
{appearance_block}

The current prompt for this video is:
"{body.current_prompt}"

The user wants changes: {body.feedback}

Watch the video and rewrite the generation prompt incorporating the user's feedback.
{"The caption MUST start with a brief description of the person's appearance (from the description above)." if appearance_desc else ""}
Focus on: person's movements, gestures, body language, facial expressions, poses, and camera angle.
Do NOT focus on background, environment, or clothing.
{lora_instruction}

Output format rules:
- Return ONLY valid JSON
- No markdown, no extra commentary
- Use this exact schema:
{{"caption": "your rewritten generation prompt here"}}
""".strip()

    try:
        payload, _raw = await asyncio.to_thread(
            call_gemini,
            model=get_config().gemini_model,
            api_key=api_key,
            video_path=video_path,
            prompt=prompt,
            timeout_sec=120,
            temperature=0.4,
        )
        caption = str(payload.get("caption", "")).strip()
        if not caption:
            raise ValueError("empty caption in response")

        # Persist the new caption directly to the review_videos table
        try:
            db = get_db()
            await db.execute(
                "UPDATE review_videos SET prompt = ? "
                "WHERE file_name = ? AND review_id IN ("
                "  SELECT id FROM reviews WHERE run_id = ?"
                ")",
                [caption, body.file_name, run_id],
            )
        except Exception:
            logger.warning("Failed to persist regenerated caption to DB", exc_info=True)

        return {"caption": caption}
    except Exception as exc:
        safe_msg = sanitize_error_message(str(exc), api_key=api_key)
        logger.error("Caption regeneration failed: %s", safe_msg)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {safe_msg}")


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
    from api.path_utils import to_relative
    raw_base = run.get("base_dir", "")
    rel_base = to_relative(raw_base, get_store().data_dir) if raw_base else ""
    await db.execute(
        "INSERT OR IGNORE INTO pipeline_runs "
        "(run_id, influencer_id, started_at, base_dir, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'running', ?, ?)",
        [run_id, influencer_id, run.get("started_at", _now()),
         rel_base, _now(), _now()],
    )


def _fs_to_url(stored_path: str, data_dir: Path) -> str:
    """Convert a stored (absolute or relative) path to a /files/ URL."""
    if not stored_path:
        return ""
    try:
        from api.path_utils import to_absolute
        abs_path = to_absolute(stored_path, data_dir)
        rel = abs_path.relative_to(data_dir)
        return f"/files/{rel}"
    except ValueError:
        logger.warning("Path outside data_dir, refusing to expose: %s", stored_path)
        return ""


def _list_videos(directory: str | None, data_dir: Path) -> list[dict[str, str]]:
    """List video files in a directory, returning name + URL."""
    if not directory:
        return []
    from api.path_utils import to_absolute
    d = to_absolute(directory, data_dir)
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


def _load_json(path: str | None, data_dir: Path) -> dict[str, Any] | None:
    if not path:
        return None
    from api.path_utils import to_absolute
    p = to_absolute(path, data_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load JSON from %s: %s", p, exc)
        return None


def _load_vlm_video_details(vlm_dir: str | None, data_dir: Path) -> list[dict[str, Any]]:
    """Load per-video VLM JSON files from the vlm/ directory."""
    if not vlm_dir:
        return []
    from api.path_utils import to_absolute
    d = to_absolute(vlm_dir, data_dir)
    if not d.is_dir():
        return []
    results = []
    for f in sorted(d.iterdir()):
        if f.suffix != ".json" or f.name.startswith("vlm_summary"):
            continue
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            model_output = payload.get("model_output") or {}
            scores = model_output.get("scores") or {}
            results.append({
                "file_name": payload.get("file_name", f.stem),
                "auto_decision": payload.get("auto_decision"),
                "summary": model_output.get("summary", ""),
                "scores": scores,
                "confidence": model_output.get("confidence", 0),
                "reasons": payload.get("reasons") or model_output.get("reasons", []),
                "decision": model_output.get("decision"),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return results


async def _enrich_run(run: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Enrich a pipeline run manifest with video lists and sub-report data."""
    from api.path_utils import to_absolute

    # Ensure this run exists in DB (pipeline runner only writes filesystem)
    await _ensure_run_in_db(run)

    raw_base_dir = run.get("base_dir", "")
    run_id = run.get("run_id", "")
    abs_base_dir = to_absolute(raw_base_dir, data_dir) if raw_base_dir else None

    if abs_base_dir:
        # Load review from DB
        review = await _load_review_from_db(run_id)
        if review:
            run["review"] = review

        # Load generation data from DB
        gen_data = await _load_generation_from_db(run_id, data_dir)
        if gen_data:
            run["generation"] = gen_data

    for plat in run.get("platforms", []):
        platform_name = plat.get("platform", "")
        plat_dir = abs_base_dir / platform_name if abs_base_dir else None

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

        # Candidate filter report — try manifest path, fall back to latest on disk
        report = _load_json(plat.get("candidate_report_path"), data_dir)
        if not report and plat_dir:
            analysis_dir = plat_dir / "analysis"
            if analysis_dir.is_dir():
                reports = sorted(analysis_dir.glob("candidate_filter_report_*.json"))
                if reports:
                    report = _load_json(str(reports[-1].relative_to(data_dir)), data_dir)
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
                "rejected_candidates": [
                    {
                        "file_name": c.get("file_name"),
                        "platform": c.get("platform"),
                        "views": c.get("views"),
                        "metrics": c.get("metrics"),
                        "scores": c.get("scores"),
                        "reject_reasons": c.get("reject_reasons"),
                    }
                    for c in report.get("rejected_candidates", [])
                ],
            }

        # VLM summary — try manifest path, fall back to latest on disk
        vlm = _load_json(plat.get("vlm_summary_path"), data_dir)
        if not vlm and plat_dir:
            vlm_dir = plat_dir / "vlm"
            if vlm_dir.is_dir():
                summaries = sorted(vlm_dir.glob("vlm_summary_*.json"))
                if summaries:
                    vlm = _load_json(str(summaries[-1].relative_to(data_dir)), data_dir)
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

        # VLM per-video details (accepted + rejected with scores)
        vlm_dir_path = str(plat_dir / "vlm") if plat_dir else None
        plat["vlm_video_details"] = _load_vlm_video_details(vlm_dir_path, data_dir)

        # Rejected videos (VLM-rejected)
        rejected_dir = str(plat_dir / "rejected") if plat_dir else None
        plat["rejected_videos"] = _list_videos(rejected_dir, data_dir)

        # Platform manifest (ingested items with URLs, captions, views)
        pm_path = str(plat_dir / "platform_manifest.json") if plat_dir else None
        pm = _load_json(pm_path, data_dir)
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
        from api.path_utils import to_absolute

        db = get_db()
        rows = await db.fetchall(
            "SELECT gj.*, j.status as job_status, j.progress_json, j.error as job_error, "
            "j.result_json "
            "FROM generation_jobs gj "
            "LEFT JOIN jobs j ON j.job_id = gj.job_id "
            "WHERE gj.run_id = ? "
            "  AND gj.id = ("
            "    SELECT MAX(gj2.id) FROM generation_jobs gj2"
            "    WHERE gj2.run_id = gj.run_id AND gj2.file_name = gj.file_name"
            "  ) "
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
                        entry["outputs"] = []
                        for p in result.get("outputs", []):
                            abs_p = to_absolute(p, data_dir)
                            if abs_p.exists():
                                entry["outputs"].append({
                                    "path": p,
                                    "url": _fs_to_url(str(abs_p), data_dir),
                                    "name": abs_p.parent.name,
                                })
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
                    entry["outputs"] = []
                    for p in raw_outputs:
                        if not isinstance(p, str):
                            continue
                        abs_p = to_absolute(p, data_dir)
                        if abs_p.exists():
                            entry["outputs"].append({
                                "path": p,
                                "url": _fs_to_url(str(abs_p), data_dir),
                                "name": abs_p.parent.name,
                            })

            # QA review data (always from DB)
            qa_status = row.get("qa_status")
            if qa_status:
                entry["qa_status"] = qa_status
                qa_result_json = row.get("qa_result_json")
                if qa_result_json:
                    try:
                        entry["qa_result"] = json.loads(qa_result_json)
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Aligned reference image
            aligned_path = row.get("aligned_image_path")
            if aligned_path:
                abs_aligned = to_absolute(aligned_path, data_dir)
                if abs_aligned.exists():
                    entry["aligned_image_url"] = _fs_to_url(str(abs_aligned), data_dir)

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

    # Override Gemini model from config (yaml takes precedence over schema defaults)
    body.vlm.model = config.gemini_model
    body.review.model = config.gemini_model

    # Load appearance/content descriptions from DB for caption generation
    appearance_description = None
    content_description = None
    try:
        from api.deps import get_db_store
        db_store = get_db_store()
        inf_record = await db_store.load_influencer(body.influencer_id)
        if inf_record:
            appearance_description = inf_record.get("appearance_description")
            content_description = inf_record.get("description")
    except Exception:
        logger.debug("Failed to load influencer appearance from DB", exc_info=True)

    run_id = None
    try:
        result, auto_review_videos = await asyncio.to_thread(
            runner.run, body, progress_callback=progress_fn,
            appearance_description=appearance_description,
            content_description=content_description,
        )
        result_dict = result.model_dump(mode="json")
        run_id = Path(result.base_dir).name
        await _update_pipeline_run_status(body.influencer_id, run_id, result.base_dir, "completed")

        # Auto-submit review if the pipeline produced one
        if auto_review_videos is not None:
            try:
                await _auto_submit_review(run_id, auto_review_videos)
            except Exception as e:
                logger.error("Auto-submit review failed for run %s: %s", run_id, e)

        return result_dict
    except Exception:
        if run_id:
            await _update_pipeline_run_status(body.influencer_id, run_id, "", "failed")
        raise


async def _auto_submit_review(run_id: str, videos: list[dict]) -> None:
    """Write auto-generated review decisions to DB (same pattern as submit_review)."""
    logger.info("Auto-submitting review for run %s with %d videos", run_id, len(videos))
    try:
        db = get_db()
        now = _now()

        existing = await db.fetchone(
            "SELECT id FROM reviews WHERE run_id = ?", [run_id]
        )
        if existing:
            review_id = existing["id"]
            await db.execute(
                "UPDATE reviews SET completed = 1, updated_at = ? WHERE id = ?",
                [now, review_id],
            )
            await db.execute("DELETE FROM review_videos WHERE review_id = ?", [review_id])
        else:
            review_id = await db.execute_insert(
                "INSERT INTO reviews (run_id, completed, created_at, updated_at) VALUES (?, 1, ?, ?)",
                [run_id, now, now],
            )

        for v in videos:
            await db.execute(
                "INSERT INTO review_videos (review_id, file_name, approved, prompt) VALUES (?, ?, ?, ?)",
                [review_id, v["file_name"], int(v["approved"]), v.get("prompt", "")],
            )
    except Exception:
        logger.warning("Failed to save auto-review to DB", exc_info=True)
        return
    logger.info("Auto-review saved for run %s: %d videos", run_id, len(videos))


async def _update_pipeline_run_status(
    influencer_id: str, run_id: str, base_dir: str, status: str,
) -> None:
    """Ensure pipeline run exists in DB with the correct status."""
    try:
        db = get_db()
        now = _now()
        existing = await db.fetchone(
            "SELECT 1 FROM pipeline_runs WHERE run_id = ?", [run_id]
        )
        if existing:
            await db.execute(
                "UPDATE pipeline_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                [status, now, run_id],
            )
        else:
            # Row doesn't exist yet — insert it with the final status directly
            inf_exists = await db.fetchone(
                "SELECT 1 FROM influencers WHERE influencer_id = ?", [influencer_id]
            )
            if not inf_exists and influencer_id:
                await db.execute(
                    "INSERT OR IGNORE INTO influencers (influencer_id, name, created_at, updated_at) "
                    "VALUES (?, 'Influencer', ?, ?)",
                    [influencer_id, now, now],
                )
            await db.execute(
                "INSERT OR IGNORE INTO pipeline_runs "
                "(run_id, influencer_id, started_at, base_dir, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [run_id, influencer_id, now, base_dir, status, now, now],
            )
    except Exception:
        logger.debug("Failed to update pipeline run %s status to %s", run_id, status, exc_info=True)


async def _rerun_vlm(run: dict, body: RerunVlmRequest, progress_fn=None) -> dict:
    # Use Gemini model from config (yaml takes precedence over request default)
    body.model = get_config().gemini_model

    store = get_store()
    influencer = store.load_influencer(body.influencer_id)
    if influencer is None:
        raise ValueError(f"Influencer '{body.influencer_id}' not found")

    persona_path = store.influencer_dir(body.influencer_id) / "persona.json"
    if persona_path.exists():
        persona = PersonaProfile.from_dict(json.loads(persona_path.read_text(encoding="utf-8")))
    else:
        persona = PersonaProfile(
            persona_id=body.influencer_id,
            name=influencer.name,
            summary=influencer.description or "",
        )

    # Override persona description if provided in the request
    if body.custom_persona_description is not None:
        persona.summary = body.custom_persona_description

    video_requirements = (
        body.custom_video_requirements
        if body.custom_video_requirements is not None
        else influencer.video_suggestions_requirement
    )

    thresholds = body.thresholds
    from api.path_utils import to_absolute
    raw_base = run.get("base_dir", "")
    abs_base_dir = to_absolute(raw_base, get_store().data_dir) if raw_base else None

    for plat in run.get("platforms", []):
        platform_name = plat.get("platform", "")
        plat_dir = abs_base_dir / platform_name if abs_base_dir else Path(raw_base) / platform_name
        filtered_dir = plat_dir / "filtered"
        vlm_dir = plat_dir / "vlm"
        selected_dir = plat_dir / "selected"
        rejected_dir = plat_dir / "rejected"

        if not filtered_dir.is_dir():
            continue

        video_count = sum(1 for f in filtered_dir.iterdir() if f.is_file() and f.suffix in (".mp4", ".webm", ".mkv", ".mov"))
        if progress_fn:
            progress_fn({"stage": "vlm", "platform": platform_name, "status": "running", "total": video_count})

        th = SelectorThresholds()
        if thresholds:
            th = SelectorThresholds(
                min_readiness=thresholds.min_readiness,
                min_confidence=thresholds.min_confidence,
                min_persona_fit=thresholds.min_persona_fit,
                max_occlusion_risk=thresholds.max_occlusion_risk,
                max_scene_cut_complexity=thresholds.max_scene_cut_complexity,
            )

        await asyncio.to_thread(
            run_selector,
            SelectorRunConfig(
                input_dir=filtered_dir,
                output_dir=vlm_dir,
                selected_dir=selected_dir,
                rejected_dir=rejected_dir,
                theme=body.theme,
                hashtags=influencer.hashtags or [],
                model=body.model,
                api_key_env="GEMINI_API_KEY",
                timeout_sec=300,
                mock=False,
                max_videos=body.max_videos,
                sync_folders=True,
                thresholds=th,
                persona=persona,
                video_suggestions_requirement=video_requirements,
            ),
        )

        # Update run_manifest.json with the new vlm_summary_path
        run_manifest_path = abs_base_dir / "run_manifest.json"
        if run_manifest_path.is_file():
            try:
                summaries = sorted(vlm_dir.glob("vlm_summary_*.json"))
                if summaries:
                    rel_vlm = str(summaries[-1].resolve().relative_to(get_store().data_dir))
                    rm = json.loads(run_manifest_path.read_text())
                    for p in rm.get("platforms", []):
                        if p.get("platform") == platform_name:
                            p["vlm_summary_path"] = rel_vlm
                    run_manifest_path.write_text(json.dumps(rm, indent=2, ensure_ascii=False))
            except Exception:
                logger.debug("Failed to update run_manifest vlm path for %s", platform_name, exc_info=True)

        if progress_fn:
            progress_fn({"stage": "vlm", "platform": platform_name, "status": "completed"})

    # Invalidate existing review (VLM results changed)
    run_id = run.get("run_id", "")
    try:
        db = get_db()
        existing_review = await db.fetchone("SELECT id FROM reviews WHERE run_id = ?", [run_id])
        if existing_review:
            await db.execute("DELETE FROM review_videos WHERE review_id = ?", [existing_review["id"]])
            await db.execute("DELETE FROM reviews WHERE id = ?", [existing_review["id"]])
    except Exception:
        logger.debug("Failed to invalidate review for run %s", run_id, exc_info=True)

    # Auto-review: generate captions for VLM-selected videos
    if progress_fn:
        progress_fn({"stage": "review", "status": "running"})
    try:
        from trend_parser.caption import run_caption, CaptionRunConfig
        from trend_parser.runner import find_video_files

        appearance_description = None
        content_description = None
        try:
            from api.deps import get_db_store
            db_store = get_db_store()
            inf_record = await db_store.load_influencer(body.influencer_id)
            if inf_record:
                appearance_description = inf_record.get("appearance_description")
                content_description = inf_record.get("description")
        except Exception:
            logger.debug("Failed to load influencer appearance for auto-review", exc_info=True)

        has_lora = False
        try:
            from comfy_pipeline.config import WorkflowConfig
            wf_config = WorkflowConfig.from_yaml(
                Path(__file__).resolve().parents[2] / "configs" / "wan_animate.yaml"
            )
            has_lora = wf_config.characters.get(body.influencer_id) is not None
        except Exception:
            pass

        video_paths: list[Path] = []
        for plat in run.get("platforms", []):
            selected_dir = abs_base_dir / plat.get("platform", "") / "selected"
            if selected_dir.is_dir():
                video_paths.extend(find_video_files(selected_dir, max_videos=200))

        if video_paths:
            config = get_config()
            results = await asyncio.to_thread(
                run_caption,
                CaptionRunConfig(
                    video_paths=video_paths,
                    model=config.gemini_model,
                    api_key_env="GEMINI_API_KEY",
                    timeout_sec=300,
                    has_lora=has_lora,
                    appearance_description=appearance_description,
                    content_description=content_description,
                ),
            )
            auto_videos = [
                {"file_name": r.file_name, "approved": True, "prompt": r.caption}
                for r in results
            ]
            await _auto_submit_review(run_id, auto_videos)
    except Exception:
        logger.warning("Auto-review after VLM rerun failed for run %s", run_id, exc_info=True)

    if progress_fn:
        progress_fn({"stage": "review", "status": "completed"})

    return {"status": "completed", "run_id": run_id}


async def _rerun_download(run: dict, progress_fn=None) -> dict:
    """Re-download failed videos from a pipeline run's platform manifests."""
    import json as _json

    from api.path_utils import to_absolute
    from trend_parser.adapters.types import RawTrendVideo
    from trend_parser.config import ParserConfig
    from trend_parser.downloader import TrendDownloadService

    store = get_store()
    config = get_config()
    raw_base = run.get("base_dir", "")
    abs_base_dir = to_absolute(raw_base, store.data_dir) if raw_base else None
    run_id = run.get("run_id", "")
    total_retried = 0
    total_succeeded = 0

    downloader = TrendDownloadService(config=config, downloads_dir=store.data_dir / "downloads")

    for plat in run.get("platforms", []):
        platform_name = plat.get("platform", "")
        plat_dir = abs_base_dir / platform_name if abs_base_dir else Path(raw_base) / platform_name
        manifest_path = plat_dir / "platform_manifest.json"
        download_dir = plat_dir / "downloads"

        if not manifest_path.is_file():
            continue

        manifest = _json.loads(manifest_path.read_text())
        records = manifest.get("download_records", [])

        # Collect failed records that have a source URL
        failed = [r for r in records if r.get("status") == "failed" and r.get("source_url")]
        if not failed:
            continue

        total_for_platform = len(failed)
        if progress_fn:
            progress_fn({"stage": "download", "platform": platform_name, "status": "running", "current": 0, "total": total_for_platform})

        def _on_video_progress(current: int, total: int) -> None:
            if progress_fn:
                progress_fn({"stage": "download", "platform": platform_name, "status": "running", "current": current, "total": total})

        # Build RawTrendVideo objects from failed records
        videos = [
            RawTrendVideo(
                platform=platform_name,
                source_item_id=r.get("source_item_id", ""),
                video_url=r["source_url"],
            )
            for r in failed
        ]

        new_records = await asyncio.to_thread(
            downloader.download_raw_videos,
            platform=platform_name,
            videos=videos,
            force=True,
            download_dir=str(download_dir),
            progress_callback=_on_video_progress,
        )

        succeeded = sum(1 for r in new_records if r.get("status") == "downloaded")
        total_retried += len(failed)
        total_succeeded += succeeded

        # Update manifest: replace failed records with new results
        failed_ids = {r.get("source_item_id") for r in failed}
        kept = [r for r in records if r.get("source_item_id") not in failed_ids]
        manifest["download_records"] = kept + new_records
        # Update download counts
        from collections import Counter
        all_records = manifest["download_records"]
        manifest["download_counts"] = dict(Counter(r["status"] for r in all_records))

        manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False))

        # Also update the run_manifest.json download_counts
        run_manifest_path = abs_base_dir / "run_manifest.json"
        if run_manifest_path.is_file():
            try:
                rm = _json.loads(run_manifest_path.read_text())
                for p in rm.get("platforms", []):
                    if p.get("platform") == platform_name:
                        p["download_counts"] = manifest.get("download_counts", {})
                run_manifest_path.write_text(_json.dumps(rm, indent=2, ensure_ascii=False))
            except Exception:
                logger.debug("Failed to update run_manifest for %s", platform_name, exc_info=True)

        if progress_fn:
            progress_fn({"stage": "download", "platform": platform_name, "status": "completed",
                          "retried": len(failed), "succeeded": succeeded})

    return {"status": "completed", "run_id": run_id, "retried": total_retried, "succeeded": total_succeeded}


async def _rerun_filter(run: dict, body: RerunFilterRequest, progress_fn=None) -> dict:
    import json as _json
    from api.path_utils import to_absolute
    store = get_store()
    raw_base = run.get("base_dir", "")
    abs_base_dir = to_absolute(raw_base, store.data_dir) if raw_base else None

    for plat in run.get("platforms", []):
        platform_name = plat.get("platform", "")
        plat_dir = abs_base_dir / platform_name if abs_base_dir else Path(raw_base) / platform_name
        download_dir = plat_dir / "downloads"
        analysis_dir = plat_dir / "analysis"
        filtered_dir = plat_dir / "filtered"

        if not download_dir.is_dir():
            continue

        video_count = sum(1 for f in download_dir.iterdir() if f.is_file() and f.suffix in (".mp4", ".webm", ".mkv", ".mov"))
        if progress_fn:
            progress_fn({"stage": "filter", "platform": platform_name, "status": "running", "total": video_count})

        _report, report_path = await asyncio.to_thread(
            run_candidate_filter,
            CandidateFilterConfig(
                download_dir=download_dir,
                report_dir=analysis_dir,
                filtered_dir=filtered_dir,
                probe_seconds=body.probe_seconds,
                top_k=body.top_k,
                sync_filtered=True,
            ),
        )

        # Update run_manifest.json with the new candidate_report_path
        run_manifest_path = abs_base_dir / "run_manifest.json"
        if run_manifest_path.is_file():
            try:
                rm = _json.loads(run_manifest_path.read_text())
                rel_report = str(report_path.resolve().relative_to(store.data_dir))
                for p in rm.get("platforms", []):
                    if p.get("platform") == platform_name:
                        p["candidate_report_path"] = rel_report
                run_manifest_path.write_text(_json.dumps(rm, indent=2, ensure_ascii=False))
            except Exception:
                logger.debug("Failed to update run_manifest filter path for %s", platform_name, exc_info=True)

        if progress_fn:
            progress_fn({"stage": "filter", "platform": platform_name, "status": "completed"})

    return {"status": "completed", "run_id": run.get("run_id", "")}
