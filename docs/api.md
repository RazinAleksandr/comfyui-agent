# API Server

FastAPI application exposing all business logic as a REST API. The Telegram bot and future frontends consume this API.

```
src/api/
  app.py              FastAPI factory, CORS, router registration, CLI entry
  deps.py             Singleton dependency injection (config, store, job manager)
  jobs.py             JobManager — in-memory async job tracking
  server.py           Click CLI entry for comfy-api (unused, argparse in app.py)
  main.py             Uvicorn app entrypoint for programmatic use

  routes/
    health.py          GET /health
    parser.py          /api/v1/parser/* — trend parsing pipeline
    influencers.py     /api/v1/influencers/* — influencer CRUD
    generation.py      /api/v1/generation/* — GPU server + workflow execution
    jobs.py            /api/v1/jobs/* — job status polling
```

## Starting the server

```bash
comfy-api --host 0.0.0.0 --port 8000
comfy-api --port 8000 --reload    # auto-reload for development
```

## Routes

### Health

```
GET /health → {"status": "ok"}
```

### Parser (`/api/v1/parser`)

| Method | Path | Async | Description |
|--------|------|-------|-------------|
| `POST` | `/run` | job | Ingest trending videos from configured source |
| `POST` | `/pipeline` | job | Full pipeline: ingest → download → filter → VLM |
| `POST` | `/signals` | sync | Lightweight signal extraction (no download) |
| `GET` | `/runs?influencer_id=...` | sync | List pipeline runs (enriched with video lists, reports) |
| `GET` | `/runs/{run_id}?influencer_id=...` | sync | Get specific run details (enriched) |
| `POST` | `/runs/{run_id}/review?influencer_id=...` | sync | Submit human review decisions for a run |

`POST /run` request:

```json
{
  "platforms": ["tiktok", "instagram"],
  "limit": 10,
  "source": "seed",
  "selectors": {
    "tiktok": {"hashtags": ["dance"], "min_views": 5000}
  }
}
```

`POST /pipeline` request: see [trend_parser.md](trend_parser.md) for full schema.

`POST /runs/{run_id}/review` request:

```json
{
  "videos": [
    {"file_name": "tiktok_xxx.mp4", "approved": true, "prompt": "sks girl dancing"},
    {"file_name": "tiktok_yyy.mp4", "approved": false, "prompt": ""}
  ]
}
```

Saves `review_manifest.json` in the run directory. The enriched run response includes this as `run.review`.

### Influencers (`/api/v1/influencers`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all influencers |
| `GET` | `/{influencer_id}` | Get influencer profile |
| `PUT` | `/{influencer_id}` | Create or update influencer |
| `POST` | `/{influencer_id}/reference-image` | Upload reference image (multipart) |

`PUT` request:

```json
{
  "name": "Emi2Souls",
  "description": "Fitness and dance creator",
  "hashtags": ["fitness", "dance", "gym"],
  "video_suggestions_requirement": "Reject videos with multiple people"
}
```

### Generation (`/api/v1/generation`)

| Method | Path | Async | Description |
|--------|------|-------|-------------|
| `GET` | `/servers` | sync | List all GPU servers with status, influencer, active jobs |
| `GET` | `/server/status?influencer_id=...` | sync | Server status for an influencer |
| `GET` | `/server/allocate?influencer_id=...` | sync | Check server allocation info for an influencer |
| `POST` | `/server/up` | job | Start/allocate GPU server for an influencer |
| `POST` | `/server/down` | sync | Destroy the legacy/default GPU server |
| `POST` | `/server/{server_id}/down` | sync | Destroy a specific GPU server |
| `POST` | `/server/{server_id}/auto-shutdown` | sync | Toggle auto-shutdown for a server |
| `POST` | `/run` | job | Run video generation on GPU (auto-allocates server) |

`POST /server/up` request:

```json
{"workflow": "wan_animate", "influencer_id": "emi2souls"}
```

`POST /run` request:

```json
{
  "influencer_id": "emi2souls",
  "workflow": "wan_animate",
  "reference_image": "/path/to/image.jpg",
  "reference_video": "/path/to/video.mp4",
  "prompt": "A woman dancing in a nightclub",
  "set_args": {"lora_high": "custom.safetensors"}
}
```

`GET /server/allocate` response:

```json
{
  "has_own_server": false,
  "server_id": null,
  "server_busy": false,
  "active_jobs": 0,
  "can_borrow": true,
  "borrow_server_id": "srv_32921955"
}
```

`POST /server/{server_id}/auto-shutdown` request:

```json
{"enabled": true}
```

When auto-shutdown is enabled, the server is destroyed after all generation jobs complete.

Generation output is automatically postprocessed (grain, sharpness, brightness, vignette) and the postprocessed file is included in the result. Generation jobs persist to `generation_manifest.json` in the run directory, surviving page refreshes.

### Jobs (`/api/v1/jobs`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/active?type=...&influencer_id=...` | Find active (pending/running) jobs by tag filters |
| `GET` | `/{job_id}` | Get job status, result, or error |
| `GET` | `/?limit=50` | List recent jobs (newest first) |

Job response:

```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "completed",
  "created_at": "2026-03-13T14:30:00+00:00",
  "started_at": "2026-03-13T14:30:00+00:00",
  "completed_at": "2026-03-13T14:32:15+00:00",
  "result": { "..." : "..." },
  "error": null,
  "progress": {}
}
```

**Status lifecycle:** `pending` → `running` → `completed` | `failed`

Jobs include `tags` for categorization (e.g. `{"type": "pipeline", "influencer_id": "emi2soul"}`). The `/active` endpoint filters by these tags.

Job types: `parse`, `pipeline`, `server_up`, `generation`.

## Async Job System

Long-running operations (parsing ~2-10 min, generation ~3-15 min, server startup ~5-10 min) are tracked by the in-memory `JobManager`:

1. API endpoint calls `job_manager.submit_tagged(async_fn, tags, *args)` → returns `job_id`
2. Response is `{"job_id": "..."}` (immediate, non-blocking)
3. Client polls `GET /api/v1/jobs/{job_id}` until status is `completed` or `failed`
4. Result or error is available in the job response
5. Generation jobs also report real-time `progress` (current node, sampling step)

Jobs are stored in memory — they don't survive server restarts. Generation job IDs are also persisted in `generation_manifest.json` per pipeline run, so the frontend can restore tracking after page refresh.

## Dependencies

The API uses singleton dependency injection via `deps.py`:

| Dependency | Type | Purpose |
|-----------|------|---------|
| `config` | `ParserConfig` | Loaded from `configs/parser.yaml` at startup |
| `store` | `FilesystemStore` | CRUD for influencers, pipeline runs in `shared/` |
| `job_manager` | `JobManager` | Async job tracking |
| `seed_dir` | `Path` | Location of seed JSON files |
| `server_manager` | `ServerManager` | Multi-server GPU orchestration (lazy-loaded) |

## Static File Serving

The API serves files from the `shared/` directory at `/files/*`:

- `/files/influencers/emi2soul/reference.png` — influencer reference images
- `/files/influencers/emi2soul/pipeline_runs/{run_id}/tiktok/downloads/...` — downloaded videos
- `/files/influencers/emi2soul/pipeline_runs/{run_id}/tiktok/generated/...` — generated outputs

In production, the built frontend SPA is served from `frontend-dist/` at `/` with SPA catch-all routing.

## Frontend

The React frontend is served by FastAPI in production:

```bash
cd frontend && npm run build   # outputs to frontend-dist/
comfy-api --port 8000          # serves API + SPA + files on one port
```

For development with hot-reload:

```bash
comfy-api --port 8000          # backend
cd frontend && npm run dev     # Vite dev server with proxy to backend
```

The Vite config proxies `/api` and `/files` to `localhost:8000`.

## Configuration

The API loads `configs/parser.yaml` at startup for parser settings. VastAI config is loaded lazily from `configs/vast.yaml` when the first generation request arrives.

Environment variables are resolved via `${ENV_VAR}` syntax in YAML files. Secrets (API keys, tokens) should be in `.env`.
