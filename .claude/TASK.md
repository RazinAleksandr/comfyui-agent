# ComfyUI Agent — Full Architecture

## Overview

Unified backend for AI influencer video generation:

```
Clients (Telegram Bot, Vue Frontend) → FastAPI API → Internal Modules
                                                      ├── trend_parser (VPS)
                                                      ├── vast_agent (VPS)
                                                      ├── isp_pipeline (VPS)
                                                      └── comfy_pipeline (GPU)
```

---

## Module 1: ComfyUI Pipeline ✅ DONE

**Location:** `src/comfy_pipeline/`
**CLI:** `comfy-pipeline`

Config-driven CLI that runs on the GPU server:
- Sets up ComfyUI (clone repo, install custom nodes, download models)
- Manages server lifecycle (start/stop/status with PID tracking)
- Runs workflows programmatically (upload inputs, execute, download results)
- Per-character LoRA selection via `characters` config in workflow YAML

---

## Module 2: VastAI Agent ✅ DONE

**Location:** `src/vast_agent/`
**CLI:** `vast-agent`
**Config:** `configs/vast.yaml`

GPU server lifecycle management on VastAI:
- Search offers, rent, push code, bootstrap, run pipeline, pull results, destroy
- `VastAgentService` programmatic API used by generation routes

---

## Module 3: Telegram Bot ✅ DONE

**Location:** `src/telegram_bot/`
**CLI:** `comfy-bot`
**Config:** `configs/telegram.yaml`

Telegram UI with conversation state machine:
- `/start` — manual mode (image → video → prompt → generate)
- `/parse` — batch trending video discovery and generation
- `/resume` — crash recovery for incomplete sessions
- Uses `BackendClient` for all API communication

---

## Module 4: ISP Pipeline ✅ DONE

**Location:** `src/isp_pipeline/`
**CLI:** `isp-pipeline`

Video post-processing (grain, sharpness, brightness, vignette).

---

## Module 5: Trend Parser ✅ DONE

**Location:** `src/trend_parser/`
**Config:** `configs/parser.yaml`

Video trend parsing pipeline (filesystem-only, no DB):
- Adapters: TikTok (custom + Apify), Instagram (instaloader + Apify), seed
- Ingest → Download (yt-dlp) → Filter (ffprobe) → VLM (Gemini) → Selected videos
- Influencer profile management via `shared/influencers/`
- Migrated from AI_Influencer_studio with all DB dependencies removed

---

## Module 6: API Server ✅ DONE

**Location:** `src/api/`
**CLI:** `comfy-api`
**Port:** 8000

FastAPI application exposing all business logic:
- `/api/v1/parser/*` — trend parsing pipeline (async jobs)
- `/api/v1/influencers/*` — influencer CRUD + reference image upload
- `/api/v1/generation/*` — GPU server control + workflow execution (async jobs)
- `/api/v1/jobs/*` — async job polling
- In-memory `JobManager` for long-running operations

---

## Deployment

```
VPS ($5/mo, always-on):
  systemd "comfy-api":  comfy-api --host 0.0.0.0 --port 8000
  systemd "comfy-bot":  comfy-bot

VastAI GPU (on-demand, $/hr):
  comfy-pipeline (called by vast-agent via SSH)
  Only core deps installed
```

---

## Integration Status

- ✅ Phase 1: Core integration (trend_parser + API + bot via BackendClient)
- ✅ Phase 2: Generation API (VastAgentService + generation routes + bot wired)
- ⏭️ Phase 3: Skipped (X pipeline + image gen — unused features)
