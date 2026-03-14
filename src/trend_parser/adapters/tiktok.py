from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from trend_parser.adapters.types import RawTrendVideo, TrendFetchSelector

try:
    from TikTokApi import TikTokApi
except Exception:  # pragma: no cover - optional dependency
    TikTokApi = None

logger = logging.getLogger(__name__)


class TikTokCustomAdapter:
    def __init__(
        self,
        query: str,
        ms_tokens_csv: str | None = None,
        headless: bool = True,
        session_count: int = 1,
        sleep_after: int = 3,
        browser: str = "chromium",
    ):
        self.query = query
        self.ms_tokens_csv = ms_tokens_csv or ""
        self.headless = bool(headless)
        self.session_count = max(1, int(session_count))
        self.sleep_after = max(1, int(sleep_after))
        self.browser = browser or "chromium"

    def fetch(self, limit: int, selector: TrendFetchSelector | None = None) -> list[RawTrendVideo]:
        if TikTokApi is None:
            raise RuntimeError(
                "TikTokApi is not installed. Install with: pip install -e '.[vps]'"
            )
        return self._run_async(self._fetch_async(limit=limit, selector=selector))

    async def _fetch_async(self, limit: int, selector: TrendFetchSelector | None) -> list[RawTrendVideo]:
        hashtags = self._selector_hashtags(selector)
        if not hashtags:
            hashtags = self._fallback_hashtags(selector)
        if not hashtags:
            hashtags = ["healthylifestyle"]

        ms_tokens = self._ms_tokens()
        per_tag_limit = max(1, (max(1, int(limit)) + len(hashtags) - 1) // len(hashtags))

        unique: dict[str, RawTrendVideo] = {}
        async with TikTokApi() as api:
            await api.create_sessions(
                ms_tokens=ms_tokens,
                num_sessions=self.session_count,
                sleep_after=self.sleep_after,
                browser=self.browser,
                headless=self.headless,
            )
            for tag in hashtags:
                try:
                    hashtag = api.hashtag(name=tag)
                    async for video in hashtag.videos(count=per_tag_limit):
                        row = self._to_video(video.as_dict)
                        if row is None:
                            continue
                        if row.source_item_id and row.source_item_id in unique:
                            continue
                        if not self._passes_filters(row, selector):
                            continue
                        key = row.source_item_id or f"{tag}:{len(unique)}"
                        unique[key] = row
                        if len(unique) >= limit:
                            return list(unique.values())[:limit]
                except Exception as exc:
                    logger.warning("[tiktok_custom] hashtag '%s' failed: %s", tag, exc)
                    continue
        return list(unique.values())[:limit]

    def _to_video(self, data: dict[str, Any]) -> RawTrendVideo | None:
        source_id = str(data.get("id") or "").strip()
        if not source_id:
            return None

        author = data.get("author") or {}
        stats = data.get("stats") or {}
        music = data.get("music") or {}
        challenges = data.get("challenges") or []

        unique_id = str(author.get("uniqueId") or "").strip()
        video_url = f"https://www.tiktok.com/@{unique_id}/video/{source_id}" if unique_id else None

        published_at = None
        create_time = data.get("createTime")
        try:
            if create_time is not None:
                published_at = datetime.fromtimestamp(int(create_time), tz=UTC)
        except (TypeError, ValueError, OSError):
            published_at = None

        hashtags: list[str] = []
        for tag in challenges:
            if isinstance(tag, dict):
                name = str(tag.get("title") or "").strip().lstrip("#")
                if name:
                    hashtags.append(name)
        if not hashtags:
            for item in data.get("textExtra") or []:
                if isinstance(item, dict):
                    text = str(item.get("hashtagName") or "").strip().lstrip("#")
                    if text:
                        hashtags.append(text)

        return RawTrendVideo(
            platform="tiktok",
            source_item_id=source_id,
            video_url=video_url,
            caption=data.get("desc"),
            hashtags=list(dict.fromkeys(hashtags)),
            audio=(music.get("title") or music.get("authorName") or None),
            style_hint=str(data.get("CategoryType")) if data.get("CategoryType") is not None else None,
            published_at=published_at,
            views=self._to_int(stats.get("playCount")),
            likes=self._to_int(stats.get("diggCount")),
            comments=self._to_int(stats.get("commentCount")),
            shares=self._to_int(stats.get("shareCount")),
            raw_payload=data,
        )

    def _passes_filters(self, video: RawTrendVideo, selector: TrendFetchSelector | None) -> bool:
        if selector is None:
            return True
        if selector.min_views is not None and video.views < int(selector.min_views):
            return False
        if selector.min_likes is not None and video.likes < int(selector.min_likes):
            return False
        if selector.published_within_days is not None:
            if video.published_at is None:
                return False
            cutoff = datetime.now(UTC) - timedelta(days=max(1, int(selector.published_within_days)))
            if video.published_at < cutoff:
                return False
        return True

    def _selector_hashtags(self, selector: TrendFetchSelector | None) -> list[str]:
        if selector is None:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for raw in selector.hashtags:
            tag = str(raw or "").strip().lstrip("#").lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            out.append(tag)
        return out

    def _fallback_hashtags(self, selector: TrendFetchSelector | None) -> list[str]:
        candidates: list[str] = []
        if selector:
            candidates.extend(selector.search_terms)
        if self.query:
            candidates.append(self.query)

        out: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            text = str(raw or "").strip().lower()
            if not text:
                continue
            compact = "".join(ch for ch in text if ch.isalnum() or ch == "_")
            if not compact or compact in seen:
                continue
            seen.add(compact)
            out.append(compact)
        return out[:3]

    def _ms_tokens(self) -> list[str]:
        tokens = [t.strip() for t in self.ms_tokens_csv.split(",") if t.strip()]
        return tokens or [""]

    def _to_int(self, value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _run_async(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        out: dict[str, Any] = {}
        err: dict[str, BaseException] = {}

        def _target():
            try:
                out["value"] = asyncio.run(coro)
            except BaseException as exc:  # pragma: no cover
                err["error"] = exc

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join()
        if "error" in err:
            raise err["error"]
        return out.get("value", [])
