# VastAI Agent

Manages GPU server lifecycle on VastAI and executes `comfy-pipeline` commands remotely via SSH/rsync.

```
src/vast_agent/
  cli.py        Click CLI entry point (thin wrapper over service)
  service.py    VastAgentService — programmatic interface used by API
  manager.py    ServerManager — multi-server allocation and lifecycle
  registry.py   ServerRegistry — persistent server-to-influencer mapping
  config.py     YAML config loading
  vastai.py     VastAI REST API wrapper
  remote.py     SSH/rsync operations
```

## CLI Commands

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

## Programmatic API

The `VastAgentService` class provides a Python interface used by the generation API routes:

```python
from vast_agent.service import VastAgentService

svc = VastAgentService(config)

# Check server status
status = svc.status()           # → ServerStatus(running, instance_id, ssh_host, ...)

# Full lifecycle
status = svc.up(workflow="wan_animate")    # rent + push + bootstrap + setup + start
result = svc.run(                          # upload inputs + run + download results
    workflow="wan_animate",
    inputs={"reference_image": "img.jpg", "reference_video": "vid.mp4"},
    overrides={"prompt": "..."},
    output_dir="./output"
)                                          # → RunResult(outputs=[...], output_dir="...")
svc.down()                                 # stop processes + destroy instance

# Code sync
svc.push()                                 # rsync code to remote
```

### ServerStatus

```python
@dataclass
class ServerStatus:
    running: bool
    instance_id: int | None
    ssh_host: str | None
    ssh_port: int | None
    actual_status: str | None
    dph_total: float | None
    ssh_reachable: bool
```

### RunResult

```python
@dataclass
class RunResult:
    outputs: list[str]   # local file paths of downloaded results
    output_dir: str      # directory containing all outputs
```

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

## Multi-Server Management

The system supports multiple concurrent VastAI servers, each mapped to an influencer. This is managed by `ServerManager` and persisted in `.vast-registry.json`.

### Server Registry

`.vast-registry.json` replaces the old `.vast-instance.json`:

```json
{
  "servers": {
    "srv_32921955": {
      "instance_id": 32921955,
      "ssh_host": "ssh5.vast.ai",
      "ssh_port": 11954,
      "dph_total": 0.414,
      "influencer_id": "emi2soul",
      "workflow": "wan_animate",
      "created_at": "2026-03-16T10:00:00Z",
      "auto_shutdown": false
    }
  }
}
```

Old `.vast-instance.json` files are auto-migrated to the registry on first startup.

### Smart Allocation

When a generation request arrives for influencer X:

1. **Own server** — check if X has a running server → use it
2. **Borrow** — check if any other influencer's server is free (no active jobs) → borrow it
3. **Create new** — rent a new VastAI instance for X

### Auto-Shutdown

Each server has an `auto_shutdown` flag. When enabled, the server is automatically destroyed after all generation jobs on it complete. Toggled via:

```
POST /api/v1/generation/server/{server_id}/auto-shutdown
{"enabled": true}
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/generation/servers` | List all servers with status and active jobs |
| `GET` | `/generation/server/allocate?influencer_id=...` | Check allocation for an influencer |
| `POST` | `/generation/server/up` | Allocate/start a server (with `influencer_id`) |
| `POST` | `/generation/server/{id}/down` | Shut down a specific server |
| `POST` | `/generation/server/{id}/auto-shutdown` | Toggle auto-shutdown |

### ServerManager API

```python
from vast_agent.manager import ServerManager

manager = get_server_manager()

# Allocate a server for an influencer
server_id, service = manager.allocate_server("emi2soul", "wan_animate")

# List all servers
servers = manager.list_servers()

# Shut down
manager.shutdown_server(server_id)

# Auto-shutdown after jobs complete
manager.set_auto_shutdown(server_id, True)
```

## Legacy State Tracking

The CLI still uses per-server state files (`.vast-server-{id}.json`) for backward compatibility. These are created by `ServerManager` and managed automatically.

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
