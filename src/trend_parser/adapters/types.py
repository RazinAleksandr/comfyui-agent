from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawTrendVideo(BaseModel):
    platform: str
    source_item_id: str | None = None
    video_url: str | None = None
    caption: str | None = None

    hashtags: list[str] = Field(default_factory=list)
    audio: str | None = None
    style_hint: str | None = None

    published_at: datetime | None = None

    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0

    raw_payload: dict = Field(default_factory=dict)


class TrendFetchSelector(BaseModel):
    mode: str = "auto"  # auto | search | hashtag | mixed
    search_terms: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    min_views: int | None = None
    min_likes: int | None = None
    published_within_days: int | None = None
    require_topic_match: bool = False
    source_params: dict | None = None
