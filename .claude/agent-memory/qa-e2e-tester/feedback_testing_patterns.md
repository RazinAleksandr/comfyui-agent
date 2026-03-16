---
name: QA Testing Patterns and Gotchas
description: Lessons learned from testing AI Influencer Studio to avoid false positives and missed bugs
type: feedback
---

## Stage Status Check — Use Icon Classes Not Text

Do NOT count text occurrences of "completed" to verify stage statuses on task detail page. The stage cards use color-coded icon backgrounds (bg-green-100) and SVG icons (lucide-circle-check text-green-600), not text badges.

**Why:** Initial test counted only 2 "completed" text occurrences and filed a false Major bug. The stages were actually rendering correctly via icons.
**How to apply:** Query `[class*="bg-green-100"]` containers or `svg[class*="lucide-circle-check"]` to verify stage completion status.

## Video Elements vs Images

The task detail page uses `<video>` elements (not `<img>`) for ALL thumbnails in every stage. The video elements use `autoplay=false controls=false` and only get `controls=true` when shown in the modal. Use `document.querySelectorAll('video')` to find thumbnails.

**Why:** The test was looking for `img[src*="/files/"]` but all thumbnails are video elements.

## API /api/v1/influencers/ (Trailing Slash) — Now Returns JSON 404 (FIXED)

As of 2026-03-16, the SPA routing fix means `/api/v1/influencers/` (trailing slash) returns `{"detail":"Not Found"}` with status 404 and `Content-Type: application/json`. Previously returned HTML. This is now correct behavior — test that it returns JSON, not HTML.

## Generation Panel Only Shows When Server is Running

The "Generate All", individual "Generate", and "Retry" buttons for generation jobs only appear when `serverState === "running"`. When the server is offline, only "Start Server" is shown. This is intentional — do not report as a bug.

## Console Errors from 404 API calls

404 console errors appear when navigating to nonexistent influencer/task routes — this is expected behavior (API returns 404, which the frontend catches and shows a graceful error state). These are NOT bugs.

## Mobile Overflow on Task Detail — PARTIALLY FIXED (2026-03-16)

The table in Stage 1 now has `overflow-x-auto` wrapper (`bg-slate-50 rounded-lg overflow-x-auto`). However, page-level horizontal scroll STILL occurs at 375px (`body.scrollWidth=685px`, user can scroll 310px horizontally). Root cause: `overflow-x: auto` clips rendering but does NOT prevent `scrollWidth` propagation to ancestor elements. The container `div.container.mx-auto.px-4.py-8.max-w-6xl` has `scrollWidth=685px` because CSS `overflow-x: auto` propagates scrollWidth to parents (unlike `overflow-x: clip` which prevents this). Fix requires either `overflow-x: hidden` on the page wrapper or `overflow-x: clip` on the scroll containers.

To correctly detect this bug: measure `document.documentElement.scrollWidth > window.innerWidth` AND `window.scrollX + window.innerWidth < document.documentElement.scrollWidth` (maxScrollX > 0). Do NOT rely only on whether overflow containers exist.

## Video Modal Title — FIXED (2026-03-16)

VideoPlayerModal now shows `"Video Preview"` as `[data-slot="dialog-title"]` and the raw filename only in `[data-slot="dialog-description"]`. Previously showed filename as both title and description.

## wait_until="networkidle" Never Resolves

The app polls job status every 2-3 seconds. Never use networkidle — always use domcontentloaded + explicit wait (4000-6000ms).
