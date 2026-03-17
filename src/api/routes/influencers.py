"""Influencer CRUD routes — DB-backed with filesystem for files."""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_db_store, get_store

router = APIRouter(prefix="/influencers", tags=["influencers"])


class InfluencerUpsertRequest(BaseModel):
    name: str = "Influencer"
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    video_suggestions_requirement: str | None = None
    reference_image_path: str | None = None


class InfluencerOut(BaseModel):
    influencer_id: str
    name: str
    description: str | None = None
    hashtags: list[str] | None = None
    video_suggestions_requirement: str | None = None
    reference_image_path: str | None = None
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
