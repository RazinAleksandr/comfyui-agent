"""Generation API routes — wraps VastAgentService for GPU video generation."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_db, get_job_manager, get_server_manager, get_store, get_vast_service
from isp_pipeline.processor import postprocess_outputs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# --- DB-backed generation job tracking ---


async def _save_generation_job(
    reference_video: str,
    job_id: str,
    server_id: str,
    influencer_id: str,
) -> None:
    """Persist a generation job entry to the database.

    Replaces the old filesystem-based generation_manifest.json approach.
    Atomic — no race conditions from concurrent writes.
    """
    db = get_db()
    video_p = Path(reference_video)

    # Determine run_id from the video path
    run_id = _resolve_run_id(video_p)
    if not run_id:
        logger.warning("Could not resolve run_id for %s", reference_video)
        return

    # Check for existing active generation for this video in this run
    existing = await db.fetchone(
        "SELECT gj.job_id FROM generation_jobs gj "
        "JOIN jobs j ON j.job_id = gj.job_id "
        "WHERE gj.run_id = ? AND gj.file_name = ? AND j.status IN ('pending', 'running')",
        [run_id, video_p.name],
    )
    if existing:
        logger.info(
            "Active generation already exists for %s (job %s), skipping duplicate",
            video_p.name, existing["job_id"],
        )
        return

    now = datetime.now(UTC).isoformat()
    output_dir = None
    if video_p.parent.name == "selected":
        output_dir = str(video_p.parent.parent / "generated")

    await db.execute(
        "INSERT OR IGNORE INTO generation_jobs "
        "(job_id, run_id, file_name, server_id, influencer_id, started_at, status, output_dir) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        [job_id, run_id, video_p.name, server_id, influencer_id, now, output_dir],
    )

    # Also store run_id on the job itself for easy lookup
    await db.execute(
        "UPDATE jobs SET run_id = ?, reference_video = ? WHERE job_id = ?",
        [run_id, reference_video, job_id],
    )


def _resolve_run_id(video_p: Path) -> str | None:
    """Determine the pipeline run_id from a video file path."""
    if video_p.parent.name == "selected":
        # selected/ -> platform_dir -> run_dir
        run_dir = video_p.parent.parent.parent
        if (run_dir / "run_manifest.json").is_file():
            return run_dir.name
    # Walk up to find run_manifest.json
    for parent in video_p.parents:
        if (parent / "run_manifest.json").is_file():
            return parent.name
    return None


async def _update_generation_job_complete(
    job_id: str, outputs: list[str], output_dir: str
) -> None:
    """Update the generation_jobs record with results."""
    db = get_db()
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE generation_jobs SET status = 'completed', completed_at = ?, "
        "outputs_json = ?, output_dir = ? WHERE job_id = ?",
        [now, json.dumps(outputs), output_dir, job_id],
    )


async def _update_generation_job_failed(job_id: str, error: str) -> None:
    """Mark a generation_jobs record as failed."""
    db = get_db()
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE generation_jobs SET status = 'failed', completed_at = ?, error = ? WHERE job_id = ?",
        [now, error, job_id],
    )


# --- Request / Response models ---


class GenerationRequest(BaseModel):
    influencer_id: str
    workflow: str = "wan_animate"
    reference_image: str | None = None
    reference_video: str | None = None
    prompt: str = ""
    set_args: dict[str, str] = Field(default_factory=dict)
    output_dir: str | None = None


class ServerRequest(BaseModel):
    workflow: str = "wan_animate"
    influencer_id: str | None = None


class AutoShutdownRequest(BaseModel):
    enabled: bool


# --- Server management routes ---


@router.get("/servers")
async def list_servers() -> list[dict]:
    """List all servers with status, influencer, active jobs, auto-shutdown."""
    manager = get_server_manager()
    return manager.list_servers()


@router.get("/server/allocate")
async def get_allocation_info(influencer_id: str) -> dict:
    """Get server allocation info for an influencer."""
    manager = get_server_manager()
    return manager.get_influencer_server_info(influencer_id)


@router.get("/server/status")
async def server_status(influencer_id: str | None = None) -> dict:
    """Check GPU server status, including any running startup job.

    Looks for the influencer's own server first, then any available
    free server (discovered instances that aren't assigned yet).
    """
    manager = get_server_manager()

    if influencer_id:
        info = manager.get_influencer_server_info(influencer_id)
        # Try own server, then borrowable free server
        server_id = info.get("server_id") or info.get("borrow_server_id")
        if server_id:
            status = manager.server_status(server_id)
            # Attach startup job info
            jm = get_job_manager()
            server_jobs = jm.find_jobs(type="server_up", server_id=server_id)
            active = next((j for j in server_jobs if j.status in ("pending", "running")), None)
            status["startup_job_id"] = active.job_id if active else None
            status["startup_job_status"] = active.status if active else None
            # If this is a borrowable server, note it so frontend knows
            if not info.get("server_id") and info.get("borrow_server_id"):
                status["is_borrowable"] = True
            return status

    if influencer_id:
        # Influencer has no own server and no borrowable server — report offline
        return {
            "status": "offline",
            "instance_id": None,
            "ssh_host": None,
            "ssh_port": None,
            "actual_status": None,
            "dph_total": None,
            "ssh_reachable": False,
            "startup_job_id": None,
            "startup_job_status": None,
        }

    # Fallback (no influencer_id): check any server in registry
    all_servers = manager.list_servers()
    if all_servers:
        first = all_servers[0]
        sid = first["server_id"]
        status = manager.server_status(sid)
        jm = get_job_manager()
        server_jobs = jm.find_jobs(type="server_up", server_id=sid)
        active = next((j for j in server_jobs if j.status in ("pending", "running")), None)
        status["startup_job_id"] = active.job_id if active else None
        status["startup_job_status"] = active.status if active else None
        return status

    return {
        "status": "offline",
        "instance_id": None,
        "ssh_host": None,
        "ssh_port": None,
        "actual_status": None,
        "dph_total": None,
        "ssh_reachable": False,
        "startup_job_id": None,
        "startup_job_status": None,
    }


@router.post("/server/up")
async def server_up(body: ServerRequest) -> dict:
    """Start the GPU server. Returns job_id and server_id (long-running)."""
    manager = get_server_manager()
    influencer_id = body.influencer_id

    if influencer_id:
        server_id, svc = manager.allocate_server(influencer_id, body.workflow)
    else:
        # Legacy: use default allocation
        server_id, svc = manager.allocate_server("__default__", body.workflow)

    jm = get_job_manager()
    job_id = jm.submit_tagged(
        _do_server_up,
        {"type": "server_up", "server_id": server_id},
        server_id=server_id,
        workflow=body.workflow,
    )
    return {"job_id": job_id, "server_id": server_id}


@router.post("/server/{server_id}/down")
async def shutdown_server(server_id: str) -> dict:
    """Shut down a specific server."""
    manager = get_server_manager()
    entry = manager._registry.get_server_sync(server_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        await asyncio.to_thread(manager.shutdown_server, server_id)
    except Exception:
        logger.error("Failed to shut down server %s", server_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to shut down server")
    return {"status": "destroyed"}


@router.post("/server/{server_id}/auto-shutdown")
async def set_auto_shutdown(server_id: str, body: AutoShutdownRequest) -> dict:
    """Toggle auto-shutdown flag for a server."""
    manager = get_server_manager()
    entry = manager._registry.get_server_sync(server_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Server not found")
    manager.set_auto_shutdown(server_id, body.enabled)
    return {"server_id": server_id, "auto_shutdown": body.enabled}


@router.post("/server/down")
async def server_down() -> dict:
    """Destroy the GPU server (legacy endpoint)."""
    svc = get_vast_service()
    try:
        await asyncio.to_thread(svc.down)
    except Exception:
        logger.error("Failed to shut down GPU server", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to shut down server")
    return {"status": "destroyed"}


# --- Generation jobs query route ---


@router.get("/jobs")
async def list_generation_jobs(run_id: str) -> list[dict]:
    """List generation jobs for a specific pipeline run."""
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
    jm = get_job_manager()
    result = []
    for row in rows:
        entry = dict(row)
        # Overlay live job status if available (more recent than DB)
        live_info = jm.get(entry["job_id"])
        if live_info:
            entry["status"] = live_info.status
            entry["progress"] = live_info.progress
            entry["error"] = live_info.error
            if live_info.status == "completed" and live_info.result:
                entry["outputs"] = live_info.result.get("outputs", [])
        else:
            # Use DB data
            entry["status"] = entry.get("job_status") or entry.get("status", "unknown")
            try:
                entry["progress"] = json.loads(entry.get("progress_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                entry["progress"] = {}
            try:
                entry["outputs"] = json.loads(entry.get("outputs_json") or "[]") or []
            except (json.JSONDecodeError, TypeError):
                entry["outputs"] = []
            entry["error"] = entry.get("job_error") or entry.get("error")
        # Clean up internal fields
        for key in ("job_status", "progress_json", "job_error", "result_json", "outputs_json"):
            entry.pop(key, None)
        result.append(entry)
    return result


# --- Generation routes ---


@router.post("/run")
async def start_generation(body: GenerationRequest) -> dict:
    """Start a generation job. Returns job_id for polling."""
    store = get_store()
    influencer = store.load_influencer(body.influencer_id)
    if influencer is None:
        raise HTTPException(status_code=404, detail="Influencer not found")

    # Allocate server for this influencer
    manager = get_server_manager()
    server_id, svc = manager.allocate_server(body.influencer_id, body.workflow)

    # Resolve reference image from influencer profile if not provided
    image_path = body.reference_image
    if not image_path and influencer.reference_image_path:
        candidate = store.data_dir / influencer.reference_image_path
        if candidate.exists():
            image_path = str(candidate)

    # Resolve output_dir: if reference_video is inside a pipeline run, put output
    # in a sibling "generated/" folder next to "selected/".
    output_dir = body.output_dir
    if not output_dir and body.reference_video:
        video_p = Path(body.reference_video)
        if video_p.parent.name == "selected":
            output_dir = str(video_p.parent.parent / "generated")
    if not output_dir:
        output_dir = str(PROJECT_ROOT / "output")

    # Check for active generation for this video before submitting
    if body.reference_video:
        video_p = Path(body.reference_video)
        run_id = _resolve_run_id(video_p)
        if run_id:
            db = get_db()
            existing = await db.fetchone(
                "SELECT gj.job_id FROM generation_jobs gj "
                "JOIN jobs j ON j.job_id = gj.job_id "
                "WHERE gj.run_id = ? AND gj.file_name = ? AND j.status IN ('pending', 'running')",
                [run_id, video_p.name],
            )
            if existing:
                logger.info(
                    "Active generation already running for %s (job %s), returning existing",
                    video_p.name, existing["job_id"],
                )
                return {"job_id": existing["job_id"], "server_id": server_id}

    jm = get_job_manager()
    job_id = jm.submit_tagged(
        _do_generation,
        {"type": "generation", "influencer_id": body.influencer_id, "server_id": server_id},
        workflow=body.workflow,
        image_path=image_path or "",
        video_path=body.reference_video or "",
        prompt=body.prompt,
        output_dir=output_dir,
        set_args=body.set_args,
        influencer_id=body.influencer_id,
        server_id=server_id,
    )

    # Persist job to generation_jobs table
    if body.reference_video:
        try:
            await _save_generation_job(
                body.reference_video, job_id, server_id, body.influencer_id
            )
        except Exception:
            logger.warning("Failed to save generation job to DB", exc_info=True)

    return {"job_id": job_id, "server_id": server_id}


# --- Async job functions ---


async def _do_server_up(*, server_id: str, workflow: str) -> dict:
    """Bring up the GPU server for a specific server_id."""
    manager = get_server_manager()
    svc = manager.get_or_create_service(server_id)

    # Check if already running
    current = await asyncio.to_thread(svc.status)
    if current.running:
        manager.update_registry_from_service(server_id)
        return {
            "status": "already_running",
            "server_id": server_id,
            "instance_id": current.instance_id,
            "dph_total": current.dph_total,
        }

    result = await asyncio.to_thread(svc.up, workflow)

    # Sync registry with actual instance data
    manager.update_registry_from_service(server_id)

    return {
        "status": "started",
        "server_id": server_id,
        "instance_id": result.instance_id,
        "ssh_host": result.ssh_host,
        "ssh_port": result.ssh_port,
        "dph_total": result.dph_total,
    }


async def _do_generation(
    *,
    workflow: str,
    image_path: str,
    video_path: str,
    prompt: str,
    output_dir: str,
    set_args: dict[str, str],
    influencer_id: str,
    server_id: str,
    progress_fn: Callable[[dict], None] | None = None,
    job_id: str | None = None,
) -> dict:
    """Run generation via VastAgentService."""
    manager = get_server_manager()
    svc = manager.get_or_create_service(server_id)
    server_lock = manager.get_server_lock(server_id)

    # Report "queued" while waiting for the lock
    if progress_fn:
        progress_fn({"phase": "queued", "stage": "queued"})

    # Build inputs dict
    inputs: dict[str, Path] = {}
    if image_path:
        p = Path(image_path)
        if p.exists():
            inputs["reference_image"] = p
    if video_path:
        p = Path(video_path)
        if p.exists():
            inputs["reference_video"] = p

    # Build overrides dict
    overrides: dict[str, str] = {}
    if prompt:
        overrides["prompt"] = prompt
    overrides.update(set_args)

    # Auto-apply character LoRAs from workflow config if not explicitly set
    if not set_args.get("lora_high") and not set_args.get("lora_low"):
        try:
            from comfy_pipeline.config import WorkflowConfig
            wf_config = WorkflowConfig.from_yaml(PROJECT_ROOT / "configs" / f"{workflow}.yaml")
            char_args = wf_config.character_set_args(influencer_id)
            if char_args:
                for arg in char_args:
                    k, _, v = arg.partition("=")
                    if k and v:
                        overrides[k] = v
                logger.info("Auto-applied LoRAs for %s: %s", influencer_id, char_args)
            else:
                # Unknown character — disable personal LoRAs to avoid
                # using hardcoded defaults from the workflow file
                overrides.setdefault("lora_high_strength", "0")
                overrides.setdefault("lora_low_strength", "0")
                logger.info("No LoRA config for %s, setting strengths to 0", influencer_id)
        except Exception:
            pass  # no character config — generate without LoRAs

    # Inject random seeds for KSampler nodes — ensures unique results per run.
    # seed_main (324) = main generation, seed_face (480) = face refine, seed_skin (494) = skin refine.
    for seed_key in ("seed_main", "seed_face", "seed_skin"):
        if seed_key not in overrides:
            overrides[seed_key] = str(random.randint(0, 2**32 - 1))

    def _run_locked():
        """Acquire per-server lock so only one generation runs at a time on each GPU."""
        with server_lock:
            if progress_fn:
                progress_fn({"phase": "running", "stage": "running"})
            return svc.run(
                workflow=workflow,
                inputs=inputs or None,
                overrides=overrides or None,
                output_dir=output_dir,
                progress_callback=progress_fn,
            )

    try:
        result = await asyncio.to_thread(_run_locked)
    except Exception as exc:
        if job_id:
            try:
                await _update_generation_job_failed(job_id, str(exc))
            except Exception:
                pass
        raise
    finally:
        # Trigger auto-shutdown check after generation completes
        try:
            manager.on_generation_complete(server_id)
        except Exception:
            logger.warning("Auto-shutdown check failed for %s", server_id, exc_info=True)

    # Post-processing (grain, sharpness, vignette)
    outputs = list(result.outputs)
    try:
        pp_path = await asyncio.to_thread(postprocess_outputs, outputs)
        if pp_path:
            outputs.append(pp_path)
            logger.info("Postprocessed: %s", pp_path)
    except Exception:
        logger.warning("Postprocessing failed", exc_info=True)

    if job_id:
        try:
            await _update_generation_job_complete(job_id, outputs, result.output_dir)
        except Exception:
            logger.warning("Failed to update generation_jobs on completion", exc_info=True)

    return {
        "influencer_id": influencer_id,
        "server_id": server_id,
        "workflow": workflow,
        "outputs": outputs,
        "output_dir": result.output_dir,
    }
