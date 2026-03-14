from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trend_parser.gemini import (
    call_gemini,
    clamp,
    mock_summary,
    safe_float,
    sanitize_error_message,
    sanitize_stem,
)
from trend_parser.persona import PersonaProfile


@dataclass(slots=True)
class SelectorThresholds:
    min_readiness: float = 7.0
    min_confidence: float = 0.70
    min_persona_fit: float = 6.5
    max_occlusion_risk: float = 6.0
    max_scene_cut_complexity: float = 6.0


@dataclass(slots=True)
class SelectorRunConfig:
    input_dir: Path
    output_dir: Path
    selected_dir: Path
    rejected_dir: Path
    theme: str
    hashtags: list[str]
    model: str
    api_key_env: str
    timeout_sec: int
    mock: bool
    max_videos: int
    sync_folders: bool
    thresholds: SelectorThresholds
    persona: PersonaProfile | None = None
    video_suggestions_requirement: str | None = None


@dataclass(slots=True)
class VlmDecision:
    file_name: str
    source_path: str
    decision: str
    confidence: float
    substitution_readiness: float
    persona_fit: float
    reasons: list[str]
    output_json_path: str


def build_prompt(
    theme: str,
    hashtags: list[str],
    persona: PersonaProfile | None,
    video_suggestions_requirement: str | None = None,
) -> str:
    hashtags_text = ", ".join(hashtags) if hashtags else "(none provided)"
    persona_block = persona.to_prompt_block() if persona else "Persona: generic healthy lifestyle creator"
    negative_block = (video_suggestions_requirement or "").strip() or "(none provided)"

    return f"""
You are a strict video suitability judge for AI subject substitution (face/person replacement).

Channel theme: {theme}
Target hashtags: {hashtags_text}

Persona profile:
{persona_block}

Additional rejection requirements from onboarding:
{negative_block}

Task:
Evaluate whether this video is suitable for replacing the main person with this specific persona.
When scoring, focus on whether replacement can look natural while preserving context, camera style, and motion.

Scoring rubric (0-10, except risks where higher is worse):
- theme_match
- persona_fit
- single_subject_clarity
- face_visibility
- motion_stability
- occlusion_risk (higher means worse)
- scene_cut_complexity (higher means worse)
- substitution_readiness

Hard rejection guidance:
- Multiple dominant people in most of the clip
- Main person is small/unclear/rarely visible
- Heavy occlusion
- Extremely rapid cuts that hurt stable replacement
- Strong mismatch with persona visual profile/style in most of the clip

Output format rules:
- Return ONLY valid JSON
- No markdown, no extra commentary
- Use this exact top-level schema:
{{
  "summary": "short summary",
  "scores": {{
    "theme_match": 0,
    "persona_fit": 0,
    "single_subject_clarity": 0,
    "face_visibility": 0,
    "motion_stability": 0,
    "occlusion_risk": 0,
    "scene_cut_complexity": 0,
    "substitution_readiness": 0
  }},
  "decision": "accept|reject|maybe",
  "confidence": 0.0,
  "reasons": ["reason1", "reason2"],
  "best_segments": [
    {{"start_sec": 0.0, "end_sec": 0.0, "why": "reason"}}
  ]
}}
""".strip()


def auto_decide(
    payload: dict,
    thresholds: SelectorThresholds,
) -> tuple[str, list[str]]:
    scores = payload.get("scores") or {}
    readiness = safe_float(scores.get("substitution_readiness"), 0.0)
    persona_fit = safe_float(scores.get("persona_fit"), 0.0)
    confidence = safe_float(payload.get("confidence"), 0.0)
    occlusion_risk = safe_float(scores.get("occlusion_risk"), 10.0)
    cut_complexity = safe_float(scores.get("scene_cut_complexity"), 10.0)
    model_decision = str(payload.get("decision") or "").strip().lower()

    reasons: list[str] = []
    if readiness < thresholds.min_readiness:
        reasons.append("low_substitution_readiness")
    if confidence < thresholds.min_confidence:
        reasons.append("low_model_confidence")
    if persona_fit < thresholds.min_persona_fit:
        reasons.append("low_persona_fit")
    if occlusion_risk > thresholds.max_occlusion_risk:
        reasons.append("high_occlusion_risk")
    if cut_complexity > thresholds.max_scene_cut_complexity:
        reasons.append("high_scene_cut_complexity")
    if model_decision == "reject":
        reasons.append("model_rejected")

    if reasons:
        return "reject", reasons
    return "accept", []


def find_video_files(input_dir: Path, max_videos: int) -> list[Path]:
    video_files: list[Path] = []
    for pattern in ["*.mp4", "*.mov", "*.mkv", "*.webm"]:
        video_files.extend(input_dir.rglob(pattern))
    unique = sorted({p.resolve() for p in video_files if p.is_file()})
    return unique[:max_videos]


def copy_results(decisions: list[VlmDecision], selected_dir: Path, rejected_dir: Path) -> None:
    selected_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    for folder in [selected_dir, rejected_dir]:
        for file in folder.iterdir():
            if file.is_file() or file.is_symlink():
                file.unlink()

    for row in decisions:
        src = Path(row.source_path)
        dst_dir = selected_dir if row.decision == "accept" else rejected_dir
        shutil.copy2(src, dst_dir / src.name)


def run_selector(config: SelectorRunConfig) -> int:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    files = find_video_files(config.input_dir, max_videos=config.max_videos)
    if not files:
        print(f"[vlm] no video files in {config.input_dir}")
        return 1

    prompt = build_prompt(
        theme=config.theme,
        hashtags=config.hashtags,
        persona=config.persona,
        video_suggestions_requirement=config.video_suggestions_requirement,
    )

    api_key = os.getenv(config.api_key_env, "").strip()
    if not config.mock and not api_key:
        print(f"[vlm] missing API key env: {config.api_key_env}")
        return 2

    print(
        f"[vlm] files={len(files)} model={config.model} mock={config.mock} output_dir={config.output_dir}",
        flush=True,
    )
    decisions: list[VlmDecision] = []

    for idx, video_path in enumerate(files, start=1):
        print(f"[{idx}/{len(files)}] {video_path.name}", flush=True)
        try:
            if config.mock:
                payload, raw_text = mock_summary(video_path)
            else:
                payload, raw_text = call_gemini(
                    model=config.model,
                    api_key=api_key,
                    video_path=video_path,
                    prompt=prompt,
                    timeout_sec=config.timeout_sec,
                )

            auto_decision, auto_reasons = auto_decide(payload=payload, thresholds=config.thresholds)
            scores = payload.get("scores") or {}
            confidence = clamp(safe_float(payload.get("confidence"), 0.0), 0.0, 1.0)
            readiness = clamp(safe_float(scores.get("substitution_readiness"), 0.0), 0.0, 10.0)
            persona_fit_val = clamp(safe_float(scores.get("persona_fit"), 0.0), 0.0, 10.0)
            model_reasons = [str(x) for x in (payload.get("reasons") or []) if str(x).strip()]
            reasons = list(dict.fromkeys([*auto_reasons, *model_reasons]))

            safe_stem = sanitize_stem(video_path)
            out_path = config.output_dir / f"{safe_stem}.json"
            result_payload = {
                "video_path": str(video_path),
                "file_name": video_path.name,
                "model": config.model,
                "generated_at": datetime.now(UTC).isoformat(),
                "persona": config.persona.to_dict() if config.persona else None,
                "auto_decision": auto_decision,
                "thresholds": {
                    "min_readiness": config.thresholds.min_readiness,
                    "min_confidence": config.thresholds.min_confidence,
                    "min_persona_fit": config.thresholds.min_persona_fit,
                    "max_occlusion_risk": config.thresholds.max_occlusion_risk,
                    "max_scene_cut_complexity": config.thresholds.max_scene_cut_complexity,
                },
                "model_output": payload,
                "model_raw_text": raw_text,
                "reasons": reasons,
            }
            out_path.write_text(json.dumps(result_payload, ensure_ascii=True, indent=2), encoding="utf-8")

            decisions.append(
                VlmDecision(
                    file_name=video_path.name,
                    source_path=str(video_path),
                    decision=auto_decision,
                    confidence=confidence,
                    substitution_readiness=readiness,
                    persona_fit=persona_fit_val,
                    reasons=reasons,
                    output_json_path=str(out_path),
                )
            )
        except Exception as exc:
            safe_stem = sanitize_stem(video_path)
            out_path = config.output_dir / f"{safe_stem}.json"
            err_payload = {
                "video_path": str(video_path),
                "file_name": video_path.name,
                "model": config.model,
                "generated_at": datetime.now(UTC).isoformat(),
                "persona": config.persona.to_dict() if config.persona else None,
                "auto_decision": "error",
                "error": sanitize_error_message(str(exc), api_key=api_key),
            }
            out_path.write_text(json.dumps(err_payload, ensure_ascii=True, indent=2), encoding="utf-8")
            decisions.append(
                VlmDecision(
                    file_name=video_path.name,
                    source_path=str(video_path),
                    decision="reject",
                    confidence=0.0,
                    substitution_readiness=0.0,
                    persona_fit=0.0,
                    reasons=["inference_error"],
                    output_json_path=str(out_path),
                )
            )

    if config.sync_folders:
        copy_results(decisions=decisions, selected_dir=config.selected_dir, rejected_dir=config.rejected_dir)

    accepted = [d for d in decisions if d.decision == "accept"]
    rejected_decisions = [d for d in decisions if d.decision != "accept"]
    accepted_sorted = sorted(
        accepted,
        key=lambda x: (x.substitution_readiness, x.persona_fit, x.confidence),
        reverse=True,
    )

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": config.model,
        "mock": config.mock,
        "input_dir": str(config.input_dir),
        "output_dir": str(config.output_dir),
        "sync_folders": config.sync_folders,
        "selected_dir": str(config.selected_dir),
        "rejected_dir": str(config.rejected_dir),
        "persona": config.persona.to_dict() if config.persona else None,
        "total": len(decisions),
        "accepted": len(accepted),
        "rejected": len(rejected_decisions),
        "accepted_top": [
            {
                "file_name": row.file_name,
                "readiness": row.substitution_readiness,
                "persona_fit": row.persona_fit,
                "confidence": row.confidence,
                "reasons": row.reasons,
                "json_path": row.output_json_path,
            }
            for row in accepted_sorted[:10]
        ],
    }

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    summary_path = config.output_dir / f"vlm_summary_{stamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")

    print("\n=== VLM SUMMARY ===")
    print(
        f"accepted={summary['accepted']} rejected={summary['rejected']} total={summary['total']} "
        f"mock={config.mock}"
    )
    for idx, row in enumerate(summary["accepted_top"], start=1):
        print(
            f"{idx:02d}. readiness={row['readiness']:.2f} persona_fit={row['persona_fit']:.2f} "
            f"confidence={row['confidence']:.2f} file={row['file_name']}"
        )
    print(f"summary: {summary_path.resolve()}")
    return 0
