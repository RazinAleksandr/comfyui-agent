# Database Architecture

SQLite with WAL mode. Single file at `shared/studio.db`. Zero-config — created automatically on first startup.

## Schema

```
┌─────────────────────┐     ┌──────────────────────┐
│    influencers       │     │    servers            │
│─────────────────────│     │──────────────────────│
│ influencer_id  PK   │     │ server_id       PK   │
│ name                │     │ instance_id          │
│ description         │     │ ssh_host / ssh_port  │
│ hashtags (JSON)     │     │ dph_total            │
│ reference_image_path│     │ influencer_id        │
│ created_at          │     │ workflow             │
│ updated_at          │     │ auto_shutdown        │
└────────┬────────────┘     │ created_at           │
         │                  └──────────────────────┘
         │ 1:N
         ▼
┌─────────────────────┐
│   pipeline_runs      │
│─────────────────────│
│ run_id          PK  │
│ influencer_id   FK  │
│ started_at          │
│ base_dir            │
│ request_json        │
│ status              │
└────┬───────────┬────┘
     │ 1:N       │ 1:1
     ▼           ▼
┌──────────────┐ ┌──────────────────┐
│pipeline_stages│ │    reviews        │
│──────────────│ │──────────────────│
│ run_id    FK │ │ run_id    FK  UQ │
│ platform     │ │ completed        │
│ source       │ └────────┬─────────┘
│ ingested_items│          │ 1:N
│ download_counts│         ▼
│ filtered_dir  │ ┌──────────────────┐
│ selected_dir  │ │  review_videos    │
│ accepted      │ │──────────────────│
│ rejected      │ │ review_id   FK   │
└──────────────┘ │ file_name        │
                 │ approved         │
                 │ prompt           │
                 └──────────────────┘

┌─────────────────────────┐
│         jobs             │
│─────────────────────────│
│ job_id             PK   │
│ job_type                │  generation | pipeline | parse | server_up
│ status                  │  pending | running | completed | failed
│ created_at / started_at │
│ completed_at            │
│ result_json             │
│ error                   │
│ progress_json           │  real-time progress (buffered, flushed 1/s)
│ influencer_id           │
│ server_id               │
│ reference_video         │
│ run_id                  │
└────────┬────────────────┘
         │ 1:1
         ▼
┌─────────────────────────┐
│    generation_jobs       │
│─────────────────────────│
│ job_id            FK    │  → jobs.job_id (CASCADE)
│ run_id                  │
│ file_name               │  reference video filename
│ server_id               │
│ influencer_id           │
│ started_at              │
│ status                  │
│ outputs_json            │  JSON array of output file paths
│ output_dir              │
│ UNIQUE(run_id, file_name, job_id)
└─────────────────────────┘
```

## Tables

### `influencers`
One row per AI influencer character. Canonical source for profile data (replaced `profile.json`).

### `pipeline_runs`
One row per trend-parsing pipeline execution. Links to influencer. The `base_dir` field points to the filesystem directory where video files live.

### `pipeline_stages`
Per-platform results within a pipeline run (e.g., tiktok stage, instagram stage). Stores counts and paths to report files on disk.

### `reviews` + `review_videos`
Human review decisions. One review per run, with N video approval/prompt records. Atomic writes via DB — no more overwrite race conditions.

### `jobs`
All async jobs (generation, pipeline, parse, server_up). Persists status, progress, result, error. **Survives server restart** — orphaned jobs are marked failed on startup.

Progress is buffered in memory and flushed to DB once per second. Real-time updates go to clients via SSE immediately.

### `generation_jobs`
Per-video generation tracking within a pipeline run. Links to the async `jobs` entry via `job_id`. Stores output file paths after completion. Replaces `generation_manifest.json`.

### `servers`
VastAI GPU server registry. Tracks instance allocation, SSH details, auto-shutdown flag. Replaces `.vast-registry.json`.

## What stays on filesystem

| Path | Content | Why |
|------|---------|-----|
| `shared/influencers/{id}/` | Directory per influencer | Reference images, pipeline run directories |
| `shared/influencers/{id}/pipeline_runs/{ts}/` | Run directory | Downloaded videos, filtered videos, generated outputs |
| `shared/influencers/{id}/pipeline_runs/{ts}/run_manifest.json` | Pipeline stage metadata | Written incrementally by pipeline runner (sync thread) |
| `shared/influencers/{id}/pipeline_runs/{ts}/{platform}/` | Platform artifacts | `platform_manifest.json`, `downloads/`, `filtered/`, `selected/`, `generated/` |
| `shared/influencers/{id}/pipeline_runs/{ts}/{platform}/vlm/` | VLM scoring results | Per-video JSON decisions, summary |
| `.vast-server-{id}.json` | Live SSH state per GPU server | Runtime cache — recreated from DB if missing |
| `.vast-instance.json` | Legacy single-server state | Cleaned up by `discover_instances()` once the instance is registered in DB |

## Data flow

```
Frontend (React)
    │
    ├── REST API ──→ DB reads (influencers, jobs, reviews, generation_jobs)
    │
    └── SSE stream ←── EventBus ←── PersistentJobManager (progress, state changes)
                            │
FastAPI                     │
    │                       │
    ├── PersistentJobManager ──→ jobs table (status, progress, result)
    │                          ──→ EventBus (real-time SSE push)
    │
    ├── DBStore ──→ influencers, reviews tables
    │
    ├── DBServerRegistry ──→ servers table
    │
    ├── Generation route ──→ generation_jobs table
    │
    └── Pipeline runner ──→ run_manifest.json (filesystem)
                          ──→ video files (filesystem)
```

## Migration

On first startup, `migrate.py` reads all existing filesystem JSON and populates the DB:
- `profile.json` → `influencers` table
- `run_manifest.json` → `pipeline_runs` + `pipeline_stages` tables
- `review_manifest.json` → `reviews` + `review_videos` tables
- `generation_manifest.json` → `generation_jobs` + `jobs` tables
- `.vast-registry.json` → `servers` table

Migration is idempotent — skips if DB already has data. Old files are not deleted.

## Operations

```bash
# Backup
cp shared/studio.db shared/studio.db.bak

# Check DB size
ls -lh shared/studio.db

# Query directly
sqlite3 shared/studio.db "SELECT * FROM influencers;"
sqlite3 shared/studio.db "SELECT job_id, job_type, status FROM jobs ORDER BY created_at DESC LIMIT 10;"
sqlite3 shared/studio.db "SELECT server_id, influencer_id, auto_shutdown FROM servers;"

# Health check (includes DB info)
curl http://localhost:8000/health
```
