/**
 * Mock data for development without a backend.
 * Types are now defined in ../api/types.ts — this file only contains sample data.
 */
export type { InfluencerOut, Task, StageResult, VideoPreview } from "../api/types";
import type { Task } from "../api/types";

// Shared thumbnail pool (Unsplash placeholders)
const T = {
  gym1:    "https://images.unsplash.com/photo-1434596922112-19c563067271?w=400&q=80",
  gym2:    "https://images.unsplash.com/photo-1751456357787-fe644b095838?w=400&q=80",
  game1:   "https://images.unsplash.com/photo-1758179761789-87792b6132a4?w=400&q=80",
  game2:   "https://images.unsplash.com/photo-1708032665058-31f8b77fed34?w=400&q=80",
  alt1:    "https://images.unsplash.com/photo-1656568726647-9092bf2b5640?w=400&q=80",
  tea:     "https://images.unsplash.com/photo-1757582893381-6d1b8674c1f7?w=400&q=80",
  cosplay: "https://images.unsplash.com/photo-1572489373015-46c874dc4114?w=400&q=80",
  yoga:    "https://images.unsplash.com/photo-1617391484407-e58218dee463?w=400&q=80",
  dark:    "https://images.unsplash.com/photo-1674578852134-58ff9d75afd8?w=400&q=80",
};

export const mockTasks: Task[] = [
  {
    id: "task_001",
    influencer_id: "emi2souls",
    created_at: "2026-03-13T10:00:00.000000+00:00",
    status: "completed",
    stages: {
      trend_ingestion: {
        status: "completed",
        items_count: 150,
        duration: "2m 34s",
        details: { platform: "TikTok", hashtags_searched: ["fitnessgirl", "gymgirl", "gamergirl"], videos_found: 150 },
      },
      download: {
        status: "completed",
        items_count: 150,
        duration: "5m 12s",
        details: { downloaded: 150, failed: 0, total_size: "2.3 GB" },
      },
      candidate_filter: {
        status: "completed",
        items_count: 82,
        duration: "1m 45s",
        details: { filtered_in: 82, filtered_out: 68, avg_duration: "8.2s", avg_resolution: "1080p" },
      },
      vlm_scoring: {
        status: "completed",
        items_count: 24,
        duration: "8m 30s",
        details: { accepted: 24, rejected: 58, avg_score: 7.8, top_criteria: ["theme_match", "persona_fit", "face_visibility"] },
      },
      review: {
        status: "completed",
        items_count: 18,
        duration: "15m 20s",
        details: { approved: 18, skipped: 6, prompts_added: 18 },
        videos: [
          { id: "rv_01", thumbnail: T.gym1, title: "gymgirl_morning_workout.mp4", duration: "0:32", approved: true, prompt: "Emi Noir doing a morning gym workout, dark feminine energy, cat-eye lenses" },
          { id: "rv_02", thumbnail: T.alt1, title: "darkfeminine_aesthetic.mp4", duration: "0:45", approved: true, prompt: "Alt aesthetic lifestyle vlog, edgy yet cozy, heterochromia highlight" },
          { id: "rv_03", thumbnail: T.game1, title: "lol_gamergirl_clip.mp4", duration: "1:02", approved: true, prompt: "Gaming session League of Legends, RGB setup, confident gamer girl energy" },
        ],
      },
      generation: {
        status: "completed",
        items_count: 18,
        duration: "45m 30s",
        details: { raw_generated: 18, refined: 18, upscaled: 18, postprocessed: 18, avg_generation_time: "2m 30s" },
        videos: [
          { id: "gen_01", thumbnail: T.gym1, title: "gymgirl_morning_workout — Emi Noir", duration: "0:32", generation_steps: { raw: T.gym1, refined: T.alt1, upscaled: T.dark, postprocessed: T.gym2 } },
          { id: "gen_02", thumbnail: T.alt1, title: "darkfeminine_aesthetic — Emi Noir", duration: "0:45", generation_steps: { raw: T.alt1, refined: T.dark, upscaled: T.cosplay, postprocessed: T.alt1 } },
        ],
      },
    },
  },
];
