// ---- API response types (match backend Pydantic models) ----

export interface InfluencerOut {
  influencer_id: string;
  name: string;
  description: string | null;
  hashtags: string[] | null;
  video_suggestions_requirement: string | null;
  reference_image_path: string | null;
  profile_image_url: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface InfluencerUpsertRequest {
  name: string;
  description?: string;
  hashtags?: string[];
  video_suggestions_requirement?: string;
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
  filter_report?: { total_candidates: number; accepted: number; rejected: number; top_k: number; top_candidates: FilterCandidate[] };
  vlm_report?: { model: string; total: number; accepted: number; rejected: number; accepted_top: VlmAccepted[] };
  ingested_details?: IngestedDetail[];
}

export interface PipelineRun {
  run_id: string; // injected by store layer, not in Pydantic model
  influencer_id: string;
  started_at: string;
  base_dir: string;
  platforms: PipelinePlatformRun[];
  request?: Record<string, unknown>;
}

export interface PlatformPipelineConfig {
  enabled?: boolean;
  source?: string;
  limit?: number;
  selector?: { hashtags?: string[]; min_views?: number };
}

export interface PipelineRunRequest {
  influencer_id: string;
  platforms: Record<string, PlatformPipelineConfig>;
  download?: { enabled?: boolean; force?: boolean };
  filter?: { enabled?: boolean; top_k?: number };
  vlm?: { enabled?: boolean; model?: string; max_videos?: number };
}

export interface GenerationRequest {
  influencer_id: string;
  workflow?: string;
  reference_image?: string;
  reference_video?: string;
  prompt?: string;
  set_args?: Record<string, string>;
}

export interface ServerStatus {
  status: "running" | "offline";
  instance_id: number | null;
  ssh_host: string | null;
  ssh_port: number | null;
  dph_total: number | null;
  ssh_reachable: boolean | null;
  actual_status: string | null;
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
  status: "completed" | "in-progress" | "pending" | "failed";
  items_count?: number;
  duration?: string;
  details?: Record<string, unknown>;
  videos?: VideoPreview[];
}

export interface Task {
  id: string;
  influencer_id: string;
  created_at: string;
  status: "in-progress" | "completed" | "failed";
  stages: {
    trend_ingestion: StageResult;
    download: StageResult;
    candidate_filter: StageResult;
    vlm_scoring: StageResult;
    review: StageResult;
    generation: StageResult;
  };
}
