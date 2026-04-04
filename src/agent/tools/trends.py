"""Platform-native trend research — TikTok/Instagram.

Strategy (free, no paid API needed):
  TikTok:
    Pass 1 — Fetch FYP trending via TikTokCustomAdapter (mode=trending).
             Extract music id/title/artist + hashtags from every video's raw_payload.
    Pass 2 — For top-N sounds: fetch their videos via api.sound(id).videos().
             Aggregate hashtags per sound → niche-correlated hashtag clusters.
  Instagram:
    Uses InstagramCustomAdapter hashtag feed with niche-derived tags.
    Aggregates hashtags from fetched reels.

Main entrypoints:
    research_tiktok_trends(niche, limit=60)  → dict
    research_instagram_trends(niche, limit=30) → dict
    research_platform_trends(platform, niche, limit=50) → dict
    search_trends(topic, days=30) → dict  (backward-compat wrapper)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Content pattern signals
# ---------------------------------------------------------------------------

_PATTERNS = {
    "pov": r"\bpov\b",
    "tutorial": r"\b(how to|tutorial|step by step|tips|guide)\b",
    "day_in_life": r"\b(day in (my|the|a) life|vlog|daily routine|daily vlog)\b",
    "transformation": r"\b(transformation|before|after|glow.?up|results)\b",
    "storytime": r"\b(story.?time|story time)\b",
    "motivation": r"\b(motivat|inspir|mindset|uplift)\b",
    "challenge": r"\b(challenge|trend)\b",
    "review": r"\b(review|rate|rating|honest|honest opinion)\b",
    "aesthetic": r"\b(aesthetic|vibe|cozy|grwm|get ready)\b",
}

# Noise hashtags to always strip from output
_TAG_NOISE = {
    "fyp", "foryou", "foryoupage", "viral", "trending", "tiktok", "reels",
    "instagram", "explore", "fy", "fypシ", "foryoupageofficiall", "xyzbca",
}


def research_platform_trends(
    platform: str,
    niche: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Platform-native trend research.

    Args:
        platform: "tiktok" | "instagram" | "both"
        niche: Influencer niche / topic (e.g. "yoga wellness")
        limit: Number of videos to fetch per platform

    Returns:
        {
          "platform": ...,
          "niche": ...,
          "trending_sounds": [{id, title, artist, fyp_count, niche_score}, ...],
          "hashtags": [(tag, count), ...],        # sorted by count, noise filtered
          "niche_hashtags": [(tag, count), ...],  # hashtags most relevant to niche
          "search_queries": [str, ...],           # ready for start-pipeline --hashtags
          "content_patterns": [(pattern, count), ...],
          "sound_hashtag_clusters": {sound_id: {title, artist, hashtags: [(tag,count)...]}},
          "errors": [str, ...]
        }
    """
    if platform == "both":
        tt = research_tiktok_trends(niche, limit=limit)
        ig = research_instagram_trends(niche, limit=max(20, limit // 2))
        # Merge hashtags
        merged_tags = Counter()
        for tag, cnt in tt.get("hashtags", []):
            merged_tags[tag] += cnt
        for tag, cnt in ig.get("hashtags", []):
            merged_tags[tag] += cnt
        tt["hashtags"] = merged_tags.most_common(30)
        tt["instagram"] = ig
        return tt

    if platform == "instagram":
        return research_instagram_trends(niche, limit=limit)

    return research_tiktok_trends(niche, limit=limit)


def research_tiktok_trends(niche: str, limit: int = 60) -> dict[str, Any]:
    """Research TikTok trends.

    Primary path (when SCRAPECREATORS_API_KEY is set):
      Keyword search → returns niche-specific videos sorted by views.

    Fallback path (FYP + sound mining):
      Pass 1: Fetch trending FYP videos → extract sounds + hashtags.
      Pass 2: For each top sound, fetch its videos → aggregate hashtags.
    """
    result: dict[str, Any] = {
        "platform": "tiktok",
        "niche": niche,
        "trending_sounds": [],
        "hashtags": [],
        "niche_hashtags": [],
        "search_queries": [],
        "content_patterns": [],
        "sound_hashtag_clusters": {},
        "errors": [],
    }

    sc_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    if sc_key:
        return _research_tiktok_via_scrapecreators(niche, limit, sc_key, result)
    return _research_tiktok_via_fyp(niche, limit, result)


def _research_tiktok_via_scrapecreators(
    niche: str, limit: int, api_key: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Trend research using ScrapeCreators keyword search (niche-specific, sorted by views)."""
    try:
        from trend_parser.adapters.scrapecreators import ScrapeCreatorsTikTokAdapter
    except ImportError as e:
        result["errors"].append(f"ScrapeCreatorsTikTokAdapter import failed: {e}")
        return result

    from trend_parser.adapters.types import TrendFetchSelector
    adapter = ScrapeCreatorsTikTokAdapter(query=niche, api_key=api_key)
    selector = TrendFetchSelector(mode="search", search_terms=[niche])

    try:
        videos = adapter.fetch(limit=limit, selector=selector)
    except Exception as e:
        result["errors"].append(f"ScrapeCreators fetch failed: {e}")
        return result

    if not videos:
        result["errors"].append("ScrapeCreators returned 0 videos for this query.")
        return result

    hashtag_counter: Counter[str] = Counter()
    caption_texts: list[str] = []
    niche_kw = set(_tokenize(niche))
    dates = []

    for video in videos:
        for tag in video.hashtags:
            tag = tag.lower().strip().lstrip("#")
            if tag and tag not in _TAG_NOISE and len(tag) > 1:
                hashtag_counter[tag] += 1
        if video.caption:
            caption_texts.append(video.caption.lower())
        if video.published_at:
            dates.append(video.published_at)

    all_hashtags = hashtag_counter.most_common(30)
    niche_hashtags = _rank_niche_tags(hashtag_counter, niche_kw)
    patterns = _detect_patterns(caption_texts)
    queries = _build_queries(niche, niche_hashtags, all_hashtags)

    result.update({
        "hashtags": all_hashtags,
        "niche_hashtags": niche_hashtags,
        "search_queries": queries,
        "content_patterns": patterns,
        "video_count": len(videos),
        "date_range": {
            "oldest": min(dates).isoformat() if dates else None,
            "newest": max(dates).isoformat() if dates else None,
        },
        "videos": [
            {
                "url": v.video_url,
                "caption": (v.caption or "")[:80],
                "views": v.views,
                "likes": v.likes,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "hashtags": v.hashtags[:6],
            }
            for v in videos
        ],
        "source": "scrapecreators",
    })
    return result


def _research_tiktok_via_fyp(niche: str, limit: int, result: dict[str, Any]) -> dict[str, Any]:
    """Fallback trend research using TikTok FYP + two-pass sound mining."""
    try:
        from trend_parser.adapters.tiktok import TikTokCustomAdapter
    except ImportError as e:
        result["errors"].append(f"TikTokCustomAdapter import failed: {e}")
        return result

    ms_tokens = os.environ.get("TIKTOK_MS_TOKEN") or os.environ.get("TIKTOK_MS_TOKENS", "")
    proxy_url = os.environ.get("PROXY_URL") or os.environ.get("TIKTOK_PROXY_URL") or None
    browser = os.environ.get("TIKTOK_BROWSER", "webkit")
    adapter = TikTokCustomAdapter(
        query=niche,
        ms_tokens_csv=ms_tokens,
        headless=True,
        session_count=1,
        sleep_after=3,
        proxy_url=proxy_url,
        browser=browser,
    )

    from trend_parser.adapters.types import TrendFetchSelector
    selector = TrendFetchSelector(mode="trending")

    try:
        videos = adapter.fetch(limit=limit, selector=selector)
    except Exception as e:
        result["errors"].append(f"TikTok FYP fetch failed: {e}")
        return result

    # If 0 results, try auto-refreshing the msToken via Playwright (token may be expired)
    if not videos:
        logger.info("[trends] No videos — attempting msToken auto-refresh via Playwright…")
        try:
            new_token = _run_async(adapter._refresh_ms_token())
            if new_token:
                adapter.ms_tokens_csv = new_token
                os.environ["TIKTOK_MS_TOKEN"] = new_token
                videos = adapter.fetch(limit=limit, selector=selector)
        except Exception as e:
            result["errors"].append(f"msToken auto-refresh failed: {e}")

    if not videos:
        result["errors"].append(
            "TikTok FYP returned 0 videos. "
            "Set TIKTOK_MS_TOKEN in .env (copy msToken cookie from tiktok.com browser session), "
            "or ensure Playwright is installed for auto-refresh."
        )
        return result

    # ── Pass 1: aggregate from FYP batch ───────────────────────────────────
    sound_counter: Counter[str] = Counter()
    sound_meta: dict[str, dict] = {}  # id → {title, artist}
    hashtag_counter: Counter[str] = Counter()
    caption_texts: list[str] = []
    niche_kw = set(_tokenize(niche))

    for video in videos:
        # Extract sound from raw_payload (music.id is only in raw payload)
        sound = _extract_sound(video.raw_payload)
        if sound and sound["id"]:
            sid = sound["id"]
            sound_counter[sid] += 1
            if sid not in sound_meta:
                sound_meta[sid] = {"title": sound["title"], "artist": sound["artist"]}

        # Hashtags
        for tag in video.hashtags:
            tag = tag.lower().strip().lstrip("#")
            if tag and tag not in _TAG_NOISE and len(tag) > 1:
                hashtag_counter[tag] += 1

        if video.caption:
            caption_texts.append(video.caption.lower())

    # ── Pass 2: sound-specific video fetch ────────────────────────────────
    # Pick top-5 sounds and fetch their videos for richer hashtag clusters
    top_sound_ids = [sid for sid, _ in sound_counter.most_common(5)]
    sound_hashtag_clusters: dict[str, Any] = {}

    if top_sound_ids:
        try:
            sound_hashtag_clusters = _run_async(
                _fetch_sound_hashtags(top_sound_ids, sound_meta, niche_kw, ms_tokens, proxy_url)
            )
            # Merge sound-specific hashtags into global counter
            for sid, cluster in sound_hashtag_clusters.items():
                for tag, cnt in cluster.get("hashtags", []):
                    hashtag_counter[tag] += cnt
        except Exception as e:
            result["errors"].append(f"Sound pass 2 failed (non-fatal): {e}")

    # ── Niche scoring for sounds ───────────────────────────────────────────
    trending_sounds = []
    for sid, count in sound_counter.most_common(10):
        meta = sound_meta.get(sid, {})
        cluster = sound_hashtag_clusters.get(sid, {})
        niche_score = cluster.get("niche_score", 0.0)
        trending_sounds.append({
            "id": sid,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "fyp_count": count,
            "niche_score": round(niche_score, 2),
        })

    # Sort by niche_score × fyp_count combined
    trending_sounds.sort(key=lambda s: s["niche_score"] * s["fyp_count"] + s["fyp_count"] * 0.1, reverse=True)

    # ── Hashtag ranking ────────────────────────────────────────────────────
    all_hashtags = hashtag_counter.most_common(30)
    niche_hashtags = _rank_niche_tags(hashtag_counter, niche_kw)

    # ── Content patterns ──────────────────────────────────────────────────
    patterns = _detect_patterns(caption_texts)

    # ── Search queries ─────────────────────────────────────────────────────
    queries = _build_queries(niche, niche_hashtags, all_hashtags)

    result.update({
        "trending_sounds": trending_sounds,
        "hashtags": all_hashtags,
        "niche_hashtags": niche_hashtags,
        "search_queries": queries,
        "content_patterns": patterns,
        "sound_hashtag_clusters": sound_hashtag_clusters,
        "video_count": len(videos),
    })
    return result


def research_instagram_trends(niche: str, limit: int = 30) -> dict[str, Any]:
    """Research Instagram trends via hashtag feed (InstagramCustomAdapter)."""
    result: dict[str, Any] = {
        "platform": "instagram",
        "niche": niche,
        "hashtags": [],
        "niche_hashtags": [],
        "search_queries": [],
        "content_patterns": [],
        "errors": [],
    }

    try:
        from trend_parser.adapters.instagram import InstagramCustomAdapter
    except ImportError as e:
        result["errors"].append(f"InstagramCustomAdapter import failed: {e}")
        return result

    username = os.environ.get("INSTAGRAM_CUSTOM_USERNAME", "")
    password = os.environ.get("INSTAGRAM_CUSTOM_PASSWORD", "")
    session_file = os.environ.get("INSTAGRAM_CUSTOM_SESSION_FILE", "")

    adapter = InstagramCustomAdapter(
        query=niche,
        username=username or None,
        password=password or None,
        session_file=session_file or None,
        max_posts_per_tag=80,
    )

    from trend_parser.adapters.types import TrendFetchSelector
    niche_kw = set(_tokenize(niche))
    hashtag_seeds = _niche_to_hashtags(niche, n=5)
    selector = TrendFetchSelector(
        mode="hashtag",
        hashtags=hashtag_seeds,
        min_views=1000,
    )

    try:
        videos = adapter.fetch(limit=limit, selector=selector)
    except Exception as e:
        result["errors"].append(f"Instagram fetch failed: {e}")
        return result

    hashtag_counter: Counter[str] = Counter()
    caption_texts: list[str] = []

    for video in videos:
        for tag in video.hashtags:
            tag = tag.lower().strip().lstrip("#")
            if tag and tag not in _TAG_NOISE and len(tag) > 1:
                hashtag_counter[tag] += 1
        if video.caption:
            caption_texts.append(video.caption.lower())

    result.update({
        "hashtags": hashtag_counter.most_common(25),
        "niche_hashtags": _rank_niche_tags(hashtag_counter, niche_kw),
        "search_queries": _build_queries(niche, _rank_niche_tags(hashtag_counter, niche_kw), hashtag_counter.most_common(25)),
        "content_patterns": _detect_patterns(caption_texts),
        "video_count": len(videos),
    })
    return result


def search_trends(topic: str, days: int = 30) -> dict[str, Any]:
    """Backward-compatible wrapper: research TikTok trends for a topic.

    Replaces the old last30days/ScrapeCreators implementation with free
    platform-native trend mining.
    """
    result = research_tiktok_trends(niche=topic, limit=60)

    # Normalise to the format the agent/CLI expects
    return {
        "hashtags": result.get("hashtags", []),
        "viral_content": [],   # Not available without ScrapeCreators
        "topics": [q for q in result.get("search_queries", [])],
        "search_queries": result.get("search_queries", []),
        "raw_counts": {"tiktok": result.get("video_count", 0)},
        "errors": {f"tiktok_err_{i}": e for i, e in enumerate(result.get("errors", []))},
        # Extended fields
        "trending_sounds": result.get("trending_sounds", []),
        "niche_hashtags": result.get("niche_hashtags", []),
        "content_patterns": result.get("content_patterns", []),
        "sound_hashtag_clusters": result.get("sound_hashtag_clusters", {}),
    }


# ---------------------------------------------------------------------------
# Async: Pass 2 — sound video fetch
# ---------------------------------------------------------------------------

async def _fetch_sound_hashtags(
    sound_ids: list[str],
    sound_meta: dict[str, dict],
    niche_kw: set[str],
    ms_tokens_csv: str,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """For each sound ID, fetch ~30 of its videos and aggregate hashtags."""
    try:
        from TikTokApi import TikTokApi
        from trend_parser.adapters.tiktok import _parse_proxy_url
    except ImportError:
        return {}

    ms_tokens = [t.strip() for t in ms_tokens_csv.split(",") if t.strip()] or None
    clusters: dict[str, Any] = {}

    session_kwargs: dict = dict(
        ms_tokens=ms_tokens,
        num_sessions=1,
        sleep_after=3,
        headless=True,
    )
    if proxy_url:
        session_kwargs["proxies"] = [_parse_proxy_url(proxy_url)]

    async with TikTokApi() as api:
        await api.create_sessions(**session_kwargs)
        for sid in sound_ids:
            try:
                tag_counter: Counter[str] = Counter()
                async for video in api.sound(id=sid).videos(count=30):
                    data = video.as_dict
                    for ch in data.get("challenges") or []:
                        tag = str(ch.get("title") or "").strip().lower().lstrip("#")
                        if tag and tag not in _TAG_NOISE and len(tag) > 1:
                            tag_counter[tag] += 1
                    for item in data.get("textExtra") or []:
                        tag = str(item.get("hashtagName") or "").strip().lower().lstrip("#")
                        if tag and tag not in _TAG_NOISE and len(tag) > 1:
                            tag_counter[tag] += 1

                top_tags = tag_counter.most_common(15)
                niche_score = _niche_score(tag_counter, niche_kw)
                meta = sound_meta.get(sid, {})
                clusters[sid] = {
                    "title": meta.get("title", ""),
                    "artist": meta.get("artist", ""),
                    "hashtags": top_tags,
                    "niche_score": niche_score,
                }
            except Exception as e:
                logger.debug("Sound %s fetch failed: %s", sid, e)
                continue

    return clusters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_sound(data: dict) -> dict | None:
    """Extract music/sound metadata from a TikTok video raw_payload dict."""
    music = data.get("music") or {}
    music_id = str(music.get("id") or "").strip()
    if not music_id:
        return None
    return {
        "id": music_id,
        "title": str(music.get("title") or "").strip(),
        "artist": str(music.get("authorName") or "").strip(),
        "original": bool(music.get("original")),
    }


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens from text, length > 2."""
    return [w for w in re.sub(r"[^\w\s]", " ", text.lower()).split() if len(w) > 2]


def _niche_to_hashtags(niche: str, n: int = 5) -> list[str]:
    """Convert a niche description into candidate hashtag seeds."""
    words = _tokenize(niche)
    # Generate: individual words + joined compound
    tags = list(dict.fromkeys(words))[:n]
    compound = "".join(words[:2])
    if compound and compound not in tags:
        tags.insert(0, compound)
    return tags[:n]


def _rank_niche_tags(
    counter: Counter[str],
    niche_kw: set[str],
    top_n: int = 15,
) -> list[tuple[str, int]]:
    """Return tags sorted by how niche-relevant they are × count."""
    scored = []
    for tag, cnt in counter.items():
        tag_kw = set(_tokenize(tag))
        overlap = len(tag_kw & niche_kw) + (1 if any(kw in tag for kw in niche_kw) else 0)
        score = overlap * cnt + cnt * 0.1
        scored.append((tag, cnt, score))
    scored.sort(key=lambda x: -x[2])
    return [(tag, cnt) for tag, cnt, _ in scored[:top_n]]


def _niche_score(counter: Counter[str], niche_kw: set[str]) -> float:
    """0-1 score: fraction of top-20 hashtags that overlap with niche keywords."""
    if not counter or not niche_kw:
        return 0.0
    top = [tag for tag, _ in counter.most_common(20)]
    hits = sum(
        1 for tag in top
        if any(kw in tag or tag in kw for kw in niche_kw)
    )
    return hits / max(len(top), 1)


def _detect_patterns(captions: list[str]) -> list[tuple[str, int]]:
    """Detect content format patterns from caption texts."""
    pattern_counts: Counter[str] = Counter()
    for text in captions:
        for name, regex in _PATTERNS.items():
            if re.search(regex, text):
                pattern_counts[name] += 1
    return pattern_counts.most_common()


def _build_queries(
    niche: str,
    niche_tags: list[tuple[str, int]],
    all_tags: list[tuple[str, int]],
    max_queries: int = 10,
) -> list[str]:
    """Build ready-to-use search queries from niche + top hashtags."""
    niche_lower = niche.lower()
    seen: set[str] = set()
    queries: list[str] = []

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    add(niche)

    # Top niche-relevant tags: add as standalone only if not already in niche string
    for tag, _ in niche_tags[:5]:
        if tag not in niche_lower:
            add(tag)
        # Combined query: only if tag adds new info to the niche
        if tag not in niche_lower:
            combined = f"{niche} {tag}"
            if len(combined) <= 60:
                add(combined)

    # Top overall tags not already added (skip generic noise and niche-overlap)
    for tag, _ in all_tags[:10]:
        if len(queries) >= max_queries:
            break
        if tag not in niche_lower:
            add(tag)

    return queries[:max_queries]


def _run_async(coro) -> Any:
    """Run an async coroutine from sync context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    out: dict = {}
    err: dict = {}

    def _target():
        try:
            out["value"] = asyncio.run(coro)
        except BaseException as exc:
            err["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=120)
    if "error" in err:
        raise err["error"]
    return out.get("value", {})
