# Frontend

React SPA for managing AI influencer content generation. Connects to the FastAPI backend via REST API + SSE for real-time updates.

```
frontend/
  src/
    app/
      api/
        client.ts      Fetch-based API client (all REST endpoints)
        types.ts       TypeScript types (API + presentation)
        hooks.ts       React hooks (data fetching, SSE-based job tracking)
        mappers.ts     PipelineRun → Task stage mapping
        sse.ts         SSE connection singleton with auto-reconnect
      pages/
        HomePage.tsx           Influencer grid + create dialog
        AvatarDetailPage.tsx   Profile + pipeline stages + task list
        TaskDetailPage.tsx     6-stage detail view + review + generation
      components/
        ui/            shadcn/ui components (40+)
        figma/         Custom components (ImageWithFallback)
      data/
        mockData.ts    Development fallback data
      routes.ts        React Router config
      App.tsx          Root component + ConnectionBanner
    styles/            Tailwind CSS + theme
  vite.config.ts       Vite config with proxy + build output
  package.json         Dependencies
```

## Stack

- **React 18** + **TypeScript** — UI framework
- **Vite 6** — build tool + dev server
- **Tailwind CSS 4** — styling
- **shadcn/ui (Radix)** — component library (40+ components)
- **React Router 7** — client-side routing
- **Lucide React** — icons
- **Sonner** — toast notifications

## Pages

### HomePage (`/`)

- Grid of influencer cards from `GET /api/v1/influencers`
- Profile images served from `/files/influencers/{id}/reference.{ext}`
- "Create New Avatar" card opens a dialog with: ID, name, description, hashtags, video requirements, reference image upload
- Creates via `PUT /api/v1/influencers/{id}` + `POST /api/v1/influencers/{id}/reference-image`

### AvatarDetailPage (`/avatar/:avatarId`)

- Influencer profile with hashtags and video selection requirements
- 6-stage pipeline overview cards
- Task list from `GET /api/v1/parser/runs?influencer_id=...`
- "Start Pipeline" dialog: platform selection (TikTok/Instagram), hashtags, limit, source
- Pipeline job polling with status banner (running/completed/failed)
- Active pipeline jobs restored on page refresh via `GET /api/v1/jobs/active?type=pipeline&influencer_id=...`

### TaskDetailPage (`/task/:avatarId/:runId`)

6-stage pipeline detail view with custom renderers per stage:

**Stage 1: Trend Ingestion** — table with video URL, caption, views, likes, hashtag badges, source links

**Stage 2: Download** — status badges (downloaded/failed) + video preview thumbnails from `.mp4` files via `<video preload="metadata">`

**Stage 3: Candidate Filter** — summary badges (analyzed/passed/rejected) + scored table with resolution, duration, quality/stability/final score progress bars

**Stage 4: VLM Scoring** — accepted/rejected badges, Gemini model info, per-video cards with readiness/persona_fit/confidence scores and AI reasoning points

**Stage 5: Review** — interactive review panel:
- Each VLM-accepted video with approve/skip toggle
- VLM scores and reasoning displayed
- Prompt input per approved video
- "Submit Review" button → `POST /api/v1/parser/runs/{id}/review`
- After submission, shows review summary

**Stage 6: Generation** — GPU server management + generation controls:
- Server allocation info (own server, borrow, create new)
- Server status with cost display
- "Start Server" / "Shut Down Server" buttons
- Auto-shutdown checkbox
- Per-video "Generate" buttons with real-time progress (node execution, sampling steps)
- "Generate All" for batch generation
- "Retry" button for failed jobs
- Job status persisted in SQLite DB (`generation_jobs` table) — survives page refresh and server restart

## API Integration

### Data Flow

```
Browser → Vite Dev Server (port 5173) → proxy /api, /files → FastAPI (port 8000)
Browser → FastAPI (port 8000) → /api/* routes, /files/* static, /* SPA
```

### API Client (`api/client.ts`)

Fetch-based client with methods for all endpoints:

```typescript
api.listInfluencers()
api.getInfluencer(id)
api.upsertInfluencer(id, body)
api.deleteInfluencer(id)
api.uploadReferenceImage(id, file)
api.getParserDefaults()
api.startPipeline(body)
api.listRuns(influencerId)
api.getRun(influencerId, runId)
api.submitReview(influencerId, runId, videos)
api.getJob(jobId)
api.listJobs(limit?)
api.activeJobs(type?, influencerId?)
api.serverStatus(influencerId?)
api.serverUp(workflow, influencerId?)
api.serverDown()
api.shutdownServer(serverId)
api.setAutoShutdown(serverId, enabled)
api.getAllocationInfo(influencerId)
api.startGeneration(body)
api.listServers()
api.getGenerationJobs(runId)
```

### SSE Client (`api/sse.ts`)

Singleton EventSource connection to `/api/v1/events/stream` with:
- Auto-reconnect with exponential backoff (1s to 30s)
- Typed event subscriptions: `job_progress`, `job_state`, `server_change`
- Connection lifecycle events: `__open__`, `__error__`
- Auto-disconnect when no listeners remain

```typescript
import { subscribe } from "./sse";

const unsub = subscribe("job_progress", (data) => {
  // Handle real-time progress update
});
// Call unsub() to unsubscribe
```

### Data Mapper (`api/mappers.ts`)

Converts backend `PipelineRun` manifests (enriched with video lists, reports, review data, generation jobs) into the frontend `Task` type with 6 stages. Each stage gets:
- `status`: completed / in-progress / pending / failed
- `items_count`: number of items processed
- `details`: structured data (with `_type` tag for custom rendering)
- `videos`: preview data for video cards

### Hooks (`api/hooks.ts`)

- `useInfluencers()` / `useInfluencer(id)` — data fetching with loading/error
- `usePipelineRuns(influencerId)` — fetches + maps to Task[]
- `usePipelineRun(influencerId, runId)` — single run + mapping
- `useRawPipelineRun(influencerId, runId)` — raw PipelineRun for review/generation data
- `useJobSSE(jobId)` — SSE-based real-time job tracking (initial REST fetch + live progress/state via SSE, no polling)
- `useConnectionStatus()` — returns whether the SSE connection to the backend is alive (powers the `ConnectionBanner` in `App.tsx`)

## Build

```bash
cd frontend
npm install           # install dependencies
npm run dev           # dev server at localhost:5173
npm run build         # production build to ../frontend-dist/
```

The production build is served by FastAPI's `SPAStaticFiles` mount with SPA catch-all routing.

## Configuration

`vite.config.ts`:
- `@` alias → `./src`
- Dev proxy: `/api` and `/files` → `http://localhost:8000`
- Build output: `../frontend-dist/`
