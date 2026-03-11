from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from telegram import Update
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telegram_bot.config import BotConfig
from telegram_bot.parse_session import ParseSession, QueuedGeneration
from telegram_bot.studio_client import StudioClient

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

(
    WAITING_IMAGE, WAITING_VIDEO, WAITING_PROMPT, WAITING_FEEDBACK,
    PARSE_WAITING_IMAGE, PARSE_REVIEWING, PARSE_GENERATING,
    RESUME_CHOOSING,
) = range(8)

# user_data keys
KEY_IMAGE = "image_path"
KEY_VIDEO = "video_path"
KEY_PROMPT = "prompt"
KEY_WORKFLOW = "workflow"
KEY_TMPDIR = "tmpdir"
KEY_PARSE_SESSION = "parse_session"
KEY_STUDIO_CLIENT = "studio_client"

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


async def _run_subprocess(
    *args: str,
    stream: bool = False,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[int, str, str]:
    """Run a command via asyncio subprocess and return (returncode, stdout, stderr).

    When *stream* is True, stdout/stderr lines are logged in real time so the
    operator can follow progress (e.g. vast-agent rent attempts, SSH polling).
    If *progress_callback* is provided (stream mode only), it is called with
    each stderr line so the caller can relay progress to the user.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if not stream:
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode() if stdout_bytes else ""
        stderr = stderr_bytes.decode() if stderr_bytes else ""
        return proc.returncode, stdout, stderr

    # Stream mode: read lines as they arrive and log them
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    async def _read_stream(s, buf: list[str], level: int, cb=None) -> None:
        async for raw in s:
            line = raw.decode().rstrip()
            if line:
                buf.append(line)
                logger.log(level, "[vast-agent] %s", line)
                if cb is not None:
                    await cb(line)

    await asyncio.gather(
        _read_stream(proc.stdout, stdout_lines, logging.INFO),
        _read_stream(proc.stderr, stderr_lines, logging.WARNING, cb=progress_callback),
    )
    await proc.wait()
    return proc.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)


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
    result = await _shutdown_server()
    logger.info("Idle shutdown result: %s", result)


# ---------------------------------------------------------------------------
# vast-agent subprocess wrappers
# ---------------------------------------------------------------------------

async def _ensure_server_up(
    workflow: str,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Make sure the GPU server is running. Returns a status message."""
    # Check current status
    rc, _, _ = await _run_subprocess("vast-agent", "status")
    if rc == 0:
        logger.info("Server already running")
        return "Server already running."

    # Not running — bring it up
    logger.info("Server not running, starting with workflow %s", workflow)
    rc, stdout, stderr = await _run_subprocess(
        "vast-agent", "up", "-w", workflow,
        stream=True, progress_callback=progress_callback,
    )
    if rc != 0:
        msg = stderr.strip() or stdout.strip() or "Unknown error"
        raise RuntimeError(f"vast-agent up failed: {msg}")
    return "Server started."


async def _run_generation(
    workflow: str,
    image_path: str,
    video_path: str,
    prompt: str,
    output_dir: str = "output",
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> list[str]:
    """Run a generation via vast-agent and return list of local output paths."""
    cmd: list[str] = [
        "vast-agent", "run",
        "-w", workflow,
        "--input", f"reference_image={image_path}",
        "--input", f"reference_video={video_path}",
        "--set", f"prompt={prompt}",
        "--json-output",
        "-o", output_dir,
    ]

    logger.info("Running generation: %s", " ".join(cmd))
    rc, stdout, stderr = await _run_subprocess(*cmd, stream=True, progress_callback=progress_callback)

    if rc != 0:
        msg = stderr.strip() or stdout.strip() or "Unknown error"
        raise RuntimeError(f"vast-agent run failed: {msg}")

    # Parse JSON output to get file paths
    try:
        result = json.loads(stdout.strip())
        return result.get("outputs", [])
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse JSON output, returning raw stdout")
        return []


async def _shutdown_server() -> str:
    """Destroy the GPU server via vast-agent down."""
    rc, stdout, stderr = await _run_subprocess("vast-agent", "down", stream=True)
    if rc != 0:
        msg = stderr.strip() or stdout.strip() or "Unknown error"
        logger.error("vast-agent down failed: %s", msg)
        return f"Shutdown failed: {msg}"
    return "Server destroyed."


async def _get_server_cost() -> str:
    """Get session cost from vast-agent status."""
    rc, stdout, _ = await _run_subprocess("vast-agent", "status")
    if rc != 0:
        return "unknown"
    # Try to extract cost line from status output
    for line in stdout.splitlines():
        if "cost" in line.lower() or "$" in line:
            return line.strip()
    return "see vast-agent status"


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

        cost_info = await _get_server_cost()
        result = await _shutdown_server()
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

        # Progress messages
        progress_msg = await update.message.reply_text("Renting GPU...")

        # Build a throttled progress callback that edits progress_msg
        _PROGRESS_PATTERNS = [
            (re.compile(r"Uploading input files"), "Uploading input files..."),
            (re.compile(r"Uploading (\w+)"), "Uploading {1}..."),
            (re.compile(r"Running workflow on remote"), "Running workflow..."),
            (re.compile(r"Queuing prompt"), "Queuing prompt..."),
            (re.compile(r"Waiting for completion"), "Executing workflow..."),
            (re.compile(r"Executing node \w+ \((\w+)\)"), None),  # handled specially
            (re.compile(r"Saved:"), "Saving output..."),
            (re.compile(r"Downloading results"), "Downloading results..."),
        ]
        _last_edit_time = 0.0
        _node_count = 0
        _THROTTLE_SECONDS = 3.0

        async def _progress_cb(line: str) -> None:
            nonlocal _last_edit_time, _node_count

            text = None
            for pattern, template in _PROGRESS_PATTERNS:
                m = pattern.search(line)
                if m:
                    if template is None:
                        # Executing node — count steps and show node type
                        _node_count += 1
                        node_type = m.group(1) if m.lastindex else "?"
                        text = f"Step {_node_count}: {node_type}"
                    elif "{1}" in template:
                        text = template.replace("{1}", m.group(1))
                    else:
                        text = template
                    break

            if text is None:
                return

            now = time.monotonic()
            if now - _last_edit_time < _THROTTLE_SECONDS:
                return

            try:
                await progress_msg.edit_text(text)
                _last_edit_time = now
            except Exception:
                logger.debug("Failed to edit progress message", exc_info=True)

        try:
            server_status = await _ensure_server_up(workflow, progress_callback=_progress_cb)
            await progress_msg.edit_text(f"{server_status}\nRunning generation...")
        except RuntimeError as e:
            msg = str(e)[:4000]
            await progress_msg.edit_text(f"Failed to start server: {msg}")
            return WAITING_FEEDBACK

        try:
            output_dir = str(PROJECT_ROOT / "output")
            outputs = await _run_generation(
                workflow=workflow,
                image_path=image_path,
                video_path=video_path,
                prompt=prompt,
                output_dir=output_dir,
                progress_callback=_progress_cb,
            )
        except RuntimeError as e:
            msg = str(e)[:4000]
            await progress_msg.edit_text(f"Generation failed: {msg}")
            return WAITING_FEEDBACK

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
        """Handle /parse [hashtags]. Ingest trends and start review flow."""
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

        status_msg = await update.message.reply_text(
            "Parsing trending videos... This may take a minute."
        )

        client = StudioClient(config.studio_base_url)
        context.user_data[KEY_STUDIO_CLIENT] = client
        context.user_data[KEY_WORKFLOW] = config.default_workflow

        try:
            result = await client.run_pipeline(
                influencer_id=config.studio_influencer_id,
                platforms=["tiktok"],
                hashtags=hashtags or None,
                limit=20,
            )
        except Exception as exc:
            await status_msg.edit_text(f"Pipeline failed: {exc}")
            return ConversationHandler.END

        # Extract selected videos from pipeline result
        platforms_out = result.get("platforms", [])
        if not platforms_out:
            await status_msg.edit_text("Pipeline returned no platform results.")
            return ConversationHandler.END

        selected_dir = platforms_out[0].get("selected_dir", "")
        if not selected_dir:
            await status_msg.edit_text("Pipeline returned no selected_dir.")
            return ConversationHandler.END

        video_dir = Path(selected_dir)
        if not video_dir.exists():
            await status_msg.edit_text(
                "VLM stage selected 0 videos (directory does not exist)."
            )
            return ConversationHandler.END

        video_files = sorted(video_dir.glob("*.mp4"))
        if not video_files:
            await status_msg.edit_text(
                f"No .mp4 files in {video_dir}"
            )
            return ConversationHandler.END

        items = []
        _views_re = re.compile(r"views(\d+)")
        for f in video_files:
            m = _views_re.search(f.stem)
            views = f"{int(m.group(1)):,}" if m else "?"
            items.append({
                "_local_path": str(f),
                "caption": f.stem,
                "views": views,
            })

        run_id = platforms_out[0].get("trend_run_id", 0)
        session = ParseSession(
            run_id=run_id,
            items=items,
            influencer_id=config.studio_influencer_id,
            selected_dir=str(video_dir),
            workflow=config.default_workflow,
        )
        context.user_data[KEY_PARSE_SESSION] = session

        await status_msg.edit_text(
            f"Found {len(items)} filtered videos.\n"
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

        text = (
            f"[{idx}/{total}] {caption}\n"
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
            server_status = await _ensure_server_up(workflow)
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
                item_output_dir = str(
                    session.session_dir / "results" / f"{i:03d}_{caption_slug}"
                )
                Path(item_output_dir).mkdir(parents=True, exist_ok=True)
            else:
                item_output_dir = str(PROJECT_ROOT / "output")

            item.status = "generating"
            session.save()

            try:
                outputs = await _run_generation(
                    workflow=workflow,
                    image_path=item.image_path,
                    video_path=item.video_path,
                    prompt=item.prompt,
                    output_dir=item_output_dir,
                )
            except RuntimeError as e:
                item.status = "failed"
                session.save()
                await chat.send_message(
                    f"Generation {i}/{total} failed: {str(e)[:2000]}"
                )
                continue

            item.status = "completed"
            item.output_paths = outputs
            session.save()

            # Send results
            sent = False
            for output_path in outputs:
                path = Path(output_path)
                if path.exists() and path.suffix.lower() in (".mp4", ".webm", ".mov"):
                    await chat.send_video(
                        video=str(path),
                        caption=f"[{i}/{total}] {snippet}",
                    )
                    sent = True
                elif path.exists():
                    await chat.send_document(
                        document=str(path),
                        caption=f"[{i}/{total}] {snippet}",
                    )
                    sent = True
            if sent:
                successes += 1

        completed_total = sum(1 for q in queue if q.status == "completed")
        await progress_msg.edit_text(
            f"Batch complete! {completed_total}/{total} videos generated.\n"
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
            # Restore reference image from session dir
            ref_img = incomplete[0] / "reference_image.jpg"
            if ref_img.exists():
                context.user_data[KEY_IMAGE] = str(ref_img)

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
