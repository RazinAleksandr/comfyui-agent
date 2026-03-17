"""Async SQLite database layer with WAL mode.

Provides a thin wrapper around aiosqlite for the AI Influencer Studio.
The database file lives at ``shared/studio.db`` — a single file that can
be backed up with a plain ``cp``.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class Database:
    """Async SQLite wrapper with WAL mode and convenience helpers."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the database connection and apply pragmas."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path), isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        logger.info("Database connected: %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def apply_schema(self) -> None:
        """Create tables if they don't exist yet."""
        schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
        conn = self._get_conn()
        # Filter out comment-only lines and PRAGMAs (already set in connect()),
        # then execute the remaining SQL as individual statements.
        lines = []
        for line in schema_sql.splitlines():
            stripped = line.strip()
            if stripped.startswith("--") or not stripped:
                continue
            if stripped.upper().startswith("PRAGMA"):
                continue
            lines.append(line)
        sql_body = "\n".join(lines)
        for statement in sql_body.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    await conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    # Table/index already exists — that's fine
                    if "already exists" not in str(exc):
                        logger.debug("Schema statement warning: %s", exc)
        await conn.commit()
        logger.info("Schema applied")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    async def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> aiosqlite.Cursor:
        """Execute a single SQL statement."""
        conn = self._get_conn()
        cursor = await conn.execute(sql, params or [])
        await conn.commit()
        return cursor

    async def execute_insert(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> int:
        """Execute an INSERT and return lastrowid."""
        cursor = await self.execute(sql, params)
        return cursor.lastrowid  # type: ignore[return-value]

    async def fetchone(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        """Fetch a single row as a dict, or None."""
        conn = self._get_conn()
        cursor = await conn.execute(sql, params or [])
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetchall(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        conn = self._get_conn()
        cursor = await conn.execute(sql, params or [])
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @contextlib.asynccontextmanager
    async def transaction(self):
        """Context manager for a BEGIN IMMEDIATE transaction.

        Use for atomic read-modify-write operations (e.g. server allocation).
        """
        conn = self._get_conn()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
