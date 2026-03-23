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
    close_up: bool = False,
) -> str:
    """Build the Gemini prompt for reference image alignment."""
    appearance_block = (
        f"\n\nCharacter identity details: {appearance_description}"
        if appearance_description else ""
    )
    video_block = (
        f"\n\nVideo context: {video_prompt}"
        if video_prompt else ""
    )

    if close_up:
        framing = (
            "Frame the output as a close-up portrait: head and shoulders only, "
            "face centered and clearly visible."
        )
    else:
        framing = (
            "Match the same framing and body crop as the video frame "
            "(close-up, medium shot, or full-body — whatever image 2 shows)."
        )

    return f"""You are given TWO images:
1. CHARACTER REFERENCE — the person whose identity you must preserve
2. VIDEO FRAME — the target scene whose lighting, pose, and style you must match

Generate a NEW photo-realistic image of the person from image 1, as if they were
photographed in the exact scene from image 2. Do NOT cut-and-paste or face-swap —
re-imagine the entire image from scratch so that lighting falls naturally on the
character's face and body.
{appearance_block}

CHARACTER IDENTITY (from image 1 — preserve exactly):
- All facial features, face shape, skin tone
- Eye color and shape (preserve any asymmetry such as heterochromia)
- Hair color and texture
- Body type and proportions

SCENE CONTEXT (from image 2 — match naturally):
- Pose and body position of the person in the video frame
- Clothing style and accessories worn by the person in the video frame
- Background, environment, and atmosphere
- CRITICAL: Lighting direction, intensity, color temperature, shadows, and highlights
  must be consistent across the entire image. The face and body must be lit by the
  same light sources as the background. No mismatched brightness or color casts.
{video_block}

OUTPUT REQUIREMENTS:
- {framing}
- Photorealistic — looks like a real photograph, not a composite
- Uniform lighting: face, body, and background share the same light
- The person must be unmistakably the same individual as in image 1
- No text, watermarks, borders, or artifacts""".strip()


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
    close_up: bool = False,
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
            model = "gemini-3.1-flash-image-preview"

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
            prompt = build_alignment_prompt(appearance_description, video_prompt, close_up=close_up)

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
