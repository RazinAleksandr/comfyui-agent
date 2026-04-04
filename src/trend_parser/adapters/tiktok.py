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


def _parse_proxy_url(proxy_url: str) -> dict:
    """Parse a proxy URL like http://user:pass@host:port into a Playwright proxy dict."""
    from urllib.parse import urlparse
    p = urlparse(proxy_url)
    result: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        result["username"] = p.username
    if p.password:
        result["password"] = p.password
    return result


class TikTokCustomAdapter:
    """TikTok adapter using the TikTokApi browser-based client.

    Fetch strategy is selected based on the selector:
      - search_terms present  → keyword search (/api/search/item/full/)
      - hashtags only         → hashtag feed  (/api/challenge/item_list/)
      - mode="trending"       → FYP recommend (/api/recommend/item_list/) + client-side filter
      - mode="mixed"          → keyword search + hashtag feed, merged
      - nothing provided      → FYP trending, unfiltered
    """

    def __init__(
        self,
        query: str,
        ms_tokens_csv: str | None = None,
        headless: bool = True,
        session_count: int = 1,
        sleep_after: int = 3,
        browser: str = "chromium",
        proxy_url: str | None = None,
    ):
        self.query = query
        self.ms_tokens_csv = ms_tokens_csv or ""
        self.headless = bool(headless)
        self.session_count = max(1, int(session_count))
        self.sleep_after = max(1, int(sleep_after))
        self.browser = browser or "chromium"
        self.proxy_url = proxy_url or None

    def fetch(self, limit: int, selector: TrendFetchSelector | None = None) -> list[RawTrendVideo]:
        if TikTokApi is None:
            raise RuntimeError(
                "TikTokApi is not installed. Install with: pip install -e '.[vps]'"
            )
        try:
            return self._run_async(self._fetch_async(limit=limit, selector=selector))
        except RuntimeError as exc:
            if "ms_token" not in str(exc).lower() and "failed" not in str(exc).lower():
                raise
            logger.info("[tiktok_custom] fetch failed, attempting token refresh...")
            new_token = self._run_async(self._refresh_ms_token())
            if not new_token:
                raise
            logger.info("[tiktok_custom] token refreshed, retrying fetch...")
            self.ms_tokens_csv = new_token
            return self._run_async(self._fetch_async(limit=limit, selector=selector))

    async def _fetch_async(self, limit: int, selector: TrendFetchSelector | None) -> list[RawTrendVideo]:
        mode = (selector.mode or "auto").lower() if selector else "auto"
        search_terms = [str(t).strip() for t in (selector.search_terms if selector else []) if str(t).strip()]
        hashtags = self._selector_hashtags(selector)

        ms_tokens = self._ms_tokens()
        async with TikTokApi() as api:
            session_kwargs: dict = dict(
                ms_tokens=ms_tokens,
                num_sessions=self.session_count,
                sleep_after=self.sleep_after,
                browser=self.browser,
                headless=self.headless,
            )
            if self.proxy_url:
                proxy = _parse_proxy_url(self.proxy_url)
                session_kwargs["proxies"] = [proxy] * self.session_count
            await api.create_sessions(**session_kwargs)

            if mode == "trending":
                return await self._fetch_trending(api, limit, selector)

            if mode == "mixed":
                return await self._fetch_mixed(api, limit, search_terms, hashtags, selector)

            if mode == "search" or (mode == "auto" and search_terms):
                results = await self._fetch_by_search(api, search_terms, limit, selector)
                if results:
                    return results
                # Fall through to hashtags if search yielded nothing
                logger.info("[tiktok_custom] keyword search empty, falling back to hashtags")

            if hashtags:
                return await self._fetch_by_hashtag(api, hashtags, limit, selector)

            # No terms at all — use fallback query as search, then trending
            fallback = self._build_fallback_query(selector)
            if fallback:
                results = await self._fetch_by_search(api, [fallback], limit, selector)
                if results:
                    return results

            logger.info("[tiktok_custom] no search terms/hashtags, falling back to FYP trending")
            return await self._fetch_trending(api, limit, selector)

    # --- Fetch strategies ---

    async def _fetch_by_search(
        self,
        api: Any,
        terms: list[str],
        limit: int,
        selector: TrendFetchSelector | None,
    ) -> list[RawTrendVideo]:
        """Keyword search via /api/search/item/full/ — ranked by relevance/engagement."""
        from TikTokApi.api.search import Search

        unique: dict[str, RawTrendVideo] = {}
        per_term_limit = max(1, (limit + len(terms) - 1) // len(terms))

        for term in terms:
            try:
                async for video in Search.search_type(term, "item", count=per_term_limit):
                    row = self._to_video(video.as_dict)
                    if row is None or not self._passes_filters(row, selector):
                        continue
                    key = row.source_item_id or f"s:{term}:{len(unique)}"
                    if key not in unique:
                        unique[key] = row
                    if len(unique) >= limit:
                        return list(unique.values())[:limit]
            except Exception as exc:
                logger.warning("[tiktok_custom] search '%s' failed: %s", term, exc)

        return list(unique.values())[:limit]

    async def _fetch_by_hashtag(
        self,
        api: Any,
        hashtags: list[str],
        limit: int,
        selector: TrendFetchSelector | None,
    ) -> list[RawTrendVideo]:
        """Hashtag feed via /api/challenge/item_list/ — chronological, niche-specific."""
        unique: dict[str, RawTrendVideo] = {}
        per_tag_limit = max(1, (limit + len(hashtags) - 1) // len(hashtags))
        failures: list[str] = []

        for tag in hashtags:
            try:
                async for video in api.hashtag(name=tag).videos(count=per_tag_limit):
                    row = self._to_video(video.as_dict)
                    if row is None or not self._passes_filters(row, selector):
                        continue
                    key = row.source_item_id or f"h:{tag}:{len(unique)}"
                    if key not in unique:
                        unique[key] = row
                    if len(unique) >= limit:
                        return list(unique.values())[:limit]
            except Exception as exc:
                logger.warning("[tiktok_custom] hashtag '%s' failed: %s", tag, exc)
                failures.append(f"{tag}: {exc}")

        if not unique and failures:
            raise RuntimeError(
                f"All {len(failures)} TikTok hashtag lookup(s) failed. "
                "ms_tokens may be expired — refresh from browser cookies. "
                f"Errors: {'; '.join(failures)}"
            )
        return list(unique.values())[:limit]

    async def _fetch_trending(
        self,
        api: Any,
        limit: int,
        selector: TrendFetchSelector | None,
    ) -> list[RawTrendVideo]:
        """FYP recommendations via /api/recommend/item_list/ — filtered client-side."""
        # TikTok's API silently fails with count > ~30; use 30 as the safe cap
        fetch_count = min(limit, 30)
        unique: dict[str, RawTrendVideo] = {}

        try:
            async for video in api.trending.videos(count=fetch_count):
                row = self._to_video(video.as_dict)
                if row is None or not self._passes_filters(row, selector):
                    continue
                key = row.source_item_id or f"t:{len(unique)}"
                if key not in unique:
                    unique[key] = row
                if len(unique) >= limit:
                    break
        except Exception as exc:
            logger.warning("[tiktok_custom] trending fetch failed: %s", exc)

        return list(unique.values())[:limit]

    async def _fetch_mixed(
        self,
        api: Any,
        limit: int,
        search_terms: list[str],
        hashtags: list[str],
        selector: TrendFetchSelector | None,
    ) -> list[RawTrendVideo]:
        """Merge keyword search + hashtag feed, deduplicated."""
        half = max(1, limit // 2)
        unique: dict[str, RawTrendVideo] = {}

        if search_terms:
            for row in await self._fetch_by_search(api, search_terms, half, selector):
                key = row.source_item_id or f"s:{len(unique)}"
                unique[key] = row

        if hashtags:
            for row in await self._fetch_by_hashtag(api, hashtags, half + (limit % 2), selector):
                key = row.source_item_id or f"h:{len(unique)}"
                if key not in unique:
                    unique[key] = row

        return list(unique.values())[:limit]

    # --- Helpers ---

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
            if tag and tag not in seen:
                seen.add(tag)
                out.append(tag)
        return out

    def _build_fallback_query(self, selector: TrendFetchSelector | None) -> str | None:
        """Build a single search query from search_terms, hashtags, or config query."""
        if selector:
            if selector.search_terms:
                return selector.search_terms[0]
            if selector.hashtags:
                return selector.hashtags[0]
        return self.query or None

    def _ms_tokens(self) -> list[str] | None:
        tokens = [t.strip() for t in self.ms_tokens_csv.split(",") if t.strip()]
        return tokens or None  # None → TikTokApi handles session init itself

    @staticmethod
    async def _refresh_ms_token() -> str | None:
        """Visit TikTok in a headless browser to obtain a fresh msToken cookie."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("[tiktok_custom] playwright not available for token refresh")
            return None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True, args=["--headless=new"])
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto("https://www.tiktok.com", wait_until="domcontentloaded", timeout=30000)
                for _ in range(10):
                    await asyncio.sleep(2)
                    cookies = await context.cookies()
                    for c in cookies:
                        if c["name"] == "msToken" and len(c["value"]) > 20:
                            logger.info("[tiktok_custom] refreshed msToken successfully")
                            return c["value"]
            finally:
                await browser.close()
                await pw.stop()
        except Exception as exc:
            logger.warning("[tiktok_custom] token refresh failed: %s", exc)
        return None

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
