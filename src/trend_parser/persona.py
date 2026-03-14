from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PersonaProfile:
    persona_id: str
    name: str
    summary: str
    visual_features: list[str] = field(default_factory=list)
    style_keywords: list[str] = field(default_factory=list)
    content_preferences: list[str] = field(default_factory=list)
    substitution_constraints: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> PersonaProfile:
        return cls(
            persona_id=str(payload.get("persona_id") or "default-persona"),
            name=str(payload.get("name") or "Persona"),
            summary=str(payload.get("summary") or ""),
            visual_features=[str(v) for v in (payload.get("visual_features") or []) if str(v).strip()],
            style_keywords=[str(v) for v in (payload.get("style_keywords") or []) if str(v).strip()],
            content_preferences=[str(v) for v in (payload.get("content_preferences") or []) if str(v).strip()],
            substitution_constraints=[
                str(v) for v in (payload.get("substitution_constraints") or []) if str(v).strip()
            ],
            avoid=[str(v) for v in (payload.get("avoid") or []) if str(v).strip()],
        )

    def to_dict(self) -> dict:
        return {
            "persona_id": self.persona_id,
            "name": self.name,
            "summary": self.summary,
            "visual_features": self.visual_features,
            "style_keywords": self.style_keywords,
            "content_preferences": self.content_preferences,
            "substitution_constraints": self.substitution_constraints,
            "avoid": self.avoid,
        }

    def to_prompt_block(self) -> str:
        sections: list[str] = [
            f"Persona ID: {self.persona_id}",
            f"Persona Name: {self.name}",
            f"Persona Summary: {self.summary}",
        ]

        def _add_list(title: str, values: list[str]) -> None:
            if not values:
                sections.append(f"{title}: (none)")
                return
            joined = "\n".join([f"- {value}" for value in values])
            sections.append(f"{title}:\n{joined}")

        _add_list("Visual Features", self.visual_features)
        _add_list("Style Keywords", self.style_keywords)
        _add_list("Content Preferences", self.content_preferences)
        _add_list("Substitution Constraints", self.substitution_constraints)
        _add_list("Avoid", self.avoid)
        return "\n".join(sections)


def load_persona(persona_path: Path | None) -> PersonaProfile | None:
    if persona_path is None:
        return None
    if not persona_path.exists():
        raise FileNotFoundError(f"Persona file not found: {persona_path}")

    payload = json.loads(persona_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Persona file must contain a JSON object: {persona_path}")
    return PersonaProfile.from_dict(payload)


def save_persona(persona: PersonaProfile, persona_path: Path) -> Path:
    persona_path.parent.mkdir(parents=True, exist_ok=True)
    persona_path.write_text(json.dumps(persona.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")
    return persona_path
