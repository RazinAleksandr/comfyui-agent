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

## Key Architecture Facts (verified across all modules)
- `vast_agent/cli.py` builds remote shell command strings via f-string interpolation with NO shlex.quote usage
  - Spaces in filenames or --set values (e.g. prompts) break the remote comfy-pipeline command
  - This fires in the bot flow since prompts always contain spaces
- `telegram_bot/conversation.py` uses `vast-agent status rc==0` as proxy for "ComfyUI server ready"
  - rc=0 means "state file exists + API responded", NOT "ComfyUI server process is running"
- `docs/pipeline.md` line 87 shows `--host 0.0.0.0` which is a bind address, not a connect address
- All three modules have correct `__init__.py` and `__main__.py` following the reference pattern
