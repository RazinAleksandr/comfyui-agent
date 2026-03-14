"""HTTP client for the unified backend API.

Replaces StudioClient — talks to the new /parse, /influencers, /jobs endpoints
on the same backend. The Telegram bot uses this as its sole interface to the backend.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 3.0
_POLL_TIMEOUT = 600.0
_REQUEST_TIMEOUT = 30.0


class BackendClient:
    """Async HTTP client for the unified backend."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # -- Health ---------------------------------------------------------------

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(self._url("/health"))
            resp.raise_for_status()
            return resp.json()

    # -- Parse (async job) ----------------------------------------------------

    async def start_parse(
        self,
        platforms: list[str] | None = None,
        limit: int = 10,
        source: str | None = None,
        selectors: dict | None = None,
    ) -> str:
        """Submit a parse job, return job_id."""
        body: dict = {
            "platforms": platforms or ["tiktok", "instagram"],
            "limit": limit,
        }
        if source:
            body["source"] = source
        if selectors:
            body["selectors"] = selectors

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(self._url("/api/v1/parser/run"), json=body)
            resp.raise_for_status()
            return resp.json()["job_id"]

    async def start_pipeline(
        self,
        influencer_id: str,
        platforms: list[str] | None = None,
        hashtags: list[str] | None = None,
        limit: int = 10,
    ) -> str:
        """Submit a full pipeline job, return job_id."""
        _SOURCES = {"tiktok": "tiktok_custom", "instagram": "apify"}
        platform_names = platforms or ["tiktok"]
        platform_configs: dict = {}
        for name in platform_names:
            cfg: dict = {"source": _SOURCES.get(name, "seed"), "limit": limit}
            if hashtags:
                cfg["selector"] = {"hashtags": hashtags}
            platform_configs[name] = cfg

        body: dict = {
            "influencer_id": influencer_id,
            "platforms": platform_configs,
        }
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(self._url("/api/v1/parser/pipeline"), json=body)
            resp.raise_for_status()
            return resp.json()["job_id"]

    # -- Jobs -----------------------------------------------------------------

    async def get_job(self, job_id: str) -> dict:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(self._url(f"/api/v1/jobs/{job_id}"))
            resp.raise_for_status()
            return resp.json()

    async def poll_job(self, job_id: str, timeout: float = _POLL_TIMEOUT) -> dict:
        """Poll until job completes or times out."""
        elapsed = 0.0
        while elapsed < timeout:
            info = await self.get_job(job_id)
            status = info.get("status", "")
            if status in ("completed", "failed"):
                return info
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    # -- Influencers ----------------------------------------------------------

    async def list_influencers(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(self._url("/api/v1/influencers"))
            resp.raise_for_status()
            return resp.json()

    async def get_influencer(self, influencer_id: str) -> dict:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(self._url(f"/api/v1/influencers/{influencer_id}"))
            resp.raise_for_status()
            return resp.json()

    async def upsert_influencer(self, influencer_id: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.put(self._url(f"/api/v1/influencers/{influencer_id}"), json=payload)
            resp.raise_for_status()
            return resp.json()

    # -- Generation -----------------------------------------------------------

    async def server_status(self) -> dict:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(self._url("/api/v1/generation/server/status"))
            resp.raise_for_status()
            return resp.json()

    async def server_up(self, workflow: str = "wan_animate") -> str:
        """Start GPU server. Returns job_id."""
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(
                self._url("/api/v1/generation/server/up"),
                json={"workflow": workflow},
            )
            resp.raise_for_status()
            return resp.json()["job_id"]

    async def server_down(self) -> dict:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(self._url("/api/v1/generation/server/down"))
            resp.raise_for_status()
            return resp.json()

    async def start_generation(
        self,
        influencer_id: str,
        workflow: str = "wan_animate",
        reference_image: str | None = None,
        reference_video: str | None = None,
        prompt: str = "",
        set_args: dict[str, str] | None = None,
    ) -> str:
        """Submit a generation job, return job_id."""
        body: dict = {
            "influencer_id": influencer_id,
            "workflow": workflow,
            "prompt": prompt,
        }
        if reference_image:
            body["reference_image"] = reference_image
        if reference_video:
            body["reference_video"] = reference_video
        if set_args:
            body["set_args"] = set_args

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(self._url("/api/v1/generation/run"), json=body)
            resp.raise_for_status()
            return resp.json()["job_id"]

    async def run_generation(
        self,
        influencer_id: str,
        workflow: str = "wan_animate",
        reference_image: str | None = None,
        reference_video: str | None = None,
        prompt: str = "",
        set_args: dict[str, str] | None = None,
    ) -> dict:
        """Start generation and poll until completion. Returns job result."""
        job_id = await self.start_generation(
            influencer_id=influencer_id,
            workflow=workflow,
            reference_image=reference_image,
            reference_video=reference_video,
            prompt=prompt,
            set_args=set_args,
        )
        info = await self.poll_job(job_id)
        if info.get("status") == "failed":
            raise RuntimeError(f"Generation failed: {info.get('error', 'unknown')}")
        return info.get("result", {})

    # -- Convenience: run_pipeline with polling (backward compat) -------------

    async def run_pipeline(
        self,
        influencer_id: str,
        platforms: list[str] | None = None,
        hashtags: list[str] | None = None,
        limit: int = 10,
    ) -> dict:
        """Start pipeline and poll until completion. Returns job result."""
        job_id = await self.start_pipeline(
            influencer_id=influencer_id,
            platforms=platforms,
            hashtags=hashtags,
            limit=limit,
        )
        info = await self.poll_job(job_id)
        if info.get("status") == "failed":
            raise RuntimeError(f"Pipeline failed: {info.get('error', 'unknown')}")
        return info.get("result", {})
