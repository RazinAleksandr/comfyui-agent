from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from comfy_pipeline.client import ComfyUIClient
from comfy_pipeline.config import WorkflowConfig
from comfy_pipeline.workflow import (
    apply_overrides,
    convert_to_api_format,
    inject_inputs,
    is_api_format,
    load_workflow,
)

FILE_EXTS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".mov", ".avi", ".webm"},
}
ALL_MEDIA_EXTS = FILE_EXTS["image"] | FILE_EXTS["video"]


def _get_video_duration(path: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip()) if result.returncode == 0 else 0.0


def _trim_video(path: Path, max_seconds: float) -> Path:
    """Trim video to max_seconds. Returns path to trimmed file."""
    import sys
    duration = _get_video_duration(path)
    if duration <= 0 or duration <= max_seconds:
        return path

    print(f"  Trimming {path.name}: {duration:.1f}s -> {max_seconds:.1f}s", file=sys.stderr)
    trimmed = Path(tempfile.mktemp(suffix=path.suffix, prefix="trimmed_"))
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-t", str(max_seconds),
            "-c", "copy",
            str(trimmed),
        ],
        capture_output=True, check=True,
    )
    return trimmed


def prepare_workflow(config: WorkflowConfig, client: ComfyUIClient) -> dict:
    """Load workflow file and convert to API format if needed."""
    raw = load_workflow(config.workflow_file)

    if is_api_format(raw):
        return raw

    import sys
    print("Converting workflow from UI to API format...", file=sys.stderr)
    object_info = client.get_object_info()
    return convert_to_api_format(raw, object_info)


def run_single(
    config: WorkflowConfig,
    client: ComfyUIClient,
    api_workflow: dict,
    input_files: dict[str, Path],
    output_dir: Path,
    pair_name: str = "",
    cli_overrides: dict[str, dict] | None = None,
) -> list[Path]:
    """Run a single generation with the given input files."""
    import sys
    label = pair_name or "single"
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"Running: {label}", file=sys.stderr)
    for name, path in input_files.items():
        print(f"  {name}: {path}", file=sys.stderr)
    print(f"{'=' * 50}", file=sys.stderr)

    # Upload files and build injections
    injections = []
    for input_name, file_path in input_files.items():
        if input_name not in config.inputs:
            continue
        # Trim video if it exceeds max_video_seconds
        mapping = config.inputs[input_name]
        if (
            config.max_video_seconds > 0
            and mapping.param.lower() in ("video", "videos")
            and file_path.suffix.lower() in FILE_EXTS["video"]
        ):
            file_path = _trim_video(file_path, config.max_video_seconds)
        print(f"Uploading {input_name}...", file=sys.stderr)
        uploaded_name = client.upload_file(file_path)
        injections.append((mapping.node_id, mapping.param, uploaded_name))

    workflow = inject_inputs(api_workflow, injections)

    # Apply overrides: config-level first, then CLI-level (CLI wins)
    if config.overrides:
        workflow = apply_overrides(workflow, config.overrides)
    if cli_overrides:
        workflow = apply_overrides(workflow, cli_overrides)

    # Queue and wait
    print("Queuing prompt...", file=sys.stderr)
    prompt_id = client.queue_prompt(workflow)
    print(f"Prompt ID: {prompt_id}", file=sys.stderr)

    node_names = {
        nid: node.get("class_type", "") for nid, node in workflow.items()
    }
    print("Waiting for completion...", file=sys.stderr)
    history = client.wait_for_completion(prompt_id, node_names=node_names)

    # Download outputs into: output_dir / <timestamp>_<label> / <node_name> /
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"{timestamp}_{label}"
    outputs = history.get(prompt_id, {}).get("outputs", {})

    downloaded = []
    for out_cfg in config.outputs:
        node_outputs = outputs.get(out_cfg.node_id, {})
        if not node_outputs:
            print(f"  No output from node {out_cfg.node_id} "
                  f"(available: {list(outputs.keys())})", file=sys.stderr)
            continue
        # Per-node subfolder: use config name or fallback to node_id
        node_name = out_cfg.name or f"node_{out_cfg.node_id}"
        node_dir = run_dir / node_name
        # VHS uses "gifs" key for video output (legacy naming)
        for key in ("gifs", "images", "videos", "files"):
            for item in node_outputs.get(key, []):
                path = client.download_output(
                    item["filename"],
                    item.get("subfolder", ""),
                    node_dir,
                )
                downloaded.append(path)
                print(f"  Saved: {path}", file=sys.stderr)

    if not downloaded:
        print("  Warning: no output files found", file=sys.stderr)
        for nid, ndata in outputs.items():
            print(f"  Node {nid} keys: {list(ndata.keys())}", file=sys.stderr)

    return downloaded


def run_batch(
    config: WorkflowConfig,
    client: ComfyUIClient,
    api_workflow: dict,
    batch_dir: Path,
    output_dir: Path,
    cli_overrides: dict[str, dict] | None = None,
) -> dict[str, list[Path]]:
    """Run generation for all input sets in a directory.

    Each subdirectory is one input set. Files are matched to config inputs
    by extension (images → image inputs, videos → video inputs).
    """
    input_sets = find_input_sets(batch_dir, config)
    if not input_sets:
        print(f"No input sets found in {batch_dir}")
        return {}

    print(f"Found {len(input_sets)} input set(s)")
    results = {}

    for set_name, input_files in input_sets.items():
        outputs = run_single(
            config, client, api_workflow,
            input_files,
            output_dir, set_name,
            cli_overrides=cli_overrides,
        )
        results[set_name] = outputs

    return results


def find_input_sets(
    batch_dir: Path, config: WorkflowConfig
) -> dict[str, dict[str, Path]]:
    """Find input file sets in a directory.

    Supports two layouts:
    1. Subdirectories: batch_dir/set_name/{files...}
    2. Flat: matching filenames with different extensions

    Files are assigned to config inputs by matching extension type
    (image extensions → first image input, video extensions → first video input).
    """
    # Build a mapping: extension category → input name
    # Infer category from the param name in config (e.g. "image" → image exts, "video" → video exts)
    input_ext_map: dict[str, set[str]] = {}  # input_name → set of extensions
    for input_name, mapping in config.inputs.items():
        param = mapping.param.lower()
        if param in ("image", "images"):
            input_ext_map[input_name] = FILE_EXTS["image"]
        elif param in ("video", "videos"):
            input_ext_map[input_name] = FILE_EXTS["video"]
        else:
            # Fallback: accept all media files
            input_ext_map[input_name] = ALL_MEDIA_EXTS

    sets: dict[str, dict[str, Path]] = {}

    # Try subdirectory layout first
    for subdir in sorted(batch_dir.iterdir()):
        if not subdir.is_dir():
            continue
        files = list(subdir.iterdir())
        input_files = _match_files_to_inputs(files, input_ext_map)
        if input_files:
            sets[subdir.name] = input_files

    if sets:
        return sets

    # Flat layout: group files by stem, then match extensions to inputs
    by_stem: dict[str, list[Path]] = {}
    for f in batch_dir.iterdir():
        if f.is_file() and f.suffix.lower() in ALL_MEDIA_EXTS:
            by_stem.setdefault(f.stem, []).append(f)

    for stem in sorted(by_stem):
        input_files = _match_files_to_inputs(by_stem[stem], input_ext_map)
        if input_files:
            sets[stem] = input_files

    return sets


def _match_files_to_inputs(
    files: list[Path], input_ext_map: dict[str, set[str]]
) -> dict[str, Path]:
    """Match files to input names by extension."""
    result: dict[str, Path] = {}
    used: set[Path] = set()

    for input_name, exts in input_ext_map.items():
        for f in files:
            if f.suffix.lower() in exts and f not in used:
                result[input_name] = f
                used.add(f)
                break

    return result
