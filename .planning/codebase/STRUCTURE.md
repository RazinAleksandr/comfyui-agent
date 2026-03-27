# STRUCTURE

Directory layout, key file locations, and naming conventions.

---

## Top-Level Layout

```
avatar-factory/
├── src/                    # All Python source code (PYTHONPATH=src)
│   ├── api/                # FastAPI application
│   ├── trend_parser/       # Content ingest + VLM pipeline
│   ├── vast_agent/         # VastAI GPU lifecycle management
│   ├── comfy_pipeline/     # ComfyUI workflow execution (remote GPU)
│   ├── isp_pipeline/       # Video postprocessing
│   ├── x2v_pipeline/       # X2V alternative generation pipeline
│   └── telegram_bot/       # Telegram UI (legacy)
├── frontend/               # React SPA (Vite + TypeScript)
├── frontend-dist/          # Built frontend (committed, served by FastAPI)
├── shared/                 # Runtime data (DB + media files)
│   ├── studio.db           # SQLite database
│   └── influencers/        # Per-influencer content
├── configs/                # YAML configuration files
├── docs/                   # Documentation
├── LightX2V/               # X2V model checkout
├── workflows/              # Legacy ComfyUI workflow files
├── pyproject.toml          # Python package definition + dependencies
├── bootstrap.sh            # Dev environment setup script
└── .env                    # Environment variables (not committed)
```

---

## src/api/ — FastAPI Application

```
src/api/
├── app.py              # FastAPI factory: create_app(), middleware, static files
├── main.py             # ASGI entry point for uvicorn
├── server.py           # Alternate entry point
├── routes/
│   ├── parser.py       # /api/v1/parser/* — pipeline trigger + rerun endpoints
│   ├── influencers.py  # /api/v1/influencers/* — CRUD + generated content
│   ├── generation.py   # /api/v1/generation/* — GPU management + generation
│   ├── jobs.py         # /api/v1/jobs/* — job status lookup
│   ├── events.py       # /api/v1/events/stream — SSE
│   ├── auth.py         # /auth/* — basic auth
│   └── health.py       # /health
├── database.py         # SQLite connection management (WAL mode)
├── db_store.py         # Data access layer (queries, CRUD)
├── job_manager.py      # PersistentJobManager — async job orchestration
├── events.py           # SSE event bus (in-memory pub/sub)
├── deps.py             # FastAPI dependency injection
├── auth.py             # Auth middleware + helpers
├── ref_align.py        # Gemini reference image alignment
├── qa_review.py        # QA review helpers
├── migrate.py          # DB schema migrations
├── migrate_paths.py    # Path migration utilities
├── path_utils.py       # Path helpers
└── schema.sql          # SQLite schema definition
```

---

## src/trend_parser/ — Content Pipeline

```
src/trend_parser/
├── runner.py           # PipelineRunner — orchestrates all stages
├── ingest.py           # Stage 1: collect video metadata
├── downloader.py       # Stage 2: yt-dlp video downloads
├── filter.py           # Stage 3: ffprobe quality filtering
├── vlm.py              # Stage 4: VLM scoring (calls Gemini)
├── gemini.py           # Gemini API client
├── caption.py          # Auto-caption generation
├── persona.py          # Persona context helpers
├── store.py            # FilesystemStore — file operations
├── schemas.py          # Pydantic models for pipeline data
├── config.py           # Config loading from configs/parser.yaml
└── adapters/
    ├── types.py        # Adapter base types
    ├── apify.py        # Apify API adapter (TikTok + Instagram)
    ├── tiktok.py       # TikTok browser adapter (Playwright)
    ├── instagram.py    # Instagram adapter (instaloader)
    └── seed.py         # Seed data adapter (static JSON)
```

---

## src/vast_agent/ — GPU Management

```
src/vast_agent/
├── manager.py          # ServerManager — allocation, lifecycle, health checks
├── vastai.py           # VastClient — VastAI REST API wrapper
├── service.py          # GPU setup + ComfyUI execution over SSH
├── service_mock.py     # Mock service for local testing
├── remote.py           # SSH command execution + file transfer
├── db_registry.py      # DBServerRegistry — server state in SQLite
├── config.py           # Config loading from configs/vast.yaml
├── cli.py              # CLI commands
└── __main__.py         # CLI entry point
```

---

## src/comfy_pipeline/ — ComfyUI Integration

```
src/comfy_pipeline/
├── runner.py           # Workflow execution orchestrator
├── client.py           # ComfyUI HTTP API client (port 8188)
├── workflow.py         # Workflow JSON manipulation
├── config.py           # Config loading from configs/wan_animate.yaml
├── install.py          # ComfyUI installation helpers
└── cli.py              # CLI entry point
```

---

## src/isp_pipeline/ — Postprocessing

```
src/isp_pipeline/
├── processor.py        # Video postprocessing (upscale, refine)
├── config.py           # Config loading
└── cli.py              # CLI entry point
```

---

## src/x2v_pipeline/ — X2V Alternative Pipeline

```
src/x2v_pipeline/
├── remote_runner.py    # Run X2V on remote GPU
├── install.py          # Installation helpers
├── postprocess.py      # Postprocessing
└── config.py           # Config loading from configs/x2v_animate.yaml
```

---

## frontend/ — React SPA

```
frontend/
├── src/
│   └── app/
│       ├── api/
│       │   ├── client.ts       # fetch-based API client
│       │   ├── types.ts        # TypeScript types (mirror backend Pydantic models)
│       │   ├── mappers.ts      # PipelineRun → Task transformation (6 stages)
│       │   ├── hooks.ts        # React hooks (useInfluencer, usePipelineRuns, etc.)
│       │   └── sse.ts          # SSE singleton with auto-reconnect
│       ├── pages/
│       │   ├── AvatarDetailPage.tsx    # Influencer detail + generation controls
│       │   ├── TaskDetailPage.tsx      # Pipeline run detail + review UI
│       │   └── ...
│       └── components/         # Shared UI components
├── index.html
├── vite.config.ts
├── tailwind.config.ts
└── package.json
```

---

## shared/ — Runtime Data

```
shared/
├── studio.db                       # SQLite database
├── seeds/                          # Legacy seed video data
└── influencers/
    └── {influencer_id}/
        ├── profile.json            # Influencer metadata
        ├── reference.png           # Reference image
        └── pipeline_runs/
            └── {timestamp}/
                ├── run_manifest.json           # Stage-by-stage results
                └── {platform}/
                    ├── platform_manifest.json  # Scraped items
                    ├── downloads/              # Raw video files
                    ├── analysis/               # Filter reports
                    ├── filtered/               # Quality-filtered videos
                    ├── vlm/                    # VLM scoring JSON
                    ├── selected/               # VLM-selected videos
                    └── generated/              # Output videos
```

---

## configs/ — Configuration Files

```
configs/
├── parser.yaml         # Trend parser: sources, Apify, TikTok, yt-dlp settings
├── vast.yaml           # VastAI: GPU spec, pricing, SSH, health check
├── wan_animate.yaml    # ComfyUI workflow + character LoRA mappings
└── x2v_animate.yaml    # X2V alternative pipeline config
```

---

## docs/ — Documentation

```
docs/
├── api.md              # API endpoints + async jobs + SSE
├── database.md         # SQLite schema + tables + migrations
├── trend_parser.md     # Pipeline stages + sources + VLM
├── pipeline.md         # ComfyUI workflow + configs
├── vast_agent.md       # GPU management + CLI
├── telegram_bot.md     # Bot commands + conversation flow
├── frontend.md         # React architecture + SSE integration
└── x2v_pipeline.md     # X2V pipeline docs
```

---

## Naming Conventions

| Scope | Convention | Example |
|---|---|---|
| Python modules | snake_case | `job_manager.py`, `db_store.py` |
| Python classes | PascalCase | `ServerManager`, `PersistentJobManager` |
| Python functions | snake_case | `get_or_create_server()` |
| Python private methods | `_snake_case` | `_do_generation()` |
| FastAPI routes | kebab-case paths | `/api/v1/parser/runs/{id}/rerun-vlm` |
| TypeScript files | camelCase | `client.ts`, `mappers.ts` |
| React components | PascalCase | `AvatarDetailPage.tsx` |
| Config YAML keys | snake_case | `health_check_interval`, `max_price` |
| DB tables | snake_case plural | `generation_jobs`, `pipeline_runs` |
| Env vars | UPPER_SNAKE_CASE | `VAST_API_KEY`, `GEMINI_API_KEY` |

---

## Where to Add New Code

| Task | Location |
|---|---|
| New API endpoint | `src/api/routes/` (new or existing file) |
| New pipeline stage | `src/trend_parser/` |
| New ingest source | `src/trend_parser/adapters/` |
| GPU setup changes | `src/vast_agent/service.py` |
| New workflow type | `src/comfy_pipeline/` + `configs/` |
| DB schema change | `src/api/schema.sql` + `src/api/migrate.py` |
| Frontend page | `frontend/src/app/pages/` |
| Frontend API call | `frontend/src/app/api/client.ts` + `types.ts` |
| New config option | Relevant `configs/*.yaml` + config dataclass |
