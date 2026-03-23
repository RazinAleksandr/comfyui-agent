# Code Critic Memory

## Project Structure
- `src/api/` - FastAPI backend (app.py, routes/, database.py, db_store.py, job_manager.py, ref_align.py)
- `src/trend_parser/` - Pipeline: ingest → download → filter → VLM scoring → caption
- `src/vast_agent/` - VastAI GPU orchestration + multi-server management
- `src/comfy_pipeline/` - ComfyUI workflow execution (remote GPU only, reference implementation)
- `src/isp_pipeline/` - Video post-processing
- `src/telegram_bot/` - Telegram bot (legacy, still works)
- `frontend/` - React 18 + Vite + Tailwind 4 + shadcn/ui
- `configs/` - YAML configs (vast.yaml, wan_animate.yaml, parser.yaml)

## Code Patterns (verified)
- `from __future__ import annotations` at top of every file
- SQLite database at `shared/studio.db` (WAL mode), schema in `src/api/schema.sql`
- Async jobs via `PersistentJobManager` in `src/api/job_manager.py` (SQLite-backed)
- Progress reporting via `progress_fn` callback from job manager
- FastAPI routes in `src/api/routes/` — generation.py, parser.py, influencers.py, auth.py, events.py
- Frontend API client: `frontend/src/app/api/client.ts`; types: `types.ts`; mapper: `mappers.ts`
- SSE real-time updates via EventBus → `/api/v1/events/stream`

## SQLite / aiosqlite Patterns (verified 2026-03-17)
- `aiosqlite.connect()` uses Python's default `isolation_level=''`
- `database.py transaction()` issues `BEGIN IMMEDIATE` — crashes if DML already pending
- `generation_jobs.job_id REFERENCES jobs(job_id)` has NO `ON DELETE CASCADE`
- Sync wrapper methods create+close their own `sqlite3` connection

## SSE / EventBus Patterns (verified 2026-03-17)
- `EventBus.publish(topic, event_type, data)` — topics: "jobs" (by job_manager)
- Frontend `sse.ts` listens for: `job_progress`, `job_state`, `server_change`
- `server_change` event is never published in backend — dead code

## Review System
- Reviews stored in `reviews` + `review_videos` tables
- Supports draft saves (`draft=true`) — review not marked completed
- Frontend auto-saves drafts debounced at 1.5s
- VLM rerun triggers auto-review (caption generation + draft submit)

## Rerun System
- `_rerun_download`: retries failed downloads, updates platform + run manifests
- `_rerun_filter`: re-runs candidate filter, updates run_manifest with new report path
- `_rerun_vlm`: re-runs VLM scoring, updates run_manifest, then auto-reviews
- All reruns report progress with `{stage, current, total}`
- Enrichment (`_enrich_run`) falls back to disk scan if manifest paths missing

See also: [patterns.md](patterns.md) for SSH, dataclass, and integration patterns.
