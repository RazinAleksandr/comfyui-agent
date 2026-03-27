# INTEGRATIONS

External services, APIs, and data sources this codebase integrates with.

---

## External APIs

### VastAI (GPU Cloud)
- **Purpose**: Rent on-demand GPU instances for AI video generation
- **Client**: `src/vast_agent/vastai.py` — `VastClient` wraps the VastAI REST API
- **Auth**: `VAST_API_KEY` env var (or `~/.vast_api_key` fallback)
- **Operations**: search offers, create instances, destroy instances, list running instances
- **Config**: `configs/vast.yaml` — GPU spec (L40), disk, price caps, geolocation filter, SSH key
- **SSH access**: Remote GPU accessed via SSH; `src/vast_agent/remote.py` runs commands/transfers files
- **Instance label**: `avatar-factory` (used to re-identify instances on restart)

### Google Gemini (LLM/VLM)
- **Purpose 1**: VLM scoring — evaluate videos for persona fit (8 criteria)
  - `src/trend_parser/gemini.py`, `src/trend_parser/vlm.py`
  - Model: configurable, default `gemini-2.0-flash`
- **Purpose 2**: Reference image alignment — generate character-in-scene images
  - `src/api/ref_align.py`
  - Model: `gemini-3.1-flash-image-preview` (image generation)
- **Purpose 3**: Auto-caption generation for review drafts
  - `src/trend_parser/caption.py`
- **Auth**: `GEMINI_API_KEY` env var — checked in multiple routes; requests fail with HTTP 500 if missing
- **SDK**: `google-genai` Python package

### Apify (Social Media Scraping)
- **Purpose**: Scrape TikTok and Instagram video metadata via managed cloud actors
- **Client**: `src/trend_parser/adapters/apify.py`
- **Auth**: `APIFY_TOKEN` env var
- **Actors**:
  - TikTok: `TIKTOK_APIFY_ACTOR` env var
  - Instagram: `INSTAGRAM_APIFY_ACTOR` env var
- **Config**: `configs/parser.yaml` — cost optimization, retry settings, overfetch multiplier
- **Fallback**: `apify_fallback_to_seed: false` — does not fall back to seed data

### Telegram Bot API
- **Purpose**: Legacy Telegram UI for pipeline control and result browsing
- **Client**: `src/telegram_bot/bot.py` — uses `python-telegram-bot`
- **Auth**: `TELEGRAM_BOT_TOKEN` env var
- **Status**: Legacy, still functional but secondary to web UI

### HuggingFace Hub
- **Purpose**: Download model weights to remote GPU (LoRA files, base models)
- **Auth**: `HF_TOKEN` env var — injected into remote GPU setup commands
- **Usage**: `src/vast_agent/service.py` prepends `HF_TOKEN=...` to remote setup commands

---

## Data Ingestion Sources

### TikTok Custom (Browser-Based)
- **Purpose**: Zero-cost TikTok scraping via Playwright browser automation
- **Adapter**: `src/trend_parser/adapters/tiktok.py`
- **Auth**: `TIKTOK_MS_TOKENS` env var (session tokens)
- **Config**: headless Chromium, session count, sleep delays
- **Requires**: Playwright installed on host

### Instagram Custom (Instaloader)
- **Purpose**: Instagram Reels scraping via instaloader
- **Adapter**: `src/trend_parser/adapters/instagram.py`
- **Auth**: `INSTAGRAM_CUSTOM_USERNAME`, `INSTAGRAM_CUSTOM_PASSWORD`, `INSTAGRAM_CUSTOM_SESSION_FILE` env vars
- **Config**: `instagram_custom_max_posts_per_tag: 120`

### yt-dlp (Video Downloader)
- **Purpose**: Download TikTok/Instagram videos from URLs scraped by ingest adapters
- **Invocation**: `python -m yt_dlp` (not shebang-based, avoids stale binary issues)
- **Config**: format `bv*+ba/b`, merge to mp4, optional cookies file (`YT_DLP_COOKIES_FILE`)
- **Module**: `src/trend_parser/downloader.py`

---

## Databases / Storage

### SQLite
- **Path**: `shared/studio.db`
- **Mode**: WAL (Write-Ahead Logging) for concurrent access
- **Schema**: `src/api/schema.sql`
- **Tables**: `influencers`, `pipeline_runs`, `reviews`, `review_videos`, `generation_jobs`, `servers`, `jobs`
- **Access**: `src/api/database.py` (connection), `src/api/db_store.py` (data layer)
- **Migrations**: `src/api/migrate.py` — incremental ALTER TABLE migrations

### Filesystem
- **Base path**: `shared/influencers/{id}/pipeline_runs/{timestamp}/`
- **Contents**: raw downloads, analysis reports, filtered videos, VLM results, generated outputs
- **Access**: `src/trend_parser/store.py` — `FilesystemStore` for file operations
- **Static serving**: FastAPI serves `shared/` under `/files/` via `SPAStaticFiles`

### Frontend Build Output
- **Path**: `frontend-dist/`
- **Served by**: FastAPI via `SPAStaticFiles` with SPA catch-all route

---

## Authentication (Internal)

### Basic Auth (Optional)
- **Scope**: FastAPI API — HTTP Basic Auth middleware
- **Config**: `AUTH_ADMIN_USERNAME` (default: `admin`), `AUTH_ADMIN_PASSWORD` (default: `admin`) env vars
- **Location**: `src/api/app.py`

---

## Remote GPU Setup (ComfyUI)

### ComfyUI
- **Deployed to**: VastAI GPU instances
- **Installed by**: setup scripts in `src/vast_agent/service.py`
- **Workflows**: `configs/wan_animate.yaml`, `configs/x2v_animate.yaml` — ComfyUI node graphs
- **Custom nodes**: installed via `extra_pip` in workflow configs
- **Communication**: SSH + HTTP API on remote instance port 8188

### X2V Pipeline (LightX2V)
- **Path**: `LightX2V/` (local checkout)
- **Config**: `configs/x2v_animate.yaml`
- **Purpose**: Alternative video generation pipeline (image-to-video)

---

## SSE (Real-Time Events)

- **Endpoint**: `GET /api/v1/events/stream`
- **Purpose**: Push job progress and state changes to frontend
- **Implementation**: `src/api/events.py` — async generator, per-client queues
- **Frontend**: `frontend/src/app/api/sse.ts` — singleton with auto-reconnect + exponential backoff

---

## Environment Variables Summary

| Variable | Service | Required |
|---|---|---|
| `VAST_API_KEY` | VastAI | Yes |
| `GEMINI_API_KEY` | Google Gemini | Yes |
| `APIFY_TOKEN` | Apify | For Apify source |
| `TIKTOK_APIFY_ACTOR` | Apify TikTok | For Apify source |
| `INSTAGRAM_APIFY_ACTOR` | Apify Instagram | For Apify source |
| `TIKTOK_MS_TOKENS` | TikTok custom | For TikTok custom |
| `INSTAGRAM_CUSTOM_USERNAME` | Instagram | For Instagram custom |
| `INSTAGRAM_CUSTOM_PASSWORD` | Instagram | For Instagram custom |
| `INSTAGRAM_CUSTOM_SESSION_FILE` | Instagram | For Instagram custom |
| `YT_DLP_COOKIES_FILE` | yt-dlp | Optional |
| `HF_TOKEN` | HuggingFace | For model downloads |
| `TELEGRAM_BOT_TOKEN` | Telegram | For bot |
| `AUTH_ADMIN_USERNAME` | Basic Auth | Optional (default: admin) |
| `AUTH_ADMIN_PASSWORD` | Basic Auth | Optional (default: admin) |

All env vars loaded from `.env` file via `set -a; source .env; set +a` before starting the server. NOT auto-loaded by python-dotenv.
