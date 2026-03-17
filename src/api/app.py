"""FastAPI application factory and entry point."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send

from api.database import Database
from api.deps import init_deps
from api.events import EventBus
from api.routes import events, generation, health, influencers, jobs, parser
from trend_parser.config import ParserConfig
from trend_parser.store import FilesystemStore

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for SPA client-side routing."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except Exception:
            # Don't serve SPA fallback for API or file-serving routes —
            # let them return proper 404/error responses instead of HTML.
            path = scope.get("path", "")
            if path.startswith("/api/") or path.startswith("/files/"):
                raise
            index = Path(self.directory) / "index.html"  # type: ignore[arg-type]
            if index.is_file():
                response = FileResponse(index, media_type="text/html")
                await response(scope, receive, send)
            else:
                raise

API_PREFIX = "/api/v1"


def create_app(
    config: ParserConfig | None = None,
    data_dir: Path | None = None,
    seed_dir: Path | None = None,
) -> FastAPI:
    project_root = Path(__file__).resolve().parents[2]
    if config is None:
        config_path = project_root / "configs" / "parser.yaml"
        if config_path.exists():
            config = ParserConfig.from_yaml(config_path)
        else:
            config = ParserConfig()

    if data_dir is None:
        data_dir = config.resolve_workspace_dir(project_root / "shared")
    if seed_dir is None:
        seed_dir = config.resolve_seed_dir(project_root / "shared" / "seeds")

    store = FilesystemStore(data_dir=data_dir)

    # Initialize database
    db_path = data_dir / "studio.db"
    db = Database(db_path)
    event_bus = EventBus()

    init_deps(config=config, store=store, seed_dir=seed_dir, db=db, event_bus=event_bus)

    app = FastAPI(title="AI Influencer Studio", version="0.2.0")

    @app.on_event("startup")
    async def _startup() -> None:
        """Initialize database, run migration, recover orphaned jobs, start health check."""
        # 1. Connect DB and apply schema
        await db.connect()
        await db.apply_schema()

        # 2. Run filesystem migration if DB is empty
        try:
            from api.migrate import migrate_filesystem_to_db
            await migrate_filesystem_to_db(db, data_dir, project_root)
        except Exception:
            logger.warning("Filesystem migration failed", exc_info=True)

        # 3. Recover orphaned jobs from previous run
        try:
            from api.deps import get_job_manager
            jm = get_job_manager()
            await jm.startup()
        except Exception:
            logger.warning("Job recovery on startup failed", exc_info=True)

        # 4. Discover running VastAI instances (recovers from restart)
        try:
            from api.deps import get_server_manager
            manager = get_server_manager()
            import asyncio
            discovered = await asyncio.to_thread(manager.discover_instances)
            if discovered:
                logger.info("Startup: discovered %d VastAI instances", len(discovered))
        except Exception:
            logger.warning("VastAI instance discovery failed", exc_info=True)

        # 5. Start VastAI server health check
        try:
            manager = get_server_manager()
            manager.start_health_check()
        except Exception:
            pass  # non-critical

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        """Clean shutdown: flush progress, close DB."""
        try:
            from api.deps import get_job_manager
            jm = get_job_manager()
            await jm.shutdown()
        except Exception:
            pass
        await db.close()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /health at root (no prefix)
    app.include_router(health.router)
    # All business routes under /api/v1
    app.include_router(parser.router, prefix=API_PREFIX)
    app.include_router(influencers.router, prefix=API_PREFIX)
    app.include_router(generation.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(events.router, prefix=API_PREFIX)

    # Serve files from shared/ directory (images, videos, pipeline outputs)
    data_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=data_dir), name="shared-files")

    # Serve the built frontend SPA in production
    frontend_dist = project_root / "frontend-dist"
    if (frontend_dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=frontend_dist, html=True), name="spa")

    return app


def main() -> None:
    """CLI entry point for ``comfy-api``."""
    parser = argparse.ArgumentParser(description="AI Influencer Studio API server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port)
