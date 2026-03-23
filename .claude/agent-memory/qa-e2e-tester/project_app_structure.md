---
name: AI Influencer Studio App Structure
description: Page routes, API endpoints, key selectors, and timing patterns for E2E testing
type: project
---

## Page Routes
- `/` — Home page, influencer card grid + "Create New Avatar" dashed card
- `/avatar/:avatarId` — Avatar detail: profile, 6-stage overview, task list, Start Pipeline button
- `/task/:avatarId/:runId` — Task detail: stage breakdown with videos, review panel, generation panel
- `/login` — Login page (auth required)

## Key CSS Selectors
- Video thumbnail containers: `.flex-shrink-0.w-40.rounded-xl` (clickable, opens video modal)
- Stage status cards (task detail): `[data-slot="card"]` containing `[data-slot="card-title"]` with "Stage N:"
- Stage icon container: `.w-12.h-12.rounded-lg` — bg-green-100=completed, bg-amber-100=lost
- Status badge on task header: `[data-slot="badge"]` — text "completed", "in-progress" etc.
- Generation section buttons: "Start Server", "Generate All" (only when server running), "Retry"
- Re-run buttons: "Retry failed" (download), "Re-filter" (filter), "Re-score" (VLM)
- Review panel: auto-saves drafts, shows rejected videos that can be promoted

## API Endpoints (all under /api/v1/)
- GET /influencers — list all influencers
- GET /influencers/:id — single influencer
- PUT /influencers/:id — upsert
- DELETE /influencers/:id — delete
- POST /influencers/:id/reference-image — upload image
- GET /influencers/:id/generated-content — generated content (completed jobs only)
- GET /parser/runs?influencer_id=&limit= — list pipeline runs
- GET /parser/runs/:runId?influencer_id= — single run (enriched)
- POST /parser/pipeline — start pipeline, returns {job_id}
- POST /parser/runs/:runId/rerun-download — retry failed downloads
- POST /parser/runs/:runId/rerun-filter — re-run candidate filter
- POST /parser/runs/:runId/rerun-vlm — re-run VLM scoring (+ auto-review)
- POST /parser/runs/:runId/review?influencer_id= — submit review (supports draft=true)
- GET /parser/defaults — default sources config
- GET /generation/server/status — server status
- GET /generation/servers — all servers
- POST /generation/server/up — start server
- POST /generation/server/down — stop server
- POST /generation/server/:id/down — stop specific server
- POST /generation/server/:id/auto-shutdown — set auto-shutdown
- GET /generation/server/allocate?influencer_id= — allocation info
- POST /generation/run — start generation job (supports align_reference, align_close_up)
- GET /jobs — list jobs
- GET /jobs/active?type=&influencer_id= — active jobs

## Timing Notes
- Home page: wait 4000ms after domcontentloaded
- Avatar detail: wait 4000ms (loads influencer + runs + active jobs + parser defaults)
- Task detail: wait 6000ms (loads run, raw run, server status, allocation info)
- Video modal: wait 1000ms after click
- Draft auto-save: debounced 1.5s in ReviewPanel

## Key Frontend Features
- Review draft auto-save (1.5s debounce) — changes saved to API automatically
- Rejected video recovery in ReviewPanel — promoted rejected videos persist across re-syncs
- Rerun progress bars: download/filter/vlm stages show current/total with progress bar
- Rerun job persistence: checks for active rerun jobs on mount (survives page reload)
- Close-up alignment toggle in AvatarDetailPage settings + GenerationPanel
- Reference image thumbnail in GenerationPanel (click to preview)
- Re-run buttons show for failed stages too (not just completed)

## Status Rendering
- Stage status uses VISUAL indicators, not text badges in detail view:
  - bg-green-100 icon container + lucide-circle-check svg = completed
  - bg-amber-100 + lucide-circle amber = lost
  - bg-blue-100 + lucide-loader-2 = in-progress
- Overall task badge shows text: "completed", "in-progress", etc.
- "5 of 6 stages completed" progress bar in task header

**Why:** These patterns recur every test run and help avoid false positives when counting "completed" text.
**How to apply:** When checking stage statuses, count SVG icon classes or bg-green-100 containers, not "completed" text occurrences.
