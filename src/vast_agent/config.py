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
    image: str = "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-devel"
    remote_path: str = "/workspace/comfyui-agent"
    label: str = "comfyui-agent"
    ssh_key: str = "~/.ssh/id_rsa"
    onstart: str = ""
    geolocation: str = "EU"
    extra_filters: dict = field(default_factory=dict)

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
            image=data.get("image", cls.image),
            remote_path=data.get("remote_path", cls.remote_path),
            label=data.get("label", cls.label),
            ssh_key=data.get("ssh_key", cls.ssh_key),
            onstart=data.get("onstart", cls.onstart),
            geolocation=data.get("geolocation", cls.geolocation),
            extra_filters=data.get("extra_filters", {}),
        )
