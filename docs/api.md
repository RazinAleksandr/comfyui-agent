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
| `GET` | `/runs?influencer_id=...` | sync | List pipeline runs for influencer |
| `GET` | `/runs/{run_id}?influencer_id=...` | sync | Get specific run details |

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
| `GET` | `/server/status` | sync | GPU server status (running/offline, cost, SSH) |
| `POST` | `/server/up` | job | Start GPU server (rent + push + bootstrap + setup) |
| `POST` | `/server/down` | sync | Destroy GPU server |
| `POST` | `/run` | job | Run video generation on GPU |

`POST /server/up` request:

```json
{"workflow": "wan_animate"}
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

Generation output is automatically postprocessed (grain, sharpness, brightness, vignette) and the postprocessed file is included in the result.

### Jobs (`/api/v1/jobs`)

| Method | Path | Description |
|--------|------|-------------|
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

## Async Job System

Long-running operations (parsing ~2-10 min, generation ~3-15 min, server startup ~5-10 min) are tracked by the in-memory `JobManager`:

1. API endpoint calls `job_manager.submit(async_fn, *args)` → returns `job_id`
2. Response is `{"job_id": "..."}` (immediate, non-blocking)
3. Client polls `GET /api/v1/jobs/{job_id}` until status is `completed` or `failed`
4. Result or error is available in the job response

Jobs are stored in memory — they don't survive server restarts. This is acceptable for single-server deployment.

## Dependencies

The API uses singleton dependency injection via `deps.py`:

| Dependency | Type | Purpose |
|-----------|------|---------|
| `config` | `ParserConfig` | Loaded from `configs/parser.yaml` at startup |
| `store` | `FilesystemStore` | CRUD for influencers, pipeline runs in `shared/` |
| `job_manager` | `JobManager` | Async job tracking |
| `seed_dir` | `Path` | Location of seed JSON files |
| `vast_service` | `VastAgentService` | Lazy-loaded GPU orchestration (avoids import on GPU-less envs) |

## Configuration

The API loads `configs/parser.yaml` at startup for parser settings. VastAI config is loaded lazily from `configs/vast.yaml` when the first generation request arrives.

Environment variables are resolved via `${ENV_VAR}` syntax in YAML files. Secrets (API keys, tokens) should be in `.env`.
