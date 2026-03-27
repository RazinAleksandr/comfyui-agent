# Codebase Concerns

**Analysis Date:** 2026-03-27

## Tech Debt

**Broad Exception Handling with Silent Failures:**
- Issue: Multiple critical paths catch all exceptions and silently pass or log at warning level without proper recovery
- Files: `src/api/routes/generation.py` (lines 263, 286, 382, 437, 616, 669, 679, 686, 699, 709, 729), `src/api/routes/parser.py` (lines 182, 387, 400, 455, 459, 741, 854)
- Impact: Errors get masked, making debugging difficult and allowing partial failures to go unnoticed. Failed operations may not be properly rolled back.
- Fix approach: Replace bare `except Exception` with specific exception types. Add proper error context (what operation failed, why). Distinguish between recoverable (log and retry) vs fatal (propagate) failures.

**Direct sqlite3 Connections in Async Context:**
- Issue: `src/api/job_manager.py` opens sync sqlite3 connections directly (lines 179, 307, 360, 384) in code that's part of the async job manager
- Files: `src/api/job_manager.py` (multiple sync sqlite3.connect calls)
- Impact: Blocks the event loop when accessing the database outside the async Database wrapper. Can cause latency spikes and starve other async tasks.
- Fix approach: Refactor to use the async Database class for all DB access instead of opening raw sqlite3 connections. Use `asyncio.to_thread()` if sync operations are unavoidable.

**Blocking Operations in Async Context (time.sleep):**
- Issue: `src/vast_agent/service.py` uses `time.sleep()` for polling (lines 209, 373) in what should be an async service
- Files: `src/vast_agent/service.py` (lines 209, 373)
- Impact: Blocks event loop during VastAI instance search and remote execution polling. Frontend becomes unresponsive during these operations.
- Fix approach: Replace `time.sleep()` with `asyncio.sleep()`. Refactor service methods to be async or ensure they're always called via `asyncio.to_thread()`.

**Unhandled JSON Parsing Without Fallback:**
- Issue: Multiple calls to `json.loads()` without try/except in hot paths
- Files: `src/vast_agent/service.py` (line 142), `src/vast_agent/manager.py` (lines 79, 168), `src/api/routes/parser.py` (lines 550, 569, 1016, 1092, 1205)
- Impact: Malformed JSON from corrupted state files, disk reads, or API responses will crash the service instead of gracefully degrading.
- Fix approach: Wrap all `json.loads()` calls with try/except (json.JSONDecodeError). Provide sensible defaults (empty dict, empty list). Log corruption warnings.

**Per-Server Lock Using threading.Lock:**
- Issue: `src/vast_agent/manager.py` uses `threading.Lock` for per-server generation queue (line 62)
- Files: `src/vast_agent/manager.py` (line 62), `src/api/routes/generation.py` (lines 639, 674)
- Impact: Lock is acquired in async context via `asyncio.to_thread()`, which works but is inefficient. No fairness guarantees between multiple concurrent requests.
- Fix approach: Consider using `asyncio.Lock` per server instead, or at least document the lock ordering to prevent deadlocks.

## Known Bugs

**Server Health Check Can Remove Servers with Active Jobs:**
- Symptoms: Servers disappear from UI mid-generation even though a job is running
- Files: `src/vast_agent/manager.py` (lines 223-234)
- Trigger: Health check runs while a job is transitioning between statuses, or `_job_checker` reports 0 jobs due to timing
- Current mitigation: 15-minute grace period for new servers (line 217), attempts to query job manager (lines 229-234)
- Workaround: Manually re-create server if it's removed unexpectedly
- Fix approach: Make job checking more robust by checking both `_job_checker` AND recent completion timestamps, not just counts. Add server "protected" state that survives one health check cycle.

**Generation Job Duplicate Detection Race Condition:**
- Symptoms: Duplicate generation jobs created for the same video in rapid requests
- Files: `src/api/routes/generation.py` (lines 52-63, 395-412)
- Trigger: Two simultaneous requests for the same reference video before first job is saved to DB
- Current mitigation: `INSERT OR IGNORE` uniqueness constraint (line 71), duplicate check before submit (line 401)
- Workaround: Retry failed generations manually (they'll be deduplicated)
- Fix approach: Use database transaction with serializable isolation or check-and-insert atomicity. Move duplicate check to right before DB insert, inside a transaction.

**Broken SSE Parser Loss on Frontend Reconnect:**
- Symptoms: Event updates stop flowing after network interruption, even though SSE reconnects
- Files: `frontend/src/app/api/sse.ts` (lines 42-55)
- Trigger: Network disconnect during event generation; EventSource auto-reconnects but may miss queued events
- Current mitigation: Exponential backoff (line 54), heartbeat (sse.ts ecosystem sends heartbeats)
- Workaround: Manual page refresh restores state from API polling
- Fix approach: Frontend should poll `/api/v1/generation/jobs?run_id=X` on SSE reconnect to catch missed updates.

**Auto-Shutdown Off-By-One with Active Jobs:**
- Symptoms: Server shuts down while job is still running, or job count underflows
- Files: `src/vast_agent/manager.py` (line 420), `src/api/routes/generation.py` (line 685)
- Trigger: `on_generation_complete()` called while the calling job still shows as "running" in JobManager
- Current mitigation: Subtracts 1 from active job count (line 420)
- Workaround: Disable auto-shutdown if issues arise
- Fix approach: Refactor to pass job_id to `on_generation_complete()` and check by job_id not count. Or use post-completion callback instead of calling from within job.

## Security Considerations

**Default JWT Secret in Development:**
- Risk: `AUTH_JWT_SECRET` defaults to hardcoded "default-dev-secret" if env var not set
- Files: `src/api/auth.py` (line 16)
- Current mitigation: Works for dev; production relies on environment variable
- Recommendations: Remove default, make JWT_SECRET required. Add startup check that throws if missing in production. Consider using asymmetric keys (RS256).

**No HTTPS Enforcement:**
- Risk: SSE connection sends auth tokens in query string without transport encryption
- Files: `frontend/src/app/api/sse.ts` (line 23), `src/api/routes/events.py`
- Current mitigation: Production likely behind reverse proxy with HTTPS
- Recommendations: Document HTTPS requirement clearly. Consider moving token to Authorization header instead of query string.

**SQL Injection Risk in Query String Parsing:**
- Risk: Low — parameterized queries are used throughout. However, some dynamic table/column names exist in migration code.
- Files: `src/api/migrate.py`, `src/api/migrate_paths.py`
- Current mitigation: Migration runs once at startup
- Recommendations: Review and audit migration code for any dynamic SQL construction.

**Insufficient Input Validation on Paths:**
- Risk: File paths from user input (`reference_video`, `output_dir`) are used in filesystem operations without validation
- Files: `src/api/routes/generation.py` (lines 358-393)
- Current mitigation: Paths are converted to absolute (via `to_absolute`), but no symlink/traversal checks
- Recommendations: Validate paths are within `data_dir` after absolutizing. Use `Path.resolve().relative_to(data_dir)` to detect escapes.

## Performance Bottlenecks

**Synchronous VastAI API Calls in Generation Requests:**
- Problem: Instance discovery and status checks make network calls to VastAI API synchronously (blocking event loop)
- Files: `src/vast_agent/manager.py` (lines 112-117), `src/vast_agent/service.py` (lines 137-150, 205-211)
- Cause: VastClient wraps HTTP calls that are blocking. Called from async routes via `asyncio.to_thread()`, which works but is inefficient for many concurrent requests.
- Improvement path: Wrap VastClient in httpx async client. Cache instance status with TTL instead of checking on every request.

**Full Database Scan for Job Cleanup:**
- Problem: Completed jobs cleanup (line 95 in job_manager.py) may scan large job tables
- Files: `src/api/job_manager.py` (implied from `_MAX_COMPLETED_JOBS` constant)
- Cause: No index on status + created_at for efficient range queries
- Improvement path: Add index on `jobs(status, created_at)`. Batch delete old jobs instead of per-job operations.

**Inefficient Generation Jobs Query:**
- Problem: Query to list generation jobs uses subquery to find latest job per video (lines 305-308 in generation.py)
- Files: `src/api/routes/generation.py` (lines 299-310)
- Cause: Subquery runs for every row; scales poorly with many generations
- Improvement path: Use window function `ROW_NUMBER()` over (partition by file_name order by id desc) instead of correlated subquery.

**No Pagination for Large Run Lists:**
- Problem: Frontend fetches all pipeline runs without limit
- Files: `src/api/routes/parser.py` (implied), frontend queries
- Cause: Long-running studios accumulate hundreds of runs; loading all at once stalls browser
- Improvement path: Implement cursor-based pagination. Return runs in reverse chronological order with limit (e.g., 50). Add "load more" button.

**ISP Post-Processing Runs Synchronously in Generation Path:**
- Problem: `postprocess_outputs()` blocks generation job completion
- Files: `src/api/routes/generation.py` (line 695)
- Cause: Runs locally after GPU job completes; ties up server thread
- Improvement path: Queue ISP as separate background job. Return generation result immediately, apply ISP async.

## Fragile Areas

**Pipeline Run Manifest Enrichment Logic:**
- Files: `src/api/routes/parser.py` (enrichment fallback logic, multiple places)
- Why fragile: Relies on scanned disk files as fallback if DB is incomplete. Two sources of truth (DB + filesystem) can diverge.
- Safe modification: Add comprehensive logging of which source is used. Periodically audit DB vs filesystem consistency. Store file paths explicitly in DB instead of deriving from directory structure.
- Test coverage: Limited coverage of enrichment fallbacks; mostly happy-path tests.

**Server Allocation and State Synchronization:**
- Files: `src/vast_agent/manager.py` (allocate_server, update_registry_from_service), `src/vast_agent/db_registry.py`
- Why fragile: Multiple sources of state (VastAI API, state files, DB, in-memory service objects). Timing windows where they can diverge.
- Safe modification: Make all state transitions go through the DB (single source of truth). State files are cache only, always read from DB first.
- Test coverage: No integration tests for server allocation under concurrent requests.

**Reference Alignment Gemini API Integration:**
- Files: `src/api/ref_align.py`
- Why fragile: Depends on Gemini API availability and exact response format. Falls back silently on any error (line 670), user doesn't know alignment failed.
- Safe modification: Add explicit error reporting to frontend. Cache successful alignments so retries are fast. Add retry logic with exponential backoff.
- Test coverage: No tests for failure modes (API down, timeout, invalid response).

**Telegram Bot Conversation State:**
- Files: `src/telegram_bot/conversation.py` (1080 lines, largest file in codebase)
- Why fragile: State machine with 15+ states, complex transitions. No formal state definition or validation.
- Safe modification: Extract state machine to separate class. Add tests for all transition paths. Document valid transitions explicitly.
- Test coverage: Minimal; mostly integration tests hitting live Telegram API.

## Scaling Limits

**SQLite File Database as Bottleneck:**
- Current capacity: Single file, WAL mode allows concurrent reads + one writer. Tested up to ~10K jobs in DB.
- Limit: When approaches 100K+ rows, queries slow down. No sharding.
- Scaling path: Migrate to PostgreSQL for horizontal scaling. Add read replicas. Implement archive table for old jobs.

**Per-Server Threading Lock (generation queue):**
- Current capacity: 1 generation per server at a time (by design), with queue handled at FastAPI layer.
- Limit: With hundreds of influencers each wanting to generate, request queue at API layer can grow unbounded. No backpressure.
- Scaling path: Implement bounded queue with rejection (HTTP 429 Too Many Requests). Or implement priority queue (VIP influencers first).

**In-Memory Event Bus:**
- Current capacity: 256-item queue per subscriber (asyncio.Queue maxsize=256). Drops events for slow consumers.
- Limit: With many SSE clients or high event frequency, subscribers fall behind and miss updates.
- Scaling path: Use Redis pub/sub instead. Or increase queue size (trades memory for latency). Add metrics to detect slow consumers.

**VastAI Instance Search Loop:**
- Current capacity: Searches up to 20 times with 30-second delays (~10 minutes total).
- Limit: If GPU market is saturated, search gives up. No fallback (e.g., try different GPU types).
- Scaling path: Implement GPU preference fallback (e.g., try RTX 4090 → RTX 4080 → H100). Cache available offers.

## Dependencies at Risk

**yt-dlp (Video Download):**
- Risk: Maintained by community, not major company. API frequently breaks when platforms change (TikTok, Instagram).
- Impact: Downloads fail silently or with cryptic errors. Blocks content ingestion pipeline.
- Migration plan: Keep fallback scrapers (Apify, custom parsing). Add detection for download failures and notify user. Monitor yt-dlp issues.

**ComfyUI Workflow (Remote Generation):**
- Risk: Workflow files are JSON; changes to output node indices or model names break workflow. No versioning.
- Impact: Generation jobs fail with cryptic node errors. Hard to debug without access to remote GPU.
- Migration plan: Add workflow validation on startup. Record workflow version in generation_jobs table. Support multiple workflow versions.

**VastAI Python SDK (VastClient):**
- Risk: Wrapper around undocumented HTTP API. Can break if API changes. Library is thin (241 lines).
- Impact: Instance discovery, status checks, bidding all fail.
- Migration plan: Consider using VastAI's official CLI instead of Python SDK. Or maintain local fork if needed.

**Gemini API (VLM Scoring, Reference Alignment):**
- Risk: Rate-limited API with no local fallback. Costs per call.
- Impact: VLM scoring fails silently (line 256 in vlm.py catches all exceptions). Reference alignment degraded (line 670 in ref_align.py).
- Migration plan: Cache VLM scores in DB. Use cheaper model (Claude 3.5 Sonnet instead of Gemini Flash). Add local image model fallback.

## Missing Critical Features

**No Conflict Resolution for Simultaneous Edits:**
- Problem: Two users editing same influencer profile simultaneously; last write wins
- Blocks: Collaborative content creation workflows

**No Backup/Restore for Database:**
- Problem: Studio.db is single point of failure; no automated backups
- Blocks: Business continuity for paid studios

**No Video Asset Deduplication:**
- Problem: Same video downloaded multiple times if ingested from different platforms
- Blocks: Storage efficiency for large studios

**No Scheduled / Recurring Jobs:**
- Problem: All pipeline runs are manual; no ability to schedule daily trending videos ingest
- Blocks: Automation-first workflows

## Test Coverage Gaps

**Server Allocation Under Concurrent Requests:**
- What's not tested: Two simultaneous generation requests trying to allocate free servers; race condition scenarios
- Files: `src/vast_agent/manager.py`
- Risk: Silent allocation failures or resource exhaustion
- Priority: High

**Generation Job Duplicate Detection:**
- What's not tested: Rapid duplicate requests for same video; timing window between check and insert
- Files: `src/api/routes/generation.py` (lines 52-63, 395-412)
- Risk: Duplicate jobs, wasted GPU time
- Priority: High

**Reference Alignment Failure Modes:**
- What's not tested: Gemini API down, timeout, invalid image format, network errors
- Files: `src/api/ref_align.py`
- Risk: Silent alignment skip, user doesn't know why image looks wrong
- Priority: Medium

**Database Corruption / WAL Recovery:**
- What's not tested: Unclean shutdown, disk full during write, WAL file corruption
- Files: `src/api/database.py`
- Risk: Unrecoverable database or data loss
- Priority: High

**Broad Exception Handling Scenarios:**
- What's not tested: What happens when generation fails with network error vs CUDA error vs disk full?
- Files: `src/api/routes/generation.py`, many catch-all exception handlers
- Risk: Different failures get same treatment; poor debugging and recovery
- Priority: Medium

**Frontend SSE Reconnection:**
- What's not tested: Network disconnect → reconnect timing; does frontend miss updates?
- Files: `frontend/src/app/api/sse.ts`, frontend components
- Risk: Stale UI after network hiccup
- Priority: Medium

---

*Concerns audit: 2026-03-27*
