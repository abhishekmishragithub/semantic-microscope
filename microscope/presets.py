"""Load a YAML preset and turn it into Jev wire questions.

Bounds enforced here are the spec's (Score 2..10 levels, Choice 2..255 options). The API itself
only requires >= 2 Score levels; see docs/jev-api-observed.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"
KINDS = ("noul", "score", "choice")


@dataclass(frozen=True)
class Dimension:
    name: str
    kind: str
    label: str
    instructions: str
    criteria: Any = None

    def to_question(self) -> dict[str, Any]:
        q: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            q["criteria"] = self.criteria
        return q

    @property
    def n_levels(self) -> int | None:
        return len(self.criteria) if self.kind == "score" else None


@dataclass(frozen=True)
class Preset:
    name: str
    document_kind: str
    dimensions: list[Dimension] = field(default_factory=list)

    def questions(self) -> dict[str, dict[str, Any]]:
        return {d.name: d.to_question() for d in self.dimensions}

    def digest(self) -> str:
        blob = json.dumps(
            {"document_kind": self.document_kind, "q": self.questions()}, sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "document_kind": self.document_kind,
            "dimensions": {
                d.name: {"type": d.kind, "label": d.label, **d.to_question()}
                for d in self.dimensions
            },
        }


def _validate(name: str, spec: dict[str, Any]) -> Dimension:
    kind = spec.get("type")
    if kind not in KINDS:
        raise ValueError(f"dimension {name!r}: type must be one of {KINDS}, got {kind!r}")
    instructions = spec.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError(f"dimension {name!r}: instructions must be a non-empty string")
    criteria = spec.get("criteria")
    if kind == "score":
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise ValueError(f"dimension {name!r}: score criteria must be a list of 2..10 levels")
        criteria = [str(c) for c in criteria]
    elif kind == "choice":
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
            raise ValueError(f"dimension {name!r}: choice criteria must be a map of 2..255 options")
        criteria = {str(k): (None if v is None else str(v)) for k, v in criteria.items()}
    elif criteria is not None:
        if not isinstance(criteria, dict) or set(criteria) - {"true", "false", True, False}:
            raise ValueError(f"dimension {name!r}: noul criteria may only have true/false keys")
        criteria = {str(k).lower(): str(v) for k, v in criteria.items()}
    return Dimension(name, kind, str(spec.get("label", name)), instructions.strip(), criteria)


def load_preset(name_or_path: str) -> Preset:
    path = Path(name_or_path)
    if not path.exists():
        path = PRESETS_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no preset {name_or_path!r} (looked in {PRESETS_DIR})")
    raw = yaml.safe_load(path.read_text())
    dims = raw.get("dimensions") or {}
    if not dims:
        raise ValueError(f"preset {path}: no dimensions")
    if len(dims) > 9:
        raise ValueError(
            f"preset {path}: more than 9 dimensions, keyboard 1-9 would not cover them"
        )
    return Preset(
        name=str(raw.get("name", path.stem)),
        document_kind=str(raw["document_kind"]),
        dimensions=[_validate(k, v) for k, v in dims.items()],
    )
