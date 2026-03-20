-- AI Influencer Studio — SQLite schema
-- Applied automatically on first startup.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- INFLUENCERS
-- ============================================================
CREATE TABLE IF NOT EXISTS influencers (
    influencer_id   TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT 'Influencer',
    description     TEXT,
    hashtags        TEXT,  -- JSON array stored as text
    video_suggestions_requirement TEXT,
    reference_image_path TEXT,
    appearance_description TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- ============================================================
-- PIPELINE RUNS
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id          TEXT PRIMARY KEY,
    influencer_id   TEXT NOT NULL REFERENCES influencers(influencer_id) ON DELETE CASCADE,
    started_at      TEXT NOT NULL,
    base_dir        TEXT NOT NULL,
    request_json    TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_influencer ON pipeline_runs(influencer_id, started_at DESC);

-- ============================================================
-- PIPELINE STAGES (per-platform results within a run)
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_stages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT '',
    ingested_items  INTEGER DEFAULT 0,
    download_counts TEXT,
    candidate_report_path TEXT,
    filtered_dir    TEXT,
    vlm_summary_path TEXT,
    selected_dir    TEXT,
    accepted        INTEGER,
    rejected        INTEGER,
    created_at      TEXT NOT NULL,
    UNIQUE(run_id, platform)
);

-- ============================================================
-- REVIEWS
-- ============================================================
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL UNIQUE REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    completed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id       INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    file_name       TEXT NOT NULL,
    approved        INTEGER NOT NULL DEFAULT 0,
    prompt          TEXT NOT NULL DEFAULT ''
);

-- ============================================================
-- JOBS (persistent, replaces in-memory JobManager)
-- ============================================================
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    job_type        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    result_json     TEXT,
    error           TEXT,
    progress_json   TEXT DEFAULT '{}',
    influencer_id   TEXT,
    server_id       TEXT,
    reference_video TEXT,
    run_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_type_status ON jobs(job_type, status);
CREATE INDEX IF NOT EXISTS idx_jobs_server ON jobs(server_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_influencer ON jobs(influencer_id, job_type);

-- ============================================================
-- GENERATION JOBS (per-video tracking)
-- ============================================================
CREATE TABLE IF NOT EXISTS generation_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    run_id          TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    server_id       TEXT,
    influencer_id   TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    error           TEXT,
    output_dir      TEXT,
    outputs_json    TEXT,
    qa_status       TEXT,
    qa_result_json  TEXT,
    qa_completed_at TEXT,
    UNIQUE(run_id, file_name, job_id)
);
CREATE INDEX IF NOT EXISTS idx_gen_jobs_run ON generation_jobs(run_id);

-- ============================================================
-- SERVERS (replaces .vast-registry.json)
-- ============================================================
CREATE TABLE IF NOT EXISTS servers (
    server_id       TEXT PRIMARY KEY,
    instance_id     INTEGER,
    ssh_host        TEXT,
    ssh_port        INTEGER,
    dph_total       REAL,
    influencer_id   TEXT,
    workflow        TEXT NOT NULL DEFAULT 'wan_animate',
    auto_shutdown   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
