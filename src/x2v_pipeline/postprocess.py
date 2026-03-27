"""Post-processing for LightX2V pipeline output.

Applies SkinContrast enhancement and RIFE frame interpolation to raw
generated video. Runs on the remote GPU as a CLI entry point:
    python -m x2v_pipeline.postprocess --input raw.mp4 --output final.mp4

Steps:
1. Load video frames from input mp4
2. If --skin_model provided: load via spandrel, apply per-frame
3. If --rife_model provided: use LightX2V's RIFEWrapper for frame interpolation
4. Encode output via subprocess ffmpeg call (h264, yuv420p)
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch

logger = logging.getLogger(__name__)


def load_video_frames(video_path: str) -> tuple[np.ndarray, float]:
    """Load video frames and fps using cv2.

    Returns:
        Tuple of (frames array [N, H, W, C] uint8 BGR, fps float).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
    cap.release()

    if not frames:
        raise RuntimeError(f"No frames read from: {video_path}")

    return np.stack(frames), fps


def apply_skin_contrast(
    frames: np.ndarray, model_path: str
) -> np.ndarray:
    """Apply SkinContrast model per-frame using spandrel.

    Args:
        frames: [N, H, W, C] uint8 BGR array.
        model_path: Path to SkinContrast .pth model.

    Returns:
        Enhanced frames as [N, H, W, C] uint8 BGR array.
    """
    import spandrel

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = spandrel.ModelLoader(device=device).load_from_file(model_path)
    model.eval()

    enhanced = []
    total = len(frames)
    for i, frame in enumerate(frames):
        if (i + 1) % 20 == 0 or i == 0:
            logger.info(f"SkinContrast: frame {i + 1}/{total}")

        # BGR uint8 -> RGB float32 [0, 1] -> [1, C, H, W]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        tensor = (
            torch.from_numpy(rgb.astype(np.float32) / 255.0)
            .permute(2, 0, 1)
            .unsqueeze(0)
            .to(device)
        )

        with torch.no_grad():
            output = model(tensor)

        # [1, C, H, W] -> [H, W, C] uint8 BGR
        out_np = (
            output.squeeze(0)
            .clamp(0, 1)
            .permute(1, 2, 0)
            .cpu()
            .numpy()
        )
        out_bgr = cv2.cvtColor(
            (out_np * 255).astype(np.uint8), cv2.COLOR_RGB2BGR
        )
        enhanced.append(out_bgr)

    return np.stack(enhanced)


def apply_rife_interpolation(
    frames: np.ndarray,
    rife_model_dir: str,
    source_fps: float,
    target_fps: float,
) -> np.ndarray:
    """Apply RIFE frame interpolation using LightX2V's RIFEWrapper.

    Args:
        frames: [N, H, W, C] uint8 BGR array.
        rife_model_dir: Directory containing flownet.pkl.
        source_fps: Source frame rate.
        target_fps: Target frame rate.

    Returns:
        Interpolated frames as [N, H, W, C] uint8 BGR array.
    """
    from lightx2v.models.vfi.rife.rife_comfyui_wrapper import RIFEWrapper

    logger.info(
        f"RIFE interpolation: {source_fps}fps -> {target_fps}fps "
        f"({len(frames)} frames)"
    )

    # BGR uint8 -> RGB float32 [0, 1] tensor [N, H, W, C]
    rgb_frames = np.stack(
        [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames]
    )
    tensor = torch.from_numpy(rgb_frames.astype(np.float32) / 255.0)

    rife = RIFEWrapper(rife_model_dir)
    result = rife.interpolate_frames(tensor, source_fps, target_fps)

    logger.info(f"RIFE output: {result.shape[0]} frames")

    # [N, H, W, C] float32 -> BGR uint8
    result_np = (result.clamp(0, 1).numpy() * 255).astype(np.uint8)
    bgr_frames = np.stack(
        [cv2.cvtColor(f, cv2.COLOR_RGB2BGR) for f in result_np]
    )
    return bgr_frames


def encode_video(
    frames: np.ndarray,
    output_path: str,
    fps: float,
    crf: int = 19,
    audio_source: str | None = None,
) -> None:
    """Encode frames to mp4 via ffmpeg subprocess.

    Args:
        frames: [N, H, W, C] uint8 BGR array.
        output_path: Output mp4 path.
        fps: Output frame rate.
        crf: Constant rate factor for h264.
        audio_source: Optional path to source video for audio track.
    """
    h, w = frames.shape[1], frames.shape[2]

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{w}x{h}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "pipe:0",
    ]

    if audio_source:
        cmd.extend(["-i", audio_source, "-map", "0:v", "-map", "1:a?"])

    cmd.extend([
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-preset", "medium",
        tmp_path,
    ])

    logger.info(f"Encoding {len(frames)} frames at {fps}fps to {output_path}")

    proc = subprocess.run(
        cmd,
        input=frames.tobytes(),
        capture_output=True,
    )

    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {stderr}")

    # Move to final path (shutil handles cross-device)
    import shutil
    shutil.move(tmp_path, output_path)
    logger.info(f"Video saved: {output_path}")


def main() -> None:
    """CLI entry point for post-processing."""
    parser = argparse.ArgumentParser(
        description="LightX2V post-processing: SkinContrast + RIFE"
    )
    parser.add_argument(
        "--input", required=True, help="Input video path (raw generation output)"
    )
    parser.add_argument(
        "--output", required=True, help="Output video path"
    )
    parser.add_argument(
        "--skin_model", default=None,
        help="Path to SkinContrast .pth model (skip if not provided)"
    )
    parser.add_argument(
        "--rife_model", default=None,
        help="Path to RIFE model directory containing flownet.pkl "
             "(skip if not provided)"
    )
    parser.add_argument(
        "--source_fps", type=float, default=16.0,
        help="Source video fps (default: 16)"
    )
    parser.add_argument(
        "--output_fps", type=float, default=32.0,
        help="Output video fps after interpolation (default: 32)"
    )
    parser.add_argument(
        "--crf", type=int, default=19,
        help="h264 CRF quality (default: 19)"
    )
    parser.add_argument(
        "--audio", default=None,
        help="Path to source video for audio track extraction"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Load input video
    frames, detected_fps = load_video_frames(args.input)
    logger.info(
        f"Loaded {len(frames)} frames ({frames.shape[1]}x{frames.shape[2]}) "
        f"at {detected_fps}fps"
    )

    current_fps = args.source_fps

    # Step 1: SkinContrast enhancement
    if args.skin_model:
        logger.info(f"Applying SkinContrast from {args.skin_model}")
        frames = apply_skin_contrast(frames, args.skin_model)

    # Step 2: RIFE frame interpolation
    if args.rife_model:
        logger.info(
            f"Applying RIFE interpolation: {current_fps} -> {args.output_fps}fps"
        )
        frames = apply_rife_interpolation(
            frames, args.rife_model, current_fps, args.output_fps
        )
        current_fps = args.output_fps

    # Step 3: Encode output
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    encode_video(frames, args.output, current_fps, args.crf, args.audio)


if __name__ == "__main__":
    main()
