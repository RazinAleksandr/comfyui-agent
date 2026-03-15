import type { PipelineRun, Task, StageResult, VideoPreview, IngestedDetail, VlmAccepted, ReviewVideo } from "./types";

/**
 * Convert a backend PipelineRun manifest (enriched) into the frontend Task shape
 * with 6 pipeline stages.
 */
export function pipelineRunToTask(run: PipelineRun): Task {
  const totalIngested = run.platforms.reduce((s, p) => s + (p.ingested_items || 0), 0);
  const totalDownloaded = run.platforms.reduce(
    (s, p) => s + (p.download_counts?.downloaded ?? 0),
    0,
  );
  const hasFilter = run.platforms.some((p) => p.candidate_report_path || p.filter_report);
  const hasVlm = run.platforms.some((p) => p.vlm_summary_path || p.vlm_report);
  const totalAccepted = run.platforms.reduce((s, p) => s + (p.accepted ?? 0), 0);
  const totalRejected = run.platforms.reduce((s, p) => s + (p.rejected ?? 0), 0);

  // Collect ingested details for Stage 1 (metadata only, no video files)
  const allIngested: IngestedDetail[] = [];
  for (const p of run.platforms) {
    for (const item of p.ingested_details ?? []) {
      allIngested.push(item);
    }
  }

  // -- Stage 1: Trend Ingestion (metadata only — no video previews) --
  const trend_ingestion: StageResult =
    totalIngested > 0
      ? {
          status: "completed",
          items_count: totalIngested,
          details: {
            _type: "ingestion",
            platforms: run.platforms.map((p) => p.platform),
            sources: run.platforms.map((p) => ({ platform: p.platform, source: p.source })),
            ingested: allIngested,
          },
        }
      : { status: "pending" };

  // -- Stage 2: Download --
  const downloadVideos: VideoPreview[] = [];
  for (const p of run.platforms) {
    for (const v of p.download_videos ?? []) {
      downloadVideos.push({
        id: v.file_name,
        thumbnail: v.url,
        title: v.file_name,
      });
    }
  }
  const totalFailed = run.platforms.reduce(
    (s, p) => s + (p.download_counts?.failed ?? 0),
    0,
  );
  const download: StageResult =
    totalDownloaded > 0 || totalFailed > 0
      ? {
          status: "completed",
          items_count: totalDownloaded,
          details: {
            _type: "download",
            downloaded: totalDownloaded,
            failed: totalFailed,
          },
          videos: downloadVideos.length > 0 ? downloadVideos : undefined,
        }
      : totalIngested > 0
        ? { status: "in-progress" }
        : { status: "pending" };

  // -- Stage 3: Candidate Filter --
  const filterVideos: VideoPreview[] = [];
  let filterAccepted = 0;
  let filterRejected = 0;
  const filterCandidates: Array<{
    file_name: string;
    resolution: string;
    duration: string;
    fps: number;
    quality: number;
    stability: number;
    final_score: number;
  }> = [];

  for (const p of run.platforms) {
    if (p.filter_report) {
      filterAccepted += p.filter_report.accepted ?? 0;
      filterRejected += p.filter_report.rejected ?? 0;
      for (const c of p.filter_report.top_candidates ?? []) {
        const res = c.metrics ? `${c.metrics.width}x${c.metrics.height}` : "";
        filterCandidates.push({
          file_name: c.file_name,
          resolution: res,
          duration: c.metrics?.duration_sec ? `${c.metrics.duration_sec.toFixed(1)}s` : "",
          fps: c.metrics?.fps ?? 0,
          quality: c.scores?.quality ?? 0,
          stability: c.scores?.temporal_stability ?? 0,
          final_score: c.scores?.final ?? 0,
        });
        filterVideos.push({
          id: c.file_name,
          thumbnail: "",
          title: c.file_name,
          resolution: res,
          score: c.scores?.final ? Math.round(c.scores.final * 100) / 100 : undefined,
          duration: c.metrics?.duration_sec ? `${c.metrics.duration_sec.toFixed(1)}s` : undefined,
        });
      }
    }
    // Add video thumbnails from filtered directory
    for (const v of p.filtered_videos ?? []) {
      const existing = filterVideos.find((fv) => fv.id === v.file_name);
      if (existing) {
        existing.thumbnail = v.url;
      } else {
        filterVideos.push({ id: v.file_name, thumbnail: v.url, title: v.file_name });
      }
    }
  }
  const candidate_filter: StageResult = hasFilter
    ? {
        status: "completed",
        items_count: filterAccepted,
        details: {
          _type: "filter",
          total_candidates: filterAccepted + filterRejected,
          passed: filterAccepted,
          rejected: filterRejected,
          candidates: filterCandidates,
        },
        videos: filterVideos.length > 0 ? filterVideos : undefined,
      }
    : totalDownloaded > 0
      ? { status: "in-progress" }
      : { status: "pending" };

  // -- Stage 4: VLM Scoring --
  const vlmVideos: VideoPreview[] = [];
  const vlmAccepted: VlmAccepted[] = [];
  let vlmModel = "";

  for (const p of run.platforms) {
    if (p.vlm_report) {
      vlmModel = p.vlm_report.model;
      for (const v of p.vlm_report.accepted_top ?? []) {
        vlmAccepted.push(v);
        vlmVideos.push({
          id: v.file_name,
          thumbnail: "",
          title: v.file_name,
          score: v.readiness,
          approved: true,
        });
      }
    }
    for (const v of p.selected_videos ?? []) {
      const existing = vlmVideos.find((sv) => sv.id === v.file_name);
      if (existing) {
        existing.thumbnail = v.url;
      } else {
        vlmVideos.push({ id: v.file_name, thumbnail: v.url, title: v.file_name, approved: true });
      }
    }
  }
  const vlm_scoring: StageResult = hasVlm
    ? {
        status: "completed",
        items_count: totalAccepted,
        details: {
          _type: "vlm",
          accepted: totalAccepted,
          rejected: totalRejected,
          model: vlmModel,
          accepted_items: vlmAccepted,
        },
        videos: vlmVideos.length > 0 ? vlmVideos : undefined,
      }
    : hasFilter
      ? { status: "in-progress" }
      : { status: "pending" };

  // -- Stage 5: Review --
  const reviewData = run.review;
  const reviewCompleted = reviewData?.completed === true;
  const reviewVideos: VideoPreview[] = [];

  if (reviewCompleted && reviewData.videos) {
    for (const rv of reviewData.videos) {
      // Find the matching VLM video for thumbnail
      const vlmV = vlmVideos.find((v) => v.id === rv.file_name);
      reviewVideos.push({
        id: rv.file_name,
        thumbnail: vlmV?.thumbnail ?? "",
        title: rv.file_name,
        approved: rv.approved,
        prompt: rv.prompt || undefined,
        score: vlmV?.score,
      });
    }
  }

  const approvedCount = reviewData?.videos?.filter((v: ReviewVideo) => v.approved).length ?? 0;
  const review: StageResult = reviewCompleted
    ? {
        status: "completed",
        items_count: approvedCount,
        details: {
          _type: "review",
          total: reviewData.videos.length,
          approved: approvedCount,
          skipped: reviewData.videos.length - approvedCount,
        },
        videos: reviewVideos.length > 0 ? reviewVideos : undefined,
      }
    : hasVlm
      ? { status: "pending" }
      : { status: "pending" };

  // -- Stage 6: Generation --
  const genJobs = run.generation?.jobs ?? [];
  let generation: StageResult;
  if (genJobs.length > 0) {
    // Jobs without a status (lost on server restart) are treated as "failed" (unknown)
    const jobStatuses = genJobs.map((j) => j.status || "unknown");
    const allCompleted = jobStatuses.every((s) => s === "completed");
    const anyRunning = jobStatuses.some((s) => s === "running" || s === "pending");
    const anyFailed = jobStatuses.some((s) => s === "failed" || s === "unknown");
    const genStatus = allCompleted ? "completed" : anyRunning ? "in-progress" : anyFailed ? "failed" : "pending";

    const completedCount = jobStatuses.filter((s) => s === "completed").length;
    const failedCount = jobStatuses.filter((s) => s === "failed" || s === "unknown").length;

    generation = {
      status: genStatus,
      items_count: genJobs.length,
      details: {
        _type: "generation",
        total: genJobs.length,
        completed: completedCount,
        failed: failedCount,
        running: jobStatuses.filter((s) => s === "running").length,
      },
    };
  } else {
    generation = reviewCompleted ? { status: "pending" } : { status: "pending" };
  }

  // -- Overall task status --
  const pipelineStages = [trend_ingestion, download, candidate_filter, vlm_scoring];
  const pipelineDone = pipelineStages.every((s) => s.status === "completed");
  const hasFailed = pipelineStages.some((s) => s.status === "failed");
  const hasInProgress = pipelineStages.some((s) => s.status === "in-progress");

  let status: Task["status"];
  if (hasFailed) status = "failed";
  else if (pipelineDone) status = "completed";
  else if (hasInProgress) status = "in-progress";
  else status = "in-progress";

  return {
    id: run.run_id,
    influencer_id: run.influencer_id,
    created_at: run.started_at,
    status,
    stages: {
      trend_ingestion,
      download,
      candidate_filter,
      vlm_scoring,
      review,
      generation,
    },
  };
}
