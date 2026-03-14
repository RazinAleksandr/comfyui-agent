"""CLI entry point for vast-agent — thin wrapper around VastAgentService."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from vast_agent.remote import run_ssh_interactive
from vast_agent.service import (
    NoInstanceError,
    VastAgentService,
    _load_config,
)
from vast_agent.vastai import VastAPIError

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _make_service(config_path: str | None = None) -> VastAgentService:
    config = _load_config(config_path)
    return VastAgentService(
        config=config,
        project_root=PROJECT_ROOT,
        progress_callback=lambda msg: print(msg, file=sys.stderr),
    )


def _parse_inputs(raw: tuple[str, ...]) -> dict[str, Path]:
    """Parse --input name=path pairs."""
    result: dict[str, Path] = {}
    for item in raw:
        eq = item.find("=")
        if eq < 1:
            raise click.BadParameter(f"Invalid input: '{item}'. Use: name=path")
        name = item[:eq]
        path = Path(item[eq + 1:])
        if not path.exists():
            raise click.BadParameter(f"File not found: {path}")
        result[name] = path
    return result


def _parse_overrides(raw: tuple[str, ...]) -> dict[str, str]:
    """Parse --set name=value pairs into a dict."""
    result: dict[str, str] = {}
    for item in raw:
        eq = item.find("=")
        if eq < 1:
            raise click.BadParameter(f"Invalid override: '{item}'. Use: name=value")
        result[item[:eq]] = item[eq + 1:]
    return result


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
    svc = _make_service(config_path)

    print(f"Searching for {svc.config.gpu} instance...")
    from vast_agent.vastai import VastClient
    from vast_agent.service import _search_offers, _rent_with_retry

    client = VastClient()
    offers = _search_offers(client, svc.config)
    if not offers:
        click.echo("No offers found matching criteria.", err=True)
        sys.exit(1)

    best = offers[0]
    print(f"Found {len(offers)} offers. Best: ${best.get('dph_total', 0):.3f}/hr")

    instance_id, instance = _rent_with_retry(
        client, svc.config, offers,
        log=lambda msg: print(msg, file=sys.stderr),
    )
    svc._save_state(instance_id, instance.ssh_host, instance.ssh_port, instance.dph_total)
    print(f"Instance ready! SSH: root@{instance.ssh_host} -p {instance.ssh_port}")


@main.command()
def push():
    """Push project code to remote server via rsync."""
    svc = _make_service()
    try:
        svc.push()
        print("Push complete.")
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name")
@click.option("--config", "-c", "config_path", default=None, help="VastAI config file path")
def up(workflow: str, config_path: str | None):
    """Rent instance, push code, setup, and start ComfyUI server."""
    svc = _make_service(config_path)
    try:
        result = svc.up(workflow)
        print(f"\nInstance is up and running!")
        print(f"  SSH: ssh -p {result.ssh_port} root@{result.ssh_host}")
        print(f"  Instance ID: {result.instance_id}")
        if result.dph_total:
            print(f"  Cost: ${result.dph_total}/hr")
    except VastAPIError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.option("--workflow", "-w", required=True, help="Workflow config name")
@click.option(
    "--input", "inputs", multiple=True,
    help="Input file: name=path (repeatable)",
)
@click.option(
    "--set", "overrides", multiple=True,
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
    svc = _make_service()
    input_files = _parse_inputs(inputs)
    override_dict = _parse_overrides(overrides)

    try:
        result = svc.run(
            workflow=workflow,
            inputs=input_files or None,
            overrides=override_dict or None,
            output_dir=output,
        )
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if json_output:
        print(json.dumps({"outputs": result.outputs}))
    else:
        print(f"\nDone! Results saved to: {result.output_dir}")


@main.command()
@click.option("--config", "-c", "config_path", default=None, help="VastAI config file path")
def down(config_path: str | None):
    """Stop server and destroy the instance."""
    svc = _make_service(config_path)
    try:
        svc.down()
        print("Instance destroyed.")
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
def destroy():
    """Destroy the instance without stopping server gracefully."""
    svc = _make_service()
    try:
        svc.destroy()
        print("Instance destroyed.")
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
def ssh():
    """Open an interactive SSH session to the remote server."""
    svc = _make_service()
    try:
        state = svc._load_state()
        run_ssh_interactive(state["ssh_host"], state["ssh_port"], ssh_key=svc.config.ssh_key)
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command("exec")
@click.argument("command")
def exec_cmd(command: str):
    """Run a command on the remote server."""
    svc = _make_service()
    try:
        state = svc._load_state()
        from vast_agent.remote import run_remote
        run_remote(state["ssh_host"], state["ssh_port"], command, ssh_key=svc.config.ssh_key)
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
@click.argument("path", default="output/")
@click.option("--output", "-o", default=".", help="Local destination")
def pull(path: str, output: str):
    """Download files from the remote server."""
    svc = _make_service()
    try:
        state = svc._load_state()
        from vast_agent.remote import rsync_pull
        if not path.startswith("/"):
            remote_path = f"{svc.config.remote_path}/{path}"
        else:
            remote_path = path
        rsync_pull(
            host=state["ssh_host"],
            port=state["ssh_port"],
            remote_path=remote_path,
            local_path=output,
            ssh_key=svc.config.ssh_key,
        )
        print("Pull complete.")
    except NoInstanceError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@main.command()
def status():
    """Show instance status and cost."""
    svc = _make_service()
    result = svc.status()

    if not result.instance_id:
        click.echo("No active instance.", err=True)
        sys.exit(1)

    print(f"Instance: {result.instance_id}")
    print(f"  Status: {result.actual_status or 'unknown'}")
    print(f"  SSH:    root@{result.ssh_host} -p {result.ssh_port}")
    if result.dph_total:
        print(f"  Cost:   ${result.dph_total}/hr")
    else:
        print("  Cost:   unknown")
    if result.label:
        print(f"  Label:  {result.label}")
    print(f"  SSH OK: {'yes' if result.ssh_reachable else 'no'}")

    if not result.running:
        sys.exit(1)
