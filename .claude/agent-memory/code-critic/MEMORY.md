# Code Critic Memory

## Project Structure
- `src/comfy_pipeline/` - Module 1: ComfyUI Pipeline (DONE, reference impl)
- `src/vast_agent/` - Module 2: VastAI Agent
- `src/telegram_bot/` - Module 3: Telegram Bot (DONE)
- `configs/` - YAML config files
- `pyproject.toml` - Entry points: `comfy-pipeline = "comfy_pipeline.cli:main"`, `vast-agent = "vast_agent.cli:main"`, `comfy-bot = "telegram_bot.bot:main"`

## Code Patterns (verified against comfy_pipeline)
- `from __future__ import annotations` at top of every file
- Click-based CLI: `@click.group()` main, `@main.command()` subcommands
- Dataclasses with `@classmethod from_yaml()` factory
- `CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"` for locating configs
- Error output: `click.echo(..., err=True)` then `sys.exit(1)`
- `__init__.py` has only `__version__ = "0.1.0"`
- `__main__.py` imports and calls `main()` from cli module

## VastAI API Details (verified)
- Base URL: `https://console.vast.ai`
- Auth: `Authorization: Bearer <API_KEY>` header
- Key from `VAST_API_KEY` env var or `~/.vast_api_key` file
- Search: `POST /api/v0/bundles/` - returns `{"offers": [...]}`
- Create: `PUT /api/v0/asks/{offer_id}/` returns `{"new_contract": instance_id}`
- List instances: `GET /api/v0/instances/` returns `{"instances": [...]}`
- Single instance: `GET /api/v0/instances/{id}/` - likely same `{"instances": [...]}` list format
- Destroy: `DELETE /api/v0/instances/{id}/`
- `gpu_ram` filter is in MB (48000 = 48GB)
- `disk_space` filter is in GB

## Studio API Pipeline Contract (verified from backend source)
- Endpoint: `POST /api/v1/pipeline/run`
- Request `PipelineRunRequest`:
  - `influencer_id: str` (required)
  - `platforms: dict[str, PlatformPipelineConfigIn]` — a DICT (platform name -> config object), NOT a list
    - Each value requires `source: str` matching `^(seed|apify|tiktok_custom|instagram_custom)$`
    - `limit`, `enabled`, `selector` are optional with defaults
  - All other stage fields optional with defaults
- Response `PipelineRunOut`:
  - `influencer_id`, `started_at`, `base_dir`, `platforms: list[...]`, `generated_images: list[...]`
  - NO top-level `id` field
- `PipelinePlatformRunOut.selected_dir`: only non-None when `vlm.enabled=True` (default True)
- `AI_Influencer_studio/backend/app/api/pipeline.py` is the authoritative contract source

## Key Architecture Facts (verified across all modules)
- `vast_agent/cli.py` NOW uses `shlex.quote` throughout for all shell arguments in remote commands
  - `_parse_sets` returns list elements individually; they are quoted one-by-one in the run command loop
  - This is correct: `'--set'` and `'prompt=foo bar'` both survive bash quote-stripping
- `run_remote_stream` in `remote.py`: `stderr=None` means inherited from parent process (pass-through), not suppressed
  - When vast-agent is itself piped (e.g. from asyncio subprocess in bot), comfy-pipeline stderr flows back correctly
- `telegram_bot/conversation.py` uses `vast-agent status rc==0` as proxy for "ComfyUI server ready"
  - rc=0 means "state file exists + SSH reachable + instance running", NOT "ComfyUI server process is running"
- `_ensure_server_up` in conversation.py does NOT accept or forward a progress_callback
  - This means during `vast-agent up` (potentially 10+ minutes) the bot shows no progress to the user
  - `_progress_cb` is only active during `_run_generation`, not during server startup
- `client.py` `wait_for_completion` prints `"  Executing node {node}..."` to stderr
  - The regex `r"Executing node (\w+)"` in conversation.py correctly matches this (node IDs are alphanumeric)
- `comfy_pipeline/cli.py` `run --json-output` prints JSON to stdout, all progress to stderr
  - JSON is captured by `run_remote_stream` → vast-agent stdout → bot's stdout_lines → parsed in `_run_generation`
- All three modules have correct `__init__.py` and `__main__.py` following the reference pattern
- `src/api/jobs.py` (old in-memory JobManager) still exists but is orphaned — nothing imports it
- `src/api/job_manager.py` is the new persistent implementation; `src/api/deps.py` wires it up

## SQLite / aiosqlite Patterns (verified 2026-03-17)
- `aiosqlite.connect()` uses Python's default `isolation_level=''` (implicit transaction management by Python)
- With `isolation_level=''`, Python auto-BEGINs a DEFERRED transaction before the first DML in a sequence
- Manually calling `BEGIN IMMEDIATE` after pending implicit DML raises "cannot start a transaction within a transaction"
- `database.py transaction()` issues `BEGIN IMMEDIATE` — will crash if any `execute()` DML was called first on the same connection without an intervening commit
- `generation_jobs.job_id REFERENCES jobs(job_id)` has NO `ON DELETE CASCADE` — deleting from `jobs` with `PRAGMA foreign_keys = ON` will fail if any generation_jobs row references the deleted job
- Sync wrapper methods in `db_registry.py` and `job_manager.py` each create+close their own `sqlite3` connection — no `check_same_thread` issue

## SSE / EventBus Patterns (verified 2026-03-17)
- `EventBus.publish(topic, event_type, data)` — topics used: "jobs" (by job_manager); "servers" topic is subscribed but never published to
- Frontend `sse.ts` listens for event types: `job_progress`, `job_state`, `server_change`
- `server_change` event is never published anywhere in the backend — server update notifications are dead code
- `sse_stream_generator` `heartbeat_interval` parameter is effectively ignored: `min(heartbeat_interval, 1.0)` always sleeps 1s and sends a heartbeat every loop iteration when idle
