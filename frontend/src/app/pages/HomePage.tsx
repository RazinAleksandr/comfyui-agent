import { useState } from "react";
import { Link } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
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
import { Sparkles, Plus, Loader2, LogOut } from "lucide-react";
import { useInfluencers } from "../api/hooks";
import { api } from "../api/client";
import { ImageWithFallback } from "../components/figma/ImageWithFallback";
import { useAuth } from "../auth/AuthContext";

export default function HomePage() {
  const { data: influencers, loading, error, refetch } = useInfluencers();
  const [dialogOpen, setDialogOpen] = useState(false);
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="container mx-auto px-4 py-8">
        <div className="absolute top-4 right-4 flex items-center gap-2">
          <span className="text-sm text-slate-500">{user?.display_name}</span>
          <Button variant="ghost" size="sm" onClick={logout} className="text-slate-500 hover:text-slate-700">
            <LogOut className="w-4 h-4" />
          </Button>
        </div>
        <div className="mb-12 text-center">
          <div className="flex items-center justify-center gap-3 mb-4">
            <Sparkles className="w-10 h-10 text-purple-600" />
            <h1 className="text-4xl font-bold">AI Avatar Studio</h1>
          </div>
          <p className="text-lg text-slate-600">
            Select an AI avatar to start content generation
          </p>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-center">
            Failed to load influencers: {error}
            <Button variant="ghost" size="sm" onClick={refetch} className="ml-2">
              Retry
            </Button>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {loading
            ? Array.from({ length: 4 }).map((_, i) => (
                <Card key={i} className="overflow-hidden">
                  <Skeleton className="aspect-square w-full" />
                  <CardHeader>
                    <Skeleton className="h-6 w-3/4" />
                    <Skeleton className="h-4 w-full mt-2" />
                    <Skeleton className="h-4 w-2/3 mt-1" />
                  </CardHeader>
                </Card>
              ))
            : influencers?.map((influencer) => (
                <Link
                  key={influencer.influencer_id}
                  to={`/avatar/${influencer.influencer_id}`}
                  className="group"
                >
                  <Card className="overflow-hidden transition-all duration-300 hover:shadow-xl hover:-translate-y-1">
                    <div className="aspect-square overflow-hidden bg-gradient-to-br from-purple-100 to-pink-100">
                      <ImageWithFallback
                        src={influencer.profile_image_url ?? ""}
                        alt={influencer.name}
                        className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-110"
                      />
                    </div>
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between">
                        {influencer.name}
                        <Badge variant="outline" className="text-xs">
                          @{influencer.influencer_id}
                        </Badge>
                      </CardTitle>
                      <CardDescription className="line-clamp-3">
                        {influencer.description}
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-1.5">
                        {influencer.hashtags?.slice(0, 4).map((tag) => (
                          <Badge key={tag} variant="secondary" className="text-xs">
                            #{tag}
                          </Badge>
                        ))}
                        {(influencer.hashtags?.length ?? 0) > 4 && (
                          <Badge variant="secondary" className="text-xs">
                            +{(influencer.hashtags?.length ?? 0) - 4}
                          </Badge>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              ))}

          {/* Create New Influencer Card */}
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Card className="overflow-hidden border-dashed border-2 cursor-pointer transition-all duration-300 hover:shadow-lg hover:-translate-y-1 flex items-center justify-center min-h-[400px]">
                <CardContent className="text-center py-8">
                  <div className="w-16 h-16 rounded-full bg-purple-100 flex items-center justify-center mx-auto mb-4">
                    <Plus className="w-8 h-8 text-purple-600" />
                  </div>
                  <p className="font-semibold text-slate-700">Create New Avatar</p>
                  <p className="text-sm text-slate-500 mt-1">Add a new AI influencer</p>
                </CardContent>
              </Card>
            </DialogTrigger>
            <CreateInfluencerDialog
              onCreated={() => {
                setDialogOpen(false);
                refetch();
              }}
            />
          </Dialog>
        </div>
      </div>
    </div>
  );
}

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
