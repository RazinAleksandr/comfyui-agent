"""Shared dependencies for the FastAPI application."""
from __future__ import annotations

from pathlib import Path

from trend_parser.config import ParserConfig
from trend_parser.store import FilesystemStore

from api.database import Database
from api.events import EventBus
from api.job_manager import PersistentJobManager

# Singleton instances — initialized once at startup via init_deps()
_config: ParserConfig | None = None
_store: FilesystemStore | None = None
_job_manager: PersistentJobManager | None = None
_seed_dir: Path | None = None
_db: Database | None = None
_event_bus: EventBus | None = None
_vast_service = None  # VastAgentService (lazy, avoids import on GPU-less envs)
_server_manager = None  # ServerManager (lazy)


def init_deps(
    config: ParserConfig,
    store: FilesystemStore,
    seed_dir: Path,
    db: Database,
    event_bus: EventBus,
) -> None:
    global _config, _store, _job_manager, _seed_dir, _db, _event_bus
    _config = config
    _store = store
    _seed_dir = seed_dir
    _db = db
    _event_bus = event_bus
    _job_manager = PersistentJobManager(db=db, event_bus=event_bus)


def get_config() -> ParserConfig:
    assert _config is not None, "deps not initialized"
    return _config


def get_store() -> FilesystemStore:
    assert _store is not None, "deps not initialized"
    return _store


def get_job_manager() -> PersistentJobManager:
    assert _job_manager is not None, "deps not initialized"
    return _job_manager


def get_db() -> Database:
    assert _db is not None, "deps not initialized"
    return _db


def get_event_bus() -> EventBus:
    assert _event_bus is not None, "deps not initialized"
    return _event_bus


def get_seed_dir() -> Path:
    assert _seed_dir is not None, "deps not initialized"
    return _seed_dir


def get_vast_service():
    """Lazy-initialized VastAgentService singleton.

    Set VAST_MOCK=1 env var to use the mock (no real GPU).
    """
    global _vast_service
    if _vast_service is None:
        import os
        if os.getenv("VAST_MOCK", "").strip() in ("1", "true", "yes"):
            from vast_agent.service_mock import VastAgentServiceMock
            _vast_service = VastAgentServiceMock()
        else:
            from vast_agent.service import VastAgentService
            _vast_service = VastAgentService()
    return _vast_service


def _job_checker(server_id: str) -> int:
    """Count active generation jobs for a given server_id."""
    jm = get_job_manager()
    active = [
        j for j in jm.find_jobs(type="generation")
        if j.status in ("pending", "running") and j.tags.get("server_id") == server_id
    ]
    return len(active)


def get_server_manager():
    """Lazy-initialized ServerManager singleton."""
    global _server_manager
    if _server_manager is None:
        from vast_agent.config import VastConfig
        from vast_agent.manager import ServerManager
        from vast_agent.db_registry import DBServerRegistry

        project_root = Path(__file__).resolve().parents[2]
        config_path = project_root / "configs" / "vast.yaml"
        config = VastConfig.from_yaml(config_path) if config_path.exists() else VastConfig()
        db = get_db()
        registry = DBServerRegistry(db)
        _server_manager = ServerManager(
            registry=registry,
            config=config,
            project_root=project_root,
            job_checker=_job_checker,
        )
    return _server_manager
