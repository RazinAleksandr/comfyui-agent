import { useState } from "react";
import { Link } from "react-router";
import { Card, CardContent } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { Label } from "../components/ui/label";
import { Sparkles, Plus, Loader2, LogOut, ArrowRight, User } from "lucide-react";
import { useInfluencers } from "../api/hooks";
import { api } from "../api/client";
import { ImageWithFallback } from "../components/figma/ImageWithFallback";
import { useAuth } from "../auth/AuthContext";
import type { InfluencerOut } from "../api/types";

/* ─────────────────────────── Influencer Card ─────────────────────────── */

function InfluencerCard({ influencer }: { influencer: InfluencerOut }) {
  return (
    <Link
      to={`/avatar/${influencer.influencer_id}`}
      className="group block"
    >
      <div className="relative rounded-2xl overflow-hidden bg-white border border-slate-200/80 shadow-sm transition-all duration-300 hover:shadow-xl hover:-translate-y-1.5">
        {/* ── Portrait ── */}
        <div className="relative aspect-[3/4] overflow-hidden bg-gradient-to-br from-blue-100 via-slate-100 to-blue-50">
          <ImageWithFallback
            src={influencer.profile_image_url ?? ""}
            alt={influencer.name}
            className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
          {/* Gradient overlay at bottom for text readability */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent" />

          {/* ── Content overlay on image ── */}
          <div className="absolute bottom-0 left-0 right-0 p-5">
            {/* Handle badge */}
            <span className="inline-block px-2.5 py-0.5 rounded-full bg-white/20 backdrop-blur-sm text-[11px] font-medium text-white/90 tracking-wide mb-2.5">
              @{influencer.influencer_id}
            </span>

            {/* Name */}
            <h3 className="text-xl font-bold text-white leading-tight mb-1.5 tracking-tight">
              {influencer.name}
            </h3>

            {/* Description */}
            {influencer.description && (
              <p className="text-sm text-white/75 leading-relaxed line-clamp-2 mb-3">
                {influencer.description}
              </p>
            )}

            {/* Hashtags */}
            {influencer.hashtags && influencer.hashtags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {influencer.hashtags.slice(0, 3).map((tag) => (
                  <span
                    key={tag}
                    className="px-2 py-0.5 rounded-full bg-white/15 backdrop-blur-sm text-[11px] font-medium text-white/80"
                  >
                    #{tag}
                  </span>
                ))}
                {influencer.hashtags.length > 3 && (
                  <span className="px-2 py-0.5 rounded-full bg-white/15 backdrop-blur-sm text-[11px] font-medium text-white/80">
                    +{influencer.hashtags.length - 3}
                  </span>
                )}
              </div>
            )}
          </div>

          {/* ── Hover arrow indicator ── */}
          <div className="absolute top-4 right-4 w-8 h-8 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center opacity-0 translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all duration-300">
            <ArrowRight className="w-4 h-4 text-white" />
          </div>
        </div>
      </div>
    </Link>
  );
}

/* ─────────────────────────── Loading Skeleton ─────────────────────────── */

function InfluencerSkeleton() {
  return (
    <div className="rounded-2xl overflow-hidden bg-white border border-slate-200/80 shadow-sm">
      <div className="aspect-[3/4] relative">
        <Skeleton className="absolute inset-0 w-full h-full" />
        <div className="absolute bottom-0 left-0 right-0 p-5 space-y-3">
          <Skeleton className="h-4 w-16 rounded-full opacity-40" />
          <Skeleton className="h-6 w-3/4 opacity-40" />
          <Skeleton className="h-4 w-full opacity-40" />
          <div className="flex gap-1.5">
            <Skeleton className="h-5 w-14 rounded-full opacity-40" />
            <Skeleton className="h-5 w-14 rounded-full opacity-40" />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────── Create New Card ─────────────────────────── */

function CreateNewCard() {
  return (
    <div className="relative rounded-2xl overflow-hidden border-2 border-dashed border-blue-300/60 bg-gradient-to-br from-blue-50/80 via-white to-blue-50/40 cursor-pointer transition-all duration-300 hover:shadow-lg hover:-translate-y-1.5 hover:border-blue-400/80 group">
      <div className="aspect-[3/4] flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center mb-5 shadow-lg shadow-blue-500/25 transition-transform duration-300 group-hover:scale-110">
          <Plus className="w-7 h-7 text-white" />
        </div>
        <p className="text-lg font-bold text-slate-800 mb-1.5">Create Avatar</p>
        <p className="text-sm text-slate-500 leading-relaxed max-w-[180px]">
          Add a new AI influencer to your studio
        </p>
      </div>
    </div>
  );
}

/* ─────────────────────────── Home Page ─────────────────────────── */

export default function HomePage() {
  const { data: influencers, loading, error, refetch } = useInfluencers();
  const [dialogOpen, setDialogOpen] = useState(false);
  const { user, logout } = useAuth();

  const count = influencers?.length ?? 0;

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50/80 via-slate-50 to-blue-100/60">
      {/* ── Header ── */}
      <header className="sticky top-0 z-30 backdrop-blur-md bg-white/70 border-b border-slate-200/60">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          {/* Brand */}
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center shadow-sm">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div className="flex flex-col">
              <span className="text-lg font-bold tracking-tight text-slate-900">
                AI Avatar Studio
              </span>
              <span className="text-[11px] text-slate-400 font-normal hidden md:block">Content Studio</span>
            </div>
          </div>

          {/* User */}
          <div className="flex items-center gap-3">
            <span className="bg-slate-100 rounded-full px-3 py-1 text-sm text-slate-600 font-medium hidden sm:flex items-center gap-1.5">
              <User className="w-3.5 h-3.5 text-slate-400" />
              {user?.display_name}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={logout}
              className="text-slate-400 hover:text-slate-600"
            >
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="max-w-7xl mx-auto px-6 py-10">
        {/* Hero section */}
        <div className="mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-slate-900 mb-2">
            Your Talent Roster
          </h1>
          <p className="text-base text-slate-500 max-w-lg">
            {loading
              ? "Loading your AI influencers..."
              : count > 0
                ? `${count} AI influencer${count !== 1 ? "s" : ""} ready for content generation`
                : "Create your first AI influencer to get started"
            }
          </p>
        </div>

        {/* Error state */}
        {error && (
          <Card className="mb-8 border-red-200 bg-red-50/50">
            <CardContent className="py-4 flex items-center justify-between">
              <p className="text-sm text-red-700">
                Failed to load influencers: {error}
              </p>
              <Button variant="outline" size="sm" onClick={refetch} className="text-red-700 border-red-200 hover:bg-red-100">
                Retry
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-5">
          {loading
            ? Array.from({ length: 5 }).map((_, i) => (
                <InfluencerSkeleton key={i} />
              ))
            : influencers?.map((influencer) => (
                <InfluencerCard key={influencer.influencer_id} influencer={influencer} />
              ))}

          {/* Create New Influencer */}
          {!loading && (
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger asChild>
                <CreateNewCard />
              </DialogTrigger>
              <CreateInfluencerDialog
                onCreated={() => {
                  setDialogOpen(false);
                  refetch();
                }}
              />
            </Dialog>
          )}
        </div>

        {/* Empty state (no influencers, not loading, no error) */}
        {!loading && !error && count === 0 && (
          <div className="text-center mt-16">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-blue-100 to-blue-200 flex items-center justify-center mx-auto mb-6">
              <Sparkles className="w-10 h-10 text-blue-500" />
            </div>
            <h2 className="text-xl font-bold text-slate-800 mb-2">
              No influencers yet
            </h2>
            <p className="text-slate-500 mb-6 max-w-md mx-auto">
              Your studio is empty. Create your first AI avatar to begin generating content.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

/* ─────────────────────────── Create Influencer Dialog ─────────────────────────── */

function CreateInfluencerDialog({ onCreated }: { onCreated: () => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refImage, setRefImage] = useState<File | null>(null);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSaving(true);
    setError(null);

    const form = new FormData(e.currentTarget);
    const influencerId = (form.get("influencer_id") as string).trim().toLowerCase().replace(/\s+/g, "_");
    const name = (form.get("name") as string).trim();
    const description = (form.get("description") as string).trim();
    const hashtagsRaw = (form.get("hashtags") as string).trim();
    const videoReq = (form.get("video_suggestions_requirement") as string).trim();
    const appearanceDesc = (form.get("appearance_description") as string).trim();

    if (!influencerId || !name) {
      setError("ID and Name are required");
      setSaving(false);
      return;
    }

    try {
      await api.upsertInfluencer(influencerId, {
        name,
        description: description || undefined,
        hashtags: hashtagsRaw ? hashtagsRaw.split(",").map((h) => h.trim().replace(/^#/, "")) : undefined,
        video_suggestions_requirement: videoReq || undefined,
        appearance_description: appearanceDesc || undefined,
      });

      if (refImage) {
        await api.uploadReferenceImage(influencerId, refImage);
      }

      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Create New Avatar</DialogTitle>
        <DialogDescription>
          Add a new AI influencer to the studio
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="influencer_id">ID (slug)</Label>
          <Input id="influencer_id" name="influencer_id" placeholder="emi2souls" required />
        </div>
        <div className="space-y-2">
          <Label htmlFor="name">Name</Label>
          <Input id="name" name="name" placeholder="Emi Noir" required />
        </div>
        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Textarea id="description" name="description" placeholder="Alt-aesthetic lifestyle creator..." rows={3} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="hashtags">Hashtags (comma-separated)</Label>
          <Input id="hashtags" name="hashtags" placeholder="fitness, gaming, altgirl" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="video_suggestions_requirement">Video Selection Requirements</Label>
          <Textarea id="video_suggestions_requirement" name="video_suggestions_requirement" placeholder="Avoid videos with..." rows={2} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="appearance_description">Appearance Description</Label>
          <Textarea id="appearance_description" name="appearance_description" placeholder="Describe the person's physical appearance for video generation prompts..." rows={3} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ref_image">Reference Image</Label>
          <Input
            id="ref_image"
            type="file"
            accept="image/*"
            onChange={(e) => setRefImage(e.target.files?.[0] ?? null)}
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <DialogFooter>
          <Button type="submit" disabled={saving}>
            {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Create Avatar
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
