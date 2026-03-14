from __future__ import annotations

import math
import random
import time
from datetime import UTC, datetime, timedelta

import requests
from dateutil import parser

from trend_parser.adapters.types import RawTrendVideo, TrendFetchSelector


class ApifyTrendAdapter:
    RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        token: str,
        actor_id: str,
        platform: str,
        query: str,
        request_retries: int = 4,
        retry_backoff_sec: float = 1.5,
        retry_max_backoff_sec: float = 12.0,
    ):
        self.token = token
        self.actor_id = actor_id.replace("/", "~")
        self.platform = platform
        self.query = query
        self.base_url = "https://api.apify.com/v2"
        self.request_retries = max(int(request_retries), 1)
        self.retry_backoff_sec = max(float(retry_backoff_sec), 0.1)
        self.retry_max_backoff_sec = max(float(retry_max_backoff_sec), self.retry_backoff_sec)

    def fetch(self, limit: int, selector: TrendFetchSelector | None = None) -> list[RawTrendVideo]:
        actor_input = self._build_actor_input(limit=limit, selector=selector)
        run = self._start_actor_run(actor_input=actor_input)
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            return []

        rows = self._read_dataset(dataset_id, limit)
        videos: list[RawTrendVideo] = []
        for row in rows:
            videos.extend(self._normalize_rows(row))

        unique: dict[tuple[str, str], RawTrendVideo] = {}
        for video in videos:
            key = (
                (video.source_item_id or "").strip(),
                (video.video_url or "").strip(),
            )
            if key in unique:
                continue
            unique[key] = video
        return self._apply_post_filters(list(unique.values()), selector)

    def _start_actor_run(self, actor_input: dict) -> dict:
        endpoint = f"{self.base_url}/acts/{self.actor_id}/runs"
        params = {"waitForFinish": 120}
        headers = {"Authorization": f"Bearer {self.token}"}

        response = self._request_with_retry(
            "post",
            endpoint,
            params=params,
            headers=headers,
            json=actor_input,
            timeout=140,
            operation=f"start actor run ({self.actor_id})",
        )
        return response.json().get("data", {})

    def _read_dataset(self, dataset_id: str, limit: int) -> list[dict]:
        endpoint = f"{self.base_url}/datasets/{dataset_id}/items"
        params = {
            "clean": "true",
            "limit": limit,
            "format": "json",
        }
        headers = {"Authorization": f"Bearer {self.token}"}
        response = self._request_with_retry(
            "get",
            endpoint,
            params=params,
            headers=headers,
            timeout=60,
            operation=f"read dataset ({dataset_id})",
        )
        data = response.json()
        if isinstance(data, list):
            return data
        return []

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json: dict | None = None,
        timeout: int = 60,
        operation: str = "apify request",
    ) -> requests.Response:
        last_error: Exception | None = None
        last_response: requests.Response | None = None

        for attempt in range(1, self.request_retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=headers,
                    json=json,
                    timeout=timeout,
                )
                if response.ok:
                    return response

                last_response = response
                if response.status_code not in self.RETRYABLE_STATUSES:
                    response.raise_for_status()

            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
            except requests.HTTPError as exc:
                if last_response is not None and last_response.status_code not in self.RETRYABLE_STATUSES:
                    raise
                last_error = exc
            except requests.RequestException as exc:
                last_error = exc

            if attempt >= self.request_retries:
                break

            delay = min(self.retry_backoff_sec * (2 ** (attempt - 1)), self.retry_max_backoff_sec)
            delay *= random.uniform(0.85, 1.15)
            time.sleep(delay)

        if last_response is not None:
            try:
                last_response.raise_for_status()
            except requests.HTTPError as exc:
                body = (last_response.text or "").strip()
                snippet = body[:400] if body else ""
                details = (
                    f"{operation} failed after {self.request_retries} attempts: "
                    f"HTTP {last_response.status_code}"
                )
                if snippet:
                    details = f"{details}; response={snippet}"
                raise RuntimeError(details) from exc
        if last_error is not None:
            raise RuntimeError(
                f"{operation} failed after {self.request_retries} attempts: {last_error}"
            ) from last_error
        raise RuntimeError(f"{operation} failed after {self.request_retries} attempts")

    def _normalize_row(self, row: dict) -> RawTrendVideo | None:
        video_url = row.get("videoUrl") or row.get("webVideoUrl")
        post_url = row.get("url") or row.get("webVideoUrl")
        caption = row.get("text") or row.get("caption") or row.get("description")

        hashtags = self._normalize_hashtags(row.get("hashtags") or [])
        if not hashtags:
            hashtags = self._extract_hashtags_from_caption(caption)

        media_type = str(row.get("type") or row.get("productType") or "").lower().strip()
        if self.platform == "instagram":
            has_video_hints = bool(
                video_url
                or row.get("videoViewCount")
                or row.get("videoPlayCount")
                or row.get("videoDuration")
                or row.get("audioUrl")
                or media_type in {"video", "reel", "clips"}
            )
            if not has_video_hints:
                return None
            if not video_url:
                video_url = post_url

        published_at = None
        for candidate in ["createTimeISO", "timestamp", "publishedAt", "createTime"]:
            value = row.get(candidate)
            if not value:
                continue
            try:
                published_at = parser.parse(str(value))
                break
            except (ValueError, TypeError):
                continue

        stats = row.get("stats") or {}
        music_meta = row.get("musicMeta") or {}
        music_info = row.get("musicInfo") or {}

        views = self._first_int(
            row.get("playCount"),
            row.get("videoViewCount"),
            row.get("videoPlayCount"),
            row.get("igPlayCount"),
            stats.get("playCount"),
            row.get("views"),
            row.get("viewCount"),
            default=0,
        )
        likes = self._first_int(
            row.get("diggCount"),
            row.get("likesCount"),
            stats.get("diggCount"),
            row.get("likes"),
            default=0,
        )
        comments = self._first_int(
            row.get("commentCount"),
            row.get("commentsCount"),
            stats.get("commentCount"),
            row.get("comments"),
            default=0,
        )
        shares = self._first_int(
            row.get("shareCount"),
            stats.get("shareCount"),
            row.get("shares"),
            default=0,
        )
        views = max(0, views)
        likes = max(0, likes)
        comments = max(0, comments)
        shares = max(0, shares)

        return RawTrendVideo(
            platform=self.platform,
            source_item_id=str(row.get("id") or row.get("shortCode") or ""),
            video_url=video_url or post_url,
            caption=caption,
            hashtags=[str(h).strip("#") for h in hashtags if h],
            audio=(
                row.get("musicName")
                or row.get("audioName")
                or row.get("music")
                or music_meta.get("musicName")
                or music_meta.get("title")
                or music_info.get("song_name")
                or music_info.get("artist_name")
                or None
            ),
            style_hint=row.get("type") or row.get("contentType") or None,
            published_at=published_at,
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            raw_payload=row,
        )

    def _normalize_rows(self, row: dict) -> list[RawTrendVideo]:
        normalized: list[RawTrendVideo] = []
        parent = self._normalize_row(row)
        if parent is not None:
            normalized.append(parent)

        if self.platform != "instagram":
            return normalized

        child_posts = row.get("childPosts")
        if not isinstance(child_posts, list):
            return normalized

        for child in child_posts:
            if not isinstance(child, dict):
                continue
            merged_child = self._merge_instagram_child_row(parent=row, child=child)
            child_video = self._normalize_row(merged_child)
            if child_video is not None:
                normalized.append(child_video)

        return normalized

    def _merge_instagram_child_row(self, parent: dict, child: dict) -> dict:
        merged = dict(parent)
        merged.update(child)
        for key in ["url", "shortCode", "ownerId", "ownerUsername", "ownerFullName", "musicInfo"]:
            if merged.get(key) is None:
                merged[key] = parent.get(key)
        if not merged.get("caption"):
            merged["caption"] = parent.get("caption")
        if not merged.get("hashtags"):
            merged["hashtags"] = parent.get("hashtags")
        if merged.get("likesCount") is None:
            merged["likesCount"] = parent.get("likesCount")
        if merged.get("commentsCount") is None:
            merged["commentsCount"] = parent.get("commentsCount")
        if merged.get("timestamp") is None:
            merged["timestamp"] = parent.get("timestamp")
        return merged

    def _build_actor_input(self, limit: int, selector: TrendFetchSelector | None) -> dict:
        actor = self.actor_id.lower()
        if "clockworks~tiktok-scraper" in actor:
            return self._build_tiktok_actor_input(limit=limit, selector=selector)
        if "instagram-api-scraper" in actor or "instagram-scraper" in actor:
            return self._build_instagram_api_actor_input(limit=limit, selector=selector)

        query = self._selector_query(selector)
        actor_input: dict = {
            "search": query,
            "resultsLimit": limit,
        }
        if selector and selector.source_params:
            actor_input.update(selector.source_params)
            actor_input.setdefault("resultsLimit", limit)
            actor_input.setdefault("search", query)
        return actor_input

    def _selector_query(self, selector: TrendFetchSelector | None) -> str:
        if selector is None:
            return self.query

        mode = (selector.mode or "auto").lower().strip()
        tags = [f"#{str(tag).strip().lstrip('#')}" for tag in selector.hashtags if str(tag).strip()]
        terms = [str(term).strip() for term in selector.search_terms if str(term).strip()]

        if mode == "hashtag" and tags:
            return " ".join(tags)
        if mode == "search" and terms:
            return " ".join(terms)
        if mode == "mixed":
            mixed = [*tags, *terms]
            if mixed:
                return " ".join(mixed)

        fallback = [*tags, *terms]
        if fallback:
            return " ".join(fallback)
        return self.query

    def _build_tiktok_actor_input(self, limit: int, selector: TrendFetchSelector | None) -> dict:
        hashtags = [str(tag).strip().lstrip("#") for tag in (selector.hashtags if selector else []) if str(tag).strip()]
        terms = [str(term).strip() for term in (selector.search_terms if selector else []) if str(term).strip()]
        source_count = len(hashtags) if hashtags else len(terms) if terms else 1
        per_source_limit = max(1, math.ceil(limit / source_count))

        actor_input: dict = {
            "resultsPerPage": per_source_limit,
            "resultsLimit": limit,
        }
        if hashtags:
            actor_input["hashtags"] = hashtags
        elif terms:
            actor_input["searchQueries"] = terms
            actor_input["searchSection"] = "/video"
        else:
            actor_input["searchQueries"] = [self.query]
            actor_input["searchSection"] = "/video"

        if selector and selector.source_params:
            actor_input.update(selector.source_params)
            actor_input.setdefault("resultsLimit", limit)
        return actor_input

    def _build_instagram_api_actor_input(self, limit: int, selector: TrendFetchSelector | None) -> dict:
        hashtags = [str(tag).strip().lstrip("#") for tag in (selector.hashtags if selector else []) if str(tag).strip()]
        terms = [str(term).strip() for term in (selector.search_terms if selector else []) if str(term).strip()]

        def to_tag_url(tag: str) -> str:
            slug = "".join(ch for ch in tag.lower() if ch.isalnum() or ch == "_")
            return f"https://www.instagram.com/explore/tags/{slug}/"

        actor_input: dict = {
            "resultsType": "reels",
            "resultsLimit": limit,
        }

        if hashtags:
            actor_input["directUrls"] = [to_tag_url(tag) for tag in hashtags]
        elif terms:
            actor_input["directUrls"] = [to_tag_url(term.replace(" ", "")) for term in terms]
        else:
            actor_input["search"] = self.query
            actor_input["searchType"] = "hashtag"
            actor_input["searchLimit"] = max(limit, 1)

        if selector and selector.source_params:
            actor_input.update(selector.source_params)
            actor_input.setdefault("resultsLimit", limit)
            actor_input.setdefault("resultsType", "reels")
        return actor_input

    def _apply_post_filters(
        self,
        videos: list[RawTrendVideo],
        selector: TrendFetchSelector | None,
    ) -> list[RawTrendVideo]:
        if selector is None:
            return videos

        min_views = selector.min_views
        min_likes = selector.min_likes
        published_within_days = selector.published_within_days
        if min_views is None and min_likes is None and published_within_days is None:
            return videos

        filtered: list[RawTrendVideo] = []
        recent_cutoff = None
        if published_within_days is not None:
            recent_cutoff = datetime.now(UTC) - timedelta(days=max(1, int(published_within_days)))

        for video in videos:
            if min_views is not None and video.views < min_views:
                continue
            if min_likes is not None and video.likes < min_likes:
                continue
            if recent_cutoff is not None:
                if video.published_at is None:
                    continue
                published_at = video.published_at
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=UTC)
                else:
                    published_at = published_at.astimezone(UTC)
                if published_at < recent_cutoff:
                    continue
            filtered.append(video)
        return filtered

    def _normalize_hashtags(self, hashtags: list | str) -> list[str]:
        if isinstance(hashtags, str):
            return [tag.strip("#") for tag in hashtags.split() if tag.startswith("#")]
        if not isinstance(hashtags, list):
            return []

        output: list[str] = []
        for tag in hashtags:
            if isinstance(tag, str):
                clean = tag.strip().lstrip("#")
                if clean:
                    output.append(clean)
                continue
            if isinstance(tag, dict):
                value = tag.get("name") or tag.get("tag") or tag.get("hash")
                if value:
                    output.append(str(value).strip().lstrip("#"))
        return output

    def _extract_hashtags_from_caption(self, caption: str | None) -> list[str]:
        if not caption:
            return []
        return [token.strip().lstrip("#") for token in caption.split() if token.startswith("#")]

    def _first_int(self, *values, default: int = 0) -> int:
        for value in values:
            parsed = self._to_int(value)
            if parsed is not None:
                return parsed
        return default

    def _to_int(self, value) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            raw = value.strip().lower()
            if not raw:
                return None
            multiplier = 1
            if raw.endswith("k"):
                multiplier = 1_000
                raw = raw[:-1]
            elif raw.endswith("m"):
                multiplier = 1_000_000
                raw = raw[:-1]
            elif raw.endswith("b"):
                multiplier = 1_000_000_000
                raw = raw[:-1]
            raw = raw.replace(",", "").replace(" ", "")
            try:
                return int(float(raw) * multiplier)
            except ValueError:
                return None
        return None
