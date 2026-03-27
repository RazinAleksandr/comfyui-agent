"""Influencer CRUD routes — DB-backed with filesystem for files."""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_config, get_db, get_db_store, get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/influencers", tags=["influencers"])


class InfluencerUpsertRequest(BaseModel):
    name: str = "Influencer"
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    video_suggestions_requirement: str | None = None
    reference_image_path: str | None = None
    appearance_description: str | None = None


class InfluencerOut(BaseModel):
    influencer_id: str
    name: str
    description: str | None = None
    hashtags: list[str] | None = None
    video_suggestions_requirement: str | None = None
    reference_image_path: str | None = None
    appearance_description: str | None = None
    profile_image_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@router.get("")
async def list_influencers() -> list[InfluencerOut]:
    db_store = get_db_store()
    records = await db_store.list_influencers()
    if not records:
        # Fallback to filesystem if DB empty (pre-migration)
        fs_store = get_store()
        fs_records = fs_store.list_influencers()
        return [_fs_to_out(r) for r in fs_records]
    return [_dict_to_out(r) for r in records]


@router.get("/{influencer_id}")
async def get_influencer(influencer_id: str) -> InfluencerOut:
    db_store = get_db_store()
    record = await db_store.load_influencer(influencer_id)
    if record is None:
        # Fallback to filesystem
        fs_store = get_store()
        fs_record = fs_store.load_influencer(influencer_id)
        if fs_record is None:
            raise HTTPException(status_code=404, detail="Influencer not found")
        return _fs_to_out(fs_record)
    return _dict_to_out(record)


@router.delete("/{influencer_id}")
async def delete_influencer(influencer_id: str) -> dict:
    """Delete an influencer and all associated data."""
    db_store = get_db_store()
    record = await db_store.load_influencer(influencer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Influencer not found")
    # Delete from DB
    await db_store.delete_influencer(influencer_id)
    # Also delete filesystem directory
    fs_store = get_store()
    fs_store.delete_influencer(influencer_id)
    return {"deleted": influencer_id}


@router.put("/{influencer_id}")
async def upsert_influencer(influencer_id: str, body: InfluencerUpsertRequest) -> InfluencerOut:
    db_store = get_db_store()
    # Ensure influencer directory exists on filesystem (for reference images, pipeline runs)
    fs_store = get_store()
    fs_store.influencer_dir(influencer_id).mkdir(parents=True, exist_ok=True)
    record = await db_store.save_influencer(influencer_id, body.model_dump(exclude_unset=True))
    return _dict_to_out(record)


@router.post("/{influencer_id}/reference-image")
async def upload_reference_image(influencer_id: str, file: UploadFile) -> dict:
    """Upload a reference image for an influencer."""
    db_store = get_db_store()
    record = await db_store.load_influencer(influencer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Influencer not found")

    fs_store = get_store()
    ext = _safe_extension(file.filename or "image.jpg")
    dest = fs_store.influencer_dir(influencer_id) / f"reference{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    rel_path = str(dest.relative_to(fs_store.data_dir))
    # Update both DB and filesystem
    await db_store.save_influencer(influencer_id, {"reference_image_path": rel_path})
    try:
        fs_store.save_influencer(influencer_id, {"reference_image_path": rel_path})
    except Exception:
        pass
    return {"reference_image_path": rel_path}


@router.post("/{influencer_id}/generate-appearance")
async def generate_appearance(influencer_id: str) -> dict:
    """Generate an appearance description from the reference image using Gemini."""
    db_store = get_db_store()
    record = await db_store.load_influencer(influencer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Influencer not found")

    ref_path = record.get("reference_image_path")
    if not ref_path:
        raise HTTPException(status_code=400, detail="No reference image uploaded")

    fs_store = get_store()
    image_path = fs_store.data_dir / ref_path
    if not image_path.is_file():
        raise HTTPException(status_code=400, detail="Reference image file not found")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    from trend_parser.gemini import call_gemini_image, sanitize_error_message

    prompt = (
        "Describe the person in this image in detail for use as a reference in AI video generation prompts. "
        "Focus on:\n"
        "- Physical appearance: hair color/style, skin tone, body type, approximate age\n"
        "- Facial features: eye shape, face shape, distinctive features\n"
        "- Makeup if visible\n\n"
        "Write 2-4 sentences. Be specific and objective. "
        "Do NOT describe clothing, accessories, background, setting, or mood — only the person's physical features."
    )

    try:
        description = call_gemini_image(
            model=get_config().gemini_model,
            api_key=api_key,
            image_path=image_path,
            prompt=prompt,
            timeout_sec=60,
        )
    except Exception as exc:
        safe_msg = sanitize_error_message(str(exc), api_key=api_key)
        logger.error("Gemini appearance generation failed: %s", safe_msg)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {safe_msg}")

    description = description.strip()
    await db_store.save_influencer(influencer_id, {"appearance_description": description})
    return {"appearance_description": description}


@router.get("/{influencer_id}/generated-content")
async def get_generated_content(influencer_id: str) -> list[dict]:
    """Return all completed generation videos for an influencer with source metadata."""
    db = get_db()
    store = get_store()
    data_dir = store.data_dir

    rows = await db.fetchall(
        "SELECT gj.job_id, gj.file_name, gj.run_id, gj.outputs_json, gj.output_dir, "
        "j.result_json, j.completed_at "
        "FROM generation_jobs gj "
        "LEFT JOIN jobs j ON j.job_id = gj.job_id "
        "WHERE gj.influencer_id = ? AND gj.status = 'completed' "
        "  AND gj.id = ("
        "    SELECT MAX(gj2.id) FROM generation_jobs gj2"
        "    WHERE gj2.run_id = gj.run_id AND gj2.file_name = gj.file_name"
        "      AND gj2.status = 'completed'"
        "  ) "
        "ORDER BY j.completed_at DESC",
        [influencer_id],
    )

    if not rows:
        return []

    # Cache platform manifests per (run_id, platform) to avoid re-reading
    manifest_cache: dict[str, list[dict]] = {}

    result = []
    for row in rows:
        file_name = row["file_name"]
        run_id = row["run_id"]

        # Find the best output video (postprocessed > upscaled > refined > raw)
        raw_outputs: list[str] = []
        outputs_json = row.get("outputs_json")
        if outputs_json:
            try:
                raw_outputs = json.loads(outputs_json)
            except (json.JSONDecodeError, TypeError):
                pass
        if not raw_outputs:
            result_json = row.get("result_json")
            if result_json:
                try:
                    raw_outputs = json.loads(result_json).get("outputs", [])
                except (json.JSONDecodeError, TypeError):
                    pass

        if not raw_outputs:
            continue

        # Pick best output: prefer isp > final > postprocessed > upscaled > refined > raw
        from api.path_utils import to_absolute
        _OUTPUT_PRIORITY = ("output_isp", "output_final", "postprocessed", "upscaled", "refined", "output_raw", "raw")
        video_path = None
        for preferred in _OUTPUT_PRIORITY:
            for p in raw_outputs:
                if isinstance(p, str) and preferred in p and to_absolute(p, data_dir).is_file():
                    video_path = p
                    break
            if video_path:
                break
        # Fallback: last existing file
        if not video_path:
            for p in reversed(raw_outputs):
                if isinstance(p, str) and to_absolute(p, data_dir).is_file():
                    video_path = p
                    break
        if not video_path:
            continue

        video_url = _path_to_url(video_path, data_dir)
        if not video_url:
            continue

        # Determine platform from filename (e.g. tiktok_20260314_views18500_uid123.mp4)
        platform = file_name.split("_")[0] if "_" in file_name else ""

        # Look up source metadata from platform manifest
        source_info = _lookup_source_info(
            influencer_id, run_id, platform, file_name, data_dir, manifest_cache
        )

        result.append({
            "job_id": row.get("job_id", ""),
            "file_name": file_name,
            "run_id": run_id,
            "video_url": video_url,
            "completed_at": row.get("completed_at"),
            "source": source_info,
        })

    return result


def _path_to_url(stored_path: str, data_dir: Path) -> str:
    """Convert a stored (absolute or relative) path to a /files/ URL."""
    if not stored_path:
        return ""
    try:
        from api.path_utils import to_absolute
        abs_path = to_absolute(stored_path, data_dir)
        rel = abs_path.relative_to(data_dir)
        return f"/files/{rel}"
    except ValueError:
        return ""


def _lookup_source_info(
    influencer_id: str,
    run_id: str,
    platform: str,
    file_name: str,
    data_dir: Path,
    cache: dict[str, list[dict]],
) -> dict:
    """Look up original source metadata from the platform manifest."""
    cache_key = f"{run_id}/{platform}"
    if cache_key not in cache:
        manifest_path = (
            data_dir
            / "influencers"
            / influencer_id
            / "pipeline_runs"
            / run_id
            / platform
            / "platform_manifest.json"
        )
        try:
            with open(manifest_path) as f:
                data = json.load(f)
            cache[cache_key] = data.get("ingested_items", [])
        except Exception:
            cache[cache_key] = []

    # Match by source_item_id embedded in the filename (uid{id})
    # Filename format: {platform}_{date}_views{count}_uid{source_item_id}.mp4
    uid = ""
    stem = Path(file_name).stem
    if "_uid" in stem:
        uid = stem.split("_uid")[-1]

    for item in cache[cache_key]:
        if uid and item.get("source_item_id") == uid:
            return {
                "video_url": item.get("video_url", ""),
                "platform": item.get("platform", platform),
                "views": item.get("views", 0),
                "likes": item.get("likes", 0),
                "caption": item.get("caption", ""),
            }

    # Fallback: parse what we can from the filename
    views = 0
    if "_views" in stem:
        try:
            views_part = stem.split("_views")[1].split("_")[0]
            views = int(views_part)
        except (ValueError, IndexError):
            pass

    return {
        "video_url": "",
        "platform": platform,
        "views": views,
        "likes": 0,
        "caption": "",
    }


def _safe_extension(filename: str) -> str:
    """Extract file extension, default to .jpg."""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            return ext
    return ".jpg"


def _dict_to_out(record: dict) -> InfluencerOut:
    profile_image_url = None
    if record.get("reference_image_path"):
        updated = record.get("updated_at", "")
        try:
            ts = int(datetime.fromisoformat(str(updated).replace("Z", "+00:00")).timestamp()) if updated else 0
        except (ValueError, TypeError):
            ts = 0
        profile_image_url = "/files/" + quote(record["reference_image_path"], safe="/") + f"?v={ts}"
    return InfluencerOut(
        influencer_id=record["influencer_id"],
        name=record.get("name", "Influencer"),
        description=record.get("description"),
        hashtags=record.get("hashtags"),
        video_suggestions_requirement=record.get("video_suggestions_requirement"),
        reference_image_path=record.get("reference_image_path"),
        appearance_description=record.get("appearance_description"),
        profile_image_url=profile_image_url,
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
    )


def _fs_to_out(record) -> InfluencerOut:
    """Convert old FsInfluencerRecord to InfluencerOut."""
    profile_image_url = None
    if record.reference_image_path:
        ts = int(record.updated_at.timestamp()) if record.updated_at else 0
        profile_image_url = "/files/" + quote(record.reference_image_path, safe="/") + f"?v={ts}"
    return InfluencerOut(
        influencer_id=record.influencer_id,
        name=record.name,
        description=record.description,
        hashtags=record.hashtags,
        video_suggestions_requirement=record.video_suggestions_requirement,
        reference_image_path=record.reference_image_path,
        profile_image_url=profile_image_url,
        created_at=record.created_at.isoformat() if record.created_at else None,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
    )
