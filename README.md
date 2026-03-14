# ComfyUI Agent

Unified backend for AI influencer video generation: trend discovery, GPU orchestration, and Telegram UI through a single REST API.

```
Clients (Telegram Bot, Frontend)
    │
    ▼
FastAPI API (port 8000)
├── /api/v1/parser/*       trend parsing pipeline
├── /api/v1/influencers/*  influencer management
├── /api/v1/generation/*   GPU server + workflow execution
├── /api/v1/jobs/*         async job tracking
└── /health
    │
    ├── trend_parser/   ingest → download → filter → VLM select
    ├── vast_agent/     VastAI GPU rental + SSH execution
    ├── isp_pipeline/   video postprocessing
    └── comfy_pipeline/ ComfyUI workflow runner (remote GPU)
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

**Parser (for `/parse` flow):**

- **Gemini API key** — required for VLM video scoring ([https://aistudio.google.com/apikey](https://aistudio.google.com/apikey))
- **Apify token** — optional, for Apify-based scraping ([https://apify.com](https://apify.com))
- **TikTok/Instagram credentials** — optional, for custom browser scraping (requires playwright)

### 3. Configuration

```bash
# Set secrets
cp .env.example .env
# Edit .env — fill in all required keys:
#   VAST_API_KEY, TELEGRAM_BOT_TOKEN, HF_TOKEN
#   GEMINI_API_KEY (required for /parse)
#   APIFY_TOKEN, TIKTOK_MS_TOKENS, INSTAGRAM_* (optional, per source)
```

```bash
# Edit configs if needed
nano configs/vast.yaml         # GPU type, price, disk, SSH key
nano configs/telegram.yaml     # allowed_users, backend_url, default workflow
nano configs/parser.yaml       # default source, VLM settings, filter params
```

The `.env` file is loaded automatically by all CLIs. It is git-ignored.

### 4. Verify

```bash
# Test API
comfy-api &
curl http://localhost:8000/health

# Test VastAI
vast-agent rent && vast-agent status && vast-agent destroy

# Test Telegram bot
comfy-bot    # send /start to your bot in Telegram
```

## Usage

### Two processes

The backend runs as two separate processes on the VPS:

```bash
comfy-api --host 0.0.0.0 --port 8000    # FastAPI server
comfy-bot                                 # Telegram bot (calls API via HTTP)
```

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/parser/run` | Start trend parsing job |
| `POST` | `/api/v1/parser/pipeline` | Run full pipeline (ingest→download→filter→VLM) |
| `POST` | `/api/v1/parser/signals` | Lightweight signal extraction |
| `GET` | `/api/v1/parser/runs` | List pipeline runs for influencer |
| `GET` | `/api/v1/parser/runs/{run_id}` | Get run details |
| `GET` | `/api/v1/influencers` | List all influencers |
| `GET` | `/api/v1/influencers/{id}` | Get influencer profile |
| `PUT` | `/api/v1/influencers/{id}` | Upsert influencer |
| `POST` | `/api/v1/influencers/{id}/reference-image` | Upload reference image |
| `GET` | `/api/v1/generation/server/status` | GPU server status |
| `POST` | `/api/v1/generation/server/up` | Start GPU server |
| `POST` | `/api/v1/generation/server/down` | Destroy GPU server |
| `POST` | `/api/v1/generation/run` | Start generation job |
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status |
| `GET` | `/api/v1/jobs` | List recent jobs |

Long-running operations (parsing, generation, server startup) return a `job_id` immediately. Poll `/api/v1/jobs/{job_id}` for status.

### Telegram bot

```bash
comfy-bot
```

| Command | Description |
|---------|-------------|
| `/start` | Manual mode — provide image, video, prompt one-by-one |
| `/parse [#hashtags]` | Auto-discover trending videos, review & batch generate |
| `/resume` | Resume an interrupted batch generation |
| `/skip` | Skip the current video during `/parse` review |
| `/done` | Finish review early and start batch generation |
| `/stop` | Shut down GPU server and end session |
| `/cancel` | End conversation without destroying the server |

See [docs/telegram_bot.md](docs/telegram_bot.md) for conversation flow details.

### CLI only

```bash
vast-agent up -w wan_animate

vast-agent run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  --set prompt="A woman dancing in a nightclub"

vast-agent down
```

See [docs/vast_agent.md](docs/vast_agent.md) for all commands.

### Direct server access

If you already have a GPU server, use `comfy-pipeline` directly — see [docs/pipeline.md](docs/pipeline.md).

## VPS Deployment

For running 24/7 on a VPS ($5/mo):

```bash
git clone git@github.com:RazinAleksandr/comfyui-agent.git
cd comfyui-agent
./bootstrap.sh
source .venv/bin/activate
cp .env.example .env
# Edit .env with your keys
```

systemd services:

```bash
# API server
sudo tee /etc/systemd/system/comfy-api.service << 'EOF'
[Unit]
Description=ComfyUI API Server
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/comfyui-agent
ExecStart=/path/to/comfyui-agent/.venv/bin/comfy-api --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Telegram bot
sudo tee /etc/systemd/system/comfy-bot.service << 'EOF'
[Unit]
Description=ComfyUI Telegram Bot
After=network.target comfy-api.service
Wants=comfy-api.service

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

sudo systemctl enable comfy-api comfy-bot
sudo systemctl start comfy-api comfy-bot
```

## Project Structure

```
src/
  api/              FastAPI application (routes, deps, job manager)
  trend_parser/     Trend discovery pipeline (ingest, download, filter, VLM)
  comfy_pipeline/   ComfyUI workflow execution (runs on GPU server)
  vast_agent/       VastAI GPU rental + remote execution (runs on VPS)
  telegram_bot/     Telegram user interface (runs on VPS)
  isp_pipeline/     Video postprocessing (grain, sharpness, brightness, vignette)

configs/
  parser.yaml       Trend parser settings (sources, VLM, filter)
  vast.yaml         VastAI GPU preferences
  telegram.yaml     Telegram bot settings
  wan_animate.yaml  Wan 2.2 Animate workflow config
  isp_postprocess.yaml  ISP postprocessing settings

shared/             Data directory (gitignored)
  influencers/      Per-influencer profiles, reference images, pipeline runs
  downloads/        Downloaded video cache
  seeds/            Seed data for development
  parse_sessions/   Telegram /parse session logs
```

## Docs

- [API Server](docs/api.md) — endpoints, async job system, dependencies
- [Trend Parser](docs/trend_parser.md) — pipeline stages, sources, filter scoring, VLM evaluation
- [ComfyUI Pipeline](docs/pipeline.md) — commands, workflow configs, batch mode, parameter overrides
- [VastAI Agent](docs/vast_agent.md) — commands, config, state tracking, programmatic API
- [Telegram Bot](docs/telegram_bot.md) — conversation flow, commands, session logging
