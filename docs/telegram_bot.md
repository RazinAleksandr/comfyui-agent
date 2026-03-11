# Telegram Bot

User-facing conversation interface for video generation. Collects inputs via chat, manages GPU servers, runs workflows, sends results back.

```
src/telegram_bot/
  bot.py             Entry point + handler registration
  conversation.py    ConversationHandler state machine
  config.py          Config loading
  parse_session.py   /parse session dataclasses + disk persistence
  studio_client.py   Async HTTP client for the Parser API
```

Calls `vast-agent` CLI via async subprocess — each layer talks to the next via CLI.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Manual mode — provide image, video, prompt one-by-one |
| `/parse [#hashtags]` | Discover trending videos via Parser API, review & batch generate |
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

The `/parse` command connects to the AI Influencer Studio Parser API to discover and filter trending videos, then lets you batch-generate with a shared reference image.

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
      [sends result 1]
      [sends result 2]
      Batch complete! 2/2 videos generated.
```

### Parser API

The bot calls `POST /api/v1/pipeline/run` on the Studio API (configured via `studio_base_url` in `telegram.yaml`). The pipeline:

1. **Ingests** trending videos from TikTok for the configured influencer
2. **Downloads** the video files
3. **Filters** by hashtags (if provided)
4. **VLM-selects** the best candidates

The API returns a `selected_dir` containing `.mp4` files that the bot presents for review.

Request body:

```json
{
  "influencer_id": "altf4girl",
  "platforms": {
    "tiktok": {
      "source": "tiktok_custom",
      "limit": 20,
      "selector": {"hashtags": ["dance", "trending"]}
    }
  }
}
```

## Session Logging

Every `/parse` session is persisted to disk in `output/parse_sessions/`:

```
output/parse_sessions/
└── 20260311_174413/
    ├── session.json
    ├── reference_image.jpg
    └── results/
        ├── 001_tiktok_20260305_views173500_.../
        │   └── output.mp4
        └── 002_tiktok_20260302_views7872_.../
            └── output.mp4
```

### `session.json` schema

```json
{
  "created_at": "2026-03-11T17:44:13",
  "run_id": 42,
  "influencer_id": "altf4girl",
  "selected_dir": "/path/to/pipeline/selected",
  "reference_image": "reference_image.jpg",
  "workflow": "wan_animate",
  "queue": [
    {
      "index": 1,
      "trend_item_id": 0,
      "video_path": "/abs/path/to/selected/tiktok_....mp4",
      "image_path": "/abs/path/to/reference_image.jpg",
      "caption": "tiktok_20260305_views173500_...",
      "prompt": "girl dancing in the street",
      "status": "completed",
      "output_paths": ["results/001_.../output.mp4"]
    },
    {
      "index": 2,
      "video_path": "/abs/path/to/selected/tiktok_....mp4",
      "image_path": "/abs/path/to/reference_image.jpg",
      "caption": "tiktok_20260302_views7872_...",
      "prompt": "girl on motorcycle",
      "status": "pending",
      "output_paths": []
    }
  ]
}
```

Item status lifecycle: `pending` -> `generating` -> `completed` / `failed`.

### Timeline of saves

- **After sending reference image** — session dir created, reference image copied, empty queue saved
- **After each approval** — new queue item saved with status `pending`
- **Before each generation** — item status set to `generating`
- **After each generation** — item status set to `completed` (with output paths) or `failed`

## Resume (`/resume`)

If the bot crashes or the GPU fails mid-batch, the session is already on disk. `/resume` scans `output/parse_sessions/` for sessions with incomplete items:

- If **one** incomplete session found — resumes it immediately
- If **multiple** found — lists them for the user to choose:
  ```
  Incomplete sessions:
  1. 20260311_174413 — 2/5 pending
  2. 20260311_192030 — 3/3 pending
  Reply with number to resume.
  ```

Items left in `generating` status (from a crash) are treated as `failed` on load and retried.

## Idle Timeout

If no messages for `idle_timeout_minutes` (default: 30), the bot auto-destroys the GPU server to prevent cost overruns.

## Config

`configs/telegram.yaml`:

```yaml
token: ${TELEGRAM_BOT_TOKEN}     # resolved from .env
allowed_users: [123456789]        # Telegram user ID whitelist
default_workflow: wan_animate
idle_timeout_minutes: 30
studio_base_url: "http://localhost:8000"   # Parser API endpoint
studio_influencer_id: "altf4girl"          # influencer for trend discovery
```

Token resolution: `${ENV_VAR}` syntax, `$ENV_VAR`, literal string, or fallback to `TELEGRAM_BOT_TOKEN` env var.

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
