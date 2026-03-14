import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "./client";
import { pipelineRunToTask } from "./mappers";
import type { InfluencerOut, JobInfo, Task } from "./types";

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

// ---- Job polling hook ----

interface UseJobPollerResult {
  job: JobInfo | null;
  loading: boolean;
  isComplete: boolean;
  error: string | null;
}

export function useJobPoller(
  jobId: string | null,
  intervalMs = 3000,
): UseJobPollerResult {
  const [job, setJob] = useState<JobInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isTerminal = job?.status === "completed" || job?.status === "failed";

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const poll = async () => {
      try {
        const info = await api.getJob(jobId);
        if (!cancelled) {
          setJob(info);
          setLoading(false);
          if (info.status === "completed" || info.status === "failed") {
            if (intervalRef.current) {
              clearInterval(intervalRef.current);
              intervalRef.current = null;
            }
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      }
    };

    poll();
    intervalRef.current = setInterval(poll, intervalMs);

    return () => {
      cancelled = true;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [jobId, intervalMs]);

  return { job, loading, isComplete: isTerminal, error };
}
