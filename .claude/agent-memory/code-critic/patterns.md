# Patterns and Pitfalls

## SSH Command Construction (remote.py)
- `ssh_command()` places `root@host` at the END of the arg list
- Any options appended AFTER `ssh_command()` are treated as the REMOTE COMMAND, not SSH flags
- check_ssh() has this bug: appends `-o ConnectTimeout=N` after hostname → sent as remote cmd
- Fix: add ConnectTimeout to the base `ssh_command()` or insert before the hostname

## Dataclass cls.field Access in from_yaml()
- Fields with `field(default_factory=...)` are NOT accessible as `cls.field` (AttributeError)
- Fields with simple defaults (str, int, float) ARE accessible as `cls.field`

## Instance Import in cli.py
- `Instance` class is in `vast_agent.vastai` but NOT imported in `vast_agent.cli`
- With `from __future__ import annotations`, the type annotation doesn't crash at runtime
- But it's still a broken annotation — tools using get_type_hints() will fail

## comfy-pipeline run output directory
- `comfy-pipeline run -o DIR` writes to `DIR/<workflow_name>/` (appends config.name)
- vast-agent rsync_pull handles the subdirectory correctly

## Integration Review Checklist
- NO SQLAlchemy/DB imports in trend_parser/ or api/ packages
- NO `Session` parameters in any service constructor
- NO `UploadFile` in trend_parser/ — use Path for file operations
- Config from YAML (not pydantic-settings BaseSettings) for parser
- API schemas are separate from internal data models
- comfy_pipeline has ZERO imports from trend_parser or api
- Telegram bot uses HTTP client only, no direct Python imports from backend internals
- Error responses use proper HTTP status codes (400 validation, 404 not found, 502 upstream)
- Async jobs for long-running operations (parse, generation)
- No path traversal vulnerabilities in file upload/download endpoints

## telegram_bot / conversation.py
- `_do_generation` returns `WAITING_IMAGE` for ALL missing-input cases — semantically wrong
  if only prompt is missing (should be WAITING_PROMPT). Low priority, only hit if user_data corrupted.
- Dead variable: `stdout` in `_ensure_server_up` first call. Should be `rc, _, _`.
