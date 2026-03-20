"""Gemini QA review for generated videos.

After a video is generated, this module sends both the original source video
and the generated output to Gemini for quality comparison. Results are saved
to the generation_jobs table and pushed to the frontend via SSE.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import requests

from trend_parser.gemini import MIME_BY_SUFFIX, extract_json_object, sanitize_error_message

logger = logging.getLogger(__name__)

QA_PROMPT = """
You are a strict quality reviewer for AI-generated videos where a person has been swapped/replaced.

You are given TWO videos:
1. ORIGINAL — the source video with the original person
2. GENERATED — the AI-generated version where the person has been replaced

Compare them and evaluate the quality of the generated video. Look for:

**Face & Identity:**
- Face distortion, warping, or melting
- Inconsistent facial features across frames
- Unnatural expressions or frozen face
- Extra faces or ghost faces appearing

**Body & Proportions:**
- Body distortion or unnatural proportions
- Missing or extra limbs/fingers
- Body parts clipping through each other
- Unnatural body movements or poses

**Temporal Consistency:**
- Flickering or jittering
- Sudden appearance changes between frames
- Motion artifacts or ghosting
- Abrupt transitions or jumps

**Overall Quality:**
- Blurriness or loss of detail compared to original
- Color artifacts or banding
- Background distortion or warping
- Lighting inconsistencies

**Motion Preservation:**
- Does the generated person perform the same actions as the original?
- Is the camera angle and framing preserved?
- Are gestures and body language maintained?

Scoring (0-10, higher is better):
- face_quality: face naturalness and consistency
- body_quality: body proportions and movement naturalness
- temporal_consistency: frame-to-frame stability
- motion_preservation: how well original motion is replicated
- overall_quality: general visual quality

Verdict: "pass" (good enough for publishing), "marginal" (minor issues), or "fail" (significant problems)

Output format rules:
- Return ONLY valid JSON
- No markdown, no extra commentary
- Use this exact schema:
{
  "verdict": "pass|marginal|fail",
  "score": 0.0,
  "issues": ["issue1", "issue2"],
  "scores": {
    "face_quality": 0,
    "body_quality": 0,
    "temporal_consistency": 0,
    "motion_preservation": 0,
    "overall_quality": 0
  },
  "summary": "brief 1-2 sentence summary of quality"
}

The "score" field should be the overall score from 0.0 to 10.0.
The "issues" array should list specific problems found (empty if none).
""".strip()


def _call_gemini_two_videos(
    *,
    model: str,
    api_key: str,
    original_path: Path,
    generated_path: Path,
    prompt: str,
    timeout_sec: int = 180,
    temperature: float = 0.1,
) -> tuple[dict, str]:
    """Call Gemini with two videos (original + generated) and return parsed JSON + raw text."""
    orig_mime = MIME_BY_SUFFIX.get(original_path.suffix.lower())
    gen_mime = MIME_BY_SUFFIX.get(generated_path.suffix.lower())
    if not orig_mime:
        raise RuntimeError(f"unsupported video extension: {original_path.suffix}")
    if not gen_mime:
        raise RuntimeError(f"unsupported video extension: {generated_path.suffix}")

    orig_b64 = base64.b64encode(original_path.read_bytes()).decode("ascii")
    gen_b64 = base64.b64encode(generated_path.read_bytes()).decode("ascii")

    payload = {
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"text": "ORIGINAL VIDEO:"},
                    {"inlineData": {"mimeType": orig_mime, "data": orig_b64}},
                    {"text": "GENERATED VIDEO:"},
                    {"inlineData": {"mimeType": gen_mime, "data": gen_b64}},
                ],
            }
        ],
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

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_parts = [str(part.get("text") or "").strip() for part in parts if part.get("text")]
    model_text = "\n".join([p for p in text_parts if p]).strip()
    if not model_text:
        raise RuntimeError(f"no text output in model response: {data}")

    parsed = extract_json_object(model_text)
    return parsed, model_text


async def run_qa_review(
    *,
    job_id: str,
    original_video_path: str,
    generated_video_path: str,
    model: str = "",
) -> None:
    """Run QA review comparing original and generated videos.

    Updates generation_jobs table and publishes SSE event.
    Non-blocking — intended to be called via asyncio.create_task().
    """
    from api.deps import get_config, get_db, get_event_bus

    if not model:
        model = get_config().gemini_model

    db = get_db()
    bus = get_event_bus()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    # Mark as pending
    await db.execute(
        "UPDATE generation_jobs SET qa_status = 'pending' WHERE job_id = ?",
        [job_id],
    )
    bus.publish("events", "qa_review", {
        "job_id": job_id,
        "qa_status": "pending",
    })

    if not api_key:
        logger.warning("[qa] no GEMINI_API_KEY, skipping QA review for %s", job_id)
        await db.execute(
            "UPDATE generation_jobs SET qa_status = 'failed', "
            "qa_result_json = ?, qa_completed_at = ? WHERE job_id = ?",
            [json.dumps({"error": "missing GEMINI_API_KEY"}), datetime.now(UTC).isoformat(), job_id],
        )
        bus.publish("events", "qa_review", {
            "job_id": job_id,
            "qa_status": "failed",
            "qa_result": {"error": "missing GEMINI_API_KEY"},
        })
        return

    orig = Path(original_video_path)
    gen = Path(generated_video_path)

    if not orig.is_file() or not gen.is_file():
        err = f"video files not found: orig={orig.exists()} gen={gen.exists()}"
        logger.warning("[qa] %s for %s", err, job_id)
        await db.execute(
            "UPDATE generation_jobs SET qa_status = 'failed', "
            "qa_result_json = ?, qa_completed_at = ? WHERE job_id = ?",
            [json.dumps({"error": err}), datetime.now(UTC).isoformat(), job_id],
        )
        bus.publish("events", "qa_review", {
            "job_id": job_id,
            "qa_status": "failed",
            "qa_result": {"error": err},
        })
        return

    try:
        last_error = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    wait = 2 ** attempt
                    logger.info("[qa] retry %d/2 for %s after %ds", attempt, job_id, wait)
                    await asyncio.sleep(wait)

                result, _raw = await asyncio.to_thread(
                    _call_gemini_two_videos,
                    model=model,
                    api_key=api_key,
                    original_path=orig,
                    generated_path=gen,
                    prompt=QA_PROMPT,
                )
                break
            except Exception as exc:
                last_error = exc
        else:
            raise last_error  # type: ignore[misc]

        now = datetime.now(UTC).isoformat()
        await db.execute(
            "UPDATE generation_jobs SET qa_status = 'completed', "
            "qa_result_json = ?, qa_completed_at = ? WHERE job_id = ?",
            [json.dumps(result), now, job_id],
        )
        bus.publish("events", "qa_review", {
            "job_id": job_id,
            "qa_status": "completed",
            "qa_result": result,
        })
        logger.info("[qa] %s → %s (score=%.1f)", job_id, result.get("verdict", "?"), result.get("score", 0))

    except Exception as exc:
        safe_msg = sanitize_error_message(str(exc), api_key=api_key)
        logger.warning("[qa] failed for %s: %s", job_id, safe_msg)
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "UPDATE generation_jobs SET qa_status = 'failed', "
            "qa_result_json = ?, qa_completed_at = ? WHERE job_id = ?",
            [json.dumps({"error": safe_msg}), now, job_id],
        )
        bus.publish("events", "qa_review", {
            "job_id": job_id,
            "qa_status": "failed",
            "qa_result": {"error": safe_msg},
        })
