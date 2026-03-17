from __future__ import annotations

import os
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


@dataclass(slots=True)
class CaptionResult:
    file_name: str
    caption: str
    error: str | None


def build_caption_prompt(has_lora: bool) -> str:
    """Build the Gemini prompt for generating a video caption/generation prompt."""
    lora_instruction = (
        'Start the caption with "sks woman" or "sks girl" as the very first words.'
        if has_lora
        else ""
    )

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

    prompt = build_caption_prompt(has_lora=config.has_lora)
    results: list[CaptionResult] = []

    for idx, video_path in enumerate(config.video_paths, start=1):
        print(f"[caption {idx}/{len(config.video_paths)}] {video_path.name}", flush=True)
        try:
            payload, _raw = call_gemini(
                model=config.model,
                api_key=api_key,
                video_path=video_path,
                prompt=prompt,
                timeout_sec=config.timeout_sec,
                temperature=0.4,
            )
            # call_gemini returns the parsed JSON dict and raw text
            caption_text = str(payload.get("caption", "")).strip()
            if not caption_text:
                raise ValueError("empty caption in model response")
            results.append(CaptionResult(file_name=video_path.name, caption=caption_text, error=None))
        except Exception as exc:
            safe_msg = sanitize_error_message(str(exc), api_key=api_key)
            print(f"  [caption] error: {safe_msg}", flush=True)
            results.append(CaptionResult(file_name=video_path.name, caption="", error=safe_msg))

    return results
