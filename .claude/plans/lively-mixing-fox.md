# Plan: Connect Frontend with Backend for Production — COMPLETED

## Context

The AI Influencer Studio has a working FastAPI backend (port 8000) with REST APIs for influencer CRUD, trend parsing pipeline, video generation, and job polling. A React/Vite frontend was exported from Figma with 3 pages (Home, AvatarDetail, TaskDetail) but uses **mock data only** — no API calls exist. The goal is to connect them into a production-ready service.

**Key gaps:**
- Frontend has no API client — all data is hardcoded mock
- Backend doesn't serve images/videos via HTTP (only filesystem paths)
- Frontend `Task` type doesn't map directly to backend pipeline run manifests
- No static file serving for production SPA delivery

---

## Phase 1: Backend Changes (2 files)

### 1.1 Add static file serving + SPA catch-all
**File: `src/api/app.py`**

- Import `StaticFiles` from `starlette.staticfiles`
- Mount `StaticFiles(directory=data_dir)` at `/files` — makes `shared/influencers/emi2souls/reference.png` available at `/files/influencers/emi2souls/reference.png`
- Mount `StaticFiles(directory=frontend_dist, html=True)` at `/` if `frontend-dist/` exists (production SPA serving, must be last mount)

### 1.2 Add `profile_image_url` to influencer response
**File: `src/api/routes/influencers.py`**

- Add `profile_image_url: str | None = None` to `InfluencerOut`
- In `_to_out()`: if `reference_image_path` is set, compute `profile_image_url = f"/files/{reference_image_path}"`
- Non-breaking addition — existing fields stay

---

## Phase 2: Vite Dev Proxy
**File: `frontend/vite.config.ts`**

Add proxy + build output:
```typescript
server: {
  proxy: {
    '/api': { target: 'http://localhost:8000', changeOrigin: true },
    '/files': { target: 'http://localhost:8000', changeOrigin: true },
  },
},
build: { outDir: '../frontend-dist' },
```

---

## Phase 3: Frontend API Layer (4 new files)

### 3.1 API client
**New: `frontend/src/app/api/client.ts`**

Fetch-based client mirroring the Python `BackendClient` (`src/telegram_bot/backend_client.py`). Methods:
- `listInfluencers()`, `getInfluencer(id)`, `upsertInfluencer(id, body)`, `uploadReferenceImage(id, file)`
- `startPipeline(body)`, `listRuns(influencerId)`, `getRun(influencerId, runId)`
- `getJob(jobId)`, `listJobs()`
- `serverStatus()`, `serverUp()`, `serverDown()`, `startGeneration(body)`

### 3.2 API response types
**New: `frontend/src/app/api/types.ts`**

Types matching backend Pydantic models: `InfluencerOut`, `JobInfo`, `PipelineRun`, `PipelinePlatformRun`, `PipelineRunRequest`, `ServerStatus`, `GenerationRequest`

### 3.3 Data mapper
**New: `frontend/src/app/api/mappers.ts`**

Converts backend `PipelineRun` → frontend `Task` with 6 stages:
- `run_id` → `Task.id`
- `ingested_items` → `trend_ingestion` stage
- `download_counts` → `download` stage
- `candidate_report_path` presence → `candidate_filter` stage
- `vlm_summary_path` / `accepted`/`rejected` → `vlm_scoring` stage
- `review` / `generation` — pending by default (separate flows)
- `fsPathToUrl(path)` helper: strips prefix up to `shared/` and prepends `/files/`

### 3.4 React hooks
**New: `frontend/src/app/api/hooks.ts`**

- `useInfluencers()` — fetches and returns `{ data, loading, error, refetch }`
- `useInfluencer(id)` — single influencer
- `usePipelineRuns(influencerId)` — fetches runs, maps via mapper to `Task[]`
- `usePipelineRun(influencerId, runId)` — single run → `Task`
- `useJobPoller(jobId, intervalMs=3000)` — polls until terminal state, returns `{ job, loading, isComplete, error }`

---

## Phase 4: Page Updates (3 existing files + routes)

### 4.1 Update routes
**File: `frontend/src/app/routes.ts`**

Change `/task/:taskId` to `/task/:avatarId/:runId` (need both IDs for API call)

### 4.2 HomePage
**File: `frontend/src/app/pages/HomePage.tsx`**

- Replace `mockInfluencers` import with `useInfluencers()` hook
- Use `profile_image_url` instead of `profile_image`
- Add loading skeleton
- Add "Create Influencer" button → opens `Dialog` with form (id, name, description, hashtags, reference image upload). Submit calls `api.upsertInfluencer()` + `api.uploadReferenceImage()`

### 4.3 AvatarDetailPage
**File: `frontend/src/app/pages/AvatarDetailPage.tsx`**

- Replace mock lookups with `useInfluencer(avatarId)` + `usePipelineRuns(avatarId)`
- Use `profile_image_url` for images
- Add "Start Pipeline" button → Dialog with: platform checkboxes, hashtags (pre-filled), limit, source per platform
- On submit: call `api.startPipeline()`, get `job_id`, show toast via `sonner`, poll with `useJobPoller`, refetch runs on completion
- Update task links to `/task/${influencerId}/${runId}`

### 4.4 TaskDetailPage
**File: `frontend/src/app/pages/TaskDetailPage.tsx`**

- Read `avatarId` + `runId` from params
- Replace mocks with `useInfluencer(avatarId)` + `usePipelineRun(avatarId, runId)`
- Video thumbnails: use `<video preload="metadata">` element with `/files/...` URL (backend serves mp4s)
- Review stage shows "Awaiting Telegram review" if pending
- Generation stage shows "Not yet started" with start button if applicable

---

## Phase 5: Type Cleanup
**File: `frontend/src/app/data/mockData.ts`**

- Move type definitions (`Influencer`, `Task`, `StageResult`, `VideoPreview`) to `frontend/src/app/api/types.ts` (as presentation types alongside API types)
- Keep mock data arrays as development fallback (optional)

---

## File Summary

| File | Action |
|------|--------|
| `src/api/app.py` | Modify — add `/files` mount + SPA catch-all |
| `src/api/routes/influencers.py` | Modify — add `profile_image_url` |
| `frontend/vite.config.ts` | Modify — add dev proxy + build output |
| `frontend/src/app/api/client.ts` | **Create** — fetch-based API client |
| `frontend/src/app/api/types.ts` | **Create** — API + presentation types |
| `frontend/src/app/api/mappers.ts` | **Create** — PipelineRun → Task mapper |
| `frontend/src/app/api/hooks.ts` | **Create** — data fetching + polling hooks |
| `frontend/src/app/routes.ts` | Modify — update task route params |
| `frontend/src/app/pages/HomePage.tsx` | Modify — real data + create dialog |
| `frontend/src/app/pages/AvatarDetailPage.tsx` | Modify — real data + pipeline trigger |
| `frontend/src/app/pages/TaskDetailPage.tsx` | Modify — real pipeline run data |
| `frontend/src/app/data/mockData.ts` | Modify — extract types |

## Implementation Order

1. Backend changes (Phase 1) — testable with curl independently
2. Vite proxy (Phase 2) — enables frontend→backend in dev
3. API layer (Phase 3) — client, types, mapper, hooks
4. Pages (Phase 4) — HomePage first (simplest), then AvatarDetail, then TaskDetail
5. Type cleanup (Phase 5) + production build config

## Known Limitations (Addressed Later)

- **Video thumbnails**: No server-side frame extraction. Using `<video preload="metadata">` for now.
- **Review stage gap**: Telegram review data isn't in pipeline manifest. Shows as pending/auto from VLM.
- **Generation linkage**: Generation jobs aren't linked to pipeline runs in the data model. Manual action button.
- **No auth**: CORS allows all origins. Auth is out of scope for this integration.

## Verification

1. `cd frontend && pnpm dev` + `comfy-api --port 8000` — verify proxy works, pages load real data
2. Create an influencer via the HomePage dialog — verify it appears in the list
3. Navigate to AvatarDetailPage — verify profile loads from API
4. Start a pipeline — verify job polling shows progress, run appears in task list
5. Navigate to TaskDetailPage — verify stage data renders from pipeline run manifest
6. `cd frontend && pnpm build` → `comfy-api` — verify SPA serves from FastAPI at `/`
7. Verify `/files/influencers/{id}/reference.png` serves the actual image
