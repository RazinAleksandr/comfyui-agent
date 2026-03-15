from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from comfy_pipeline.client import ComfyUIClient
from comfy_pipeline.config import WorkflowConfig
from comfy_pipeline.install import (
    download_models,
    install_comfyui,
    install_custom_nodes,
    server_status,
    start_server,
    stop_server,
    verify_models,
)
from comfy_pipeline.runner import prepare_workflow, run_batch, run_single

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


@click.group()
def main():
    """ComfyUI automated pipeline."""


@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name or path")
@click.option("--skip-models", is_flag=True, help="Skip model downloads")
def setup(workflow: str, skip_models: bool):
    """Setup ComfyUI, install custom nodes, and download models."""
    config = _load_config(workflow)

    print(f"Setting up: {config.name}")
    print(f"  {config.description}\n")

    print("=== Installing ComfyUI ===")
    install_comfyui(config)

    print("\n=== Installing Custom Nodes ===")
    install_custom_nodes(config)

    # Re-install extra_pip after custom nodes to ensure GPU packages
    # override any CPU-only versions pulled in by node requirements
    if config.extra_pip:
        print("\n=== Installing GPU-accelerated packages ===")
        for pkg in config.extra_pip:
            print(f"  Installing {pkg}...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg], check=False)

    if not skip_models:
        print("\n=== Downloading Models ===")
        download_models(config)

        print("\n=== Verifying Models ===")
        issues = verify_models(config)
        if issues:
            print("Issues found:")
            for issue in issues:
                print(f"  {issue}")
        else:
            print("All models OK")

    print("\nSetup complete!")


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

@main.group()
def server():
    """Manage ComfyUI server."""


@server.command("start")
@click.option("--workflow", "-w", required=True, help="Workflow config name or path")
@click.option("--listen", default="127.0.0.1", help="Listen address")
@click.option("--port", default=8188, type=int, help="Listen port")
@click.option("--wait", is_flag=True, help="Wait until server is ready")
def server_start(workflow: str, listen: str, port: int, wait: bool):
    """Start ComfyUI server in the background."""
    config = _load_config(workflow)
    start_server(config, listen=listen, port=port)

    if wait:
        client = ComfyUIClient(host=listen, port=port)
        print("Waiting for server to be ready...")
        client.wait_ready()
        print("ComfyUI is ready!")


@server.command("stop")
@click.option("--workflow", "-w", required=True, help="Workflow config name or path")
def server_stop(workflow: str):
    """Stop ComfyUI server."""
    config = _load_config(workflow)
    stop_server(config)


@server.command("status")
@click.option("--workflow", "-w", required=True, help="Workflow config name or path")
def server_status_cmd(workflow: str):
    """Check if ComfyUI server is running."""
    config = _load_config(workflow)
    pid = server_status(config)
    if pid:
        print(f"ComfyUI is running (PID {pid})")
    else:
        print("ComfyUI is not running")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name or path")
@click.option(
    "--input", "inputs", multiple=True,
    help="Input file: name=path (repeatable, names from config inputs section)",
)
@click.option(
    "--batch-dir", type=click.Path(exists=True), help="Directory with input sets"
)
@click.option("--output", "-o", default="output", help="Output directory")
@click.option("--host", default="127.0.0.1", help="ComfyUI host")
@click.option("--port", default=8188, type=int, help="ComfyUI port")
@click.option(
    "--set", "overrides", multiple=True,
    help="Override param: name=value or node_id.param=value (repeatable)",
)
@click.option("--json-output", is_flag=True, help="Print result as JSON to stdout")
def run(
    workflow: str,
    inputs: tuple[str, ...],
    batch_dir: str | None,
    output: str,
    host: str,
    port: int,
    overrides: tuple[str, ...],
    json_output: bool,
):
    """Run workflow generation with input files."""
    config = _load_config(workflow)
    output_dir = Path(output) / config.name
    cli_overrides = _parse_overrides(overrides, config)
    input_files = _parse_inputs(inputs, config)

    client = ComfyUIClient(host=host, port=port)
    print("Waiting for ComfyUI server...", file=sys.stderr)
    client.wait_ready()
    print("ComfyUI is ready!\n", file=sys.stderr)

    api_workflow = prepare_workflow(config, client)

    downloaded: list[Path] = []
    if batch_dir:
        results = run_batch(
            config, client, api_workflow, Path(batch_dir), output_dir,
            cli_overrides=cli_overrides,
        )
        for paths in results.values():
            downloaded.extend(paths)
    elif input_files:
        pair_name = "_".join(p.stem for p in input_files.values())
        downloaded = run_single(
            config, client, api_workflow,
            input_files,
            output_dir, pair_name,
            cli_overrides=cli_overrides,
        )
    else:
        available = ", ".join(config.inputs.keys()) if config.inputs else "(none)"
        click.echo(
            f"Error: provide --input flags or --batch-dir.\n"
            f"Available inputs: {available}",
            err=True,
        )
        sys.exit(1)

    if json_output:
        print(json.dumps({"outputs": [str(p) for p in downloaded]}))
    else:
        print(f"\nDone! Results saved to: {output_dir}")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

@main.command("list")
def list_workflows():
    """List available workflow configurations."""
    if not CONFIGS_DIR.exists():
        click.echo(f"No configs directory at {CONFIGS_DIR}", err=True)
        return

    for f in sorted(CONFIGS_DIR.glob("*.yaml")):
        config = WorkflowConfig.from_yaml(f)
        click.echo(f"  {f.stem:20s} - {config.description}")


@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name or path")
@click.option("--host", default="127.0.0.1", help="ComfyUI host")
@click.option("--port", default=8188, type=int, help="ComfyUI port")
@click.option("--output", "-o", help="Output path (default: <workflow>_api.json)")
def convert(workflow: str, host: str, port: int, output: str | None):
    """Convert UI-format workflow to API format (requires running ComfyUI)."""
    config = _load_config(workflow)
    client = ComfyUIClient(host=host, port=port)

    if not client.is_ready():
        click.echo("Error: ComfyUI is not running. Start it first.", err=True)
        sys.exit(1)

    api_workflow = prepare_workflow(config, client)

    out_path = output or config.workflow_file.replace(".json", "_api.json")
    with open(out_path, "w") as f:
        json.dump(api_workflow, f, indent=2)

    click.echo(f"API workflow saved to: {out_path}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_overrides(
    raw: tuple[str, ...], config: WorkflowConfig
) -> dict[str, dict] | None:
    """Parse --set flags into override dict.

    Supports two formats:
      --set prompt="dancing in rain"          (semantic name from config parameters)
      --set 227.text="dancing in rain"        (raw node_id.param)
    """
    if not raw:
        return None

    overrides: dict[str, dict] = {}
    for item in raw:
        eq = item.find("=")
        if eq < 1:
            raise click.BadParameter(
                f"Invalid override: '{item}'. Use: name=value or node_id.param=value"
            )

        key = item[:eq]
        value = _coerce_value(item[eq + 1 :])

        # Check if key is a semantic parameter name
        if key in config.parameters:
            mapping = config.parameters[key]
            node_id = mapping.node_id
            param = mapping.param
        elif "." in key:
            # Raw format: node_id.param
            dot = key.find(".")
            node_id = key[:dot]
            param = key[dot + 1 :]
        else:
            available = ", ".join(config.parameters.keys()) if config.parameters else "(none defined)"
            raise click.BadParameter(
                f"Unknown parameter: '{key}'. "
                f"Available: {available}. Or use node_id.param=value format."
            )

        overrides.setdefault(node_id, {})[param] = value

    return overrides


def _coerce_value(s: str):
    """Convert string to int/float/bool if possible."""
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_inputs(
    raw: tuple[str, ...], config: WorkflowConfig
) -> dict[str, Path]:
    """Parse --input flags into {input_name: Path} dict.

    Format: --input reference_image=char.png --input reference_video=dance.mp4
    Names must match keys in the config's inputs section.
    """
    if not raw:
        return {}

    result: dict[str, Path] = {}
    for item in raw:
        eq = item.find("=")
        if eq < 1:
            raise click.BadParameter(
                f"Invalid input: '{item}'. Use: name=path"
            )

        name = item[:eq]
        path = Path(item[eq + 1:])

        if name not in config.inputs:
            available = ", ".join(config.inputs.keys()) if config.inputs else "(none)"
            raise click.BadParameter(
                f"Unknown input: '{name}'. Available: {available}"
            )

        if not path.exists():
            raise click.BadParameter(f"File not found: {path}")

        result[name] = path

    return result


def _load_config(name: str) -> WorkflowConfig:
    path = Path(name)
    if path.exists():
        return WorkflowConfig.from_yaml(path)

    path = CONFIGS_DIR / f"{name}.yaml"
    if path.exists():
        return WorkflowConfig.from_yaml(path)

    click.echo(f"Config not found: {name}", err=True)
    if CONFIGS_DIR.exists():
        click.echo(f"Available configs in {CONFIGS_DIR}:", err=True)
        for f in CONFIGS_DIR.glob("*.yaml"):
            click.echo(f"  {f.stem}", err=True)
    sys.exit(1)
