"""HTTP client for the Avatar Factory API — wraps all pipeline operations."""
from __future__ import annotations

import os
import time
from typing import Any

import requests

DEFAULT_API_URL = "http://localhost:8000/api/v1"
_DEFAULT_TIMEOUT = 30


class PipelineClient:
    """Authenticated client for the Avatar Factory REST API."""

    def __init__(self, api_url: str = DEFAULT_API_URL, username: str = "admin", password: str = "admin"):
        self.base = api_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._login(username, password)

    def _login(self, username: str, password: str) -> None:
        r = self._session.post(
            f"{self.base}/auth/login",
            json={"username": username, "password": password},
            timeout=_DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, path: str, params: dict | None = None) -> Any:
        r = self._session.get(f"{self.base}{path}", params=params, timeout=_DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict | None = None, timeout: int = _DEFAULT_TIMEOUT) -> Any:
        r = self._session.post(f"{self.base}{path}", json=body or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # --- Influencers ---

    def list_influencers(self) -> list[dict]:
        return self._get("/influencers")

    def get_influencer(self, influencer_id: str) -> dict:
        return self._get(f"/influencers/{influencer_id}")

    # --- Pipeline ---

    def start_pipeline(
        self,
        influencer_id: str,
        hashtags: list[str],
        search_terms: list[str] | None = None,
        platforms: list[str] | None = None,
        limit: int = 20,
        vlm_theme: str = "influencer channel",
        auto_review: bool = False,
    ) -> dict:
        """Start the full 5-stage pipeline (ingest→download→filter→VLM→review)."""
        platforms = platforms or ["tiktok", "instagram"]
        selector = {
            "mode": "search" if search_terms else "hashtag",
            "hashtags": hashtags,
            "search_terms": search_terms or [],
            "require_topic_match": False,
        }
        # Determine source per platform: env override → SCRAPECREATORS_API_KEY → platform default
        sc_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
        platform_configs = {}
        for p in platforms:
            source_env = os.environ.get(f"{p.upper()}_DEFAULT_SOURCE")
            if source_env:
                source = source_env
            elif p == "tiktok" and sc_key:
                source = "scrapecreators"
            elif p == "tiktok":
                source = "tiktok_custom"
            else:
                source = "apify"
            platform_configs[p] = {
                "enabled": True,
                "source": source,
                "limit": limit,
                "selector": selector,
            }

        body = {
            "influencer_id": influencer_id,
            "platforms": platform_configs,
            "download": {"enabled": True, "force": False},
            "filter": {"enabled": True, "probe_seconds": 8, "workers": 4, "top_k": 15},
            "vlm": {
                "enabled": True,
                "max_videos": 15,
                "theme": vlm_theme,
                "sync_folders": True,
                "thresholds": {
                    "min_readiness": 7.0,
                    "min_confidence": 0.70,
                    "min_persona_fit": 6.5,
                    "max_occlusion_risk": 6.0,
                    "max_scene_cut_complexity": 6.0,
                },
            },
            "review": {"auto": auto_review},
        }
        return self._post("/parser/pipeline", body, timeout=60)

    def list_runs(self, influencer_id: str, limit: int = 10) -> list[dict]:
        return self._get("/parser/runs", params={"influencer_id": influencer_id, "limit": limit})

    def get_run(self, run_id: str, influencer_id: str) -> dict:
        return self._get(f"/parser/runs/{run_id}", params={"influencer_id": influencer_id})

    def submit_review(
        self,
        influencer_id: str,
        run_id: str,
        videos: list[dict],
        draft: bool = False,
    ) -> dict:
        """videos: list of {file_name, approved, prompt}"""
        return self._post(
            f"/parser/runs/{run_id}/review?influencer_id={influencer_id}",
            body={"videos": videos, "draft": draft},
            timeout=60,
        )

    def start_generation(
        self,
        influencer_id: str,
        reference_video: str,
        prompt: str = "",
    ) -> dict:
        body = {
            "influencer_id": influencer_id,
            "reference_video": reference_video,
            "prompt": prompt,
        }
        return self._post("/generation/run", body, timeout=60)

    # --- Jobs ---

    def get_job(self, job_id: str) -> dict:
        return self._get(f"/jobs/{job_id}")

    def wait_for_job(
        self,
        job_id: str,
        timeout: int = 1800,
        poll_interval: int = 5,
        on_progress: Any = None,
    ) -> dict:
        """Poll a job until it completes or fails. Returns the final job dict."""
        deadline = time.time() + timeout
        last_msg = None
        while time.time() < deadline:
            job = self.get_job(job_id)
            status = job.get("status")
            msg = job.get("message") or job.get("progress", {}).get("message")
            if msg and msg != last_msg:
                if on_progress:
                    on_progress(msg)
                last_msg = msg
            if status in ("completed", "failed", "cancelled"):
                return job
            time.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
