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
import type { InfluencerOut, JobInfo, Task } from "../api/types";
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
  const [settingsDialog, setSettingsDialog] = useState<string | null>(null);

  // Load default sources from config
  useEffect(() => {
    api.getParserDefaults().then((d) => setDefaultSources(d.default_sources)).catch(() => {});
  }, []);

  // Sync hashtags when influencer loads
  useEffect(() => {
    if (influencer?.hashtags) setHashtags(influencer.hashtags.join(", "));
  }, [influencer?.hashtags]);

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

  // The newest task is the active one when a pipeline job is running
  const newestTaskId = tasks && tasks.length > 0 ? tasks[0].id : null;

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
                  <div className="flex gap-2">
                    <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
                      <DialogTrigger asChild>
                        <Button variant="outline" size="sm" className="gap-1.5">
                          <Pencil className="w-4 h-4" />
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
                          <Trash2 className="w-4 h-4" />
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
            <div className="space-y-3">
              {influencer.appearance_description && (
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
                  <h4 className="font-semibold text-purple-900 mb-2 flex items-center gap-2">
                    <Sparkles className="w-4 h-4" />
                    Appearance Description
                  </h4>
                  <p className="text-sm text-purple-800">
                    {influencer.appearance_description}
                  </p>
                </div>
              )}
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
            </div>
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
          {Object.entries(stageIcons).map(([key, Icon]) => {
            const isConfigurable = key === "trend_ingestion" || key === "candidate_filter" || key === "vlm_scoring" || key === "review" || key === "generation";
            return (
              <Card
                key={key}
                className={`transition-shadow flex flex-col ${isConfigurable ? "cursor-pointer hover:shadow-md" : ""}`}
                onClick={isConfigurable ? () => setSettingsDialog(key) : undefined}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg flex items-center justify-center bg-purple-100">
                        <Icon className="w-5 h-5 text-purple-600" />
                      </div>
                      <CardTitle className="text-lg">
                        {stageTitles[key as keyof typeof stageTitles]}
                      </CardTitle>
                    </div>
                    {isConfigurable && <Settings2 className="w-4 h-4 text-slate-400 flex-shrink-0" />}
                  </div>
                </CardHeader>
                <CardContent className="flex-1 flex flex-col">
                  <p className="text-sm text-slate-600 mb-3">
                    {stageDescriptions[key as keyof typeof stageDescriptions]}
                  </p>
                  <div className="mt-auto pt-2 border-t text-xs text-slate-500 space-y-0.5">
                    {key === "trend_ingestion" && (
                      <>
                        <div>Platforms: <span className="font-medium text-slate-700">{[tiktok && "TikTok", instagram && "Instagram"].filter(Boolean).join(", ") || "none"}</span></div>
                        <div>Limit: <span className="font-medium text-slate-700">{limit}</span></div>
                        {hashtags && <div>Hashtags: <span className="font-medium text-slate-700">{hashtags}</span></div>}
                      </>
                    )}
                    {key === "candidate_filter" && (
                      <div>Top K: <span className="font-medium text-slate-700">{filterTopK}</span></div>
                    )}
                    {key === "vlm_scoring" && (
                      <div>Max videos: <span className="font-medium text-slate-700">{vlmMaxVideos}</span></div>
                    )}
                    {key === "review" && (
                      <div>Auto-review: <span className="font-medium text-slate-700">{autoReview ? "yes" : "no"}</span></div>
                    )}
                    {key === "download" && (
                      <div>Mode: <span className="font-medium text-slate-700">yt-dlp</span></div>
                    )}
                    {key === "generation" && (
                      <>
                        <div>Workflow: <span className="font-medium text-slate-700">wan_animate</span></div>
                        <div>Align reference: <span className="font-medium text-slate-700">{alignReference ? "yes" : "no"}</span></div>
                      </>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

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
            <DialogFooter>
              <Button onClick={() => setSettingsDialog(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

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
              defaultSources={defaultSources}
              onStarted={(jobId) => {
                setPipelineDialogOpen(false);
                setActiveJobId(jobId);
              }}
            />
          </Dialog>
        </div>
        <Separator className="mb-6" />

        {activeJobId && activeJob && (
          <LiveTaskCard job={activeJob} isDone={jobDone} />
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
            tasks?.filter((task: Task) => {
              // Hide the newest task when the live card is showing (avoids duplicate)
              if (activeJobId && !jobDone && task.id === newestTaskId) return false;
              return true;
            }).map((task: Task) => {
              return (
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
                            <div key={key} className={`flex flex-col items-center text-center p-3 rounded-lg transition-all duration-500 ${
                              stage.status === "completed" ? "bg-green-50" :
                              stage.status === "in-progress" ? "bg-blue-50" :
                              "bg-slate-50"
                            }`}>
                              <div className="flex items-center gap-2 mb-2 transition-all duration-300">
                                {getStatusIcon(stage.status)}
                                <Icon className={`w-4 h-4 transition-colors duration-300 ${
                                  stage.status === "completed" ? "text-green-600" :
                                  stage.status === "in-progress" ? "text-blue-600" :
                                  "text-slate-600"
                                }`} />
                              </div>
                              <div className="text-xs font-medium text-slate-700 mb-1">
                                {stageTitles[key as keyof typeof stageTitles]}
                              </div>
                              <div className="text-lg font-bold transition-all duration-300" style={{ opacity: stage.items_count !== undefined ? 1 : 0 }}>
                                <span className={stage.status === "completed" ? "text-green-600" : "text-purple-600"}>
                                  {stage.items_count ?? "\u00A0"}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              );
            })
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
    <Card className={`mb-4 transition-all duration-500 ${isDone ? "ring-2 ring-green-400 ring-opacity-50" : "ring-2 ring-blue-400 ring-opacity-50"}`}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {isDone ? (
              <CheckCircle2 className="w-5 h-5 text-green-600" />
            ) : (
              <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />
            )}
            <div>
              <CardTitle className="text-xl mb-1">
                {isDone ? "Pipeline Completed" : "Pipeline Running"}
              </CardTitle>
              <CardDescription className="flex items-center gap-2">
                <Clock className="w-4 h-4" />
                {isDone
                  ? "Finished — loading results..."
                  : progress?.current_stage
                    ? `Stage: ${progress.current_stage}`
                    : "Starting..."}
              </CardDescription>
            </div>
          </div>
          <Badge className={isDone ? "bg-green-100 text-green-800" : "bg-blue-100 text-blue-800"}>
            {isDone ? "completed" : "in-progress"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          {Object.entries(stageData).map(([key, stage]) => {
            const Icon = stageIcons[key as keyof typeof stageIcons];
            const uiStatus = stage.status === "completed" ? "completed" :
              stage.status === "running" ? "in-progress" : "pending";
            return (
              <div key={key} className={`flex flex-col items-center text-center p-3 rounded-lg transition-all duration-500 ${
                uiStatus === "completed" ? "bg-green-50" :
                uiStatus === "in-progress" ? "bg-blue-50" : "bg-slate-50"
              }`}>
                <div className="flex items-center gap-2 mb-2 transition-all duration-300">
                  {getStatusIcon(uiStatus)}
                  <Icon className={`w-4 h-4 transition-colors duration-300 ${
                    uiStatus === "completed" ? "text-green-600" :
                    uiStatus === "in-progress" ? "text-blue-600" :
                    "text-slate-600"
                  }`} />
                </div>
                <div className="text-xs font-medium text-slate-700 mb-1">
                  {stageTitles[key as keyof typeof stageTitles]}
                </div>
                <div className="text-lg font-bold transition-all duration-300" style={{ opacity: stage.items !== undefined ? 1 : 0 }}>
                  <span className={uiStatus === "completed" ? "text-green-600" : "text-purple-600"}>
                    {stage.items ?? "\u00A0"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
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
