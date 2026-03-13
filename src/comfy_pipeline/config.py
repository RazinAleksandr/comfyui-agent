from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CustomNode:
    name: str
    url: str


@dataclass
class Model:
    path: str
    url: str
    min_size: int = 0


@dataclass
class InputMapping:
    node_id: str
    param: str


@dataclass
class OutputMapping:
    node_id: str
    type: str
    name: str = ""


@dataclass
class ParamMapping:
    """Maps a semantic name to a specific node parameter."""
    node_id: str
    param: str


@dataclass
class CharacterConfig:
    """Per-character personal LoRA preset."""
    lora_high: str = ""
    lora_low: str = ""
    lora_high_strength: float = 0.89
    lora_low_strength: float = 0.89


@dataclass
class WorkflowConfig:
    name: str
    description: str
    workflow_file: str
    comfyui_path: str
    comfyui_repo: str
    custom_nodes: list[CustomNode]
    models: list[Model]
    inputs: dict[str, InputMapping]
    outputs: list[OutputMapping]
    parameters: dict[str, ParamMapping] = field(default_factory=dict)
    overrides: dict[str, dict] = field(default_factory=dict)
    extra_pip: list[str] = field(default_factory=list)
    max_video_seconds: float = 0  # 0 = no limit
    characters: dict[str, CharacterConfig] = field(default_factory=dict)

    def character_set_args(self, character_id: str) -> list[str]:
        """Return --set key=value pairs for the given character's LoRAs."""
        char = self.characters.get(character_id)
        if not char:
            return []
        args: list[str] = []
        if char.lora_high:
            args.append(f"lora_high={char.lora_high}")
        if char.lora_low:
            args.append(f"lora_low={char.lora_low}")
        args.append(f"lora_high_strength={char.lora_high_strength}")
        args.append(f"lora_low_strength={char.lora_low_strength}")
        return args

    @classmethod
    def from_yaml(cls, path: str | Path) -> WorkflowConfig:
        with open(path) as f:
            data = yaml.safe_load(f)

        config_path = Path(path).resolve()
        # Resolve workflow_file relative to project root (config's parent's parent)
        # e.g. configs/wan_animate.yaml → project_root / workflows/...
        project_root = config_path.parent.parent
        workflow_file = str(project_root / data["workflow_file"])

        comfyui = data.get("comfyui", {})

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            workflow_file=workflow_file,
            comfyui_path=comfyui.get("path", "/workspace/ComfyUI"),
            comfyui_repo=comfyui.get(
                "repo", "https://github.com/comfyanonymous/ComfyUI.git"
            ),
            custom_nodes=[CustomNode(**n) for n in data.get("custom_nodes", [])],
            models=[Model(**m) for m in data.get("models", [])],
            inputs={
                k: InputMapping(**v) for k, v in data.get("inputs", {}).items()
            },
            outputs=[OutputMapping(**o) for o in data.get("outputs", [])],
            parameters={
                k: ParamMapping(**v) for k, v in data.get("parameters", {}).items()
            },
            overrides=data.get("overrides", {}),
            extra_pip=comfyui.get("extra_pip", []),
            max_video_seconds=float(data.get("max_video_seconds", 0)),
            characters={
                k: CharacterConfig(**v) for k, v in data.get("characters", {}).items()
            },
        )
