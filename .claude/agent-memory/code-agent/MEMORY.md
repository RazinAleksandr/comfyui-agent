# Code Agent Memory

## Project Structure
- `src/api/` - FastAPI backend (app.py, routes/, database.py, db_store.py, job_manager.py, ref_align.py, path_utils.py, auth.py)
- `src/trend_parser/` - Pipeline: ingest → download → filter → VLM scoring → caption
- `src/vast_agent/` - VastAI GPU orchestration, multi-server management (db_registry.py, manager.py, service.py)
- `src/comfy_pipeline/` - ComfyUI workflow execution (runs on remote GPU only)
- `src/x2v_pipeline/` - LightX2V pipeline (replacing ComfyUI): config.py, remote_runner.py, install.py, postprocess.py
- `src/isp_pipeline/` - Video post-processing
- `src/telegram_bot/` - Telegram UI (legacy, still functional)
- `frontend/` - React 18 + Vite + Tailwind 4 + shadcn/ui
- `configs/` - YAML config files (vast.yaml, wan_animate.yaml, x2v_animate.yaml, parser.yaml)

## Architecture
- SQLite database at `shared/studio.db` (WAL mode). Schema in `src/api/schema.sql`.
- Async jobs via `PersistentJobManager` (SQLite-backed). Jobs tagged with `{type, influencer_id, server_id}`.
- SSE real-time updates at `/api/v1/events/stream` via `EventBus`.
- Frontend served by FastAPI in production (`frontend-dist/`), Vite dev proxy in development.
- `FilesystemStore` handles file operations (video files, pipeline run directories).

## Code Patterns
- `from __future__ import annotations` at top of files
- Dataclasses for config with `@classmethod from_yaml()` factory
- `PYTHONPATH=src` when running — all imports relative to `src/`
- FastAPI routes in `src/api/routes/` (generation.py, parser.py, influencers.py, auth.py, events.py)
- Progress reporting via `progress_fn` callback injected by PersistentJobManager
- Pipeline rerun functions: `_rerun_download`, `_rerun_filter`, `_rerun_vlm` in parser.py
- Reference alignment via Gemini in `src/api/ref_align.py` (close-up mode supported)
- yt-dlp invoked via `python -m yt_dlp` (not direct binary) to avoid stale shebang issues

## Key APIs
- Rerun endpoints: `POST /runs/{run_id}/rerun-download`, `/rerun-filter`, `/rerun-vlm`
- Review: `POST /runs/{run_id}/review?influencer_id=` accepts `draft` flag for auto-save
- Generation: `POST /generation/run` with `align_reference` and `align_close_up` options
- Enrichment: `_enrich_run` falls back to scanning disk if manifest paths are missing
