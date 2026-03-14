import { Link, useParams } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { useInfluencer, usePipelineRun } from "../api/hooks";
import { ImageWithFallback } from "../components/figma/ImageWithFallback";
import type { VideoPreview } from "../api/types";
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
  review: "Bot sends selected videos to you in Telegram one by one. You watch each and either type a prompt to approve or /skip to skip",
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
          const el = e.currentTarget;
          if (el.duration > 1) el.currentTime = 1;
        }}
      />
    );
  }
  return <img src={src} alt={alt} className={className} />;
}

function VideoCard({ video, stageKey }: { video: VideoPreview; stageKey: string }) {
  return (
    <div className="flex-shrink-0 w-40 rounded-xl overflow-hidden bg-slate-900 shadow-sm hover:shadow-md transition-shadow group cursor-pointer border border-slate-200">
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
          <p className="text-slate-400 text-xs truncate mt-0.5 italic">"{video.prompt}"</p>
        )}
      </div>
    </div>
  );
}

function GenerationVideoCard({ video }: { video: VideoPreview }) {
  const steps = video.generation_steps;
  const stepLabels = ["Raw", "Refined", "Upscaled", "Post"];
  const stepThumbs = steps ? [steps.raw, steps.refined, steps.upscaled, steps.postprocessed] : [];

  return (
    <div className="flex-shrink-0 w-72 rounded-xl overflow-hidden border border-slate-200 bg-slate-900 shadow-sm hover:shadow-md transition-shadow">
      <div className="grid grid-cols-2 gap-0.5 bg-slate-700 p-0.5">
        {stepThumbs.map((thumb, i) => (
          <div key={i} className="relative group cursor-pointer overflow-hidden" style={{ aspectRatio: "9/16" }}>
            <img src={thumb} alt={stepLabels[i]} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
            <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <div className="w-7 h-7 rounded-full bg-white/90 flex items-center justify-center">
                <Play className="w-3 h-3 text-slate-900 ml-0.5" fill="currentColor" />
              </div>
            </div>
            <div className="absolute bottom-1 left-1/2 -translate-x-1/2">
              <span className={`text-white text-xs px-1.5 py-0.5 rounded font-bold ${
                i === 0 ? "bg-slate-600" : i === 1 ? "bg-blue-600" : i === 2 ? "bg-purple-600" : "bg-green-600"
              }`}>
                {stepLabels[i]}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-center gap-0.5 px-3 py-1 bg-slate-800">
        {stepLabels.map((label, i) => (
          <div key={i} className="flex items-center gap-0.5">
            <span className={`text-xs font-bold ${
              i === 0 ? "text-slate-400" : i === 1 ? "text-blue-400" : i === 2 ? "text-purple-400" : "text-green-400"
            }`}>{label}</span>
            {i < 3 && <ChevronRight className="w-2.5 h-2.5 text-slate-500" />}
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

function StageDetails({ details }: { details: Record<string, unknown> }) {
  const type = details._type as string | undefined;

  if (type === "ingestion") return <IngestionDetails details={details} />;
  if (type === "download") return <DownloadDetails details={details} />;
  if (type === "filter") return <FilterDetails details={details} />;
  if (type === "vlm") return <VlmDetails details={details} />;

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
        <div className="bg-slate-50 rounded-lg overflow-hidden">
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
        <div className="bg-slate-50 rounded-lg overflow-hidden">
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

function VlmDetails({ details }: { details: Record<string, unknown> }) {
  const accepted = details.accepted as number ?? 0;
  const rejected = details.rejected as number ?? 0;
  const model = details.model as string ?? "";
  const items = details.accepted_items as Array<{
    file_name: string; readiness: number; persona_fit: number;
    confidence: number; reasons: string[];
  }> ?? [];

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
    </div>
  );
}

function StageVideoPreview({ videos, stageKey, totalCount }: { videos: VideoPreview[]; stageKey: string; totalCount?: number }) {
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
          ? videos.map((v) => <GenerationVideoCard key={v.id} video={v} />)
          : videos.map((v) => <VideoCard key={v.id} video={v} stageKey={stageKey} />)
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

export default function TaskDetailPage() {
  const { avatarId, runId } = useParams();
  const { data: task, loading: loadingTask } = usePipelineRun(avatarId, runId);
  const { data: influencer, loading: loadingInf } = useInfluencer(avatarId);

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
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
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
                <Card className={`${stage.status === "in-progress" ? "border-blue-300 shadow-lg" : ""}`}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-4">
                        <div className={`w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0 ${
                          stage.status === "completed" ? "bg-green-100" :
                          stage.status === "in-progress" ? "bg-blue-100" :
                          stage.status === "failed" ? "bg-red-100" :
                          "bg-slate-100"
                        }`}>
                          <Icon className={`w-6 h-6 ${
                            stage.status === "completed" ? "text-green-600" :
                            stage.status === "in-progress" ? "text-blue-600" :
                            stage.status === "failed" ? "text-red-600" :
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
                      {getStatusIcon(stage.status, "w-6 h-6")}
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
                          <StageDetails details={stage.details} />
                        )}

                        {stage.videos && stage.videos.length > 0 && (
                          <StageVideoPreview
                            videos={stage.videos}
                            stageKey={key}
                            totalCount={stage.items_count}
                          />
                        )}
                      </>
                    )}

                    {stage.status === "pending" && (
                      <div className="bg-slate-50 rounded-lg p-6 text-center">
                        <AlertCircle className="w-8 h-8 text-slate-400 mx-auto mb-2" />
                        <p className="text-slate-500">
                          {key === "review"
                            ? "Awaiting human review via Telegram bot"
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
    </div>
  );
}
