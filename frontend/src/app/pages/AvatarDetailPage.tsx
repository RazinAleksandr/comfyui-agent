import { useState, useCallback, useEffect } from "react";
import { Link, useParams } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../components/ui/dialog";
import { useInfluencer, usePipelineRuns, useJobPoller } from "../api/hooks";
import { api } from "../api/client";
import { ImageWithFallback } from "../components/figma/ImageWithFallback";
import type { Task } from "../api/types";
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
} from "lucide-react";
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
  trend_ingestion: "TikTok custom adapter searches hashtags and collects video metadata",
  download: "Downloads video files via yt-dlp into pipeline directory",
  candidate_filter: "Deterministic pre-filtering using ffprobe for quality checks",
  vlm_scoring: "Gemini AI scores videos on 8 criteria for persona match",
  review: "Human review via Telegram bot for final approval",
  generation: "ComfyUI's Wan 2.2 Animate workflow generates final content",
};

function getStatusIcon(status: string) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="w-5 h-5 text-green-600" />;
    case "in-progress":
      return <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />;
    case "failed":
      return <XCircle className="w-5 h-5 text-red-600" />;
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
    default:
      return "bg-slate-100 text-slate-600";
  }
}

export default function AvatarDetailPage() {
  const { avatarId } = useParams();
  const { data: influencer, loading: loadingInf } = useInfluencer(avatarId);
  const { data: tasks, loading: loadingTasks, refetch: refetchTasks } = usePipelineRuns(avatarId);

  const [pipelineDialogOpen, setPipelineDialogOpen] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [completedJobStatus, setCompletedJobStatus] = useState<string | null>(null);
  const { job, isComplete: jobDone } = useJobPoller(activeJobId);

  // On mount, restore any active pipeline job for this influencer
  useEffect(() => {
    if (!avatarId) return;
    api.activeJobs("pipeline", avatarId).then((jobs) => {
      if (jobs.length > 0) {
        setActiveJobId(jobs[0].job_id);
      }
    }).catch(() => {});
  }, [avatarId]);

  // When job completes, show result briefly, refetch, then clear
  useEffect(() => {
    if (jobDone && activeJobId && job) {
      setCompletedJobStatus(job.status === "failed" ? "failed" : "completed");
      refetchTasks();
      const timer = setTimeout(() => {
        setActiveJobId(null);
        setCompletedJobStatus(null);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [jobDone, activeJobId, job, refetchTasks]);

  if (loadingInf) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
        <div className="container mx-auto px-4 py-8">
          <Skeleton className="h-8 w-40 mb-6" />
          <Card className="mb-8">
            <CardHeader>
              <div className="flex gap-6">
                <Skeleton className="w-32 h-32 rounded-lg" />
                <div className="flex-1 space-y-3">
                  <Skeleton className="h-8 w-64" />
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-3/4" />
                </div>
              </div>
            </CardHeader>
          </Card>
        </div>
      </div>
    );
  }

  if (!influencer) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <h2 className="text-2xl font-bold mb-4">Avatar not found</h2>
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

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="container mx-auto px-4 py-8">
        <Link to="/">
          <Button variant="ghost" className="mb-6">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to Avatars
          </Button>
        </Link>

        {/* Avatar Profile Section */}
        <Card className="mb-8">
          <CardHeader>
            <div className="flex flex-col md:flex-row gap-6">
              <div className="w-32 h-32 rounded-lg overflow-hidden flex-shrink-0 bg-gradient-to-br from-purple-100 to-pink-100">
                <ImageWithFallback
                  src={influencer.profile_image_url ?? ""}
                  alt={influencer.name}
                  className="w-full h-full object-cover"
                />
              </div>
              <div className="flex-1">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <CardTitle className="text-3xl mb-2">{influencer.name}</CardTitle>
                    <Badge variant="outline" className="mb-3">
                      @{influencer.influencer_id}
                    </Badge>
                  </div>
                </div>
                <CardDescription className="text-base mb-4">
                  {influencer.description}
                </CardDescription>
                <div className="flex flex-wrap gap-2">
                  {influencer.hashtags?.map((tag) => (
                    <Badge key={tag} variant="secondary">
                      #{tag}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {influencer.video_suggestions_requirement && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                <h4 className="font-semibold text-amber-900 mb-2 flex items-center gap-2">
                  <Eye className="w-4 h-4" />
                  Video Selection Requirements
                </h4>
                <p className="text-sm text-amber-800">
                  {influencer.video_suggestions_requirement}
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Content Generation Pipeline */}
        <div className="mb-6">
          <h2 className="text-2xl font-bold mb-2">Content Generation Pipeline</h2>
          <p className="text-slate-600 mb-4">
            Six-stage AI workflow from trend discovery to final content
          </p>
          <Separator />
        </div>

        {/* Pipeline Stages Overview */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
          {Object.entries(stageIcons).map(([key, Icon]) => (
            <Card key={key} className="hover:shadow-md transition-shadow">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-purple-100 flex items-center justify-center">
                    <Icon className="w-5 h-5 text-purple-600" />
                  </div>
                  <CardTitle className="text-lg">
                    {stageTitles[key as keyof typeof stageTitles]}
                  </CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-slate-600">
                  {stageDescriptions[key as keyof typeof stageDescriptions]}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Generation Tasks */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-bold mb-2">Generation Tasks</h2>
            <p className="text-slate-600">
              Click on a task to view detailed stage results
            </p>
          </div>
          <Dialog open={pipelineDialogOpen} onOpenChange={setPipelineDialogOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2">
                <Play className="w-4 h-4" />
                Start Pipeline
              </Button>
            </DialogTrigger>
            <StartPipelineDialog
              influencerId={influencer.influencer_id}
              defaultHashtags={influencer.hashtags ?? []}
              onStarted={(jobId) => {
                setPipelineDialogOpen(false);
                setActiveJobId(jobId);
              }}
            />
          </Dialog>
        </div>
        <Separator className="mb-6" />

        {/* Active job banner */}
        {(activeJobId || completedJobStatus) && (
          <div className={`mb-4 p-4 rounded-lg border flex items-center gap-3 ${
            completedJobStatus === "failed" || job?.status === "failed"
              ? "bg-red-50 border-red-200"
              : completedJobStatus === "completed"
                ? "bg-green-50 border-green-200"
                : "bg-blue-50 border-blue-200"
          }`}>
            {completedJobStatus === "failed" || job?.status === "failed" ? (
              <XCircle className="w-5 h-5 text-red-600" />
            ) : completedJobStatus === "completed" ? (
              <CheckCircle2 className="w-5 h-5 text-green-600" />
            ) : (
              <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />
            )}
            <div>
              <p className={`font-medium ${
                completedJobStatus === "failed" || job?.status === "failed" ? "text-red-900"
                  : completedJobStatus === "completed" ? "text-green-900"
                  : "text-blue-900"
              }`}>
                {completedJobStatus === "failed" || job?.status === "failed"
                  ? "Pipeline failed"
                  : completedJobStatus === "completed"
                    ? "Pipeline completed!"
                    : "Pipeline running..."}
              </p>
              <p className={`text-sm ${
                completedJobStatus === "failed" || job?.status === "failed" ? "text-red-700"
                  : completedJobStatus === "completed" ? "text-green-700"
                  : "text-blue-700"
              }`}>
                {job?.status ?? completedJobStatus}
                {job?.error && `: ${job.error}`}
              </p>
            </div>
          </div>
        )}

        <div className="space-y-4">
          {loadingTasks ? (
            Array.from({ length: 2 }).map((_, i) => (
              <Card key={i}>
                <CardHeader>
                  <Skeleton className="h-6 w-48" />
                  <Skeleton className="h-4 w-32 mt-2" />
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-6 gap-4">
                    {Array.from({ length: 6 }).map((_, j) => (
                      <Skeleton key={j} className="h-20" />
                    ))}
                  </div>
                </CardContent>
              </Card>
            ))
          ) : (
            tasks?.map((task: Task) => (
              <Link key={task.id} to={`/task/${influencer.influencer_id}/${task.id}`}>
                <Card className="hover:shadow-lg transition-all duration-300 hover:-translate-y-0.5 cursor-pointer">
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-4">
                        <div>
                          <CardTitle className="text-xl mb-1">Run {task.id}</CardTitle>
                          <CardDescription className="flex items-center gap-2">
                            <Clock className="w-4 h-4" />
                            {new Date(task.created_at).toLocaleString()}
                          </CardDescription>
                        </div>
                      </div>
                      <Badge className={getStatusColor(task.status)}>
                        {task.status}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                      {Object.entries(task.stages).map(([key, stage]) => {
                        const Icon = stageIcons[key as keyof typeof stageIcons];
                        return (
                          <div key={key} className="flex flex-col items-center text-center p-3 rounded-lg bg-slate-50">
                            <div className="flex items-center gap-2 mb-2">
                              {getStatusIcon(stage.status)}
                              <Icon className="w-4 h-4 text-slate-600" />
                            </div>
                            <div className="text-xs font-medium text-slate-700 mb-1">
                              {stageTitles[key as keyof typeof stageTitles]}
                            </div>
                            {stage.items_count !== undefined && (
                              <div className="text-lg font-bold text-purple-600">
                                {stage.items_count}
                              </div>
                            )}
                            {stage.duration && (
                              <div className="text-xs text-slate-500">
                                {stage.duration}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))
          )}

          {!loadingTasks && (!tasks || tasks.length === 0) && (
            <Card>
              <CardContent className="py-12 text-center">
                <Sparkles className="w-12 h-12 text-slate-300 mx-auto mb-4" />
                <p className="text-slate-500">No generation tasks yet</p>
                <p className="text-sm text-slate-400 mt-1">Start a pipeline to begin</p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function StartPipelineDialog({
  influencerId,
  defaultHashtags,
  onStarted,
}: {
  influencerId: string;
  defaultHashtags: string[];
  onStarted: (jobId: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tiktok, setTiktok] = useState(true);
  const [instagram, setInstagram] = useState(false);

  const handleSubmit = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      setSubmitting(true);
      setError(null);

      const form = new FormData(e.currentTarget);
      const limit = parseInt(form.get("limit") as string) || 10;
      const hashtagsRaw = (form.get("hashtags") as string).trim();
      const hashtags = hashtagsRaw
        ? hashtagsRaw.split(",").map((h) => h.trim().replace(/^#/, ""))
        : undefined;

      const platforms: Record<string, { source: string; limit: number; selector?: { hashtags: string[] } }> = {};
      if (tiktok) {
        platforms.tiktok = { source: "tiktok_custom", limit, ...(hashtags ? { selector: { hashtags } } : {}) };
      }
      if (instagram) {
        platforms.instagram = { source: "instagram_custom", limit, ...(hashtags ? { selector: { hashtags } } : {}) };
      }

      if (Object.keys(platforms).length === 0) {
        setError("Select at least one platform");
        setSubmitting(false);
        return;
      }

      try {
        const { job_id } = await api.startPipeline({
          influencer_id: influencerId,
          platforms,
        });
        onStarted(job_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [influencerId, tiktok, instagram, onStarted],
  );

  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>Start Pipeline</DialogTitle>
        <DialogDescription>
          Run the full trend discovery pipeline for {influencerId}
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit} className="space-y-4">
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
          <Label htmlFor="limit">Limit per platform</Label>
          <Input id="limit" name="limit" type="number" defaultValue={10} min={1} max={200} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="hashtags">Hashtags (comma-separated)</Label>
          <Input id="hashtags" name="hashtags" defaultValue={defaultHashtags.join(", ")} />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <DialogFooter>
          <Button type="submit" disabled={submitting}>
            {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Start Pipeline
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
