from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FsInfluencerRecord:
    id: int
    influencer_id: str
    name: str
    reference_image_path: str | None
    description: str | None
    hashtags: list[str] | None
    video_suggestions_requirement: str | None
    created_at: datetime
    updated_at: datetime


class FilesystemStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    @property
    def influencers_dir(self) -> Path:
        return self.data_dir / "influencers"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def pipeline_runs_dir(self) -> Path:
        return self.data_dir / "pipeline_runs"

    def influencer_dir(self, influencer_id: str) -> Path:
        _validate_id(influencer_id)
        return self.influencers_dir / influencer_id

    def influencer_profile_path(self, influencer_id: str) -> Path:
        return self.influencer_dir(influencer_id) / "profile.json"

    def influencer_pipeline_runs_dir(self, influencer_id: str) -> Path:
        return self.influencer_dir(influencer_id) / "pipeline_runs"

    def list_influencers(self) -> list[FsInfluencerRecord]:
        base = self.influencers_dir
        if not base.exists():
            return []
        records: list[FsInfluencerRecord] = []
        for path in sorted(base.glob("*/profile.json")):
            record = self.load_influencer(path.parent.name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda row: (row.updated_at, row.id), reverse=True)
        return records

    def load_influencer(self, influencer_id: str) -> FsInfluencerRecord | None:
        path = self.influencer_profile_path(influencer_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = _parse_dt(payload.get("created_at"))
        updated_at = _parse_dt(payload.get("updated_at"))
        return FsInfluencerRecord(
            id=int(payload.get("id") or 0),
            influencer_id=str(payload.get("influencer_id") or influencer_id),
            name=str(payload.get("name") or "Influencer"),
            reference_image_path=payload.get("reference_image_path"),
            description=payload.get("description"),
            hashtags=list(payload.get("hashtags") or []),
            video_suggestions_requirement=payload.get("video_suggestions_requirement"),
            created_at=created_at,
            updated_at=updated_at,
        )

    def save_influencer(self, influencer_id: str, payload: dict[str, Any]) -> FsInfluencerRecord:
        normalized = str(influencer_id)
        target_dir = self.influencer_dir(normalized)
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = self.load_influencer(normalized)
        now = datetime.now(UTC)
        doc = {
            "id": existing.id if existing is not None else _stable_id(normalized),
            "influencer_id": normalized,
            "name": payload.get("name") or (existing.name if existing else "Influencer"),
            "reference_image_path": payload.get("reference_image_path") or (existing.reference_image_path if existing else None),
            "description": payload.get("description") if "description" in payload else (existing.description if existing else None),
            "hashtags": payload.get("hashtags") if "hashtags" in payload else (existing.hashtags if existing else []),
            "video_suggestions_requirement": (
                payload.get("video_suggestions_requirement")
                if "video_suggestions_requirement" in payload
                else (existing.video_suggestions_requirement if existing else None)
            ),
            "created_at": (existing.created_at if existing else now).isoformat(),
            "updated_at": now.isoformat(),
        }
        self.influencer_profile_path(normalized).write_text(
            json.dumps(doc, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.load_influencer(normalized)  # type: ignore[return-value]

    def delete_influencer(self, influencer_id: str) -> None:
        """Remove an influencer directory and all its data."""
        _validate_id(influencer_id)
        target = self.influencer_dir(influencer_id)
        if target.exists():
            shutil.rmtree(target)

    def list_pipeline_runs(self, influencer_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """List pipeline runs for an influencer, newest first."""
        runs_dir = self.influencer_pipeline_runs_dir(influencer_id)
        if not runs_dir.exists():
            return []
        runs: list[dict[str, Any]] = []
        for manifest in sorted(runs_dir.glob("*/run_manifest.json"), reverse=True):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["run_id"] = manifest.parent.name
                runs.append(payload)
            except (json.JSONDecodeError, OSError):
                continue
            if len(runs) >= limit:
                break
        return runs

    def load_pipeline_run(self, influencer_id: str, run_id: str) -> dict[str, Any] | None:
        """Load a single pipeline run manifest."""
        _validate_id(run_id)
        manifest = self.influencer_pipeline_runs_dir(influencer_id) / run_id / "run_manifest.json"
        if not manifest.exists():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["run_id"] = run_id
            return payload
        except (json.JSONDecodeError, OSError):
            return None

    def save_pipeline_manifest(self, influencer_id: str, run_id: str, payload: dict[str, Any]) -> Path:
        run_dir = self.influencer_pipeline_runs_dir(influencer_id) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        return manifest_path


def _validate_id(value: str) -> None:
    """Reject path traversal attempts in identifiers."""
    if not value or ".." in value or "/" in value or "\\" in value or "\0" in value:
        raise ValueError(f"Invalid identifier: {value!r}")


def _stable_id(value: str) -> int:
    return abs(hash(value)) % 2_000_000_000


def _parse_dt(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)
