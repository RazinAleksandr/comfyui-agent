"""CLI entry point for `comfy-api` command."""
import click


@click.command()
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=8000, type=int, help="Bind port")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
def main(host: str, port: int, reload: bool) -> None:
    """Start the AI Influencer Studio API server."""
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=reload)
