from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


@dataclass
class ParserConfig:
    """Parser configuration loaded from YAML with ${ENV_VAR} interpolation."""

    default_sources: dict = field(default_factory=lambda: {"tiktok": "scrapecreators", "instagram": "apify"})

    # ScrapeCreators
    scrapecreators_api_key: str = ""

    # Apify
    apify_token: str = ""
    tiktok_apify_actor: str = ""
    instagram_apify_actor: str = ""
    apify_overfetch_multiplier: int = 1
    apify_cost_optimized: bool = True
    apify_max_selector_terms: int = 1
    apify_fallback_to_seed: bool = False
    apify_request_retries: int = 4
    apify_retry_backoff_sec: float = 1.5
    apify_retry_max_backoff_sec: float = 12.0

    # TikTok
    tiktok_query: str = "viral videos"
    tiktok_ms_tokens: str = ""
    tiktok_custom_headless: bool = True
    tiktok_custom_sessions: int = 1
    tiktok_custom_sleep_after: int = 3
    tiktok_custom_browser: str = "chromium"

    # Instagram
    instagram_query: str = "reels trends"
    instagram_custom_username: str = ""
    instagram_custom_password: str = ""
    instagram_custom_session_file: str = ""
    instagram_custom_max_posts_per_tag: int = 120

    # yt-dlp
    yt_dlp_command: str = "yt-dlp"
    yt_dlp_format: str = "bv*+ba/b"
    yt_dlp_merge_format: str = "mp4"
    yt_dlp_cookies_file: str = ""
    download_timeout_sec: int = 900

    # Gemini VLM
    gemini_api_key: str = ""
    gemini_model: str = GEMINI_DEFAULT_MODEL
    gemini_image_model: str = "gemini-3.1-flash-image-preview"

    # Directories — resolved relative to workspace_data_dir
    workspace_data_dir: str = ""
    seed_data_dir: str = ""

    @classmethod
    def from_yaml(cls, path: str | Path) -> ParserConfig:
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        kwargs: dict = {}
        for key in cls.__dataclass_fields__:
            if key in data:
                kwargs[key] = _resolve_env(data[key])
        return cls(**kwargs)

    def resolve_workspace_dir(self, fallback: Path) -> Path:
        if self.workspace_data_dir:
            return Path(self.workspace_data_dir).expanduser().resolve()
        return fallback.resolve()

    def resolve_seed_dir(self, fallback: Path) -> Path:
        if self.seed_data_dir:
            return Path(self.seed_data_dir).expanduser().resolve()
        return fallback.resolve()


def _resolve_env(value):
    """Resolve ${ENV_VAR} references in string values."""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if raw.startswith("${") and raw.endswith("}"):
        env_name = raw[2:-1]
        return os.environ.get(env_name, "")
    if raw.startswith("$") and not raw.startswith("${"):
        env_name = raw[1:]
        return os.environ.get(env_name, "")
    return value
