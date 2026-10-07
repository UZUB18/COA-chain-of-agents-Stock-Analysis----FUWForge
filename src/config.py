"""
Configuration loaders for the FUWForge orchestrator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - dependency optional at runtime
    yaml = None  # type: ignore


def _load_yaml(path: Path) -> Dict[str, Any]:
    """
    Load YAML from path, falling back to JSON parsing or companion .json file if PyYAML is unavailable.
    """
    if yaml is not None:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}

    json_companion = path.with_suffix(".json")
    if json_companion.exists():
        return json.loads(json_companion.read_text(encoding="utf-8"))

    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
        raise RuntimeError(
            f"Unable to parse {path}. Install PyYAML (`pip install pyyaml`) "
            "or provide JSON-formatted configuration."
        ) from exc


@dataclass(frozen=True)
class DepthModeConfig:
    name: str
    payload: Dict[str, Any]


def load_depth_modes(config_dir: Path) -> Dict[str, DepthModeConfig]:
    """
    Load depth mode definitions from config/depth_modes.yaml.
    """
    file_path = config_dir / "depth_modes.yaml"
    data = _load_yaml(file_path)
    return {
        key: DepthModeConfig(name=key, payload=value) for key, value in data.items()
    }


def load_focus_routing(config_dir: Path) -> Dict[str, Dict[str, Any]]:
    """
    Load focus routing instructions from config/focus_routing.yaml.
    """
    file_path = config_dir / "focus_routing.yaml"
    return _load_yaml(file_path)


def load_orchestrator_config(config_dir: Path) -> Dict[str, Any]:
    """
    Load orchestrator run settings from config/orchestrator.yaml.
    """
    file_path = config_dir / "orchestrator.yaml"
    return _load_yaml(file_path)


def load_mapping(path: Path) -> Dict[str, Any]:
    """
    Load an arbitrary YAML (preferred) or JSON mapping from disk.
    """
    return _load_yaml(path)
