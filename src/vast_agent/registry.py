"""Server registry for multi-server VastAI management.

Replaces the single `.vast-instance.json` with `.vast-registry.json`
that tracks multiple servers mapped to influencers.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OLD_STATE_FILE = ".vast-instance.json"
DEFAULT_REGISTRY_FILE = ".vast-registry.json"


@dataclass
class ServerEntry:
    """A single server entry in the registry."""

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
    def from_dict(cls, data: dict) -> ServerEntry:
        return cls(
            instance_id=data.get("instance_id"),
            ssh_host=data.get("ssh_host"),
            ssh_port=data.get("ssh_port"),
            dph_total=data.get("dph_total"),
            influencer_id=data.get("influencer_id"),
            workflow=data.get("workflow", "wan_animate"),
            created_at=data.get("created_at", ""),
            auto_shutdown=data.get("auto_shutdown", False),
        )


class ServerRegistry:
    """Thread-safe registry of VastAI servers.

    Stores data in a JSON file at the given path. On init, migrates
    the old single-instance `.vast-instance.json` if the registry
    file does not yet exist.
    """

    def __init__(self, registry_path: Path) -> None:
        self._path = registry_path
        self._lock = threading.Lock()
        self._migrate_old_state()

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def _migrate_old_state(self) -> None:
        """Migrate old `.vast-instance.json` into the registry if needed."""
        if self._path.exists():
            return

        old_file = self._path.parent / OLD_STATE_FILE
        if not old_file.exists():
            return

        try:
            old_data = json.loads(old_file.read_text())
            instance_id = old_data.get("instance_id")
            if instance_id is None:
                return

            server_id = f"srv_{instance_id}"
            entry = ServerEntry(
                instance_id=instance_id,
                ssh_host=old_data.get("ssh_host"),
                ssh_port=old_data.get("ssh_port"),
                dph_total=old_data.get("dph_total"),
                created_at=datetime.now(UTC).isoformat(),
            )
            data = {"servers": {server_id: entry.to_dict()}}
            self._path.write_text(json.dumps(data, indent=2) + "\n")
            logger.info("Migrated old state file to registry: %s", server_id)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to migrate old state file: %s", exc)

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, ServerEntry]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
            return {
                sid: ServerEntry.from_dict(entry)
                for sid, entry in data.get("servers", {}).items()
            }
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, servers: dict[str, ServerEntry]) -> None:
        data = {"servers": {sid: entry.to_dict() for sid, entry in servers.items()}}
        self._path.write_text(json.dumps(data, indent=2) + "\n")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_server(self, server_id: str, entry: ServerEntry) -> None:
        """Add or update a server entry."""
        with self._lock:
            servers = self._read()
            servers[server_id] = entry
            self._write(servers)

    def remove_server(self, server_id: str) -> None:
        """Remove a server entry."""
        with self._lock:
            servers = self._read()
            servers.pop(server_id, None)
            self._write(servers)

    def get_server(self, server_id: str) -> ServerEntry | None:
        """Get a server entry by ID."""
        with self._lock:
            servers = self._read()
            return servers.get(server_id)

    def list_servers(self) -> dict[str, ServerEntry]:
        """Return all server entries."""
        with self._lock:
            return self._read()

    def find_by_influencer(self, influencer_id: str) -> tuple[str, ServerEntry] | None:
        """Find a server assigned to the given influencer."""
        with self._lock:
            servers = self._read()
            for sid, entry in servers.items():
                if entry.influencer_id == influencer_id:
                    return sid, entry
            return None

    def find_free_server(
        self,
        exclude_influencer_id: str | None = None,
        busy_server_ids: set[str] | None = None,
    ) -> tuple[str, ServerEntry] | None:
        """Find a server with no active generation jobs.

        Args:
            exclude_influencer_id: Skip servers owned by this influencer.
            busy_server_ids: Set of server IDs that have active jobs.
        """
        busy = busy_server_ids or set()
        with self._lock:
            servers = self._read()
            for sid, entry in servers.items():
                if exclude_influencer_id and entry.influencer_id == exclude_influencer_id:
                    continue
                if sid not in busy:
                    return sid, entry
            return None

    def update_entry(self, server_id: str, **kwargs: object) -> None:
        """Update specific fields on a server entry."""
        with self._lock:
            servers = self._read()
            entry = servers.get(server_id)
            if entry is None:
                return
            for key, val in kwargs.items():
                if hasattr(entry, key):
                    setattr(entry, key, val)
            servers[server_id] = entry
            self._write(servers)
