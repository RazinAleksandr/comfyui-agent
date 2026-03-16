---
name: AI Influencer Studio App Structure
description: Page routes, API endpoints, key selectors, and timing patterns for E2E testing
type: project
---

## Page Routes
- `/` — Home page, influencer card grid + "Create New Avatar" dashed card
- `/avatar/:avatarId` — Avatar detail: profile, 6-stage overview, task list, Start Pipeline button
- `/task/:avatarId/:runId` — Task detail: stage breakdown with videos, review panel, generation panel

## Key CSS Selectors
- Video thumbnail containers: `.flex-shrink-0.w-40.rounded-xl` (clickable, opens video modal)
- Stage status cards (task detail): `[data-slot="card"]` containing `[data-slot="card-title"]` with "Stage N:"
- Stage icon container: `.w-12.h-12.rounded-lg` — bg-green-100=completed, bg-amber-100=lost
- Status badge on task header: `[data-slot="badge"]` — text "completed", "in-progress" etc.
- Generation section buttons: "Start Server", "Generate All" (only when server running), "Retry"

## API Endpoints (all under /api/v1/)
- GET /influencers — list all influencers
- GET /influencers/:id — single influencer
- PUT /influencers/:id — upsert
- DELETE /influencers/:id — delete
- POST /influencers/:id/reference-image — upload image
- GET /parser/runs?influencer_id=&limit= — list pipeline runs
- GET /parser/runs/:runId?influencer_id= — single run (enriched)
- POST /parser/pipeline — start pipeline, returns {job_id}
- GET /parser/defaults — default sources config
- GET /generation/server/status — server status
- GET /generation/servers — all servers
- POST /generation/server/up — start server
- POST /generation/server/down — stop server
- POST /generation/server/:id/down — stop specific server
- POST /generation/server/:id/auto-shutdown — set auto-shutdown
- GET /generation/server/allocate?influencer_id= — allocation info
- POST /generation/run — start generation job
- GET /jobs — list jobs
- GET /jobs/active?type=&influencer_id= — active jobs

## Timing Notes
- Home page: wait 4000ms after domcontentloaded
- Avatar detail: wait 4000ms (loads influencer + runs + active jobs + parser defaults)
- Task detail: wait 6000ms (loads run, raw run, server status, allocation info)
- Video modal: wait 1000ms after click

## Known Data
- Influencers: emi2souls (Emi Noir), grannys
- Latest run for emi2souls: 20260316_112159 (full pipeline: 49 ingested, 40 filtered, 16 VLM accepted, 3 approved, 3 generation jobs)
- Second run for emi2souls: 20260316_110859 (12 ingested, 12 downloaded, 12 filtered, 6 VLM, 1 reviewed, 1 gen)
- Generation jobs are "lost" after server restart (normal behavior)
- Server: VastAI instance 32961207, currently offline

## Fix History (2026-03-16)
- SPA routing: /api/v1/influencers/ now returns JSON 404 (was HTML)
- Video modal: title now "Video Preview", filename only in description (was filename as title)
- Review stage: description now "Human review in web UI" (was "Telegram bot")
- Mobile overflow: PARTIALLY fixed — table has overflow-x-auto container, but page still overflows 310px at 375px due to CSS scrollWidth propagation bug

## Status Rendering
- Stage status uses VISUAL indicators, not text badges in detail view:
  - bg-green-100 icon container + lucide-circle-check svg = completed
  - bg-amber-100 + lucide-circle amber = lost
  - bg-blue-100 + lucide-loader-2 = in-progress
- Overall task badge shows text: "completed", "in-progress", etc.
- "5 of 6 stages completed" progress bar in task header

**Why:** These patterns recur every test run and help avoid false positives when counting "completed" text.
**How to apply:** When checking stage statuses, count SVG icon classes or bg-green-100 containers, not "completed" text occurrences.
