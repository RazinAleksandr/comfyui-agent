import { useState, useEffect } from "react";
import { Link, useParams, useNavigate } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../components/ui/dialog";
import { useInfluencer, usePipelineRuns, useJobSSE } from "../api/hooks";
import { api } from "../api/client";
import { ImageWithFallback } from "../components/figma/ImageWithFallback";
import type { InfluencerOut, JobInfo, Task, GeneratedContentItem } from "../api/types";
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
  Play,
  Pencil,
  Trash2,
  Settings2,
  ExternalLink,
} from "lucide-react";
import { Separator } from "../components/ui/separator";
import { Switch } from "../components/ui/switch";

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
  trend_ingestion: "TikTok custom adapter searches hashtags and collects video metadata",
  download: "Downloads video files via yt-dlp into pipeline directory",
  candidate_filter: "Deterministic pre-filtering using ffprobe for quality checks",
  vlm_scoring: "Gemini AI scores videos on 8 criteria for persona match",
  review: "Human review in web UI — approve or skip videos and add generation prompts",
  generation: "ComfyUI's Wan 2.2 Animate workflow generates final content",
};

function formatViews(views: number): string {
  if (views >= 1_000_000) return `${(views / 1_000_000).toFixed(1)}M`;
  if (views >= 1_000) return `${(views / 1_000).toFixed(1)}K`;
  return String(views);
}

function getStatusIcon(status: string) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="w-5 h-5 text-green-600" />;
    case "in-progress":
      return <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />;
    case "failed":
      return <XCircle className="w-5 h-5 text-red-600" />;
    case "lost":
      return <Circle className="w-5 h-5 text-amber-500" />;
    default:
      return <Circle className="w-5 h-5 text-slate-300" />;
  }
}

function getStatusColor(status: string) {
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-800";
    case "in-progress":
      return "bg-blue-100 text-blue-800";
    case "failed":
      return "bg-red-100 text-red-800";
    case "lost":
      return "bg-amber-100 text-amber-800";
    default:
      return "bg-slate-100 text-slate-600";
  }
}

/* ── Stage color helpers for the pipeline flow ── */

const stageColorClasses: Record<string, { bg: string; text: string }> = {
  trend_ingestion: { bg: "bg-blue-100", text: "text-blue-600" },
  download: { bg: "bg-blue-100", text: "text-blue-600" },
  candidate_filter: { bg: "bg-blue-100", text: "text-blue-600" },
  vlm_scoring: { bg: "bg-blue-100", text: "text-blue-600" },
  review: { bg: "bg-blue-100", text: "text-blue-600" },
  generation: { bg: "bg-blue-100", text: "text-blue-600" },
};

/* ── Stage pill color helper for task cards ── */

function getStagePillColors(status: string) {
  switch (status) {
    case "completed":
      return { bg: "bg-green-50", text: "text-green-700", bold: "text-green-700", dot: "bg-green-500" };
    case "in-progress":
      return { bg: "bg-blue-50", text: "text-blue-700", bold: "text-blue-700", dot: "bg-blue-500" };
    case "failed":
      return { bg: "bg-red-50", text: "text-red-700", bold: "text-red-700", dot: "bg-red-500" };
    default:
      return { bg: "bg-slate-50", text: "text-slate-400", bold: "text-slate-400", dot: "bg-slate-300" };
  }
}

const stageKeysInOrder = ["trend_ingestion", "download", "candidate_filter", "vlm_scoring", "review", "generation"] as const;
const stageShortNames: Record<string, string> = {
  trend_ingestion: "Ingest",
  download: "Download",
  candidate_filter: "Filter",
  vlm_scoring: "VLM",
  review: "Review",
  generation: "Gen",
};

export default function AvatarDetailPage() {
  const { avatarId } = useParams();
  const navigate = useNavigate();
  const { data: influencer, loading: loadingInf, refetch: refetchInfluencer } = useInfluencer(avatarId);
  const { data: tasks, loading: loadingTasks, refetch: refetchTasks } = usePipelineRuns(avatarId);

  const [pipelineDialogOpen, setPipelineDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Pipeline stage config (lifted so cards on page can edit them)
  const [filterTopK, setFilterTopK] = useState(15);
  const [vlmMaxVideos, setVlmMaxVideos] = useState(15);
  const [autoReview, setAutoReview] = useState(true);
  const [tiktok, setTiktok] = useState(true);
  const [instagram, setInstagram] = useState(false);
  const [limit, setLimit] = useState(10);
  const [hashtags, setHashtags] = useState(influencer?.hashtags?.join(", ") ?? "");
  const [defaultSources, setDefaultSources] = useState<Record<string, string>>({ tiktok: "tiktok_custom", instagram: "apify" });
  const [alignReference, setAlignReference] = useState(true);
  const [alignCloseUp, setAlignCloseUp] = useState(false);
  const [settingsDialog, setSettingsDialog] = useState<string | null>(null);
  const [generatedContent, setGeneratedContent] = useState<GeneratedContentItem[]>([]);
  const [loadingContent, setLoadingContent] = useState(true);
  const [playingVideo, setPlayingVideo] = useState<string | null>(null);

  // Load default sources from config
  useEffect(() => {
    api.getParserDefaults().then((d) => setDefaultSources(d.default_sources)).catch(() => {});
  }, []);

  // Sync hashtags when influencer loads
  useEffect(() => {
    if (influencer?.hashtags) setHashtags(influencer.hashtags.join(", "));
  }, [influencer?.hashtags]);

  // Fetch generated content
  useEffect(() => {
    if (!avatarId) return;
    setLoadingContent(true);
    api.getGeneratedContent(avatarId)
      .then(setGeneratedContent)
      .catch(() => setGeneratedContent([]))
      .finally(() => setLoadingContent(false));
  }, [avatarId]);

  const { job: activeJob, isComplete: jobDone } = useJobSSE(activeJobId);

  // On mount, restore any active pipeline job for this influencer
  useEffect(() => {
    if (!avatarId) return;
    api.activeJobs("pipeline", avatarId).then((jobs) => {
      if (jobs.length > 0) {
        setActiveJobId(jobs[0].job_id);
      }
    }).catch((err) => console.warn("Failed to restore active pipeline job:", err));
  }, [avatarId]);

  // When job completes, keep live card visible briefly, then refetch and clear
  useEffect(() => {
    if (jobDone && activeJobId) {
      // Show final state for 2 seconds before switching to the static task card
      const timer = setTimeout(() => {
        refetchTasks();
        // Give refetch time to complete before hiding live card
        setTimeout(() => setActiveJobId(null), 500);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [jobDone, activeJobId, refetchTasks]);

  if (loadingInf) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50/80 via-slate-50 to-blue-100/60">
        {/* Header skeleton */}
        <header className="sticky top-0 z-30 backdrop-blur-md bg-white/70 border-b border-slate-200/60">
          <div className="max-w-7xl mx-auto px-6 h-16 flex items-center gap-4">
            <Skeleton className="w-8 h-8 rounded-lg" />
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-24 ml-4" />
          </div>
        </header>
        <main className="max-w-7xl mx-auto px-6 py-10">
          <div className="flex gap-12">
            <Skeleton className="w-80 h-[420px] rounded-3xl flex-shrink-0" />
            <div className="flex-1 space-y-4 pt-2">
              <Skeleton className="h-6 w-24 rounded-full" />
              <Skeleton className="h-10 w-64" />
              <Skeleton className="h-5 w-full max-w-lg" />
              <Skeleton className="h-5 w-3/4 max-w-lg" />
              <div className="flex gap-2 pt-2">
                <Skeleton className="h-6 w-16 rounded-full" />
                <Skeleton className="h-6 w-16 rounded-full" />
                <Skeleton className="h-6 w-16 rounded-full" />
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  if (!influencer) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50/80 via-slate-50 to-blue-100/60">
        <div className="text-center">
          <h2 className="text-2xl font-bold mb-4 text-slate-900">Avatar not found</h2>
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

  // The newest task is the active one when a pipeline job is running
  const newestTaskId = tasks && tasks.length > 0 ? tasks[0].id : null;

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50/80 via-slate-50 to-blue-100/60">
      {/* ── Frosted Glass Header ── */}
      <header className="sticky top-0 z-30 backdrop-blur-md bg-white/70 border-b border-slate-200/60">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          {/* Left: Brand + breadcrumb */}
          <div className="flex items-center gap-4">
            <Link to="/" className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center shadow-sm">
                <Sparkles className="w-4 h-4 text-white" />
              </div>
              <span className="text-lg font-bold tracking-tight text-slate-900">
                AI Avatar Studio
              </span>
            </Link>
            <div className="flex items-center gap-2 text-sm">
              <Link to="/" className="text-slate-400 hover:text-slate-600 transition-colors font-medium">
                Studio
              </Link>
              <span className="text-slate-300">/</span>
              <span className="text-slate-900 font-medium">
                {influencer.name}
              </span>
            </div>
          </div>

          {/* Right: Edit / Delete */}
          <div className="flex items-center gap-2">
            <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1.5 text-slate-600 border-slate-200">
                  <Pencil className="w-3.5 h-3.5" />
                  Edit
                </Button>
              </DialogTrigger>
              <EditInfluencerDialog
                influencer={influencer}
                onSaved={() => {
                  setEditDialogOpen(false);
                  refetchInfluencer();
                }}
              />
            </Dialog>
            <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1.5 text-red-600 hover:text-red-700 hover:bg-red-50 border-red-200">
                  <Trash2 className="w-3.5 h-3.5" />
                  Delete
                </Button>
              </DialogTrigger>
              <DeleteInfluencerDialog
                influencer={influencer}
                onDeleted={() => {
                  setDeleteDialogOpen(false);
                  navigate("/");
                }}
              />
            </Dialog>
          </div>
        </div>
      </header>

      {/* ── Main Content ── */}
      <main className="max-w-7xl mx-auto px-6 py-10 space-y-12">

        {/* ── Hero Profile Section ── */}
        <section className="flex flex-col md:flex-row gap-10 md:gap-12">
          {/* Large Portrait */}
          <div className="w-72 md:w-80 flex-shrink-0">
            <div className="aspect-[3/4] rounded-3xl overflow-hidden bg-gradient-to-br from-blue-100 via-slate-100 to-blue-50 shadow-lg shadow-slate-200/50">
              <ImageWithFallback
                src={influencer.profile_image_url ?? ""}
                alt={influencer.name}
                className="w-full h-full object-cover"
              />
            </div>
          </div>

          {/* Profile Info */}
          <div className="flex-1 pt-2 space-y-4">
            {/* Handle badge */}
            <span className="inline-flex items-center px-3 py-1 rounded-full bg-blue-50 border border-blue-200/50 text-xs font-medium text-blue-600 tracking-wide">
              @{influencer.influencer_id}
            </span>

            {/* Name */}
            <h1 className="text-4xl font-bold tracking-tight text-slate-900 leading-tight">
              {influencer.name}
            </h1>

            {/* Description */}
            {influencer.description && (
              <p className="text-base text-slate-500 leading-relaxed max-w-xl">
                {influencer.description}
              </p>
            )}

            {/* Hashtags */}
            {influencer.hashtags && influencer.hashtags.length > 0 && (
              <div className="flex flex-wrap gap-2 pt-1">
                {influencer.hashtags.map((tag) => (
                  <span
                    key={tag}
                    className="px-2.5 py-1 rounded-full bg-slate-100 text-xs font-medium text-slate-600"
                  >
                    #{tag}
                  </span>
                ))}
              </div>
            )}

            {/* Info Blocks: Appearance + Video Requirements */}
            <div className="space-y-2 pt-2">
              {influencer.appearance_description && (
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-slate-400" />
                    <h4 className="text-xs tracking-wider text-slate-500 uppercase font-medium">
                      Appearance
                    </h4>
                  </div>
                  <p className="text-sm text-slate-700 leading-relaxed">
                    {influencer.appearance_description}
                  </p>
                </div>
              )}
              {influencer.video_suggestions_requirement && (
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <Eye className="w-3.5 h-3.5 text-slate-400" />
                    <h4 className="text-xs tracking-wider text-slate-500 uppercase font-medium">
                      Video Requirements
                    </h4>
                  </div>
                  <p className="text-sm text-slate-700 leading-relaxed">
                    {influencer.video_suggestions_requirement}
                  </p>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ── Generated Content ── */}
        {loadingContent ? (
          <section>
            <Skeleton className="h-7 w-48 mb-2" />
            <Skeleton className="h-4 w-64 mb-5" />
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="aspect-[9/16] rounded-2xl" />
              ))}
            </div>
          </section>
        ) : (
          <section>
            <div className="mb-5">
              <h2 className="text-xl font-bold text-slate-900 mb-1">Generated Content</h2>
              <p className="text-sm text-slate-500">
                {generatedContent.length} video{generatedContent.length !== 1 ? "s" : ""} generated across all pipeline runs
              </p>
            </div>
            {generatedContent.length > 0 ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                {generatedContent.map((item) => (
                  <div
                    key={`${item.run_id}-${item.file_name}`}
                    className="group relative rounded-2xl overflow-hidden bg-black aspect-[9/16] cursor-pointer shadow-md hover:shadow-xl transition-all duration-300 hover:-translate-y-0.5"
                    onClick={() => setPlayingVideo(item.video_url)}
                  >
                    <video
                      src={item.video_url}
                      className="w-full h-full object-cover"
                      muted
                      preload="metadata"
                      playsInline
                      onMouseEnter={(e) => e.currentTarget.play().catch(() => {})}
                      onMouseLeave={(e) => { e.currentTarget.pause(); e.currentTarget.currentTime = 0; }}
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent opacity-100 group-hover:opacity-100 transition-opacity" />
                    <div className="absolute bottom-0 left-0 right-0 p-3 text-white">
                      <div className="flex items-center gap-1.5 mb-1">
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-white/20 text-white border-0 backdrop-blur-sm">
                          {item.source.platform || "unknown"}
                        </Badge>
                        {item.source.views > 0 && (
                          <span className="text-[10px] text-white/80">
                            {formatViews(item.source.views)} views
                          </span>
                        )}
                      </div>
                      {item.source.caption && (
                        <p className="text-[11px] text-white/70 line-clamp-2 leading-tight">
                          {item.source.caption}
                        </p>
                      )}
                      {item.source.video_url && (
                        <a
                          href={item.source.video_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-[10px] text-white/60 hover:text-white mt-1"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLink className="w-3 h-3" />
                          Original
                        </a>
                      )}
                    </div>
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                      <div className="w-12 h-12 rounded-full bg-white/30 backdrop-blur-sm flex items-center justify-center">
                        <Play className="w-6 h-6 text-white ml-0.5" />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-200 py-12 text-center">
                <Sparkles className="w-10 h-10 text-slate-300 mx-auto mb-3" />
                <p className="text-sm text-slate-400">No generated content yet</p>
              </div>
            )}
          </section>
        )}

        {/* Video Playback Modal */}
        <Dialog open={!!playingVideo} onOpenChange={(open) => !open && setPlayingVideo(null)}>
          <DialogContent className="sm:max-w-3xl p-0 bg-black border-0 overflow-hidden">
            {playingVideo && (
              <video
                src={playingVideo}
                controls
                autoPlay
                className="w-full max-h-[85vh]"
              />
            )}
          </DialogContent>
        </Dialog>

        {/* ── Content Generation Pipeline ── */}
        <section>
          <div className="mb-5">
            <h2 className="text-xl font-bold text-slate-900 mb-1">Content Generation Pipeline</h2>
            <p className="text-sm text-slate-500">
              Six-stage AI workflow from trend discovery to final content
            </p>
          </div>

          {/* Pipeline Stages — 3×2 Grid */}
          <div className="grid grid-cols-3 gap-4">
            {stageKeysInOrder.map((key) => {
              const Icon = stageIcons[key];
              const isConfigurable = key === "trend_ingestion" || key === "candidate_filter" || key === "vlm_scoring" || key === "review" || key === "generation";
              const colors = stageColorClasses[key];

              return (
                <div
                  key={key}
                  className={`rounded-xl border border-slate-200 bg-white p-4 transition-all group ${isConfigurable ? "cursor-pointer hover:shadow-md hover:border-blue-200" : "cursor-default"}`}
                  onClick={isConfigurable ? () => setSettingsDialog(key) : undefined}
                >
                  {/* Top row: icon + name + gear */}
                  <div className="flex items-center gap-3">
                    <div className={`w-12 h-12 rounded-full ${colors.bg} flex items-center justify-center flex-shrink-0`}>
                      <Icon className={`w-5 h-5 ${colors.text}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="font-semibold text-slate-800 text-sm">
                        {stageTitles[key]}
                      </span>
                    </div>
                    {isConfigurable && (
                      <Settings2 className="w-4 h-4 text-slate-300 group-hover:text-blue-400 transition-colors flex-shrink-0" />
                    )}
                  </div>

                  {/* Description */}
                  <p className="text-xs text-slate-500 mt-2 line-clamp-2 leading-relaxed">
                    {stageDescriptions[key]}
                  </p>

                  {/* Config summary */}
                  <div className="text-xs text-slate-600 mt-3 pt-3 border-t border-slate-100">
                    {key === "trend_ingestion" && (
                      <>Platforms: {[tiktok && "TikTok", instagram && "Instagram"].filter(Boolean).join(", ") || "none"} &middot; Limit: {limit}</>
                    )}
                    {key === "download" && "Engine: yt-dlp"}
                    {key === "candidate_filter" && `Top K: ${filterTopK}`}
                    {key === "vlm_scoring" && `Max videos: ${vlmMaxVideos}`}
                    {key === "review" && (autoReview ? "Mode: Auto (AI captions)" : "Mode: Manual")}
                    {key === "generation" && (
                      <>Ref: {alignReference ? "aligned" : "direct"}{alignReference && alignCloseUp ? " + close-up" : ""}</>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Stage settings dialogs */}
        <Dialog open={settingsDialog === "trend_ingestion"} onOpenChange={(open) => !open && setSettingsDialog(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>Trend Ingestion Settings</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Platforms</Label>
                <div className="flex gap-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={tiktok} onChange={(e) => setTiktok(e.target.checked)} className="rounded" />
                    TikTok
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={instagram} onChange={(e) => setInstagram(e.target.checked)} className="rounded" />
                    Instagram
                  </label>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="cfg-limit">Limit per platform</Label>
                <Input id="cfg-limit" type="number" value={limit} onChange={(e) => setLimit(parseInt(e.target.value) || 10)} min={1} max={200} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cfg-hashtags">Hashtags (comma-separated)</Label>
                <Input id="cfg-hashtags" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
              </div>
            </div>
            <DialogFooter>
              <Button onClick={() => setSettingsDialog(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={settingsDialog === "candidate_filter"} onOpenChange={(open) => !open && setSettingsDialog(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>Candidate Filter Settings</DialogTitle>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="cfg-filter-top-k">Top K</Label>
              <Input id="cfg-filter-top-k" type="number" value={filterTopK} onChange={(e) => setFilterTopK(parseInt(e.target.value) || 15)} min={1} max={200} />
            </div>
            <DialogFooter>
              <Button onClick={() => setSettingsDialog(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={settingsDialog === "vlm_scoring"} onOpenChange={(open) => !open && setSettingsDialog(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>VLM Scoring Settings</DialogTitle>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="cfg-vlm-max">Max Videos</Label>
              <Input id="cfg-vlm-max" type="number" value={vlmMaxVideos} onChange={(e) => setVlmMaxVideos(parseInt(e.target.value) || 15)} min={1} max={200} />
            </div>
            <DialogFooter>
              <Button onClick={() => setSettingsDialog(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={settingsDialog === "review"} onOpenChange={(open) => !open && setSettingsDialog(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>Review Settings</DialogTitle>
            </DialogHeader>
            <div className="flex items-center justify-between">
              <Label htmlFor="cfg-auto-review">Auto-review (AI captions)</Label>
              <Switch id="cfg-auto-review" checked={autoReview} onCheckedChange={setAutoReview} />
            </div>
            <DialogFooter>
              <Button onClick={() => setSettingsDialog(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={settingsDialog === "generation"} onOpenChange={(open) => !open && setSettingsDialog(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>Generation Settings</DialogTitle>
            </DialogHeader>
            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="cfg-align-ref">Align reference to video</Label>
                <p className="text-xs text-slate-500 mt-0.5">Generate adapted character image per video before ComfyUI</p>
              </div>
              <Switch id="cfg-align-ref" checked={alignReference} onCheckedChange={setAlignReference} />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="cfg-align-closeup">Close-up alignment</Label>
                <p className="text-xs text-slate-500 mt-0.5">Generate close-up portrait instead of matching video framing</p>
              </div>
              <Switch id="cfg-align-closeup" checked={alignCloseUp} onCheckedChange={setAlignCloseUp} disabled={!alignReference} />
            </div>
            <DialogFooter>
              <Button onClick={() => setSettingsDialog(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* ── Start Pipeline CTA Banner ── */}
        <Dialog open={pipelineDialogOpen} onOpenChange={setPipelineDialogOpen}>
          <DialogTrigger asChild>
            <div className="rounded-2xl bg-gradient-to-r from-blue-600 to-blue-500 text-white px-8 py-5 flex items-center justify-between cursor-pointer hover:from-blue-700 hover:to-blue-600 transition-all shadow-lg shadow-blue-500/20">
              <div className="flex items-center gap-4">
                <Play className="w-7 h-7 flex-shrink-0" />
                <div>
                  <p className="font-bold text-lg">Ready to generate content?</p>
                  <p className="text-blue-100 text-sm mt-0.5">Run the full 6-stage pipeline for {influencer.name}</p>
                </div>
              </div>
              <Button className="bg-white text-blue-600 hover:bg-blue-50 shadow-md font-semibold px-6">
                Start Pipeline
              </Button>
            </div>
          </DialogTrigger>
          <StartPipelineConfirm
            influencerId={influencer.influencer_id}
            tiktok={tiktok}
            instagram={instagram}
            limit={limit}
            hashtags={hashtags}
            filterTopK={filterTopK}
            vlmMaxVideos={vlmMaxVideos}
            autoReview={autoReview}
            alignReference={alignReference}
            alignCloseUp={alignCloseUp}
            defaultSources={defaultSources}
            onStarted={(jobId) => {
              setPipelineDialogOpen(false);
              setActiveJobId(jobId);
            }}
          />
        </Dialog>

        {/* ── Generation Tasks ── */}
        <section>
          <div className="mb-5">
            <h2 className="text-xl font-bold text-slate-900 mb-1">Generation Tasks</h2>
            <p className="text-sm text-slate-500">
              Click on a task to view detailed stage results
            </p>
          </div>

          {activeJobId && activeJob && (
            <LiveTaskCard job={activeJob} isDone={jobDone} />
          )}

          <div className="space-y-3">
            {loadingTasks ? (
              Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="rounded-2xl bg-white border border-slate-200/80 p-5">
                  <div className="flex items-center justify-between">
                    <div className="space-y-2">
                      <Skeleton className="h-5 w-48" />
                      <Skeleton className="h-4 w-32" />
                    </div>
                    <div className="flex gap-2">
                      {Array.from({ length: 6 }).map((_, j) => (
                        <Skeleton key={j} className="h-12 w-16 rounded-lg" />
                      ))}
                    </div>
                    <Skeleton className="h-6 w-20 rounded-lg" />
                  </div>
                </div>
              ))
            ) : (
              tasks?.filter((task: Task) => {
                // Hide the newest task when the live card is showing (avoids duplicate)
                if (activeJobId && !jobDone && task.id === newestTaskId) return false;
                return true;
              }).map((task: Task) => {
                return (
                  <Link key={task.id} to={`/task/${influencer.influencer_id}/${task.id}`}>
                    <div className="rounded-2xl bg-white border border-slate-200/80 shadow-sm hover:shadow-md transition-all duration-300 hover:-translate-y-0.5 cursor-pointer p-5">
                      <div className="flex items-center justify-between gap-4">
                        {/* Left: run info */}
                        <div className="flex-shrink-0">
                          <p className="text-sm font-semibold text-slate-800">
                            Run {task.id}
                          </p>
                          <p className="text-xs text-slate-400 flex items-center gap-1.5 mt-1">
                            <Clock className="w-3 h-3" />
                            {new Date(task.created_at).toLocaleString()}
                          </p>
                        </div>

                        {/* Center: stage pills */}
                        <div className="flex items-center gap-1.5">
                          {Object.entries(task.stages).map(([key, stage]) => {
                            const pillColors = getStagePillColors(stage.status);
                            return (
                              <div
                                key={key}
                                className={`flex flex-col items-center text-center px-3 py-2 rounded-lg ${pillColors.bg} min-w-[4.5rem]`}
                              >
                                <div className="flex items-center gap-1.5">
                                  <span className={`w-1.5 h-1.5 rounded-full ${pillColors.dot} flex-shrink-0`} />
                                  <span className={`text-[10px] font-medium leading-tight ${pillColors.text}`}>
                                    {stageShortNames[key] ?? key}
                                  </span>
                                </div>
                                {stage.items_count !== undefined && (
                                  <span className={`text-base font-bold leading-tight mt-1 text-slate-800`}>
                                    {stage.items_count}
                                  </span>
                                )}
                              </div>
                            );
                          })}
                        </div>

                        {/* Right: status badge */}
                        <Badge className={`${getStatusColor(task.status)} border-0 text-xs`}>
                          {task.status}
                        </Badge>
                      </div>
                    </div>
                  </Link>
                );
              })
            )}

            {!loadingTasks && (!tasks || tasks.length === 0) && (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-white/50 py-12 text-center">
                <Sparkles className="w-10 h-10 text-slate-300 mx-auto mb-3" />
                <p className="text-sm text-slate-400">No generation tasks yet</p>
                <p className="text-xs text-slate-300 mt-1">Start a pipeline to begin</p>
              </div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function LiveTaskCard({ job, isDone }: { job: JobInfo; isDone: boolean }) {
  const progress = job.progress as {
    current_stage?: string;
    stages?: Record<string, { status: string; items?: number }>;
  };
  const stages = progress?.stages ?? {};

  const stageData: Record<string, { status: string; items?: number }> = {
    trend_ingestion: stages.ingestion ?? { status: "pending" },
    download: stages.download ?? { status: "pending" },
    candidate_filter: stages.filter ?? { status: "pending" },
    vlm_scoring: stages.vlm ?? { status: "pending" },
    review: stages.review ?? { status: "pending" },
    generation: { status: "pending" },
  };

  return (
    <div className={`rounded-2xl bg-white border shadow-sm mb-3 p-5 transition-all duration-500 ${
      isDone
        ? "border-green-300 ring-1 ring-green-200"
        : "border-blue-300 ring-1 ring-blue-200"
    }`}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          {isDone ? (
            <CheckCircle2 className="w-5 h-5 text-green-600" />
          ) : (
            <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />
          )}
          <div>
            <p className="text-sm font-semibold text-slate-800">
              {isDone ? "Pipeline Completed" : "Pipeline Running"}
            </p>
            <p className="text-xs text-slate-400 flex items-center gap-1.5 mt-0.5">
              <Clock className="w-3 h-3" />
              {isDone
                ? "Finished — loading results..."
                : progress?.current_stage
                  ? `Stage: ${progress.current_stage}`
                  : "Starting..."}
            </p>
          </div>
        </div>
        <Badge className={`${isDone ? "bg-green-100 text-green-800" : "bg-blue-100 text-blue-800"} border-0 text-xs`}>
          {isDone ? "completed" : "in-progress"}
        </Badge>
      </div>

      <div className="flex items-center gap-1.5">
        {Object.entries(stageData).map(([key, stage]) => {
          const Icon = stageIcons[key as keyof typeof stageIcons];
          const uiStatus = stage.status === "completed" ? "completed" :
            stage.status === "running" ? "in-progress" : "pending";
          const pillColors = getStagePillColors(uiStatus);
          return (
            <div
              key={key}
              className={`flex flex-col items-center text-center px-3 py-2 rounded-lg flex-1 transition-all duration-500 ${pillColors.bg}`}
            >
              <div className="flex items-center gap-1.5 mb-1">
                {getStatusIcon(uiStatus)}
                <Icon className={`w-3.5 h-3.5 transition-colors duration-300 ${
                  uiStatus === "completed" ? "text-green-600" :
                  uiStatus === "in-progress" ? "text-blue-600" :
                  "text-slate-400"
                }`} />
              </div>
              <span className={`text-[10px] font-medium ${pillColors.text}`}>
                {stageShortNames[key] ?? key}
              </span>
              {stage.items !== undefined && (
                <span className={`text-sm font-bold leading-tight ${pillColors.bold}`}>
                  {stage.items}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EditInfluencerDialog({
  influencer,
  onSaved,
}: {
  influencer: InfluencerOut;
  onSaved: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refImage, setRefImage] = useState<File | null>(null);
  const [appearanceDesc, setAppearanceDesc] = useState(influencer.appearance_description ?? "");
  const [generatingAppearance, setGeneratingAppearance] = useState(false);

  const handleGenerateAppearance = async () => {
    setGeneratingAppearance(true);
    setError(null);
    try {
      const result = await api.generateAppearance(influencer.influencer_id);
      setAppearanceDesc(result.appearance_description);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGeneratingAppearance(false);
    }
  };

  const handleSubmit = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSaving(true);
    setError(null);

    const form = new FormData(e.currentTarget);
    const name = (form.get("name") as string).trim();
    const description = (form.get("description") as string).trim();
    const hashtagsRaw = (form.get("hashtags") as string).trim();
    const videoReq = (form.get("video_suggestions_requirement") as string).trim();

    if (!name) {
      setError("Name is required");
      setSaving(false);
      return;
    }

    try {
      await api.upsertInfluencer(influencer.influencer_id, {
        name,
        description: description || undefined,
        hashtags: hashtagsRaw ? hashtagsRaw.split(",").map((h) => h.trim().replace(/^#/, "")) : undefined,
        video_suggestions_requirement: videoReq || undefined,
        appearance_description: appearanceDesc || undefined,
      });

      if (refImage) {
        await api.uploadReferenceImage(influencer.influencer_id, refImage);
      }

      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Edit Avatar</DialogTitle>
        <DialogDescription>
          Update {influencer.name}'s profile
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="edit-name">Name</Label>
          <Input id="edit-name" name="name" defaultValue={influencer.name} required />
        </div>
        <div className="space-y-2">
          <Label htmlFor="edit-description">Description</Label>
          <Textarea id="edit-description" name="description" defaultValue={influencer.description ?? ""} rows={3} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="edit-hashtags">Hashtags (comma-separated)</Label>
          <Input id="edit-hashtags" name="hashtags" defaultValue={influencer.hashtags?.join(", ") ?? ""} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="edit-video-req">Video Selection Requirements</Label>
          <Textarea id="edit-video-req" name="video_suggestions_requirement" defaultValue={influencer.video_suggestions_requirement ?? ""} rows={2} />
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="edit-appearance">Appearance Description</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-1.5 text-xs"
              onClick={handleGenerateAppearance}
              disabled={generatingAppearance || !influencer.reference_image_path}
            >
              {generatingAppearance ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
              Generate with AI
            </Button>
          </div>
          <Textarea
            id="edit-appearance"
            value={appearanceDesc}
            onChange={(e) => setAppearanceDesc(e.target.value)}
            rows={3}
            placeholder="Describe the person's physical appearance for video generation prompts..."
          />
          {!influencer.reference_image_path && (
            <p className="text-xs text-slate-500">Upload a reference image first to use AI generation.</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="edit-ref-image">Reference Image</Label>
          <Input
            id="edit-ref-image"
            type="file"
            accept="image/*"
            onChange={(e) => setRefImage(e.target.files?.[0] ?? null)}
          />
          {influencer.profile_image_url && !refImage && (
            <p className="text-xs text-slate-500">Current image will be kept if no new image is selected.</p>
          )}
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <DialogFooter>
          <Button type="submit" disabled={saving}>
            {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Save Changes
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

function DeleteInfluencerDialog({
  influencer,
  onDeleted,
}: {
  influencer: InfluencerOut;
  onDeleted: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteInfluencer(influencer.influencer_id);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>Delete Avatar</DialogTitle>
        <DialogDescription>
          Are you sure you want to delete {influencer.name}? This will remove all associated data including pipeline runs. This action cannot be undone.
        </DialogDescription>
      </DialogHeader>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <DialogFooter className="gap-2 sm:gap-0">
        <DialogTrigger asChild>
          <Button variant="outline">Cancel</Button>
        </DialogTrigger>
        <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
          {deleting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
          Delete
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function StartPipelineConfirm({
  influencerId,
  tiktok,
  instagram,
  limit,
  hashtags,
  filterTopK,
  vlmMaxVideos,
  autoReview,
  alignReference,
  alignCloseUp,
  defaultSources,
  onStarted,
}: {
  influencerId: string;
  tiktok: boolean;
  instagram: boolean;
  limit: number;
  hashtags: string;
  filterTopK: number;
  vlmMaxVideos: number;
  autoReview: boolean;
  alignReference: boolean;
  alignCloseUp: boolean;
  defaultSources: Record<string, string>;
  onStarted: (jobId: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const platformList = [tiktok && "TikTok", instagram && "Instagram"].filter(Boolean).join(", ");
  const parsedHashtags = hashtags.trim()
    ? hashtags.split(",").map((h) => h.trim().replace(/^#/, "")).filter(Boolean)
    : [];

  const handleRun = async () => {
    if (!tiktok && !instagram) {
      setError("No platforms selected — configure in the Trend Ingestion card");
      return;
    }
    setSubmitting(true);
    setError(null);

    const platforms: Record<string, { source: string; limit: number; selector?: { hashtags: string[] } }> = {};
    if (tiktok) {
      platforms.tiktok = { source: defaultSources.tiktok || "tiktok_custom", limit, ...(parsedHashtags.length ? { selector: { hashtags: parsedHashtags } } : {}) };
    }
    if (instagram) {
      platforms.instagram = { source: defaultSources.instagram || "apify", limit, ...(parsedHashtags.length ? { selector: { hashtags: parsedHashtags } } : {}) };
    }

    try {
      const { job_id } = await api.startPipeline({
        influencer_id: influencerId,
        platforms,
        filter: { top_k: filterTopK },
        vlm: { max_videos: vlmMaxVideos },
        ...(autoReview ? { review: { auto: true } } : {}),
      });
      onStarted(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>Confirm Pipeline Run</DialogTitle>
        <DialogDescription>
          Review settings before starting the pipeline for {influencerId}
        </DialogDescription>
      </DialogHeader>
      <div className="rounded-lg bg-slate-50 border px-4 py-3 text-sm text-slate-600 space-y-2">
        <div className="flex justify-between">
          <span className="text-slate-500">Platforms</span>
          <span className="font-medium text-slate-700">{platformList || "none"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Limit per platform</span>
          <span className="font-medium text-slate-700">{limit}</span>
        </div>
        {parsedHashtags.length > 0 && (
          <div className="flex justify-between">
            <span className="text-slate-500">Hashtags</span>
            <span className="font-medium text-slate-700">{parsedHashtags.map(h => `#${h}`).join(", ")}</span>
          </div>
        )}
        <Separator />
        <div className="flex justify-between">
          <span className="text-slate-500">Filter — Top K</span>
          <span className="font-medium text-slate-700">{filterTopK}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">VLM — Max videos</span>
          <span className="font-medium text-slate-700">{vlmMaxVideos}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Auto-review</span>
          <span className="font-medium text-slate-700">{autoReview ? "yes" : "no"}</span>
        </div>
        <Separator />
        <div className="flex justify-between">
          <span className="text-slate-500">Align reference</span>
          <span className="font-medium text-slate-700">{alignReference ? "yes" : "no"}</span>
        </div>
        {alignReference && (
          <div className="flex justify-between">
            <span className="text-slate-500">Close-up alignment</span>
            <span className="font-medium text-slate-700">{alignCloseUp ? "yes" : "no"}</span>
          </div>
        )}
      </div>
      <p className="text-xs text-slate-400">To change settings, close this dialog and click on the stage cards above.</p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <DialogFooter>
        <Button onClick={handleRun} disabled={submitting} className="gap-2">
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Confirm and Run
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
