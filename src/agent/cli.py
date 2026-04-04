"""studio-agent CLI — multi-command wrapper for the Avatar Factory API.

Usage:
    set -a; source .env; set +a
    PYTHONPATH=src .venv/bin/python3 -m agent.cli <command> [args]

Quick alias in shell:
    alias studio="PYTHONPATH=src .venv/bin/python3 -m agent.cli"

Required env for trend research:
    TIKTOK_MS_TOKEN     (optional) — speeds up token acquisition
Required env for generation:
    ANTHROPIC_API_KEY   — only needed if using AI-assisted review
"""
from __future__ import annotations

import json
import os
import sys
import time

import click

from agent.tools.pipeline import PipelineClient

DEFAULT_API_URL = os.environ.get("STUDIO_API_URL", "http://localhost:8000/api/v1")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--api-url", default=DEFAULT_API_URL, show_default=True, envvar="STUDIO_API_URL")
@click.option("--username", default=None, envvar="AUTH_ADMIN_USERNAME")
@click.option("--password", default=None, envvar="AUTH_ADMIN_PASSWORD")
@click.pass_context
def cli(ctx: click.Context, api_url: str, username: str | None, password: str | None) -> None:
    """AI Influencer Studio — pipeline CLI."""
    ctx.ensure_object(dict)
    username = username or "admin"
    password = password or "admin"
    try:
        ctx.obj["client"] = PipelineClient(api_url=api_url, username=username, password=password)
    except Exception as e:
        click.echo(f"Error: cannot connect to API at {api_url}: {e}", err=True)
        sys.exit(1)


def _client(ctx: click.Context) -> PipelineClient:
    return ctx.obj["client"]


def _j(data) -> str:
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Influencer commands
# ---------------------------------------------------------------------------

@cli.command("list-influencers")
@click.pass_context
def list_influencers(ctx: click.Context) -> None:
    """List all influencers (id, name, theme)."""
    for inf in _client(ctx).list_influencers():
        iid = inf.get("id") or inf.get("influencer_id", "?")
        click.echo(f"  {iid:30s}  {inf.get('name','?'):20s}  theme={inf.get('theme') or inf.get('description','?')[:60]}")


@cli.command("get-influencer")
@click.argument("influencer_id")
@click.pass_context
def get_influencer(ctx: click.Context, influencer_id: str) -> None:
    """Get full influencer profile."""
    click.echo(_j(_client(ctx).get_influencer(influencer_id)))


# ---------------------------------------------------------------------------
# Trend research
# ---------------------------------------------------------------------------

@cli.command("search-trends")
@click.argument("topic")
@click.option("--platform", default="tiktok", show_default=True,
              type=click.Choice(["tiktok", "instagram", "both"]),
              help="Platform to research trends on")
@click.option("--limit", default=60, show_default=True,
              help="Number of trending videos to analyse")
@click.option("--json-out", is_flag=True, help="Output full JSON instead of summary")
@click.pass_context
def search_trends(
    ctx: click.Context,
    topic: str,
    platform: str,
    limit: int,
    json_out: bool,
) -> None:
    """Research trending hashtags and sounds for a niche.

    Uses platform-native data (TikTok FYP + sound mining) — no paid API needed.

    Output fields:
      hashtags          — [(tag, count), ...] sorted by relevance
      niche_hashtags    — hashtags most correlated with your niche
      trending_sounds   — sounds dominating the FYP right now
      search_queries    — ready-to-paste queries for start-pipeline --hashtags
      content_patterns  — format signals (pov, tutorial, transformation…)
    """
    from agent.tools.trends import research_platform_trends

    click.echo(f"Researching {platform} trends for: {topic!r} (limit={limit})…", err=True)
    result = research_platform_trends(platform=platform, niche=topic, limit=limit)

    if json_out:
        click.echo(_j(result))
        return

    # ── Human-readable summary ─────────────────────────────────────────────
    errors = result.get("errors", [])
    if errors:
        for e in errors:
            click.echo(f"  [warn] {e}", err=True)

    vcount = result.get("video_count", 0)
    source = result.get("source", "fyp")
    dr = result.get("date_range", {})
    date_str = ""
    if dr.get("oldest") and dr.get("newest"):
        date_str = f"  {dr['oldest'][:10]} → {dr['newest'][:10]}"
    click.echo(f"\n=== TikTok Trend Report: {topic!r} ({vcount} videos · {source}{date_str}) ===\n")

    videos = result.get("videos", [])
    if videos:
        click.echo("TOP VIDEOS (by views):")
        for v in sorted(videos, key=lambda x: x.get("views", 0), reverse=True)[:8]:
            date = (v.get("published_at") or "")[:10]
            click.echo(
                f"  {v.get('views', 0):>10,}  {date}  {v.get('caption', '')[:55]}"
            )
        click.echo()

    sounds = result.get("trending_sounds", [])
    if sounds:
        click.echo("TRENDING SOUNDS (use these as search targets):")
        for s in sounds[:8]:
            score = s.get("niche_score", 0)
            click.echo(
                f"  [{s['fyp_count']:2d}x FYP | niche={score:.2f}]  "
                f"{s['title']!r} — {s['artist']}"
            )

    click.echo()
    niche_tags = result.get("niche_hashtags", [])
    if niche_tags:
        click.echo("NICHE HASHTAGS (most relevant to your topic):")
        tags_str = "  " + "  ".join(f"#{t}({c})" for t, c in niche_tags[:12])
        click.echo(tags_str)

    all_tags = result.get("hashtags", [])
    if all_tags:
        click.echo("\nALL TRENDING HASHTAGS (raw count):")
        tags_str = "  " + "  ".join(f"#{t}({c})" for t, c in all_tags[:15])
        click.echo(tags_str)

    patterns = result.get("content_patterns", [])
    if patterns:
        click.echo("\nCONTENT PATTERNS (format signals from captions):")
        click.echo("  " + "  ".join(f"{p}({c})" for p, c in patterns[:8]))

    queries = result.get("search_queries", [])
    if queries:
        click.echo("\nSEARCH QUERIES (paste into --hashtags for start-pipeline):")
        click.echo("  " + ", ".join(queries))

    click.echo()


# ---------------------------------------------------------------------------
# Pipeline commands
# ---------------------------------------------------------------------------

@cli.command("start-pipeline")
@click.argument("influencer_id")
@click.option("--hashtags", required=True,
              help="Comma-separated hashtags/search terms (from search-trends output)")
@click.option("--platforms", default="tiktok", show_default=True,
              help="Comma-separated platforms: tiktok,instagram")
@click.option("--limit", default=20, show_default=True,
              help="Videos to fetch per platform")
@click.option("--theme", default="",
              help="VLM theme override (default: influencer's own theme)")
@click.option("--mode", default="search", show_default=True,
              type=click.Choice(["search", "hashtag", "mixed", "trending"]),
              help="Fetch mode")
@click.option("--auto-review", is_flag=True,
              help="Auto-submit reviews after VLM scoring")
@click.option("--wait", is_flag=True, help="Block until pipeline finishes")
@click.pass_context
def start_pipeline(
    ctx: click.Context,
    influencer_id: str,
    hashtags: str,
    platforms: str,
    limit: int,
    theme: str,
    mode: str,
    auto_review: bool,
    wait: bool,
) -> None:
    """Start the full pipeline: ingest → download → filter → VLM → review."""
    client = _client(ctx)

    inf = client.get_influencer(influencer_id)
    vlm_theme = theme or inf.get("theme") or "influencer channel"

    tag_list = [t.strip() for t in hashtags.split(",") if t.strip()]
    platform_list = [p.strip() for p in platforms.split(",") if p.strip()]

    # Decide mode: search_terms vs hashtags
    search_terms = tag_list if mode in ("search", "mixed") else []
    ht_list = tag_list if mode in ("hashtag", "mixed") else []

    result = client.start_pipeline(
        influencer_id=influencer_id,
        hashtags=ht_list,
        search_terms=search_terms,
        platforms=platform_list,
        limit=limit,
        vlm_theme=vlm_theme,
        auto_review=auto_review,
    )

    job_id = result.get("job_id") or result.get("id", "")
    click.echo(f"Pipeline started. job_id={job_id}")
    click.echo(f"  influencer={influencer_id}  platforms={platforms}  limit={limit}")
    click.echo(f"  hashtags/terms: {tag_list}")

    if wait and job_id:
        ctx.invoke(wait_job, job_id=job_id)


@cli.command("list-runs")
@click.argument("influencer_id")
@click.option("--limit", default=10, show_default=True)
@click.pass_context
def list_runs(ctx: click.Context, influencer_id: str, limit: int) -> None:
    """List recent pipeline runs for an influencer."""
    runs = _client(ctx).list_runs(influencer_id=influencer_id, limit=limit)
    for run in runs:
        click.echo(f"  {run.get('id','?'):36s}  status={run.get('status','?'):12s}  {run.get('created_at','')[:19]}")


@cli.command("get-run")
@click.argument("run_id")
@click.argument("influencer_id")
@click.option("--json-out", is_flag=True)
@click.pass_context
def get_run(ctx: click.Context, run_id: str, influencer_id: str, json_out: bool) -> None:
    """Get pipeline run details including per-stage status."""
    run = _client(ctx).get_run(run_id=run_id, influencer_id=influencer_id)
    if json_out:
        click.echo(_j(run))
        return
    click.echo(f"Run {run_id}")
    click.echo(f"  status: {run.get('status')}")
    click.echo(f"  stages: {_j(run.get('stages', {}))}")
    videos = run.get("selected_videos") or run.get("videos") or []
    click.echo(f"  videos: {len(videos)} selected")
    for v in videos[:5]:
        fname = v.get("file_name") or v.get("url") or "?"
        click.echo(f"    - {fname}")


@cli.command("rerun-download")
@click.argument("run_id")
@click.argument("influencer_id")
@click.option("--wait", is_flag=True)
@click.pass_context
def rerun_download(ctx: click.Context, run_id: str, influencer_id: str, wait: bool) -> None:
    """Re-run the download stage for failed videos."""
    client = _client(ctx)
    result = client._post(f"/parser/runs/{run_id}/rerun-download?influencer_id={influencer_id}")
    job_id = result.get("job_id", "")
    click.echo(f"Rerun-download job_id={job_id}")
    if wait and job_id:
        ctx.invoke(wait_job, job_id=job_id)


@cli.command("rerun-filter")
@click.argument("run_id")
@click.argument("influencer_id")
@click.option("--wait", is_flag=True)
@click.pass_context
def rerun_filter(ctx: click.Context, run_id: str, influencer_id: str, wait: bool) -> None:
    """Re-run the filter stage."""
    client = _client(ctx)
    result = client._post(f"/parser/runs/{run_id}/rerun-filter?influencer_id={influencer_id}")
    job_id = result.get("job_id", "")
    click.echo(f"Rerun-filter job_id={job_id}")
    if wait and job_id:
        ctx.invoke(wait_job, job_id=job_id)


@cli.command("rerun-vlm")
@click.argument("run_id")
@click.argument("influencer_id")
@click.option("--wait", is_flag=True)
@click.pass_context
def rerun_vlm(ctx: click.Context, run_id: str, influencer_id: str, wait: bool) -> None:
    """Re-run VLM scoring (also triggers auto-review)."""
    client = _client(ctx)
    result = client._post(f"/parser/runs/{run_id}/rerun-vlm?influencer_id={influencer_id}")
    job_id = result.get("job_id", "")
    click.echo(f"Rerun-VLM job_id={job_id}")
    if wait and job_id:
        ctx.invoke(wait_job, job_id=job_id)


@cli.command("submit-review")
@click.argument("influencer_id")
@click.argument("run_id")
@click.argument("videos_json")
@click.option("--draft", is_flag=True)
@click.pass_context
def submit_review(
    ctx: click.Context,
    influencer_id: str,
    run_id: str,
    videos_json: str,
    draft: bool,
) -> None:
    """Submit review decisions. VIDEOS_JSON is a JSON array of {file_name,approved,prompt}."""
    videos = json.loads(videos_json)
    result = _client(ctx).submit_review(
        influencer_id=influencer_id, run_id=run_id, videos=videos, draft=draft
    )
    click.echo(_j(result))


@cli.command("start-generation")
@click.argument("influencer_id")
@click.argument("reference_video")
@click.option("--prompt", default="", help="Generation prompt override")
@click.option("--wait", is_flag=True)
@click.pass_context
def start_generation(
    ctx: click.Context,
    influencer_id: str,
    reference_video: str,
    prompt: str,
    wait: bool,
) -> None:
    """Start video generation for a reference video."""
    result = _client(ctx).start_generation(
        influencer_id=influencer_id,
        reference_video=reference_video,
        prompt=prompt,
    )
    job_id = result.get("job_id", "")
    click.echo(f"Generation job_id={job_id}")
    if wait and job_id:
        ctx.invoke(wait_job, job_id=job_id)


# ---------------------------------------------------------------------------
# Job commands
# ---------------------------------------------------------------------------

@cli.command("get-job")
@click.argument("job_id")
@click.pass_context
def get_job(ctx: click.Context, job_id: str) -> None:
    """Get job status and progress."""
    click.echo(_j(_client(ctx).get_job(job_id)))


@cli.command("wait-job")
@click.argument("job_id")
@click.option("--timeout", default=1800, show_default=True, help="Seconds before giving up")
@click.pass_context
def wait_job(ctx: click.Context, job_id: str, timeout: int) -> None:
    """Poll a job until it completes or fails."""
    start = time.time()

    def on_progress(msg: str) -> None:
        elapsed = int(time.time() - start)
        click.echo(f"  [{elapsed:4d}s] {msg}", err=True)

    click.echo(f"Waiting for job {job_id}…", err=True)
    try:
        job = _client(ctx).wait_for_job(job_id, timeout=timeout, on_progress=on_progress)
    except TimeoutError:
        click.echo(f"Timed out after {timeout}s", err=True)
        sys.exit(1)

    status = job.get("status")
    click.echo(f"Job {job_id}: {status}", err=True)
    click.echo(_j(job))
    if status == "failed":
        sys.exit(1)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
