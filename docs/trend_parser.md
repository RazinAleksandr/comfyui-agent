# Trend Parser

Discovers, downloads, filters, and scores trending videos for AI subject substitution. Filesystem-only, no database.

```
src/trend_parser/
  runner.py           PipelineRunnerService — orchestrates the full pipeline
  config.py           ParserConfig from YAML with ${ENV_VAR} interpolation
  ingest.py           TrendIngestService — collect videos, extract signals, score/rank
  downloader.py       TrendDownloadService — yt-dlp wrapper with rename/hash
  filter.py           Candidate filter — ffprobe/ffmpeg quality analysis + scoring
  vlm.py              VLM selector — Gemini video evaluation + accept/reject
  gemini.py           Low-level Gemini API client
  persona.py          PersonaProfile dataclass (JSON I/O, prompt generation)
  store.py            FilesystemStore — influencer profiles, pipeline runs
  schemas.py          Pydantic models for pipeline request/response

  adapters/
    types.py           RawTrendVideo, TrendFetchSelector
    seed.py            SeedTrendAdapter — local JSON files
    apify.py           ApifyTrendAdapter — Apify scraping API
    tiktok.py          TikTokCustomAdapter — browser-based via TikTokApi
    instagram.py       InstagramCustomAdapter — instaloader
```

## Pipeline Stages

```
Ingest → Download → Filter → VLM Select
```

Each stage can be individually enabled/disabled in the pipeline request.

### 1. Ingest

Collects raw video metadata from a configured source. No files downloaded yet.

**Sources:**

| Source | Platform | How it works |
|--------|----------|-------------|
| `seed` | tiktok, instagram | Reads from local JSON files in `shared/seeds/` |
| `apify` | tiktok, instagram | Calls Apify actor API (paid, most reliable) |
| `tiktok_custom` | tiktok only | Browser scraping via TikTokApi + Playwright |
| `instagram_custom` | instagram only | Instaloader with session auth |

Videos are scored by reach (views, likes, comments, shares), engagement rate, and recency, then ranked. Selector filtering (hashtags, search terms, min views) is applied before ranking.

**Signal extraction** (lightweight, no download needed): extracts trending hashtags, audio tracks, topics, content styles, and hooks from collected video metadata.

### 2. Download

Downloads video files via yt-dlp. Files are renamed to a normalized format:

```
{platform}_{date}_views{count}_uid{source_id}.mp4
```

Each download record includes: local path, file size, SHA256 hash, status (downloaded/failed/skipped).

### 3. Filter

Analyzes downloaded videos using ffprobe and ffmpeg (multi-threaded):

- **Resolution, bitrate, FPS, duration** — via ffprobe
- **Motion** — VMAF motion average
- **Scene cuts** — scene change detection
- **Blur** — blur detection mean

Produces a composite score from four components:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Quality | 35% | Resolution, bitrate, FPS, blur |
| Temporal stability | 25% | Scene cut frequency, motion level |
| Swap compatibility | 20% | Duration sweet spot (~12s), vertical orientation |
| Virality | 20% | Views + engagement (log-scaled) |

Hard rejects: too short (<4s), low resolution (<480px), excessive scene cuts (>24/min), excessive motion, excessive blur.

Top-K candidates are copied to the `filtered/` directory.

### 4. VLM Select

Sends each filtered video to Gemini for AI evaluation of subject substitution suitability.

**Scoring rubric (0-10):**

| Metric | Description |
|--------|-------------|
| theme_match | Content fits the influencer's channel theme |
| persona_fit | Visual style matches the target persona |
| single_subject_clarity | One dominant person, clearly visible |
| face_visibility | Main subject's face visible and trackable |
| motion_stability | Camera/subject motion is manageable |
| occlusion_risk | How much the subject is obscured (higher = worse) |
| scene_cut_complexity | Cut frequency impact on replacement (higher = worse) |
| substitution_readiness | Overall suitability for face/person swap |

**Auto-decision thresholds** (configurable):

| Threshold | Default | Rejects if |
|-----------|---------|-----------|
| min_readiness | 7.0 | substitution_readiness < threshold |
| min_confidence | 0.70 | model confidence < threshold |
| min_persona_fit | 6.5 | persona_fit < threshold |
| max_occlusion_risk | 6.0 | occlusion_risk > threshold |
| max_scene_cut_complexity | 6.0 | scene_cut_complexity > threshold |

Accepted videos are copied to `selected/`, rejected to `rejected/`.

## Pipeline Output

Each run creates a timestamped directory under the influencer's profile:

```
shared/influencers/{influencer_id}/pipeline_runs/{timestamp}/
├── run_manifest.json                    # Full run metadata + request
└── {platform}/
    ├── platform_manifest.json           # Per-platform results
    ├── downloads/                       # Raw downloaded videos
    ├── analysis/
    │   └── candidate_filter_report_*.json   # Filter scores + details
    ├── filtered/                        # Top-K candidates
    ├── vlm/
    │   ├── {video_stem}.json            # Per-video VLM evaluation
    │   └── vlm_summary_*.json           # Accept/reject summary
    ├── selected/                        # Final accepted videos
    └── rejected/                        # Rejected by VLM
```

## API Endpoints

The parser is exposed through the unified backend API (see [api.md](api.md)):

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/parser/run` | Ingest-only job (returns job_id) |
| `POST` | `/api/v1/parser/pipeline` | Full pipeline job (returns job_id) |
| `POST` | `/api/v1/parser/signals` | Lightweight signal extraction (synchronous) |
| `GET` | `/api/v1/parser/runs` | List pipeline runs for influencer |
| `GET` | `/api/v1/parser/runs/{run_id}` | Get specific run details |

### Pipeline request body

```json
{
  "influencer_id": "emi2souls",
  "platforms": {
    "tiktok": {
      "enabled": true,
      "source": "seed",
      "limit": 20,
      "selector": {
        "hashtags": ["dance", "trending"],
        "min_views": 10000
      }
    }
  },
  "download": {
    "enabled": true,
    "force": false
  },
  "filter": {
    "enabled": true,
    "probe_seconds": 8,
    "top_k": 15,
    "workers": 4
  },
  "vlm": {
    "enabled": true,
    "model": "gemini-3.1-flash-lite-preview",
    "max_videos": 15,
    "theme": "influencer channel",
    "thresholds": {
      "min_readiness": 7.0,
      "min_confidence": 0.70,
      "min_persona_fit": 6.5,
      "max_occlusion_risk": 6.0,
      "max_scene_cut_complexity": 6.0
    }
  }
}
```

### Pipeline response

```json
{
  "influencer_id": "emi2souls",
  "started_at": "2026-03-13T14:30:00+00:00",
  "base_dir": "/path/to/shared/influencers/emi2souls/pipeline_runs/20260313_143000",
  "platforms": [
    {
      "platform": "tiktok",
      "source": "seed",
      "ingested_items": 20,
      "download_counts": {"downloaded": 18, "failed": 2},
      "candidate_report_path": "/path/to/analysis/candidate_filter_report_*.json",
      "filtered_dir": "/path/to/filtered",
      "vlm_summary_path": "/path/to/vlm/vlm_summary_*.json",
      "selected_dir": "/path/to/selected",
      "accepted": 5,
      "rejected": 13
    }
  ]
}
```

## Config

`configs/parser.yaml`:

```yaml
default_source: seed                     # seed | apify | tiktok_custom | instagram_custom

# Apify (paid scraping API)
apify_token: ${APIFY_TOKEN}
tiktok_apify_actor: ${TIKTOK_APIFY_ACTOR}
instagram_apify_actor: ${INSTAGRAM_APIFY_ACTOR}
apify_overfetch_multiplier: 1
apify_cost_optimized: true
apify_fallback_to_seed: false

# TikTok custom (browser-based, requires playwright)
tiktok_query: "viral videos"
tiktok_ms_tokens: ${TIKTOK_MS_TOKENS}
tiktok_custom_headless: true
tiktok_custom_browser: chromium

# Instagram custom (instaloader-based)
instagram_query: "reels trends"
instagram_custom_username: ${INSTAGRAM_CUSTOM_USERNAME}
instagram_custom_password: ${INSTAGRAM_CUSTOM_PASSWORD}

# yt-dlp downloader
yt_dlp_command: yt-dlp
yt_dlp_format: "bv*+ba/b"
yt_dlp_cookies_file: ${YT_DLP_COOKIES_FILE}
download_timeout_sec: 900

# Gemini VLM scoring
gemini_api_key: ${GEMINI_API_KEY}

# Directories (leave empty for defaults: shared/)
workspace_data_dir: ""
seed_data_dir: ""
```

## Data Storage

All data lives under `shared/` (filesystem-only, no database):

```
shared/
├── influencers/
│   └── {influencer_id}/
│       ├── profile.json           # Name, description, hashtags, reference image
│       ├── reference.jpg          # Reference image for generation
│       ├── persona.json           # Optional: detailed persona profile for VLM
│       └── pipeline_runs/         # Timestamped run outputs
├── downloads/                     # Shared download cache
└── seeds/
    ├── tiktok_videos.json         # Seed data for development
    └── instagram_videos.json
```

Influencer profiles are managed via the `/api/v1/influencers/*` endpoints.
