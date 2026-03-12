from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ISPConfig:
    """Configuration for ISP video post-processing."""

    name: str
    description: str
    graininess: int = 40
    sharpness: int = 20
    brightness: int = -5
    vignette: int = 10

    @classmethod
    def from_yaml(cls, path: str | Path) -> ISPConfig:
        """Load ISP config from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        params = data.get("parameters", {})
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            graininess=params.get("graininess", 40),
            sharpness=params.get("sharpness", 20),
            brightness=params.get("brightness", -5),
            vignette=params.get("vignette", 10),
        )
