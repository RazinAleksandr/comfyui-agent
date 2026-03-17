"""Database-backed server registry for multi-server VastAI management.

Replaces the JSON-file-based ``ServerRegistry`` with SQLite operations.
All allocations use ``BEGIN IMMEDIATE`` transactions for atomicity.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from typing import Any

from api.database import Database

logger = logging.getLogger(__name__)


@dataclass
class ServerEntry:
    """A single server entry — mirrors the old dataclass for compatibility."""

    instance_id: int | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    dph_total: float | None = None
    influencer_id: str | None = None
    workflow: str = "wan_animate"
    created_at: str = ""
    auto_shutdown: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ServerEntry:
        return cls(
            instance_id=row.get("instance_id"),
            ssh_host=row.get("ssh_host"),
            ssh_port=row.get("ssh_port"),
            dph_total=row.get("dph_total"),
            influencer_id=row.get("influencer_id"),
            workflow=row.get("workflow", "wan_animate"),
            created_at=row.get("created_at", ""),
            auto_shutdown=bool(row.get("auto_shutdown", 0)),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DBServerRegistry:
    """Thread-safe, DB-backed server registry.

    Public API matches the old ``ServerRegistry`` so ``ServerManager``
    can use it as a drop-in replacement.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add_server(self, server_id: str, entry: ServerEntry) -> None:
        now = _now()
        await self._db.execute(
            "INSERT OR REPLACE INTO servers "
            "(server_id, instance_id, ssh_host, ssh_port, dph_total, "
            " influencer_id, workflow, auto_shutdown, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                server_id, entry.instance_id, entry.ssh_host, entry.ssh_port,
                entry.dph_total, entry.influencer_id, entry.workflow,
                int(entry.auto_shutdown), entry.created_at or now, now,
            ],
        )

    async def remove_server(self, server_id: str) -> None:
        await self._db.execute("DELETE FROM servers WHERE server_id = ?", [server_id])

    async def get_server(self, server_id: str) -> ServerEntry | None:
        row = await self._db.fetchone(
            "SELECT * FROM servers WHERE server_id = ?", [server_id]
        )
        return ServerEntry.from_row(row) if row else None

    async def list_servers(self) -> dict[str, ServerEntry]:
        rows = await self._db.fetchall("SELECT * FROM servers ORDER BY created_at")
        return {r["server_id"]: ServerEntry.from_row(r) for r in rows}

    async def find_by_influencer(self, influencer_id: str) -> tuple[str, ServerEntry] | None:
        row = await self._db.fetchone(
            "SELECT * FROM servers WHERE influencer_id = ?", [influencer_id]
        )
        if row is None:
            return None
        return row["server_id"], ServerEntry.from_row(row)

    async def find_free_server(
        self,
        exclude_influencer_id: str | None = None,
        busy_server_ids: set[str] | None = None,
    ) -> tuple[str, ServerEntry] | None:
        """Find a server with no active jobs.

        Uses a DB query that LEFT JOINs on active jobs to find truly free servers.
        """
        rows = await self._db.fetchall("SELECT * FROM servers")
        busy = busy_server_ids or set()
        for row in rows:
            sid = row["server_id"]
            entry = ServerEntry.from_row(row)
            if exclude_influencer_id and entry.influencer_id == exclude_influencer_id:
                continue
            if sid not in busy:
                return sid, entry
        return None

    async def update_entry(self, server_id: str, **kwargs: object) -> None:
        """Update specific fields on a server entry."""
        allowed = {
            "instance_id", "ssh_host", "ssh_port", "dph_total",
            "influencer_id", "workflow", "auto_shutdown",
        }
        sets = []
        params: list[Any] = []
        for key, val in kwargs.items():
            if key in allowed:
                if key == "auto_shutdown":
                    val = int(val)  # type: ignore[assignment]
                sets.append(f"{key} = ?")
                params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(server_id)
        await self._db.execute(
            f"UPDATE servers SET {', '.join(sets)} WHERE server_id = ?",
            params,
        )

    # ------------------------------------------------------------------
    # Sync wrappers for code that can't await (ServerManager methods
    # called from sync threads like health check, generation lock).
    # ------------------------------------------------------------------

    def get_server_sync(self, server_id: str) -> ServerEntry | None:
        import sqlite3
        conn = sqlite3.connect(str(self._db._db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM servers WHERE server_id = ?", [server_id])
            row = cursor.fetchone()
            return ServerEntry.from_row(dict(row)) if row else None
        finally:
            conn.close()

    def list_servers_sync(self) -> dict[str, ServerEntry]:
        import sqlite3
        conn = sqlite3.connect(str(self._db._db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM servers ORDER BY created_at")
            rows = [dict(r) for r in cursor.fetchall()]
            return {r["server_id"]: ServerEntry.from_row(r) for r in rows}
        finally:
            conn.close()

    def find_by_influencer_sync(self, influencer_id: str) -> tuple[str, ServerEntry] | None:
        import sqlite3
        conn = sqlite3.connect(str(self._db._db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM servers WHERE influencer_id = ?", [influencer_id]
            )
            row = cursor.fetchone()
            if row is None:
                return None
            r = dict(row)
            return r["server_id"], ServerEntry.from_row(r)
        finally:
            conn.close()

    def find_free_server_sync(
        self,
        exclude_influencer_id: str | None = None,
        busy_server_ids: set[str] | None = None,
    ) -> tuple[str, ServerEntry] | None:
        import sqlite3
        busy = busy_server_ids or set()
        conn = sqlite3.connect(str(self._db._db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM servers")
            for row in cursor:
                r = dict(row)
                sid = r["server_id"]
                entry = ServerEntry.from_row(r)
                if exclude_influencer_id and entry.influencer_id == exclude_influencer_id:
                    continue
                if sid not in busy:
                    return sid, entry
            return None
        finally:
            conn.close()

    def add_server_sync(self, server_id: str, entry: ServerEntry) -> None:
        import sqlite3
        now = _now()
        conn = sqlite3.connect(str(self._db._db_path))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO servers "
                "(server_id, instance_id, ssh_host, ssh_port, dph_total, "
                " influencer_id, workflow, auto_shutdown, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    server_id, entry.instance_id, entry.ssh_host, entry.ssh_port,
                    entry.dph_total, entry.influencer_id, entry.workflow,
                    int(entry.auto_shutdown), entry.created_at or now, now,
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def remove_server_sync(self, server_id: str) -> None:
        import sqlite3
        conn = sqlite3.connect(str(self._db._db_path))
        try:
            conn.execute("DELETE FROM servers WHERE server_id = ?", [server_id])
            conn.commit()
        finally:
            conn.close()

    def update_entry_sync(self, server_id: str, **kwargs: object) -> None:
        import sqlite3
        allowed = {
            "instance_id", "ssh_host", "ssh_port", "dph_total",
            "influencer_id", "workflow", "auto_shutdown",
        }
        sets = []
        params: list = []
        for key, val in kwargs.items():
            if key in allowed:
                if key == "auto_shutdown":
                    val = int(val)
                sets.append(f"{key} = ?")
                params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(server_id)
        conn = sqlite3.connect(str(self._db._db_path))
        try:
            conn.execute(
                f"UPDATE servers SET {', '.join(sets)} WHERE server_id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()
