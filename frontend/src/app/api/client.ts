import type {
  InfluencerOut,
  InfluencerUpsertRequest,
  JobInfo,
  PipelineRun,
  PipelineRunRequest,
  GenerationRequest,
  ServerStatus,
} from "./types";

const BASE = "/api/v1";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `API ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  // -- Influencers --
  listInfluencers: () => request<InfluencerOut[]>("/influencers"),

  getInfluencer: (id: string) => request<InfluencerOut>(`/influencers/${id}`),

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

  // -- Jobs --
  getJob: (jobId: string) => request<JobInfo>(`/jobs/${encodeURIComponent(jobId)}`),

  listJobs: (limit = 50) => request<JobInfo[]>(`/jobs?limit=${limit}`),

  // -- Generation --
  serverStatus: () => request<ServerStatus>("/generation/server/status"),

  serverUp: (workflow = "wan_animate") =>
    request<{ job_id: string }>("/generation/server/up", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow }),
    }),

  serverDown: () =>
    request<{ status: string }>("/generation/server/down", { method: "POST" }),

  startGeneration: (body: GenerationRequest) =>
    request<{ job_id: string }>("/generation/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
