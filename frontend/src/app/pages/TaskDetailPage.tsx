import { useState, useCallback, useEffect, useRef } from "react";
import { Link, useParams } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { useInfluencer, usePipelineRun, useRawPipelineRun, useJobSSE, useConnectionStatus } from "../api/hooks";
import { ImageWithFallback } from "../components/figma/ImageWithFallback";
import { api } from "../api/client";
import type { VideoPreview, ReviewVideo, VlmAccepted, GenerationJob, AllocationInfo, VlmVideoDetail, FilterRejectedCandidate, InfluencerOut, QaResult } from "../api/types";
import { subscribe } from "../api/sse";
import {
  ArrowLeft,
  Download,
  Filter,
  Sparkles,
  Eye,
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
  TrendingUp,
  Clock,
  FileVideo,
  AlertCircle,
  Play,
  Star,
  CheckCheck,
  X as XIcon,
  ChevronRight,
  ExternalLink,
  Hash,
  ThumbsUp,
  Brain,
  Gauge,
  Shield,
  Send,
  Zap,
  Server,
  SkipForward,
  Power,
  RotateCcw,
  ArrowUpCircle,
  Pencil,
  RefreshCw,
} from "lucide-react";
import { Progress } from "../components/ui/progress";
import { Separator } from "../components/ui/separator";

const stageIcons = {
  trend_ingestion: TrendingUp,
  download: Download,
  candidate_filter: Filter,
  vlm_scoring: Sparkles,
  review: Eye,
  generation: CheckCircle2,
};

const stageTitles = {
  trend_ingestion: "Trend Ingestion",
  download: "Download",
  candidate_filter: "Candidate Filter",
  vlm_scoring: "VLM Scoring",
  review: "Review",
  generation: "Generation",
};

const stageDescriptions = {
  trend_ingestion: "TikTok custom adapter searches hashtags and collects video metadata (URLs, views, likes, captions)",
  download: "Downloads actual video files via yt-dlp into the pipeline run directory",
  candidate_filter: "Deterministic pre-filtering using ffprobe - probes first 8 seconds of each video to check resolution, duration, codec quality. Ranks candidates and copies top-K to filtered/",
  vlm_scoring: "Sends each filtered video to Gemini with the influencer's persona profile. Gemini scores on 8 criteria (theme_match, persona_fit, face_visibility, motion_stability, occlusion_risk, etc.). Auto-decides accept/reject based on thresholds",
  review: "Review VLM-approved videos. Approve or skip each video and provide generation prompts for approved ones",
  generation: "For each approved video, vast-agent uploads the reference image + video to the remote GPU and runs ComfyUI's Wan 2.2 Animate workflow: Raw \u2192 Refined \u2192 Upscaled \u2192 Postprocessed",
};

function getStatusIcon(status: string, size = "w-5 h-5") {
  switch (status) {
    case "completed":
      return <CheckCircle2 className={`${size} text-green-600`} />;
    case "in-progress":
      return <Loader2 className={`${size} text-blue-600 animate-spin`} />;
    case "failed":
      return <XCircle className={`${size} text-red-600`} />;
    case "lost":
      return <Circle className={`${size} text-amber-500`} />;
    default:
      return <Circle className={`${size} text-slate-300`} />;
  }
}

function getStatusColor(status: string) {
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-800 border-green-200";
    case "in-progress":
      return "bg-blue-100 text-blue-800 border-blue-200";
    case "failed":
      return "bg-red-100 text-red-800 border-red-200";
    case "lost":
      return "bg-amber-100 text-amber-800 border-amber-200";
    default:
      return "bg-slate-100 text-slate-600 border-slate-200";
  }
}

function scoreColor(score: number) {
  if (score >= 8.5) return "bg-green-500";
  if (score >= 7.0) return "bg-yellow-500";
  return "bg-red-500";
}

function isVideoUrl(url: string) {
  return /\.(mp4|webm|mov|mkv)$/i.test(url);
}

function VideoPlayerModal({
  url,
  title,
  open,
  onClose,
}: {
  url: string;
  title: string;
  open: boolean;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!open && videoRef.current) {
      videoRef.current.pause();
    }
  }, [open]);

  const filename = url.split("/").pop() ?? url;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Video Preview</DialogTitle>
          <DialogDescription className="font-mono text-xs truncate">{title}</DialogDescription>
        </DialogHeader>
        <video
          ref={videoRef}
          src={url}
          controls
          autoPlay
          className="w-full max-h-[80vh] rounded-lg bg-black"
        />
      </DialogContent>
    </Dialog>
  );
}

function MediaThumb({ src, alt, className }: { src: string; alt: string; className?: string }) {
  if (!src) {
    return (
      <div className={`${className ?? ""} bg-slate-800 flex items-center justify-center`}>
        <FileVideo className="w-8 h-8 text-slate-500" />
      </div>
    );
  }
  if (isVideoUrl(src)) {
    return (
      <video
        src={src}
        preload="metadata"
        muted
        className={className}
        onLoadedData={(e) => {
          // Seek to 1s for a better thumbnail frame
          try {
            const el = e.currentTarget;
            if (el.duration > 1) el.currentTime = 1;
          } catch {
            // Some video formats don't support seeking
          }
        }}
      />
    );
  }
  return <img src={src} alt={alt} className={className} />;
}

function VideoCard({ video, stageKey, onPlay }: { video: VideoPreview; stageKey: string; onPlay?: (url: string, title: string) => void }) {
  const handleClick = () => {
    if (onPlay && video.thumbnail && isVideoUrl(video.thumbnail)) {
      onPlay(video.thumbnail, video.title);
    }
  };

  return (
    <div
      className="flex-shrink-0 w-40 rounded-xl overflow-hidden bg-slate-900 shadow-sm hover:shadow-md transition-shadow group cursor-pointer border border-slate-200"
      onClick={handleClick}
    >
      <div className="relative w-full" style={{ aspectRatio: "9/16" }}>
        <MediaThumb
          src={video.thumbnail}
          alt={video.title}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
        />
        <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
          <div className="w-10 h-10 rounded-full bg-white/90 flex items-center justify-center shadow-lg">
            <Play className="w-4 h-4 text-slate-900 ml-0.5" fill="currentColor" />
          </div>
        </div>
        {video.duration && (
          <div className="absolute bottom-1.5 right-1.5 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded font-mono">
            {video.duration}
          </div>
        )}
        {stageKey === "vlm_scoring" && video.score !== undefined && (
          <div className={`absolute top-1.5 right-1.5 ${scoreColor(video.score)} text-white text-xs px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5`}>
            <Star className="w-2.5 h-2.5" fill="currentColor" />
            {video.score}
          </div>
        )}
        {stageKey === "review" && (
          <div className={`absolute top-1.5 left-1.5 text-white text-xs px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5 ${video.approved ? "bg-green-600" : "bg-slate-500"}`}>
            {video.approved ? <CheckCheck className="w-2.5 h-2.5" /> : <XIcon className="w-2.5 h-2.5" />}
            {video.approved ? "OK" : "Skip"}
          </div>
        )}
        {stageKey === "trend_ingestion" && video.platform && (
          <div className="absolute top-1.5 left-1.5 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded">
            {video.platform}
          </div>
        )}
        {(stageKey === "download" || stageKey === "candidate_filter") && video.resolution && (
          <div className="absolute top-1.5 left-1.5 bg-purple-600/80 text-white text-xs px-1.5 py-0.5 rounded font-mono">
            {video.resolution}
          </div>
        )}
      </div>
      <div className="p-2">
        <p className="text-white text-xs truncate leading-tight">{video.title}</p>
        {stageKey === "review" && video.approved && video.prompt && (
          <p className="text-slate-400 text-xs line-clamp-3 mt-0.5 italic leading-snug">"{video.prompt}"</p>
        )}
      </div>
    </div>
  );
}

function GenerationVideoCard({ video, onPlay }: { video: VideoPreview; onPlay?: (url: string, title: string) => void }) {
  const steps = video.generation_steps;
  const stepColorByIdx = ["bg-slate-600", "bg-blue-600", "bg-purple-600", "bg-green-600"];
  const stepThumbs = steps
    ? [
        { thumb: steps.raw, label: "Raw", idx: 0 },
        { thumb: steps.refined, label: "Refined", idx: 1 },
        { thumb: steps.upscaled, label: "Upscaled", idx: 2 },
        { thumb: steps.postprocessed, label: "Post", idx: 3 },
      ].filter((s) => s.thumb != null && s.thumb !== "")
    : [];

  return (
    <div className="flex-shrink-0 w-72 rounded-xl overflow-hidden border border-slate-200 bg-slate-900 shadow-sm hover:shadow-md transition-shadow">
      <div className="grid grid-cols-2 gap-0.5 bg-slate-700 p-0.5">
        {stepThumbs.map((step) => (
          <div
            key={step.idx}
            className="relative group cursor-pointer overflow-hidden"
            style={{ aspectRatio: "9/16" }}
            onClick={() => {
              if (onPlay && step.thumb && isVideoUrl(step.thumb)) {
                onPlay(step.thumb, `${video.title} - ${step.label}`);
              }
            }}
          >
            <img src={step.thumb} alt={step.label} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
            <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <div className="w-7 h-7 rounded-full bg-white/90 flex items-center justify-center">
                <Play className="w-3 h-3 text-slate-900 ml-0.5" fill="currentColor" />
              </div>
            </div>
            <div className="absolute bottom-1 left-1/2 -translate-x-1/2">
              <span className={`text-white text-xs px-1.5 py-0.5 rounded font-bold ${stepColorByIdx[step.idx]}`}>
                {step.label}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-center gap-0.5 px-3 py-1 bg-slate-800">
        {stepThumbs.map((step, i) => (
          <div key={step.idx} className="flex items-center gap-0.5">
            <span className={`text-xs font-bold ${
              step.idx === 0 ? "text-slate-400" : step.idx === 1 ? "text-blue-400" : step.idx === 2 ? "text-purple-400" : "text-green-400"
            }`}>{step.label}</span>
            {i < stepThumbs.length - 1 && <ChevronRight className="w-2.5 h-2.5 text-slate-500" />}
          </div>
        ))}
      </div>
      <div className="px-3 pb-2.5">
        <p className="text-white text-xs truncate">{video.title}</p>
        {video.duration && (
          <p className="text-slate-400 text-xs font-mono mt-0.5">{video.duration}</p>
        )}
      </div>
    </div>
  );
}

// --- Custom details renderers per stage type ---

function formatViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function StageDetails({ details, onPlay }: { details: Record<string, unknown>; onPlay?: (url: string, title: string) => void }) {
  const type = details._type as string | undefined;

  if (type === "ingestion") return <IngestionDetails details={details} />;
  if (type === "download") return <DownloadDetails details={details} />;
  if (type === "filter") return <FilterDetails details={details} />;
  if (type === "vlm") return <VlmDetails details={details} onPlay={onPlay} />;
  if (type === "review") return <ReviewSummaryDetails details={details} />;
  if (type === "generation") return <GenerationSummaryDetails details={details} />;

  // Fallback: generic key-value
  return (
    <div className="bg-slate-50 rounded-lg p-4 mb-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {Object.entries(details)
          .filter(([k]) => !k.startsWith("_"))
          .map(([k, v]) => (
            <div key={k} className="flex justify-between items-center text-sm">
              <span className="text-slate-600 capitalize">{k.replace(/_/g, " ")}:</span>
              <span className="font-semibold text-slate-800">
                {Array.isArray(v) ? v.join(", ") : String(v)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

function IngestionDetails({ details }: { details: Record<string, unknown> }) {
  const sources = details.sources as Array<{ platform: string; source: string }> ?? [];
  const ingested = details.ingested as Array<{
    video_url: string; caption: string; views: number; likes: number;
    hashtags: string[]; platform: string;
  }> ?? [];

  return (
    <div className="space-y-4 mb-4">
      {/* Source badges */}
      <div className="flex flex-wrap gap-2">
        {sources.map((s, i) => (
          <Badge key={i} variant="outline" className="text-sm gap-1.5 py-1">
            <TrendingUp className="w-3 h-3" />
            {s.platform}
            <span className="text-slate-400">via</span>
            <span className="font-semibold">{s.source}</span>
          </Badge>
        ))}
      </div>

      {/* Ingested items table */}
      {ingested.length > 0 && (
        <div className="bg-slate-50 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-100">
                <th className="text-left px-4 py-2.5 font-semibold text-slate-700">Video</th>
                <th className="text-right px-4 py-2.5 font-semibold text-slate-700 w-24">Views</th>
                <th className="text-right px-4 py-2.5 font-semibold text-slate-700 w-24">Likes</th>
                <th className="text-left px-4 py-2.5 font-semibold text-slate-700 w-48">Hashtags</th>
              </tr>
            </thead>
            <tbody>
              {ingested.map((item, i) => (
                <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/50">
                  <td className="px-4 py-3">
                    <div className="flex items-start gap-2 min-w-0">
                      <Badge variant="secondary" className="text-xs flex-shrink-0">{item.platform}</Badge>
                      <div className="min-w-0">
                        <p className="text-slate-800 truncate max-w-md">{item.caption || "Untitled"}</p>
                        <a
                          href={item.video_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-blue-500 hover:underline flex items-center gap-1 mt-0.5"
                        >
                          <ExternalLink className="w-3 h-3" />
                          Source
                        </a>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right font-mono font-semibold text-slate-700">
                    {formatViews(item.views)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-slate-600">
                    {formatViews(item.likes)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {item.hashtags.slice(0, 3).map((h) => (
                        <span key={h} className="inline-flex items-center gap-0.5 text-xs text-purple-700 bg-purple-50 px-1.5 py-0.5 rounded">
                          <Hash className="w-2.5 h-2.5" />{h}
                        </span>
                      ))}
                      {item.hashtags.length > 3 && (
                        <span className="text-xs text-slate-400">+{item.hashtags.length - 3}</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DownloadDetails({ details }: { details: Record<string, unknown> }) {
  const downloaded = details.downloaded as number ?? 0;
  const failed = details.failed as number ?? 0;
  return (
    <div className="flex gap-3 mb-4">
      <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2.5">
        <CheckCircle2 className="w-4 h-4 text-green-600" />
        <span className="text-sm font-semibold text-green-800">{downloaded} downloaded</span>
      </div>
      {failed > 0 && (
        <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-4 py-2.5">
          <XCircle className="w-4 h-4 text-red-600" />
          <span className="text-sm font-semibold text-red-800">{failed} failed</span>
        </div>
      )}
      {failed === 0 && (
        <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5">
          <CheckCheck className="w-4 h-4 text-slate-500" />
          <span className="text-sm text-slate-600">No failures</span>
        </div>
      )}
    </div>
  );
}

function FilterDetails({ details }: { details: Record<string, unknown> }) {
  const total = details.total_candidates as number ?? 0;
  const passed = details.passed as number ?? 0;
  const rejected = details.rejected as number ?? 0;
  const candidates = details.candidates as Array<{
    file_name: string; resolution: string; duration: string;
    fps: number; quality: number; stability: number; final_score: number;
  }> ?? [];
  const rejectedCandidates = details.rejected_candidates as FilterRejectedCandidate[] ?? [];

  const [showRejected, setShowRejected] = useState(false);

  return (
    <div className="space-y-4 mb-4">
      {/* Summary badges */}
      <div className="flex gap-3 flex-wrap">
        <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5">
          <FileVideo className="w-4 h-4 text-slate-500" />
          <span className="text-sm text-slate-700"><span className="font-semibold">{total}</span> analyzed</span>
        </div>
        <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2.5">
          <CheckCircle2 className="w-4 h-4 text-green-600" />
          <span className="text-sm font-semibold text-green-800">{passed} passed</span>
        </div>
        <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-4 py-2.5">
          <XCircle className="w-4 h-4 text-red-500" />
          <span className="text-sm text-red-700">{rejected} rejected</span>
        </div>
      </div>

      {/* Candidates table */}
      {candidates.length > 0 && (
        <div className="bg-slate-50 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-100">
                <th className="text-left px-4 py-2.5 font-semibold text-slate-700">File</th>
                <th className="text-center px-3 py-2.5 font-semibold text-slate-700">Resolution</th>
                <th className="text-center px-3 py-2.5 font-semibold text-slate-700">Duration</th>
                <th className="text-center px-3 py-2.5 font-semibold text-slate-700">
                  <span className="flex items-center justify-center gap-1"><Gauge className="w-3 h-3" />Quality</span>
                </th>
                <th className="text-center px-3 py-2.5 font-semibold text-slate-700">
                  <span className="flex items-center justify-center gap-1"><Shield className="w-3 h-3" />Stability</span>
                </th>
                <th className="text-center px-3 py-2.5 font-semibold text-slate-700">
                  <span className="flex items-center justify-center gap-1"><Star className="w-3 h-3" />Final</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c, i) => (
                <tr key={i} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-2.5 font-mono text-xs text-slate-700 truncate max-w-[240px]">{c.file_name}</td>
                  <td className="px-3 py-2.5 text-center">
                    <Badge variant="secondary" className="text-xs font-mono">{c.resolution}</Badge>
                  </td>
                  <td className="px-3 py-2.5 text-center font-mono text-xs text-slate-600">{c.duration}</td>
                  <td className="px-3 py-2.5 text-center">
                    <ScoreBar value={c.quality} />
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <ScoreBar value={c.stability} />
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <ScoreBar value={c.final_score} highlight />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Rejected candidates collapsible */}
      {rejectedCandidates.length > 0 && (
        <div>
          <button
            onClick={() => setShowRejected(!showRejected)}
            className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors"
          >
            <ChevronRight className={`w-4 h-4 transition-transform ${showRejected ? "rotate-90" : ""}`} />
            Show {rejectedCandidates.length} rejected candidates
          </button>
          {showRejected && (
            <div className="space-y-2 mt-3">
              {rejectedCandidates.map((rc, i) => (
                <div key={i} className="bg-red-50/50 rounded-lg p-3 border border-red-100">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="secondary" className="font-mono text-xs">{rc.file_name}</Badge>
                    <Badge variant="outline" className="text-xs">{rc.platform}</Badge>
                    {rc.metrics && (
                      <span className="text-xs text-slate-500 font-mono">
                        {rc.metrics.width}x{rc.metrics.height} / {rc.metrics.duration_sec.toFixed(1)}s
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {rc.reject_reasons.map((reason, j) => (
                      <Badge key={j} variant="destructive" className="text-xs font-normal">
                        {reason}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScoreBar({ value, highlight }: { value: number; highlight?: boolean }) {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "bg-green-500" : pct >= 50 ? "bg-yellow-500" : "bg-red-400";
  return (
    <div className="flex items-center gap-2 justify-center">
      <div className="w-16 h-1.5 bg-slate-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs font-mono ${highlight ? "font-bold text-slate-800" : "text-slate-600"}`}>
        {pct}%
      </span>
    </div>
  );
}

// --- VLM Score bar helpers ---

const VLM_SCORE_ENTRIES: Array<{ key: keyof VlmVideoDetail["scores"]; label: string; inverted: boolean }> = [
  { key: "theme_match", label: "Theme Match", inverted: false },
  { key: "persona_fit", label: "Persona Fit", inverted: false },
  { key: "single_subject_clarity", label: "Subject Clarity", inverted: false },
  { key: "face_visibility", label: "Face Visibility", inverted: false },
  { key: "motion_stability", label: "Motion Stability", inverted: false },
  { key: "occlusion_risk", label: "Occlusion Risk", inverted: true },
  { key: "scene_cut_complexity", label: "Cut Complexity", inverted: true },
  { key: "substitution_readiness", label: "Readiness", inverted: false },
];

function vlmScoreBarColor(value: number, inverted: boolean): string {
  if (inverted) {
    if (value <= 4) return "bg-green-500";
    if (value <= 6) return "bg-yellow-500";
    return "bg-red-500";
  }
  if (value >= 7) return "bg-green-500";
  if (value >= 5) return "bg-yellow-500";
  return "bg-red-500";
}

// Compact card for rejected VLM videos — same width as VideoCard, shows thumbnail + rejection info
function VlmRejectedCard({ item, thumbnailUrl, onPlay, selected, onToggleSelect }: {
  item: VlmVideoDetail;
  thumbnailUrl?: string;
  onPlay?: (url: string, title: string) => void;
  selected?: boolean;
  onToggleSelect?: (fileName: string) => void;
}) {
  const readiness = item.scores.substitution_readiness;
  const hasThumb = !!thumbnailUrl;

  const handleClick = () => {
    if (onPlay && thumbnailUrl && isVideoUrl(thumbnailUrl)) {
      onPlay(thumbnailUrl, item.file_name);
    }
  };

  return (
    <div className={`flex-shrink-0 w-40 rounded-xl overflow-hidden bg-slate-900 shadow-sm hover:shadow-md transition-shadow group border ${selected ? "border-blue-400 ring-2 ring-blue-300" : "border-red-300"}`}>
      {/* Checkbox for selection */}
      {onToggleSelect && (
        <div
          className="absolute top-1.5 left-1.5 z-10"
          onClick={(e) => { e.stopPropagation(); onToggleSelect(item.file_name); }}
        >
          <div className={`w-5 h-5 rounded border-2 flex items-center justify-center cursor-pointer ${selected ? "bg-blue-500 border-blue-500" : "bg-white/80 border-slate-400 hover:border-blue-400"}`}>
            {selected && <CheckCheck className="w-3 h-3 text-white" />}
          </div>
        </div>
      )}
      <div className="relative w-full cursor-pointer" style={{ aspectRatio: "9/16" }} onClick={handleClick}>
        {hasThumb ? (
          <MediaThumb src={thumbnailUrl} alt={item.file_name} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
        ) : (
          <div className="w-full h-full bg-slate-800 flex items-center justify-center">
            <FileVideo className="w-8 h-8 text-slate-500" />
          </div>
        )}
        {/* Play overlay */}
        {hasThumb && isVideoUrl(thumbnailUrl!) && (
          <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="w-10 h-10 rounded-full bg-white/90 flex items-center justify-center shadow-lg">
              <Play className="w-4 h-4 text-slate-900 ml-0.5" fill="currentColor" />
            </div>
          </div>
        )}
        {/* Rejected badge */}
        <div className="absolute top-1.5 right-1.5 bg-red-600/90 text-white text-xs px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5">
          <XIcon className="w-2.5 h-2.5" />
          Rej
        </div>
        {/* Readiness score */}
        {readiness !== undefined && (
          <div className={`absolute bottom-1.5 right-1.5 ${vlmScoreBarColor(readiness, false)} text-white text-xs px-1.5 py-0.5 rounded font-bold`}>
            {readiness}/10
          </div>
        )}
      </div>
      <div className="p-2">
        <p className="text-white text-xs truncate leading-tight">{item.file_name}</p>
        {item.reasons.length > 0 && (
          <p className="text-red-300 text-xs truncate mt-0.5 leading-snug" title={item.reasons[0]}>{item.reasons[0]}</p>
        )}
      </div>
    </div>
  );
}

function VlmDetails({ details, onPlay }: { details: Record<string, unknown>; onPlay?: (url: string, title: string) => void }) {
  const accepted = details.accepted as number ?? 0;
  const rejected = details.rejected as number ?? 0;
  const model = details.model as string ?? "";
  const items = details.accepted_items as Array<{
    file_name: string; readiness: number; persona_fit: number;
    confidence: number; reasons: string[];
  }> ?? [];
  const rejectedItems = details.rejected_items as VlmVideoDetail[] ?? [];
  const rejectedVideoUrls = details.rejected_video_urls as Record<string, string> ?? {};

  const [showRejected, setShowRejected] = useState(false);

  return (
    <div className="space-y-4 mb-4">
      {/* Summary */}
      <div className="flex gap-3 flex-wrap items-center">
        <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2.5">
          <ThumbsUp className="w-4 h-4 text-green-600" />
          <span className="text-sm font-semibold text-green-800">{accepted} accepted</span>
        </div>
        {rejected > 0 && (
          <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-4 py-2.5">
            <XCircle className="w-4 h-4 text-red-500" />
            <span className="text-sm text-red-700">{rejected} rejected</span>
          </div>
        )}
        <div className="flex items-center gap-2 bg-purple-50 border border-purple-200 rounded-lg px-4 py-2.5">
          <Brain className="w-4 h-4 text-purple-600" />
          <span className="text-sm text-purple-800">{model}</span>
        </div>
      </div>

      {/* Accepted items detail */}
      {items.map((item, i) => (
        <div key={i} className="bg-slate-50 rounded-lg p-4">
          <div className="flex items-center gap-3 mb-3">
            <Badge variant="secondary" className="font-mono text-xs">{item.file_name}</Badge>
            <div className="flex gap-3">
              <span className="text-xs">
                Readiness: <span className="font-bold text-green-700">{item.readiness}/10</span>
              </span>
              <span className="text-xs">
                Persona Fit: <span className="font-bold text-blue-700">{item.persona_fit}/10</span>
              </span>
              <span className="text-xs">
                Confidence: <span className="font-bold text-purple-700">{Math.round(item.confidence * 100)}%</span>
              </span>
            </div>
          </div>
          <ul className="space-y-1.5">
            {item.reasons.map((reason, j) => (
              <li key={j} className="flex items-start gap-2 text-sm text-slate-700">
                <CheckCircle2 className="w-3.5 h-3.5 text-green-500 mt-0.5 flex-shrink-0" />
                {reason}
              </li>
            ))}
          </ul>
        </div>
      ))}

      {/* Rejected items — horizontal scroll, same style as VideoCard */}
      {rejectedItems.length > 0 && (
        <div>
          <button
            onClick={() => setShowRejected(!showRejected)}
            className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors mb-3"
          >
            <ChevronRight className={`w-4 h-4 transition-transform ${showRejected ? "rotate-90" : ""}`} />
            Show {rejectedItems.length} rejected videos
          </button>
          {showRejected && (
            <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
              {rejectedItems.map((item) => (
                <div key={item.file_name} className="relative">
                  <VlmRejectedCard
                    item={item}
                    thumbnailUrl={rejectedVideoUrls[item.file_name]}
                    onPlay={onPlay}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ReviewSummaryDetails({ details }: { details: Record<string, unknown> }) {
  const total = details.total as number ?? 0;
  const approved = details.approved as number ?? 0;
  const skipped = details.skipped as number ?? 0;

  return (
    <div className="flex gap-3 flex-wrap mb-4">
      <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2.5">
        <ThumbsUp className="w-4 h-4 text-green-600" />
        <span className="text-sm font-semibold text-green-800">{approved} approved</span>
      </div>
      {skipped > 0 && (
        <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5">
          <SkipForward className="w-4 h-4 text-slate-500" />
          <span className="text-sm text-slate-600">{skipped} skipped</span>
        </div>
      )}
      <div className="flex items-center gap-2 bg-purple-50 border border-purple-200 rounded-lg px-4 py-2.5">
        <FileVideo className="w-4 h-4 text-purple-600" />
        <span className="text-sm text-purple-800">{total} total reviewed</span>
      </div>
    </div>
  );
}

function GenerationSummaryDetails({ details }: { details: Record<string, unknown> }) {
  const total = details.total as number ?? 0;
  const completed = details.completed as number ?? 0;
  const failed = details.failed as number ?? 0;
  const lost = details.lost as number ?? 0;
  const running = details.running as number ?? 0;

  return (
    <div className="flex gap-3 flex-wrap mb-4">
      {completed > 0 && (
        <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2.5">
          <CheckCircle2 className="w-4 h-4 text-green-600" />
          <span className="text-sm font-semibold text-green-800">{completed} completed</span>
        </div>
      )}
      {running > 0 && (
        <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-4 py-2.5">
          <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
          <span className="text-sm font-semibold text-blue-800">{running} running</span>
        </div>
      )}
      {failed > 0 && (
        <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-4 py-2.5">
          <XCircle className="w-4 h-4 text-red-500" />
          <span className="text-sm text-red-700">{failed} failed</span>
        </div>
      )}
      {lost > 0 && (
        <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-4 py-2.5">
          <Circle className="w-4 h-4 text-amber-500" />
          <span className="text-sm text-amber-700">{lost} lost (server restarted)</span>
        </div>
      )}
      <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5">
        <FileVideo className="w-4 h-4 text-slate-500" />
        <span className="text-sm text-slate-700">{total} total jobs</span>
      </div>
    </div>
  );
}

function StageVideoPreview({ videos, stageKey, totalCount, onPlay }: { videos: VideoPreview[]; stageKey: string; totalCount?: number; onPlay?: (url: string, title: string) => void }) {
  if (!videos || videos.length === 0) return null;

  const shownCount = videos.length;
  const remaining = totalCount && totalCount > shownCount ? totalCount - shownCount : 0;

  return (
    <div className="mt-4">
      <div className="flex items-center gap-2 mb-3">
        <FileVideo className="w-4 h-4 text-slate-500" />
        <span className="text-sm font-medium text-slate-700">
          Video Preview
          {totalCount && totalCount > shownCount && (
            <span className="ml-1 text-slate-400 font-normal">
              — showing {shownCount} of {totalCount}
            </span>
          )}
        </span>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
        {stageKey === "generation"
          ? videos.map((v) => <GenerationVideoCard key={v.id} video={v} onPlay={onPlay} />)
          : videos.map((v) => <VideoCard key={v.id} video={v} stageKey={stageKey} onPlay={onPlay} />)
        }
        {remaining > 0 && (
          <div className="flex-shrink-0 w-40 rounded-xl border border-dashed border-slate-300 flex flex-col items-center justify-center gap-2 cursor-pointer hover:bg-slate-50 transition-colors" style={{ minHeight: "200px" }}>
            <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center">
              <span className="text-slate-500 text-xs font-bold">+{remaining}</span>
            </div>
            <span className="text-xs text-slate-500 text-center px-2">more videos</span>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Re-run Dialogs ---

function RerunVlmDialog({ open, onClose, influencerId, runId, onJobStarted, influencer }: {
  open: boolean;
  onClose: () => void;
  influencerId: string;
  runId: string;
  onJobStarted: (jobId: string) => void;
  influencer?: InfluencerOut | null;
}) {
  const [theme, setTheme] = useState("influencer channel");
  const [maxVideos, setMaxVideos] = useState(30);
  const [minReadiness, setMinReadiness] = useState(7.0);
  const [minConfidence, setMinConfidence] = useState(0.70);
  const [minPersonaFit, setMinPersonaFit] = useState(6.5);
  const [maxOcclusionRisk, setMaxOcclusionRisk] = useState(6.0);
  const [maxSceneCutComplexity, setMaxSceneCutComplexity] = useState(6.0);
  const [personaDescription, setPersonaDescription] = useState("");
  const [videoRequirements, setVideoRequirements] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pre-fill from influencer when dialog opens
  useEffect(() => {
    if (open && influencer) {
      setPersonaDescription(influencer.description ?? "");
      setVideoRequirements(influencer.video_suggestions_requirement ?? "");
    }
  }, [open, influencer]);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const { job_id } = await api.rerunVlm(runId, {
        influencer_id: influencerId,
        theme,
        max_videos: maxVideos,
        thresholds: {
          min_readiness: minReadiness,
          min_confidence: minConfidence,
          min_persona_fit: minPersonaFit,
          max_occlusion_risk: maxOcclusionRisk,
          max_scene_cut_complexity: maxSceneCutComplexity,
        },
        custom_persona_description: personaDescription || null,
        custom_video_requirements: videoRequirements || null,
      });
      onJobStarted(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Re-run VLM Scoring</DialogTitle>
          <DialogDescription>
            Re-score filtered videos with Gemini AI. Edit the prompt or adjust thresholds.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Prompt editing */}
          <div className="space-y-2">
            <Label htmlFor="vlm-persona-desc" className="text-sm font-medium">
              Persona Description
              <span className="ml-1 text-xs text-slate-400 font-normal">(Gemini uses this to evaluate persona fit)</span>
            </Label>
            <Textarea
              id="vlm-persona-desc"
              value={personaDescription}
              onChange={(e) => setPersonaDescription(e.target.value)}
              placeholder="Describe the influencer's persona, style, and target audience..."
              rows={3}
              className="text-sm"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="vlm-video-req" className="text-sm font-medium">
              Video Requirements
              <span className="ml-1 text-xs text-slate-400 font-normal">(Additional rejection criteria for Gemini)</span>
            </Label>
            <Textarea
              id="vlm-video-req"
              value={videoRequirements}
              onChange={(e) => setVideoRequirements(e.target.value)}
              placeholder="Describe what makes a video unsuitable (e.g. avoid busy backgrounds, multiple people, etc.)..."
              rows={3}
              className="text-sm"
            />
          </div>

          <Separator />

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="vlm-theme">Theme</Label>
              <Input id="vlm-theme" value={theme} onChange={(e) => setTheme(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="vlm-max-videos">Max Videos</Label>
              <Input id="vlm-max-videos" type="number" value={maxVideos} onChange={(e) => setMaxVideos(Number(e.target.value))} min={1} max={200} />
            </div>
          </div>

          <Separator />
          <p className="text-sm font-medium text-slate-700">Thresholds</p>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="vlm-min-readiness" className="text-xs">Min Readiness</Label>
              <Input id="vlm-min-readiness" type="number" value={minReadiness} onChange={(e) => setMinReadiness(Number(e.target.value))} step={0.5} min={0} max={10} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="vlm-min-confidence" className="text-xs">Min Confidence</Label>
              <Input id="vlm-min-confidence" type="number" value={minConfidence} onChange={(e) => setMinConfidence(Number(e.target.value))} step={0.05} min={0} max={1} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="vlm-min-persona-fit" className="text-xs">Min Persona Fit</Label>
              <Input id="vlm-min-persona-fit" type="number" value={minPersonaFit} onChange={(e) => setMinPersonaFit(Number(e.target.value))} step={0.5} min={0} max={10} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="vlm-max-occlusion" className="text-xs">Max Occlusion Risk</Label>
              <Input id="vlm-max-occlusion" type="number" value={maxOcclusionRisk} onChange={(e) => setMaxOcclusionRisk(Number(e.target.value))} step={0.5} min={0} max={10} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="vlm-max-scene-cut" className="text-xs">Max Scene Cut Complexity</Label>
              <Input id="vlm-max-scene-cut" type="number" value={maxSceneCutComplexity} onChange={(e) => setMaxSceneCutComplexity(Number(e.target.value))} step={0.5} min={0} max={10} />
            </div>
          </div>

          <div className="flex items-center gap-2 text-amber-700 text-xs bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            This will invalidate existing review decisions
          </div>

          {error && (
            <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={submitting} className="gap-2">
            {submitting ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> Re-scoring...</>
            ) : (
              <><RotateCcw className="w-4 h-4" /> Re-run VLM</>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RerunFilterDialog({ open, onClose, influencerId, runId, onJobStarted }: {
  open: boolean;
  onClose: () => void;
  influencerId: string;
  runId: string;
  onJobStarted: (jobId: string) => void;
}) {
  const [topK, setTopK] = useState(15);
  const [probeSeconds, setProbeSeconds] = useState(8);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const { job_id } = await api.rerunFilter(runId, {
        influencer_id: influencerId,
        top_k: topK,
        probe_seconds: probeSeconds,
      });
      onJobStarted(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Re-run Filter</DialogTitle>
          <DialogDescription>
            Re-filter downloaded videos with updated parameters.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label htmlFor="filter-top-k">Top K</Label>
            <Input id="filter-top-k" type="number" value={topK} onChange={(e) => setTopK(Number(e.target.value))} min={1} max={200} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="filter-probe-seconds">Probe Seconds</Label>
            <Input id="filter-probe-seconds" type="number" value={probeSeconds} onChange={(e) => setProbeSeconds(Number(e.target.value))} min={3} max={120} />
          </div>

          {error && (
            <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={submitting} className="gap-2">
            {submitting ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> Re-filtering...</>
            ) : (
              <><RotateCcw className="w-4 h-4" /> Re-run Filter</>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// (PromotableRejectedCard replaced by checkbox selection inside ReviewPanel)

// --- Review Panel: interactive review UI for Stage 5 ---

interface ReviewItem {
  file_name: string;
  thumbnail: string;
  approved: boolean;
  prompt: string;
  readiness?: number;
  persona_fit?: number;
  confidence?: number;
  reasons?: string[];
}

function ReviewPanel({
  vlmVideos,
  vlmAccepted,
  influencerId,
  runId,
  onComplete,
  onPlay,
  vlmRejectedItems,
  rejectedVideoUrls,
  initialReview,
}: {
  vlmVideos: VideoPreview[];
  vlmAccepted: VlmAccepted[];
  influencerId: string;
  runId: string;
  onComplete: () => void;
  onPlay?: (url: string, title: string) => void;
  vlmRejectedItems?: VlmVideoDetail[];
  rejectedVideoUrls?: Record<string, string>;
  initialReview?: ReviewVideo[];
}) {
  const buildItems = useCallback((): ReviewItem[] =>
    vlmVideos.map((v) => {
      const vlm = vlmAccepted.find((a) => a.file_name === v.id);
      const existing = initialReview?.find((r) => r.file_name === v.id);
      return {
        file_name: v.id,
        thumbnail: v.thumbnail,
        approved: existing?.approved ?? true,
        prompt: existing?.prompt ?? "",
        readiness: vlm?.readiness,
        persona_fit: vlm?.persona_fit,
        confidence: vlm?.confidence,
        reasons: vlm?.reasons,
      };
    }), [vlmVideos, vlmAccepted, initialReview]);

  const [items, setItems] = useState<ReviewItem[]>(buildItems);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRejected, setSelectedRejected] = useState<Set<string>>(new Set());
  const [regeneratingIdx, setRegeneratingIdx] = useState<number | null>(null);
  const [feedbackIdx, setFeedbackIdx] = useState<number | null>(null);
  const [feedbackText, setFeedbackText] = useState("");

  const handleRegenerate = async (idx: number) => {
    const item = items[idx];
    if (!feedbackText.trim()) return;
    setRegeneratingIdx(idx);
    try {
      const result = await api.regenerateCaption(runId, {
        influencer_id: influencerId,
        file_name: item.file_name,
        current_prompt: item.prompt,
        feedback: feedbackText.trim(),
      });
      setPrompt(idx, result.caption);
      setFeedbackIdx(null);
      setFeedbackText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegeneratingIdx(null);
    }
  };

  const toggleRejectedSelection = (fileName: string) => {
    setSelectedRejected((prev) => {
      const next = new Set(prev);
      if (next.has(fileName)) next.delete(fileName);
      else next.add(fileName);
      return next;
    });
  };

  const addSelectedToReview = () => {
    if (!vlmRejectedItems) return;
    const toAdd = vlmRejectedItems.filter((item) => selectedRejected.has(item.file_name));
    const newItems: ReviewItem[] = toAdd.map((item) => ({
      file_name: item.file_name,
      thumbnail: rejectedVideoUrls?.[item.file_name] ?? "",
      approved: true,
      prompt: "",
      readiness: item.scores.substitution_readiness,
      persona_fit: item.scores.persona_fit,
      confidence: item.confidence,
      reasons: item.reasons,
    }));
    setItems((prev) => {
      // Avoid duplicates
      const existingNames = new Set(prev.map((it) => it.file_name));
      return [...prev, ...newItems.filter((n) => !existingNames.has(n.file_name))];
    });
    setSelectedRejected(new Set());
  };

  // BUG-003: Re-sync items when VLM data changes (e.g., after refetch)
  useEffect(() => {
    setItems((prev) => {
      const next = buildItems();
      // Preserve user edits for items that still exist, but prefer initialReview prompts
      // over stale empty strings (avoids overwriting Gemini captions when rawRun loads async)
      return next.map((n) => {
        const existing = prev.find((p) => p.file_name === n.file_name);
        if (existing) {
          return { ...n, approved: existing.approved, prompt: existing.prompt || n.prompt };
        }
        return n;
      });
    });
  }, [buildItems]);

  const toggle = (idx: number) => {
    setItems((prev) => prev.map((it, i) => (i === idx ? { ...it, approved: !it.approved } : it)));
  };

  const setPrompt = (idx: number, prompt: string) => {
    setItems((prev) => prev.map((it, i) => (i === idx ? { ...it, prompt } : it)));
  };

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const videos: ReviewVideo[] = items.map((it) => ({
        file_name: it.file_name,
        approved: it.approved,
        prompt: it.prompt,
      }));
      await api.submitReview(influencerId, runId, videos);
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const approvedCount = items.filter((it) => it.approved).length;
  const approvedWithoutPrompt = items.filter((it) => it.approved && !it.prompt.trim()).length;
  const canSubmit = approvedCount > 0 && approvedWithoutPrompt === 0 && !submitting;
  const [showRejectedInReview, setShowRejectedInReview] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-600">
          Review each video from VLM scoring. Toggle to approve/skip and add a prompt for generation.
        </p>
        <Badge variant="outline" className="text-sm">
          {approvedCount} / {items.length} approved
        </Badge>
      </div>

      <div className="space-y-3">
        {items.map((item, idx) => (
          <div
            key={item.file_name}
            className={`flex gap-4 p-4 rounded-lg border transition-colors ${
              item.approved ? "bg-green-50/50 border-green-200" : "bg-slate-50 border-slate-200"
            }`}
          >
            {/* Thumbnail */}
            <div
              className="flex-shrink-0 w-24 rounded-lg overflow-hidden cursor-pointer group/thumb"
              style={{ aspectRatio: "9/16" }}
              onClick={() => {
                if (onPlay && item.thumbnail && isVideoUrl(item.thumbnail)) {
                  onPlay(item.thumbnail, item.file_name);
                }
              }}
            >
              <div className="relative w-full h-full">
                <MediaThumb
                  src={item.thumbnail}
                  alt={item.file_name}
                  className="w-full h-full object-cover"
                />
                {item.thumbnail && isVideoUrl(item.thumbnail) && (
                  <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover/thumb:opacity-100 transition-opacity">
                    <div className="w-8 h-8 rounded-full bg-white/90 flex items-center justify-center">
                      <Play className="w-3 h-3 text-slate-900 ml-0.5" fill="currentColor" />
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Info + controls */}
            <div className="flex-1 min-w-0 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-mono text-sm font-medium text-slate-800 truncate">{item.file_name}</p>
                  {item.readiness !== undefined && (
                    <div className="flex gap-3 mt-1">
                      <span className="text-xs text-slate-500">
                        Readiness: <span className="font-bold text-green-700">{item.readiness}/10</span>
                      </span>
                      <span className="text-xs text-slate-500">
                        Persona Fit: <span className="font-bold text-blue-700">{item.persona_fit}/10</span>
                      </span>
                      <span className="text-xs text-slate-500">
                        Confidence: <span className="font-bold text-purple-700">{Math.round((item.confidence ?? 0) * 100)}%</span>
                      </span>
                    </div>
                  )}
                </div>
                <Button
                  variant={item.approved ? "default" : "outline"}
                  size="sm"
                  onClick={() => toggle(idx)}
                  className={item.approved ? "bg-green-600 hover:bg-green-700" : ""}
                >
                  {item.approved ? (
                    <><CheckCheck className="w-3.5 h-3.5 mr-1" /> Approved</>
                  ) : (
                    <><SkipForward className="w-3.5 h-3.5 mr-1" /> Skipped</>
                  )}
                </Button>
              </div>

              {/* VLM reasons */}
              {item.reasons && item.reasons.length > 0 && (
                <ul className="space-y-1">
                  {item.reasons.map((reason, j) => (
                    <li key={j} className="flex items-start gap-1.5 text-xs text-slate-600">
                      <CheckCircle2 className="w-3 h-3 text-green-500 mt-0.5 flex-shrink-0" />
                      {reason}
                    </li>
                  ))}
                </ul>
              )}

              {/* Prompt input */}
              {item.approved && (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Enter generation prompt for this video..."
                      value={item.prompt}
                      onChange={(e) => setPrompt(idx, e.target.value)}
                      className={`flex-1 px-3 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${
                        !item.prompt.trim() ? "border-amber-400 bg-amber-50/50" : "border-slate-300"
                      }`}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="gap-1 text-xs flex-shrink-0"
                      onClick={() => {
                        if (feedbackIdx === idx) {
                          setFeedbackIdx(null);
                          setFeedbackText("");
                        } else {
                          setFeedbackIdx(idx);
                          setFeedbackText("");
                        }
                      }}
                      disabled={regeneratingIdx !== null}
                    >
                      <RefreshCw className="w-3 h-3" />
                      Regenerate
                    </Button>
                  </div>
                  {feedbackIdx === idx && (
                    <div className="flex gap-2 items-start">
                      <input
                        type="text"
                        placeholder="What to change? e.g. 'add more detail about hand movements'"
                        value={feedbackText}
                        onChange={(e) => setFeedbackText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && feedbackText.trim()) handleRegenerate(idx);
                        }}
                        className="flex-1 px-3 py-1.5 text-sm border border-blue-300 rounded-md bg-blue-50/50 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        autoFocus
                      />
                      <Button
                        type="button"
                        size="sm"
                        className="gap-1 text-xs"
                        onClick={() => handleRegenerate(idx)}
                        disabled={!feedbackText.trim() || regeneratingIdx === idx}
                      >
                        {regeneratingIdx === idx ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <Sparkles className="w-3 h-3" />
                        )}
                        Go
                      </Button>
                    </div>
                  )}
                  {!item.prompt.trim() && feedbackIdx !== idx && (
                    <p className="text-xs text-amber-600">Prompt is required for approved videos</p>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg px-4 py-2">
          <AlertCircle className="w-4 h-4" />
          {error}
        </div>
      )}

      <div className="flex items-center justify-end gap-3">
        {approvedWithoutPrompt > 0 && (
          <p className="text-sm text-amber-600">{approvedWithoutPrompt} approved video(s) need a prompt</p>
        )}
        <Button onClick={submit} disabled={!canSubmit} className="gap-2">
          {submitting ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Submitting...</>
          ) : (
            <><Send className="w-4 h-4" /> Submit Review ({approvedCount} approved)</>
          )}
        </Button>
      </div>

      {/* Rejected VLM videos — checkbox selection to add to review */}
      {vlmRejectedItems && vlmRejectedItems.length > 0 && (
        <div className="mt-6 pt-6 border-t border-slate-200">
          <div className="flex items-center justify-between mb-2">
            <button
              onClick={() => setShowRejectedInReview(!showRejectedInReview)}
              className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors"
            >
              <ChevronRight className={`w-4 h-4 transition-transform ${showRejectedInReview ? "rotate-90" : ""}`} />
              Gemini Rejected ({vlmRejectedItems.length}) — select to add to review
            </button>
            {showRejectedInReview && selectedRejected.size > 0 && (
              <Button
                size="sm"
                className="gap-1.5 bg-blue-600 hover:bg-blue-700"
                onClick={addSelectedToReview}
              >
                <ArrowUpCircle className="w-3.5 h-3.5" />
                Add {selectedRejected.size} to Review
              </Button>
            )}
          </div>
          {showRejectedInReview && (
            <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
              {vlmRejectedItems.filter((item) => !items.some((it) => it.file_name === item.file_name)).map((item) => (
                <div key={item.file_name} className="relative flex-shrink-0">
                  <VlmRejectedCard
                    item={item}
                    thumbnailUrl={rejectedVideoUrls?.[item.file_name]}
                    onPlay={onPlay}
                    selected={selectedRejected.has(item.file_name)}
                    onToggleSelect={toggleRejectedSelection}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Generation Panel: controls for Stage 6 ---

function formatProgress(progress: Record<string, unknown> | null | undefined): string {
  if (!progress || typeof progress !== "object") return "Processing...";
  const stage = progress.stage as string | undefined;
  if (!stage) return "Processing...";
  if (stage === "queued") return "Waiting in queue...";
  if (stage === "uploading") return "Uploading files...";
  if (stage === "downloading") return "Downloading results...";
  if (stage === "executing") {
    const node = progress.node as string | undefined;
    return node ? `Executing: ${node}` : "Executing...";
  }
  if (stage === "sampling") {
    const step = typeof progress.step === "number" ? progress.step : undefined;
    const total = typeof progress.total === "number" ? progress.total : undefined;
    if (step !== undefined && total !== undefined) return `Sampling: step ${step}/${total}`;
    const pct = typeof progress.percent === "number" ? progress.percent : undefined;
    if (pct !== undefined) return `Sampling: ${pct}%`;
    return "Sampling...";
  }
  if (stage === "running") return "Running workflow...";
  return stage;
}

function GenerationJobProgress({ jobId }: { jobId: string }) {
  const { job } = useJobSSE(jobId);
  if (!job) return null;

  const progress = job.progress ?? {};
  const stage = progress.stage as string | undefined;
  const step = progress.step as number | undefined;
  const total = progress.total as number | undefined;
  const percent = progress.percent as number | undefined;
  const statusText = formatProgress(progress);

  const barValue =
    step !== undefined && total !== undefined && total > 0
      ? Math.round((step / total) * 100)
      : percent ?? undefined;

  const isDone = job.status === "completed";
  const isFailed = job.status === "failed";

  return (
    <div className="w-full mt-1.5 space-y-1">
      <div className="flex items-center gap-2">
        {isDone ? (
          <CheckCircle2 className="w-3.5 h-3.5 text-green-600 flex-shrink-0" />
        ) : isFailed ? (
          <XCircle className="w-3.5 h-3.5 text-red-600 flex-shrink-0" />
        ) : (
          <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600 flex-shrink-0" />
        )}
        <span className={`text-xs ${isDone ? "text-green-700" : isFailed ? "text-red-600" : "text-slate-600"}`}>
          {isDone ? "Completed" : isFailed ? (job.error ?? "Failed") : (statusText || "Running...")}
        </span>
      </div>
      {!isDone && !isFailed && stage === "sampling" && barValue !== undefined && (
        <Progress value={barValue} className="h-1.5" />
      )}
    </div>
  );
}

function ReviewCompletedList({
  videos,
  vlmAccepted,
  editing,
  initialReview,
  influencerId,
  runId,
  onComplete,
  onCancel,
  onPlay,
  vlmRejectedItems,
  rejectedVideoUrls,
}: {
  videos: VideoPreview[];
  vlmAccepted: VlmAccepted[];
  editing: boolean;
  initialReview?: ReviewVideo[];
  influencerId: string;
  runId: string;
  onComplete: () => void;
  onCancel: () => void;
  onPlay?: (url: string, title: string) => void;
  vlmRejectedItems?: VlmVideoDetail[];
  rejectedVideoUrls?: Record<string, string>;
}) {
  const [items, setItems] = useState(() =>
    videos.map(v => ({
      file_name: v.id,
      thumbnail: v.thumbnail,
      approved: v.approved ?? true,
      prompt: v.prompt ?? "",
    }))
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regeneratingIdx, setRegeneratingIdx] = useState<number | null>(null);
  const [feedbackIdx, setFeedbackIdx] = useState<number | null>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [selectedRejected, setSelectedRejected] = useState<Set<string>>(new Set());
  const [showRejectedInReview, setShowRejectedInReview] = useState(false);

  const toggleRejectedSelection = (fileName: string) => {
    setSelectedRejected((prev) => {
      const next = new Set(prev);
      if (next.has(fileName)) next.delete(fileName);
      else next.add(fileName);
      return next;
    });
  };

  const addSelectedToReview = () => {
    if (!vlmRejectedItems) return;
    const toAdd = vlmRejectedItems.filter((item) => selectedRejected.has(item.file_name));
    const newItems = toAdd.map((item) => ({
      file_name: item.file_name,
      thumbnail: rejectedVideoUrls?.[item.file_name] ?? "",
      approved: true,
      prompt: "",
    }));
    setItems((prev) => {
      const existingNames = new Set(prev.map((it) => it.file_name));
      return [...prev, ...newItems.filter((n) => !existingNames.has(n.file_name))];
    });
    setSelectedRejected(new Set());
  };

  const handleRegenerate = async (idx: number) => {
    const item = items[idx];
    if (!feedbackText.trim()) return;
    setRegeneratingIdx(idx);
    try {
      const result = await api.regenerateCaption(runId, {
        influencer_id: influencerId,
        file_name: item.file_name,
        current_prompt: item.prompt,
        feedback: feedbackText.trim(),
      });
      setItems(prev => prev.map((it, i) => i === idx ? { ...it, prompt: result.caption } : it));
      setFeedbackIdx(null);
      setFeedbackText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegeneratingIdx(null);
    }
  };

  useEffect(() => {
    if (initialReview && initialReview.length > 0) {
      setItems(prev => prev.map(item => {
        const r = initialReview.find(rv => rv.file_name === item.file_name);
        if (r) return { ...item, approved: r.approved, prompt: r.prompt || item.prompt };
        return item;
      }));
    }
  }, [initialReview]);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await api.submitReview(influencerId, runId,
        items.map(it => ({ file_name: it.file_name, approved: it.approved, prompt: it.prompt }))
      );
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const approvedWithoutPrompt = items.filter(it => it.approved && !it.prompt.trim()).length;

  return (
    <div className="mt-3">
      {editing && (
        <div className="flex items-center justify-between mb-4 px-1">
          <p className="text-sm font-medium text-slate-700">Editing review — changes will overwrite the current review</p>
          <div className="flex items-center gap-2">
            {error && <span className="text-xs text-red-600">{error}</span>}
            {approvedWithoutPrompt > 0 && (
              <span className="text-xs text-amber-600">{approvedWithoutPrompt} need prompt</span>
            )}
            <Button variant="outline" size="sm" onClick={onCancel} disabled={submitting}>Cancel</Button>
            <Button
              size="sm"
              onClick={submit}
              disabled={approvedWithoutPrompt > 0 || submitting}
              className="gap-1.5 bg-green-600 hover:bg-green-700 text-white"
            >
              {submitting ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Saving...</> : "Save Review"}
            </Button>
          </div>
        </div>
      )}
      <div className="space-y-3">
        {items.map((item, idx) => {
          const vlm = vlmAccepted.find(a => a.file_name === item.file_name);
          return (
            <div
              key={item.file_name}
              className={`flex gap-4 rounded-xl border p-3 bg-white ${editing ? "border-blue-200" : "border-slate-200"}`}
            >
              {/* Thumbnail */}
              <div
                className="w-28 flex-shrink-0 rounded-lg overflow-hidden cursor-pointer bg-slate-900 group relative"
                style={{ aspectRatio: "9/16" }}
                onClick={() => item.thumbnail && isVideoUrl(item.thumbnail) && onPlay?.(item.thumbnail, item.file_name)}
              >
                <MediaThumb src={item.thumbnail} alt={item.file_name} className="w-full h-full object-cover" />
                <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                  <div className="w-8 h-8 rounded-full bg-white/90 flex items-center justify-center shadow-lg">
                    <Play className="w-3.5 h-3.5 text-slate-900 ml-0.5" fill="currentColor" />
                  </div>
                </div>
              </div>
              {/* Info */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-900 truncate">{item.file_name}</p>
                {vlm && (
                  <div className="flex gap-4 text-xs mt-1 text-slate-500">
                    <span>Readiness: <span className="text-green-600 font-semibold">{vlm.readiness}/10</span></span>
                    <span>Persona Fit: <span className="text-blue-600 font-semibold">{vlm.persona_fit}/10</span></span>
                    <span>Confidence: <span className="text-purple-600 font-semibold">{Math.round((vlm.confidence ?? 0) * 100)}%</span></span>
                  </div>
                )}
                {vlm?.reasons && vlm.reasons.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {vlm.reasons.slice(0, 5).map((r, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-xs text-slate-600">
                        <CheckCircle2 className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {/* Prompt */}
                <div className="mt-2">
                  {editing ? (
                    <div className="space-y-2">
                      <div className="flex gap-2">
                        <input
                          type="text"
                          placeholder="Enter generation prompt for this video..."
                          value={item.prompt}
                          onChange={e => setItems(prev => prev.map((it, i) => i === idx ? { ...it, prompt: e.target.value } : it))}
                          className={`flex-1 px-3 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${
                            !item.prompt.trim() && item.approved ? "border-amber-400 bg-amber-50/50" : "border-slate-300"
                          }`}
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="gap-1 text-xs flex-shrink-0"
                          onClick={() => {
                            if (feedbackIdx === idx) {
                              setFeedbackIdx(null);
                              setFeedbackText("");
                            } else {
                              setFeedbackIdx(idx);
                              setFeedbackText("");
                            }
                          }}
                          disabled={regeneratingIdx !== null}
                        >
                          <RefreshCw className="w-3 h-3" />
                          Regenerate
                        </Button>
                      </div>
                      {feedbackIdx === idx && (
                        <div className="flex gap-2 items-start">
                          <input
                            type="text"
                            placeholder="What to change? e.g. 'include person description, more detail about poses'"
                            value={feedbackText}
                            onChange={e => setFeedbackText(e.target.value)}
                            onKeyDown={e => { if (e.key === "Enter" && feedbackText.trim()) handleRegenerate(idx); }}
                            className="flex-1 px-3 py-1.5 text-sm border border-blue-300 rounded-md bg-blue-50/50 focus:outline-none focus:ring-2 focus:ring-blue-500"
                            autoFocus
                          />
                          <Button
                            type="button"
                            size="sm"
                            className="gap-1 text-xs"
                            onClick={() => handleRegenerate(idx)}
                            disabled={!feedbackText.trim() || regeneratingIdx === idx}
                          >
                            {regeneratingIdx === idx ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Sparkles className="w-3 h-3" />
                            )}
                            Go
                          </Button>
                        </div>
                      )}
                      {!item.prompt.trim() && item.approved && feedbackIdx !== idx && (
                        <p className="text-xs text-amber-600">Prompt is required for approved videos</p>
                      )}
                    </div>
                  ) : (
                    <div className={`px-3 py-1.5 rounded-md text-sm border ${item.prompt ? "bg-slate-50 border-slate-100 text-slate-700 italic" : "border-transparent text-amber-500"}`}>
                      {item.prompt ? `"${item.prompt}"` : "No prompt"}
                    </div>
                  )}
                </div>
              </div>
              {/* Approved status / toggle */}
              <div className="flex-shrink-0 flex items-start pt-0.5">
                {editing ? (
                  <button
                    onClick={() => setItems(prev => prev.map((it, i) => i === idx ? { ...it, approved: !it.approved } : it))}
                    className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
                      item.approved ? "bg-green-600 hover:bg-green-700 text-white" : "bg-slate-200 hover:bg-slate-300 text-slate-600"
                    }`}
                  >
                    {item.approved ? <CheckCheck className="w-4 h-4" /> : <XIcon className="w-4 h-4" />}
                    {item.approved ? "Approved" : "Skipped"}
                  </button>
                ) : (
                  <div className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold ${
                    item.approved ? "bg-green-600 text-white" : "bg-slate-200 text-slate-600"
                  }`}>
                    {item.approved ? <CheckCheck className="w-4 h-4" /> : <XIcon className="w-4 h-4" />}
                    {item.approved ? "Approved" : "Skipped"}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Rejected VLM videos — select to add to review (only in edit mode) */}
      {editing && vlmRejectedItems && vlmRejectedItems.length > 0 && (
        <div className="mt-6 pt-6 border-t border-slate-200">
          <div className="flex items-center justify-between mb-2">
            <button
              onClick={() => setShowRejectedInReview(!showRejectedInReview)}
              className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors"
            >
              <ChevronRight className={`w-4 h-4 transition-transform ${showRejectedInReview ? "rotate-90" : ""}`} />
              Gemini Rejected ({vlmRejectedItems.filter((item) => !items.some((it) => it.file_name === item.file_name)).length}) — select to add to review
            </button>
            {showRejectedInReview && selectedRejected.size > 0 && (
              <Button
                size="sm"
                className="gap-1.5 bg-blue-600 hover:bg-blue-700"
                onClick={addSelectedToReview}
              >
                <ArrowUpCircle className="w-3.5 h-3.5" />
                Add {selectedRejected.size} to Review
              </Button>
            )}
          </div>
          {showRejectedInReview && (
            <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
              {vlmRejectedItems.filter((item) => !items.some((it) => it.file_name === item.file_name)).map((item) => (
                <div key={item.file_name} className="relative flex-shrink-0">
                  <VlmRejectedCard
                    item={item}
                    thumbnailUrl={rejectedVideoUrls?.[item.file_name]}
                    onPlay={onPlay}
                    selected={selectedRejected.has(item.file_name)}
                    onToggleSelect={toggleRejectedSelection}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GenerationPanel({
  reviewVideos,
  influencerId,
  selectedVideoUrls,
  onJobStarted,
  existingJobs,
  onPlayVideo,
}: {
  reviewVideos: ReviewVideo[];
  influencerId: string;
  selectedVideoUrls: Record<string, string>;
  onJobStarted: () => void;
  existingJobs?: GenerationJob[];
  onPlayVideo?: (url: string, title: string) => void;
}) {
  const [serverState, setServerState] = useState<"unknown" | "checking" | "offline" | "starting" | "running">("unknown");
  const [generatingIdx, setGeneratingIdx] = useState<number | null>(null);
  const [videoJobs, setVideoJobs] = useState<Record<string, string>>({});

  // Sync videoJobs from existingJobs (DB) whenever they change
  useEffect(() => {
    const fromDb: Record<string, string> = {};
    for (const job of existingJobs ?? []) {
      fromDb[job.file_name] = job.job_id;
    }
    setVideoJobs((prev) => {
      // Merge: keep locally-submitted job IDs (newer), add DB ones we don't have yet
      const merged = { ...fromDb };
      for (const [fname, jid] of Object.entries(prev)) {
        // Local job ID takes priority (just submitted, may not be in DB yet)
        if (jid) merged[fname] = jid;
      }
      return merged;
    });
  }, [existingJobs]);
  const [error, setError] = useState<string | null>(null);
  const [serverJobId, setServerJobId] = useState<string | null>(null);
  const [serverId, setServerId] = useState<string | null>(null);
  const [serverCost, setServerCost] = useState<number | null>(null);
  const [autoShutdown, setAutoShutdown] = useState(false);
  const [allocation, setAllocation] = useState<AllocationInfo | null>(null);
  const [shuttingDown, setShuttingDown] = useState(false);
  const [alignReference, setAlignReference] = useState(true);
  // QA review state: job_id → { status, result }
  const [qaReviews, setQaReviews] = useState<Record<string, { qa_status: string; qa_result?: QaResult }>>({});

  // Seed QA state from existing jobs
  useEffect(() => {
    const initial: Record<string, { qa_status: string; qa_result?: QaResult }> = {};
    for (const job of existingJobs ?? []) {
      if (job.qa_status) {
        initial[job.job_id] = { qa_status: job.qa_status, qa_result: job.qa_result };
      }
    }
    setQaReviews((prev) => ({ ...initial, ...prev }));
  }, [existingJobs]);

  // Subscribe to QA review SSE events
  useEffect(() => {
    return subscribe("qa_review", (data: unknown) => {
      const d = data as { job_id: string; qa_status: string; qa_result?: QaResult };
      setQaReviews((prev) => ({
        ...prev,
        [d.job_id]: { qa_status: d.qa_status, qa_result: d.qa_result },
      }));
    });
  }, []);

  const approvedVideos = reviewVideos.filter((v) => v.approved);
  const { job: serverJob } = useJobSSE(serverJobId);

  // When server startup job completes, update state
  if (serverJob?.status === "completed" && serverState === "starting") {
    setServerState("running");
    setServerJobId(null);
    // Refresh allocation info
    api.getAllocationInfo(influencerId).then(setAllocation).catch((err) => {
      console.warn("Failed to refresh allocation info:", err);
    });
  }

  // Auto-check server status on mount
  useEffect(() => {
    api.getAllocationInfo(influencerId).then((info) => {
      setAllocation(info);
      if (info.server_id) {
        setServerId(info.server_id);
      }
    }).catch((err) => console.warn("Failed to get allocation info:", err));

    api.serverStatus(influencerId).then((s) => {
      if (s.server_id) setServerId(s.server_id);
      if (s.dph_total) setServerCost(s.dph_total);
      if (s.auto_shutdown !== undefined) setAutoShutdown(s.auto_shutdown);
      if (s.startup_job_id && s.startup_job_status && ["pending", "running"].includes(s.startup_job_status)) {
        setServerState("starting");
        setServerJobId(s.startup_job_id);
      } else if (s.status === "running") {
        setServerState("running");
      } else {
        setServerState("offline");
      }
    }).catch(() => setServerState("offline"));
  }, [influencerId]);

  const checkAndStartServer = async () => {
    setError(null);
    setServerState("checking");
    try {
      const status = await api.serverStatus(influencerId);
      if (status.status === "running") {
        setServerState("running");
        if (status.server_id) setServerId(status.server_id);
        if (status.dph_total) setServerCost(status.dph_total);
      } else {
        setServerState("starting");
        const { job_id, server_id } = await api.serverUp("wan_animate", influencerId);
        setServerJobId(job_id);
        setServerId(server_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setServerState("offline");
    }
  };

  const handleShutdown = async () => {
    if (!serverId) return;
    setShuttingDown(true);
    setError(null);
    try {
      await api.shutdownServer(serverId);
      setServerState("offline");
      setServerId(null);
      setServerCost(null);
      setAllocation(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setShuttingDown(false);
    }
  };

  const handleAutoShutdownToggle = async () => {
    if (!serverId) return;
    const newVal = !autoShutdown;
    try {
      await api.setAutoShutdown(serverId, newVal);
      setAutoShutdown(newVal);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const runGeneration = async (video: ReviewVideo, idx: number) => {
    setGeneratingIdx(idx);
    setError(null);
    try {
      const videoPath = selectedVideoUrls[video.file_name] || video.file_name;
      const { job_id } = await api.startGeneration({
        influencer_id: influencerId,
        reference_video: videoPath,
        prompt: video.prompt,
        align_reference: alignReference,
      });
      setVideoJobs((prev) => ({ ...prev, [video.file_name]: job_id }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGeneratingIdx(null);
    }
  };

  const runAll = async () => {
    for (let i = 0; i < approvedVideos.length; i++) {
      const video = approvedVideos[i];
      const existingJob = existingJobs?.filter((j) => j.file_name === video.file_name).pop();
      if (existingJob && ["completed", "running", "pending"].includes(existingJob.status ?? "")) {
        continue;
      }
      await runGeneration(video, i);
    }
    onJobStarted();
  };

  return (
    <div className="space-y-4">
      {/* Server status */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border ${
          serverState === "running"
            ? "bg-green-50 border-green-200"
            : serverState === "starting" || serverState === "checking"
              ? "bg-blue-50 border-blue-200"
              : "bg-slate-50 border-slate-200"
        }`}>
          <Server className={`w-4 h-4 ${
            serverState === "running" ? "text-green-600" :
            serverState === "starting" ? "text-blue-600" : "text-slate-500"
          }`} />
          <span className="text-sm font-medium">
            {serverState === "unknown" && "GPU Server: Check status to begin"}
            {serverState === "checking" && "Checking server..."}
            {serverState === "offline" && "GPU Server: Offline"}
            {serverState === "starting" && "GPU Server: Starting up..."}
            {serverState === "running" && "GPU Server: Running"}
          </span>
          {serverState === "starting" && <Loader2 className="w-4 h-4 animate-spin text-blue-600" />}
          {serverState === "running" && serverCost !== null && (
            <span className="text-xs text-green-700 ml-1">(${serverCost.toFixed(3)}/hr)</span>
          )}
        </div>

        {(serverState === "unknown" || serverState === "offline") && (
          <Button onClick={checkAndStartServer} variant="outline" size="sm" className="gap-2">
            <Zap className="w-3.5 h-3.5" />
            {serverState === "offline" ? "Start Server" : "Check & Start"}
          </Button>
        )}

        {serverState === "running" && serverId && (
          <Button
            onClick={handleShutdown}
            variant="outline"
            size="sm"
            className="gap-2 text-red-600 border-red-200 hover:bg-red-50"
            disabled={shuttingDown}
          >
            {shuttingDown ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Power className="w-3.5 h-3.5" />
            )}
            Shut Down Server
          </Button>
        )}
      </div>

      {/* Auto-shutdown toggle */}
      {serverState === "running" && serverId && (
        <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={autoShutdown}
            onChange={handleAutoShutdownToggle}
            className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          />
          Auto-shutdown after generation completes
        </label>
      )}

      {/* Server busy message */}
      {allocation && allocation.has_own_server && allocation.server_busy && serverState === "running" && (
        <div className="flex items-center gap-2 text-amber-700 text-sm bg-amber-50 border border-amber-200 rounded-lg px-4 py-2">
          <AlertCircle className="w-4 h-4" />
          Server is busy with {allocation.active_jobs} active job{allocation.active_jobs !== 1 ? "s" : ""}.
          {allocation.can_borrow && " A free server is available to borrow."}
        </div>
      )}

      {/* Approved videos for generation */}
      <>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <p className="text-sm text-slate-600">
              {approvedVideos.length} approved videos ready for generation
            </p>
            <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={alignReference}
                onChange={() => setAlignReference(!alignReference)}
                className="rounded border-slate-300"
              />
              Align reference to video
            </label>
          </div>
          <Button onClick={runAll} disabled={generatingIdx !== null || serverState !== "running"} className="gap-2">
            <Zap className="w-4 h-4" />
            Generate All
          </Button>
        </div>

          <div className="space-y-2">
            {approvedVideos.map((video, idx) => {
              const jobId = videoJobs[video.file_name];
              const isGenerating = generatingIdx === idx;
              const existingJob = existingJobs?.filter((j) => j.file_name === video.file_name).pop();
              // Determine status: DB status is canonical, local submission is "running"
              const jobStatus = existingJob?.status || (jobId ? "running" : undefined);
              const isCompletedJob = jobStatus === "completed";
              const isFailedJob = jobStatus === "failed";
              const isLostJob = jobStatus === "lost";
              const isRunningJob = jobStatus === "running" || jobStatus === "pending";

              return (
                <div key={video.file_name} className={`flex items-center gap-3 p-3 rounded-lg border bg-white ${
                  isCompletedJob ? "border-green-200" : isFailedJob ? "border-red-200" : isLostJob ? "border-amber-200" : "border-slate-200"
                }`}>
                  <FileVideo className={`w-4 h-4 flex-shrink-0 ${
                    isCompletedJob ? "text-green-600" : isFailedJob ? "text-red-500" : isLostJob ? "text-amber-500" : "text-purple-600"
                  }`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-mono truncate">{video.file_name}</p>
                    {video.prompt && (
                      <p className="text-xs text-slate-500 truncate italic">"{video.prompt}"</p>
                    )}
                    {existingJob?.aligned_image_url && (
                      <div className="flex items-center gap-1.5 mt-1">
                        <span className="text-[10px] text-slate-400">Aligned ref:</span>
                        <img
                          src={existingJob.aligned_image_url}
                          alt="Aligned reference"
                          className="w-10 h-14 rounded border border-slate-200 object-cover"
                        />
                      </div>
                    )}
                    {isRunningJob && <GenerationJobProgress jobId={existingJob?.job_id || jobId} />}
                    {isCompletedJob && existingJob?.outputs && existingJob.outputs.length > 0 && (
                      <div className="flex gap-2 mt-2">
                        {existingJob.outputs.map((out: { url: string; name: string }, oi: number) => (
                          <div
                            key={oi}
                            className="relative w-16 h-28 rounded overflow-hidden bg-slate-900 cursor-pointer group border border-slate-200 hover:border-green-400 transition-colors"
                            onClick={() => onPlayVideo?.(out.url, `${out.name} — ${video.file_name}`)}
                          >
                            <video src={out.url} preload="metadata" muted className="w-full h-full object-cover" onLoadedData={(e) => { try { if (e.currentTarget.duration > 1) e.currentTarget.currentTime = 1; } catch { /* some formats don't support seeking */ } }} />
                            <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                              <Play className="w-4 h-4 text-white" fill="white" />
                            </div>
                            <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[9px] text-center py-0.5 capitalize">{out.name}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    {/* QA Review result */}
                    {(() => {
                      const qa = qaReviews[existingJob?.job_id || jobId];
                      if (!qa) return null;
                      if (qa.qa_status === "pending") {
                        return (
                          <div className="flex items-center gap-1.5 mt-2 text-xs text-blue-600">
                            <Loader2 className="w-3 h-3 animate-spin" />
                            QA Review in progress...
                          </div>
                        );
                      }
                      if (qa.qa_status === "failed") {
                        return (
                          <div className="flex items-center gap-1.5 mt-2 text-xs text-red-500">
                            <XCircle className="w-3 h-3" />
                            QA Review failed{qa.qa_result?.error ? `: ${qa.qa_result.error}` : ""}
                          </div>
                        );
                      }
                      if (qa.qa_status === "completed" && qa.qa_result) {
                        const r = qa.qa_result;
                        const badgeCls = r.verdict === "pass"
                          ? "bg-green-100 text-green-800 border-green-200"
                          : r.verdict === "fail"
                            ? "bg-red-100 text-red-800 border-red-200"
                            : "bg-amber-100 text-amber-800 border-amber-200";
                        return (
                          <div className="mt-2 space-y-1">
                            <div className="flex items-center gap-2">
                              <Badge className={`text-[10px] px-1.5 py-0 ${badgeCls}`}>
                                {r.verdict === "pass" ? <CheckCircle2 className="w-2.5 h-2.5 mr-0.5" /> : r.verdict === "fail" ? <XCircle className="w-2.5 h-2.5 mr-0.5" /> : <AlertCircle className="w-2.5 h-2.5 mr-0.5" />}
                                QA: {r.verdict?.toUpperCase()}
                              </Badge>
                              <span className="text-[10px] text-slate-500">Score: {r.score?.toFixed(1)}/10</span>
                            </div>
                            {r.summary && (
                              <p className="text-[10px] text-slate-500 italic">{r.summary}</p>
                            )}
                            {r.issues && r.issues.length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {r.issues.map((issue, ii) => (
                                  <span key={ii} className="text-[9px] px-1.5 py-0.5 rounded bg-red-50 text-red-700 border border-red-100">
                                    {issue}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      }
                      return null;
                    })()}
                  </div>
                  {isCompletedJob ? (
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <Badge className="bg-green-100 text-green-800 border-green-200">
                        <CheckCircle2 className="w-3 h-3 mr-1" /> Done
                      </Badge>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-slate-500 border-slate-200 hover:bg-slate-50"
                        onClick={() => runGeneration(video, idx)}
                        disabled={generatingIdx !== null || serverState !== "running"}
                      >
                        <RefreshCw className="w-3.5 h-3.5 mr-1" /> Re-run
                      </Button>
                    </div>
                  ) : isFailedJob ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-red-600 border-red-200 hover:bg-red-50"
                      onClick={() => runGeneration(video, idx)}
                      disabled={generatingIdx !== null || serverState !== "running"}
                    >
                      <XCircle className="w-3.5 h-3.5 mr-1" /> Retry
                    </Button>
                  ) : isLostJob ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-amber-600 border-amber-200 hover:bg-amber-50"
                      onClick={() => runGeneration(video, idx)}
                      disabled={generatingIdx !== null || serverState !== "running"}
                      title="Status unavailable (server restarted)"
                    >
                      <Circle className="w-3.5 h-3.5 mr-1" /> Retry
                    </Button>
                  ) : isRunningJob ? (
                    <Badge className="bg-blue-100 text-blue-800 border-blue-200 flex-shrink-0">
                      <Loader2 className="w-3 h-3 animate-spin mr-1" /> Running
                    </Badge>
                  ) : isGenerating ? (
                    <Badge className="bg-blue-100 text-blue-800 border-blue-200 flex-shrink-0">
                      <Loader2 className="w-3 h-3 animate-spin mr-1" /> Submitting...
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => runGeneration(video, idx)}
                      disabled={generatingIdx !== null || serverState !== "running"}
                    >
                      Generate
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
      </>

      {error && (
        <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg px-4 py-2">
          <AlertCircle className="w-4 h-4" />
          {error}
        </div>
      )}
    </div>
  );
}

export default function TaskDetailPage() {
  const { avatarId, runId } = useParams();
  const { data: task, loading: loadingTask, refetch: refetchTask } = usePipelineRun(avatarId, runId);
  const { data: rawRun, refetch: refetchRaw } = useRawPipelineRun(avatarId, runId);
  const { data: influencer, loading: loadingInf } = useInfluencer(avatarId);

  const refetchAll = useCallback(() => {
    refetchTask();
    refetchRaw();
  }, [refetchTask, refetchRaw]);

  const [playingVideo, setPlayingVideo] = useState<{ url: string; title: string } | null>(null);
  const [showVlmRerunDialog, setShowVlmRerunDialog] = useState(false);
  const [showFilterRerunDialog, setShowFilterRerunDialog] = useState(false);
  const [rerunJobId, setRerunJobId] = useState<string | null>(null);
  const [editingReview, setEditingReview] = useState(false);

  const handlePlayVideo = useCallback((url: string, title: string) => {
    setPlayingVideo({ url, title });
  }, []);

  // Track re-run job progress
  const rerunJob = useJobSSE(rerunJobId);
  useEffect(() => {
    if (rerunJob.isComplete) {
      setRerunJobId(null);
      refetchTask();
      refetchRaw();
    }
  }, [rerunJob.isComplete, refetchTask, refetchRaw]);

  if (loadingTask || loadingInf) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
        <div className="container mx-auto px-4 py-8 max-w-6xl">
          <Skeleton className="h-8 w-40 mb-6" />
          <Card className="mb-8">
            <CardHeader>
              <Skeleton className="h-8 w-64 mb-4" />
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-2 w-full mt-4" />
            </CardHeader>
          </Card>
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="mb-6">
              <CardHeader>
                <Skeleton className="h-6 w-48" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-20 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (!task || !influencer) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <h2 className="text-2xl font-bold mb-4">Task not found</h2>
          <Link to="/">
            <Button>
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to Home
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  const completedStages = Object.values(task.stages).filter(
    (stage) => stage.status === "completed"
  ).length;
  const totalStages = Object.keys(task.stages).length;
  const progressPercentage = (completedStages / totalStages) * 100;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 overflow-x-hidden">
      <div className="container mx-auto px-4 py-8 max-w-6xl">
        <Link to={`/avatar/${influencer.influencer_id}`}>
          <Button variant="ghost" className="mb-6">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to {influencer.name}
          </Button>
        </Link>

        {/* Task Header */}
        <Card className="mb-8">
          <CardHeader>
            <div className="flex items-start justify-between mb-4">
              <div>
                <CardTitle className="text-3xl mb-2">Run {task.id}</CardTitle>
                <CardDescription className="flex items-center gap-2 text-base">
                  <Clock className="w-4 h-4" />
                  Created: {new Date(task.created_at).toLocaleString()}
                </CardDescription>
              </div>
              <Badge className={`${getStatusColor(task.status)} text-base px-4 py-1`}>
                {task.status}
              </Badge>
            </div>

            <div className="flex items-center gap-4 mb-4">
              <div className="w-16 h-16 rounded-lg overflow-hidden bg-gradient-to-br from-purple-100 to-pink-100 flex-shrink-0">
                <ImageWithFallback
                  src={influencer.profile_image_url ?? ""}
                  alt={influencer.name}
                  className="w-full h-full object-cover"
                />
              </div>
              <div>
                <div className="font-semibold text-lg">{influencer.name}</div>
                <div className="text-sm text-slate-600">@{influencer.influencer_id}</div>
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-600">Pipeline Progress</span>
                <span className="font-semibold">
                  {completedStages} of {totalStages} stages completed
                </span>
              </div>
              <Progress value={progressPercentage} className="h-2" />
            </div>
          </CardHeader>
        </Card>

        {/* Stage Details */}
        <div className="mb-6">
          <h2 className="text-2xl font-bold mb-2">Stage Results</h2>
          <p className="text-slate-600 mb-4">
            Detailed breakdown of each pipeline stage
          </p>
          <Separator />
        </div>

        <div className="space-y-6">
          {Object.entries(task.stages).map(([key, stage], index) => {
            const Icon = stageIcons[key as keyof typeof stageIcons];
            const isLast = index === Object.keys(task.stages).length - 1;

            return (
              <div key={key} className="relative">
                <Card className={`${stage.status === "in-progress" ? "border-blue-300 shadow-lg" : stage.status === "lost" ? "border-amber-300" : ""}`}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-4">
                        <div className={`w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0 ${
                          stage.status === "completed" ? "bg-green-100" :
                          stage.status === "in-progress" ? "bg-blue-100" :
                          stage.status === "failed" ? "bg-red-100" :
                          stage.status === "lost" ? "bg-amber-100" :
                          "bg-slate-100"
                        }`}>
                          <Icon className={`w-6 h-6 ${
                            stage.status === "completed" ? "text-green-600" :
                            stage.status === "in-progress" ? "text-blue-600" :
                            stage.status === "failed" ? "text-red-600" :
                            stage.status === "lost" ? "text-amber-500" :
                            "text-slate-400"
                          }`} />
                        </div>
                        <div>
                          <CardTitle className="text-xl mb-1">
                            Stage {index + 1}: {stageTitles[key as keyof typeof stageTitles]}
                          </CardTitle>
                          <CardDescription>
                            {stageDescriptions[key as keyof typeof stageDescriptions]}
                          </CardDescription>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {/* Re-run buttons */}
                        {key === "vlm_scoring" && stage.status === "completed" && !rerunJobId && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1.5 text-xs"
                            onClick={() => setShowVlmRerunDialog(true)}
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                            Re-score
                          </Button>
                        )}
                        {key === "candidate_filter" && stage.status === "completed" && !rerunJobId && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1.5 text-xs"
                            onClick={() => setShowFilterRerunDialog(true)}
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                            Re-filter
                          </Button>
                        )}
                        {/* Re-run progress indicator */}
                        {rerunJobId && (key === "vlm_scoring" || key === "candidate_filter") && (
                          <div className="flex items-center gap-1.5 text-xs text-blue-600">
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            Re-running...
                          </div>
                        )}
                        {key === "review" && stage.status === "completed" && task.stages.vlm_scoring.status === "completed" && !editingReview && (
                          <Button variant="outline" size="sm" className="gap-1.5 text-xs" onClick={() => setEditingReview(true)}>
                            <Pencil className="w-3.5 h-3.5" />
                            Edit Review
                          </Button>
                        )}
                        {getStatusIcon(stage.status, "w-6 h-6")}
                      </div>
                    </div>
                  </CardHeader>

                  <CardContent>
                    {stage.status !== "pending" && (
                      <>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                          {stage.items_count !== undefined && (
                            <div className="bg-slate-50 rounded-lg p-4">
                              <div className="flex items-center gap-2 mb-1">
                                <FileVideo className="w-4 h-4 text-purple-600" />
                                <div className="text-xs font-medium text-slate-600">Items</div>
                              </div>
                              <div className="text-2xl font-bold text-purple-600">
                                {stage.items_count}
                              </div>
                            </div>
                          )}
                          {stage.duration && (
                            <div className="bg-slate-50 rounded-lg p-4">
                              <div className="flex items-center gap-2 mb-1">
                                <Clock className="w-4 h-4 text-blue-600" />
                                <div className="text-xs font-medium text-slate-600">Duration</div>
                              </div>
                              <div className="text-2xl font-bold text-blue-600">
                                {stage.duration}
                              </div>
                            </div>
                          )}
                        </div>

                        {stage.details && (
                          <StageDetails details={stage.details} onPlay={handlePlayVideo} />
                        )}

                        {stage.videos && stage.videos.length > 0 && key !== "review" && (
                          <StageVideoPreview
                            videos={stage.videos}
                            stageKey={key}
                            totalCount={stage.items_count}
                            onPlay={handlePlayVideo}
                          />
                        )}
                      </>
                    )}

                    {/* Review panel: show interactive UI when VLM is done but review is pending */}
                    {key === "review" && stage.status === "pending" && task.stages.vlm_scoring.status === "completed" && (
                      <ReviewPanel
                        vlmVideos={task.stages.vlm_scoring.videos ?? []}
                        vlmAccepted={(task.stages.vlm_scoring.details?.accepted_items as VlmAccepted[]) ?? []}
                        influencerId={avatarId!}
                        runId={runId!}
                        onComplete={refetchAll}
                        onPlay={handlePlayVideo}
                        vlmRejectedItems={(task.stages.vlm_scoring.details?.rejected_items as VlmVideoDetail[]) ?? []}
                        rejectedVideoUrls={(task.stages.vlm_scoring.details?.rejected_video_urls as Record<string, string>) ?? {}}
                      />
                    )}

                    {/* Completed review: video list with prompts (read-only or editable inline) */}
                    {key === "review" && stage.status === "completed" && stage.videos && stage.videos.length > 0 && (
                      <ReviewCompletedList
                        videos={stage.videos}
                        vlmAccepted={(task.stages.vlm_scoring.details?.accepted_items as VlmAccepted[]) ?? []}
                        editing={editingReview}
                        initialReview={rawRun?.review?.videos}
                        influencerId={avatarId!}
                        runId={runId!}
                        onComplete={() => { setEditingReview(false); refetchAll(); }}
                        onCancel={() => setEditingReview(false)}
                        onPlay={handlePlayVideo}
                        vlmRejectedItems={(task.stages.vlm_scoring.details?.rejected_items as VlmVideoDetail[]) ?? []}
                        rejectedVideoUrls={(task.stages.vlm_scoring.details?.rejected_video_urls as Record<string, string>) ?? {}}
                      />
                    )}

                    {/* Generation panel: show controls when review is completed */}
                    {key === "generation" && task.stages.review.status === "completed" && rawRun?.review?.videos && (
                      <GenerationPanel
                        reviewVideos={rawRun.review.videos}
                        influencerId={avatarId!}
                        selectedVideoUrls={
                          Object.fromEntries(
                            (rawRun?.platforms ?? []).flatMap((p) => {
                              const dir = p.selected_dir || "";
                              return (p.selected_videos ?? []).map((v) => [
                                v.file_name,
                                dir ? `${dir}/${v.file_name}` : v.file_name,
                              ]);
                            })
                          )
                        }
                        onJobStarted={refetchAll}
                        existingJobs={rawRun?.generation?.jobs}
                        onPlayVideo={handlePlayVideo}
                      />
                    )}

                    {stage.status === "pending" && !(
                      (key === "review" && task.stages.vlm_scoring.status === "completed") ||
                      (key === "generation" && task.stages.review.status === "completed")
                    ) && (
                      <div className="bg-slate-50 rounded-lg p-6 text-center">
                        <AlertCircle className="w-8 h-8 text-slate-400 mx-auto mb-2" />
                        <p className="text-slate-500">
                          {key === "review"
                            ? "Review will be available after VLM scoring completes"
                            : key === "generation"
                              ? "Generation will start after review is complete"
                              : "This stage is pending and will start after previous stages complete"}
                        </p>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {!isLast && (
                  <div className="flex justify-center py-2">
                    <div className="w-0.5 h-6 bg-slate-300"></div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {playingVideo && (
        <VideoPlayerModal
          url={playingVideo.url}
          title={playingVideo.title}
          open={true}
          onClose={() => setPlayingVideo(null)}
        />
      )}

      <RerunVlmDialog
        open={showVlmRerunDialog}
        onClose={() => setShowVlmRerunDialog(false)}
        influencerId={avatarId!}
        runId={runId!}
        onJobStarted={(id) => { setRerunJobId(id); setShowVlmRerunDialog(false); }}
        influencer={influencer}
      />
      <RerunFilterDialog
        open={showFilterRerunDialog}
        onClose={() => setShowFilterRerunDialog(false)}
        influencerId={avatarId!}
        runId={runId!}
        onJobStarted={(id) => { setRerunJobId(id); setShowFilterRerunDialog(false); }}
      />
    </div>
  );
}
