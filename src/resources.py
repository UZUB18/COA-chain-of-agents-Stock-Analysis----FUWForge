"""
Resource resolution helper for FUWForge.

Provides a single source of truth for runtime non-code resources (configuration,
schemas, contract specifications, and templates) across both developer repository
checkouts / editable installs and standalone installed wheel / sdist packages.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Root of the package source directory (src/)
_SRC_DIR = Path(__file__).resolve().parent

# If running in a repository checkout or editable install, parents[1] is the repo root
_CHECKOUT_ROOT = _SRC_DIR.parent

# Packaged resources directory inside the installed package
_PACKAGED_DIR = _SRC_DIR / "_packaged_resources"

# Top-level site-packages directory (parents[1] when installed in site-packages)
_SITE_PACKAGES_ROOT = _SRC_DIR.parent


def is_site_packages(path: Union[str, Path]) -> bool:
    """
    Return True if the specified path points to or resides in a site-packages
    or dist-packages installation directory.
    """
    try:
        resolved = Path(path).resolve()
    except Exception:
        resolved = Path(path)

    name = resolved.name.lower()
    if name in ("site-packages", "dist-packages"):
        return True
    parts = [p.lower() for p in resolved.parts]
    if "site-packages" in parts or "dist-packages" in parts:
        return True
    return False


def is_checkout_workspace() -> bool:
    """
    Return True if running directly from a repository checkout or editable install.
    A valid checkout workspace has a repo-root config directory containing depth_modes.yaml
    and is not inside site-packages.
    """
    if is_site_packages(_CHECKOUT_ROOT):
        return False
    repo_config = _CHECKOUT_ROOT / "config" / "depth_modes.yaml"
    return repo_config.is_file()


def resolve_env_path() -> Optional[Path]:
    """Project .env: explicit override, else the checkout root, else the CWD.

    Deliberately no parent-directory walk, so an unrelated ``~/.env`` (or another
    project's keys) is never picked up implicitly.
    """
    override = os.environ.get("FUWFORGE_ENV_PATH")
    candidates = [Path(override)] if override else []
    candidates += [_CHECKOUT_ROOT / ".env", Path.cwd() / ".env"]
    return next((c.resolve() for c in candidates if c.is_file()), None)


def get_resource_path(
    relative_path: Union[str, Path],
    custom_root: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Resolve a resource file path with single-source-of-truth precedence:
    1. If `custom_root` is provided (e.g. from tests or explicit CLI argument)
       and is NOT site-packages:
       - If `custom_root / relative_path` exists, return it.
       - If `custom_root` is a sub-directory and contains the file directly, return it.
    2. If running in a checkout workspace or editable install:
       - If `_CHECKOUT_ROOT / relative_path` exists, return it.
    3. If running from an installed package:
       - Check `src/_packaged_resources / relative_path`
       - Check contract doc aliases (docs/contracts <-> contracts)
    4. Fallback to candidate location.
    """
    rel = Path(relative_path)
    rel_str = str(relative_path).replace("\\", "/").lstrip("/")

    # 1. Custom root if provided (ignore if pointing to site-packages root)
    if custom_root is not None and not is_site_packages(custom_root):
        root_path = Path(custom_root)
        candidate = root_path / rel
        if candidate.exists():
            return candidate
        if len(rel.parts) > 1 and (root_path / rel.name).exists():
            return root_path / rel.name

    # 2. Checkout / editable mode
    if is_checkout_workspace():
        candidate = _CHECKOUT_ROOT / rel
        if candidate.exists():
            return candidate

    # 3. Packaged resources inside src/_packaged_resources
    if _PACKAGED_DIR.exists():
        candidate = _PACKAGED_DIR / rel
        if candidate.exists():
            return candidate

        # Handle aliases between "docs/contracts/..." and "contracts/..."
        if rel_str.startswith("docs/contracts/"):
            sub = rel_str[len("docs/contracts/"):]
            for alias in [
                _PACKAGED_DIR / "docs" / "contracts" / sub,
                _PACKAGED_DIR / "contracts" / sub,
            ]:
                if alias.exists():
                    return alias
        elif rel_str.startswith("contracts/"):
            sub = rel_str[len("contracts/"):]
            for alias in [
                _PACKAGED_DIR / "docs" / "contracts" / sub,
                _PACKAGED_DIR / "contracts" / sub,
            ]:
                if alias.exists():
                    return alias

    # 4. Default candidate path
    if is_checkout_workspace():
        return _CHECKOUT_ROOT / rel
    if _PACKAGED_DIR.exists():
        return _PACKAGED_DIR / rel
    return _CHECKOUT_ROOT / rel


def get_config_dir(custom_root: Optional[Union[str, Path]] = None) -> Path:
    """
    Return the path to the configuration directory containing depth_modes.yaml,
    focus_routing.yaml, and orchestrator.yaml.
    """
    if custom_root is not None and not is_site_packages(custom_root):
        root_path = Path(custom_root)
        if (root_path / "config" / "depth_modes.yaml").is_file():
            return root_path / "config"
        if (root_path / "depth_modes.yaml").is_file():
            return root_path

    if is_checkout_workspace():
        checkout_config = _CHECKOUT_ROOT / "config"
        if checkout_config.is_dir():
            return checkout_config

    pkg_config = _PACKAGED_DIR / "config"
    if pkg_config.is_dir():
        return pkg_config

    return _CHECKOUT_ROOT / "config" if is_checkout_workspace() else pkg_config


def get_config_path(
    filename: str,
    custom_root: Optional[Union[str, Path]] = None,
) -> Path:
    """Return the path to a specific configuration file."""
    config_dir = get_config_dir(custom_root)
    return config_dir / filename


def get_schema_dir(custom_root: Optional[Union[str, Path]] = None) -> Path:
    """Return the directory containing JSON schema files."""
    if custom_root is not None and not is_site_packages(custom_root):
        root_path = Path(custom_root)
        if (root_path / "schemas").is_dir():
            return root_path / "schemas"

    if is_checkout_workspace():
        checkout_schemas = _CHECKOUT_ROOT / "schemas"
        if checkout_schemas.is_dir():
            return checkout_schemas

    pkg_schemas = _PACKAGED_DIR / "schemas"
    if pkg_schemas.is_dir():
        return pkg_schemas

    return _CHECKOUT_ROOT / "schemas" if is_checkout_workspace() else pkg_schemas


def get_schema_path(
    filename: Union[str, Path],
    custom_root: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Return the path to a specific JSON schema file, e.g. envelope_v2_1.json,
    run_manifest_v1.json, research_packet_v1.json, etc.
    """
    fn = Path(filename).name
    return get_resource_path(f"schemas/{fn}", custom_root=custom_root)


def load_schema(
    filename: Union[str, Path],
    custom_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Load and parse a JSON schema file as a dictionary."""
    schema_path = get_schema_path(filename, custom_root=custom_root)
    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def get_contract_doc_path(
    agent_id_or_path: str,
    custom_root: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Resolve the path to a contract markdown document.
    Accepts an agent ID (e.g. 'A1', 'A2', 'A6') or relative path ('docs/contracts/A1_GOD_Gate.md').
    """
    from src.contracts import CONTRACT_DOCS

    rel_path = CONTRACT_DOCS.get(agent_id_or_path, agent_id_or_path)
    return get_resource_path(rel_path, custom_root=custom_root)


def read_resource_text(
    relative_path: Union[str, Path],
    custom_root: Optional[Union[str, Path]] = None,
) -> str:
    """Read and return the UTF-8 text content of a resource."""
    path = get_resource_path(relative_path, custom_root=custom_root)
    if not path.is_file():
        raise FileNotFoundError(f"Resource file not found: {path}")
    return path.read_text(encoding="utf-8")


def get_default_runs_dir(custom_root: Optional[Union[str, Path]] = None) -> Path:
    """
    Return a writable default directory for saving runs.

    - If custom_root is provided and is NOT site-packages, return custom_root / "runs".
    - If running in a repository checkout workspace, return _CHECKOUT_ROOT / "runs".
    - Otherwise (e.g. installed package), return Path.cwd() / "runs" to avoid
      confusing the installed package root with the writable runs directory.
    """
    if custom_root is not None:
        root_path = Path(custom_root).resolve()
        if not is_site_packages(root_path):
            return root_path / "runs"

    if is_checkout_workspace():
        return _CHECKOUT_ROOT / "runs"

    return Path.cwd() / "runs"
