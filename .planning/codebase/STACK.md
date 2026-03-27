# Technology Stack

**Analysis Date:** 2026-03-27

## Languages

**Primary:**
- Python 3.10+ - All backend services, pipelines, and CLI tools live in `src/`
- TypeScript 5 - React frontend with strict type checking
- SQL - SQLite WAL-mode database at `shared/studio.db`

**Secondary:**
- YAML - Configuration files for all major subsystems (`configs/`)
- JSON - ComfyUI workflow definitions and API payloads

## Runtime

**Environment:**
- Python 3.10+ (specified in `pyproject.toml`)
- Node.js + npm (package-lock.json indicates npm usage)
- Virtual environment: `.venv/` directory with `pip install -e ".[vps]"`

**Package Manager:**
- **Python:** pip with setuptools build system
  - Lockfile: `pyproject.toml` with optional `[vps]` extra group for production dependencies
  - Dependencies auto-install via `pip install -e ".[vps]"`
- **Node.js:** npm (v6+ inferred from package-lock.json v3)
  - Lockfile: `frontend/package-lock.json` (checked into git)

## Frameworks

**Backend Core:**
- FastAPI 0.115+ - REST API server at `src/api/app.py:create_app()`, runs on port 8000 via Uvicorn
- Uvicorn 0.30+ - ASGI server with standard middleware support
- Pydantic 2.0+ - Request/response validation and Pydantic models (Settings pattern)

**Frontend Core:**
- React 18.3.1 - SPA built with JSX/TSX
- React Router 7.13.0 - Client-side routing
- Vite 6.3.5 - Build tool and dev server (dev proxy at port 5173)
- Tailwind CSS 4.1.12 - Utility-first CSS framework
- @tailwindcss/vite 4.1.12 - Tailwind integration with Vite (replaces PostCSS plugin)

**UI Component Library:**
- shadcn/ui (Radix UI primaries) - Headless component library
  - @radix-ui/* (checkbox, dialog, dropdown-menu, select, etc.) - 40+ primitive components
  - lucide-react 0.487.0 - Icon library (2400+ SVG icons)

**Form & Data:**
- React Hook Form 7.55.0 - Lightweight form state management
- date-fns 3.6.0 - Date manipulation and formatting
- react-hook-form - Uncontrolled form components with validation hooks

**Layout & Animation:**
- react-resizable-panels 2.1.7 - Resizable panel splitters
- embla-carousel-react 8.6.0 - Carousel/slider component
- motion 12.23.24 - Animation library (Framer Motion fork)
- canvas-confetti 1.9.4 - Confetti animation effects
- sonner 2.0.3 - Toast notifications

**Styling & Utilities:**
- Emotion (@emotion/react 11.14.0, @emotion/styled 11.14.1) - CSS-in-JS
- Material-UI (MUI) 7.3.5 - Component library (icons + core components)
- class-variance-authority 0.7.1 - CSS class composition
- clsx 2.1.1 - Conditional CSS class names
- tailwind-merge 3.2.0 - Merge conflicting Tailwind classes

**Theme Management:**
- next-themes 0.4.6 - Dark mode toggle

**Testing:**
- pytest (inferred from `qa_test.py` but not in dependencies) - Testing framework

**Build/Dev:**
- Setuptools 68+ - Python package building
- PostCSS - CSS transformation (config at `frontend/postcss.config.mjs`, empty)

**Pipeline Frameworks:**
- ComfyUI - Node-based video generation workflow (remote GPU execution)
- LightX2V - Video animation model (custom repo cloned to `/workspace/LightX2V`)
- Wan 2.2 - Character animation model via ComfyUI

## Key Dependencies

**Critical (Backend):**
- `aiosqlite>=0.20` - Async SQLite driver for database access (`src/api/database.py`)
- `httpx>=0.27` - Async HTTP client for API calls
- `requests>=2.28` - Sync HTTP client for VastAI and Apify APIs
- `pyyaml>=6.0` - YAML config parsing (all `configs/*.yaml` files)
- `pydantic>=2.0` - Data validation and config management
- `PyJWT>=2.8` - JWT token generation/validation for auth
- `bcrypt>=4.0` - Password hashing for authentication
- `websocket-client>=1.5` - WebSocket support (legacy; SSE now used for live updates)

**Critical (Trend Parser):**
- `yt-dlp` - Video downloading from social platforms
- `instaloader>=4.10` - Instagram scraping (custom adapter at `src/trend_parser/adapters/instagram.py`)
- `TikTokApi>=6.0` - TikTok API client for custom scraping (custom adapter at `src/trend_parser/adapters/tiktok.py`)
- `playwright` - Browser automation for TikTok scraping (headless Chromium)
- `requests` - Apify HTTP API calls (custom adapter at `src/trend_parser/adapters/apify.py`)

**Critical (GPU Orchestration):**
- `requests` - VastAI REST API calls (client at `src/vast_agent/vastai.py:VastClient`)
- SSH-based remote execution (paramiko wrapped in `src/vast_agent/remote.py`)

**Critical (Media Processing):**
- `opencv-python-headless>=4.8` - Video frame extraction and processing
- `numpy>=1.24` - Array operations for image/video frames
- `torch` - PyTorch for ML inference (installed on remote GPU, not in main venv)
- `onnxruntime-gpu` - ONNX model runtime on remote GPU (installed via `wan_animate.yaml` extra_pip)

**AI/ML:**
- `huggingface_hub>=0.20` - Model downloads from HuggingFace Hub
- Google Gemini API (via `requests` HTTP calls in `src/trend_parser/gemini.py`)
  - Models: `gemini-2.5-flash` (text) and `gemini-3.1-flash-image-preview` (image)
- Apify API (HTTP-based, scraping orchestration)

**CLI & Config:**
- `click>=8.0` - Command-line interface builder (entry points in `pyproject.toml`)
- `python-dotenv>=1.0` - `.env` file loading (sourced manually before CLI/API startup)
- `python-telegram-bot>=21.0` - Telegram bot framework
- `python-dateutil>=2.8` - Date/time parsing and utilities
- `python-multipart>=0.0.20` - Multipart form data parsing

**Logging:**
- Python built-in `logging` module configured at startup in `src/api/app.py`

## Configuration

**Environment:**
- `.env` file at project root (NOT auto-loaded by API server; must be sourced with `set -a; source .env; set +a`)
- `.env.example` documents required environment variables:
  - `VAST_API_KEY` - VastAI API authentication
  - `TELEGRAM_BOT_TOKEN` - Telegram bot token
  - `HF_TOKEN` - HuggingFace token for model downloads
  - `GEMINI_API_KEY` - Google Gemini API key (required for VLM scoring)
  - `APIFY_TOKEN` - Apify scraping API token (optional)
  - `TIKTOK_MS_TOKENS` - TikTok authentication tokens (optional)
  - `INSTAGRAM_CUSTOM_*` - Instagram credentials (optional)
  - `YT_DLP_COOKIES_FILE` - yt-dlp cookie file path (optional)

**Application Config Files:**
- `configs/parser.yaml` - Trend parser settings (sources, VLM models, filter params)
  - Supports `${ENV_VAR}` substitution for secrets
  - Models: `gemini-2.5-flash` and `gemini-3.1-flash-image-preview`
  - Sources: `tiktok_custom`, `apify`, `instagram_custom`, `seed`
- `configs/vast.yaml` - GPU instance specifications (L40 GPU, CUDA 12.8, pricing)
- `configs/wan_animate.yaml` - ComfyUI workflow for Wan 2.2 character animation
  - Specifies 14 custom ComfyUI nodes (git repos)
  - Lists 100+ model files (~200GB total) to download to ComfyUI/models/
  - Extra pip: `onnxruntime-gpu` with custom CUDA 12 PyPI index
- `configs/x2v_animate.yaml` - LightX2V video animation config
- `configs/isp_postprocess.yaml` - Video post-processing effects
- `configs/telegram.yaml` - Telegram bot configuration

**Build:**
- `pyproject.toml` - Python package metadata, entry points, dependencies
  - Entry points: `comfy-api`, `vast-agent`, `comfy-bot`, `isp-pipeline`, `comfy-pipeline`
- `frontend/package.json` - Node.js dependencies and scripts
  - `npm run build` → `vite build` → outputs to `frontend-dist/`
  - `npm run dev` → `vite` → dev server at port 5173 with API proxy

**Frontend Build Output:**
- `frontend-dist/` directory served by FastAPI in production (SPA static files)
- `index.html` catch-all for client-side routing (custom `SPAStaticFiles` middleware)

## Platform Requirements

**Development:**
- Linux/macOS with Python 3.10+
- Node.js (v18+ recommended)
- `.venv/` Python virtual environment
- SSH key pair (~/.ssh/id_ed25519 by default for VastAI)
- Playwright browsers for TikTok scraping (auto-installed on first use)

**Production:**
- VastAI rented GPU instances:
  - L40/L40S GPU with 48GB VRAM minimum
  - CUDA 12.8, PyTorch 2.8.0 base image
  - 100+ GB disk space for models
  - EUR geolocation preferred (configurable in `vast.yaml`)
- Telegram-compatible messaging (optional)
- Google Gemini API access (required for VLM)

---

*Stack analysis: 2026-03-27*
