from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class VastConfig:
    """VastAI instance configuration."""

    gpu: str = "RTX 5090"
    min_gpu_ram: int = 32000  # MB
    disk_space: int = 150  # GB
    max_price: float = 0.50  # $/hr
    max_bw_price: float = 0.0  # $/GB download; 0 = no limit
    image: str = "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-devel"
    remote_path: str = "/workspace/avatar-factory"
    label: str = "avatar-factory"
    ssh_key: str = "~/.ssh/id_rsa"
    onstart: str = ""
    geolocation: str = "EU"
    extra_filters: dict = field(default_factory=dict)
    health_check_interval: int = 120  # seconds between background server health checks (0 = disabled)
    search_retry_delay: int = 30  # seconds between offer search retries when no offers found
    max_search_attempts: int = 20  # max search retries (~10 min at 30s interval)

    @classmethod
    def from_yaml(cls, path: str | Path) -> VastConfig:
        """Load config from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(
            gpu=data.get("gpu", cls.gpu),
            min_gpu_ram=data.get("min_gpu_ram", cls.min_gpu_ram),
            disk_space=data.get("disk_space", cls.disk_space),
            max_price=data.get("max_price", cls.max_price),
            max_bw_price=data.get("max_bw_price", cls.max_bw_price),
            image=data.get("image", cls.image),
            remote_path=data.get("remote_path", cls.remote_path),
            label=data.get("label", cls.label),
            ssh_key=data.get("ssh_key", cls.ssh_key),
            onstart=data.get("onstart", cls.onstart),
            geolocation=data.get("geolocation", cls.geolocation),
            extra_filters=data.get("extra_filters", {}),
            health_check_interval=data.get("health_check_interval", cls.health_check_interval),
            search_retry_delay=data.get("search_retry_delay", cls.search_retry_delay),
            max_search_attempts=data.get("max_search_attempts", cls.max_search_attempts),
        )
