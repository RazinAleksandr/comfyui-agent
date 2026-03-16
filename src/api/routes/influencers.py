"""Influencer CRUD routes — filesystem-backed."""
from __future__ import annotations

import shutil
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_store

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
    store = get_store()
    records = store.list_influencers()
    return [_to_out(r) for r in records]


@router.get("/{influencer_id}")
async def get_influencer(influencer_id: str) -> InfluencerOut:
    store = get_store()
    record = store.load_influencer(influencer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Influencer not found")
    return _to_out(record)


@router.delete("/{influencer_id}")
async def delete_influencer(influencer_id: str) -> dict:
    """Delete an influencer and all associated data."""
    store = get_store()
    record = store.load_influencer(influencer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Influencer not found")
    store.delete_influencer(influencer_id)
    return {"deleted": influencer_id}


@router.put("/{influencer_id}")
async def upsert_influencer(influencer_id: str, body: InfluencerUpsertRequest) -> InfluencerOut:
    store = get_store()
    record = store.save_influencer(influencer_id, body.model_dump(exclude_unset=True))
    return _to_out(record)


@router.post("/{influencer_id}/reference-image")
async def upload_reference_image(influencer_id: str, file: UploadFile) -> dict:
    """Upload a reference image for an influencer."""
    store = get_store()
    record = store.load_influencer(influencer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Influencer not found")

    ext = _safe_extension(file.filename or "image.jpg")
    dest = store.influencer_dir(influencer_id) / f"reference{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    rel_path = str(dest.relative_to(store.data_dir))
    store.save_influencer(influencer_id, {"reference_image_path": rel_path})
    return {"reference_image_path": rel_path}


def _safe_extension(filename: str) -> str:
    """Extract file extension, default to .jpg."""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            return ext
    return ".jpg"


def _to_out(record) -> InfluencerOut:
    profile_image_url = None
    if record.reference_image_path:
        # Add cache-busting param so browser reloads after image changes
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
