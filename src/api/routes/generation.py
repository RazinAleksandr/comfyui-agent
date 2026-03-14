"""Generation API routes — wraps VastAgentService for GPU video generation."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_job_manager, get_store, get_vast_service
from isp_pipeline.processor import postprocess_outputs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]


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


# --- Server management routes ---


@router.get("/server/status")
async def server_status() -> dict:
    """Check GPU server status."""
    svc = get_vast_service()
    result = await asyncio.to_thread(svc.status)
    return {
        "status": "running" if result.running else "offline",
        "instance_id": result.instance_id,
        "ssh_host": result.ssh_host,
        "ssh_port": result.ssh_port,
        "actual_status": result.actual_status,
        "dph_total": result.dph_total,
        "ssh_reachable": result.ssh_reachable,
    }


@router.post("/server/up")
async def server_up(body: ServerRequest) -> dict:
    """Start the GPU server. Returns job_id (long-running)."""
    jm = get_job_manager()
    job_id = jm.submit(_do_server_up, body.workflow)
    return {"job_id": job_id}


@router.post("/server/down")
async def server_down() -> dict:
    """Destroy the GPU server."""
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
    job_id = jm.submit(
        _do_generation,
        workflow=body.workflow,
        image_path=image_path or "",
        video_path=body.reference_video or "",
        prompt=body.prompt,
        output_dir=output_dir,
        set_args=body.set_args,
        influencer_id=body.influencer_id,
    )
    return {"job_id": job_id}


# --- Async job functions ---


async def _do_server_up(workflow: str) -> dict:
    """Bring up the GPU server."""
    svc = get_vast_service()

    # Check if already running
    current = await asyncio.to_thread(svc.status)
    if current.running:
        return {
            "status": "already_running",
            "instance_id": current.instance_id,
            "dph_total": current.dph_total,
        }

    result = await asyncio.to_thread(svc.up, workflow)
    return {
        "status": "started",
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
) -> dict:
    """Run generation via VastAgentService."""
    svc = get_vast_service()

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

    result = await asyncio.to_thread(
        svc.run,
        workflow=workflow,
        inputs=inputs or None,
        overrides=overrides or None,
        output_dir=output_dir,
    )

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
        "workflow": workflow,
        "outputs": outputs,
        "output_dir": result.output_dir,
    }
