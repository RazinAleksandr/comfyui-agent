from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    """Telegram bot configuration."""

    token: str = ""
    allowed_users: list[int] = field(default_factory=list)
    default_workflow: str = "wan_animate"
    idle_timeout_minutes: int = 30
    backend_url: str = "http://localhost:8000"
    default_influencer_id: str = "altf4girl"
    default_parse_limit: int = 10

    @classmethod
    def from_yaml(cls, path: str | Path) -> BotConfig:
        """Load config from a YAML file.

        The token can be specified as ``${TELEGRAM_BOT_TOKEN}`` in the YAML
        file, in which case the value is resolved from the environment variable.
        If the token field is empty or absent, the ``TELEGRAM_BOT_TOKEN`` env
        var is used as a fallback.
        """
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        raw_token = data.get("token", "")
        token = _resolve_token(raw_token)

        return cls(
            token=token,
            allowed_users=data.get("allowed_users", []),
            default_workflow=data.get("default_workflow", cls.default_workflow),
            idle_timeout_minutes=data.get("idle_timeout_minutes", cls.idle_timeout_minutes),
            backend_url=data.get("backend_url", cls.backend_url),
            default_influencer_id=data.get("default_influencer_id", cls.default_influencer_id),
            default_parse_limit=data.get("default_parse_limit", cls.default_parse_limit),
        )


def _resolve_token(raw: str) -> str:
    """Resolve the bot token from config value or environment.

    If the raw value looks like ``${ENV_VAR}`` or ``$ENV_VAR``, the
    corresponding environment variable is used.  Otherwise if the raw value
    is empty, the ``TELEGRAM_BOT_TOKEN`` environment variable is used as a
    fallback.
    """
    if raw.startswith("${") and raw.endswith("}"):
        env_name = raw[2:-1]
        return os.environ.get(env_name, "")
    if raw.startswith("$") and not raw.startswith("${"):
        env_name = raw[1:]
        return os.environ.get(env_name, "")
    if raw:
        return raw
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")
