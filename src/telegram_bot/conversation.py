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

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

WAITING_IMAGE, WAITING_VIDEO, WAITING_PROMPT, WAITING_FEEDBACK = range(4)

# user_data keys
KEY_IMAGE = "image_path"
KEY_VIDEO = "video_path"
KEY_PROMPT = "prompt"
KEY_WORKFLOW = "workflow"
KEY_TMPDIR = "tmpdir"

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
    # Build the ConversationHandler
    # -----------------------------------------------------------------------

    handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
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
        },
        fallbacks=[
            CommandHandler("stop", stop),
            CommandHandler("cancel", cancel),
        ],
    )

    return handler
