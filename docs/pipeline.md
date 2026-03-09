# ComfyUI Pipeline Guide

Automated CLI for setting up ComfyUI and running workflows without the UI.

## Quick Start

```bash
# 1. Create virtual environment
./bootstrap.sh
source .venv/bin/activate

# 2. Setup ComfyUI on server (install nodes + download models)
comfy-pipeline setup -w wan_animate

# 3. Start ComfyUI server
comfy-pipeline server start -w wan_animate --wait

# 4. Run generation
comfy-pipeline run -w wan_animate \
  --input reference_image=character.png \
  --input reference_video=dance.mp4

# 5. Stop server when done
comfy-pipeline server stop -w wan_animate
```

## Commands

### `comfy-pipeline setup -w <workflow>`

Installs ComfyUI, custom nodes, and downloads all models defined in the workflow config.

```bash
comfy-pipeline setup -w wan_animate              # full setup
comfy-pipeline setup -w wan_animate --skip-models # nodes only, skip large downloads
```

### `comfy-pipeline server start|stop|status`

Manage the ComfyUI server independently from runs.

```bash
comfy-pipeline server start -w wan_animate            # start in background
comfy-pipeline server start -w wan_animate --wait      # start and wait until ready
comfy-pipeline server start -w wan_animate --listen 0.0.0.0  # listen on all interfaces
comfy-pipeline server status -w wan_animate            # check if running
comfy-pipeline server stop -w wan_animate              # stop server
```

The server stays running between `run` calls — no model reload overhead per generation. PID is tracked in `<comfyui_path>/.comfyui.pid`.

### `comfy-pipeline run -w <workflow>`

Uploads inputs, executes the workflow, and downloads results. Requires a running server.

```bash
# single run — input names come from the config's inputs section
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png \
  --input reference_video=ref.mp4

# batch (see "Batch Input" below)
comfy-pipeline run -w wan_animate --batch-dir ./inputs/ -o ./output/

# override parameters per-run using semantic names (repeatable)
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --set prompt="A person dancing dynamically" \
  --set lora_high=altf4_high_noise.safetensors \
  --set lora_high_strength=0.7

# raw node_id.param format also works
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --set 227.text="A person dancing dynamically"

# machine-readable output (for agents)
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --json-output
# -> {"outputs": ["output/wan_animate/ref_ref/AnimateDiff_00001.mp4"]}

# connect to remote ComfyUI
comfy-pipeline run -w wan_animate \
  --input reference_image=ref.png --input reference_video=ref.mp4 \
  --host 0.0.0.0 --port 8188
```

`--input` names must match keys defined in the config's `inputs` section. Each workflow defines its own set of inputs — no code changes needed for new workflows.

`--set` supports semantic names (defined in config `parameters` section) or raw `node_id.param` format. Values are auto-coerced: `0.7` -> float, `42` -> int, `true` -> bool, everything else -> string.

### `comfy-pipeline list`

Shows available workflow configs from `configs/` directory.

### `comfy-pipeline convert -w <workflow>`

Converts a UI-format workflow to API format (requires running ComfyUI). Useful for debugging or using the workflow with other tools.

## Batch Input

Put input files in a directory. Two layouts supported:

**Subdirectory layout** (recommended):
```
inputs/
  dance_scene/
    character.png
    dance.mp4
  walk_scene/
    character.png
    walk.mp4
```

**Flat layout** (matching filenames):
```
inputs/
  scene1.png
  scene1.mp4
  scene2.jpg
  scene2.mp4
```

Files are automatically matched to config inputs by extension (image extensions -> image inputs, video extensions -> video inputs).

Results are saved to `output/<workflow_name>/<set_name>/`.

## Workflow Configs

Configs live in `configs/*.yaml`. Each config maps a workflow to its dependencies:

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
    min_size: 10000000000    # skip re-download if file is at least this size

inputs:
  reference_image:
    node_id: "311"           # LoadImage node ID in the workflow
    param: image
  reference_video:
    node_id: "417"           # VHS_LoadVideo node ID
    param: video

outputs:
  - node_id: "504"           # VHS_VideoCombine node ID
    type: VHS_VideoCombine

parameters:
  prompt:
    node_id: "227"
    param: text
  lora_high:
    node_id: "463"
    param: lora_name
```

### Adding a New Workflow

1. Save your workflow JSON to `workflows/`
2. Create a new YAML config in `configs/`
3. Find node IDs for input/output nodes (open the workflow JSON, search for `LoadImage`, `VHS_LoadVideo`, `VHS_VideoCombine`, etc.)
4. List all required custom nodes and models with download URLs
5. Define `inputs` (file uploads), `outputs` (result downloads), and `parameters` (tunable values)
6. Run `comfy-pipeline setup -w your_workflow`

No code changes needed — everything is config-driven.

### Overrides

Change node parameters without editing the workflow file:

```yaml
overrides:
  "463":                        # node ID
    lora_name: "my_lora.safetensors"
    strength_model: 0.7
  "227":                        # positive prompt node
    text: "A person dancing in a club"
```

This is useful for swapping LoRAs per character or changing prompts.

## Architecture

```
src/comfy_pipeline/
  cli.py        Click CLI entry point
  config.py     YAML config loading
  client.py     ComfyUI HTTP/WebSocket client
  workflow.py   Workflow format conversion and input injection
  install.py    ComfyUI + nodes + models installation
  runner.py     Pipeline orchestration (upload -> run -> download)
```

The pipeline communicates with ComfyUI via its REST API (`/upload/image`, `/prompt`, `/history`, `/view`). Workflow progress is tracked over WebSocket.

UI-format workflows are automatically converted to API format at runtime using ComfyUI's `/object_info` endpoint. This resolves SetNode/GetNode variable passing from the `cg-use-everywhere` plugin.

## Agent Integration

The pipeline is designed for iterative use by an agent (e.g. Telegram bot). The server starts once and stays running — each `run` call is fast since models are already loaded.

### Agent loop

```bash
# 1. Start server once per session
comfy-pipeline server start -w wan_animate --wait

# 2. First attempt
comfy-pipeline run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  --set prompt="A woman dancing in a nightclub" \
  --set lora_high=altf4_high_noise.safetensors \
  --set lora_high_strength=0.7 \
  --json-output
# -> {"outputs": ["output/wan_animate/char_dance/AnimateDiff_00001.mp4"]}
# -> agent sends result video to user via telegram

# 3. User: "make it more dynamic"
comfy-pipeline run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  --set prompt="A woman dancing energetically in a nightclub, dynamic movement" \
  --set lora_high=altf4_high_noise.safetensors \
  --set lora_high_strength=0.7 \
  --json-output

# 4. User: "try different video"
comfy-pipeline run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=new_dance.mp4 \
  --set prompt="A woman dancing energetically in a nightclub, dynamic movement" \
  --set lora_high=altf4_high_noise.safetensors \
  --set lora_high_strength=0.7 \
  --json-output

# 5. User: "looks good!" -> done

# 6. Stop server when session ends
comfy-pipeline server stop -w wan_animate
```

### Key flags for agents

| Flag | Purpose |
|------|---------|
| `--input name=path` | Provide input file (names from config, repeatable) |
| `--set name=value` | Change parameter by semantic name (defined in config) |
| `--set node.param=val` | Change parameter by raw node ID (advanced) |
| `--json-output` | Machine-readable output: `{"outputs": ["path/to/video.mp4"]}` |
| `server start --wait` | Block until server is ready before first run |
| `server status` | Check if server is alive before running |

### Available parameters for Wan Animate

Defined in `configs/wan_animate.yaml`:

**Inputs** (via `--input`):

| Name | What it expects |
|------|----------------|
| `reference_image` | Reference character image (.png, .jpg) |
| `reference_video` | Reference motion video (.mp4) |

**Parameters** (via `--set`):

| Name | What it controls |
|------|-----------------|
| `prompt` | Positive text prompt |
| `lora_high` | High-noise LoRA filename |
| `lora_high_strength` | High-noise LoRA strength (float) |
| `lora_low` | Low-noise LoRA filename |
| `lora_low_strength` | Low-noise LoRA strength (float) |

To add more parameters or inputs, edit the config YAML — no code changes needed.

## Typical Server Workflow

```bash
# On your local machine
rsync -avz --exclude .venv --exclude ComfyUI . user@server:/workspace/comfyui-agent/

# On the server
cd /workspace/comfyui-agent
./bootstrap.sh
source .venv/bin/activate
comfy-pipeline setup -w wan_animate
comfy-pipeline server start -w wan_animate --wait

# Run generations (server stays up between runs)
comfy-pipeline run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4
comfy-pipeline run -w wan_animate --batch-dir ./inputs/

# Stop when done
comfy-pipeline server stop -w wan_animate

# Copy results back (from local machine)
rsync -avz user@server:/workspace/comfyui-agent/output/ ./output/
```
