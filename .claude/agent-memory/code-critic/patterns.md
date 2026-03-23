# Patterns and Pitfalls

## SSH Command Construction (remote.py)
- `ssh_command()` places `root@host` at the END of the arg list
- Any options appended AFTER `ssh_command()` are treated as the REMOTE COMMAND, not SSH flags
- check_ssh() had bug: appended `-o ConnectTimeout=N` after hostname → sent as remote cmd
- Fix: add ConnectTimeout to the base `ssh_command()` or insert before the hostname

## Dataclass cls.field Access in from_yaml()
- Fields with `field(default_factory=...)` are NOT accessible as `cls.field` (AttributeError)
- Fields with simple defaults (str, int, float) ARE accessible as `cls.field`

## comfy-pipeline run output directory
- `comfy-pipeline run -o DIR` writes to `DIR/<workflow_name>/` (appends config.name)
- vast-agent rsync_pull handles the subdirectory correctly

## Remote GPU Output Parsing (vast_agent/service.py)
- stdout may contain mixed JSON + log output
- Parser scans lines in reverse to find JSON object with `outputs` key
- Falls back to scanning local output directory for media files if JSON not found
- Media extensions checked: .mp4, .webm, .gif, .png, .jpg

## Integration Review Checklist
- comfy_pipeline has ZERO imports from trend_parser or api
- Telegram bot uses HTTP client only, no direct Python imports from backend internals
- Config from YAML with dataclass `from_yaml()` for parser/pipeline configs
- API schemas (Pydantic BaseModel) are separate from internal data models
- Async jobs for long-running operations (pipeline, generation, reruns)
- No path traversal vulnerabilities in file upload/download endpoints
- yt-dlp invoked via `python -m yt_dlp` (not direct binary)

## telegram_bot / conversation.py
- `_do_generation` returns `WAITING_IMAGE` for ALL missing-input cases — semantically wrong
  if only prompt is missing (should be WAITING_PROMPT). Low priority, only hit if user_data corrupted.
- Dead variable: `stdout` in `_ensure_server_up` first call. Should be `rc, _, _`.
