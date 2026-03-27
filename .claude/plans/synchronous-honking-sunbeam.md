# Plan: Replace ComfyUI with LightX2V — Smart Defaults

## Status

Steps 1-7 **IMPLEMENTED**. Step 8 deferred. Step 9 pending quality validation.
**Step 10 (runtime debugging) IN PROGRESS** — inference runs but model loading strategy not yet resolved.

### Implementation Progress

| Step | Status | Notes |
|------|--------|-------|
| 1. Config YAML | Done | `configs/x2v_animate.yaml` |
| 2. X2VConfig | Done | `src/x2v_pipeline/config.py` |
| 3. Postprocess CLI | Done | `src/x2v_pipeline/postprocess.py` |
| 4. Remote install | Done | `src/x2v_pipeline/install.py` |
| 5. Remote runner | Done | `src/x2v_pipeline/remote_runner.py` |
| 6. VastAI service | Done | `src/vast_agent/service.py` — `run_x2v()` + `up_x2v()` |
| 7. API generation | Done | `src/api/routes/generation.py` — engine dispatch |
| 7b. Frontend switch | Done | Default workflow → `x2v_animate` |
| 7c. ISP on remote | Done | ISP phase added to remote script, local ISP skipped for x2v |
| 7d. Output structure | Done | `generated/x2v_animate/{video_stem}/{gen_timestamp}/` |
| 7e. Generated content | Done | `job_id` returned, output picker understands x2v naming |
| 8. Refinement | Deferred | Placeholder in script, `enabled: false` in config |
| 9. Remove ComfyUI | Pending | After quality validation |
| 10. Runtime debug | **In Progress** | Model loading/memory strategy (see below) |

### Runtime Debugging Progress (Step 10)

#### What Works
- Server setup: VastAI instance running (2x RTX 5090, 32GB each)
- All models downloaded: ComfyUI-format (.safetensors) + native LightX2V (.pth)
- Install pipeline: `install.py` correctly bootstraps LightX2V, deps, models
- Remote runner: `remote_runner.py` generates correct bash scripts
- Preprocessing: Pose extraction + face detection completes (~30s)
- Text encoder: Loads from native .pth, runs in 47s, unloads via `unload_modules` — no OOM
- Config plumbing: YAML → inference config JSON → remote script works end-to-end

#### Current Blocker: Model Loading Strategy

The Wan 2.2 Animate 14B transformer is ~28GB bf16. RTX 5090 has 32GB VRAM.

**Approaches tried:**

| Approach | Result | Problem |
|----------|--------|---------|
| No offload, no unload | OOM | All models loaded simultaneously (~40GB+) |
| `cpu_offload: true` alone | Hangs 15-30 min | Phase/block offload buffer creation is extremely slow |
| FP8 quantization | LoRA incompatible | LightX2V doesn't support LoRA on quantized models |
| Sequence parallel (2 GPU) | OOM on both | Each GPU loads FULL model, seq_parallel splits attention not weights |
| Tensor parallel | Not available | Only implemented for ltx2 model, not wan animate |
| `unload_modules: true` + `cpu_offload: true` | Hangs | Same slow buffer creation, GPU stays at 738 MiB |

**Untested approach (most promising):**
`unload_modules: true` + `cpu_offload: false` — load transformer directly to GPU.
- Text encoder already unloaded before transformer loads (via unload_modules)
- 28GB transformer should fit in 32GB VRAM (4GB headroom)
- LoRA merge happens in-place (modifies existing weights, no extra memory)
- Previously failed because native .pth files weren't downloaded — now they are

#### Errors Fixed During Debugging

1. **KeyError 'dim'** — missing `config.json` for architecture params → added auto-download to `install.py`
2. **OOM (no offload)** — all models loaded simultaneously → enabled `unload_modules`
3. **OOM (unload + no offload)** — wrong model files (ComfyUI format for text encoder) → downloaded native .pth
4. **sgl-kernel missing** — added to `extra_pip` in config
5. **install.py mkdir** — `/workspace/models` didn't exist before config.json download → added `mkdir -p`
6. **Sequence parallel OOM** — each GPU loads full model, doesn't split weights → abandoned
7. **FP8 + LoRA incompatible** — LightX2V assertion error → reverted

#### Current Config State (`configs/x2v_animate.yaml`)

```yaml
inference:
  infer_steps: 20
  cpu_offload: true          # NEEDS CHANGE → false (causes slow hang)
  offload_granularity: phase  # REMOVE if cpu_offload disabled
  unload_modules: true        # KEEP — sequential model loading works
  # flash_attn2 (not 3 — 5090 Blackwell FA3 not yet in LightX2V)
  self_attn_1_type: flash_attn2
  cross_attn_1_type: flash_attn2
  cross_attn_2_type: flash_attn2
  adapter_attn_type: flash_attn2
```

#### Files Modified (from original plan)

| File | Additional Changes |
|------|-------------------|
| `configs/x2v_animate.yaml` | Added cpu_offload, offload_granularity, unload_modules, sgl-kernel in extra_pip |
| `configs/vast.yaml` | Tried 2-GPU (`num_gpus: 2`), reverted to 1-GPU; added `cuda_max_good >= 13.0` for Blackwell |
| `src/x2v_pipeline/install.py` | Added `config.json` download from HF, `mkdir -p` before download |
| `src/x2v_pipeline/remote_runner.py` | Added `_infer_launch_cmd()` for multi-GPU torchrun support |

#### Next Steps

1. **Try `unload_modules: true` + `cpu_offload: false`** — kill current run, update config, regenerate
2. If OOM on transformer load: try reducing LoRA count or strength
3. If works: monitor full pipeline (preprocess → inference → postprocess → ISP → download)
4. Verify SSE progress updates show step_index during inference
5. Check output quality
6. Update `install.py` to auto-download native .pth files for future instances
7. Revert `vast.yaml` to 1-GPU, destroy 2-GPU instance

#### Remote Server State

- Instance: 33446013 (ssh1.vast.ai:16012), 2x RTX 5090
- Cost: $1.09/hr (2-GPU pricing, only using 1 GPU)
- Models at `/workspace/models/`: all downloaded (native .pth + ComfyUI safetensors + LoRAs + RIFE + process_checkpoint)
- LightX2V at `/workspace/LightX2V/`: installed, importable

---

## Strategy

Use LightX2V's strengths (FP8 quantization, flash attention, optimized scheduling) to produce high-quality raw output — rather than replicating ComfyUI's workaround of 4-step distilled + refinement.

**Key insight**: ComfyUI uses 4-step distilled inference (fast but low quality) + 2 refinement passes (to fix artifacts). LightX2V with 20-step non-distilled + optimized attention is comparably fast but produces much better raw output. Refinement may not be needed.

**Approach**: Build pipeline with 20-step generation + SkinContrast + RIFE + ISP. Refinement is configurable but OFF by default.

## Pipeline Stages (5 phases on remote GPU)

1. **Preprocess** — LightX2V native (ViTPose + YOLO + SAM2 → pose, face, ref image)
2. **Generate** — LightX2V 20-step animate + LoRA chaining → `output_raw.mp4`
3. **Post-process** — SkinContrast 1x enhance + RIFE 2x interpolation (16fps→32fps) → `output_final.mp4`
4. **ISP** — grain + sharpness + brightness + vignette → `output_isp.mp4`
5. **Download** — rsync all 3 outputs to local

## Architecture

```
configs/x2v_animate.yaml              # Pipeline config (all stages + ISP configurable)
src/x2v_pipeline/
├── __init__.py
├── config.py                         # X2VConfig: YAML loader, LoRA merging, inference config
├── install.py                        # Remote bootstrap: LightX2V + models + deps
├── remote_runner.py                  # Build remote script + progress parsing
└── postprocess.py                    # SkinContrast + RIFE (runs on remote GPU)

src/isp_pipeline/processor.py         # MODIFIED: added CLI entry point (runs on remote GPU)

src/vast_agent/service.py             # MODIFIED: run_x2v() + up_x2v()
src/api/routes/generation.py          # MODIFIED: engine dispatch, x2v output dirs
src/api/routes/influencers.py         # MODIFIED: output picker, job_id in response
frontend/                             # MODIFIED: x2v_animate default, job_id display
```

## Remote Execution Flow (after VastAI rent)

### Server Setup (`up_x2v`)
1. Search VastAI offers → rent GPU instance → wait for SSH
2. rsync push project code to `/workspace/avatar-factory/`
3. (**No bootstrap.sh** — install directly to system Python, disposable container)
4. `build_install_commands()` runs over SSH one by one:
   - `git clone LightX2V` → `pip install -e`
   - Extra pip: onnxruntime-gpu, flash-attn, decord, spandrel, moviepy, sgl-kernel
   - SAM2 from git (needs CUDA dev headers)
   - Download ~13 models (wget/huggingface-cli, with existence+size checks)
   - Download `config.json` from Wan-AI/Wan2.2-Animate-14B for architecture params
5. Verify: `python -c "import lightx2v"`

### Generation (`run_x2v`)
1. rsync upload reference image + driving video
2. `build_remote_command()` generates bash script with PHASE markers
3. Write `run.sh` to remote via heredoc
4. `nohup bash run.sh` → stdout/stderr to `/tmp/comfy_stdout.txt` / `/tmp/comfy_stderr.log`
5. Poll every 5s → `parse_x2v_progress()` for SSE updates
6. rsync download 3 outputs (raw, final, isp) to local

### Output Directory Structure
```
shared/influencers/{id}/pipeline_runs/{run_id}/{platform}/generated/
  x2v_animate/
    {video_stem}/
      {gen_timestamp}/          ← unique per generation attempt
        output_raw.mp4          ← raw 20-step generation (16fps)
        output_final.mp4        ← after SkinContrast + RIFE (32fps)
        output_isp.mp4          ← after ISP grain/sharpness/vignette (final deliverable)
```

Re-generations of the same video create new timestamp subdirectories. Frontend shows latest completed generation per video, picking best output (isp > final > raw).

### Database
- `generation_jobs` table tracks each generation attempt
- `outputs_json` stores relative paths to all 3 output files
- `get_generated_content` API returns `job_id` per item for identification
- Output priority: `output_isp` > `output_final` > `postprocessed` > `upscaled` > `refined` > `output_raw`

## Remaining Steps

### Step 8: LightX2V refinement support (conditional)
Only if quality testing shows refinement is necessary. Requires modifying LightX2V scheduler to support init_latent + denoise_strength.

### Step 9: Remove ComfyUI (after quality validation)
1. Delete `src/comfy_pipeline/`, `configs/wan_animate.yaml`, `workflows/`
2. Remove ComfyUI code paths from `service.py`, `generation.py`
3. Remove engine dispatch — x2v only

## Verification Checklist

- [x] Server setup and model download via `up_x2v`
- [x] Preprocessing completes (pose + face extraction)
- [x] Text encoder loads/runs/unloads without OOM
- [ ] Transformer loads to GPU without OOM (`unload_modules` only, no cpu_offload)
- [ ] Full 20-step inference completes
- [ ] Postprocess (SkinContrast + RIFE) completes
- [ ] ISP phase completes
- [ ] All 3 outputs download correctly to proper directory structure
- [ ] SSE progress updates work (phase transitions, step progress)
- [ ] Generated Content page shows latest video with job ID
- [ ] Re-generation creates new timestamp dir, UI shows latest
- [ ] Quality comparison: LightX2V 20-step vs ComfyUI 4-step+refinement
- [ ] Decision: enable refinement or keep off
- [ ] Revert to 1-GPU VastAI config
- [ ] ComfyUI removal (Step 9)
