from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://console.vast.ai"
MIN_REQUEST_INTERVAL = 2.0  # seconds between requests to same endpoint


@dataclass
class Instance:
    """Represents a rented VastAI instance."""

    instance_id: int
    ssh_host: str | None = None
    ssh_port: int | None = None
    actual_status: str | None = None
    dph_total: float | None = None
    label: str | None = None


class VastAPIError(Exception):
    """Raised when a VastAI API request fails."""


class VastClient:
    """REST API client for VastAI."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or _resolve_api_key()
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {self._api_key}"
        self._last_request_time: float = 0.0

    def _throttle(self) -> None:
        """Ensure minimum interval between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an API request with throttling and error handling."""
        self._throttle()
        url = f"{BASE_URL}{path}"
        resp = self._session.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise VastAPIError(
                f"API {method} {path} returned {resp.status_code}: {resp.text}"
            )
        if not resp.content:
            return {}
        return resp.json()

    # ISO 3166 codes for European countries (EU + EEA + CH/UK).
    EU_COUNTRIES = [
        "AT", "BE", "BG", "CH", "CZ", "DE", "DK", "EE", "ES", "FI",
        "FR", "GB", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV",
        "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
    ]

    def search_offers(
        self,
        gpu_name: str,
        min_gpu_ram: int,
        disk_space: int,
        max_price: float,
        geolocation: str = "",
        extra_filters: dict | None = None,
        max_bw_price: float = 0.0,
    ) -> list[dict]:
        """Search for available GPU offers matching criteria.

        *geolocation* can be a two-letter country code, a comma-separated
        list of codes, or the shorthand ``"EU"`` for European countries.
        Pass an empty string to skip geolocation filtering.

        Returns list of offers sorted by price (cheapest first).
        """
        filters: dict = {
            "gpu_name": {"eq": gpu_name},
            "gpu_ram": {"gte": min_gpu_ram},
            "disk_space": {"gte": disk_space},
            "dph_total": {"lte": max_price},
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "order": [["dph_total", "asc"]],
            "type": "on-demand",
        }
        if geolocation:
            if geolocation.upper() == "EU":
                filters["geolocation"] = {"in": self.EU_COUNTRIES}
            else:
                codes = [c.strip().upper() for c in geolocation.split(",")]
                if len(codes) == 1:
                    filters["geolocation"] = {"eq": codes[0]}
                else:
                    filters["geolocation"] = {"in": codes}
        if extra_filters:
            filters.update(extra_filters)

        data = self._request("POST", "/api/v0/bundles/", json=filters)
        offers = data.get("offers", [])

        # Filter by bandwidth price client-side (not supported by API)
        if max_bw_price > 0:
            max_bw_per_tb = max_bw_price * 1000
            offers = [o for o in offers if o.get("internet_down_cost_per_tb", 0) <= max_bw_per_tb]

        # Re-sort by true session cost (dph + estimated 80GB model download)
        # so cheap-bandwidth hosts rank higher than expensive ones
        for o in offers:
            dl_per_gb = o.get("internet_down_cost_per_tb", 0) / 1000
            o["_true_session_cost"] = o.get("dph_total", 0) + 80 * dl_per_gb
        offers.sort(key=lambda o: o["_true_session_cost"])

        return offers

    def create_instance(
        self,
        offer_id: int,
        image: str,
        disk: int,
        label: str = "",
        onstart: str = "",
    ) -> int:
        """Rent an instance from an offer. Returns the instance ID."""
        body: dict = {
            "image": image,
            "disk": disk,
            "runtype": "ssh_direc",
            "label": label,
        }
        if onstart:
            body["onstart"] = onstart

        data = self._request("PUT", f"/api/v0/asks/{offer_id}/", json=body)

        instance_id = data.get("new_contract")
        if not instance_id:
            raise VastAPIError(f"No instance ID in response: {data}")
        return int(instance_id)

    def get_instance(self, instance_id: int) -> Instance:
        """Get details of a specific instance."""
        data = self._request("GET", f"/api/v0/instances/{instance_id}/")

        # API may return {"instances": {...}} or the instance directly
        info = data.get("instances", data)
        if isinstance(info, list):
            # Search through list for our instance
            for inst in info:
                if inst.get("id") == instance_id:
                    info = inst
                    break
            else:
                raise VastAPIError(f"Instance {instance_id} not found in response")

        return Instance(
            instance_id=instance_id,
            ssh_host=info.get("ssh_host"),
            ssh_port=info.get("ssh_port"),
            actual_status=info.get("actual_status"),
            dph_total=info.get("dph_total"),
            label=info.get("label"),
        )

    def list_instances(self) -> list[Instance]:
        """List all rented instances."""
        data = self._request("GET", "/api/v0/instances/")
        instances = data.get("instances", [])
        return [
            Instance(
                instance_id=inst["id"],
                ssh_host=inst.get("ssh_host"),
                ssh_port=inst.get("ssh_port"),
                actual_status=inst.get("actual_status"),
                dph_total=inst.get("dph_total"),
                label=inst.get("label"),
            )
            for inst in instances
        ]

    def destroy_instance(self, instance_id: int) -> None:
        """Destroy (terminate) an instance."""
        self._request("DELETE", f"/api/v0/instances/{instance_id}/")

    def start_instance(self, instance_id: int) -> None:
        """Start a stopped instance."""
        self._request(
            "PUT", f"/api/v0/instances/{instance_id}/", json={"state": "running"}
        )

    def stop_instance(self, instance_id: int) -> None:
        """Stop a running instance."""
        self._request(
            "PUT", f"/api/v0/instances/{instance_id}/", json={"state": "stopped"}
        )


def _resolve_api_key() -> str:
    """Resolve API key from environment or file."""
    key = os.environ.get("VAST_API_KEY")
    if key:
        return key.strip()

    key_file = Path.home() / ".vast_api_key"
    if key_file.exists():
        return key_file.read_text().strip()

    raise VastAPIError(
        "VastAI API key not found. Set VAST_API_KEY env var or create ~/.vast_api_key"
    )
