from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telegram_bot.backend_client import BackendClient
from telegram_bot.config import BotConfig
from telegram_bot.parse_session import ParseSession, QueuedGeneration
from comfy_pipeline.config import WorkflowConfig
from isp_pipeline.processor import postprocess_outputs

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"


def _set_args_list_to_dict(args: list[str]) -> dict[str, str]:
    """Convert ['key=value', ...] to {'key': 'value', ...}."""
    result: dict[str, str] = {}
    for item in args:
        eq = item.find("=")
        if eq > 0:
            result[item[:eq]] = item[eq + 1:]
    return result


def _character_set_args(workflow: str, character_id: str) -> list[str]:
    """Load character LoRA --set args from the workflow config."""
    cfg_path = CONFIGS_DIR / f"{workflow}.yaml"
    if not cfg_path.exists():
        return []
    try:
        wf_cfg = WorkflowConfig.from_yaml(cfg_path)
        return wf_cfg.character_set_args(character_id)
    except Exception:
        logger.warning("Failed to load character config for %s/%s", workflow, character_id)
        return []

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

(
    WAITING_IMAGE, WAITING_VIDEO, WAITING_PROMPT, WAITING_FEEDBACK,
    PARSE_CHOOSING_PLATFORMS, PARSE_WAITING_IMAGE, PARSE_REVIEWING,
    PARSE_GENERATING, RESUME_CHOOSING,
) = range(9)

# user_data keys
KEY_IMAGE = "image_path"
KEY_VIDEO = "video_path"
KEY_PROMPT = "prompt"
KEY_WORKFLOW = "workflow"
KEY_TMPDIR = "tmpdir"
KEY_PARSE_SESSION = "parse_session"
KEY_PARSE_HASHTAGS = "parse_hashtags"
KEY_PARSE_PLATFORMS = "parse_platforms"
KEY_BACKEND_CLIENT = "backend_client"

# Job name for idle-timeout scheduler
IDLE_JOB_NAME = "idle_shutdown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_allowed(update: Update, config: BotConfig) -> bool:
    """Check whether the user is in the allowed list."""
    if not config.allowed_users:
        return True
    user = update.effective_user
    if user is None:
        return False
    return user.id in config.allowed_users



def _get_tmpdir(context: CallbackContext) -> Path:
    """Return a per-user temporary directory, creating it if needed."""
    if KEY_TMPDIR not in context.user_data:
        tmpdir = tempfile.mkdtemp(prefix="comfy-bot-")
        context.user_data[KEY_TMPDIR] = tmpdir
    return Path(context.user_data[KEY_TMPDIR])


def _reset_idle_timer(context: CallbackContext, config: BotConfig) -> None:
    """Reset the idle-shutdown timer.

    Removes any previously scheduled shutdown job and schedules a new one
    ``idle_timeout_minutes`` from now.
    """
    job_queue = context.application.job_queue
    if job_queue is None:
        return

    # Remove existing idle jobs
    current_jobs = job_queue.get_jobs_by_name(IDLE_JOB_NAME)
    for job in current_jobs:
        job.schedule_removal()

    timeout_seconds = config.idle_timeout_minutes * 60
    job_queue.run_once(
        _idle_shutdown_callback,
        when=timeout_seconds,
        name=IDLE_JOB_NAME,
        data=config,
    )
    logger.info("Idle timer reset: %d minutes", config.idle_timeout_minutes)


async def _idle_shutdown_callback(context: CallbackContext) -> None:
    """Called when the idle timeout expires. Shuts down the GPU server."""
    logger.info("Idle timeout reached — shutting down server")
    bot_config: BotConfig = context.job.data
    result = await _shutdown_server(bot_config.backend_url)
    logger.info("Idle shutdown result: %s", result)


# ---------------------------------------------------------------------------
# Backend API wrappers (replaces subprocess calls to vast-agent)
# ---------------------------------------------------------------------------

_JOB_POLL_INTERVAL = 5.0
_JOB_POLL_TIMEOUT = 900.0


async def _ensure_server_up(backend_url: str, workflow: str) -> str:
    """Make sure the GPU server is running via backend API."""
    client = BackendClient(backend_url)

    # Check current status
    status = await client.server_status()
    if status.get("status") == "running":
        logger.info("Server already running")
        return "Server already running."

    # Not running — bring it up via async job
    logger.info("Server not running, starting with workflow %s", workflow)
    job_id = await client.server_up(workflow)
    result = await client.poll_job(job_id, timeout=_JOB_POLL_TIMEOUT)

    if result.get("status") == "failed":
        raise RuntimeError(f"Server startup failed: {result.get('error', 'unknown')}")
    return "Server started."


async def _run_generation_via_api(
    backend_url: str,
    influencer_id: str,
    workflow: str,
    image_path: str,
    video_path: str,
    prompt: str,
    set_args: dict[str, str] | None = None,
    output_dir: str | None = None,
) -> list[str]:
    """Run a generation via backend API and return list of output paths."""
    client = BackendClient(backend_url)
    body: dict = {
        "influencer_id": influencer_id,
        "workflow": workflow,
        "prompt": prompt,
    }
    if image_path:
        body["reference_image"] = image_path
    if video_path:
        body["reference_video"] = video_path
    if set_args:
        body["set_args"] = set_args
    if output_dir:
        body["output_dir"] = output_dir

    job_id = await client.start_generation(**body)
    result = await client.poll_job(job_id, timeout=_JOB_POLL_TIMEOUT)

    if result.get("status") == "failed":
        raise RuntimeError(f"Generation failed: {result.get('error', 'unknown')}")

    gen_result = result.get("result", {})
    return gen_result.get("outputs", [])


async def _shutdown_server(backend_url: str) -> str:
    """Destroy the GPU server via backend API."""
    client = BackendClient(backend_url)
    try:
        await client.server_down()
        return "Server destroyed."
    except Exception as exc:
        msg = str(exc)
        logger.error("Server shutdown failed: %s", msg)
        return f"Shutdown failed: {msg}"


async def _get_server_cost(backend_url: str) -> str:
    """Get session cost from backend API."""
    client = BackendClient(backend_url)
    try:
        status = await client.server_status()
        dph = status.get("dph_total")
        if dph:
            return f"${dph}/hr"
        return status.get("actual_status") or "unknown"
    except Exception:
        return "unknown"


async def _get_dph_rate(backend_url: str) -> float | None:
    """Get $/hr rate from backend API."""
    client = BackendClient(backend_url)
    try:
        status = await client.server_status()
        return status.get("dph_total")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Handler functions
# ---------------------------------------------------------------------------

def build_conversation_handler(config: BotConfig) -> ConversationHandler:
    """Build and return the ConversationHandler for the bot."""

    async def start(update: Update, context: CallbackContext) -> int:
        """Handle /start command. Transition to WAITING_IMAGE."""
        if not _is_allowed(update, config):
            await update.message.reply_text("You are not authorized to use this bot.")
            return ConversationHandler.END

        # Initialize user session data
        context.user_data[KEY_WORKFLOW] = config.default_workflow

        _reset_idle_timer(context, config)
        await update.message.reply_text("Ready. Send me a reference image.")
        return WAITING_IMAGE

    async def receive_image(update: Update, context: CallbackContext) -> int:
        """Handle incoming photo. Save it and transition to WAITING_VIDEO."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        if not update.message.photo:
            await update.message.reply_text("Please send a photo (image).")
            return WAITING_IMAGE

        # Download highest-resolution version
        photo = update.message.photo[-1]
        file = await photo.get_file()
        tmpdir = _get_tmpdir(context)
        image_path = tmpdir / f"reference_image_{photo.file_unique_id}.jpg"
        await file.download_to_drive(str(image_path))

        context.user_data[KEY_IMAGE] = str(image_path)
        logger.info("Saved reference image: %s", image_path)
        await update.message.reply_text("Got it. Now send a reference video.")
        return WAITING_VIDEO

    async def receive_video(update: Update, context: CallbackContext) -> int:
        """Handle incoming video. Save it and transition to WAITING_PROMPT."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        # Accept video or document (users sometimes send videos as documents)
        if update.message.video:
            file = await update.message.video.get_file()
            file_id = update.message.video.file_unique_id
        elif update.message.document:
            file = await update.message.document.get_file()
            file_id = update.message.document.file_unique_id
        else:
            await update.message.reply_text("Please send a video file.")
            return WAITING_VIDEO

        tmpdir = _get_tmpdir(context)
        video_path = tmpdir / f"reference_video_{file_id}.mp4"
        await file.download_to_drive(str(video_path))

        context.user_data[KEY_VIDEO] = str(video_path)
        logger.info("Saved reference video: %s", video_path)
        await update.message.reply_text("What prompt?")
        return WAITING_PROMPT

    async def receive_prompt(update: Update, context: CallbackContext) -> int:
        """Handle text prompt. Start generation and transition to WAITING_FEEDBACK."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        prompt = update.message.text.strip()
        if not prompt:
            await update.message.reply_text("Please send a text prompt.")
            return WAITING_PROMPT

        context.user_data[KEY_PROMPT] = prompt
        return await _do_generation(update, context)

    async def handle_feedback(update: Update, context: CallbackContext) -> int:
        """Handle user feedback after seeing a result.

        The user can:
        - Send text to adjust the prompt and re-run
        - Send a new photo to replace the reference image
        - Send a new video to replace the reference video
        - Use /stop to shut down
        """
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        # New photo replaces reference image
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await photo.get_file()
            tmpdir = _get_tmpdir(context)
            image_path = tmpdir / f"reference_image_{photo.file_unique_id}.jpg"
            await file.download_to_drive(str(image_path))
            context.user_data[KEY_IMAGE] = str(image_path)
            await update.message.reply_text("Updated reference image. Re-running with same prompt...")
            return await _do_generation(update, context)

        # New video replaces reference video
        if update.message.video or update.message.document:
            if update.message.video:
                file = await update.message.video.get_file()
                file_id = update.message.video.file_unique_id
            else:
                file = await update.message.document.get_file()
                file_id = update.message.document.file_unique_id
            tmpdir = _get_tmpdir(context)
            video_path = tmpdir / f"reference_video_{file_id}.mp4"
            await file.download_to_drive(str(video_path))
            context.user_data[KEY_VIDEO] = str(video_path)
            await update.message.reply_text("Updated reference video. Re-running with same prompt...")
            return await _do_generation(update, context)

        # Text feedback becomes the new prompt
        new_prompt = update.message.text.strip() if update.message.text else ""
        if not new_prompt:
            await update.message.reply_text(
                "Send text to adjust the prompt, a new photo/video to replace inputs, "
                "or /stop to shut down."
            )
            return WAITING_FEEDBACK

        context.user_data[KEY_PROMPT] = new_prompt
        return await _do_generation(update, context)

    async def stop(update: Update, context: CallbackContext) -> int:
        """Handle /stop — shut down the server and end the conversation."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        status_msg = await update.message.reply_text("Shutting down server...")

        # Cancel idle timer
        job_queue = context.application.job_queue
        if job_queue is not None:
            for job in job_queue.get_jobs_by_name(IDLE_JOB_NAME):
                job.schedule_removal()

        cost_info = await _get_server_cost(config.backend_url)
        result = await _shutdown_server(config.backend_url)
        await status_msg.edit_text(f"{result}\nSession cost: {cost_info}")

        # Clean up user data
        context.user_data.clear()
        return ConversationHandler.END

    async def cancel(update: Update, context: CallbackContext) -> int:
        """Handle /cancel — end conversation without shutting down the server."""
        await update.message.reply_text(
            "Conversation cancelled. The server is still running.\n"
            "Use /start to begin a new session, or /stop to shut down the server."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # -----------------------------------------------------------------------
    # Generation runner (shared by receive_prompt and handle_feedback)
    # -----------------------------------------------------------------------

    async def _do_generation(update: Update, context: CallbackContext) -> int:
        """Ensure server is up, run the generation, send results."""
        workflow = context.user_data.get(KEY_WORKFLOW, config.default_workflow)
        image_path = context.user_data.get(KEY_IMAGE, "")
        video_path = context.user_data.get(KEY_VIDEO, "")
        prompt = context.user_data.get(KEY_PROMPT, "")
        if not image_path:
            await update.message.reply_text("Missing reference image. Please send one.")
            return WAITING_IMAGE
        if not video_path:
            await update.message.reply_text("Missing reference video. Please send one.")
            return WAITING_VIDEO
        if not prompt:
            await update.message.reply_text("Missing prompt. Please send one.")
            return WAITING_PROMPT

        progress_msg = await update.message.reply_text("Starting GPU server...")

        try:
            server_status = await _ensure_server_up(config.backend_url, workflow)
            await progress_msg.edit_text(f"{server_status}\nRunning generation...")
        except RuntimeError as e:
            msg = str(e)[:4000]
            await progress_msg.edit_text(f"Failed to start server: {msg}")
            return WAITING_FEEDBACK

        try:
            char_args = _character_set_args(workflow, config.default_influencer_id)
            set_args = _set_args_list_to_dict(char_args)
            outputs = await _run_generation_via_api(
                backend_url=config.backend_url,
                influencer_id=config.default_influencer_id,
                workflow=workflow,
                image_path=image_path,
                video_path=video_path,
                prompt=prompt,
                set_args=set_args or None,
                output_dir=str(PROJECT_ROOT / "output"),
            )
        except RuntimeError as e:
            msg = str(e)[:4000]
            await progress_msg.edit_text(f"Generation failed: {msg}")
            return WAITING_FEEDBACK

        # ISP post-processing on the best output
        if outputs:
            try:
                pp_path = postprocess_outputs(outputs)
                if pp_path:
                    outputs.append(pp_path)
                    logger.info("Postprocessed: %s", pp_path)
            except Exception:
                logger.warning("Postprocessing failed", exc_info=True)

        # Send results back
        if outputs:
            await progress_msg.edit_text("Done! Sending results...")
            for output_path in outputs:
                path = Path(output_path)
                if path.exists() and path.suffix.lower() in (".mp4", ".webm", ".mov"):
                    await update.message.reply_video(video=str(path))
                elif path.exists():
                    await update.message.reply_document(document=str(path))
                else:
                    logger.warning("Output file not found: %s", path)
            await progress_msg.edit_text(
                "Generation complete. Send feedback to adjust, or /stop to shut down."
            )
        else:
            await progress_msg.edit_text(
                "Generation finished but no output files were returned.\n"
                "Send feedback to try again, or /stop to shut down."
            )

        return WAITING_FEEDBACK

    # -----------------------------------------------------------------------
    # /parse flow handlers
    # -----------------------------------------------------------------------

    async def parse_command(update: Update, context: CallbackContext) -> int:
        """Handle /parse [hashtags]. Ask which platforms to scrape."""
        if not _is_allowed(update, config):
            await update.message.reply_text("You are not authorized to use this bot.")
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        # Parse optional hashtags from command arguments
        hashtags: list[str] = []
        if context.args:
            hashtags = [
                tag.lstrip("#") for tag in context.args if tag.strip()
            ]

        context.user_data[KEY_PARSE_HASHTAGS] = hashtags
        context.user_data[KEY_WORKFLOW] = config.default_workflow

        keyboard = [
            [InlineKeyboardButton("TikTok", callback_data="parse_plat:tiktok")],
            [InlineKeyboardButton("Instagram", callback_data="parse_plat:instagram")],
            [InlineKeyboardButton("Both", callback_data="parse_plat:tiktok,instagram")],
        ]
        await update.message.reply_text(
            "Which platforms to parse?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return PARSE_CHOOSING_PLATFORMS

    async def parse_platform_chosen(update: Update, context: CallbackContext) -> int:
        """User picked platforms via inline button. Run the pipeline."""
        query = update.callback_query
        await query.answer()

        platforms = query.data.removeprefix("parse_plat:").split(",")
        context.user_data[KEY_PARSE_PLATFORMS] = platforms
        hashtags: list[str] = context.user_data.get(KEY_PARSE_HASHTAGS, [])

        plat_label = " + ".join(p.capitalize() for p in platforms)
        status_msg = await query.edit_message_text(
            f"Parsing {plat_label} trending videos... This may take a minute."
        )

        client = BackendClient(config.backend_url)
        context.user_data[KEY_BACKEND_CLIENT] = client

        try:
            result = await client.run_pipeline(
                influencer_id=config.default_influencer_id,
                platforms=platforms,
                hashtags=hashtags or None,
                limit=config.default_parse_limit,
            )
        except Exception as exc:
            await status_msg.edit_text(f"Pipeline failed: {exc}")
            return ConversationHandler.END

        # Extract selected videos from all platforms
        platforms_out = result.get("platforms", [])
        if not platforms_out:
            await status_msg.edit_text("Pipeline returned no platform results.")
            return ConversationHandler.END

        # Collect .mp4 files from every platform's selected_dir
        video_files: list[Path] = []
        first_selected_dir = ""
        _views_re = re.compile(r"views(\d+)")
        for plat in platforms_out:
            sel_dir = plat.get("selected_dir", "")
            if not sel_dir:
                continue
            if not first_selected_dir:
                first_selected_dir = sel_dir
            d = Path(sel_dir)
            if d.exists():
                video_files.extend(sorted(d.glob("*.mp4")))

        if not video_files:
            await status_msg.edit_text(
                "VLM stage selected 0 videos across all platforms."
            )
            return ConversationHandler.END

        items = []
        for f in video_files:
            m = _views_re.search(f.stem)
            views = f"{int(m.group(1)):,}" if m else "?"
            platform_tag = f.parent.parent.name  # e.g. "tiktok" or "instagram"
            items.append({
                "_local_path": str(f),
                "caption": f.stem,
                "views": views,
                "platform": platform_tag,
            })

        run_id = platforms_out[0].get("trend_run_id", 0)
        session = ParseSession(
            run_id=run_id,
            items=items,
            influencer_id=config.default_influencer_id,
            selected_dir=first_selected_dir,
            workflow=config.default_workflow,
        )
        context.user_data[KEY_PARSE_SESSION] = session

        # Per-platform breakdown
        from collections import Counter
        plat_counts = Counter(it.get("platform", "unknown") for it in items)
        breakdown = ", ".join(f"{cnt} from {p}" for p, cnt in plat_counts.items())

        await status_msg.edit_text(
            f"Found {len(items)} filtered videos ({breakdown}).\n"
            "Send a reference photo to use for all generations."
        )
        return PARSE_WAITING_IMAGE

    async def _show_current_item(
        update: Update, context: CallbackContext,
    ) -> int:
        """Send the current trend item video and stats to the user."""
        session: ParseSession | None = context.user_data.get(KEY_PARSE_SESSION)
        if session is None:
            await update.effective_chat.send_message(
                "Session expired. Use /parse to start again."
            )
            return ConversationHandler.END

        item = session.current_item

        if item is None:
            return await _finish_review(update, context)

        idx = session.current_index + 1
        total = len(session.items)
        caption = item.get("caption", "")
        views = item.get("views", "?")
        local_path = item.get("_local_path", "")

        platform = item.get("platform", "")
        platform_label = f" [{platform}]" if platform else ""

        text = (
            f"[{idx}/{total}]{platform_label} {caption}\n"
            f"Views: {views}\n\n"
            "Send a prompt to approve, "
            "/skip to skip, /done to finish review."
        )

        # Send the downloaded video file
        video_file = Path(local_path)
        chat = update.effective_chat
        if video_file.exists():
            try:
                await chat.send_video(video=str(video_file))
            except Exception:
                logger.warning("Could not send video %s, sending as document", video_file)
                try:
                    await chat.send_document(document=str(video_file))
                except Exception:
                    logger.error("Failed to send file %s", video_file)
        else:
            logger.warning("Video file not found: %s", local_path)

        await chat.send_message(text)
        return PARSE_REVIEWING

    async def parse_receive_image(
        update: Update, context: CallbackContext,
    ) -> int:
        """User sends a photo in PARSE_WAITING_IMAGE. Save as shared reference."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        if not update.message.photo:
            await update.message.reply_text("Please send a photo.")
            return PARSE_WAITING_IMAGE

        photo = update.message.photo[-1]
        file = await photo.get_file()
        tmpdir = _get_tmpdir(context)
        image_path = tmpdir / f"parse_ref_{photo.file_unique_id}.jpg"
        await file.download_to_drive(str(image_path))

        context.user_data[KEY_IMAGE] = str(image_path)
        logger.info("Saved shared reference image: %s", image_path)

        # Initialize session directory for persistence
        session: ParseSession | None = context.user_data.get(KEY_PARSE_SESSION)
        if session is not None:
            session.init_session_dir(
                PROJECT_ROOT / "output", str(image_path),
            )
            # Use the persistent copy instead of /tmp
            persistent_img = session.session_dir / "reference_image.jpg"
            if persistent_img.exists():
                context.user_data[KEY_IMAGE] = str(persistent_img)

        await update.message.reply_text("Got it. Showing first video...")
        return await _show_current_item(update, context)

    async def parse_approve(
        update: Update, context: CallbackContext,
    ) -> int:
        """User sends text in PARSE_REVIEWING. Text is the prompt — queue and advance."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        prompt = update.message.text.strip() if update.message.text else ""
        if not prompt:
            await update.message.reply_text("Please send a text prompt to approve.")
            return PARSE_REVIEWING

        session: ParseSession | None = context.user_data.get(KEY_PARSE_SESSION)
        if session is None:
            await update.message.reply_text("Session expired. Use /parse to start again.")
            return ConversationHandler.END

        item = session.current_item
        if item is None:
            return await _finish_review(update, context)

        queued = QueuedGeneration(
            trend_item_id=item.get("id", 0),
            caption=item.get("caption", ""),
            video_path=item.get("_local_path", ""),
            image_path=context.user_data.get(KEY_IMAGE, ""),
            prompt=prompt,
            platform=item.get("platform", ""),
        )
        session.queue.append(queued)
        session.save()

        n = len(session.queue)
        session.advance()
        remaining = len(session.items) - session.current_index

        if session.current_item is None:
            await update.message.reply_text(
                f"Queued! ({n} in queue). No more videos to review."
            )
            return await _finish_review(update, context)

        await update.message.reply_text(
            f"Queued! ({n} in queue, {remaining} left). Next video:"
        )
        return await _show_current_item(update, context)

    async def parse_skip(update: Update, context: CallbackContext) -> int:
        """Handle /skip in PARSE_REVIEWING. Advance to next item."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        session: ParseSession | None = context.user_data.get(KEY_PARSE_SESSION)
        if session is None:
            await update.message.reply_text("Session expired. Use /parse to start again.")
            return ConversationHandler.END

        session.advance()

        if session.current_item is None:
            await update.message.reply_text("No more videos to review.")
            return await _finish_review(update, context)

        return await _show_current_item(update, context)

    async def parse_done(update: Update, context: CallbackContext) -> int:
        """Handle /done in PARSE_REVIEWING. Finish review, start batch."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        _reset_idle_timer(context, config)
        return await _finish_review(update, context)

    async def _finish_review(
        update: Update, context: CallbackContext,
    ) -> int:
        """End the review phase and start batch generation if queue is non-empty."""
        session: ParseSession | None = context.user_data.get(KEY_PARSE_SESSION)
        chat = update.effective_chat

        if session is None or not session.queue:
            await chat.send_message(
                "No videos were approved. Use /start for manual mode "
                "or /parse to try again."
            )
            return ConversationHandler.END

        return await _run_batch_generation(update, context)

    async def _run_batch_generation(
        update: Update, context: CallbackContext,
    ) -> int:
        """Rent GPU once, then generate all queued videos sequentially."""
        session: ParseSession = context.user_data[KEY_PARSE_SESSION]
        queue = session.queue
        total = len(queue)
        workflow = context.user_data.get(KEY_WORKFLOW, config.default_workflow)
        chat = update.effective_chat

        # Count only items that still need generating
        actionable = [q for q in queue if q.status != "completed"]
        if not actionable:
            await chat.send_message("All items already completed. Nothing to generate.")
            return WAITING_FEEDBACK

        progress_msg = await chat.send_message(
            f"Starting batch generation ({len(actionable)}/{total} videos). Renting GPU..."
        )

        # Bring up the server once for the entire batch
        try:
            server_status = await _ensure_server_up(config.backend_url, workflow)
            await progress_msg.edit_text(
                f"{server_status}\nGenerating {len(actionable)} videos..."
            )
        except RuntimeError as e:
            await progress_msg.edit_text(
                f"Failed to start server: {str(e)[:4000]}\n"
                "Use /parse to try again or /resume to retry later."
            )
            return ConversationHandler.END

        successes = 0

        # Get vast.ai hourly rate for cost tracking
        dph_rate = await _get_dph_rate(config.backend_url)
        if dph_rate:
            logger.info("Vast.ai rate: $%.3f/hr", dph_rate)

        for i, item in enumerate(queue, start=1):
            if item.status in ("completed",):
                continue

            snippet = (item.caption[:40] + "...") if len(item.caption) > 40 else item.caption
            try:
                await progress_msg.edit_text(
                    f"Generating {i}/{total}: {snippet}"
                )
            except Exception:
                pass

            # Per-item output directory inside session_dir
            if session.session_dir is not None:
                caption_slug = re.sub(r"[^a-zA-Z0-9_]", "_", item.caption)[:60]
                platform_folder = item.platform or "unknown"
                item_output_dir = str(
                    session.session_dir / "results" / platform_folder / f"{i:03d}_{caption_slug}"
                )
                Path(item_output_dir).mkdir(parents=True, exist_ok=True)
            else:
                item_output_dir = str(PROJECT_ROOT / "output")

            item.status = "generating"
            item.dph_rate = dph_rate
            item.generation_start = time.time()
            session.save()

            char_args = _character_set_args(workflow, session.influencer_id)
            set_args = _set_args_list_to_dict(char_args)
            try:
                outputs = await _run_generation_via_api(
                    backend_url=config.backend_url,
                    influencer_id=session.influencer_id,
                    workflow=workflow,
                    reference_image=item.image_path,
                    reference_video=item.video_path,
                    prompt=item.prompt,
                    output_dir=item_output_dir,
                    set_args=set_args,
                )
            except RuntimeError as e:
                item.generation_end = time.time()
                if dph_rate and item.generation_start:
                    elapsed_hrs = (item.generation_end - item.generation_start) / 3600
                    item.cost_usd = round(dph_rate * elapsed_hrs, 4)
                item.status = "failed"
                session.save()
                await chat.send_message(
                    f"Generation {i}/{total} failed: {str(e)[:2000]}"
                )
                continue

            item.generation_end = time.time()
            if dph_rate and item.generation_start:
                elapsed_hrs = (item.generation_end - item.generation_start) / 3600
                item.cost_usd = round(dph_rate * elapsed_hrs, 4)
            item.status = "completed"
            item.output_paths = outputs

            # ISP post-processing on the best output
            try:
                pp_path = postprocess_outputs(outputs)
                if pp_path:
                    item.output_paths.append(pp_path)
                    logger.info("Postprocessed: %s", pp_path)
            except Exception:
                logger.warning("Postprocessing failed for %s", snippet, exc_info=True)

            session.save()

            # Send only postprocessed + upscaled videos
            sent = False
            cost_str = ""
            if item.cost_usd is not None:
                elapsed_min = (item.generation_end - item.generation_start) / 60 if item.generation_start and item.generation_end else 0
                cost_str = f" | {elapsed_min:.0f}min ${item.cost_usd:.4f}"
            send_paths = [
                p for p in item.output_paths
                if Path(p).exists() and (
                    "postprocessed" in p or Path(p).parent.name == "upscaled"
                )
            ]
            if not send_paths:
                # Fallback: send any video if no postprocessed/upscaled found
                send_paths = [
                    p for p in item.output_paths
                    if Path(p).exists() and Path(p).suffix.lower() in (".mp4", ".webm", ".mov")
                ]
            for output_path in send_paths:
                path = Path(output_path)
                label = "postprocessed" if "postprocessed" in str(path) else path.parent.name
                if path.suffix.lower() in (".mp4", ".webm", ".mov"):
                    await chat.send_video(
                        video=str(path),
                        caption=f"[{i}/{total}] {label} | {snippet}{cost_str}",
                    )
                    sent = True
            if sent:
                successes += 1

        completed_total = sum(1 for q in queue if q.status == "completed")
        total_cost = sum(q.cost_usd for q in queue if q.cost_usd)
        cost_line = f"\nTotal cost: ${total_cost:.4f}" if total_cost else ""
        await progress_msg.edit_text(
            f"Batch complete! {completed_total}/{total} videos generated.{cost_line}\n"
            "Send feedback to adjust, or /stop to shut down."
        )
        return WAITING_FEEDBACK

    # -----------------------------------------------------------------------
    # /resume flow
    # -----------------------------------------------------------------------

    async def resume_command(update: Update, context: CallbackContext) -> int:
        """Handle /resume — find incomplete sessions and resume generation."""
        if not _is_allowed(update, config):
            await update.message.reply_text("You are not authorized to use this bot.")
            return ConversationHandler.END

        _reset_idle_timer(context, config)

        base_output = PROJECT_ROOT / "output"
        incomplete = ParseSession.find_incomplete_sessions(base_output)

        if not incomplete:
            await update.message.reply_text("Nothing to resume. All sessions are complete.")
            return ConversationHandler.END

        if len(incomplete) == 1:
            session = ParseSession.load(incomplete[0])
            context.user_data[KEY_PARSE_SESSION] = session
            context.user_data[KEY_WORKFLOW] = session.workflow
            # Restore reference image from session dir and fix stale paths in queue
            ref_img = incomplete[0] / "reference_image.jpg"
            if ref_img.exists():
                context.user_data[KEY_IMAGE] = str(ref_img)
                for q in session.queue:
                    if not Path(q.image_path).exists():
                        q.image_path = str(ref_img)
                session.save()

            remaining = session.pending_or_failed_count()
            total = len(session.queue)
            await update.message.reply_text(
                f"Resuming session ({remaining}/{total} remaining)..."
            )
            return await _run_batch_generation(update, context)

        # Multiple incomplete sessions — let the user choose
        lines = ["Incomplete sessions:"]
        for idx, sd in enumerate(incomplete, start=1):
            try:
                s = ParseSession.load(sd)
                remaining = s.pending_or_failed_count()
                total = len(s.queue)
                lines.append(f"{idx}. {sd.name} — {remaining}/{total} pending")
            except (json.JSONDecodeError, OSError):
                lines.append(f"{idx}. {sd.name} — (corrupt)")
        lines.append("\nReply with number to resume.")

        context.user_data["_resume_choices"] = incomplete
        await update.message.reply_text("\n".join(lines))
        return RESUME_CHOOSING

    async def resume_choose(update: Update, context: CallbackContext) -> int:
        """Handle the user's numeric choice in RESUME_CHOOSING."""
        if not _is_allowed(update, config):
            return ConversationHandler.END

        text = (update.message.text or "").strip()
        choices: list[Path] = context.user_data.get("_resume_choices", [])

        try:
            idx = int(text) - 1
            if not (0 <= idx < len(choices)):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                f"Please reply with a number between 1 and {len(choices)}."
            )
            return RESUME_CHOOSING

        session_dir = choices[idx]
        session = ParseSession.load(session_dir)
        context.user_data[KEY_PARSE_SESSION] = session
        context.user_data[KEY_WORKFLOW] = session.workflow
        ref_img = session_dir / "reference_image.jpg"
        if ref_img.exists():
            context.user_data[KEY_IMAGE] = str(ref_img)
            for q in session.queue:
                if not Path(q.image_path).exists():
                    q.image_path = str(ref_img)
            session.save()

        context.user_data.pop("_resume_choices", None)

        remaining = session.pending_or_failed_count()
        total = len(session.queue)
        await update.message.reply_text(
            f"Resuming session {session_dir.name} ({remaining}/{total} remaining)..."
        )
        return await _run_batch_generation(update, context)

    # -----------------------------------------------------------------------
    # Build the ConversationHandler
    # -----------------------------------------------------------------------

    handler = ConversationHandler(
        per_message=False,
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("parse", parse_command),
            CommandHandler("resume", resume_command),
        ],
        states={
            WAITING_IMAGE: [
                MessageHandler(filters.PHOTO, receive_image),
            ],
            WAITING_VIDEO: [
                MessageHandler(filters.VIDEO | filters.Document.ALL, receive_video),
            ],
            WAITING_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt),
            ],
            WAITING_FEEDBACK: [
                MessageHandler(
                    filters.PHOTO | filters.VIDEO | filters.Document.ALL
                    | (filters.TEXT & ~filters.COMMAND),
                    handle_feedback,
                ),
                CommandHandler("stop", stop),
            ],
            PARSE_CHOOSING_PLATFORMS: [
                CallbackQueryHandler(parse_platform_chosen, pattern=r"^parse_plat:"),
                CommandHandler("stop", stop),
            ],
            PARSE_WAITING_IMAGE: [
                MessageHandler(filters.PHOTO, parse_receive_image),
                CommandHandler("stop", stop),
            ],
            PARSE_REVIEWING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, parse_approve),
                CommandHandler("skip", parse_skip),
                CommandHandler("done", parse_done),
                CommandHandler("stop", stop),
            ],
            PARSE_GENERATING: [],
            RESUME_CHOOSING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, resume_choose),
                CommandHandler("stop", stop),
            ],
        },
        fallbacks=[
            CommandHandler("stop", stop),
            CommandHandler("cancel", cancel),
        ],
    )

    return handler
