# VastAI Agent

Manages GPU server lifecycle on VastAI and executes `comfy-pipeline` commands remotely via SSH/rsync.

```
src/vast_agent/
  cli.py        Click CLI entry point
  config.py     YAML config loading
  vastai.py     VastAI REST API wrapper
  remote.py     SSH/rsync operations
```

## Commands

### `vast-agent up -w <workflow>`

Full lifecycle: rent -> push code -> bootstrap -> setup workflow -> start ComfyUI server.

```bash
vast-agent up -w wan_animate
vast-agent up -w wan_animate -c configs/vast_custom.yaml
```

### `vast-agent run`

Upload inputs, run workflow on remote GPU, download results.

```bash
vast-agent run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4

vast-agent run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  --set prompt="A woman dancing energetically" \
  --set lora_high=altf4_high_noise.safetensors \
  --json-output

vast-agent run -w wan_animate \
  --input reference_image=char.png \
  --input reference_video=dance.mp4 \
  -o ./my_results/
```

Internally: rsync inputs -> SSH `comfy-pipeline run` -> rsync results back.

### `vast-agent down`

Gracefully stop remote processes and destroy the instance.

### `vast-agent rent`

Just rent an instance without pushing code or setting up.

### `vast-agent push`

Rsync project code to remote server. Excludes `.git/`, `.venv/`, `__pycache__/`, `output/`, `ComfyUI/`.

### `vast-agent ssh`

Open interactive SSH session.

### `vast-agent exec "command"`

Run any command on the remote server.

```bash
vast-agent exec "nvidia-smi"
vast-agent exec "cd /workspace/comfyui-agent && source .venv/bin/activate && comfy-pipeline list"
```

### `vast-agent pull [path]`

Download files from remote. Relative paths resolve from `remote_path` in config.

```bash
vast-agent pull output/
vast-agent pull logs/ -o ./local_logs/
```

### `vast-agent status`

Show instance info, cost, SSH connectivity. Exits 0 if running and reachable, 1 otherwise.

### `vast-agent destroy`

Destroy instance immediately without graceful shutdown.

## Config

`configs/vast.yaml`:

```yaml
gpu: RTX 5090                # GPU model (use spaces, not underscores)
min_gpu_ram: 32000           # Minimum VRAM in MB
disk_space: 150              # Disk in GB
max_price: 0.50              # Max $/hr
image: pytorch/pytorch:2.4.0-cuda12.4-cudnn9-devel
remote_path: /workspace/comfyui-agent
label: comfyui-agent
ssh_key: ~/.ssh/id_rsa       # must match key registered on VastAI
```

Common GPU names: `RTX 5090`, `RTX 4090`, `RTX 3090`, `RTX A6000`, `A100 SXM4`, `A100 PCIE`, `H100 SXM5`, `L40S`.

## State Tracking

Instance state persisted in `.vast-instance.json`:

```json
{
  "instance_id": 12345,
  "ssh_host": "ssh123.vast.ai",
  "ssh_port": 10600,
  "dph_total": 0.449
}
```

`dph_total` is the hourly cost ($/hr) at rent time, used by the Telegram bot for per-generation cost tracking. Created by `rent`/`up`, deleted by `down`/`destroy`.

## Internal Flow

```
vast-agent up -w wan_animate
  +-- POST /api/v0/bundles/          (search offers)
  +-- PUT /api/v0/asks/{id}/         (rent cheapest)
  +-- poll GET /api/v0/instances/{id}/ + ssh test  (wait for SSH)
  +-- rsync code to remote
  +-- SSH: bootstrap.sh
  +-- SSH: comfy-pipeline setup -w wan_animate
  +-- SSH: comfy-pipeline server start -w wan_animate --listen 0.0.0.0 --wait

vast-agent run -w wan_animate --input ...
  +-- rsync input files to remote
  +-- SSH: comfy-pipeline run ... --json-output
  +-- rsync output/ back to local

vast-agent down
  +-- SSH: pkill ComfyUI processes
  +-- DELETE /api/v0/instances/{id}/
  +-- remove .vast-instance.json
```
