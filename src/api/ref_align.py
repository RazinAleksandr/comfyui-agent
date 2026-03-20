"""Reference alignment: adapt character reference image to match target video context.

Before video generation, uses Gemini image generation (Nano Banana) to create
an adapted reference image that preserves character identity but adjusts
clothing, hairstyle, and pose to match the target video.
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
import time
from pathlib import Path

import requests

from trend_parser.gemini import IMAGE_MIME_BY_SUFFIX, sanitize_error_message

logger = logging.getLogger(__name__)


def extract_video_frame(video_path: Path, time_sec: float = 1.0) -> Path:
    """Extract a single JPEG frame from a video at the given timestamp."""
    output = video_path.parent / f"{video_path.stem}_frame.jpg"
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr[:500]}")
    return output


def build_alignment_prompt(
    appearance_description: str = "",
    video_prompt: str = "",
) -> str:
    """Build the Gemini prompt for reference image alignment."""
    appearance_block = (
        f"\nThe character's key features: {appearance_description}"
        if appearance_description else ""
    )
    video_block = (
        f"\nContext about the target video: {video_prompt}"
        if video_prompt else ""
    )
    return f"""You are given TWO images:
1. CHARACTER REFERENCE — the person whose identity must be preserved
2. VIDEO FRAME — a frame from the target video showing the desired scene

Generate a new photo of the person from image 1, adapted to match the context of image 2.

PRESERVE from the character reference:
- Face shape, facial features, skin tone
- Eye color and shape
- Overall body type and proportions
{appearance_block}

ADAPT to match the video frame:
- Clothing style and colors (match what the person in the video frame is wearing)
- Hair styling (adapt to match the video context)
- Body pose and camera angle (match the approximate pose in the video frame)
- Lighting direction and color temperature
{video_block}

IMPORTANT:
- The generated image must clearly be the SAME PERSON as in image 1
- Photo-realistic, clean portrait suitable as a reference for video generation
- No text, watermarks, or borders
- Single person only""".strip()


def call_gemini_generate_image(
    *,
    model: str,
    api_key: str,
    reference_image_path: Path,
    video_frame_path: Path,
    prompt: str,
    timeout_sec: int = 120,
) -> bytes:
    """Call Gemini image generation API with two reference images and a text prompt.

    Returns raw image bytes (JPEG/PNG).
    """
    parts: list[dict] = []

    # Add reference image
    ref_mime = IMAGE_MIME_BY_SUFFIX.get(reference_image_path.suffix.lower())
    if not ref_mime:
        ref_mime = "image/jpeg"
    ref_b64 = base64.b64encode(reference_image_path.read_bytes()).decode("ascii")
    parts.append({"inlineData": {"mimeType": ref_mime, "data": ref_b64}})

    # Add video frame
    frame_b64 = base64.b64encode(video_frame_path.read_bytes()).decode("ascii")
    parts.append({"inlineData": {"mimeType": "image/jpeg", "data": frame_b64}})

    # Add text prompt
    parts.append({"text": prompt})

    payload = {
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"],
        },
        "contents": [{"role": "user", "parts": parts}],
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(
        url,
        params={"key": api_key},
        json=payload,
        timeout=timeout_sec,
    )
    response.raise_for_status()

    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"no candidates returned: {data}")

    # Find the image part in the response
    resp_parts = (candidates[0].get("content") or {}).get("parts") or []
    for part in resp_parts:
        inline = part.get("inlineData")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])

    # No image in response — check for text explanation (safety filter, etc.)
    text_parts = [p.get("text", "") for p in resp_parts if p.get("text")]
    text_msg = " ".join(text_parts).strip()
    raise RuntimeError(f"no image in response. Model text: {text_msg[:300]}")


async def align_reference_image(
    *,
    influencer_id: str,
    reference_image_path: str,
    reference_video_path: str,
    appearance_description: str = "",
    video_prompt: str = "",
    output_dir: str,
    model: str = "",
    job_id: str = "",
) -> str | None:
    """Generate an aligned reference image adapted to the target video.

    Returns the filesystem path to the aligned image, or None on failure.
    Designed to be non-blocking — failures fall back to the original reference.
    """
    import asyncio

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.warning("GEMINI_API_KEY not set, skipping reference alignment")
        return None

    if not model:
        try:
            from api.deps import get_config
            model = get_config().gemini_image_model
        except Exception:
            model = "gemini-2.5-flash-image"

    ref_path = Path(reference_image_path)
    video_path = Path(reference_video_path)

    if not ref_path.exists() or not video_path.exists():
        logger.warning("Reference image or video not found, skipping alignment")
        return None

    # Create output directory
    aligned_dir = Path(output_dir) / "aligned"
    aligned_dir.mkdir(parents=True, exist_ok=True)
    output_path = aligned_dir / f"{video_path.stem}_aligned.jpg"

    def _do_align() -> str:
        # Step 1: Extract frame from video
        frame_path = extract_video_frame(video_path, time_sec=1.0)

        try:
            # Step 2: Build prompt
            prompt = build_alignment_prompt(appearance_description, video_prompt)

            # Step 3: Call Gemini with retries
            last_error = None
            for attempt in range(3):
                try:
                    if attempt > 0:
                        wait = 2 ** attempt
                        logger.info("Alignment retry %d/2 after %ds", attempt, wait)
                        time.sleep(wait)
                    image_bytes = call_gemini_generate_image(
                        model=model,
                        api_key=api_key,
                        reference_image_path=ref_path,
                        video_frame_path=frame_path,
                        prompt=prompt,
                        timeout_sec=120,
                    )
                    # Step 4: Save the aligned image
                    output_path.write_bytes(image_bytes)
                    logger.info("Aligned reference saved: %s (%d bytes)", output_path, len(image_bytes))
                    return str(output_path)
                except Exception as exc:
                    last_error = exc
                    safe_msg = sanitize_error_message(str(exc), api_key=api_key)
                    logger.warning("Alignment attempt %d failed: %s", attempt + 1, safe_msg)

            safe_msg = sanitize_error_message(str(last_error), api_key=api_key)
            raise RuntimeError(f"alignment failed after 3 attempts: {safe_msg}")
        finally:
            # Clean up extracted frame
            try:
                frame_path.unlink(missing_ok=True)
            except Exception:
                pass

    try:
        result = await asyncio.to_thread(_do_align)

        # Persist aligned image path to DB (stored as relative)
        if job_id:
            try:
                from api.deps import get_db, get_store
                from api.path_utils import to_relative
                db = get_db()
                rel_path = to_relative(result, get_store().data_dir)
                await db.execute(
                    "UPDATE generation_jobs SET aligned_image_path = ? WHERE job_id = ?",
                    [rel_path, job_id],
                )
            except Exception:
                logger.warning("Failed to save aligned_image_path to DB", exc_info=True)

        return result
    except Exception:
        logger.warning("Reference alignment failed", exc_info=True)
        return None
