"""Server manager for multi-server VastAI allocation.

The brain that allocates servers to influencers, manages lifecycle,
and coordinates with the job system via an injectable checker callback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from vast_agent.config import VastConfig
from vast_agent.registry import ServerEntry, ServerRegistry
from vast_agent.service import VastAgentService

logger = logging.getLogger(__name__)


class ServerManager:
    """Allocates and manages multiple VastAI servers."""

    def __init__(
        self,
        registry: ServerRegistry,
        config: VastConfig,
        project_root: Path,
        job_checker: Callable[[str], int] | None = None,
    ) -> None:
        """Initialize the server manager.

        Args:
            registry: Server registry for persistence.
            config: VastAI configuration.
            project_root: Root directory of the project.
            job_checker: Callable that takes a server_id and returns the
                number of active generation jobs on it. Injected by the
                API layer to avoid circular imports.
        """
        self._registry = registry
        self._config = config
        self._project_root = project_root
        self._job_checker = job_checker
        self._services: dict[str, VastAgentService] = {}
        self._server_locks: dict[str, threading.Lock] = {}
        self._health_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Service factory
    # ------------------------------------------------------------------

    def _is_mock(self) -> bool:
        return os.getenv("VAST_MOCK", "").strip() in ("1", "true", "yes")

    def get_server_lock(self, server_id: str) -> threading.Lock:
        """Get a per-server lock for serializing generation runs."""
        if server_id not in self._server_locks:
            self._server_locks[server_id] = threading.Lock()
        return self._server_locks[server_id]

    def get_or_create_service(self, server_id: str) -> VastAgentService:
        """Get or create a VastAgentService for a specific server."""
        if server_id in self._services:
            return self._services[server_id]

        if self._is_mock():
            from vast_agent.service_mock import VastAgentServiceMock
            svc = VastAgentServiceMock()  # type: ignore[assignment]
        else:
            state_file = self._project_root / f".vast-server-{server_id}.json"
            # Seed state file from registry if it has instance data
            entry = self._registry.get_server(server_id)
            if entry and entry.instance_id and not state_file.exists():
                import json
                state = {
                    "instance_id": entry.instance_id,
                    "ssh_host": entry.ssh_host,
                    "ssh_port": entry.ssh_port,
                    "dph_total": entry.dph_total,
                }
                state_file.write_text(json.dumps(state, indent=2) + "\n")

            svc = VastAgentService(
                config=self._config,
                state_file=state_file,
                project_root=self._project_root,
            )

        self._services[server_id] = svc
        return svc

    # ------------------------------------------------------------------
    # Health check — verify registry against VastAI API
    # ------------------------------------------------------------------

    def verify_servers(self) -> list[str]:
        """Verify all registry entries against VastAI.

        Removes servers whose VastAI instances no longer exist.
        Returns list of removed server IDs.
        """
        removed = []
        servers = self._registry.list_servers()
        for sid, entry in list(servers.items()):
            # Never remove servers created within the last 15 minutes — they may be booting
            if entry.created_at:
                try:
                    created = datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
                    age_minutes = (datetime.now(UTC) - created).total_seconds() / 60
                    if age_minutes < 15:
                        logger.debug("Server %s is only %.1f min old, skipping verify", sid, age_minutes)
                        continue
                except (ValueError, TypeError):
                    pass

            if not entry.instance_id:
                # No instance ID and older than 15 min — leftover placeholder, remove
                self._registry.remove_server(sid)
                self._services.pop(sid, None)
                state_file = self._project_root / f".vast-server-{sid}.json"
                state_file.unlink(missing_ok=True)
                removed.append(sid)
                continue

            # Check if the instance is actually alive
            # Skip servers that have active startup jobs (still booting)
            has_active_job = False
            if self._job_checker:
                has_active_job = self._job_checker(sid) > 0
            if not has_active_job:
                # Also check the in-memory job tags for server_up jobs
                try:
                    from api.deps import get_job_manager
                    jm = get_job_manager()
                    server_up_jobs = jm.find_jobs(type="server_up")
                    has_active_job = any(j.status in ("pending", "running") for j in server_up_jobs)
                except Exception:
                    pass

            svc = self.get_or_create_service(sid)
            try:
                status = svc.status()
                # Don't remove if:
                # - instance is running (SSH reachable)
                # - instance has an actual_status (exists on VastAI, might be booting)
                # - there's an active startup job
                if not status.running and not status.actual_status and not has_active_job:
                    logger.info(
                        "Server %s (instance %s) is gone, removing from registry",
                        sid, entry.instance_id,
                    )
                    self._registry.remove_server(sid)
                    self._services.pop(sid, None)
                    state_file = self._project_root / f".vast-server-{sid}.json"
                    state_file.unlink(missing_ok=True)
                    removed.append(sid)
                elif not status.running and status.actual_status:
                    logger.info(
                        "Server %s (instance %s) exists but not ready (status=%s), keeping",
                        sid, entry.instance_id, status.actual_status,
                    )
            except Exception as exc:
                if has_active_job:
                    logger.info("Server %s query failed but has active job, keeping: %s", sid, exc)
                else:
                    logger.warning("Failed to check server %s: %s — removing", sid, exc)
                    self._registry.remove_server(sid)
                    self._services.pop(sid, None)
                    state_file = self._project_root / f".vast-server-{sid}.json"
                    state_file.unlink(missing_ok=True)
                    removed.append(sid)

        if removed:
            logger.info("Removed %d stale servers: %s", len(removed), removed)
        return removed

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def _generate_server_id(self) -> str:
        return f"srv_{uuid.uuid4().hex[:8]}"

    def _get_busy_server_ids(self) -> set[str]:
        """Return server IDs that have active generation jobs."""
        if self._job_checker is None:
            return set()
        servers = self._registry.list_servers()
        busy = set()
        for sid in servers:
            if self._job_checker(sid) > 0:
                busy.add(sid)
        return busy

    def allocate_server(
        self, influencer_id: str, workflow: str = "wan_animate"
    ) -> tuple[str, VastAgentService]:
        """Smart allocation for an influencer.

        1. Check if influencer has own running server -> use it.
        2. Check if any other server is free -> borrow it.
        3. Create new server -> rent VastAI instance.

        Returns (server_id, service).
        """
        # 1. Check influencer's own server
        own = self._registry.find_by_influencer(influencer_id)
        if own is not None:
            sid, entry = own
            svc = self.get_or_create_service(sid)
            return sid, svc

        # 2. Check for a free server to borrow
        busy_ids = self._get_busy_server_ids()
        free = self._registry.find_free_server(
            exclude_influencer_id=influencer_id,
            busy_server_ids=busy_ids,
        )
        if free is not None:
            sid, entry = free
            # Re-assign to this influencer
            self._registry.update_entry(sid, influencer_id=influencer_id)
            svc = self.get_or_create_service(sid)
            return sid, svc

        # 3. Create new server
        sid = self._generate_server_id()
        entry = ServerEntry(
            influencer_id=influencer_id,
            workflow=workflow,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._registry.add_server(sid, entry)
        svc = self.get_or_create_service(sid)
        return sid, svc

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def list_servers(self) -> list[dict]:
        """List all servers with status info."""
        result = []
        servers = self._registry.list_servers()
        for sid, entry in servers.items():
            active_jobs = 0
            if self._job_checker:
                active_jobs = self._job_checker(sid)
            result.append({
                "server_id": sid,
                "instance_id": entry.instance_id,
                "influencer_id": entry.influencer_id,
                "ssh_host": entry.ssh_host,
                "ssh_port": entry.ssh_port,
                "dph_total": entry.dph_total,
                "workflow": entry.workflow,
                "created_at": entry.created_at,
                "auto_shutdown": entry.auto_shutdown,
                "active_jobs": active_jobs,
            })
        return result

    def shutdown_server(self, server_id: str) -> None:
        """Shut down and remove a server."""
        svc = self.get_or_create_service(server_id)
        try:
            if svc.has_instance():
                svc.down()
        except Exception as exc:
            logger.warning("Error during shutdown of %s: %s", server_id, exc)

        self._registry.remove_server(server_id)
        self._services.pop(server_id, None)

        # Clean up state file
        state_file = self._project_root / f".vast-server-{server_id}.json"
        state_file.unlink(missing_ok=True)

    def set_auto_shutdown(self, server_id: str, enabled: bool) -> None:
        """Toggle auto-shutdown flag."""
        self._registry.update_entry(server_id, auto_shutdown=enabled)

    def on_generation_complete(self, server_id: str) -> None:
        """Called after generation finishes.

        Auto-shuts down if the flag is set and no more active jobs.
        Note: called from within the finishing job, so that job still shows
        as "running" in the JobManager. We subtract 1 to account for it.
        """
        entry = self._registry.get_server(server_id)
        if entry is None:
            return

        if not entry.auto_shutdown:
            return

        active = 0
        if self._job_checker:
            active = max(0, self._job_checker(server_id) - 1)  # -1 for the calling job

        if active <= 0:
            logger.info(
                "Auto-shutdown triggered for server %s (influencer=%s)",
                server_id,
                entry.influencer_id,
            )
            self.shutdown_server(server_id)

    def server_status(self, server_id: str) -> dict:
        """Get status of a specific server."""
        entry = self._registry.get_server(server_id)
        if entry is None:
            return {"server_id": server_id, "status": "not_found"}

        svc = self.get_or_create_service(server_id)

        # For mock service, just check if it reports running
        if self._is_mock():
            st = svc.status()
            return {
                "server_id": server_id,
                "status": "running" if st.running else "offline",
                "instance_id": st.instance_id,
                "ssh_host": st.ssh_host,
                "ssh_port": st.ssh_port,
                "dph_total": st.dph_total,
                "influencer_id": entry.influencer_id,
                "auto_shutdown": entry.auto_shutdown,
                "active_jobs": self._job_checker(server_id) if self._job_checker else 0,
            }

        # Real service — check VastAI
        try:
            st = svc.status()
        except Exception:
            st = None

        if st and st.running:
            status = "running"
        elif st and st.actual_status:
            status = st.actual_status
        else:
            status = "offline"

        return {
            "server_id": server_id,
            "status": status,
            "instance_id": entry.instance_id,
            "ssh_host": entry.ssh_host,
            "ssh_port": entry.ssh_port,
            "dph_total": entry.dph_total,
            "ssh_reachable": st.ssh_reachable if st else False,
            "influencer_id": entry.influencer_id,
            "auto_shutdown": entry.auto_shutdown,
            "active_jobs": self._job_checker(server_id) if self._job_checker else 0,
        }

    def get_influencer_server_info(self, influencer_id: str) -> dict:
        """Get server allocation info for an influencer."""
        own = self._registry.find_by_influencer(influencer_id)
        has_own = own is not None

        server_id = own[0] if own else None
        server_busy = False
        active_jobs = 0

        if has_own and self._job_checker:
            active_jobs = self._job_checker(server_id)  # type: ignore[arg-type]
            server_busy = active_jobs > 0

        # Check if can borrow
        busy_ids = self._get_busy_server_ids()
        free = self._registry.find_free_server(
            exclude_influencer_id=influencer_id,
            busy_server_ids=busy_ids,
        )
        can_borrow = free is not None
        borrow_server_id = free[0] if free else None

        return {
            "has_own_server": has_own,
            "server_id": server_id,
            "server_busy": server_busy,
            "active_jobs": active_jobs,
            "can_borrow": can_borrow,
            "borrow_server_id": borrow_server_id,
        }

    def update_registry_from_service(self, server_id: str) -> None:
        """Sync registry entry with VastAgentService state after server up."""
        svc = self.get_or_create_service(server_id)
        try:
            st = svc.status()
        except Exception:
            return

        if st.instance_id:
            self._registry.update_entry(
                server_id,
                instance_id=st.instance_id,
                ssh_host=st.ssh_host,
                ssh_port=st.ssh_port,
                dph_total=st.dph_total,
            )

    # ------------------------------------------------------------------
    # Background health check
    # ------------------------------------------------------------------

    def start_health_check(self) -> None:
        """Start the background health-check loop as an asyncio task.

        Interval is configured via ``VastConfig.health_check_interval`` (seconds).
        Set to 0 to disable.
        """
        interval = self._config.health_check_interval
        if interval <= 0:
            logger.info("Server health check disabled (interval=0)")
            return
        if self._health_task is not None:
            return  # already running

        async def _loop() -> None:
            logger.info(
                "Server health-check started (every %ds)", interval
            )
            while True:
                await asyncio.sleep(interval)
                try:
                    removed = await asyncio.to_thread(self.verify_servers)
                    if removed:
                        logger.info(
                            "Health-check removed %d stale servers: %s",
                            len(removed), removed,
                        )
                except Exception:
                    logger.warning("Health-check error", exc_info=True)

        self._health_task = asyncio.create_task(_loop())

    def stop_health_check(self) -> None:
        """Cancel the background health-check task."""
        if self._health_task is not None:
            self._health_task.cancel()
            self._health_task = None
