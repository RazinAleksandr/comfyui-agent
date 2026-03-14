from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from trend_parser.adapters.types import RawTrendVideo, TrendFetchSelector

try:
    import instaloader
except Exception:  # pragma: no cover - optional dependency
    instaloader = None


class InstagramCustomAdapter:
    def __init__(
        self,
        query: str,
        username: str | None = None,
        password: str | None = None,
        session_file: str | None = None,
        max_posts_per_tag: int = 120,
    ):
        self.query = query
        self.username = (username or "").strip()
        self.password = password or ""
        self.session_file = (session_file or "").strip()
        self.max_posts_per_tag = max(10, int(max_posts_per_tag))

    def fetch(self, limit: int, selector: TrendFetchSelector | None = None) -> list[RawTrendVideo]:
        if instaloader is None:
            raise RuntimeError(
                "instaloader is not installed. Install with: pip install -e '.[vps]'"
            )

        loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )
        self._authenticate(loader)

        hashtags = self._selector_hashtags(selector)
        if not hashtags:
            hashtags = self._fallback_hashtags(selector)
        if not hashtags:
            hashtags = ["healthylifestyle"]

        target = max(1, int(limit))
        per_tag_limit = max(1, int(math.ceil(target / len(hashtags))))
        recent_cutoff = self._recent_cutoff(selector)
        unique: dict[str, RawTrendVideo] = {}
        errors: list[str] = []

        for tag in hashtags:
            try:
                hashtag = instaloader.Hashtag.from_name(loader.context, tag)
            except Exception:
                errors.append(f"{tag}: unable to resolve hashtag (auth may be required)")
                continue

            scanned = 0
            try:
                for post in hashtag.get_posts():
                    scanned += 1
                    if scanned > self.max_posts_per_tag:
                        break
                    row = self._to_video(post)
                    if row is None:
                        continue
                    if recent_cutoff is not None and row.published_at is not None and row.published_at < recent_cutoff:
                        break
                    if row.source_item_id and row.source_item_id in unique:
                        continue
                    if not self._passes_filters(row, selector):
                        continue
                    key = row.source_item_id or f"{tag}:{len(unique)}"
                    unique[key] = row
                    if len(unique) >= target:
                        return list(unique.values())[:target]
            except Exception as exc:
                errors.append(f"{tag}: {exc}")
                continue

        if not unique and errors:
            preview = "; ".join(errors[:3])
            raise RuntimeError(
                "instagram_custom could not fetch hashtag videos. "
                "Instagram likely requires authentication. "
                "Set INSTAGRAM_CUSTOM_USERNAME / INSTAGRAM_CUSTOM_PASSWORD "
                "or INSTAGRAM_CUSTOM_SESSION_FILE. "
                f"sample_errors={preview}"
            )
        return list(unique.values())[:target]

    def _authenticate(self, loader) -> None:
        if not self.username:
            return
        if self.session_file:
            try:
                loader.load_session_from_file(self.username, self.session_file)
                return
            except Exception:
                pass
        if not self.password:
            return
        try:
            loader.login(self.username, self.password)
            if self.session_file:
                try:
                    loader.save_session_to_file(self.session_file)
                except Exception:
                    pass
        except Exception:
            return

    def _to_video(self, post) -> RawTrendVideo | None:
        try:
            if not bool(post.is_video):
                return None
        except Exception:
            return None

        shortcode = str(getattr(post, "shortcode", "") or "").strip()
        source_id = shortcode or str(getattr(post, "mediaid", "") or "").strip()
        if not source_id:
            return None

        caption = getattr(post, "caption", None)
        caption_hashtags = list(getattr(post, "caption_hashtags", []) or [])
        hashtags = [str(tag).strip().lstrip("#") for tag in caption_hashtags if str(tag).strip()]

        published_at = getattr(post, "date_utc", None)
        if isinstance(published_at, datetime):
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            else:
                published_at = published_at.astimezone(UTC)
        else:
            published_at = None

        views = self._to_int(getattr(post, "video_view_count", None))
        likes = self._to_int(getattr(post, "likes", None))
        comments = self._to_int(getattr(post, "comments", None))

        video_url = None
        try:
            video_url = getattr(post, "video_url", None)
        except Exception:
            video_url = None
        if not video_url and shortcode:
            video_url = f"https://www.instagram.com/reel/{shortcode}/"

        raw_payload: dict[str, Any] = {
            "shortcode": shortcode,
            "typename": str(getattr(post, "typename", "") or ""),
            "owner_username": str(getattr(getattr(post, "owner_profile", None), "username", "") or ""),
            "url": str(getattr(post, "url", "") or ""),
            "is_video": True,
        }

        return RawTrendVideo(
            platform="instagram",
            source_item_id=source_id,
            video_url=video_url,
            caption=caption,
            hashtags=list(dict.fromkeys(hashtags)),
            audio=None,
            style_hint=str(getattr(post, "typename", "") or None),
            published_at=published_at,
            views=views,
            likes=likes,
            comments=comments,
            shares=0,
            raw_payload=raw_payload,
        )

    def _passes_filters(self, video: RawTrendVideo, selector: TrendFetchSelector | None) -> bool:
        if selector is None:
            return True
        if selector.min_views is not None and video.views < int(selector.min_views):
            return False
        if selector.min_likes is not None and video.likes < int(selector.min_likes):
            return False
        cutoff = self._recent_cutoff(selector)
        if cutoff is not None:
            if video.published_at is None:
                return False
            if video.published_at < cutoff:
                return False
        return True

    def _recent_cutoff(self, selector: TrendFetchSelector | None) -> datetime | None:
        if selector is None or selector.published_within_days is None:
            return None
        return datetime.now(UTC) - timedelta(days=max(1, int(selector.published_within_days)))

    def _selector_hashtags(self, selector: TrendFetchSelector | None) -> list[str]:
        if selector is None:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for raw in selector.hashtags:
            tag = self._compact_tag(raw)
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
            tag = self._compact_tag(raw)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            out.append(tag)
        return out[:3]

    def _compact_tag(self, raw: Any) -> str:
        text = str(raw or "").strip().lower()
        return "".join(ch for ch in text if ch.isalnum() or ch == "_")

    def _to_int(self, value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0
