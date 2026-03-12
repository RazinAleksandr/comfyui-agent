from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import click

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
    run_ssh_interactive,
)
from vast_agent.vastai import Instance, VastAPIError, VastClient

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_FILE = PROJECT_ROOT / ".vast-instance.json"
DEFAULT_CONFIG = CONFIGS_DIR / "vast.yaml"

SSH_POLL_INTERVAL = 10  # seconds between SSH readiness checks
SSH_POLL_TIMEOUT = 600  # max seconds to wait for SSH
INSTANCE_LOAD_TIMEOUT = 180  # seconds before giving up on a single offer
MAX_RENT_ATTEMPTS = 5  # max total attempts before failing
RENT_RETRY_DELAY = 30  # seconds to wait before re-searching offers


class LoadTimeoutError(Exception):
    """Raised when an instance takes too long to load."""


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def _save_state(instance_id: int, ssh_host: str, ssh_port: int, dph_total: float | None = None) -> None:
    """Save instance state to local JSON file."""
    state = {
        "instance_id": instance_id,
        "ssh_host": ssh_host,
        "ssh_port": ssh_port,
        "dph_total": dph_total,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _load_state() -> dict:
    """Load instance state from local JSON file."""
    if not STATE_FILE.exists():
        click.echo(
            "No active instance. Run 'vast-agent rent' or 'vast-agent up' first.",
            err=True,
        )
        sys.exit(1)
    return json.loads(STATE_FILE.read_text())


def _clear_state() -> None:
    """Remove instance state file."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config(config_path: str | None = None) -> VastConfig:
    """Load VastAI config from file."""
    if config_path:
        path = Path(config_path)
        if not path.exists():
            path = CONFIGS_DIR / config_path
        if not path.exists():
            click.echo(f"Config not found: {config_path}", err=True)
            sys.exit(1)
        return VastConfig.from_yaml(path)

    if DEFAULT_CONFIG.exists():
        return VastConfig.from_yaml(DEFAULT_CONFIG)

    # Use defaults
    return VastConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client() -> VastClient:
    """Create a VastAI API client."""
    try:
        return VastClient()
    except VastAPIError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _wait_for_ssh(
    client: VastClient,
    instance_id: int,
    ssh_key: str = "",
    timeout: int = SSH_POLL_TIMEOUT,
) -> Instance:
    """Poll instance until SSH is ready.

    Raises LoadTimeoutError if *timeout* seconds elapse without SSH becoming
    ready, allowing callers to retry with a different offer.
    """
    print("Waiting for instance to be ready...")
    start = time.time()

    while time.time() - start < timeout:
        instance = client.get_instance(instance_id)

        status = instance.actual_status or "unknown"
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] status={status}", end="")

        if instance.ssh_host and instance.ssh_port:
            print(f" ssh={instance.ssh_host}:{instance.ssh_port}", end="")

            if status == "running":
                print(" - testing SSH...", end="")
                if check_ssh(instance.ssh_host, instance.ssh_port, ssh_key=ssh_key):
                    print(" OK!")
                    return instance
                else:
                    print(" not ready yet")
            else:
                print()
        else:
            print()

        time.sleep(SSH_POLL_INTERVAL)

    raise LoadTimeoutError(
        f"Instance {instance_id} did not become SSH-ready within {timeout}s"
    )


def _search_offers(client: VastClient, config: VastConfig) -> list[dict]:
    """Search for offers matching config criteria."""
    return client.search_offers(
        gpu_name=config.gpu,
        min_gpu_ram=config.min_gpu_ram,
        disk_space=config.disk_space,
        max_price=config.max_price,
        geolocation=config.geolocation,
        extra_filters=config.extra_filters or None,
        max_bw_price=config.max_bw_price,
    )


def _rent_with_retry(
    client: VastClient,
    config: VastConfig,
    offers: list[dict],
) -> tuple[int, Instance]:
    """Try renting offers in order, re-searching when the list is exhausted.

    Returns (instance_id, instance) on success.
    Raises VastAPIError if all attempts fail.
    """
    tried_ids: set[int] = set()

    for attempt in range(MAX_RENT_ATTEMPTS):
        # Pick next untried offer, or re-search
        offer = None
        for o in offers:
            if o["id"] not in tried_ids:
                offer = o
                break

        if offer is None:
            print(f"No untried offers left. Re-searching in {RENT_RETRY_DELAY}s...")
            time.sleep(RENT_RETRY_DELAY)
            offers = _search_offers(client, config)
            for o in offers:
                if o["id"] not in tried_ids:
                    offer = o
                    break
            if offer is None:
                print("Still no new offers available.")
                continue

        offer_id = offer["id"]
        tried_ids.add(offer_id)
        price = offer.get("dph_total", "?")
        gpu = offer.get("gpu_name", config.gpu)
        dl_gb = offer.get("internet_down_cost_per_tb", 0) / 1000
        print(
            f"Attempt {attempt + 1}/{MAX_RENT_ATTEMPTS}: renting {gpu} @ ${price}/hr, BW ${dl_gb:.4f}/GB (offer {offer_id})"
        )

        try:
            instance_id = client.create_instance(
                offer_id=offer_id,
                image=config.image,
                disk=config.disk_space,
                label=config.label,
                onstart=config.onstart,
            )
        except VastAPIError as e:
            print(f"Offer {offer_id} unavailable: {e}. Trying next...")
            continue

        print(f"Instance created: {instance_id}")

        try:
            instance = _wait_for_ssh(
                client,
                instance_id,
                ssh_key=config.ssh_key,
                timeout=INSTANCE_LOAD_TIMEOUT,
            )
            return instance_id, instance
        except LoadTimeoutError:
            print(
                f"Instance {instance_id} took too long to load. Destroying and trying next offer..."
            )
            client.destroy_instance(instance_id)

    raise VastAPIError(
        f"All {MAX_RENT_ATTEMPTS} rent attempts failed"
    )


def _parse_inputs(raw: tuple[str, ...]) -> dict[str, Path]:
    """Parse --input name=path pairs."""
    result: dict[str, Path] = {}
    for item in raw:
        eq = item.find("=")
        if eq < 1:
            raise click.BadParameter(f"Invalid input: '{item}'. Use: name=path")
        name = item[:eq]
        path = Path(item[eq + 1 :])
        if not path.exists():
            raise click.BadParameter(f"File not found: {path}")
        result[name] = path
    return result


def _parse_sets(raw: tuple[str, ...]) -> list[str]:
    """Convert --set flags into CLI arguments for comfy-pipeline."""
    args: list[str] = []
    for item in raw:
        args.extend(["--set", item])
    return args


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
def main():
    """VastAI GPU agent for ComfyUI pipeline."""


@main.command()
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
def rent(config_path: str | None):
    """Rent a GPU instance on VastAI."""
    config = _load_config(config_path)
    client = _get_client()

    geo_label = f", geo={config.geolocation}" if config.geolocation else ""
    print(
        f"Searching for {config.gpu} (>={config.min_gpu_ram}MB RAM, "
        f">={config.disk_space}GB disk, <=${config.max_price}$/hr{geo_label})..."
    )

    offers = _search_offers(client, config)

    if not offers:
        click.echo(
            "No offers found matching criteria. Try relaxing constraints.", err=True
        )
        sys.exit(1)

    best = offers[0]
    dl_gb = best.get("internet_down_cost_per_tb", 0) / 1000
    print(f"Found {len(offers)} offers (sorted by true session cost).")
    print(f"Best: ${best.get('dph_total', 0):.3f}/hr + ${dl_gb:.4f}/GB bandwidth")

    instance_id, instance = _rent_with_retry(client, config, offers)
    _save_state(instance_id, instance.ssh_host, instance.ssh_port, instance.dph_total)
    print(f"Instance ready! SSH: root@{instance.ssh_host} -p {instance.ssh_port}")
    print(f"State saved to {STATE_FILE}")


@main.command()
def push():
    """Push project code to remote server via rsync."""
    state = _load_state()
    config = _load_config()

    rsync_push(
        host=state["ssh_host"],
        port=state["ssh_port"],
        local_path=PROJECT_ROOT,
        remote_path=config.remote_path,
        ssh_key=config.ssh_key,
    )
    print("Push complete.")


@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name")
@click.option(
    "--config", "-c", "config_path", default=None, help="VastAI config file path"
)
def up(workflow: str, config_path: str | None):
    """Rent instance, push code, setup, and start ComfyUI server."""
    config = _load_config(config_path)
    client = _get_client()

    # Step 1: Rent instance
    print(f"=== Renting {config.gpu} instance ===")
    offers = _search_offers(client, config)

    if not offers:
        click.echo("No offers found matching criteria.", err=True)
        sys.exit(1)

    instance_id, instance = _rent_with_retry(client, config, offers)
    _save_state(instance_id, instance.ssh_host, instance.ssh_port, instance.dph_total)

    host = instance.ssh_host
    port = instance.ssh_port
    key = config.ssh_key

    # Step 2: Push code
    print("\n=== Pushing code ===")
    rsync_push(
        host=host,
        port=port,
        local_path=PROJECT_ROOT,
        remote_path=config.remote_path,
        ssh_key=key,
    )

    # Step 3: Bootstrap + setup + start server
    print("\n=== Bootstrapping ===")
    run_remote(
        host,
        port,
        f"cd {shlex.quote(config.remote_path)} && bash bootstrap.sh",
        ssh_key=key,
    )

    print("\n=== Setting up workflow ===")
    # Pass HF_TOKEN to remote for faster authenticated downloads
    hf_prefix = ""
    hf_token = os.environ.get("HF_TOKEN", "")
    if hf_token:
        hf_prefix = f"HF_TOKEN={shlex.quote(hf_token)} "
    run_remote(
        host,
        port,
        f"cd {shlex.quote(config.remote_path)} && source .venv/bin/activate && "
        f"{hf_prefix}comfy-pipeline setup -w {shlex.quote(workflow)}",
        ssh_key=key,
    )

    print("\n=== Starting ComfyUI server ===")
    run_remote(
        host,
        port,
        f"cd {shlex.quote(config.remote_path)} && source .venv/bin/activate && "
        f"comfy-pipeline server start -w {shlex.quote(workflow)} --listen 0.0.0.0 --wait",
        ssh_key=key,
    )

    print(f"\nInstance is up and running!")
    print(f"  SSH: ssh -p {port} root@{host}")
    print(f"  Instance ID: {instance_id}")
    print(
        f"  Cost: ${instance.dph_total}/hr" if instance.dph_total else "  Cost: unknown"
    )


@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name")
@click.option(
    "--input",
    "inputs",
    multiple=True,
    help="Input file: name=path (repeatable)",
)
@click.option(
    "--set",
    "overrides",
    multiple=True,
    help="Override param: name=value (repeatable)",
)
@click.option("--json-output", is_flag=True, help="Print result as JSON to stdout")
@click.option("--output", "-o", default="output", help="Local output directory")
def run(
    workflow: str,
    inputs: tuple[str, ...],
    overrides: tuple[str, ...],
    json_output: bool,
    output: str,
):
    """Run a workflow on the remote GPU server."""
    state = _load_state()
    config = _load_config()
    host = state["ssh_host"]
    port = state["ssh_port"]

    key = config.ssh_key
    input_files = _parse_inputs(inputs)
    set_args = _parse_sets(overrides)

    # Upload input files to remote
    remote_input_dir = f"{config.remote_path}/_inputs"
    if input_files:
        print("Uploading input files...", file=sys.stderr)
        remote_paths = rsync_push_files(
            host, port, input_files, remote_input_dir, ssh_key=key
        )
    else:
        remote_paths = {}

    # Build comfy-pipeline run command
    cmd_parts = [
        f"cd {shlex.quote(config.remote_path)}",
        "source .venv/bin/activate",
        f"comfy-pipeline run -w {shlex.quote(workflow)}",
    ]

    for name, remote_path in remote_paths.items():
        cmd_parts[-1] += f" --input {shlex.quote(name + '=' + remote_path)}"

    for arg in set_args:
        cmd_parts[-1] += f" {shlex.quote(arg)}"

    remote_output = f"{config.remote_path}/output"
    cmd_parts[-1] += f" -o {shlex.quote(remote_output)}"

    if json_output:
        cmd_parts[-1] += " --json-output"

    # Clean remote output dir so rsync only pulls this run's files
    run_remote(host, port,
               f"rm -rf {shlex.quote(remote_output)} && mkdir -p {shlex.quote(remote_output)}",
               ssh_key=key, capture=True, check=False)

    remote_cmd = " && ".join(cmd_parts)

    # Run remotely
    print("Running workflow on remote server...", file=sys.stderr)
    if json_output:
        # Detached execution to survive SSH proxy drops on vast.ai.
        # Start the command via nohup, then poll for completion with
        # short-lived SSH calls so no single connection lives > a few seconds.
        run_remote_detached(
            host, port, f"PYTHONUNBUFFERED=1 {remote_cmd}", ssh_key=key
        )
        stderr_offset = 0
        poll_interval = 5
        exit_code: int | None = None

        while exit_code is None:
            time.sleep(poll_interval)
            # Stream new stderr lines (progress) to local stderr
            stderr_all = get_remote_file(host, port, "/tmp/comfy_stderr.log", ssh_key=key)
            if len(stderr_all) > stderr_offset:
                new_data = stderr_all[stderr_offset:]
                print(new_data, end="", file=sys.stderr, flush=True)
                stderr_offset = len(stderr_all)
            exit_code = poll_remote_done(host, port, ssh_key=key)

        # Flush any remaining stderr
        stderr_all = get_remote_file(host, port, "/tmp/comfy_stderr.log", ssh_key=key)
        if len(stderr_all) > stderr_offset:
            print(stderr_all[stderr_offset:], end="", file=sys.stderr, flush=True)

        stdout = get_remote_file(host, port, "/tmp/comfy_stdout.txt", ssh_key=key)

        if exit_code != 0:
            raise RemoteError(
                f"Remote command failed (exit {exit_code}): {remote_cmd}"
            )

        result = subprocess.CompletedProcess(
            args=remote_cmd, returncode=exit_code, stdout=stdout, stderr=""
        )
    else:
        result = run_remote(host, port, remote_cmd, ssh_key=key)

    # Pull results back
    local_output = Path(output)
    local_output.mkdir(parents=True, exist_ok=True)

    # Ensure remote output dir exists before pulling
    run_remote(host, port, f"mkdir -p {shlex.quote(remote_output)}",
               ssh_key=key, capture=True, check=False)

    print("Downloading results...", file=sys.stderr)
    rsync_pull(
        host,
        port,
        f"{remote_output}/",
        str(local_output) + "/",
        ssh_key=key,
    )

    if json_output and result.stdout:
        # Parse remote JSON output, rewrite paths to local
        try:
            remote_result = json.loads(result.stdout.strip())
            remote_outputs = remote_result.get("outputs", [])
            local_outputs = []
            for rpath in remote_outputs:
                # Convert remote path to local equivalent
                rel = rpath.replace(remote_output + "/", "")
                local_outputs.append(str(local_output / rel))
            print(json.dumps({"outputs": local_outputs}))
        except (json.JSONDecodeError, KeyError):
            # If we can't parse, pass through raw output
            print(result.stdout)
    else:
        print(f"\nDone! Results saved to: {local_output}")


@main.command()
@click.option(
    "--config", "-c", "config_path", default=None, help="VastAI config file path"
)
def down(config_path: str | None):
    """Stop server and destroy the instance."""
    state = _load_state()
    config = _load_config(config_path)
    client = _get_client()
    host = state["ssh_host"]
    port = state["ssh_port"]
    instance_id = state["instance_id"]

    # Try to gracefully stop any running server processes
    print("Stopping remote processes...")
    try:
        run_remote(
            host,
            port,
            f"pkill -f 'python.*ComfyUI' 2>/dev/null; "
            f"pkill -f 'comfy-pipeline' 2>/dev/null; true",
            ssh_key=config.ssh_key,
            capture=True,
            check=False,
        )
    except RemoteError:
        pass  # Instance may already be unreachable

    print(f"Destroying instance {instance_id}...")
    client.destroy_instance(instance_id)
    _clear_state()
    print("Instance destroyed.")


@main.command()
def destroy():
    """Destroy the instance without stopping server gracefully."""
    state = _load_state()
    client = _get_client()
    instance_id = state["instance_id"]

    print(f"Destroying instance {instance_id}...")
    client.destroy_instance(instance_id)
    _clear_state()
    print("Instance destroyed.")


@main.command()
def ssh():
    """Open an interactive SSH session to the remote server."""
    state = _load_state()
    config = _load_config()
    run_ssh_interactive(state["ssh_host"], state["ssh_port"], ssh_key=config.ssh_key)


@main.command("exec")
@click.argument("command")
def exec_cmd(command: str):
    """Run a command on the remote server."""
    state = _load_state()
    config = _load_config()
    run_remote(state["ssh_host"], state["ssh_port"], command, ssh_key=config.ssh_key)


@main.command()
@click.argument("path", default="output/")
@click.option("--output", "-o", default=".", help="Local destination")
def pull(path: str, output: str):
    """Download files from the remote server."""
    state = _load_state()
    config = _load_config()

    # If path is relative, make it relative to remote_path
    if not path.startswith("/"):
        remote_path = f"{config.remote_path}/{path}"
    else:
        remote_path = path

    rsync_pull(
        host=state["ssh_host"],
        port=state["ssh_port"],
        remote_path=remote_path,
        local_path=output,
        ssh_key=config.ssh_key,
    )
    print("Pull complete.")


@main.command()
def status():
    """Show instance status and cost."""
    state = _load_state()
    client = _get_client()
    instance_id = state["instance_id"]

    instance = client.get_instance(instance_id)

    print(f"Instance: {instance.instance_id}")
    print(f"  Status: {instance.actual_status or 'unknown'}")
    print(f"  SSH:    root@{instance.ssh_host} -p {instance.ssh_port}")
    print(
        f"  Cost:   ${instance.dph_total}/hr"
        if instance.dph_total
        else "  Cost:   unknown"
    )
    if instance.label:
        print(f"  Label:  {instance.label}")

    # Test SSH connectivity
    reachable = False
    if instance.ssh_host and instance.ssh_port:
        config = _load_config()
        reachable = check_ssh(
            instance.ssh_host, instance.ssh_port, ssh_key=config.ssh_key
        )
        print(f"  SSH OK: {'yes' if reachable else 'no'}")

    # Exit non-zero if instance isn't running or SSH isn't reachable
    if instance.actual_status != "running" or not reachable:
        sys.exit(1)
