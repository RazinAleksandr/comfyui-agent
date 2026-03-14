from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from trend_parser.adapters.apify import ApifyTrendAdapter
from trend_parser.adapters.instagram import InstagramCustomAdapter
from trend_parser.adapters.seed import SeedTrendAdapter
from trend_parser.adapters.tiktok import TikTokCustomAdapter
from trend_parser.adapters.types import RawTrendVideo, TrendFetchSelector
from trend_parser.config import ParserConfig

VALID_PLATFORMS = {"tiktok", "instagram"}
VALID_SOURCES = {"seed", "apify", "tiktok_custom", "instagram_custom"}

STOPWORDS = {
    "the", "and", "for", "you", "with", "this", "that", "from", "your", "have",
    "just", "when", "into", "over", "about", "what", "why", "how", "our", "are",
    "can", "its", "out", "new", "viral", "trend", "trending", "reel", "reels",
    "video", "videos",
}

STYLE_KEYWORDS = {
    "tutorial": ["how to", "tutorial", "step by step", "guide"],
    "before_after": ["before", "after", "transformation"],
    "day_in_life": ["day in my life", "ditl", "routine"],
    "behind_the_scenes": ["behind the scenes", "bts", "making of"],
    "listicle": ["top 3", "top 5", "top 10", "things"],
    "storytime": ["storytime", "plot twist", "what happened"],
}


class TrendIngestService:
    def __init__(self, config: ParserConfig, seed_dir: Path):
        self.config = config
        self.seed_dir = seed_dir

    def collect_raw(
        self,
        platforms: list[str],
        limit_per_platform: int,
        source: str | None = None,
        sources_by_platform: dict[str, str] | None = None,
        selectors: dict[str, dict] | None = None,
    ) -> dict[str, list[RawTrendVideo]]:
        platforms_normalized = _normalize_platforms(platforms)
        source_strategy = (source or self.config.default_source).lower().strip()
        if source_strategy not in VALID_SOURCES:
            raise ValueError("Unsupported source. Use seed, apify, tiktok_custom, or instagram_custom.")
        sources_by_platform = {str(k).lower(): str(v).lower() for k, v in (sources_by_platform or {}).items()}
        selectors = selectors or {}

        platform_videos: dict[str, list[RawTrendVideo]] = {}
        for platform in platforms_normalized:
            selector = TrendFetchSelector(**(selectors.get(platform) or {}))
            platform_source = sources_by_platform.get(platform, source_strategy)
            videos = self._fetch_videos(
                platform=platform,
                limit=limit_per_platform,
                source=platform_source,
                selector=selector,
            )
            platform_videos[platform] = videos
        return platform_videos

    def extract_signals(self, platform_videos: dict[str, list[RawTrendVideo]]) -> list[dict]:
        signals: list[dict] = []
        for platform, videos in platform_videos.items():
            hashtag_counter: Counter[str] = Counter()
            audio_counter: Counter[str] = Counter()
            topic_counter: Counter[str] = Counter()
            style_counter: Counter[str] = Counter()
            hook_counter: Counter[str] = Counter()

            for video in videos:
                weight = max(score_video(video), 0.1)

                for tag in video.hashtags:
                    clean_tag = tag.lower().strip().replace("#", "")
                    if clean_tag:
                        hashtag_counter[clean_tag] += weight

                if video.audio:
                    audio_counter[video.audio.lower().strip()] += weight

                caption = (video.caption or "").strip().lower()
                if caption:
                    for token in _caption_tokens(caption):
                        topic_counter[token] += weight

                    hook = _hook(caption)
                    if hook:
                        hook_counter[hook] += weight

                    style = video.style_hint or _infer_style(caption)
                    if style:
                        style_counter[style.lower().strip()] += weight

            signals.extend(_counter_to_signals(platform, "hashtag", hashtag_counter, top_n=12))
            signals.extend(_counter_to_signals(platform, "audio", audio_counter, top_n=8))
            signals.extend(_counter_to_signals(platform, "topic", topic_counter, top_n=12))
            signals.extend(_counter_to_signals(platform, "style", style_counter, top_n=8))
            signals.extend(_counter_to_signals(platform, "hook", hook_counter, top_n=8))
        return signals

    def build_summary(self, platform_videos: dict[str, list[RawTrendVideo]], extracted_signals: list[dict]) -> dict:
        by_platform = defaultdict(lambda: {"videos": 0, "signals": defaultdict(list)})
        for platform, videos in platform_videos.items():
            by_platform[platform]["videos"] = len(videos)
        for signal in extracted_signals:
            by_platform[signal["platform"]]["signals"][signal["signal_type"]].append(
                {"value": signal["value"], "score": signal["score"]}
            )
        output = {"platforms": {}, "totals": {"videos": 0, "signals": len(extracted_signals)}}
        for platform, payload in by_platform.items():
            output["platforms"][platform] = {"videos": payload["videos"], "signals": dict(payload["signals"])}
            output["totals"]["videos"] += payload["videos"]
        return output

    def _fetch_videos(
        self,
        platform: str,
        limit: int,
        source: str,
        selector: TrendFetchSelector | None = None,
    ) -> list[RawTrendVideo]:
        selector = self._optimize_selector(selector, source=source)

        if source == "seed":
            videos = SeedTrendAdapter(platform=platform, seed_dir=self.seed_dir).fetch(limit=limit, selector=selector)
            return _select_top_videos(videos=videos, limit=limit, selector=selector)

        if source == "apify":
            multiplier = max(int(self.config.apify_overfetch_multiplier), 1)
            fetch_limit = min(max(limit * multiplier, limit), 200)
            actor_id = self.config.tiktok_apify_actor if platform == "tiktok" else self.config.instagram_apify_actor
            query = self.config.tiktok_query if platform == "tiktok" else self.config.instagram_query
            if not self.config.apify_token or not actor_id:
                raise RuntimeError(
                    f"Apify source selected, but missing token/actor for platform={platform}. "
                    "Check APIFY_TOKEN and platform actor env."
                )
            try:
                videos = ApifyTrendAdapter(
                    token=self.config.apify_token,
                    actor_id=actor_id,
                    platform=platform,
                    query=query,
                    request_retries=self.config.apify_request_retries,
                    retry_backoff_sec=self.config.apify_retry_backoff_sec,
                    retry_max_backoff_sec=self.config.apify_retry_max_backoff_sec,
                ).fetch(limit=fetch_limit, selector=selector)
                return _select_top_videos(videos=videos, limit=limit, selector=selector)
            except Exception as exc:
                if self.config.apify_fallback_to_seed:
                    videos = SeedTrendAdapter(platform=platform, seed_dir=self.seed_dir).fetch(
                        limit=limit, selector=selector,
                    )
                    return _select_top_videos(videos=videos, limit=limit, selector=selector)
                raise RuntimeError(f"Apify fetch failed for platform={platform}: {exc}") from exc

        if source == "tiktok_custom":
            if platform != "tiktok":
                raise RuntimeError("source=tiktok_custom supports only platform=tiktok.")
            videos = TikTokCustomAdapter(
                query=self.config.tiktok_query,
                ms_tokens_csv=self.config.tiktok_ms_tokens,
                headless=self.config.tiktok_custom_headless,
                session_count=self.config.tiktok_custom_sessions,
                sleep_after=self.config.tiktok_custom_sleep_after,
                browser=self.config.tiktok_custom_browser,
            ).fetch(limit=limit, selector=selector)
            return _select_top_videos(videos=videos, limit=limit, selector=selector)

        if source == "instagram_custom":
            if platform != "instagram":
                raise RuntimeError("source=instagram_custom supports only platform=instagram.")
            videos = InstagramCustomAdapter(
                query=self.config.instagram_query,
                username=self.config.instagram_custom_username,
                password=self.config.instagram_custom_password,
                session_file=self.config.instagram_custom_session_file,
                max_posts_per_tag=self.config.instagram_custom_max_posts_per_tag,
            ).fetch(limit=limit, selector=selector)
            return _select_top_videos(videos=videos, limit=limit, selector=selector)

        # Fallback to seed
        videos = SeedTrendAdapter(platform=platform, seed_dir=self.seed_dir).fetch(limit=limit, selector=selector)
        return _select_top_videos(videos=videos, limit=limit, selector=selector)

    def _optimize_selector(
        self, selector: TrendFetchSelector | None, source: str
    ) -> TrendFetchSelector | None:
        if selector is None:
            return None

        def _clean(items: list[str]) -> list[str]:
            seen: set[str] = set()
            output: list[str] = []
            for raw in items:
                item = str(raw or "").strip()
                if not item:
                    continue
                key = item.lower()
                if key in seen:
                    continue
                seen.add(key)
                output.append(item)
            return output

        hashtags = _clean(selector.hashtags)
        search_terms = _clean(selector.search_terms)

        if source == "apify" and self.config.apify_cost_optimized:
            max_terms = max(int(self.config.apify_max_selector_terms), 1)
            hashtags = hashtags[:max_terms]
            search_terms = search_terms[:max_terms]

        return TrendFetchSelector(
            mode=selector.mode,
            search_terms=search_terms,
            hashtags=hashtags,
            min_views=selector.min_views,
            min_likes=selector.min_likes,
            published_within_days=selector.published_within_days,
            require_topic_match=bool(selector.require_topic_match),
            source_params=selector.source_params,
        )


# --- Scoring & ranking helpers (module-level, no DB) ---

def score_video(video: RawTrendVideo) -> float:
    interactions = video.likes + video.comments + video.shares
    reach_proxy = max(
        video.views,
        (video.likes * 25) + (video.comments * 40) + (video.shares * 60),
    )
    reach_component = math.log(reach_proxy + 1, 10)
    engagement = interactions / max(reach_proxy, 1)

    recency_boost = 0.15
    if video.published_at:
        published_utc = _to_utc(video.published_at)
        days_old = max((datetime.now(UTC) - published_utc).total_seconds() / 86_400, 0.0)
        recency_boost = max(0.03, math.exp(-(days_old / 21.0)))

    return round((reach_component * 0.58) + (engagement * 1.15) + (recency_boost * 1.45), 4)


def _normalize_platforms(platforms: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for platform in platforms:
        p = (platform or "").lower().strip()
        if p in VALID_PLATFORMS and p not in seen:
            normalized.append(p)
            seen.add(p)
    if not normalized:
        raise ValueError("No supported platforms provided. Use tiktok and/or instagram.")
    return normalized


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _published_rank_value(published_at: datetime | None) -> float:
    if not published_at:
        return 0.0
    return _to_utc(published_at).timestamp()


def _normalize_term(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def _selector_terms(selector: TrendFetchSelector | None) -> tuple[list[str], list[str]]:
    if selector is None:
        return [], []
    hashtags = [_normalize_term(tag.lstrip("#")) for tag in selector.hashtags]
    hashtags = [tag for tag in hashtags if tag]
    search_terms = [_normalize_term(term) for term in selector.search_terms]
    search_terms = [term for term in search_terms if term]
    return hashtags, search_terms


def _topic_match_score(video: RawTrendVideo, selector: TrendFetchSelector | None) -> int:
    hashtags, search_terms = _selector_terms(selector)
    if not hashtags and not search_terms:
        return 0
    tags = {_normalize_term(tag.lstrip("#")) for tag in (video.hashtags or []) if str(tag).strip()}
    tags.discard("")
    text = _normalize_term(f"{video.caption or ''} {' '.join(video.hashtags or [])}")
    score = 0
    for tag in hashtags:
        if tag in tags:
            score += 3
        elif tag in text:
            score += 2
    for term in search_terms:
        if term in text:
            score += 2
    return score


def _apply_selector_focus(
    videos: list[RawTrendVideo], selector: TrendFetchSelector | None
) -> list[RawTrendVideo]:
    if selector is None:
        return videos
    hashtags, search_terms = _selector_terms(selector)
    if not hashtags and not search_terms:
        return videos
    matched = [video for video in videos if _topic_match_score(video, selector) > 0]
    if matched:
        return matched
    if selector.require_topic_match:
        return []
    return videos


def _select_top_videos(
    videos: list[RawTrendVideo], limit: int, selector: TrendFetchSelector | None = None
) -> list[RawTrendVideo]:
    focused = _apply_selector_focus(videos, selector)
    ranked = sorted(
        focused,
        key=lambda video: (
            _topic_match_score(video, selector),
            score_video(video),
            video.views,
            _published_rank_value(video.published_at),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _caption_tokens(caption: str) -> list[str]:
    tokens = [token for token in re.split(r"[^a-z0-9]+", caption) if token]
    return [t for t in tokens if len(t) >= 4 and t not in STOPWORDS]


def _hook(caption: str) -> str | None:
    first_part = re.split(r"[.!?\n]", caption)[0].strip()
    if len(first_part) < 8:
        return None
    return first_part[:120]


def _infer_style(caption: str) -> str | None:
    for style, keywords in STYLE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in caption:
                return style
    return None


def _counter_to_signals(platform: str, signal_type: str, counter: Counter[str], top_n: int) -> list[dict]:
    return [
        {
            "platform": platform,
            "signal_type": signal_type,
            "value": value,
            "score": round(score, 4),
            "metadata": {"rank": idx + 1},
        }
        for idx, (value, score) in enumerate(counter.most_common(top_n))
    ]
