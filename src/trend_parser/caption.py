from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from trend_parser.gemini import call_gemini, sanitize_error_message


@dataclass(slots=True)
class CaptionRunConfig:
    video_paths: list[Path]
    model: str
    api_key_env: str = "GEMINI_API_KEY"
    timeout_sec: int = 120
    has_lora: bool = False
    appearance_description: str | None = None
    content_description: str | None = None


@dataclass(slots=True)
class CaptionResult:
    file_name: str
    caption: str
    error: str | None


def build_caption_prompt(
    has_lora: bool,
    appearance_description: str | None = None,
    content_description: str | None = None,
) -> str:
    """Build the Gemini prompt for generating a video caption/generation prompt."""
    lora_instruction = (
        'Start the caption with "sks woman" or "sks girl" as the very first words.'
        if has_lora
        else ""
    )

    if appearance_description:
        # Person-focused prompt: include appearance in output + describe actions
        content_block = f"\nContent style: {content_description}" if content_description else ""

        return f"""
You are a video description expert for AI video generation prompts.

The person we are generating looks like this: {appearance_description}{content_block}

Watch this video and write a generation prompt for recreating it with the person described above.
The caption MUST start with a brief description of the person's appearance (from the description above), then describe what they are doing.

The prompt should be 3-4 sentences:
1. First sentence: describe the person's physical appearance (hair, skin, features) based on the description above
2. Remaining sentences: describe their movements, gestures, body language, facial expressions, and poses from the video
3. Include camera angle and framing

Do NOT focus on background, environment, or clothing. Keep the setting description minimal (1-2 words max).

{lora_instruction}

Output format rules:
- Return ONLY valid JSON
- No markdown, no extra commentary
- Use this exact schema:
{{"caption": "your 3-4 sentence generation prompt here"}}
""".strip()

    # Fallback: original environment-focused prompt when no appearance info available
    return f"""
You are a video description expert for AI video generation prompts.

Watch this video and write a generation prompt that captures its visual essence.
The prompt should be 2-3 sentences describing:
- Scene setting and environment
- Lighting style and color palette
- Camera angle and motion
- Mood and atmosphere
- Activity and body movement
- Overall visual aesthetic

Do NOT describe the subject's specific face or identity — the person will be replaced.
Focus on everything else: pose, clothing style, setting, cinematography.

{lora_instruction}

Output format rules:
- Return ONLY valid JSON
- No markdown, no extra commentary
- Use this exact schema:
{{"caption": "your 2-3 sentence generation prompt here"}}
""".strip()


def run_caption(config: CaptionRunConfig) -> list[CaptionResult]:
    """Generate captions for a batch of videos using Gemini.

    Per-video errors are captured in the result — the batch never aborts.
    """
    api_key = os.getenv(config.api_key_env, "").strip()
    if not api_key:
        return [
            CaptionResult(file_name=p.name, caption="", error=f"missing API key env: {config.api_key_env}")
            for p in config.video_paths
        ]

    prompt = build_caption_prompt(
        has_lora=config.has_lora,
        appearance_description=config.appearance_description,
        content_description=config.content_description,
    )

    total = len(config.video_paths)

    def _caption_one(idx: int, video_path: Path) -> CaptionResult:
        print(f"[caption {idx}/{total}] {video_path.name}", flush=True)
        last_error = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    wait = 2 ** attempt
                    print(f"  [caption {idx}] retry {attempt}/2 after {wait}s...", flush=True)
                    time.sleep(wait)
                payload, _raw = call_gemini(
                    model=config.model,
                    api_key=api_key,
                    video_path=video_path,
                    prompt=prompt,
                    timeout_sec=config.timeout_sec,
                    temperature=0.4,
                )
                caption_text = str(payload.get("caption", "")).strip()
                if not caption_text:
                    raise ValueError("empty caption in model response")
                return CaptionResult(file_name=video_path.name, caption=caption_text, error=None)
            except Exception as exc:
                last_error = exc
        safe_msg = sanitize_error_message(str(last_error), api_key=api_key)
        print(f"  [caption {idx}] error after 3 attempts: {safe_msg}", flush=True)
        return CaptionResult(file_name=video_path.name, caption="", error=safe_msg)

    workers = min(5, total)
    results: list[CaptionResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_caption_one, idx, path): idx
            for idx, path in enumerate(config.video_paths, start=1)
        }
        for future in as_completed(futures):
            results.append(future.result())

    return results
