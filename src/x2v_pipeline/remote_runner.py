"""Remote runner for LightX2V pipeline.

Generates bash scripts for remote GPU execution, parses progress output,
and collects output file paths.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from x2v_pipeline.config import X2VConfig


# Progress parsing patterns
_PHASE_RE = re.compile(r"^PHASE:(\S+)")
_STEP_RE = re.compile(r"==> step_index:\s*(\d+)\s*/\s*(\d+)")
_SAVE_START_RE = re.compile(r"Start to save video")
_SAVE_DONE_RE = re.compile(r"Video saved successfully")
_OUTPUT_RE = re.compile(r"^OUTPUT_(RAW|FINAL|ISP):(.+)$")


def _infer_launch_cmd(inference_config: dict) -> str:
    """Return the launch prefix for LightX2V inference.

    Uses torchrun for multi-GPU when parallel config is present,
    otherwise plain python3.
    """
    parallel = inference_config.get("parallel")
    if parallel:
        nproc = max(
            parallel.get("seq_p_size", 1) * parallel.get("cfg_p_size", 1),
            parallel.get("tensor_p_size", 1),
        )
        if nproc > 1:
            return f"torchrun --nproc_per_node={nproc} -m lightx2v.infer"
    return "python3 -m lightx2v.infer"


def build_remote_command(
    config: X2VConfig,
    inputs: dict[str, str],
    run_dir: str,
    model_path: str,
    project_src: str = "",
) -> str:
    """Generate a bash script string for remote LightX2V execution.

    Args:
        config: Pipeline configuration.
        inputs: Dict with keys: video_path, refer_path, prompt, negative_prompt.
        run_dir: Remote working directory for this generation run.
        model_path: Remote path to model weights directory.
        project_src: Remote path to project src/ dir (for x2v_pipeline imports).

    Returns:
        Complete bash script as a string.
    """
    repo_path = config.repo_path
    preprocess = config.preprocessing
    postprocess_cfg = config.postprocess
    outputs_cfg = config.outputs

    resolution = preprocess.get("resolution_area", [1280, 720])
    retarget_flag = preprocess.get("retarget_flag", True)

    # Build inference config JSON (include character LoRAs if character_id provided)
    character_id = inputs.get("character_id")
    inference_config = config.build_inference_config(
        overrides={"lora_configs": config.build_lora_configs(character_id)}
    )
    # Make LoRA paths absolute
    for lora in inference_config.get("lora_configs", []):
        if not lora["path"].startswith("/"):
            lora["path"] = f"{model_path}/{lora['path']}"

    config_json = json.dumps(inference_config, indent=2)

    # Prompt/negative prompt — shell-escaped for safe embedding
    prompt = shlex.quote(
        inputs.get("prompt", config.parameters.get("prompt", ""))
    )
    negative_prompt = shlex.quote(
        inputs.get("negative_prompt", config.parameters.get("negative_prompt", ""))
    )
    video_path = inputs["video_path"]
    refer_path = inputs["refer_path"]

    # Output filenames
    raw_output = f"{run_dir}/{outputs_cfg.get('raw', 'output_raw.mp4')}"
    final_output = f"{run_dir}/{outputs_cfg.get('final', 'output_final.mp4')}"
    isp_output = f"{run_dir}/{outputs_cfg.get('isp', 'output_isp.mp4')}"

    # Preprocess dir
    preprocess_dir = f"{run_dir}/preprocess"

    # Build retarget flag
    retarget_arg = "--retarget_flag" if retarget_flag else ""

    # Postprocess args
    postprocess_args: list[str] = []
    if postprocess_cfg.get("skin_contrast", False):
        skin_path = f"{model_path}/{config.get_model_path('skin_contrast')}"
        postprocess_args.append(f"--skin_model {skin_path}")

    if postprocess_cfg.get("rife_multiplier", 0) > 1:
        rife_path = f"{model_path}/{config.get_model_path('rife')}"
        postprocess_args.append(f"--rife_model {rife_path}")

    source_fps = config.inference.get("fps", 16)
    output_fps = postprocess_cfg.get("output_fps", 32)
    crf = postprocess_cfg.get("crf", 19)

    # Build the script
    lines = [
        "#!/bin/bash",
        "set -e",
        "",
        f"export PYTHONPATH={repo_path}:{project_src}:$PYTHONPATH" if project_src
        else f"export PYTHONPATH={repo_path}:$PYTHONPATH",
        "",
        "# Write inference config",
        f"mkdir -p {run_dir}",
        f"cat > {run_dir}/animate_config.json << 'CONFIGEOF'",
        config_json,
        "CONFIGEOF",
        "",
        'echo "PHASE:preprocess"',
        f"cd {repo_path}",
        f"python3 tools/preprocess/preprocess_data.py \\",
        f"    --ckpt_path {model_path}/process_checkpoint \\",
        f"    --video_path {video_path} \\",
        f"    --refer_path {refer_path} \\",
        f"    --save_path {preprocess_dir} \\",
        f"    --resolution_area {resolution[0]} {resolution[1]} \\",
        f"    {retarget_arg}",
        "",
        'echo "PHASE:inference"',
        f"{_infer_launch_cmd(inference_config)} \\",
        f"    --model_cls wan2.2_animate --task animate \\",
        f"    --model_path {model_path} \\",
        f"    --config_json {run_dir}/animate_config.json \\",
        f"    --src_pose_path {preprocess_dir}/src_pose.mp4 \\",
        f"    --src_face_path {preprocess_dir}/src_face.mp4 \\",
        f"    --src_ref_images {preprocess_dir}/src_ref.png \\",
        f"    --image_path {preprocess_dir}/src_ref.png \\",
        f"    --prompt {prompt} \\",
        f"    --negative_prompt {negative_prompt} \\",
        f"    --save_result_path {raw_output}",
    ]

    # Optional refinement phases
    if config.is_refinement_enabled():
        refinement = config.refinement
        # Refinement pass 1
        lines.extend([
            "",
            'echo "PHASE:refinement_1"',
            f"# Refinement pass 1 (denoise={refinement['pass1']['denoise_strength']})",
            "# Refinement is deferred — not yet implemented in LightX2V",
            "# This is a placeholder for when wan_refine_runner is added",
        ])
        # Refinement pass 2
        lines.extend([
            "",
            'echo "PHASE:refinement_2"',
            f"# Refinement pass 2 (denoise={refinement['pass2']['denoise_strength']})",
            "# Refinement is deferred — not yet implemented in LightX2V",
        ])

    # Determine postprocess input (refined output if refinement, else raw)
    postprocess_input = raw_output

    # Build postprocess command with optional args
    pp_cmd_parts = [
        "python3 -m x2v_pipeline.postprocess",
        f"--input {postprocess_input}",
    ]
    pp_cmd_parts.extend(postprocess_args)
    pp_cmd_parts.extend([
        f"--source_fps {source_fps}",
        f"--output_fps {output_fps}",
        f"--crf {crf}",
    ])
    if video_path:
        pp_cmd_parts.append(f"--audio {video_path}")
    pp_cmd_parts.append(f"--output {final_output}")

    # Format as multi-line command with continuations
    pp_lines = [pp_cmd_parts[0] + " \\"]
    for part in pp_cmd_parts[1:-1]:
        pp_lines.append(f"    {part} \\")
    pp_lines.append(f"    {pp_cmd_parts[-1]}")

    lines.extend([
        "",
        'echo "PHASE:postprocess"',
    ])
    lines.extend(pp_lines)
    # Optional ISP phase (grain, sharpness, brightness, vignette)
    isp_cfg = config.isp
    if isp_cfg.get("enabled", False):
        isp_cmd_parts = [
            "python3 -m isp_pipeline.processor",
            f"--input {final_output}",
            f"--graininess {isp_cfg.get('graininess', 40)}",
            f"--sharpness {isp_cfg.get('sharpness', 20)}",
            f"--brightness {isp_cfg.get('brightness', -5)}",
            f"--vignette {isp_cfg.get('vignette', 10)}",
            f"--output {isp_output}",
        ]
        isp_lines = [isp_cmd_parts[0] + " \\"]
        for part in isp_cmd_parts[1:-1]:
            isp_lines.append(f"    {part} \\")
        isp_lines.append(f"    {isp_cmd_parts[-1]}")

        lines.extend([
            "",
            'echo "PHASE:isp"',
        ])
        lines.extend(isp_lines)

    lines.extend([
        "",
        'echo "PHASE:complete"',
        f'echo "OUTPUT_RAW:{raw_output}"',
        f'echo "OUTPUT_FINAL:{final_output}"',
    ])
    if isp_cfg.get("enabled", False):
        lines.append(f'echo "OUTPUT_ISP:{isp_output}"')

    return "\n".join(lines)


def parse_x2v_progress(line: str) -> dict[str, Any] | None:
    """Parse a single stdout line from LightX2V execution.

    Returns a dict describing the progress event, or None if the line
    is not a recognized progress pattern.

    Recognized patterns:
        PHASE:xxx             -> {"phase": "xxx"}
        ==> step_index: X / Y -> {"stage": "inference", "current": X, "total": Y}
        Start to save video   -> {"stage": "saving"}
        Video saved success    -> {"stage": "saved"}
        OUTPUT_RAW:path       -> {"output": "raw", "path": path}
        OUTPUT_FINAL:path     -> {"output": "final", "path": path}
    """
    line = line.strip()

    match = _PHASE_RE.match(line)
    if match:
        return {"phase": match.group(1)}

    match = _STEP_RE.search(line)
    if match:
        return {
            "stage": "inference",
            "current": int(match.group(1)),
            "total": int(match.group(2)),
        }

    if _SAVE_START_RE.search(line):
        return {"stage": "saving"}

    if _SAVE_DONE_RE.search(line):
        return {"stage": "saved"}

    match = _OUTPUT_RE.match(line)
    if match:
        kind = match.group(1).lower()
        return {"output": kind, "path": match.group(2).strip()}

    return None


def collect_outputs(run_dir: str, config: X2VConfig) -> dict[str, str]:
    """Map output names to file paths from config.outputs.

    Args:
        run_dir: Remote run directory.
        config: Pipeline configuration.

    Returns:
        Dict mapping output name to full path, e.g.
        {"raw": "/path/output_raw.mp4", "final": "/path/output_final.mp4"}
    """
    result: dict[str, str] = {}
    for name, filename in config.outputs.items():
        result[name] = f"{run_dir}/{filename}"
    return result
