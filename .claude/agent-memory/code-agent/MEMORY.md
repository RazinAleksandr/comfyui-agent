# Code Agent Memory

## Project Structure
- `src/comfy_pipeline/` - Module 1: ComfyUI Pipeline (DONE)
- `src/vast_agent/` - Module 2: VastAI Agent
- `src/telegram_bot/` - Module 3: Telegram Bot (TODO)
- `configs/` - YAML config files (workflow configs, vast.yaml, telegram.yaml)
- `pyproject.toml` - Entry points registered under `[project.scripts]`
- State files: `.vast-instance.json` in project root

## Code Patterns
- `from __future__ import annotations` at top of every file
- Click-based CLIs with `@click.group()` main and `@main.command()` subcommands
- Dataclasses for config with `@classmethod from_yaml()` factory
- `CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"` pattern for locating configs
- Separator comments: `# ---------------------------------------------------------------------------`
- Error output via `click.echo(..., err=True)` then `sys.exit(1)`
- `__init__.py` has only `__version__ = "0.1.0"`
- `__main__.py` imports and calls `main()` from cli module

## VastAI API Details
- Base URL: `https://console.vast.ai`
- Auth: `Authorization: Bearer <API_KEY>` header
- Key from `VAST_API_KEY` env var or `~/.vast_api_key` file
- Rate limit: ~2s between requests (implemented via throttle in VastClient)
- Search: `POST /api/v0/bundles/` with filter JSON
- Create: `PUT /api/v0/asks/{offer_id}/` returns `{"new_contract": instance_id}`
- Instance: `GET /api/v0/instances/{id}/`
- Destroy: `DELETE /api/v0/instances/{id}/`
