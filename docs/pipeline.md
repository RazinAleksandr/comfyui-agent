# ComfyUI Pipeline

CLI that runs on the GPU server: installs ComfyUI, manages server lifecycle, runs workflows programmatically.

```
src/comfy_pipeline/
  cli.py        Click CLI entry point
  config.py     YAML config loading
  client.py     ComfyUI HTTP/WebSocket client
  workflow.py   Workflow format conversion and input injection
  install.py    ComfyUI + nodes + models installation
  runner.py     Pipeline orchestration (upload -> run -> download)
```

Communicates with ComfyUI via REST API (`/upload/image`, `/prompt`, `/history`, `/view`) and WebSocket for progress tracking. UI-format workflows are automatically converted to API format using `/object_info`.

## Commands

### `comfy-pipeline setup -w <workflow>`

Installs ComfyUI, custom nodes, and downloads all models defined in the workflow config.

```bash
comfy-pipeline setup -w wan_animate
comfy-pipeline setup -w wan_animate --skip-models    # nodes only, skip large downloads
```

### `comfy-pipeline server start|stop|status`

```bash
comfy-pipeline server start -w wan_animate --wait         # start and wait until ready
comfy-pipeline server start -w wan_animate --listen 0.0.0.0  # listen on all interfaces
comfy-pipeline server status -w wan_animate
comfy-pipeline server stop -w wan_animate
```

Server stays running between `run` calls — no model reload overhead. PID tracked in `<comfyui_path>/.comfyui.pid`.

### `comfy-pipeline run -w <workflow>`

Uploads inputs, executes the workflow, downloads results. Requires a running server.

```bash
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png \
  --input reference_video=ref.mp4

# with parameter overrides
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --set prompt="A person dancing dynamically" \
  --set lora_high=altf4_high_noise.safetensors

# raw node_id.param format
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --set 227.text="A person dancing dynamically"

# JSON output for programmatic use
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --json-output
# -> {"outputs": ["output/wan_animate/ref_ref/AnimateDiff_00001.mp4"]}

# batch mode
comfy-pipeline run -w wan_animate --batch-dir ./inputs/ -o ./output/

# connect to remote ComfyUI
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --host 192.168.1.100 --port 8188
```

`--input` names must match keys in config `inputs` section. `--set` supports semantic names (from config `parameters`) or raw `node_id.param`. Values auto-coerced: `0.7` -> float, `42` -> int, `true` -> bool.

### `comfy-pipeline list`

Shows available workflow configs from `configs/` directory.

### `comfy-pipeline convert -w <workflow>`

Converts UI-format workflow to API format (requires running ComfyUI).

## Batch Input

Two directory layouts supported:

```
# Subdirectory layout (recommended)
inputs/
  dance_scene/
    character.png
    dance.mp4
  walk_scene/
    character.png
    walk.mp4

# Flat layout (matching filenames)
inputs/
  scene1.png
  scene1.mp4
  scene2.jpg
  scene2.mp4
```

Files matched to config inputs by extension. Results saved to `output/<workflow_name>/<set_name>/`.

## Workflow Configs

Configs live in `configs/*.yaml`:

```yaml
name: wan_animate
workflow_file: workflows/Wan_Animate_workflow.json

comfyui:
  path: /workspace/ComfyUI

custom_nodes:
  - name: ComfyUI-WanVideoWrapper
    url: https://github.com/kijai/ComfyUI-WanVideoWrapper.git

models:
  - path: diffusion_models/model.safetensors
    url: https://huggingface.co/...
    min_size: 10000000000

inputs:
  reference_image:
    node_id: "311"
    param: image
  reference_video:
    node_id: "417"
    param: video

outputs:
  - node_id: "504"
    type: VHS_VideoCombine

parameters:
  prompt:
    node_id: "227"
    param: text
  lora_high:
    node_id: "463"
    param: lora_name

overrides:    # optional: change defaults for ALL runs
  "463":
    strength_model: 0.7
```

### Adding a new workflow

1. Save workflow JSON to `workflows/`
2. Create YAML config in `configs/`
3. Find node IDs for inputs/outputs (search workflow JSON for `LoadImage`, `VHS_LoadVideo`, `VHS_VideoCombine`, etc.)
4. List required custom nodes and models with download URLs
5. Define `inputs`, `outputs`, and `parameters`
6. Run `comfy-pipeline setup -w your_workflow`

No code changes needed — everything is config-driven.

## Wan Animate Parameters

Defined in `configs/wan_animate.yaml`:

**Inputs** (`--input`):

| Name | What it expects |
|------|----------------|
| `reference_image` | Reference character image (.png, .jpg) |
| `reference_video` | Reference motion video (.mp4) |

**Parameters** (`--set`):

| Name | What it controls |
|------|-----------------|
| `prompt` | Positive text prompt |
| `lora_high` | High-noise LoRA filename |
| `lora_high_strength` | High-noise LoRA strength (float) |
| `lora_low` | Low-noise LoRA filename |
| `lora_low_strength` | Low-noise LoRA strength (float) |
