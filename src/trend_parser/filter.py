from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(slots=True)
class Candidate:
    path: Path
    platform: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0


@dataclass(slots=True)
class CandidateFilterConfig:
    download_dir: Path
    report_dir: Path
    filtered_dir: Path
    probe_seconds: int = 12
    top_k: int = 8
    sync_filtered: bool = True
    workers: int = 4


def _run(cmd: list[str], timeout_sec: int = 90) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout_sec}s: {' '.join(cmd[:4])} ...") from exc


def _safe_float(value: str | None, default: float = 0.0) -> float:
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: str | None, default: int = 0) -> int:
    if not value:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_fps(raw: str | None) -> float:
    if not raw:
        return 0.0
    if "/" in raw:
        n, d = raw.split("/", 1)
        den = _safe_float(d, 1.0)
        if den == 0:
            return 0.0
        return _safe_float(n, 0.0) / den
    return _safe_float(raw, 0.0)


def scan_download_dir(download_dir: Path) -> list[Candidate]:
    if not download_dir.exists():
        return []
    patterns = ["*.mp4", "*.mov", "*.mkv", "*.webm"]
    out: list[Candidate] = []
    for pattern in patterns:
        for path in download_dir.rglob(pattern):
            out.append(Candidate(path=path, platform=path.parent.name))
    return sorted(out, key=lambda c: str(c.path))


def _probe_video(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")

    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video_stream:
        raise RuntimeError("no video stream found")

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = _safe_float(str(video_stream.get("duration") or payload.get("format", {}).get("duration") or "0"), 0.0)
    fps = _parse_fps(video_stream.get("avg_frame_rate"))
    bit_rate = _safe_int(str(video_stream.get("bit_rate") or payload.get("format", {}).get("bit_rate") or "0"), 0)
    return {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "bit_rate": bit_rate,
        "has_audio": audio_stream is not None,
    }


def _analyze_motion(path: Path, probe_seconds: int) -> float | None:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-v", "info",
        "-i", str(path), "-t", str(probe_seconds),
        "-vf", "vmafmotion", "-an", "-f", "null", "-",
    ]
    result = _run(cmd)
    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"VMAF Motion avg:\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return _safe_float(match.group(1), 0.0)


def _analyze_scene_cuts(path: Path, probe_seconds: int, threshold: float = 12.0) -> int:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-v", "info",
        "-i", str(path), "-t", str(probe_seconds),
        "-vf", f"scdet=t={threshold}", "-an", "-f", "null", "-",
    ]
    result = _run(cmd)
    text = f"{result.stdout}\n{result.stderr}"
    return len(re.findall(r"lavfi\.scd\.score:\s*[0-9.]+", text))


def _analyze_blur(path: Path, probe_seconds: int) -> float | None:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-v", "info",
        "-i", str(path), "-t", str(probe_seconds),
        "-vf", "blurdetect", "-an", "-f", "null", "-",
    ]
    result = _run(cmd)
    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"blur mean:\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return _safe_float(match.group(1), 0.0)


def _score_candidate(record: dict, max_virality: float) -> dict:
    width = int(record["metrics"]["width"])
    height = int(record["metrics"]["height"])
    duration = float(record["metrics"]["duration_sec"])
    fps = float(record["metrics"]["fps"])
    bit_rate = int(record["metrics"]["bit_rate"])
    has_audio = bool(record["metrics"]["has_audio"])

    motion = record["analysis"].get("motion_avg")
    blur_mean = record["analysis"].get("blur_mean")
    scene_cuts = int(record["analysis"].get("scene_cuts", 0))

    analyzed_duration = max(1.0, float(record["analysis"].get("analyzed_duration_sec", duration)))
    scene_cuts_per_min = scene_cuts / (analyzed_duration / 60.0)
    aspect_ratio = (height / width) if width else 0.0

    reject_reasons: list[str] = []
    if duration < 4.0:
        reject_reasons.append("too_short")
    if width < 480 or height < 480:
        reject_reasons.append("low_resolution")
    if scene_cuts_per_min > 24:
        reject_reasons.append("too_many_scene_cuts")
    if motion is not None and motion > 22:
        reject_reasons.append("too_much_motion")
    if blur_mean is not None and blur_mean > 10:
        reject_reasons.append("too_blurry")

    resolution_score = _clamp01((width * height) / (1080 * 1920))
    bitrate_mbps = bit_rate / 1_000_000 if bit_rate > 0 else 0.0
    bitrate_score = _clamp01(bitrate_mbps / 2.0)
    fps_score = _clamp01(fps / 30.0) if fps >= 15 else _clamp01((fps / 15.0) * 0.5)
    blur_score = 0.55 if blur_mean is None else _clamp01(1.0 - (blur_mean / 12.0))

    quality_score = 0.35 * resolution_score + 0.25 * bitrate_score + 0.20 * fps_score + 0.20 * blur_score
    cut_stability_score = _clamp01(1.0 - (scene_cuts_per_min / 20.0))
    motion_stability_score = 0.6 if motion is None else _clamp01(1.0 - (motion / 18.0))
    temporal_stability = 0.6 * cut_stability_score + 0.4 * motion_stability_score

    duration_score = _clamp01(1.0 - abs(duration - 12.0) / 12.0)
    orientation_score = 1.0 if aspect_ratio >= 1.4 else 0.45
    swap_compatibility = 0.7 * duration_score + 0.3 * orientation_score

    virality_raw = math.log1p(record["views"]) + 0.3 * math.log1p(
        max(0, record["likes"]) + max(0, record["comments"]) + max(0, record["shares"])
    )
    virality_score = _clamp01(virality_raw / max_virality) if max_virality > 0 else 0.0
    if not has_audio:
        quality_score *= 0.9

    final_score = 0.35 * quality_score + 0.25 * temporal_stability + 0.20 * swap_compatibility + 0.20 * virality_score

    return {
        "scores": {
            "quality": round(quality_score, 4),
            "temporal_stability": round(temporal_stability, 4),
            "swap_compatibility": round(swap_compatibility, 4),
            "virality": round(virality_score, 4),
            "final": round(final_score, 4),
        },
        "derived": {
            "aspect_ratio_h_over_w": round(aspect_ratio, 4) if aspect_ratio else 0.0,
            "scene_cuts_per_min": round(scene_cuts_per_min, 3),
            "bitrate_mbps": round(bitrate_mbps, 3),
        },
        "reject_reasons": reject_reasons,
        "hard_reject": len(reject_reasons) > 0,
    }


def _build_report_path(base_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return base_dir / f"candidate_filter_report_{stamp}.json"


def _sync_filtered_folder(top_candidates: list[dict], filtered_dir: Path) -> None:
    filtered_dir.mkdir(parents=True, exist_ok=True)
    sources = [Path(row["path"]) for row in top_candidates]
    same_dir_source = any(src.resolve().parent == filtered_dir.resolve() for src in sources if src.exists())

    if same_dir_source:
        keep_names = {src.name for src in sources if src.exists()}
        for file in filtered_dir.iterdir():
            if (file.is_file() or file.is_symlink()) and file.name not in keep_names:
                file.unlink()
        return

    for file in filtered_dir.iterdir():
        if file.is_file() or file.is_symlink():
            file.unlink()

    for src in sources:
        if src.exists():
            shutil.copy2(src, filtered_dir / src.name)


def _effective_workers(requested_workers: int) -> int:
    cpu = os.cpu_count() or 1
    if requested_workers <= 0:
        return max(1, min(4, cpu))
    return max(1, min(requested_workers, cpu))


def _analyze_candidate(item: Candidate, probe_seconds: int) -> dict:
    started = time.perf_counter()
    record: dict = {
        "path": str(item.path),
        "file_name": item.path.name,
        "platform": item.platform,
        "views": item.views,
        "likes": item.likes,
        "comments": item.comments,
        "shares": item.shares,
        "metrics": {},
        "analysis": {},
        "status": "ok",
        "error": None,
    }

    try:
        t0 = time.perf_counter()
        metrics = _probe_video(item.path)
        probe_video_sec = time.perf_counter() - t0

        analyzed_duration = min(float(metrics["duration_sec"]), float(probe_seconds))
        probe = int(max(3, analyzed_duration))

        t0 = time.perf_counter()
        motion = _analyze_motion(item.path, probe_seconds=probe)
        motion_sec = time.perf_counter() - t0

        t0 = time.perf_counter()
        scene_cuts = _analyze_scene_cuts(item.path, probe_seconds=probe)
        scene_sec = time.perf_counter() - t0

        t0 = time.perf_counter()
        blur = _analyze_blur(item.path, probe_seconds=probe)
        blur_sec = time.perf_counter() - t0

        record["metrics"] = metrics
        record["analysis"] = {
            "analyzed_duration_sec": round(analyzed_duration, 3),
            "motion_avg": None if motion is None else round(motion, 4),
            "scene_cuts": scene_cuts,
            "blur_mean": None if blur is None else round(blur, 4),
            "timings_sec": {
                "probe_video": round(probe_video_sec, 3),
                "motion": round(motion_sec, 3),
                "scene_cut": round(scene_sec, 3),
                "blur": round(blur_sec, 3),
                "total": round(time.perf_counter() - started, 3),
            },
        }
    except Exception as exc:
        record["status"] = "error"
        record["error"] = str(exc)
        record["analysis"] = {
            "timings_sec": {"total": round(time.perf_counter() - started, 3)}
        }
    return record


def run_candidate_filter(config: CandidateFilterConfig) -> tuple[dict, Path]:
    started = time.perf_counter()
    candidates = scan_download_dir(config.download_dir)
    if not candidates:
        raise RuntimeError("no downloaded videos found")

    report_path = _build_report_path(config.report_dir)
    workers = _effective_workers(config.workers)
    print(
        f"[pipeline] candidates={len(candidates)}; workers={workers}; report={report_path}",
        flush=True,
    )

    analyzed: list[dict] = []
    print(
        f"[pipeline] analyzing {len(candidates)} videos (probe_seconds={config.probe_seconds}) ...",
        flush=True,
    )
    future_map = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for item in candidates:
            future = executor.submit(_analyze_candidate, item, config.probe_seconds)
            future_map[future] = item

        done = 0
        total = len(candidates)
        for future in as_completed(future_map):
            item = future_map[future]
            done += 1
            try:
                record = future.result()
            except Exception as exc:
                record = {
                    "path": str(item.path),
                    "file_name": item.path.name,
                    "platform": item.platform,
                    "views": item.views,
                    "likes": item.likes,
                    "comments": item.comments,
                    "shares": item.shares,
                    "metrics": {},
                    "analysis": {"timings_sec": {"total": 0.0}},
                    "status": "error",
                    "error": f"unhandled_worker_error: {exc}",
                }
            analyzed.append(record)
            print(f"[{done}/{total}] {record['file_name']}", flush=True)

    ok_records = [r for r in analyzed if r["status"] == "ok"]
    virality_values = [
        math.log1p(r["views"]) + 0.3 * math.log1p(max(0, r["likes"]) + max(0, r["comments"]) + max(0, r["shares"]))
        for r in ok_records
    ]
    max_virality = max(virality_values) if virality_values else 0.0

    for record in ok_records:
        record.update(_score_candidate(record, max_virality=max_virality))

    accepted = [r for r in ok_records if not r.get("hard_reject")]
    rejected = [r for r in ok_records if r.get("hard_reject")]
    accepted.sort(key=lambda r: r["scores"]["final"], reverse=True)
    rejected.sort(key=lambda r: r["scores"]["final"], reverse=True)

    top_candidates = accepted[: max(1, config.top_k)]
    elapsed = time.perf_counter() - started
    timing_values = [
        float((r.get("analysis") or {}).get("timings_sec", {}).get("total", 0.0))
        for r in analyzed
        if r.get("status") == "ok"
    ]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "probe_seconds": config.probe_seconds,
        "workers": workers,
        "elapsed_sec": round(elapsed, 3),
        "avg_video_sec": round(sum(timing_values) / len(timing_values), 3) if timing_values else 0.0,
        "total_candidates": len(candidates),
        "processed_ok": len(ok_records),
        "errors": len(analyzed) - len(ok_records),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "top_k": config.top_k,
        "top_candidates": top_candidates,
        "rejected_candidates": rejected,
        "errored_candidates": [r for r in analyzed if r["status"] != "ok"],
        "all_candidates": analyzed,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    if config.sync_filtered:
        _sync_filtered_folder(top_candidates=top_candidates, filtered_dir=config.filtered_dir)

    return report, report_path
