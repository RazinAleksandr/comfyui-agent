import { useState, useEffect, useCallback } from "react";
import { api } from "./client";
import { pipelineRunToTask } from "./mappers";
import { subscribe } from "./sse";
import type { InfluencerOut, JobInfo, PipelineRun, Task } from "./types";

// ---- Generic fetch hook ----

interface UseQueryResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

function useQuery<T>(fetcher: () => Promise<T>, deps: unknown[] = []): UseQueryResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetcher()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message ?? String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  return { data, loading, error, refetch };
}

// ---- Influencer hooks ----

export function useInfluencers(): UseQueryResult<InfluencerOut[]> {
  return useQuery(() => api.listInfluencers());
}

export function useInfluencer(id: string | undefined): UseQueryResult<InfluencerOut> {
  return useQuery(() => {
    if (!id) return Promise.reject(new Error("No influencer ID"));
    return api.getInfluencer(id);
  }, [id]);
}

// ---- Pipeline run hooks ----

export function usePipelineRuns(influencerId: string | undefined): UseQueryResult<Task[]> {
  return useQuery(async () => {
    if (!influencerId) return [];
    const runs = await api.listRuns(influencerId);
    return runs.map(pipelineRunToTask);
  }, [influencerId]);
}

export function usePipelineRun(
  influencerId: string | undefined,
  runId: string | undefined,
): UseQueryResult<Task> {
  return useQuery(() => {
    if (!influencerId || !runId) return Promise.reject(new Error("Missing params"));
    return api.getRun(influencerId, runId).then(pipelineRunToTask);
  }, [influencerId, runId]);
}

export function useRawPipelineRun(
  influencerId: string | undefined,
  runId: string | undefined,
): UseQueryResult<PipelineRun> {
  return useQuery(() => {
    if (!influencerId || !runId) return Promise.reject(new Error("Missing params"));
    return api.getRun(influencerId, runId);
  }, [influencerId, runId]);
}

// ---- SSE-based job hook (real-time, no polling) ----

interface UseJobPollerResult {
  job: JobInfo | null;
  loading: boolean;
  isComplete: boolean;
  error: string | null;
}


/**
 * Subscribe to real-time updates for a specific job via SSE.
 *
 * Does an initial REST fetch, then receives live progress and state
 * changes via SSE. No polling interval needed.
 */
export function useJobSSE(jobId: string | null): UseJobPollerResult {
  const [job, setJob] = useState<JobInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isTerminal = job?.status === "completed" || job?.status === "failed";

  // Initial fetch from REST API
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    api
      .getJob(jobId)
      .then((info) => {
        setJob(info);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, [jobId]);

  // Subscribe to SSE progress updates
  useEffect(() => {
    if (!jobId) return;
    return subscribe("job_progress", (data: unknown) => {
      const d = data as { job_id: string; progress: Record<string, unknown> };
      if (d.job_id === jobId) {
        setJob((prev) =>
          prev ? { ...prev, progress: { ...prev.progress, ...d.progress } } : prev,
        );
      }
    });
  }, [jobId]);

  // Subscribe to SSE state changes
  useEffect(() => {
    if (!jobId) return;
    return subscribe("job_state", (data: unknown) => {
      const d = data as {
        job_id: string;
        status: string;
        error?: string;
        result?: unknown;
      };
      if (d.job_id === jobId) {
        setJob((prev) =>
          prev
            ? {
                ...prev,
                status: d.status as JobInfo["status"],
                error: d.error ?? prev.error,
                result: d.result ?? prev.result,
              }
            : prev,
        );
      }
    });
  }, [jobId]);

  return { job, loading, isComplete: isTerminal, error };
}

// ---- Connection status hook ----

/**
 * Returns whether the SSE connection to the backend is alive.
 * Shows an offline banner when the backend is unreachable.
 */
export function useConnectionStatus(): boolean {
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const unsub1 = subscribe("__open__", () => setOnline(true));
    const unsub2 = subscribe("__error__", () => setOnline(false));
    return () => {
      unsub1();
      unsub2();
    };
  }, []);

  return online;
}
