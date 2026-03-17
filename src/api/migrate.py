"""One-time migration from filesystem JSON storage to SQLite.

Reads existing profile.json, run_manifest.json, review_manifest.json,
generation_manifest.json, and .vast-registry.json files and inserts
them into the database. Idempotent — safe to run multiple times.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from api.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def migrate_filesystem_to_db(db: Database, data_dir: Path, project_root: Path) -> None:
    """Migrate all filesystem JSON data into the SQLite database."""
    logger.info("Starting filesystem → DB migration from %s", data_dir)

    # Check if DB already has data (skip if already migrated)
    row = await db.fetchone("SELECT COUNT(*) as cnt FROM influencers")
    if row and row["cnt"] > 0:
        logger.info("Database already has %d influencers, skipping migration", row["cnt"])
        return

    influencers_dir = data_dir / "influencers"
    if not influencers_dir.exists():
        logger.info("No influencers directory found, nothing to migrate")
        return

    migrated = {"influencers": 0, "runs": 0, "stages": 0, "reviews": 0, "gen_jobs": 0, "servers": 0}

    # 1. Migrate influencers
    for profile_path in sorted(influencers_dir.glob("*/profile.json")):
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            influencer_id = str(data.get("influencer_id") or profile_path.parent.name)
            await db.execute(
                "INSERT OR IGNORE INTO influencers "
                "(influencer_id, name, description, hashtags, video_suggestions_requirement, "
                " reference_image_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    influencer_id,
                    data.get("name", "Influencer"),
                    data.get("description"),
                    json.dumps(data.get("hashtags", [])),
                    data.get("video_suggestions_requirement"),
                    data.get("reference_image_path"),
                    data.get("created_at", _now()),
                    data.get("updated_at", _now()),
                ],
            )
            migrated["influencers"] += 1
        except Exception as exc:
            logger.warning("Failed to migrate influencer %s: %s", profile_path, exc)

    # 2. Migrate pipeline runs
    for manifest_path in sorted(influencers_dir.glob("*/pipeline_runs/*/run_manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_id = manifest_path.parent.name
            influencer_id = data.get("influencer_id")
            if not influencer_id:
                # Try to infer from directory structure
                influencer_id = manifest_path.parent.parent.parent.name

            # Ensure influencer exists
            exists = await db.fetchone(
                "SELECT 1 FROM influencers WHERE influencer_id = ?", [influencer_id]
            )
            if not exists:
                continue

            await db.execute(
                "INSERT OR IGNORE INTO pipeline_runs "
                "(run_id, influencer_id, started_at, base_dir, request_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    influencer_id,
                    data.get("started_at", _now()),
                    str(manifest_path.parent),
                    json.dumps(data.get("request")) if data.get("request") else None,
                    "completed",  # Existing runs are assumed completed
                    data.get("started_at", _now()),
                    _now(),
                ],
            )
            migrated["runs"] += 1

            # Migrate platform stages
            for plat in data.get("platforms", []):
                platform_name = plat.get("platform", "unknown")
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO pipeline_stages "
                        "(run_id, platform, source, ingested_items, download_counts, "
                        " candidate_report_path, filtered_dir, vlm_summary_path, "
                        " selected_dir, accepted, rejected, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            run_id,
                            platform_name,
                            plat.get("source", ""),
                            plat.get("ingested_items", 0),
                            json.dumps(plat.get("download_counts")) if plat.get("download_counts") else None,
                            plat.get("candidate_report_path"),
                            plat.get("filtered_dir"),
                            plat.get("vlm_summary_path"),
                            plat.get("selected_dir"),
                            plat.get("accepted"),
                            plat.get("rejected"),
                            data.get("started_at", _now()),
                        ],
                    )
                    migrated["stages"] += 1
                except Exception as exc:
                    logger.debug("Failed to migrate stage %s/%s: %s", run_id, platform_name, exc)

        except Exception as exc:
            logger.warning("Failed to migrate run %s: %s", manifest_path, exc)

    # 3. Migrate reviews
    for review_path in sorted(influencers_dir.glob("*/pipeline_runs/*/review_manifest.json")):
        try:
            data = json.loads(review_path.read_text(encoding="utf-8"))
            run_id = review_path.parent.name
            # Check run exists in DB
            exists = await db.fetchone(
                "SELECT 1 FROM pipeline_runs WHERE run_id = ?", [run_id]
            )
            if not exists:
                continue

            review_id = await db.execute_insert(
                "INSERT OR IGNORE INTO reviews (run_id, completed, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [run_id, int(data.get("completed", False)), _now(), _now()],
            )
            if review_id:
                for video in data.get("videos", []):
                    await db.execute(
                        "INSERT INTO review_videos (review_id, file_name, approved, prompt) "
                        "VALUES (?, ?, ?, ?)",
                        [review_id, video.get("file_name", ""), int(video.get("approved", False)),
                         video.get("prompt", "")],
                    )
                migrated["reviews"] += 1
        except Exception as exc:
            logger.debug("Failed to migrate review %s: %s", review_path, exc)

    # 4. Migrate generation manifests (create job records for historical data)
    for gen_path in sorted(influencers_dir.glob("*/pipeline_runs/*/generation_manifest.json")):
        try:
            data = json.loads(gen_path.read_text(encoding="utf-8"))
            run_id = gen_path.parent.name
            influencer_id = gen_path.parent.parent.parent.name

            for job_entry in data.get("jobs", []):
                job_id = job_entry.get("job_id", "")
                if not job_id:
                    continue
                # Create a historical job record
                await db.execute(
                    "INSERT OR IGNORE INTO jobs "
                    "(job_id, job_type, status, created_at, influencer_id, reference_video, run_id) "
                    "VALUES (?, 'generation', 'completed', ?, ?, ?, ?)",
                    [job_id, job_entry.get("started_at", _now()), influencer_id,
                     job_entry.get("file_name"), run_id],
                )
                await db.execute(
                    "INSERT OR IGNORE INTO generation_jobs "
                    "(job_id, run_id, file_name, influencer_id, started_at, status, "
                    " outputs_json, output_dir) "
                    "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?)",
                    [
                        job_id, run_id, job_entry.get("file_name", ""),
                        influencer_id, job_entry.get("started_at", _now()),
                        json.dumps(job_entry.get("outputs")) if job_entry.get("outputs") else None,
                        job_entry.get("output_dir"),
                    ],
                )
                migrated["gen_jobs"] += 1
        except Exception as exc:
            logger.debug("Failed to migrate generation manifest %s: %s", gen_path, exc)

    # 5. Migrate server registry
    registry_path = project_root / ".vast-registry.json"
    if registry_path.exists():
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            for sid, entry in data.get("servers", {}).items():
                await db.execute(
                    "INSERT OR IGNORE INTO servers "
                    "(server_id, instance_id, ssh_host, ssh_port, dph_total, "
                    " influencer_id, workflow, auto_shutdown, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        sid,
                        entry.get("instance_id"),
                        entry.get("ssh_host"),
                        entry.get("ssh_port"),
                        entry.get("dph_total"),
                        entry.get("influencer_id"),
                        entry.get("workflow", "wan_animate"),
                        int(entry.get("auto_shutdown", False)),
                        entry.get("created_at", _now()),
                        _now(),
                    ],
                )
                migrated["servers"] += 1
        except Exception as exc:
            logger.warning("Failed to migrate server registry: %s", exc)

    logger.info(
        "Migration complete: %d influencers, %d runs, %d stages, "
        "%d reviews, %d generation jobs, %d servers",
        migrated["influencers"], migrated["runs"], migrated["stages"],
        migrated["reviews"], migrated["gen_jobs"], migrated["servers"],
    )
