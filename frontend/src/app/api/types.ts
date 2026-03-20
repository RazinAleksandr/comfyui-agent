// ---- Auth types ----

export interface LoginRequest { username: string; password: string; }
export interface LoginResponse { access_token: string; token_type: string; user: AuthUser; }
export interface AuthUser { username: string; display_name: string; }

// ---- API response types (match backend Pydantic models) ----

export interface InfluencerOut {
  influencer_id: string;
  name: string;
  description: string | null;
  hashtags: string[] | null;
  video_suggestions_requirement: string | null;
  reference_image_path: string | null;
  appearance_description: string | null;
  profile_image_url: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface InfluencerUpsertRequest {
  name: string;
  description?: string;
  hashtags?: string[];
  video_suggestions_requirement?: string;
  appearance_description?: string;
}

export interface JobInfo {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: unknown;
  error: string | null;
  progress: Record<string, unknown>;
}

export interface VideoFile {
  file_name: string;
  url: string;
}

export interface FilterCandidate {
  file_name: string;
  platform: string;
  views: number;
  metrics?: { duration_sec: number; width: number; height: number; fps: number };
  scores?: { quality: number; temporal_stability: number; swap_compatibility: number; final: number };
}

export interface FilterRejectedCandidate {
  file_name: string;
  platform: string;
  views: number;
  metrics?: { duration_sec: number; width: number; height: number; fps: number };
  scores?: { quality: number; temporal_stability: number; swap_compatibility: number; final: number };
  reject_reasons: string[];
}

export interface VlmVideoDetail {
  file_name: string;
  auto_decision: string;
  summary: string;
  scores: {
    theme_match?: number;
    persona_fit?: number;
    single_subject_clarity?: number;
    face_visibility?: number;
    motion_stability?: number;
    occlusion_risk?: number;
    scene_cut_complexity?: number;
    substitution_readiness?: number;
  };
  confidence: number;
  reasons: string[];
  decision?: string;
}

export interface VlmAccepted {
  file_name: string;
  readiness: number;
  persona_fit: number;
  confidence: number;
  reasons: string[];
}

export interface IngestedDetail {
  video_url: string;
  caption: string;
  views: number;
  likes: number;
  hashtags: string[];
  platform: string;
}

export interface PipelinePlatformRun {
  platform: string;
  source: string;
  ingested_items: number;
  download_counts: Record<string, number> | null;
  candidate_report_path: string | null;
  filtered_dir: string | null;
  vlm_summary_path: string | null;
  selected_dir: string | null;
  accepted: number | null;
  rejected: number | null;
  // Enriched fields from backend
  download_videos?: VideoFile[];
  filtered_videos?: VideoFile[];
  selected_videos?: VideoFile[];
  filter_report?: { total_candidates: number; accepted: number; rejected: number; top_k: number; top_candidates: FilterCandidate[]; rejected_candidates?: FilterRejectedCandidate[] };
  vlm_report?: { model: string; total: number; accepted: number; rejected: number; accepted_top: VlmAccepted[] };
  vlm_video_details?: VlmVideoDetail[];
  rejected_videos?: VideoFile[];
  ingested_details?: IngestedDetail[];
}

export interface ReviewVideo {
  file_name: string;
  approved: boolean;
  prompt: string;
}

export interface ReviewData {
  completed: boolean;
  videos: ReviewVideo[];
}

export interface GenerationOutput {
  path: string;
  url: string;
  name: string;
}

export interface QaResult {
  verdict: string;
  score: number;
  issues: string[];
  scores?: Record<string, number>;
  summary?: string;
  error?: string;
}

export interface GenerationJob {
  file_name: string;
  job_id: string;
  started_at: string;
  status?: string;
  progress?: Record<string, unknown>;
  error?: string;
  outputs?: GenerationOutput[];
  qa_status?: string;
  qa_result?: QaResult;
  aligned_image_url?: string;
}

export interface GenerationData {
  jobs: GenerationJob[];
}

export interface PipelineRun {
  run_id: string; // injected by store layer, not in Pydantic model
  influencer_id: string;
  started_at: string;
  base_dir: string;
  platforms: PipelinePlatformRun[];
  request?: Record<string, unknown>;
  review?: ReviewData;
  generation?: GenerationData;
}

export interface PlatformPipelineConfig {
  enabled?: boolean;
  source?: string;
  limit?: number;
  selector?: { hashtags?: string[]; min_views?: number };
}

export interface ReviewStageConfig {
  auto?: boolean;
}

export interface PipelineRunRequest {
  influencer_id: string;
  platforms: Record<string, PlatformPipelineConfig>;
  download?: { enabled?: boolean; force?: boolean };
  filter?: { enabled?: boolean; top_k?: number; probe_seconds?: number; workers?: number };
  vlm?: { enabled?: boolean; max_videos?: number };
  review?: ReviewStageConfig;
}

export interface GenerationRequest {
  influencer_id: string;
  workflow?: string;
  reference_image?: string;
  reference_video?: string;
  prompt?: string;
  set_args?: Record<string, string>;
  align_reference?: boolean;
}

export interface ServerStatus {
  status: "running" | "offline";
  server_id?: string;
  instance_id: number | null;
  ssh_host: string | null;
  ssh_port: number | null;
  dph_total: number | null;
  ssh_reachable: boolean | null;
  actual_status: string | null;
  startup_job_id: string | null;
  startup_job_status: string | null;
  influencer_id?: string | null;
  auto_shutdown?: boolean;
  active_jobs?: number;
}

export interface ServerInfo {
  server_id: string;
  instance_id: number | null;
  influencer_id: string | null;
  ssh_host: string | null;
  ssh_port: number | null;
  dph_total: number | null;
  workflow: string;
  created_at: string;
  auto_shutdown: boolean;
  active_jobs: number;
}

export interface AllocationInfo {
  has_own_server: boolean;
  server_id: string | null;
  server_busy: boolean;
  active_jobs: number;
  can_borrow: boolean;
  borrow_server_id: string | null;
}

export interface GeneratedContentSource {
  video_url: string;
  platform: string;
  views: number;
  likes: number;
  caption: string;
}

export interface GeneratedContentItem {
  file_name: string;
  run_id: string;
  video_url: string;
  completed_at: string | null;
  source: GeneratedContentSource;
}

// ---- Presentation types (used by page components) ----

export interface VideoPreview {
  id: string;
  thumbnail: string;
  title: string;
  duration?: string;
  score?: number;
  platform?: string;
  resolution?: string;
  approved?: boolean;
  prompt?: string;
  generation_steps?: {
    raw: string;
    refined: string;
    upscaled: string;
    postprocessed: string;
  };
}

export interface StageResult {
  status: "completed" | "in-progress" | "pending" | "failed" | "lost";
  items_count?: number;
  duration?: string;
  details?: Record<string, unknown>;
  videos?: VideoPreview[];
}

export interface Task {
  id: string;
  influencer_id: string;
  created_at: string;
  status: "pending" | "in-progress" | "completed" | "failed" | "lost";
  stages: {
    trend_ingestion: StageResult;
    download: StageResult;
    candidate_filter: StageResult;
    vlm_scoring: StageResult;
    review: StageResult;
    generation: StageResult;
  };
}
