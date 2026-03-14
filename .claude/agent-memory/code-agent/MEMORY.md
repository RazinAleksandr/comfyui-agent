# Code Agent Memory

## Project Structure
- `src/comfy_pipeline/` - ComfyUI Pipeline (GPU server only, DONE)
- `src/vast_agent/` - VastAI GPU orchestration (VPS, DONE)
- `src/telegram_bot/` - Telegram Bot UI (VPS, DONE)
- `src/isp_pipeline/` - ISP video post-processing (VPS, DONE)
- `src/trend_parser/` - NEW: trend parsing pipeline (VPS only, TODO)
- `src/api/` - NEW: FastAPI unified API (VPS only, TODO)
- `configs/` - YAML config files (workflow, vast, telegram, parser)
- `pyproject.toml` - Entry points under `[project.scripts]`
- State files: `.vast-instance.json` in project root

## Architecture
- Unified backend: FastAPI API + parser + generation orchestration
- Telegram bot is EXTERNAL CLIENT (separate process, HTTP calls to API)
- Future Vue frontend is another external client
- No SQLAlchemy/DB anywhere — filesystem-only storage in `shared/`
- GPU isolation: `comfy_pipeline` has zero imports from `trend_parser` or `api`

## Code Patterns
- `from __future__ import annotations` at top of every file
- Click-based CLIs with `@click.group()` main and `@main.command()` subcommands
- Dataclasses for config with `@classmethod from_yaml()` factory
- `CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"` for locating configs
- Separator comments: `# ---------------------------------------------------------------------------`
- Error output via `click.echo(..., err=True)` then `sys.exit(1)`
- `__init__.py` has only `__version__ = "0.1.0"`
- `__main__.py` imports and calls `main()` from cli module

## Integration Plan
- Full plan at `/root/workspace/comfyui-agent/INTEGRATION_PLAN.md`
- Phase 1: Create trend_parser + api packages, update telegram_bot
- Phase 2: Generation API with job tracking
- Phase 3: X pipeline, image gen endpoints

## Key Rules
- No SQLAlchemy imports in any new code
- Parser services take config + store, NOT db Session
- Return dicts/dataclasses, NOT ORM models
- Config from YAML with ${ENV_VAR} interpolation
- Filesystem storage only (shared/ directory)
- Parser code importable without FastAPI installed

## VastAI API Details
- Base URL: `https://console.vast.ai`
- Auth: `Authorization: Bearer <API_KEY>` header
- Key from `VAST_API_KEY` env var or `~/.vast_api_key` file
- Rate limit: ~2s between requests (throttle in VastClient)
- Search: `POST /api/v0/bundles/` with filter JSON
- Create: `PUT /api/v0/asks/{offer_id}/` returns `{"new_contract": instance_id}`
- Instance: `GET /api/v0/instances/{id}/`
- Destroy: `DELETE /api/v0/instances/{id}/`
