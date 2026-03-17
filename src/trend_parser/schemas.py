from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TrendSelectorIn(BaseModel):
    mode: str = "auto"
    search_terms: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    min_views: int | None = None
    min_likes: int | None = None
    published_within_days: int | None = None
    require_topic_match: bool = False
    source_params: dict | None = None


class PlatformPipelineConfigIn(BaseModel):
    enabled: bool = True
    source: str = Field(..., pattern="^(seed|apify|tiktok_custom|instagram_custom)$")
    limit: int = Field(default=20, ge=1, le=200)
    selector: TrendSelectorIn = Field(default_factory=TrendSelectorIn)


class DownloadStageConfigIn(BaseModel):
    enabled: bool = True
    force: bool = False


class FilterStageConfigIn(BaseModel):
    enabled: bool = True
    probe_seconds: int = Field(default=8, ge=3, le=120)
    workers: int = Field(default=4, ge=1, le=32)
    top_k: int = Field(default=15, ge=1, le=200)


class VlmThresholdsIn(BaseModel):
    min_readiness: float = 7.0
    min_confidence: float = 0.70
    min_persona_fit: float = 6.5
    max_occlusion_risk: float = 6.0
    max_scene_cut_complexity: float = 6.0


class VlmStageConfigIn(BaseModel):
    enabled: bool = True
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_sec: int = Field(default=300, ge=30, le=3600)
    mock: bool = False
    max_videos: int = Field(default=15, ge=1, le=200)
    theme: str = "influencer channel"
    sync_folders: bool = True
    thresholds: VlmThresholdsIn = Field(default_factory=VlmThresholdsIn)


class ReviewStageConfigIn(BaseModel):
    auto: bool = False
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    timeout_sec: int = Field(default=120, ge=30, le=600)


class PipelineRunRequest(BaseModel):
    influencer_id: str = Field(..., min_length=1, max_length=128)
    platforms: dict[str, PlatformPipelineConfigIn] = Field(default_factory=dict)
    download: DownloadStageConfigIn = Field(default_factory=DownloadStageConfigIn)
    filter: FilterStageConfigIn = Field(default_factory=FilterStageConfigIn)
    vlm: VlmStageConfigIn = Field(default_factory=VlmStageConfigIn)
    review: ReviewStageConfigIn = Field(default_factory=ReviewStageConfigIn)


class PipelinePlatformRunOut(BaseModel):
    platform: str
    source: str
    ingested_items: int
    download_counts: dict[str, int]
    candidate_report_path: str | None = None
    filtered_dir: str | None = None
    vlm_summary_path: str | None = None
    selected_dir: str | None = None
    accepted: int | None = None
    rejected: int | None = None


class PipelineRunOut(BaseModel):
    influencer_id: str
    started_at: datetime
    base_dir: str
    platforms: list[PipelinePlatformRunOut]
