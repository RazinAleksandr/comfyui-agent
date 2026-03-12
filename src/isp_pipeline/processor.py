from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def add_grain(frame: np.ndarray, intensity: int) -> np.ndarray:
    """Add film grain noise. intensity: 0-100."""
    if intensity <= 0:
        return frame
    # Scale intensity to noise sigma (0-100 -> 0-50 sigma range)
    sigma = intensity * 0.5
    noise = np.random.normal(0, sigma, frame.shape).astype(np.float32)
    result = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return result


def add_sharpness(frame: np.ndarray, intensity: int) -> np.ndarray:
    """Apply unsharp mask. intensity: 0-100."""
    if intensity <= 0:
        return frame
    # Unsharp mask: sharpen = original + amount * (original - blurred)
    amount = intensity / 100.0 * 2.0  # 0-100 maps to 0-2.0 strength
    blurred = cv2.GaussianBlur(frame, (0, 0), 3)
    sharpened = cv2.addWeighted(frame, 1.0 + amount, blurred, -amount, 0)
    return sharpened


def adjust_brightness(frame: np.ndarray, value: int) -> np.ndarray:
    """Adjust brightness. value: -100 to 100."""
    if value == 0:
        return frame
    # Scale value to pixel adjustment (-100..100 -> -100..100 pixel shift)
    result = np.clip(frame.astype(np.int16) + value, 0, 255).astype(np.uint8)
    return result


def add_vignette(frame: np.ndarray, intensity: int) -> np.ndarray:
    """Add vignette (dark corners). intensity: 0-100."""
    if intensity <= 0:
        return frame
    rows, cols = frame.shape[:2]
    # Create radial gradient mask
    X = np.arange(cols) - cols / 2
    Y = np.arange(rows) - rows / 2
    X, Y = np.meshgrid(X, Y)
    # Normalized distance from center (0 at center, 1 at corners)
    max_dist = np.sqrt((cols / 2) ** 2 + (rows / 2) ** 2)
    dist = np.sqrt(X ** 2 + Y ** 2) / max_dist
    # Vignette strength: higher intensity = darker corners
    strength = intensity / 100.0
    # Smooth falloff using power curve
    mask = 1.0 - strength * (dist ** 2)
    mask = np.clip(mask, 0, 1).astype(np.float32)
    # Apply to all channels
    if len(frame.shape) == 3:
        mask = mask[:, :, np.newaxis]
    result = (frame.astype(np.float32) * mask).astype(np.uint8)
    return result


def process_video(
    input_path: Path,
    output_path: Path,
    graininess: int = 40,
    sharpness: int = 20,
    brightness: int = -5,
    vignette: int = 10,
) -> Path:
    """Process a video file with ISP post-processing effects.

    Returns the output path.
    """
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write processed frames to a temp file (OpenCV uses mp4v/mpeg4)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="isp_")
    tmp_path = Path(tmp_path)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_path), fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Cannot open output video writer")

    logger.info(
        "Processing %s (%dx%d, %.1f fps, %d frames)",
        input_path.name, width, height, fps, total_frames,
    )
    logger.info(
        "Settings: graininess=%d, sharpness=%d, brightness=%d, vignette=%d",
        graininess, sharpness, brightness, vignette,
    )

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Apply ISP pipeline in order
            frame = adjust_brightness(frame, brightness)
            frame = add_sharpness(frame, sharpness)
            frame = add_grain(frame, graininess)
            frame = add_vignette(frame, vignette)

            writer.write(frame)
            frame_idx += 1

            if frame_idx % 30 == 0:
                pct = frame_idx / total_frames * 100 if total_frames > 0 else 0
                print(
                    f"\r  Processing: {frame_idx}/{total_frames} frames ({pct:.0f}%)",
                    end="", file=sys.stderr,
                )
    finally:
        cap.release()
        writer.release()

    pct = frame_idx / total_frames * 100 if total_frames > 0 else 100
    print(f"\r  Processing: {frame_idx}/{total_frames} frames ({pct:.0f}%)", file=sys.stderr)

    # Re-encode to H.264 and copy audio from source
    _remux_h264(tmp_path, input_path, output_path)
    tmp_path.unlink(missing_ok=True)

    logger.info("Saved: %s", output_path)
    return output_path


def _remux_h264(processed: Path, original: Path, output: Path) -> None:
    """Re-encode processed video to H.264 and copy audio from original."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(processed),       # processed frames (mp4v, no audio)
        "-i", str(original),         # original (for audio stream)
        "-map", "0:v:0",             # video from processed
        "-map", "1:a:0?",            # audio from original (optional)
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr[-500:] if result.stderr else "")
        raise RuntimeError(f"ffmpeg re-encode failed: {result.stderr[-200:]}")


VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm"}

# Priority order: best quality first
_SOURCE_PREFIXES = ("upscaled_", "refined_", "raw_")


def _strip_source_prefix(name: str) -> str:
    """Strip raw_/refined_/upscaled_ prefix from a filename."""
    for pfx in _SOURCE_PREFIXES:
        if name.startswith(pfx):
            return name[len(pfx):]
    return name


def _pick_best_source(directory: Path) -> tuple[Path, str] | None:
    """Pick the single best source video from a result directory.

    Priority: upscaled > refined > raw > unprefixed.
    Returns (source_path, output_filename) where output_filename is
    ``postprocessed_<base>``, or None if no videos found.
    """
    videos = [
        str(f) for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS
    ]
    best = pick_best_source(videos)
    if best is None:
        return None
    source = Path(best)
    base = _strip_source_prefix(source.name)
    return source, f"postprocessed_{base}"


def process_directory(
    input_dir: Path,
    graininess: int = 40,
    sharpness: int = 20,
    brightness: int = -5,
    vignette: int = 10,
) -> list[Path]:
    """Process video files in a directory tree in-place.

    For each result subdirectory, picks the best source video
    (upscaled > refined > raw) and saves postprocessed_<base>
    in the same folder next to the source.
    """
    # Collect work items: (source_path, output_path)
    work: list[tuple[Path, Path]] = []

    for dirpath in sorted(_iter_leaf_dirs(input_dir)):
        picked = _pick_best_source(dirpath)
        if picked:
            source, out_name = picked
            work.append((source, source.parent / out_name))

    # Fallback: if input_dir itself contains videos
    if not work:
        picked = _pick_best_source(input_dir)
        if picked:
            source, out_name = picked
            work.append((source, source.parent / out_name))

    if not work:
        print(f"No video files found in {input_dir}", file=sys.stderr)
        return []

    print(f"Found {len(work)} video(s) to process", file=sys.stderr)
    results = []

    for source_path, out_path in work:
        print(f"\n  {source_path.name} -> {out_path.name}", file=sys.stderr)

        result = process_video(
            source_path, out_path,
            graininess=graininess,
            sharpness=sharpness,
            brightness=brightness,
            vignette=vignette,
        )
        results.append(result)

    return results


def _iter_leaf_dirs(root: Path):
    """Yield directories that contain files (leaf-level result dirs)."""
    for dirpath in sorted(root.rglob("*")):
        if dirpath.is_dir() and any(f.is_file() for f in dirpath.iterdir()):
            yield dirpath


def pick_best_source(output_paths: list[str]) -> str | None:
    """Given a list of generation output paths, return the best one.

    Priority: upscaled > refined > raw > first video found.
    """
    videos = [p for p in output_paths if Path(p).suffix.lower() in VIDEO_EXTS]
    if not videos:
        return None
    for pfx in _SOURCE_PREFIXES:
        for v in videos:
            if Path(v).name.startswith(pfx):
                return v
    return videos[0]


def postprocess_outputs(
    output_paths: list[str],
    graininess: int = 40,
    sharpness: int = 20,
    brightness: int = -5,
    vignette: int = 10,
) -> str | None:
    """Pick the best video from output_paths, postprocess it in-place.

    Returns the postprocessed file path, or None if nothing to process.
    """
    source = pick_best_source(output_paths)
    if source is None:
        return None

    source_path = Path(source)
    base = _strip_source_prefix(source_path.name)
    out_path = source_path.parent / f"postprocessed_{base}"

    logger.info("Postprocessing %s -> %s", source_path.name, out_path.name)
    process_video(
        source_path, out_path,
        graininess=graininess,
        sharpness=sharpness,
        brightness=brightness,
        vignette=vignette,
    )
    return str(out_path)
