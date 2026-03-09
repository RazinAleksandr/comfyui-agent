from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from comfy_pipeline.config import WorkflowConfig

PID_FILENAME = ".comfyui.pid"


def install_comfyui(config: WorkflowConfig):
    """Clone ComfyUI and install its Python requirements."""
    comfy_path = Path(config.comfyui_path)

    if not comfy_path.exists():
        print(f"Cloning ComfyUI to {comfy_path}...")
        _run(["git", "clone", config.comfyui_repo, str(comfy_path)])

    print("Installing ComfyUI requirements...")
    _run(
        [sys.executable, "-m", "pip", "install", "-r", str(comfy_path / "requirements.txt")]
    )

    for pkg in config.extra_pip:
        print(f"Installing {pkg}...")
        _run([sys.executable, "-m", "pip", "install", pkg], check=False)


def install_custom_nodes(config: WorkflowConfig):
    """Clone and install required custom nodes."""
    nodes_dir = Path(config.comfyui_path) / "custom_nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)

    for node in config.custom_nodes:
        node_path = nodes_dir / node.name
        if node_path.exists():
            print(f"  {node.name} already installed")
            continue

        print(f"  Installing {node.name}...")
        _run(["git", "clone", node.url, str(node_path)])

        req_file = node_path / "requirements.txt"
        if req_file.exists():
            _run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                check=False,
            )

        install_script = node_path / "install.py"
        if install_script.exists():
            _run([sys.executable, str(install_script)], cwd=str(node_path), check=False)


def download_models(config: WorkflowConfig):
    """Download all required models."""
    models_dir = Path(config.comfyui_path) / "models"

    for model in config.models:
        model_path = models_dir / model.path
        model_path.parent.mkdir(parents=True, exist_ok=True)

        if model_path.exists():
            size = model_path.stat().st_size
            if model.min_size and size < model.min_size:
                print(f"  {model_path.name} too small ({size}B), re-downloading...")
                model_path.unlink()
            else:
                print(f"  {model_path.name} already exists")
                continue

        print(f"  Downloading {model_path.name}...")
        _download_file(model.url, model_path)


def verify_models(config: WorkflowConfig) -> list[str]:
    """Verify all models exist and meet minimum size. Returns list of issues."""
    models_dir = Path(config.comfyui_path) / "models"
    issues = []

    for model in config.models:
        model_path = models_dir / model.path
        if not model_path.exists():
            issues.append(f"MISSING: {model.path}")
        elif model.min_size and model_path.stat().st_size < model.min_size:
            actual = model_path.stat().st_size
            issues.append(f"TOO SMALL: {model.path} ({actual}B < {model.min_size}B)")

    return issues


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _pid_file(config: WorkflowConfig) -> Path:
    return Path(config.comfyui_path) / PID_FILENAME


def start_server(
    config: WorkflowConfig,
    listen: str = "127.0.0.1",
    port: int = 8188,
) -> int:
    """Start ComfyUI server in the background. Returns PID."""
    pid_path = _pid_file(config)

    # Check if already running
    if pid_path.exists():
        pid = int(pid_path.read_text().strip())
        if _is_pid_alive(pid):
            print(f"ComfyUI already running (PID {pid})")
            return pid
        pid_path.unlink()

    comfy_path = Path(config.comfyui_path)
    cmd = [sys.executable, "main.py", "--listen", listen, "--port", str(port)]

    print(f"Starting ComfyUI on {listen}:{port}...")
    proc = subprocess.Popen(
        cmd,
        cwd=str(comfy_path),
        start_new_session=True,  # detach from parent so it survives CLI exit
    )

    pid_path.write_text(str(proc.pid))
    print(f"ComfyUI started (PID {proc.pid})")
    return proc.pid


def stop_server(config: WorkflowConfig) -> bool:
    """Stop a running ComfyUI server. Returns True if stopped."""
    pid_path = _pid_file(config)

    if not pid_path.exists():
        print("No ComfyUI server tracked (no PID file)")
        return False

    pid = int(pid_path.read_text().strip())

    if not _is_pid_alive(pid):
        print(f"ComfyUI (PID {pid}) is not running")
        pid_path.unlink()
        return False

    print(f"Stopping ComfyUI (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(10):
        if not _is_pid_alive(pid):
            break
        time.sleep(0.5)
    else:
        print("Force killing...")
        os.kill(pid, signal.SIGKILL)

    pid_path.unlink()
    print("ComfyUI stopped")
    return True


def server_status(config: WorkflowConfig) -> int | None:
    """Check if ComfyUI is running. Returns PID or None."""
    pid_path = _pid_file(config)

    if not pid_path.exists():
        return None

    pid = int(pid_path.read_text().strip())

    if _is_pid_alive(pid):
        return pid

    pid_path.unlink()
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, **kwargs)


def _download_file(url: str, output_path: Path):
    """Download a file, with Google Drive support via gdown."""
    if "drive.google.com" in url:
        _run(
            [sys.executable, "-m", "pip", "install", "-q", "gdown"],
            check=False,
        )
        result = _run(["gdown", url, "-O", str(output_path)], check=False)
        if result.returncode == 0:
            return

    result = _run(["wget", "-c", url, "-O", str(output_path)], check=False)
    if result.returncode != 0:
        print(f"  ERROR: Failed to download {output_path.name}")
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink()
