"""One-time migration: convert absolute paths to relative in DB and manifests.

Called during app startup. Idempotent -- skips rows/files that are already
relative or don't contain absolute paths.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from api.database import Database
from api.path_utils import to_relative

logger = logging.getLogger(__name__)


async def migrate_paths_to_relative(db: Database, data_dir: Path) -> None:
    """Convert absolute paths in DB rows and manifest files to relative."""
    migrated = {"db_rows": 0, "manifest_files": 0}

    # --- DB migration: simple text columns ---
    simple_columns = [
        ("pipeline_runs", "base_dir", "run_id"),
        ("pipeline_stages", "candidate_report_path", "id"),
        ("pipeline_stages", "filtered_dir", "id"),
        ("pipeline_stages", "vlm_summary_path", "id"),
        ("pipeline_stages", "selected_dir", "id"),
        ("generation_jobs", "output_dir", "id"),
        ("generation_jobs", "aligned_image_path", "id"),
        ("jobs", "reference_video", "job_id"),
    ]
    for table, column, pk in simple_columns:
        try:
            rows = await db.fetchall(
                f"SELECT {pk}, {column} FROM {table} WHERE {column} LIKE '/%'"
            )
            for row in rows:
                val = row[column]
                if val and val.startswith("/"):
                    rel = to_relative(val, data_dir)
                    await db.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {pk} = ?",
                        [rel, row[pk]],
                    )
                    migrated["db_rows"] += 1
        except Exception:
            logger.debug("Skipping %s.%s migration", table, column, exc_info=True)

    # --- DB migration: generation_jobs.outputs_json (JSON array) ---
    try:
        rows = await db.fetchall(
            "SELECT id, outputs_json FROM generation_jobs "
            "WHERE outputs_json IS NOT NULL AND outputs_json LIKE '%\"/%'"
        )
        for row in rows:
            try:
                outputs = json.loads(row["outputs_json"])
                converted = [
                    to_relative(p, data_dir) if isinstance(p, str) and p.startswith("/") else p
                    for p in outputs
                ]
                await db.execute(
                    "UPDATE generation_jobs SET outputs_json = ? WHERE id = ?",
                    [json.dumps(converted), row["id"]],
                )
                migrated["db_rows"] += 1
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        logger.debug("Skipping generation_jobs.outputs_json migration", exc_info=True)

    # --- DB migration: jobs.result_json (JSON object with outputs list) ---
    try:
        rows = await db.fetchall(
            "SELECT job_id, result_json FROM jobs "
            "WHERE result_json IS NOT NULL AND result_json LIKE '%\"/%'"
        )
        for row in rows:
            try:
                result = json.loads(row["result_json"])
                changed = False
                if isinstance(result.get("outputs"), list):
                    result["outputs"] = [
                        to_relative(p, data_dir) if isinstance(p, str) and p.startswith("/") else p
                        for p in result["outputs"]
                    ]
                    changed = True
                if isinstance(result.get("output_dir"), str) and result["output_dir"].startswith("/"):
                    result["output_dir"] = to_relative(result["output_dir"], data_dir)
                    changed = True
                if changed:
                    await db.execute(
                        "UPDATE jobs SET result_json = ? WHERE job_id = ?",
                        [json.dumps(result), row["job_id"]],
                    )
                    migrated["db_rows"] += 1
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        logger.debug("Skipping jobs.result_json migration", exc_info=True)

    # --- Manifest file migration ---
    influencers_dir = data_dir / "influencers"
    if influencers_dir.is_dir():
        # Only rewrite known path-valued keys to avoid corrupting other fields
        _path_keys = (
            "base_dir", "filtered_dir", "selected_dir",
            "candidate_report_path", "vlm_summary_path",
        )
        _keys_alt = "|".join(re.escape(k) for k in _path_keys)
        pattern = re.compile(
            rf'("(?:{_keys_alt})"\s*:\s*")(/[^"]*?)/shared/',
        )
        for glob_pattern in ("**/run_manifest.json", "**/platform_manifest.json"):
            for manifest in influencers_dir.glob(glob_pattern):
                try:
                    text = manifest.read_text(encoding="utf-8")
                    new_text = pattern.sub(r'\1', text)
                    if new_text != text:
                        manifest.write_text(new_text, encoding="utf-8")
                        migrated["manifest_files"] += 1
                except Exception:
                    logger.debug("Failed to migrate %s", manifest, exc_info=True)

    logger.info(
        "Path migration complete: %d DB rows, %d manifest files",
        migrated["db_rows"], migrated["manifest_files"],
    )
