# Patterns and Pitfalls

## SSH Command Construction (remote.py)
- `ssh_command()` places `root@host` at the END of the arg list
- Any options appended AFTER `ssh_command()` are treated as the REMOTE COMMAND, not SSH flags
- check_ssh() has this bug: appends `-o ConnectTimeout=N` after hostname → sent as remote cmd
- Fix: add ConnectTimeout to the base `ssh_command()` or insert before the hostname

## Dataclass cls.field Access in from_yaml()
- Fields with `field(default_factory=...)` are NOT accessible as `cls.field` (AttributeError)
- Fields with simple defaults (str, int, float) ARE accessible as `cls.field`
- VastConfig: `cls.gpu`, `cls.min_gpu_ram`, etc. work; `cls.extra_filters` would not (but the code uses `{}` literal, not `cls.extra_filters`)

## Instance Import in cli.py
- `Instance` class is in `vast_agent.vastai` but NOT imported in `vast_agent.cli`
- With `from __future__ import annotations`, the type annotation `-> Instance` doesn't crash at runtime
- But it's still a broken annotation - tools using get_type_hints() will fail

## comfy-pipeline run output directory
- `comfy-pipeline run -o DIR` actually writes to `DIR/<workflow_name>/` (appends config.name)
- vast-agent rsync_pull from `remote_output/` gets everything including subdirs - files are pulled
- JSON path rewriting in vast-agent correctly handles the subdirectory

## telegram_bot / conversation.py (post-fix state as of 2nd review)
- Issues 1-5 from initial review are all fixed.
- Remaining issue (not yet fixed): `_do_generation` returns `WAITING_IMAGE` for ALL missing-input
  cases (image, video, or prompt). If only prompt is missing, `WAITING_IMAGE` is wrong — should
  return `WAITING_PROMPT`. In practice this path is only hit if user_data is corrupted, but the
  state returned is still semantically incorrect.
- Dead variable: `stdout` in `_ensure_server_up` first call (`rc, stdout, _ = ...`) — stdout is
  never used after the rc==0 fix. Should be `rc, _, _`.
- `_idle_shutdown_callback` is defined as `async def` but `job_queue.run_once()` in PTB v21
  requires a coroutine function — async def is correct. No issue here.
- `comfy-bot` entry point in pyproject.toml points to `telegram_bot.bot:main` — correct.
- ConversationHandler fallbacks include `CommandHandler("stop", stop)` but `stop` is also in the
  WAITING_FEEDBACK state handlers. This is fine — PTB checks states first, then fallbacks.
