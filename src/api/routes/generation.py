"""Generation API routes — wraps VastAgentService for GPU video generation."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_job_manager, get_server_manager, get_store, get_vast_service
from isp_pipeline.processor import postprocess_outputs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# --- Generation manifest persistence ---


def _save_generation_manifest(reference_video: str, job_id: str) -> None:
    """Persist a generation job entry to generation_manifest.json in the run directory.

    The run directory is found by walking up from the reference_video path:
    .../run_dir/platform/selected/video.mp4 -> run_dir is parent of parent of selected/.
    """
    video_p = Path(reference_video)
    if video_p.parent.name != "selected":
        return
    # selected/ -> platform_dir -> run_dir
    run_dir = video_p.parent.parent.parent
    if not run_dir.is_dir():
        return

    manifest_path = run_dir / "generation_manifest.json"
    try:
        data: dict = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    jobs: list[dict] = data.get("jobs", [])
    # Remove any previous jobs for the same file (retry replaces old entry)
    jobs = [j for j in jobs if j.get("file_name") != video_p.name]
    jobs.append({
        "file_name": video_p.name,
        "job_id": job_id,
        "started_at": datetime.now(UTC).isoformat(),
    })
    data["jobs"] = jobs
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
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
    """Check GPU server status, including any running startup job."""
    manager = get_server_manager()

    # If influencer_id provided, find their server
    if influencer_id:
        info = manager.get_influencer_server_info(influencer_id)
        server_id = info.get("server_id")
        if server_id:
            status = manager.server_status(server_id)
            # Attach startup job info
            jm = get_job_manager()
            server_jobs = jm.find_jobs(type="server_up", server_id=server_id)
            active = next((j for j in server_jobs if j.status in ("pending", "running")), None)
            status["startup_job_id"] = active.job_id if active else None
            status["startup_job_status"] = active.status if active else None
            return status

    # Fallback: legacy single-server behavior
    svc = get_vast_service()
    result = await asyncio.to_thread(svc.status)

    jm = get_job_manager()
    server_jobs = jm.find_jobs(type="server_up")
    active = next((j for j in server_jobs if j.status in ("pending", "running")), None)

    return {
        "status": "running" if result.running else "offline",
        "instance_id": result.instance_id,
        "ssh_host": result.ssh_host,
        "ssh_port": result.ssh_port,
        "actual_status": result.actual_status,
        "dph_total": result.dph_total,
        "ssh_reachable": result.ssh_reachable,
        "startup_job_id": active.job_id if active else None,
        "startup_job_status": active.status if active else None,
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
    entry = manager._registry.get_server(server_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        await asyncio.to_thread(manager.shutdown_server, server_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "destroyed"}


@router.post("/server/{server_id}/auto-shutdown")
async def set_auto_shutdown(server_id: str, body: AutoShutdownRequest) -> dict:
    """Toggle auto-shutdown flag for a server."""
    manager = get_server_manager()
    entry = manager._registry.get_server(server_id)
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "destroyed"}


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

    # Persist job to generation manifest so it survives page refreshes
    if body.reference_video:
        try:
            _save_generation_manifest(body.reference_video, job_id)
        except Exception:
            logger.warning("Failed to save generation manifest", exc_info=True)

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
            for arg in char_args:
                k, _, v = arg.partition("=")
                if k and v:
                    overrides[k] = v
            if char_args:
                logger.info("Auto-applied LoRAs for %s: %s", influencer_id, char_args)
        except Exception:
            pass  # no character config — generate without LoRAs

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

    return {
        "influencer_id": influencer_id,
        "server_id": server_id,
        "workflow": workflow,
        "outputs": outputs,
        "output_dir": result.output_dir,
    }
