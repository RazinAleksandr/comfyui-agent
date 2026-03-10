# Telegram Bot

User-facing conversation interface for video generation. Collects inputs via chat, manages GPU servers, runs workflows, sends results back.

```
src/telegram_bot/
  bot.py           Entry point + handler registration
  conversation.py  ConversationHandler state machine
  config.py        Config loading
```

Calls `vast-agent` CLI via async subprocess — each layer talks to the next via CLI.

## Conversation Flow

```
You:  /start
Bot:  Ready. Send me a reference image.

You:  [photo]
Bot:  Got it. Now send a reference video.

You:  [video]
Bot:  What prompt?

You:  A woman dancing in a nightclub
Bot:  Renting GPU... Server started. Running generation...
      [sends result video]
      Generation complete. Send feedback to adjust, or /stop to shut down.

You:  Make it more dynamic
Bot:  [re-runs with new prompt, sends updated video]

You:  [send new video]
Bot:  Updated reference video. Re-running with same prompt...
      [sends new result]

You:  /stop
Bot:  Server destroyed. Session cost: $0.45/hr
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Begin a new session |
| `/stop` | Shut down GPU server and end session |
| `/cancel` | End conversation without destroying the server |

## Feedback Loop

After receiving a result:

| Action | What happens |
|--------|-------------|
| Send text | Used as new prompt, re-runs generation |
| Send photo | Replaces reference image, re-runs with same prompt |
| Send video | Replaces reference video, re-runs with same prompt |
| `/stop` | Destroys GPU server, ends session |

GPU server stays running between generations — no model reload overhead.

## Idle Timeout

If no messages for `idle_timeout_minutes` (default: 30), the bot auto-destroys the GPU server to prevent cost overruns.

## Config

`configs/telegram.yaml`:

```yaml
token: ${TELEGRAM_BOT_TOKEN}     # resolved from .env
allowed_users: [123456789]        # Telegram user ID whitelist
default_workflow: wan_animate
idle_timeout_minutes: 30
defaults:                         # applied to every generation
  lora_high: altf4_high_noise.safetensors
  lora_high_strength: 0.7
```

Token resolution: `${ENV_VAR}` syntax, `$ENV_VAR`, literal string, or fallback to `TELEGRAM_BOT_TOKEN` env var.

## State Machine

```
IDLE
  |  /start
  v
WAITING_IMAGE
  |  [photo]
  v
WAITING_VIDEO
  |  [video]
  v
WAITING_PROMPT
  |  [text]
  v
WAITING_FEEDBACK
  +-- [text]   -> re-run with new prompt
  +-- [photo]  -> re-run with new image
  +-- [video]  -> re-run with new video
  +-- /stop    -> destroy server -> IDLE
  +-- /cancel  -> keep server -> IDLE
```
