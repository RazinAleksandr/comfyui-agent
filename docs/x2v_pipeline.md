# LightX2V Pipeline

Direct inference pipeline for WAN 2.2 Animate 14B, replacing ComfyUI with [LightX2V](https://github.com/ModelTC/LightX2V) for lower overhead and finer control over memory management.

```
src/x2v_pipeline/
  config.py         X2VConfig dataclass — loads configs/x2v_animate.yaml
  install.py        Remote bootstrap: clone LightX2V, install deps, download models, patch source
  remote_runner.py  Generates bash scripts for remote GPU execution + parses progress
  postprocess.py    CLI entry point for SkinContrast + RIFE + ffmpeg (runs on GPU server)

configs/x2v_animate.yaml   Central config for the full pipeline
```

Orchestrated by `VastAgentService.run_x2v()` in `src/vast_agent/service.py`.

## Architecture Comparison: ComfyUI vs LightX2V

ComfyUI runs a persistent HTTP server, uploads inputs via REST, and tracks progress over WebSocket. LightX2V runs as a single Python invocation — no server process, no API overhead.

| | ComfyUI | LightX2V |
|---|---|---|
| Execution | Persistent server + REST/WS | One-shot `python -m lightx2v.infer` |
| Memory mgmt | Automatic model LRU cache | Explicit: `cpu_offload`, `unload_modules` |
| Workflow | JSON DAG of nodes | YAML config → JSON inference config |
| Progress | WebSocket events | stdout parsing (`==> step_index: X / Y`) |
| Postprocess | In-workflow (RIFE VFI node, etc.) | Separate CLI (`python -m x2v_pipeline.postprocess`) |

## Pipeline Stages

```
1. Preprocess    SAM2 + ViTPose → src_pose.mp4, src_face.mp4, src_ref.png
2. Inference     LightX2V WAN 2.2 Animate with LoRAs → output_raw.mp4
3. Postprocess   SkinContrast upscale + RIFE 2× interpolation → output_final.mp4
4. ISP           Grain, sharpness, brightness, vignette → output_isp.mp4
```

Each stage emits `PHASE:<name>` to stdout, parsed by `parse_x2v_progress()` in `remote_runner.py`.

## Config Structure (configs/x2v_animate.yaml)

### Models

All models download to `/workspace/models` on the remote server. Download is idempotent — skips if file exists and meets `min_size`.

| Model | Type | Size | Purpose |
|-------|------|------|---------|
| `wan_model` | HF repo | ~30 GB | Full Wan2.2-Animate-14B (transformer + T5 + CLIP + VAE + process_checkpoint) |
| `skin_contrast` | URL | ~50 KB | 1× SkinContrast upscale model |
| `rife` | zip | ~30 MB | RIFE v4.26 frame interpolation (flownet.pkl) |
| `relight_lora` | URL | ~600 MB | Relight LoRA (always active, strength 1.0) |
| `refine_model` | URL | ~29 GB | T2V refinement model (optional, disabled by default) |
| Character LoRAs | URL | ~600 MB each | Per-character high/low noise LoRA pairs |

### Inference Settings

```yaml
inference:
  infer_steps: 20          # Diffusion steps per segment
  sample_guide_scale: 5.0  # CFG scale (set to 5.0 when enable_cfg: false → ignored)
  sample_shift: 5.0        # Noise schedule shift
  target_video_length: 77  # Frames per segment (76 new + 1 overlap)
  target_height: 720
  target_width: 1280
  fps: 16                  # Generation FPS (doubled to 32 by RIFE in postprocess)

  # Attention backends
  self_attn_1_type: flash_attn2    # SM89+ (Ada Lovelace / L40)
  cross_attn_1_type: flash_attn2
  cross_attn_2_type: flash_attn2
  adapter_attn_type: torch_sdpa    # Animate adapter attention

  # Memory management
  cpu_offload: false       # Block-level CPU↔GPU streaming (see Memory section)
  unload_modules: true     # Load/unload T5, CLIP, VAE around usage
  rope_type: torch         # RoPE implementation (torch fallback for non-Hopper GPUs)
```

### Characters

Per-character LoRA presets. Applied automatically based on `character_id` in generation request.

```yaml
characters:
  altf4girl:
    loras:
      - path: loras/altf4_high_noise.safetensors
        strength: 0.89
      - path: loras/altf4_low_noise.safetensors
        strength: 0.89
```

## Memory Management

The 14B transformer in bf16 is ~28 GB. How it fits on different GPUs:

### Mode 1: `cpu_offload: false` (current default)

Full transformer stays on GPU. Requires ~43 GB VRAM.

| Component | VRAM |
|-----------|------|
| Transformer (14B bf16) | ~28 GB |
| T5-XXL / CLIP (if loaded) | ~10 GB |
| Activations + KV cache | ~5 GB |
| **Total** | **~43 GB** |

With `unload_modules: true`, T5/CLIP/VAE are loaded→used→freed before inference, so peak during inference is ~33 GB.

**Works on**: L40/L40S (48 GB), A100 (40/80 GB)
**Does NOT fit on**: RTX 5090/4090 (32/24 GB)

### Mode 2: `cpu_offload: true, offload_granularity: block`

Only 2 transformer blocks stay on GPU at a time (double-buffered). Rest stays in CPU RAM.

| Component | VRAM |
|-----------|------|
| 2 CUDA buffer blocks | ~1.4 GB |
| 2 adapter buffers | ~0.2 GB |
| Pre-weights (embeddings) | ~0.2 GB |
| Non-block weights (norm, head) | ~0.1 GB |
| Scheduler latents | ~0.2 GB |
| Activations (per-block) | ~2-4 GB |
| Motion + face encoder | ~0.2 GB |
| **Peak during inference** | **~5-7 GB** |

Requires ~28 GB system RAM for CPU-side model weights.

Transfer overhead per step: 40 blocks × ~700 MB / 64 GB/s PCIe 5.0 ≈ 0.4s (overlapped with compute via async streams).

**Works on**: Any GPU with ≥16 GB VRAM + ≥32 GB system RAM.

### Mode 3: `unload_modules: true` (orthogonal to cpu_offload)

Controls auxiliary models (not the transformer). When true:
- T5-XXL text encoder: loaded → encode text → deleted
- CLIP vision encoder: loaded → encode image → deleted
- VAE encoder: loaded → encode → deleted (requires source patch, see below)
- VAE decoder: loaded → decode → deleted

Can combine with either cpu_offload mode.

## Segment-Based Generation

LightX2V processes long videos in overlapping segments:

```
segments = 1 + ceil((total_frames - target_video_length) / (target_video_length - refert_num))
```

| Input duration (30fps) | Frames | Segments | Est. time (L40, 20 steps) |
|------------------------|--------|----------|---------------------------|
| 2.5s | 77 | 1 | ~18 min |
| 5s | 150 | 2 | ~36 min |
| 10s | 300 | 4 | ~72 min |
| 27s | 803 | 11 | ~3.3 hrs |

**Important**: LightX2V does NOT truncate input video. Preprocessing uses all frames. Keep driving videos short (≤5s) to avoid excessive generation time.

## GPU Compatibility

| GPU | VRAM | cpu_offload needed | Attention backend | RoPE | Notes |
|-----|------|-------------------|-------------------|------|-------|
| RTX 5090 | 32 GB | **Yes** | flash_attn3 | flashinfer or torch | Blackwell SM100, fastest per-step |
| L40/L40S | 48 GB | No | flash_attn2 | torch | Ada Lovelace SM89, enough VRAM for full model |
| A100 | 40/80 GB | No (40GB) / No | flash_attn2 | torch or flashinfer | Ampere SM80 |
| RTX 4090 | 24 GB | **Yes** | flash_attn2 | torch | Ada Lovelace SM89, tight but works |

### Attention Backend Requirements

- `flash_attn3`: Requires SM90+ (Hopper H100/H200) or SM100+ (Blackwell RTX 5090). Install from `flash-attn` package.
- `flash_attn2`: Works on SM80+ (Ampere and later). Most compatible option.
- `torch_sdpa`: Fallback, works everywhere. Used for `adapter_attn_type` because the Animate adapter's varlen attention path was only tested with flash_attn3.

### RoPE Backend Requirements

- `flashinfer`: Fastest. Provided by `sgl-kernel`. Requires matching SM architecture at install time (SM100 for Blackwell, SM89 for Ada). Falls back silently to None if wrong SM — causes runtime crash.
- `torch`: Pure PyTorch fallback. Works everywhere. ~5% slower.

## Source Patches

LightX2V requires patches applied after clone. These are generated by `build_patch_commands()` in `install.py`:

### VAE Encoder Load/Unload Guards

`WanAnimateRunner.run_vae_encoder()` is missing the load/unload guards present in `run_vae_decoder` and `run_image_encoder`. Without this patch, `unload_modules: true` + `cpu_offload: false` crashes because the VAE encoder is never loaded.

Patch adds:
```python
# Before encoding
if self.config.get("lazy_load", False) or self.config.get("unload_modules", False):
    self.vae_encoder = self.load_vae_encoder()

# After encoding
if self.config.get("lazy_load", False) or self.config.get("unload_modules", False):
    del self.vae_encoder
    torch.cuda.empty_cache()
    gc.collect()
```

### Adapter Attention flash_attn2 Compatibility (manual, not automated)

The animate adapter's `infer_post_adapter` in `transformer_infer.py` calls flash_attn with positional args designed for flash_attn3. When using flash_attn2, it needs explicit `cu_seqlens_q`, `cu_seqlens_kv`, `max_seqlen_q`, `max_seqlen_kv` kwargs. This patch is currently applied manually on the remote server and needs to be automated.

## Postprocessing

Runs as a separate CLI on the GPU server (`python -m x2v_pipeline.postprocess`):

1. **SkinContrast** — 1× upscale model (via spandrel) that enhances skin texture detail
2. **RIFE** — Frame interpolation (2× by default), doubles FPS from 16→32
3. **FFmpeg** — H.264 encoding with configurable CRF (default 19)
4. **Audio** — Copies audio from driving video to output

```yaml
postprocess:
  skin_contrast: true
  rife_multiplier: 2
  output_fps: 32
  crf: 19
```

## ISP (Image Signal Processing)

Optional final stage. Applies film-like processing:

```yaml
isp:
  enabled: true
  graininess: 40    # Film grain intensity
  sharpness: 20     # Unsharp mask strength
  brightness: -5    # EV adjustment
  vignette: 10      # Corner darkening
```

Implemented in `src/isp_pipeline/processor.py`, invoked as `python -m isp_pipeline.processor`.

## Remote Execution Flow

`VastAgentService.run_x2v()` orchestrates:

1. **Upload** — rsync driving video + reference image to remote `_inputs/`
2. **Generate script** — `build_remote_command()` creates a bash script with all stages
3. **Run detached** — Script runs in background via nohup, stdout/stderr to `/tmp/comfy_stdout.txt` and `/tmp/comfy_stderr.log`
4. **Poll progress** — Parse stderr for `step_index` lines, report via callback
5. **Download** — rsync output files (raw, final, isp) to local output directory

## Refinement (Disabled)

Two-pass T2V refinement using `wan2.2_t2v_low_noise_14B_fp16.safetensors` (~29 GB):

- Pass 1: denoise 0.13, 4 steps, CFG 1.0, masked (character region only)
- Pass 2: denoise 0.15, 6 steps, CFG 2.2, full frame

Not yet implemented in LightX2V Animate runner. Placeholders exist in `remote_runner.py`. The ComfyUI workflow had this working via two KSampler nodes with the T2V model.

## Speed Comparison

For a 5-second driving video (2 segments):

| Setup | Steps | Time per step | Total inference | Total with post |
|-------|-------|---------------|-----------------|-----------------|
| ComfyUI + 5090 (32GB) | 4 | ~10s | ~80s | ~3-5 min |
| LightX2V + L40 (48GB), no offload | 20 | ~53s | ~35 min | ~40 min |
| LightX2V + 5090 (32GB), cpu_offload | 20 | ~25s (est.) | ~17 min (est.) | ~20 min (est.) |
| LightX2V + 5090 + distill LoRA | 4 | ~25s (est.) | ~3 min (est.) | ~5 min (est.) |

The key speed difference vs ComfyUI is the **distillation LoRA** (`i2v_lightx2v_low_noise_model.safetensors`) which enables 4-step generation. Without it, LightX2V needs 20 steps.
