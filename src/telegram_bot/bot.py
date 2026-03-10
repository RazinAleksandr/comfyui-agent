from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from telegram_bot.config import BotConfig
from telegram_bot.conversation import build_conversation_handler

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"
DEFAULT_CONFIG = CONFIGS_DIR / "telegram.yaml"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_path: str | None = None) -> BotConfig:
    """Load bot config from file."""
    if config_path:
        path = Path(config_path)
        if not path.exists():
            path = CONFIGS_DIR / config_path
        if not path.exists():
            click.echo(f"Config not found: {config_path}", err=True)
            sys.exit(1)
        return BotConfig.from_yaml(path)

    if DEFAULT_CONFIG.exists():
        return BotConfig.from_yaml(DEFAULT_CONFIG)

    # Use defaults (token must come from env)
    return BotConfig()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Logging level",
)
def main(config_path: str | None, log_level: str) -> None:
    """Telegram bot for ComfyUI video generation."""
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=getattr(logging, log_level.upper()),
    )
    # Silence noisy libraries — only show warnings+
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)

    config = _load_config(config_path)

    if not config.token:
        click.echo(
            "No bot token configured. Set TELEGRAM_BOT_TOKEN env var or "
            "specify 'token' in the config file.",
            err=True,
        )
        sys.exit(1)

    logger.info("Starting bot (workflow=%s, idle_timeout=%dm)",
                config.default_workflow, config.idle_timeout_minutes)
    if config.allowed_users:
        logger.info("Allowed users: %s", config.allowed_users)
    else:
        logger.warning("No user whitelist configured — bot is open to everyone")

    # Import here so the module can be loaded without python-telegram-bot
    # installed (e.g. for config validation).
    from telegram.ext import ApplicationBuilder

    app = ApplicationBuilder().token(config.token).build()

    # Register the conversation handler
    conv_handler = build_conversation_handler(config)
    app.add_handler(conv_handler)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)
