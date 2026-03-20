from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import requests

MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


def sanitize_error_message(message: str, api_key: str | None = None) -> str:
    safe = str(message or "")
    safe = re.sub(r"([?&]key=)[^&\s]+", r"\1REDACTED", safe)
    if api_key:
        safe = safe.replace(api_key, "REDACTED")
    return safe


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sanitize_stem(path: Path) -> str:
    base = path.stem[:80]
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._-")
    if not clean:
        clean = "video"
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    return f"{clean}_{digest}"


def extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in model response")

    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("model response JSON is not an object")
    return payload


def call_gemini(
    *,
    model: str,
    api_key: str,
    video_path: Path,
    prompt: str,
    timeout_sec: int,
    temperature: float = 0.1,
) -> tuple[dict, str]:
    mime_type = MIME_BY_SUFFIX.get(video_path.suffix.lower())
    if not mime_type:
        raise RuntimeError(f"unsupported video extension: {video_path.suffix}")

    binary = video_path.read_bytes()
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
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(binary).decode("ascii"),
                        }
                    },
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


IMAGE_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def call_gemini_image(
    *,
    model: str,
    api_key: str,
    image_path: Path,
    prompt: str,
    timeout_sec: int = 60,
    temperature: float = 0.2,
) -> str:
    """Call Gemini with an image and return the raw text response."""
    mime_type = IMAGE_MIME_BY_SUFFIX.get(image_path.suffix.lower())
    if not mime_type:
        raise RuntimeError(f"unsupported image extension: {image_path.suffix}")

    binary = image_path.read_bytes()
    payload = {
        "generationConfig": {"temperature": temperature},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(binary).decode("ascii"),
                        }
                    },
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

    return model_text


def mock_summary(video_path: Path) -> tuple[dict, str]:
    seed = int(hashlib.sha1(str(video_path).encode("utf-8")).hexdigest()[:8], 16)
    readiness = 5.5 + ((seed % 420) / 100.0)
    confidence = 0.55 + ((seed % 35) / 100.0)
    occlusion_risk = 2 + (seed % 5)
    cut_complexity = 2 + ((seed // 7) % 6)
    persona_fit = 5.0 + ((seed // 17) % 45) / 10.0
    decision = "accept" if readiness >= 7.2 and confidence >= 0.70 and occlusion_risk <= 5 else "maybe"
    payload = {
        "summary": "Mock summary for local pipeline test.",
        "scores": {
            "theme_match": round(6.5 + ((seed // 11) % 30) / 10.0, 2),
            "persona_fit": round(persona_fit, 2),
            "single_subject_clarity": round(6.0 + ((seed // 13) % 35) / 10.0, 2),
            "face_visibility": round(6.0 + ((seed // 17) % 35) / 10.0, 2),
            "motion_stability": round(5.5 + ((seed // 19) % 35) / 10.0, 2),
            "occlusion_risk": float(occlusion_risk),
            "scene_cut_complexity": float(cut_complexity),
            "substitution_readiness": round(readiness, 2),
        },
        "decision": decision,
        "confidence": round(confidence, 3),
        "reasons": ["mock_mode_enabled"],
        "best_segments": [{"start_sec": 0.0, "end_sec": 6.0, "why": "mock best segment"}],
    }
    return payload, json.dumps(payload)
