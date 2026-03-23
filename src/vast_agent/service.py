"""Programmatic interface to vast-agent operations.

Extracts the core logic from cli.py into a reusable service class.
The CLI becomes a thin wrapper; the API routes call this directly.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vast_agent.config import VastConfig
from vast_agent.remote import (
    RemoteError,
    check_ssh,
    get_remote_file,
    poll_remote_done,
    rsync_pull,
    rsync_push,
    rsync_push_files,
    run_remote,
    run_remote_detached,
)
from vast_agent.vastai import Instance, VastAPIError, VastClient

logger = logging.getLogger(__name__)

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_FILE = PROJECT_ROOT / ".vast-instance.json"
DEFAULT_CONFIG = CONFIGS_DIR / "vast.yaml"

SSH_POLL_INTERVAL = 10
SSH_POLL_TIMEOUT = 600
INSTANCE_LOAD_TIMEOUT = 180
MAX_RENT_ATTEMPTS = 5
RENT_RETRY_DELAY = 30
SEARCH_RETRY_DELAY = 30  # fallback; overridden by config.search_retry_delay
MAX_SEARCH_ATTEMPTS = 20  # fallback; overridden by config.max_search_attempts


class LoadTimeoutError(Exception):
    """Raised when an instance takes too long to load."""


class NoInstanceError(Exception):
    """Raised when no active instance exists."""


@dataclass
class ServerStatus:
    running: bool
    instance_id: int | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    actual_status: str | None = None
    dph_total: float | None = None
    ssh_reachable: bool = False
    label: str | None = None


@dataclass
class RunResult:
    outputs: list[str] = field(default_factory=list)
    output_dir: str = ""


class VastAgentService:
    """Programmatic interface to vast-agent operations.

    All methods are synchronous (blocking). Callers should wrap in
    ``asyncio.to_thread()`` when used from async contexts.
    """

    def __init__(
        self,
        config: VastConfig | None = None,
        state_file: Path | None = None,
        project_root: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config or _load_config()
        self.state_file = state_file or DEFAULT_STATE_FILE
        self.project_root = project_root or PROJECT_ROOT
        self.progress_callback = progress_callback

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self.progress_callback:
            self.progress_callback(msg)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(
        self, instance_id: int, ssh_host: str, ssh_port: int, dph_total: float | None = None
    ) -> None:
        state = {
            "instance_id": instance_id,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "dph_total": dph_total,
        }
        self.state_file.write_text(json.dumps(state, indent=2) + "\n")

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            raise NoInstanceError("No active instance. Run 'up' first.")
        return json.loads(self.state_file.read_text())

    def _clear_state(self) -> None:
        if self.state_file.exists():
            self.state_file.unlink()

    def has_instance(self) -> bool:
        return self.state_file.exists()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def status(self) -> ServerStatus:
        """Get current instance status. Does not raise if no instance."""
        if not self.state_file.exists():
            return ServerStatus(running=False)

        state = json.loads(self.state_file.read_text())
        instance_id = state.get("instance_id")
        if instance_id is None:
            return ServerStatus(running=False)

        try:
            client = VastClient()
            instance = client.get_instance(instance_id)
        except (VastAPIError, Exception) as exc:
            logger.warning("Could not query instance %s: %s", instance_id, exc)
            err_msg = str(exc).lower()
            # Instance confirmed gone — clean up stale state file
            if "not found" in err_msg and "null" not in err_msg:
                self.state_file.unlink(missing_ok=True)
                return ServerStatus(running=False, instance_id=instance_id)
            # API returned junk but instance may still be alive — report
            # SSH info from state file and mark as "loading" via actual_status
            ssh_host = state.get("ssh_host")
            ssh_port = state.get("ssh_port")
            ssh_ok = False
            if ssh_host and ssh_port:
                ssh_ok = check_ssh(ssh_host, ssh_port, ssh_key=self.config.ssh_key)
            return ServerStatus(
                running=ssh_ok,
                instance_id=instance_id,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                actual_status="loading" if not ssh_ok else "running",
                dph_total=state.get("dph_total"),
                ssh_reachable=ssh_ok,
            )

        ssh_reachable = False
        if instance.ssh_host and instance.ssh_port:
            ssh_reachable = check_ssh(
                instance.ssh_host, instance.ssh_port, ssh_key=self.config.ssh_key
            )

        is_running = instance.actual_status == "running" and ssh_reachable

        return ServerStatus(
            running=is_running,
            instance_id=instance.instance_id,
            ssh_host=instance.ssh_host,
            ssh_port=instance.ssh_port,
            actual_status=instance.actual_status,
            dph_total=instance.dph_total,
            ssh_reachable=ssh_reachable,
            label=instance.label,
        )

    def up(self, workflow: str) -> ServerStatus:
        """Rent instance, push code, setup workflow, start ComfyUI server.

        Returns the final server status.
        """
        config = self.config
        client = VastClient()

        # Step 1: Search for offers (retry until found or timeout)
        retry_delay = getattr(config, "search_retry_delay", SEARCH_RETRY_DELAY)
        max_attempts = getattr(config, "max_search_attempts", MAX_SEARCH_ATTEMPTS)
        self._log(f"Searching for {config.gpu} instance...")
        offers = _search_offers(client, config)
        search_attempt = 1
        while not offers and search_attempt < max_attempts:
            self._log(f"No offers found (attempt {search_attempt}/{max_attempts}). Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            search_attempt += 1
            offers = _search_offers(client, config)
        if not offers:
            raise VastAPIError(f"No offers found after {max_attempts} attempts (~{max_attempts * retry_delay // 60} min).")

        instance_id, instance = _rent_with_retry(
            client, config, offers, log=self._log
        )
        self._save_state(instance_id, instance.ssh_host, instance.ssh_port, instance.dph_total)

        host = instance.ssh_host
        port = instance.ssh_port
        key = config.ssh_key

        # Step 2: Push code
        self._log("Pushing code to remote...")
        rsync_push(
            host=host,
            port=port,
            local_path=self.project_root,
            remote_path=config.remote_path,
            ssh_key=key,
        )

        # Step 3: Bootstrap
        self._log("Bootstrapping remote environment...")
        run_remote(
            host, port,
            f"cd {shlex.quote(config.remote_path)} && bash bootstrap.sh",
            ssh_key=key,
        )

        # Step 4: Setup workflow
        self._log(f"Setting up workflow '{workflow}'...")
        hf_prefix = ""
        hf_token = os.environ.get("HF_TOKEN", "")
        if hf_token:
            hf_prefix = f"HF_TOKEN={shlex.quote(hf_token)} "
        run_remote(
            host, port,
            f"cd {shlex.quote(config.remote_path)} && source .venv/bin/activate && "
            f"{hf_prefix}comfy-pipeline setup -w {shlex.quote(workflow)}",
            ssh_key=key,
        )

        # Step 5: Start server
        self._log("Starting ComfyUI server...")
        run_remote(
            host, port,
            f"cd {shlex.quote(config.remote_path)} && source .venv/bin/activate && "
            f"comfy-pipeline server start -w {shlex.quote(workflow)} --listen 0.0.0.0 --wait",
            ssh_key=key,
        )

        self._log(f"Instance ready! SSH: root@{host} -p {port}")
        return self.status()

    @staticmethod
    def _parse_progress(line: str, report: Callable[[dict], None]) -> None:
        """Parse ComfyUI stderr lines and emit progress updates."""
        # Match: "Executing node 42 (KSampler)..."
        m = re.search(r"Executing node \d+ \(([^)]+)\)", line)
        if m:
            report({"phase": "running", "stage": "executing", "node": m.group(1)})
            return

        # Match sampling steps: "step 15/30" or "15/30" or "Step: 15/30"
        m = re.search(r"(?:step[:\s]*)?(\d+)\s*/\s*(\d+)", line, re.IGNORECASE)
        if m:
            step, total = int(m.group(1)), int(m.group(2))
            report({
                "phase": "running",
                "stage": "sampling",
                "step": step,
                "total": total,
                "percent": round(step / total * 100) if total > 0 else 0,
            })
            return

        # Match percentage: "42%" or "42.5%"
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
        if m:
            report({"phase": "running", "stage": "sampling", "percent": round(float(m.group(1)))})
            return

    def run(
        self,
        workflow: str,
        inputs: dict[str, Path] | None = None,
        overrides: dict[str, str] | None = None,
        output_dir: str = "output",
        progress_callback: Callable[[dict], None] | None = None,
    ) -> RunResult:
        """Run a workflow on the remote GPU server.

        Args:
            workflow: Workflow config name (e.g. "wan_animate").
            inputs: Mapping of input name to local file path.
            overrides: Mapping of parameter name to value.
            output_dir: Local directory for downloaded results.

        Returns:
            RunResult with list of local output file paths.
        """
        state = self._load_state()
        host = state["ssh_host"]
        port = state["ssh_port"]
        key = self.config.ssh_key
        input_files = inputs or {}
        set_args: list[str] = []
        for k, v in (overrides or {}).items():
            set_args.extend(["--set", f"{k}={v}"])

        def _report(data: dict) -> None:
            if progress_callback is not None:
                progress_callback(data)

        # Upload input files
        remote_input_dir = f"{self.config.remote_path}/_inputs"
        remote_paths: dict[str, str] = {}
        if input_files:
            self._log("Uploading input files...")
            _report({"phase": "uploading", "stage": "uploading"})
            remote_paths = rsync_push_files(
                host, port, input_files, remote_input_dir, ssh_key=key
            )

        # Build remote command
        cmd_parts = [
            f"cd {shlex.quote(self.config.remote_path)}",
            "source .venv/bin/activate",
            f"comfy-pipeline run -w {shlex.quote(workflow)}",
        ]
        for name, remote_path in remote_paths.items():
            cmd_parts[-1] += f" --input {shlex.quote(name + '=' + remote_path)}"
        for arg in set_args:
            cmd_parts[-1] += f" {shlex.quote(arg)}"

        remote_output = f"{self.config.remote_path}/output"
        cmd_parts[-1] += f" -o {shlex.quote(remote_output)}"
        cmd_parts[-1] += " --json-output"

        # Clean remote output dir
        run_remote(
            host, port,
            f"rm -rf {shlex.quote(remote_output)} && mkdir -p {shlex.quote(remote_output)}",
            ssh_key=key, capture=True, check=False,
        )

        remote_cmd = " && ".join(cmd_parts)

        # Run via detached execution (survives SSH drops on vast.ai)
        self._log("Running workflow on remote server...")
        _report({"phase": "running", "stage": "running"})
        run_remote_detached(
            host, port, f"PYTHONUNBUFFERED=1 {remote_cmd}", ssh_key=key
        )

        stderr_offset = 0
        poll_interval = 5
        exit_code: int | None = None

        while exit_code is None:
            time.sleep(poll_interval)
            stderr_all = get_remote_file(host, port, "/tmp/comfy_stderr.log", ssh_key=key)
            if len(stderr_all) > stderr_offset:
                new_data = stderr_all[stderr_offset:]
                stderr_offset = len(stderr_all)
                for line in new_data.splitlines():
                    stripped = line.strip()
                    if stripped:
                        self._log(stripped)
                        self._parse_progress(stripped, _report)
            exit_code = poll_remote_done(host, port, ssh_key=key)

        # Flush remaining stderr
        stderr_all = get_remote_file(host, port, "/tmp/comfy_stderr.log", ssh_key=key)
        if len(stderr_all) > stderr_offset:
            for line in stderr_all[stderr_offset:].splitlines():
                if line.strip():
                    self._log(line.strip())

        stdout = get_remote_file(host, port, "/tmp/comfy_stdout.txt", ssh_key=key)

        if exit_code != 0:
            raise RemoteError(f"Remote workflow failed (exit {exit_code})")

        # Pull results
        local_output = Path(output_dir)
        local_output.mkdir(parents=True, exist_ok=True)

        run_remote(
            host, port,
            f"mkdir -p {shlex.quote(remote_output)}",
            ssh_key=key, capture=True, check=False,
        )

        self._log("Downloading results...")
        _report({"phase": "downloading", "stage": "downloading"})
        rsync_pull(host, port, f"{remote_output}/", str(local_output) + "/", ssh_key=key)

        # Validate downloaded files: compare sizes with remote
        try:
            remote_sizes_raw = run_remote(
                host, port,
                f"find {shlex.quote(remote_output)} -type f -printf '%s %f\\n'",
                ssh_key=key, capture=True, check=False,
            )
            if remote_sizes_raw:
                for line in remote_sizes_raw.strip().splitlines():
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2:
                        remote_size, fname = int(parts[0]), parts[1]
                        local_file = local_output / fname
                        if local_file.exists():
                            local_size = local_file.stat().st_size
                            if local_size != remote_size:
                                logger.warning(
                                    "Download size mismatch for %s: local=%d remote=%d",
                                    fname, local_size, remote_size,
                                )
                        else:
                            logger.warning("Remote file %s not found locally after rsync", fname)
        except Exception:
            logger.debug("Download validation skipped", exc_info=True)

        # Parse JSON output, rewrite remote paths to local
        # The JSON line may be mixed with other stdout; scan lines in reverse
        outputs: list[str] = []
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    remote_result = json.loads(line)
                    for rpath in remote_result.get("outputs", []):
                        rel = rpath.replace(remote_output + "/", "")
                        outputs.append(str(local_output / rel))
                    break
                except (json.JSONDecodeError, KeyError, AttributeError):
                    continue
        if not outputs:
            logger.warning("Could not parse JSON output from remote stdout, scanning local files")
            # Fallback: find all media files in local output dir
            for f in sorted(local_output.rglob("*")):
                if f.is_file() and f.suffix.lower() in (".mp4", ".webm", ".gif", ".png", ".jpg"):
                    outputs.append(str(f))

        return RunResult(outputs=outputs, output_dir=str(local_output))

    def down(self) -> None:
        """Stop server and destroy instance."""
        state = self._load_state()
        config = self.config
        client = VastClient()
        host = state["ssh_host"]
        port = state["ssh_port"]
        instance_id = state["instance_id"]

        # Graceful stop
        self._log("Stopping remote processes...")
        try:
            run_remote(
                host, port,
                "pkill -f 'python.*ComfyUI' 2>/dev/null; "
                "pkill -f 'comfy-pipeline' 2>/dev/null; true",
                ssh_key=config.ssh_key, capture=True, check=False,
            )
        except RemoteError:
            pass

        self._log(f"Destroying instance {instance_id}...")
        client.destroy_instance(instance_id)
        self._clear_state()
        self._log("Instance destroyed.")

    def destroy(self) -> None:
        """Force-destroy instance without graceful shutdown."""
        state = self._load_state()
        client = VastClient()
        instance_id = state["instance_id"]

        self._log(f"Destroying instance {instance_id}...")
        client.destroy_instance(instance_id)
        self._clear_state()
        self._log("Instance destroyed.")

    def push(self) -> None:
        """Push project code to remote."""
        state = self._load_state()
        rsync_push(
            host=state["ssh_host"],
            port=state["ssh_port"],
            local_path=self.project_root,
            remote_path=self.config.remote_path,
            ssh_key=self.config.ssh_key,
        )


# ---------------------------------------------------------------------------
# Shared helpers (used by both service and CLI)
# ---------------------------------------------------------------------------


def _load_config(config_path: str | None = None) -> VastConfig:
    if config_path:
        path = Path(config_path)
        if not path.exists():
            path = CONFIGS_DIR / config_path
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        return VastConfig.from_yaml(path)
    if DEFAULT_CONFIG.exists():
        return VastConfig.from_yaml(DEFAULT_CONFIG)
    return VastConfig()


def _search_offers(client: VastClient, config: VastConfig) -> list[dict]:
    logger.info(
        "Searching VastAI: gpu=%s min_ram=%sMB disk=%sGB max_price=$%.2f/hr geo=%s extra=%s",
        config.gpu, config.min_gpu_ram, config.disk_space, config.max_price,
        config.geolocation, config.extra_filters,
    )
    offers = client.search_offers(
        gpu_name=config.gpu,
        min_gpu_ram=config.min_gpu_ram,
        disk_space=config.disk_space,
        max_price=config.max_price,
        geolocation=config.geolocation,
        extra_filters=config.extra_filters or None,
        max_bw_price=config.max_bw_price,
    )
    logger.info("VastAI search returned %d offers", len(offers))
    return offers


def _wait_for_ssh(
    client: VastClient,
    instance_id: int,
    ssh_key: str = "",
    timeout: int = SSH_POLL_TIMEOUT,
    log: Callable[[str], None] | None = None,
) -> Instance:
    _log = log or (lambda msg: logger.info(msg))
    _log("Waiting for instance to be ready...")
    start = time.time()

    while time.time() - start < timeout:
        instance = client.get_instance(instance_id)
        status = instance.actual_status or "unknown"
        elapsed = int(time.time() - start)

        if instance.ssh_host and instance.ssh_port:
            if status == "running":
                if check_ssh(instance.ssh_host, instance.ssh_port, ssh_key=ssh_key):
                    _log(f"[{elapsed}s] SSH ready on {instance.ssh_host}:{instance.ssh_port}")
                    return instance
                _log(f"[{elapsed}s] status={status}, SSH not ready yet")
            else:
                _log(f"[{elapsed}s] status={status}")
        else:
            _log(f"[{elapsed}s] status={status}")

        time.sleep(SSH_POLL_INTERVAL)

    raise LoadTimeoutError(
        f"Instance {instance_id} did not become SSH-ready within {timeout}s"
    )


def _rent_with_retry(
    client: VastClient,
    config: VastConfig,
    offers: list[dict],
    log: Callable[[str], None] | None = None,
) -> tuple[int, Instance]:
    _log = log or (lambda msg: logger.info(msg))
    tried_ids: set[int] = set()

    for attempt in range(MAX_RENT_ATTEMPTS):
        offer = None
        for o in offers:
            if o["id"] not in tried_ids:
                offer = o
                break

        if offer is None:
            _log(f"No untried offers left. Re-searching in {RENT_RETRY_DELAY}s...")
            time.sleep(RENT_RETRY_DELAY)
            offers = _search_offers(client, config)
            for o in offers:
                if o["id"] not in tried_ids:
                    offer = o
                    break
            if offer is None:
                _log("Still no new offers available.")
                continue

        offer_id = offer["id"]
        tried_ids.add(offer_id)
        price = offer.get("dph_total", "?")
        gpu = offer.get("gpu_name", config.gpu)
        _log(f"Attempt {attempt + 1}/{MAX_RENT_ATTEMPTS}: renting {gpu} @ ${price}/hr (offer {offer_id})")

        try:
            instance_id = client.create_instance(
                offer_id=offer_id,
                image=config.image,
                disk=config.disk_space,
                label=config.label,
                onstart=config.onstart,
            )
        except VastAPIError as e:
            _log(f"Offer {offer_id} unavailable: {e}. Trying next...")
            continue

        _log(f"Instance created: {instance_id}")

        try:
            instance = _wait_for_ssh(
                client, instance_id, ssh_key=config.ssh_key,
                timeout=INSTANCE_LOAD_TIMEOUT, log=log,
            )
            return instance_id, instance
        except LoadTimeoutError:
            _log(f"Instance {instance_id} took too long. Destroying and trying next...")
            client.destroy_instance(instance_id)

    raise VastAPIError(f"All {MAX_RENT_ATTEMPTS} rent attempts failed")
