"""Database-backed store for influencer and pipeline data.

Provides DB read/write operations while keeping the FilesystemStore
for file operations (mkdir, file paths, video files). The DB is the
canonical source of truth for structured metadata.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from api.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DBStore:
    """DB-backed data store for influencers and pipeline runs."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Influencers
    # ------------------------------------------------------------------

    async def list_influencers(self) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM influencers ORDER BY updated_at DESC"
        )
        return [self._inflate_influencer(r) for r in rows]

    async def load_influencer(self, influencer_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM influencers WHERE influencer_id = ?", [influencer_id]
        )
        if row is None:
            return None
        return self._inflate_influencer(row)

    async def save_influencer(self, influencer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = await self.load_influencer(influencer_id)
        now = _now()

        if existing:
            # Update: merge provided fields with existing
            sets = ["updated_at = ?"]
            params: list[Any] = [now]
            for field in ("name", "description", "hashtags", "video_suggestions_requirement", "reference_image_path", "appearance_description"):
                if field in payload:
                    val = payload[field]
                    if field == "hashtags" and isinstance(val, list):
                        val = json.dumps(val)
                    sets.append(f"{field} = ?")
                    params.append(val)
            params.append(influencer_id)
            await self._db.execute(
                f"UPDATE influencers SET {', '.join(sets)} WHERE influencer_id = ?",
                params,
            )
        else:
            # Insert new
            hashtags = payload.get("hashtags", [])
            if isinstance(hashtags, list):
                hashtags = json.dumps(hashtags)
            await self._db.execute(
                "INSERT INTO influencers "
                "(influencer_id, name, description, hashtags, video_suggestions_requirement, "
                " reference_image_path, appearance_description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    influencer_id,
                    payload.get("name", "Influencer"),
                    payload.get("description"),
                    hashtags,
                    payload.get("video_suggestions_requirement"),
                    payload.get("reference_image_path"),
                    payload.get("appearance_description"),
                    now,
                    now,
                ],
            )
        return await self.load_influencer(influencer_id)  # type: ignore[return-value]

    async def delete_influencer(self, influencer_id: str) -> None:
        await self._db.execute(
            "DELETE FROM influencers WHERE influencer_id = ?", [influencer_id]
        )

    # ------------------------------------------------------------------
    # Pipeline Runs
    # ------------------------------------------------------------------

    async def save_pipeline_run(
        self, influencer_id: str, run_id: str, base_dir: str, request_json: str | None = None
    ) -> None:
        now = _now()
        await self._db.execute(
            "INSERT OR REPLACE INTO pipeline_runs "
            "(run_id, influencer_id, started_at, base_dir, request_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
            [run_id, influencer_id, now, base_dir, request_json, now, now],
        )

    async def update_pipeline_run_status(self, run_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE pipeline_runs SET status = ?, updated_at = ? WHERE run_id = ?",
            [status, _now(), run_id],
        )

    async def save_pipeline_stage(self, run_id: str, platform: str, stage_data: dict[str, Any]) -> None:
        now = _now()
        await self._db.execute(
            "INSERT OR REPLACE INTO pipeline_stages "
            "(run_id, platform, source, ingested_items, download_counts, "
            " candidate_report_path, filtered_dir, vlm_summary_path, "
            " selected_dir, accepted, rejected, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                platform,
                stage_data.get("source", ""),
                stage_data.get("ingested_items", 0),
                json.dumps(stage_data["download_counts"]) if stage_data.get("download_counts") else None,
                stage_data.get("candidate_report_path"),
                stage_data.get("filtered_dir"),
                stage_data.get("vlm_summary_path"),
                stage_data.get("selected_dir"),
                stage_data.get("accepted"),
                stage_data.get("rejected"),
                now,
            ],
        )

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    async def load_review(self, run_id: str) -> dict[str, Any] | None:
        review = await self._db.fetchone(
            "SELECT id, completed FROM reviews WHERE run_id = ?", [run_id]
        )
        if review is None:
            return None
        videos = await self._db.fetchall(
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

    async def save_review(self, run_id: str, videos: list[dict[str, Any]]) -> None:
        now = _now()
        existing = await self._db.fetchone(
            "SELECT id FROM reviews WHERE run_id = ?", [run_id]
        )
        if existing:
            review_id = existing["id"]
            await self._db.execute(
                "UPDATE reviews SET completed = 1, updated_at = ? WHERE id = ?",
                [now, review_id],
            )
            await self._db.execute("DELETE FROM review_videos WHERE review_id = ?", [review_id])
        else:
            review_id = await self._db.execute_insert(
                "INSERT INTO reviews (run_id, completed, created_at, updated_at) VALUES (?, 1, ?, ?)",
                [run_id, now, now],
            )

        for v in videos:
            await self._db.execute(
                "INSERT INTO review_videos (review_id, file_name, approved, prompt) VALUES (?, ?, ?, ?)",
                [review_id, v.get("file_name", ""), int(v.get("approved", False)), v.get("prompt", "")],
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inflate_influencer(row: dict[str, Any]) -> dict[str, Any]:
        """Convert a DB row into the dict format the rest of the code expects."""
        hashtags = row.get("hashtags")
        if isinstance(hashtags, str):
            try:
                hashtags = json.loads(hashtags)
            except (json.JSONDecodeError, TypeError):
                hashtags = []
        return {
            "influencer_id": row["influencer_id"],
            "name": row.get("name", "Influencer"),
            "description": row.get("description"),
            "hashtags": hashtags or [],
            "video_suggestions_requirement": row.get("video_suggestions_requirement"),
            "reference_image_path": row.get("reference_image_path"),
            "appearance_description": row.get("appearance_description"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
