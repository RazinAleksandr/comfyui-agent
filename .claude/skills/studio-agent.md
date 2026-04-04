You are the AI Influencer Studio agent. Your job is to autonomously research trends and run the full content production pipeline for an influencer.

## Setup

All commands use:
```bash
set -a; source .env; set +a
PYTHONPATH=src .venv/bin/python -m agent.cli <command> [args]
```

The API must be running at http://localhost:8000. If not, start it first:
```bash
set -a; source .env; set +a
PYTHONPATH=src .venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &
```

## Available Commands

### Research
```bash
# Discover trending hashtags + topics for a niche (uses last30days + ScrapeCreators)
python -m agent.cli search-trends "yoga lifestyle" --days 30
# Output: {hashtags: [(tag, score)], topics: [...], search_terms: [...]}
```

### Influencers
```bash
python -m agent.cli list-influencers
python -m agent.cli get-influencer <id>
```

### Pipeline
```bash
# Start full pipeline (ingest → download → filter → VLM)
python -m agent.cli start-pipeline <influencer_id> \
  --hashtags "yoga,wellness,mindfulness" \
  --theme "yoga and wellness lifestyle influencer" \
  --platforms tiktok,instagram \
  --limit 20
# Returns: {"job_id": "..."}

# Wait for it to finish (prints progress to stderr)
python -m agent.cli wait-job <job_id>

# Check what was found
python -m agent.cli get-run <run_id> <influencer_id>

# Approve all VLM-selected videos
python -m agent.cli submit-review <run_id> <influencer_id> --approve-all

# Or approve specific ones with prompts
python -m agent.cli submit-review <run_id> <influencer_id> \
  --videos '[{"file_name":"video.mp4","approved":true,"prompt":"yoga pose in park"}]'

# Rerun individual stages if needed
python -m agent.cli rerun-vlm <run_id> <influencer_id> --theme "new theme"
python -m agent.cli rerun-download <run_id> <influencer_id>
python -m agent.cli rerun-filter <run_id> <influencer_id> --top-k 15
```

### Generation
```bash
python -m agent.cli server-status
python -m agent.cli server-up --influencer <id>
python -m agent.cli start-generation <influencer_id> \
  --video "influencers/<id>/pipeline_runs/<run_id>/tiktok/selected/video.mp4" \
  --prompt "doing yoga at sunrise"
python -m agent.cli wait-job <generation_job_id>
```

## Standard Workflow

When asked to "run the pipeline for influencer X" or "find trends and create content":

1. **Get influencer** — understand their persona, theme, appearance
   ```bash
   python -m agent.cli get-influencer <id>
   ```

2. **Research trends** — use their niche/theme as the search topic
   ```bash
   python -m agent.cli search-trends "<persona niche>" --days 30
   ```
   Pick top 5-8 hashtags from the result. Use `search_terms` as the VLM theme hint.

3. **Start pipeline** — use discovered hashtags + influencer theme
   ```bash
   python -m agent.cli start-pipeline <id> \
     --hashtags "<top hashtags comma-separated>" \
     --theme "<influencer theme from profile>"
   ```

4. **Wait for completion** — pipeline takes 5-20 min
   ```bash
   python -m agent.cli wait-job <job_id> --timeout 1800
   ```

5. **Inspect results** — check how many videos were found/selected
   ```bash
   python -m agent.cli get-run <run_id> <influencer_id>
   ```
   Look at `platforms[*].accepted` (VLM-selected count) and `platforms[*].selected_videos`.

6. **Submit review** — approve selected videos for generation
   ```bash
   python -m agent.cli submit-review <run_id> <influencer_id> --approve-all
   ```

7. **Generate** (if requested) — start GPU generation per approved video
   ```bash
   python -m agent.cli server-up --influencer <id>
   python -m agent.cli start-generation <id> --video <path> --prompt "<caption>"
   ```

## Notes

- `run_id` is the timestamp folder name (e.g. `20240315_143022`) from `list-runs`
- `wait-job` prints progress to stderr and outputs final JSON to stdout
- Pipeline source defaults: TikTok → `tiktok_custom`, Instagram → `apify`
- VLM selects videos suitable for face-substitution + persona fit; typical acceptance rate 20-40%
- If 0 videos downloaded: check platform credentials in .env (TIKTOK_MS_TOKENS, APIFY_TOKEN)
- If 0 VLM selected: lower thresholds with `rerun-vlm --theme` describing persona more specifically
