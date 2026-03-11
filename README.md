# ComfyUI Agent

Automated video generation pipeline: Telegram bot -> VastAI GPU management -> ComfyUI workflow execution.

```
User (Telegram) -> Telegram Bot -> Parser API (trend discovery) -> VastAI Agent -> ComfyUI Pipeline (remote GPU)
```

## Setup

### 1. Clone and install

```bash
git clone git@github.com:RazinAleksandr/comfyui-agent.git
cd comfyui-agent
./bootstrap.sh
source .venv/bin/activate
```

### 2. Accounts and credentials

**VastAI:**

1. Create account at [https://vast.ai](https://vast.ai) and add credit
2. Get API key from [https://cloud.vast.ai/cli/](https://cloud.vast.ai/cli/)
3. Register your SSH public key at [https://cloud.vast.ai/account/](https://cloud.vast.ai/account/) (SSH Keys section)

**Telegram:**

1. Create a bot via [@BotFather](https://t.me/BotFather) — send `/newbot`, copy the token
2. Get your user ID from [@userinfobot](https://t.me/userinfobot)

**Parser API (optional, for `/parse` flow):**

The `/parse` command connects to a local AI Influencer Studio API to discover trending videos. See [docs/telegram_bot.md](docs/telegram_bot.md) for details.

### 3. Configuration

```bash
# Set secrets
cp .env.example .env
# Edit .env — fill in VAST_API_KEY and TELEGRAM_BOT_TOKEN
```

```bash
# Edit configs if needed
nano configs/vast.yaml         # GPU type, price, disk, SSH key
nano configs/telegram.yaml     # allowed_users, default workflow, idle timeout, studio API
# Set allowed_users to your Telegram user ID (get it from @userinfobot)
```

The `.env` file is loaded automatically by all CLIs. It is git-ignored.

### 4. Verify

```bash
# Test VastAI
vast-agent rent && vast-agent status && vast-agent destroy

# Test Telegram bot
comfy-bot    # send /start to your bot in Telegram
```

## Usage

### Telegram bot (recommended)

```bash
comfy-bot
```

#### Commands

| Command | Description |
|---------|-------------|
| `/start` | Manual mode — provide image, video, prompt one-by-one |
| `/parse [#hashtags]` | Auto-discover trending videos, review & batch generate |
| `/resume` | Resume an interrupted batch generation |
| `/skip` | Skip the current video during `/parse` review |
| `/done` | Finish review early and start batch generation |
| `/stop` | Shut down GPU server and end session |
| `/cancel` | End conversation without destroying the server |

#### Manual mode (`/start`)

1. `/start` — bot asks for a reference image
2. Send a **photo** — bot asks for a reference video
3. Send a **video** — bot asks for a prompt
4. Send **text prompt** — bot rents a GPU (if needed), runs the pipeline, sends back the generated video

After generation you enter a **feedback loop** — you can:
- Send **new text** to re-run with a different prompt (keeps same image/video)
- Send **new image** or **new video** to swap that input and re-run
- `/stop` — shuts down the GPU server and reports session cost
- `/cancel` — ends conversation but leaves the GPU running

#### Parser mode (`/parse`)

1. `/parse #dance #trending` — bot calls the Parser API to discover trending videos
2. Send a **reference photo** — shared across all generations
3. For each video, send a **text prompt** to approve or `/skip` to skip
4. `/done` or run out of videos — bot rents GPU once and generates all approved videos in batch

All inputs and outputs are logged to `output/parse_sessions/` — see [Session Logging](#session-logging).

#### Resume (`/resume`)

If the bot crashes or GPU fails mid-batch, `/resume` picks up where you left off. It scans `output/parse_sessions/` for incomplete sessions and resumes generation for pending/failed items.

### CLI only

```bash
vast-agent up -w wan_animate

vast-agent run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  --set prompt="A woman dancing in a nightclub"

vast-agent down
```

### Direct server access

If you already have a GPU server, use `comfy-pipeline` directly — see [docs/pipeline.md](docs/pipeline.md).

## Session Logging

The `/parse` flow persists session data to disk for logging and crash recovery:

```
output/
└── parse_sessions/
    └── 20260311_174413/              # timestamp of pipeline run
        ├── session.json              # manifest: ref image, queue, status per item
        ├── reference_image.jpg       # copy of user's reference photo
        └── results/                  # generation outputs
            ├── 001_tiktok_dance_.../
            │   └── output.mp4
            └── 002_tiktok_walk_.../
                └── output.mp4
```

`session.json` tracks each queued item with status (`pending` / `generating` / `completed` / `failed`), the prompt used, video path, and output paths. This lets you:
- Know exactly which reference image, video, and prompt produced each output
- Resume failed batches with `/resume` instead of re-parsing from scratch

## VPS Deployment

For running the Telegram bot 24/7 on a cheap VPS ($5/mo):

```bash
git clone git@github.com:RazinAleksandr/comfyui-agent.git
cd comfyui-agent
./bootstrap.sh
source .venv/bin/activate
cp .env.example .env
# Edit .env with your keys
```

systemd service:

```bash
sudo tee /etc/systemd/system/comfy-bot.service << 'EOF'
[Unit]
Description=ComfyUI Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/comfyui-agent
ExecStart=/path/to/comfyui-agent/.venv/bin/comfy-bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable comfy-bot
sudo systemctl start comfy-bot
```

## Project Structure

```
src/
  comfy_pipeline/   ComfyUI workflow execution (runs on GPU server)
  vast_agent/       VastAI GPU rental + remote execution (runs on VPS)
  telegram_bot/     Telegram user interface (runs on VPS)

configs/
  wan_animate.yaml  Wan 2.2 Animate workflow config
  vast.yaml         VastAI GPU preferences
  telegram.yaml     Telegram bot settings

output/
  parse_sessions/   Logged session data from /parse runs (gitignored)
```

## Docs

- [ComfyUI Pipeline](docs/pipeline.md) — commands, workflow configs, batch mode, parameter overrides
- [VastAI Agent](docs/vast_agent.md) — commands, config, state tracking
- [Telegram Bot](docs/telegram_bot.md) — conversation flow, commands, parser API, session logging
