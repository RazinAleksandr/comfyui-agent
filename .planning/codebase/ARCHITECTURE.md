# ARCHITECTURE

System design, layers, data flow, and key abstractions.

---

## Pattern

**Layered monolith with async job system.** Single Python process hosts the API, job orchestration, and background tasks. Frontend is a React SPA built separately and served by FastAPI in production. GPU work is delegated to remote VastAI instances over SSH.

---

## Layers

```
┌─────────────────────────────────────────────────┐
│  React SPA (frontend/)                           │
│  Vite + React 18 + Tailwind 4 + shadcn/ui        │
│  Dev: port 5173 with /api proxy → 8000           │
│  Prod: served from frontend-dist/ by FastAPI     │
└──────────────────┬──────────────────────────────┘
                   │ HTTP/SSE
┌──────────────────▼──────────────────────────────┐
│  FastAPI API (src/api/)                          │
│  Entry: src/api/main.py → create_app()           │
│  Routes: src/api/routes/*.py                     │
│  Async jobs: PersistentJobManager (SQLite-backed)│
│  SSE events: src/api/events.py                   │
└────┬──────────┬──────────┬──────────┬───────────┘
     │          │          │          │
┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼──────────┐
│Trend   │ │VastAI  │ │Comfy   │ │ISP / X2V     │
│Parser  │ │Agent   │ │Pipeline│ │Pipeline      │
│        │ │        │ │        │ │              │
│ingest  │ │manager │ │client  │ │postprocess   │
│download│ │vastai  │ │runner  │ │              │
│filter  │ │remote  │ │workflow│ │              │
│vlm     │ │service │ │        │ │              │
└────┬───┘ └───┬────┘ └───┬────┘ └──────────────┘
     │         │          │
┌────▼─────────▼──────────▼──────────────────────┐
│  Data Layer                                      │
│  SQLite: shared/studio.db (WAL mode)             │
│  Filesystem: shared/influencers/{id}/...         │
│  src/api/database.py + db_store.py               │
└─────────────────────────────────────────────────┘
                         │ SSH
┌────────────────────────▼────────────────────────┐
│  Remote GPU (VastAI)                             │
│  ComfyUI on port 8188                            │
│  Runs ComfyUI workflows for video generation     │
└─────────────────────────────────────────────────┘
```

---

## Entry Points

| Entry Point | Purpose |
|---|---|
| `src/api/main.py` | ASGI app factory, `uvicorn api.main:app` |
| `src/api/app.py:create_app()` | FastAPI factory with middleware + route registration |
| `src/telegram_bot/__main__.py` | Standalone Telegram bot |
| `src/vast_agent/__main__.py` | CLI for VastAI instance management |
| `src/comfy_pipeline/__main__.py` | CLI for ComfyUI pipeline |
| `src/isp_pipeline/__main__.py` | CLI for ISP postprocessing |

---

## Async Job System

Long-running operations use `PersistentJobManager` (`src/api/job_manager.py`):

1. Route handler creates a job: `job_manager.create_job(type, influencer_id, ...)`
2. Returns `{job_id}` immediately (non-blocking)
3. Background thread runs the actual work with injected `progress_fn`
4. Progress buffered in memory, flushed to SQLite once/second
5. Frontend polls via SSE (`/api/v1/events/stream`) for real-time updates
6. On server restart, orphaned jobs (state=running) are marked failed

**Job types**: `pipeline`, `download`, `filter`, `vlm`, `generation`, `ref_align`

**Per-server locking**: `threading.Lock` in `ServerManager` ensures only one generation per GPU. Queued jobs show "Waiting in queue...".

---

## Pipeline Flow (Trend → Generation)

```
Ingest          Download         Filter          VLM Scoring
(adapters/)  →  (downloader.py) → (filter.py)  →  (vlm.py)
TikTok/Apify    yt-dlp           ffprobe           Gemini API
                                 quality score     persona fit

     ↓
Review (Web UI)
Human approves/skips videos + prompt per video
Draft auto-save every 1.5s
VLM rerun → auto-review (Gemini captions)

     ↓
Generation
ref_align.py (optional) — Gemini generates aligned reference image
vast_agent/service.py — sets up remote GPU, runs ComfyUI
isp_pipeline/processor.py — postprocessing on output video
```

---

## Key Abstractions

### ServerManager (`src/vast_agent/manager.py`)
Central orchestrator for GPU lifecycle:
- Allocation: own server → borrow free server → create new
- Registry: `DBServerRegistry` in `src/vast_agent/db_registry.py` (SQLite `servers` table)
- Background health check (default: every 120s)
- Auto-shutdown after generation if flag set
- Never removes servers < 15 min old or with active jobs

### PipelineRunner (`src/trend_parser/runner.py`)
Executes the ingest→download→filter→VLM pipeline stages sequentially. Each stage writes results to `run_manifest.json` and platform manifests.

### FilesystemStore (`src/trend_parser/store.py`)
Handles all file operations for pipeline data under `shared/influencers/{id}/pipeline_runs/{timestamp}/`.

### ComfyUI Client (`src/comfy_pipeline/client.py`)
HTTP client for the ComfyUI API running on remote GPU (port 8188). Submits workflows, polls for completion, downloads outputs.

### SSE Event Bus (`src/api/events.py`)
In-memory pub/sub for real-time frontend updates. Each connected client gets its own async queue. Events: job progress, status changes, server state.

---

## API Routes

| Route prefix | File | Purpose |
|---|---|---|
| `/api/v1/parser/*` | `routes/parser.py` | Trend parsing pipeline (ingest, download, VLM) |
| `/api/v1/influencers/*` | `routes/influencers.py` | Influencer CRUD, generated content |
| `/api/v1/generation/*` | `routes/generation.py` | GPU server management + video generation |
| `/api/v1/jobs/*` | `routes/jobs.py` | Async job tracking |
| `/api/v1/events/stream` | `routes/events.py` | SSE real-time updates |
| `/auth/*` | `routes/auth.py` | Basic auth |
| `/health` | `routes/health.py` | Health check |
| `/files/*` | (app.py) | Static file serving for `shared/` |
| `/*` | (app.py) | SPA catch-all → `frontend-dist/index.html` |

---

## Data Flow: Generation Request

```
POST /api/v1/generation/{influencer_id}/generate
  → ServerManager.get_or_create_server(influencer_id)
  → PersistentJobManager.create_job("generation", ...)
  → background thread: _do_generation()
      → ref_align.py (if enabled): Gemini reference alignment
      → vast_agent/service.py: SSH setup + ComfyUI execution
      → isp_pipeline/processor.py: postprocess output
      → job marked completed, result saved to DB + filesystem
  → SSE events pushed to frontend throughout
```

---

## Multi-Process Considerations

- SQLite WAL mode allows concurrent reads with one writer
- `threading.Lock` per GPU server (not process-wide)
- No Redis/Celery — all async work is Python threads
- Single uvicorn process (no workers) — simplest deployment
