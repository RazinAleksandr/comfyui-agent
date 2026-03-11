from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default timeouts (seconds)
# ---------------------------------------------------------------------------

_TIMEOUT_PIPELINE = 600.0  # full pipeline: ingest → download → filter → VLM select


# ---------------------------------------------------------------------------
# Studio API client
# ---------------------------------------------------------------------------


class StudioClient:
    """Async HTTP client for the AI_Influencer_studio REST API."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    # -- helpers -------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # -- public API ----------------------------------------------------------

    async def run_pipeline(
        self,
        influencer_id: str,
        platforms: list[str] | None = None,
        hashtags: list[str] | None = None,
        limit: int = 20,
        source: str = "tiktok_custom",
    ) -> dict:
        """Run the full pipeline (ingest → download → filter → VLM select).

        POST /api/v1/pipeline/run
        Returns PipelineRunOut with platforms[].selected_dir.
        """
        platform_names = platforms or ["tiktok"]
        platform_configs: dict = {}
        for name in platform_names:
            cfg: dict = {"source": source, "limit": limit}
            if hashtags:
                cfg["selector"] = {"hashtags": hashtags}
            platform_configs[name] = cfg

        body: dict = {
            "influencer_id": influencer_id,
            "platforms": platform_configs,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT_PIPELINE) as client:
            resp = await client.post(self._url("/api/v1/pipeline/run"), json=body)
            resp.raise_for_status()
            return resp.json()
