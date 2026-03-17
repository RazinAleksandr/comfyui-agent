import type {
  InfluencerOut,
  InfluencerUpsertRequest,
  JobInfo,
  PipelineRun,
  PipelineRunRequest,
  GenerationRequest,
  GenerationJob,
  ServerStatus,
  ServerInfo,
  AllocationInfo,
  ReviewVideo,
  ReviewData,
} from "./types";

const BASE = "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchInit } = init ?? {};
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${BASE}${path}`, {
      ...fetchInit,
      signal: controller.signal,
    });
    // Clear timeout once headers are received — don't abort during body parsing
    clearTimeout(timeoutId);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, `API ${res.status}: ${text}`);
    }
    return res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, `Request to ${path} timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

export const api = {
  // -- Influencers --
  listInfluencers: () => request<InfluencerOut[]>("/influencers"),

  getInfluencer: (id: string) => request<InfluencerOut>(`/influencers/${id}`),

  deleteInfluencer: (id: string) =>
    request<{ deleted: string }>(`/influencers/${id}`, { method: "DELETE" }),

  upsertInfluencer: (id: string, body: InfluencerUpsertRequest) =>
    request<InfluencerOut>(`/influencers/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  uploadReferenceImage: (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ reference_image_path: string }>(
      `/influencers/${id}/reference-image`,
      { method: "POST", body: fd },
    );
  },

  // -- Parser / Pipeline --
  getParserDefaults: () =>
    request<{ default_sources: Record<string, string> }>("/parser/defaults"),

  startPipeline: (body: PipelineRunRequest) =>
    request<{ job_id: string }>("/parser/pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  listRuns: (influencerId: string, limit = 20) =>
    request<PipelineRun[]>(
      `/parser/runs?influencer_id=${encodeURIComponent(influencerId)}&limit=${limit}`,
    ),

  getRun: (influencerId: string, runId: string) =>
    request<PipelineRun>(
      `/parser/runs/${encodeURIComponent(runId)}?influencer_id=${encodeURIComponent(influencerId)}`,
    ),

  // -- Review --
  submitReview: (influencerId: string, runId: string, videos: ReviewVideo[]) =>
    request<ReviewData>(
      `/parser/runs/${encodeURIComponent(runId)}/review?influencer_id=${encodeURIComponent(influencerId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videos }),
      },
    ),

  // -- Jobs --
  getJob: (jobId: string) => request<JobInfo>(`/jobs/${encodeURIComponent(jobId)}`),

  listJobs: (limit = 50) => request<JobInfo[]>(`/jobs?limit=${limit}`),

  activeJobs: (type?: string, influencerId?: string) => {
    const params = new URLSearchParams();
    if (type) params.set("type", type);
    if (influencerId) params.set("influencer_id", influencerId);
    return request<JobInfo[]>(`/jobs/active?${params}`);
  },

  // -- Generation --
  serverStatus: (influencerId?: string) => {
    const params = influencerId ? `?influencer_id=${encodeURIComponent(influencerId)}` : "";
    return request<ServerStatus>(`/generation/server/status${params}`);
  },

  serverUp: (workflow = "wan_animate", influencerId?: string) => {
    const body: Record<string, string> = { workflow };
    if (influencerId) body.influencer_id = influencerId;
    return request<{ job_id: string; server_id: string }>("/generation/server/up", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  serverDown: () =>
    request<{ status: string }>("/generation/server/down", { method: "POST" }),

  listServers: () => request<ServerInfo[]>("/generation/servers"),

  shutdownServer: (serverId: string) =>
    request<{ status: string }>(`/generation/server/${encodeURIComponent(serverId)}/down`, {
      method: "POST",
    }),

  setAutoShutdown: (serverId: string, enabled: boolean) =>
    request<{ server_id: string; auto_shutdown: boolean }>(
      `/generation/server/${encodeURIComponent(serverId)}/auto-shutdown`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      },
    ),

  getAllocationInfo: (influencerId: string) =>
    request<AllocationInfo>(`/generation/server/allocate?influencer_id=${encodeURIComponent(influencerId)}`),

  startGeneration: (body: GenerationRequest) =>
    request<{ job_id: string; server_id: string }>("/generation/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // -- Generation Jobs (DB-backed) --
  getGenerationJobs: (runId: string) =>
    request<GenerationJob[]>(
      `/generation/jobs?run_id=${encodeURIComponent(runId)}`,
    ),
};
