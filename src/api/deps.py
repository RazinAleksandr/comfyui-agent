"""Shared dependencies for the FastAPI application."""
from __future__ import annotations

from pathlib import Path

from trend_parser.config import ParserConfig
from trend_parser.store import FilesystemStore

from api.jobs import JobManager

# Singleton instances — initialized once at startup via init_deps()
_config: ParserConfig | None = None
_store: FilesystemStore | None = None
_job_manager: JobManager | None = None
_seed_dir: Path | None = None
_vast_service = None  # VastAgentService (lazy, avoids import on GPU-less envs)


def init_deps(
    config: ParserConfig,
    store: FilesystemStore,
    seed_dir: Path,
) -> None:
    global _config, _store, _job_manager, _seed_dir
    _config = config
    _store = store
    _seed_dir = seed_dir
    _job_manager = JobManager()


def get_config() -> ParserConfig:
    assert _config is not None, "deps not initialized"
    return _config


def get_store() -> FilesystemStore:
    assert _store is not None, "deps not initialized"
    return _store


def get_job_manager() -> JobManager:
    assert _job_manager is not None, "deps not initialized"
    return _job_manager


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
