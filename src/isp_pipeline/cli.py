from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from isp_pipeline.config import ISPConfig
from isp_pipeline.processor import process_directory, process_video

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


@click.group()
def main():
    """ISP video post-processing pipeline."""


@main.command()
@click.option("--config", "-c", default="isp_postprocess", help="ISP config name or path")
@click.option("--input", "-i", "input_path", required=True, help="Input video file or directory")
@click.option("--set", "overrides", multiple=True, help="Override param: name=value (repeatable)")
@click.option("--json-output", is_flag=True, help="Print result as JSON to stdout")
@click.option("--log-level", default="INFO", help="Logging level")
def run(
    config: str,
    input_path: str,
    overrides: tuple[str, ...],
    json_output: bool,
    log_level: str,
):
    """Run ISP post-processing on video(s).

    Output is saved in-place: postprocessed_<name> next to the source file.
    """
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=getattr(logging, log_level.upper()),
    )

    cfg = _load_config(config)
    params = _apply_overrides(cfg, overrides)

    input_p = Path(input_path)

    print(f"ISP Post-processing: {cfg.name}", file=sys.stderr)
    print(f"  {cfg.description}", file=sys.stderr)
    print(f"  graininess={params['graininess']}, sharpness={params['sharpness']}, "
          f"brightness={params['brightness']}, vignette={params['vignette']}",
          file=sys.stderr)

    if input_p.is_file():
        out_path = input_p.parent / f"postprocessed_{input_p.name}"
        result = process_video(
            input_p, out_path, **params,
        )
        results = [result]
    elif input_p.is_dir():
        results = process_directory(
            input_p, **params,
        )
    else:
        click.echo(f"Error: {input_path} is not a file or directory", err=True)
        sys.exit(1)

    if json_output:
        print(json.dumps({"outputs": [str(p) for p in results]}))
    else:
        print(f"\nDone! {len(results)} video(s) postprocessed:", file=sys.stderr)
        for p in results:
            print(f"  {p}", file=sys.stderr)


@main.command("list")
def list_configs():
    """List available ISP configurations."""
    if not CONFIGS_DIR.exists():
        click.echo(f"No configs directory at {CONFIGS_DIR}", err=True)
        return

    for f in sorted(CONFIGS_DIR.glob("isp_*.yaml")):
        cfg = ISPConfig.from_yaml(f)
        click.echo(f"  {f.stem:20s} - {cfg.description}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(name: str) -> ISPConfig:
    path = Path(name)
    if path.exists():
        return ISPConfig.from_yaml(path)

    path = CONFIGS_DIR / f"{name}.yaml"
    if path.exists():
        return ISPConfig.from_yaml(path)

    click.echo(f"Config not found: {name}", err=True)
    if CONFIGS_DIR.exists():
        click.echo(f"Available ISP configs in {CONFIGS_DIR}:", err=True)
        for f in CONFIGS_DIR.glob("isp_*.yaml"):
            click.echo(f"  {f.stem}", err=True)
    sys.exit(1)


def _coerce_value(s: str) -> int | float | str:
    """Convert string to int/float if possible."""
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _apply_overrides(cfg: ISPConfig, raw: tuple[str, ...]) -> dict:
    """Apply --set overrides to config params and return as dict."""
    params = {
        "graininess": cfg.graininess,
        "sharpness": cfg.sharpness,
        "brightness": cfg.brightness,
        "vignette": cfg.vignette,
    }
    valid_keys = set(params.keys())
    for item in raw:
        eq = item.find("=")
        if eq < 1:
            raise click.BadParameter(
                f"Invalid override: '{item}'. Use: name=value"
            )
        key = item[:eq]
        value = _coerce_value(item[eq + 1:])
        if key not in valid_keys:
            raise click.BadParameter(
                f"Unknown parameter: '{key}'. Available: {', '.join(sorted(valid_keys))}"
            )
        if not isinstance(value, (int, float)):
            raise click.BadParameter(
                f"Parameter '{key}' must be numeric, got: '{item[eq + 1:]}'"
            )
        params[key] = int(round(value))
    return params
