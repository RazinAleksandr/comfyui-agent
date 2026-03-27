from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModelEntry:
    """A downloadable model with optional HuggingFace repo metadata."""

    name: str
    path: str = ""
    url: str = ""
    min_size: int = 0
    type: str = "url"  # "url", "hf_repo", or "rife_zip"
    hf_repo: str = ""
    hf_subfolder: str = ""


@dataclass
class LoraEntry:
    """A LoRA adapter with path and strength."""

    path: str
    strength: float = 1.0
    name: str = ""


@dataclass
class CharacterConfig:
    """Per-character LoRA preset."""

    loras: list[LoraEntry] = field(default_factory=list)


@dataclass
class X2VConfig:
    """Configuration for the LightX2V video generation pipeline.

    Loads from a YAML config file and provides methods to build
    LightX2V-compatible inference configs and LoRA lists.
    """

    engine: str = "lightx2v"
    repo_url: str = ""
    repo_path: str = "/workspace/LightX2V"
    extra_pip: list[str] = field(default_factory=list)
    models: list[ModelEntry] = field(default_factory=list)
    preprocessing: dict[str, Any] = field(default_factory=dict)
    inference: dict[str, Any] = field(default_factory=dict)
    postprocess: dict[str, Any] = field(default_factory=dict)
    isp: dict[str, Any] = field(default_factory=dict)
    refinement: dict[str, Any] = field(default_factory=dict)
    lora_configs: list[LoraEntry] = field(default_factory=list)
    characters: dict[str, CharacterConfig] = field(default_factory=dict)
    parameters: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> X2VConfig:
        """Load configuration from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        lightx2v = data.get("lightx2v", {})

        models = [
            ModelEntry(**m) for m in data.get("models", [])
        ]

        lora_configs = [
            LoraEntry(**lc) for lc in data.get("lora_configs", [])
        ]

        characters = {}
        for char_id, char_data in data.get("characters", {}).items():
            loras = [LoraEntry(**l) for l in char_data.get("loras", [])]
            characters[char_id] = CharacterConfig(loras=loras)

        return cls(
            engine=data.get("engine", "lightx2v"),
            repo_url=lightx2v.get("repo_url", ""),
            repo_path=lightx2v.get("repo_path", "/workspace/LightX2V"),
            extra_pip=lightx2v.get("extra_pip", []),
            models=models,
            preprocessing=data.get("preprocessing", {}),
            inference=data.get("inference", {}),
            postprocess=data.get("postprocess", {}),
            isp=data.get("isp", {}),
            refinement=data.get("refinement", {}),
            lora_configs=lora_configs,
            characters=characters,
            parameters=data.get("parameters", {}),
            outputs=data.get("outputs", {}),
        )

    def get_model(self, name: str) -> ModelEntry | None:
        """Look up a model entry by name."""
        for m in self.models:
            if m.name == name:
                return m
        return None

    def get_model_path(self, name: str) -> str:
        """Get the relative path for a named model."""
        model = self.get_model(name)
        if model is None:
            raise KeyError(f"Model not found: {name}")
        return model.path

    def get_model_url(self, name: str) -> str:
        """Get the download URL for a named model."""
        model = self.get_model(name)
        if model is None:
            raise KeyError(f"Model not found: {name}")
        return model.url

    def build_inference_config(
        self, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build a LightX2V-compatible inference config dict.

        Returns a dict matching the LightX2V JSON config format
        (infer_steps, sample_guide_scale, target_video_length, etc.)
        with lora_configs merged in.
        """
        config = copy.deepcopy(self.inference)

        # Add lora_configs (base only — character LoRAs added separately)
        lora_list = self.build_lora_configs()
        if lora_list:
            config["lora_configs"] = lora_list

        if overrides:
            # If overrides contain lora_configs, merge them
            overrides = dict(overrides)  # shallow copy to avoid mutating caller
            override_loras = overrides.pop("lora_configs", None)
            config.update(overrides)
            if override_loras is not None:
                config["lora_configs"] = override_loras

        return config

    def build_lora_configs(
        self, character_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Build merged LoRA config list for LightX2V.

        Combines base lora_configs with character-specific LoRAs.
        Each entry: {"path": "loras/name.safetensors", "strength": float}
        """
        result: list[dict[str, Any]] = []

        # Base LoRAs
        for lora in self.lora_configs:
            result.append({"path": lora.path, "strength": lora.strength})

        # Character LoRAs
        if character_id:
            char = self.characters.get(character_id)
            if char:
                for lora in char.loras:
                    result.append({
                        "path": lora.path,
                        "strength": lora.strength,
                    })

        return result

    def character_set_args(self, character_id: str) -> dict[str, Any]:
        """Return dict with lora_configs key for the given character.

        Mirrors WorkflowConfig.character_set_args() but returns
        LightX2V-format LoRA list instead of CLI --set pairs.
        """
        char = self.characters.get(character_id)
        if not char:
            return {}
        lora_list = self.build_lora_configs(character_id)
        return {"lora_configs": lora_list}

    def is_refinement_enabled(self) -> bool:
        """Check if the optional refinement stage is enabled."""
        return bool(self.refinement.get("enabled", False))
