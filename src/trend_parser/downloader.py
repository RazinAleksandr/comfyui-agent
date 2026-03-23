from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from trend_parser.adapters.types import RawTrendVideo
from trend_parser.config import ParserConfig


class TrendDownloadService:
    def __init__(self, config: ParserConfig, downloads_dir: Path):
        self.config = config
        self.downloads_dir = downloads_dir

    def download_raw_videos(
        self,
        *,
        platform: str,
        videos: list[RawTrendVideo],
        force: bool = False,
        download_dir: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        output: list[dict] = []
        total = len(videos)
        for index, video in enumerate(videos, start=1):
            if not video.video_url:
                output.append(
                    {
                        "index": index,
                        "platform": platform,
                        "source_item_id": video.source_item_id,
                        "source_url": "",
                        "status": "skipped",
                        "local_path": None,
                        "error_message": "Item has no video_url",
                    }
                )
                continue
            try:
                download_info = self._run_ytdlp(url=video.video_url, platform=platform, download_dir=download_dir)
                downloaded_path = Path(download_info["local_path"])
                path = self._rename_download_for_raw_video(downloaded_path, video)
                output.append(
                    {
                        "index": index,
                        "platform": platform,
                        "source_item_id": video.source_item_id,
                        "source_url": video.video_url,
                        "status": "downloaded",
                        "local_path": str(path.resolve()),
                        "file_ext": path.suffix.lstrip(".") or None,
                        "file_size_bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
            except Exception as exc:
                output.append(
                    {
                        "index": index,
                        "platform": platform,
                        "source_item_id": video.source_item_id,
                        "source_url": video.video_url,
                        "status": "failed",
                        "local_path": None,
                        "error_message": str(exc),
                    }
                )
            if progress_callback:
                progress_callback(index, total)
        return output

    def _run_ytdlp(self, url: str, platform: str, download_dir: str | None = None) -> dict:
        command = self.config.yt_dlp_command.strip()
        if not command:
            raise RuntimeError("YT_DLP_COMMAND is empty")

        command_parts = shlex.split(command)
        binary = command_parts[0]
        # Use python -m yt_dlp to avoid stale shebang issues after venv moves
        if binary == "yt-dlp":
            command_parts = [sys.executable, "-m", "yt_dlp"] + command_parts[1:]
        else:
            resolved_binary = _resolve_downloader_binary(binary)
            command_parts[0] = resolved_binary

        base_dir = Path(download_dir).expanduser().resolve() if download_dir else self.downloads_dir.resolve()
        # When download_dir is provided by the pipeline runner, it already
        # contains the platform path (e.g. .../instagram/downloads).
        # Only add a platform subdirectory for the legacy shared downloads dir.
        if download_dir:
            platform_dir = base_dir
        else:
            platform_dir = base_dir / platform
        platform_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(platform_dir / "%(extractor)s_%(id)s.%(ext)s")
        args = [
            *command_parts,
            "--no-progress",
            "--newline",
            "--no-playlist",
            "--restrict-filenames",
            "--format",
            self.config.yt_dlp_format,
            "--merge-output-format",
            self.config.yt_dlp_merge_format,
            "--print",
            "after_move:filepath",
            "--output",
            output_template,
            url,
        ]

        cookie_file = self.config.yt_dlp_cookies_file
        if cookie_file:
            cookie_path = Path(cookie_file).expanduser()
            if cookie_path.exists():
                args[1:1] = ["--cookies", str(cookie_path)]

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.config.download_timeout_sec,
            env=os.environ.copy(),
        )

        stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stderr_lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]

        if result.returncode != 0:
            tail = "\n".join(stderr_lines[-4:] or stdout_lines[-4:])
            raise RuntimeError(f"yt-dlp failed: {tail}")

        local_path = stdout_lines[-1] if stdout_lines else ""
        if not local_path:
            raise RuntimeError("yt-dlp did not return a file path")

        path = Path(local_path)
        if not path.exists():
            raise RuntimeError(f"Downloaded file does not exist: {path}")

        return {
            "local_path": str(path),
            "stdout_tail": stdout_lines[-5:],
            "stderr_tail": stderr_lines[-5:],
        }

    def _rename_download_for_raw_video(self, downloaded_path: Path, video: RawTrendVideo) -> Path:
        if not downloaded_path.exists():
            raise RuntimeError(f"Downloaded file does not exist: {downloaded_path}")

        suffix = downloaded_path.suffix or ".mp4"
        target_name = _build_raw_video_filename(video=video, suffix=suffix)
        if downloaded_path.name == target_name:
            return downloaded_path

        target_path = downloaded_path.with_name(target_name)
        if target_path.exists():
            target_path.unlink()
        downloaded_path.rename(target_path)
        return target_path


def _sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _build_raw_video_filename(video: RawTrendVideo, suffix: str) -> str:
    date_part = (
        video.published_at.astimezone(datetime.now(UTC).tzinfo or UTC).strftime("%Y%m%d")
        if video.published_at
        else "unknown"
    )
    views_part = str(max(0, int(video.views or 0)))
    source_uid = _sanitize_token(video.source_item_id or "")
    if not source_uid:
        fallback_seed = video.video_url or f"{video.platform}-{date_part}-{views_part}"
        source_uid = hashlib.sha1(fallback_seed.encode("utf-8")).hexdigest()[:12]
    return f"{video.platform}_{date_part}_views{views_part}_uid{source_uid}{suffix}"


def _sanitize_token(raw: str, limit: int = 64) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "", str(raw or ""))
    return token[:limit]


def _resolve_downloader_binary(binary: str) -> str:
    binary_path = Path(binary).expanduser()
    if binary_path.is_file():
        return str(binary_path.resolve())

    resolved = shutil.which(binary)
    if resolved:
        return resolved

    if binary == "yt-dlp":
        candidates = [
            Path(sys.executable).with_name("yt-dlp"),
            Path(sys.executable).resolve().parent / "yt-dlp",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    raise RuntimeError(
        f"Downloader '{binary}' not found in PATH. Install yt-dlp and/or update YT_DLP_COMMAND"
    )
