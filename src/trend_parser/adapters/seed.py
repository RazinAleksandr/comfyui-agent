from __future__ import annotations

import json
from pathlib import Path

from trend_parser.adapters.types import RawTrendVideo, TrendFetchSelector


class SeedTrendAdapter:
    def __init__(self, platform: str, seed_dir: Path):
        self.platform = platform
        self.seed_dir = seed_dir

    def fetch(self, limit: int, selector: TrendFetchSelector | None = None) -> list[RawTrendVideo]:
        file_path = self.seed_dir / f"{self.platform}_videos.json"
        if not file_path.exists():
            return []

        with file_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        filtered = self._apply_selector(payload, selector)
        items = filtered[:limit]
        return [RawTrendVideo(platform=self.platform, **item) for item in items]

    def _apply_selector(self, payload: list[dict], selector: TrendFetchSelector | None) -> list[dict]:
        if selector is None:
            return payload

        wanted_tags = {tag.lower().strip().lstrip("#") for tag in selector.hashtags if tag}
        wanted_terms = {term.lower().strip() for term in selector.search_terms if term}
        min_views = selector.min_views
        min_likes = selector.min_likes

        output: list[dict] = []
        for item in payload:
            views = int(item.get("views") or 0)
            likes = int(item.get("likes") or 0)
            if min_views is not None and views < min_views:
                continue
            if min_likes is not None and likes < min_likes:
                continue

            item_hashtags = {str(tag).lower().strip().lstrip("#") for tag in item.get("hashtags") or []}
            caption = str(item.get("caption") or "").lower()

            if wanted_tags and not (item_hashtags & wanted_tags):
                continue
            if wanted_terms and not any(term in caption for term in wanted_terms):
                continue

            output.append(item)

        return output
