# ComfyUI Agent — Full Architecture

## Overview

Three-layer architecture for automated video generation:

```
User (Telegram) → Telegram Bot → VastAI Agent → ComfyUI Pipeline (remote GPU)
```

---

## Module 1: ComfyUI Pipeline ✅ DONE

**Location:** `src/comfy_pipeline/`
**CLI:** `comfy-pipeline`

Config-driven CLI that runs on the GPU server:
- Sets up ComfyUI (clone repo, install custom nodes, download models)
- Manages server lifecycle (start/stop/status with PID tracking)
- Runs workflows programmatically (upload inputs, execute, download results)
- Dynamic inputs via config (`--input name=path`)
- Semantic parameter overrides (`--set prompt="..." --set lora_high=...`)
- JSON output for machine consumption (`--json-output`)
- Batch mode for multiple input sets

---

## Module 2: VastAI Agent ⬜ TODO

**Location:** `src/vast_agent/`
**CLI:** `vast-agent`
**Config:** `configs/vast.yaml`

Manages GPU server lifecycle on VastAI and executes commands remotely:

### Responsibilities
- Search VastAI offers matching GPU/price/disk requirements
- Rent instance, wait until SSH is ready
- Push project code to remote server via rsync
- Run commands remotely via SSH (bootstrap, setup, server start, pipeline run)
- Pull results back to local machine
- Track instance state locally (`.vast-instance.json`)
- Destroy instance when done

### CLI Commands
```bash
vast-agent up -w wan_animate              # rent + push + setup + start server
vast-agent run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  --set prompt="dancing" --json-output    # rsync inputs, run remotely, pull results
vast-agent down                           # destroy instance

# Granular control
vast-agent rent                           # just rent an instance
vast-agent push                           # rsync code to server
vast-agent ssh                            # open interactive SSH session
vast-agent exec "any command"             # run arbitrary command remotely
vast-agent pull output/                   # download specific path
vast-agent status                         # instance status + cost
vast-agent destroy                        # terminate instance
```

### Config (`configs/vast.yaml`)
```yaml
gpu: RTX_4090
min_gpu_ram: 48
disk_space: 100
max_price: 0.50
image: pytorch/pytorch:2.4.0-cuda12.4-cudnn9-devel
ssh_key: ~/.ssh/id_rsa
remote_path: /workspace/comfyui-agent
```

### Implementation
- Use VastAI REST API directly via `requests` (no CLI dependency)
- SSH/rsync via `subprocess` (no paramiko needed)
- Instance ID persisted in `.vast-instance.json`
- `vast-agent run` transparently: rsync inputs → SSH run comfy-pipeline → rsync results back
- `vast-agent up` runs full chain: rent → push → bootstrap.sh → comfy-pipeline setup → server start

### Files
```
src/vast_agent/
  __init__.py
  __main__.py
  cli.py        - Click CLI entry point
  vastai.py     - VastAI REST API wrapper (search, create, destroy, ssh info)
  remote.py     - SSH/rsync operations (push, exec, pull)
  config.py     - Config loading
```

---

## Module 3: Telegram Bot ⬜ TODO

**Location:** `src/telegram_bot/`
**CLI:** `comfy-bot`
**Config:** `configs/telegram.yaml`

Telegram bot that provides user-facing interface. Runs on a cheap always-on VPS alongside vast-agent.

### Responsibilities
- Receive reference images, videos, and prompts from user via Telegram
- Manage conversation state (collecting inputs → generating → feedback loop)
- Call `vast-agent` CLI to manage GPU server and run generations
- Send result videos back to user
- Handle feedback loop (user asks for adjustments → re-run with new params)
- Auto-shutdown GPU after idle timeout to save costs

### Conversation Flow
```
User: /start
Bot:  Ready. Send me a reference image.

User: [photo]
Bot:  Got it. Now send a reference video.

User: [video]
Bot:  What prompt?

User: A woman dancing in a nightclub
Bot:  ⏳ Renting GPU... → Setting up... → Running...
      ✅ [sends result video]

User: Make it more dynamic
Bot:  ⏳ Re-running...
      ✅ [sends result video]

User: /stop
Bot:  Server destroyed. Session cost: $1.23
```

### State Machine
```
IDLE → WAITING_IMAGE → WAITING_VIDEO → WAITING_PROMPT → GENERATING → WAITING_FEEDBACK
                                                                          ↓
                                                              re-run / new input / /stop → IDLE
```

### Config (`configs/telegram.yaml`)
```yaml
token: ${TELEGRAM_BOT_TOKEN}
allowed_users: [123456789]
default_workflow: wan_animate
idle_timeout_minutes: 30
defaults:
  lora_high: altf4_high_noise.safetensors
  lora_high_strength: 0.7
```

### Implementation
- `python-telegram-bot` library (async, ConversationHandler for state machine)
- Calls `vast-agent` CLI via subprocess (each layer talks to the next via CLI)
- Downloads Telegram files to local temp dir, passes paths to vast-agent
- Idle timeout: if no requests for N minutes, auto `vast-agent down`
- Whitelist by Telegram user ID for security

### Files
```
src/telegram_bot/
  __init__.py
  bot.py           - Entry point, handler registration
  conversation.py  - ConversationHandler state machine
  config.py        - Config loading
```

---

## Build Order

1. **VastAI Agent** — critical bridge, testable from terminal
2. **Telegram Bot** — thin conversation wrapper over vast-agent
3. **Idle auto-shutdown** — quality of life, prevents cost overruns

## Deployment

```
Your VPS ($5/mo, always-on):
  - comfy-bot (long-running telegram bot)
  - vast-agent (called by bot on demand)

VastAI GPU (on-demand, $/hr):
  - comfy-pipeline (called by vast-agent via SSH)
  - spun up per session, destroyed on /stop or idle timeout
```
