# Telegram Bot

User-facing conversation interface for video generation. Collects inputs via chat, manages GPU servers through the backend API, runs workflows, sends results back.

```
src/telegram_bot/
  bot.py             Entry point + handler registration
  conversation.py    ConversationHandler state machine
  config.py          Config loading
  parse_session.py   /parse session dataclasses + disk persistence + cost tracking
  backend_client.py  Async HTTP client for the unified backend API

src/isp_pipeline/
  processor.py       Video postprocessing (grain, sharpness, brightness, vignette)
```

The bot is an HTTP client of the unified backend API. All parser and generation operations go through `BackendClient` → `comfy-api`.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Manual mode — provide image, video, prompt one-by-one |
| `/parse [#hashtags]` | Discover trending videos via backend API, review & batch generate |
| `/resume` | Resume an interrupted batch generation from disk |
| `/skip` | Skip the current video during `/parse` review |
| `/done` | Finish `/parse` review early and start batch generation |
| `/stop` | Shut down GPU server and end session |
| `/cancel` | End conversation without destroying the server |

## Manual Flow (`/start`)

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

### Feedback Loop

After receiving a result:

| Action | What happens |
|--------|-------------|
| Send text | Used as new prompt, re-runs generation |
| Send photo | Replaces reference image, re-runs with same prompt |
| Send video | Replaces reference video, re-runs with same prompt |
| `/stop` | Destroys GPU server, ends session |

GPU server stays running between generations — no model reload overhead.

## Parser Flow (`/parse`)

The `/parse` command calls the backend API to discover and filter trending videos, then lets you batch-generate with a shared reference image.

### How it works

```
You:  /parse #dance #trending
Bot:  Parsing trending videos... This may take a minute.
      Found 5 filtered videos. Send a reference photo to use for all generations.

You:  [photo]
Bot:  Got it. Showing first video...
      [sends video 1]
      [1/5] tiktok_20260305_views173500_...
      Views: 173,500
      Send a prompt to approve, /skip to skip, /done to finish review.

You:  girl dancing in the street
Bot:  Queued! (1 in queue, 4 left). Next video:
      [sends video 2]

You:  /skip
Bot:  [sends video 3]

You:  girl on motorcycle
Bot:  Queued! (2 in queue, 2 left). Next video:

You:  /done
Bot:  Starting batch generation (2/2 videos). Renting GPU...
      Server started. Generating 2 videos...
      [sends result 1 with caption: "[1/2] tiktok_dance... | 28min $0.2095"]
      [sends result 2 with caption: "[2/2] girl_motorcycle... | 32min $0.2394"]
      Batch complete! 2/2 videos generated.
      Total cost: $0.4489
```

### Backend interaction

The bot uses `BackendClient` to call the backend API:

1. `POST /api/v1/parser/pipeline` — starts the full pipeline (ingest → download → filter → VLM)
2. Polls `GET /api/v1/jobs/{job_id}` until complete
3. Receives `selected_dir` with `.mp4` files for review
4. `POST /api/v1/generation/server/up` — starts GPU server
5. `POST /api/v1/generation/run` — runs each approved video
6. `POST /api/v1/generation/server/down` — destroys GPU after batch

## Session Logging

Every `/parse` session is persisted to disk in `shared/parse_sessions/`:

```
shared/parse_sessions/
└── 20260311_174413/
    ├── session.json
    ├── reference_image.jpg
    └── results/
        ├── tiktok/
        │   └── 001_tiktok_20260305_views173500_.../
        │       ├── raw_AnimateDiff_00001-audio.mp4
        │       ├── refined_AnimateDiff_00002-audio.mp4
        │       ├── upscaled_AnimateDiff_00003-audio.mp4
        │       └── postprocessed_AnimateDiff_00003-audio.mp4
        └── instagram/
            └── 002_insta_20260302_views7872_.../
                └── ...
```

### Cost tracking

Each queue item records the vast.ai cost for that generation:

| Field | Description |
|-------|-------------|
| `generation_start` | Unix timestamp when generation began |
| `generation_end` | Unix timestamp when generation finished (including download) |
| `dph_rate` | Vast.ai $/hr rate at time of generation (from `.vast-instance.json`) |
| `cost_usd` | Computed: `dph_rate * (end - start) / 3600` |

### Postprocessing

After each successful generation, the best output video (priority: upscaled > refined > raw) is automatically postprocessed with ISP-style film effects (grain, sharpness, brightness correction, vignette). The postprocessed file is saved as `postprocessed_*.mp4` alongside the raw outputs.

## Resume (`/resume`)

If the bot crashes or the GPU fails mid-batch, the session is already on disk. `/resume` scans `shared/parse_sessions/` for sessions with incomplete items:

- If **one** incomplete session found — resumes it immediately
- If **multiple** found — lists them for the user to choose

Items left in `generating` status (from a crash) are treated as `failed` on load and retried.

## Idle Timeout

If no messages for `idle_timeout_minutes` (default: 30), the bot auto-destroys the GPU server to prevent cost overruns.

## Config

`configs/telegram.yaml`:

```yaml
token: ${TELEGRAM_BOT_TOKEN}       # resolved from .env
allowed_users: [123456789]          # Telegram user ID whitelist
default_workflow: wan_animate
idle_timeout_minutes: 30
backend_url: "http://localhost:8000"       # unified backend API
default_influencer_id: "emi2souls"         # influencer for trend discovery
default_parse_limit: 5                     # max videos to fetch per platform
```

## State Machine

```
IDLE
  |  /start                /parse                  /resume
  v                        v                        v
WAITING_IMAGE         PARSE_WAITING_IMAGE     RESUME_CHOOSING (if multiple)
  |  [photo]               |  [photo]               |  [number]
  v                        v                        |
WAITING_VIDEO         PARSE_REVIEWING              |
  |  [video]               +-- [text] -> queue      |
  v                        +-- /skip  -> next       |
WAITING_PROMPT             +-- /done  -> generate   |
  |  [text]                v                        v
  v                   _run_batch_generation --------+
WAITING_FEEDBACK
  +-- [text]   -> re-run with new prompt
  +-- [photo]  -> re-run with new image
  +-- [video]  -> re-run with new video
  +-- /stop    -> destroy server -> IDLE
  +-- /cancel  -> keep server -> IDLE
```
