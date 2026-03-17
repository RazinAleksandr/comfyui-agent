# AI Influencer Studio

Automated content generation platform for AI influencers. Full-stack: FastAPI backend + React frontend + VastAI GPU orchestration.

## Architecture

```
Frontend (React SPA, port 5173 dev / served by FastAPI in prod)
    │
FastAPI API (port 8000)
├── /api/v1/parser/*        trend parsing pipeline
├── /api/v1/influencers/*   influencer CRUD
├── /api/v1/generation/*    GPU server management + video generation
├── /api/v1/jobs/*          async job tracking
├── /api/v1/events/stream   SSE real-time updates (job progress, state changes)
├── /files/*                static file serving (shared/ directory)
└── /health
    │
    ├── api/             database.py, db_store.py, events.py, job_manager.py, migrate.py
    ├── trend_parser/    ingest → download → filter → VLM scoring
    ├── vast_agent/      VastAI GPU rental + multi-server management (db_registry.py)
    ├── comfy_pipeline/  ComfyUI workflow execution (runs on remote GPU)
    ├── isp_pipeline/    video postprocessing
    └── telegram_bot/    Telegram UI (legacy, still works)
```

## Key Rules

### Backend (Python)

- **Source dir**: `src/` — all Python code lives here. Use `PYTHONPATH=src` when running.
- **Entry point**: `src/api/app.py:create_app()` — FastAPI factory.
- **SQLite database** at `shared/studio.db` (WAL mode). Schema in `src/api/schema.sql`. Created automatically on first startup. `FilesystemStore` in `src/trend_parser/store.py` still handles file operations (video files, directories).
- **Async jobs** — long-running ops use `PersistentJobManager` in `src/api/job_manager.py` (SQLite-backed). Jobs are tagged with `{type, influencer_id, server_id}` for lookup. Jobs survive server restarts — orphaned jobs are marked failed on startup.
- **Progress reporting** — generation and pipeline jobs report progress via `progress_fn` callback injected by PersistentJobManager. Progress is buffered in memory and flushed to DB once per second. Frontend receives real-time updates via SSE at `/api/v1/events/stream`.
- **Per-server locking** — only one generation runs at a time per GPU server (`threading.Lock` in ServerManager). Others show "Waiting in queue...".
- **LoRA auto-apply** — character LoRAs from `configs/wan_animate.yaml` are auto-applied in `_do_generation` when `set_args` doesn't specify them.
- **Config files**: `configs/parser.yaml` (parser), `configs/vast.yaml` (GPU), `configs/wan_animate.yaml` (workflow + character LoRAs).
- **Env vars**: `.env` file, loaded with `set -a; source .env; set +a` before running. NOT auto-loaded by the API server.
- **Install**: `pip install -e ".[vps]"` in the venv. Always reinstall after code changes.

### Frontend (React/TypeScript)

- **Dir**: `frontend/` — Vite + React 18 + Tailwind 4 + shadcn/ui (Radix).
- **Build**: `cd frontend && ./node_modules/.bin/vite build` → outputs to `../frontend-dist/`.
- **Dev proxy**: Vite proxies `/api` and `/files` to `localhost:8000`.
- **Production**: FastAPI serves `frontend-dist/` with SPA catch-all (`SPAStaticFiles`).
- **API client**: `frontend/src/app/api/client.ts` — fetch-based, all endpoints.
- **Types**: `frontend/src/app/api/types.ts` — must match backend Pydantic models.
- **Mapper**: `frontend/src/app/api/mappers.ts` — converts `PipelineRun` → `Task` with 6 stages. Critical logic.
- **SSE client**: `frontend/src/app/api/sse.ts` — SSE connection singleton with auto-reconnect and exponential backoff.
- **Hooks**: `frontend/src/app/api/hooks.ts` — `useInfluencer`, `usePipelineRuns`, `useJobSSE`, `useConnectionStatus`, etc.
- **State persistence**: All UI state comes from the API. No localStorage. Page refresh = same view.
- **Video player**: Click any video thumbnail → modal with `<video controls autoPlay>`.
- **Cache busting**: Image URLs include `?v={timestamp}` to bust browser cache on update.

### Multi-Server Management (VastAI)

- **Registry**: `servers` table in SQLite via `DBServerRegistry` in `src/vast_agent/db_registry.py` — maps server IDs to VastAI instances + influencer assignments.
- **Manager**: `src/vast_agent/manager.py:ServerManager` — allocation, lifecycle, auto-shutdown.
- **Instance discovery**: On startup, `ServerManager.discover_instances()` queries the VastAI API for running instances and registers any unknown ones in the DB.
- **Allocation logic**: own server → borrow free server → create new.
- **Health check**: background task every `health_check_interval` seconds (default 120). Removes dead servers.
- **IMPORTANT**: Never remove servers created < 15 minutes ago (they may be booting).
- **IMPORTANT**: Never remove servers with active jobs or `actual_status` set (instance exists on VastAI).
- **Auto-shutdown**: per-server flag. Checked in `on_generation_complete()` — must subtract 1 from active job count (calling job still shows as "running" at that point).

### Pipeline Flow

1. **Ingest** — collect video metadata from TikTok/Instagram (sources: `tiktok_custom`, `apify`)
2. **Download** — yt-dlp downloads
3. **Filter** — ffprobe quality analysis, top-K selection
4. **VLM Scoring** — Gemini AI evaluates persona fit (8 criteria)
5. **Review** — human review in web UI (approve/skip + prompt per video). Saves to `reviews` + `review_videos` tables in DB.
6. **Generation** — ComfyUI on remote GPU via VastAI. Saves to `generation_jobs` table in DB. Auto-applies character LoRAs.

### Data in run directory

```
shared/influencers/{id}/pipeline_runs/{timestamp}/
├── run_manifest.json          # Pipeline stage results (written incrementally by sync pipeline runner)
├── {platform}/
│   ├── platform_manifest.json # Ingested items with metadata
│   ├── downloads/             # Raw downloaded videos
│   ├── analysis/              # Filter reports
│   ├── filtered/              # Quality-filtered videos
│   ├── vlm/                   # VLM scoring results
│   ├── selected/              # VLM-approved videos
│   └── generated/             # Generated output videos (raw/refined/upscaled/postprocessed)
```

Review decisions and generation job tracking are stored in the SQLite DB (`reviews`, `review_videos`, `generation_jobs` tables), not as manifest files.

### Common Operations

```bash
# Start service (must source .env for API keys)
cd /root/workspace/comfyui-agent
set -a; source .env; set +a
PYTHONPATH=src .venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# Rebuild frontend after changes
cd frontend && ./node_modules/.bin/vite build

# Reinstall backend after Python changes
.venv/bin/pip install -e ".[vps]"

# Check VastAI instances
set -a; source .env; set +a
PYTHONPATH=src .venv/bin/python -c "from vast_agent.vastai import VastClient; c=VastClient(); [print(i) for i in c.list_instances()]"
```

### Known Gotchas

- `.env` must be sourced manually — `python-dotenv` is NOT used by the API server.
- `default_source: seed` was removed. Use `default_sources` (per-platform) in `parser.yaml`.
- Frontend `runAll` sends generation requests sequentially but they queue on the backend via per-server locks.
- `onnxruntime-gpu` must be installed with `--extra-index-url` for CUDA provider. Added to `wan_animate.yaml` extra_pip and reinstalled after custom nodes.
- VastAI server state: `servers` table in SQLite (canonical), `.vast-server-{id}.json` (runtime cache, recreated from DB if missing). Legacy `.vast-instance.json` is cleaned up by `discover_instances()`.
- The enriched run API response includes live job status for generation jobs. Job status persists in the DB, so it survives server restarts. Live in-memory progress overlays the DB data for running jobs.

### Docs

- `docs/api.md` — all API endpoints, async job system, SSE events, static file serving
- `docs/database.md` — SQLite schema, tables, data flow, migration, operations
- `docs/trend_parser.md` — pipeline stages, sources, filter scoring, VLM
- `docs/pipeline.md` — ComfyUI workflow commands, configs, parameters
- `docs/vast_agent.md` — CLI, multi-server management, auto-shutdown
- `docs/telegram_bot.md` — conversation flow, commands
- `docs/frontend.md` — React SPA architecture, pages, components, SSE integration
