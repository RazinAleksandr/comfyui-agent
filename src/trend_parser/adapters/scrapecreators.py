"""TikTok adapter using ScrapeCreators API via last30days lib.

Calls ScrapeCreators /v1/tiktok/search/keyword directly — no browser,
no msToken, results sorted by views. Uses the already-tested last30days
tiktok module for the actual API call and data normalisation.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from trend_parser.adapters.types import RawTrendVideo, TrendFetchSelector

# Add vendor/last30days/scripts to path so we can import its lib
_SC_BASE = "https://api.scrapecreators.com/v1/tiktok"


class ScrapeCreatorsTikTokAdapter:
    """Fetch TikTok videos via ScrapeCreators keyword search.

    Advantages over tiktok_custom:
    - No browser / Playwright / msToken required
    - Results are sorted by views (most viral first)
    - Uses SCRAPECREATORS_API_KEY already configured for last30days
    """

    def __init__(self, query: str, api_key: str | None = None):
        self.query = query
        self.api_key = api_key or os.environ.get("SCRAPECREATORS_API_KEY", "")

    def fetch(self, limit: int, selector: TrendFetchSelector | None = None) -> list[RawTrendVideo]:
        if not self.api_key:
            raise RuntimeError(
                "ScrapeCreatorsTikTokAdapter requires SCRAPECREATORS_API_KEY. "
                "Set it in .env or pass api_key= directly."
            )

        topics = self._build_topics(selector)
        raw_items = self._search_multi(topics, limit)

        videos = [self._to_video(item) for item in raw_items]
        videos = [v for v in videos if v is not None]
        # Sort by views before filtering so we keep the best ones
        videos.sort(key=lambda v: v.views, reverse=True)
        videos = self._apply_filters(videos, selector)
        return videos[:limit]

    def _search_multi(self, topics: list[str], limit: int) -> list[dict]:
        """Run one ScrapeCreators search per topic, merge and deduplicate."""
        if not topics:
            return []

        seen_ids: set[str] = set()
        all_items: list[dict] = []
        per_topic = max(limit, 20)  # fetch at least 20 per term for good dedup coverage

        for topic in topics:
            for item in self._search_one(topic, per_topic):
                vid = item.get("video_id") or item.get("url") or ""
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    all_items.append(item)

        return all_items

    def _search_one(self, topic: str, count: int) -> list[dict]:
        """Single ScrapeCreators keyword search call."""
        import requests as _req

        resp = _req.get(
            f"{_SC_BASE}/search/keyword",
            params={"query": topic, "sort_by": "relevance", "count": count},
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        entries = data.get("search_item_list") or data.get("data") or []
        items = []
        for entry in entries:
            info = entry.get("aweme_info", entry) if isinstance(entry, dict) else entry
            parsed = self._parse_raw(info)
            if parsed:
                items.append(parsed)
        return items

    # --- Helpers ---

    def _build_topics(self, selector: TrendFetchSelector | None) -> list[str]:
        """Return ordered list of search queries to run."""
        if selector:
            if selector.search_terms:
                return list(dict.fromkeys(selector.search_terms))  # deduplicated, order preserved
            if selector.hashtags:
                # Each hashtag as its own query gives better coverage than joining them
                return list(dict.fromkeys(selector.hashtags[:4]))
        return [self.query] if self.query else []

    def _parse_raw(self, info: dict[str, Any]) -> dict[str, Any] | None:
        """Normalise a raw ScrapeCreators aweme_info dict to a flat item dict."""
        video_id = str(info.get("aweme_id") or info.get("id") or "").strip()
        if not video_id:
            return None

        stats = info.get("statistics") or info.get("stats") or {}
        author = info.get("author") or {}
        author_name = author.get("unique_id") or author.get("nickname") or ""
        text = info.get("desc") or ""

        share_url = info.get("share_url") or ""
        url = share_url.split("?")[0] if share_url else ""
        if not url and author_name:
            url = f"https://www.tiktok.com/@{author_name}/video/{video_id}"

        hashtags = [
            t.get("hashtag_name", "")
            for t in (info.get("text_extra") or [])
            if isinstance(t, dict) and t.get("hashtag_name")
        ]

        published_at = None
        ts = info.get("create_time")
        if ts:
            try:
                published_at = datetime.fromtimestamp(int(ts), tz=UTC)
            except (ValueError, OSError):
                pass

        return {
            "video_id": video_id,
            "url": url,
            "text": text,
            "author_name": author_name,
            "hashtags": hashtags,
            "published_at": published_at,
            "views": int(stats.get("play_count") or 0),
            "likes": int(stats.get("digg_count") or 0),
            "comments": int(stats.get("comment_count") or 0),
            "shares": int(stats.get("share_count") or 0),
        }

    def _to_video(self, item: dict[str, Any]) -> RawTrendVideo | None:
        url = item.get("url") or ""
        video_id = item.get("video_id") or ""
        if not url and not video_id:
            return None

        return RawTrendVideo(
            platform="tiktok",
            source_item_id=video_id,
            video_url=url or None,
            caption=item.get("text") or None,
            hashtags=[str(h).lstrip("#") for h in item.get("hashtags", []) if h],
            audio=None,
            style_hint=None,
            published_at=item.get("published_at"),
            views=item.get("views", 0),
            likes=item.get("likes", 0),
            comments=item.get("comments", 0),
            shares=item.get("shares", 0),
            raw_payload=item,
        )

    def _apply_filters(
        self, videos: list[RawTrendVideo], selector: TrendFetchSelector | None
    ) -> list[RawTrendVideo]:
        if selector is None:
            return videos
        out = []
        cutoff = None
        if selector.published_within_days:
            cutoff = datetime.now(UTC) - timedelta(days=int(selector.published_within_days))
        for v in videos:
            if selector.min_views is not None and v.views < selector.min_views:
                continue
            if selector.min_likes is not None and v.likes < selector.min_likes:
                continue
            if cutoff and v.published_at and v.published_at < cutoff:
                continue
            out.append(v)
        return out
