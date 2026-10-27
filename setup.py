"""
Setup script and packaging hooks for FUWForge.

Provides a custom build_py command that packages repository-root resources
(config, schemas, docs/contracts, templates) into wheels and distributions
without requiring duplicate or drifting files committed in git.
Restricts copied files to intended resource types and excludes scratch/cache items.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable
from setuptools import setup
from setuptools.command.build_py import build_py


def _make_resource_filter(allowed_suffixes: set[str]) -> Callable[[str, list[str]], set[str]]:
    """Return an ignore callable for shutil.copytree that excludes scratch/cache and non-resource files."""
    def _ignore(directory: str, contents: list[str]) -> set[str]:
        ignored = set()
        for name in contents:
            full_path = Path(directory) / name
            if full_path.is_dir():
                if name.startswith(".") or name in {"__pycache__", "scratch", "cache", ".pytest_cache", "runs", "dist", "build"}:
                    ignored.add(name)
            else:
                if (
                    name.startswith(".")
                    or name.endswith("~")
                    or name.endswith(".tmp")
                    or name.endswith(".bak")
                    or name.endswith(".swp")
                    or (name.startswith("#") and name.endswith("#"))
                    or full_path.suffix.lower() not in allowed_suffixes
                ):
                    ignored.add(name)
        return ignored

    return _ignore


class CustomBuildPy(build_py):
    """Package non-code runtime resources at build time."""

    def run(self) -> None:
        super().run()
        root = Path(__file__).resolve().parent

        # 1. Config, schemas, and templates packaged under src/_packaged_resources/
        resource_specs = {
            "config": {".yaml", ".yml", ".json"},
            "schemas": {".json"},
            "templates": {".md", ".yaml", ".yml", ".json"},
        }
        for dirname, suffixes in resource_specs.items():
            src_dir = root / dirname
            if src_dir.exists():
                ignore_func = _make_resource_filter(suffixes)
                # Package-internal resources under src/_packaged_resources/
                dest_pkg = Path(self.build_lib) / "src" / "_packaged_resources" / dirname
                dest_pkg.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_dir, dest_pkg, dirs_exist_ok=True, ignore=ignore_func)

        # 2. Contract documentation packaged under src/_packaged_resources/docs/contracts
        contracts_src = root / "docs" / "contracts"
        if contracts_src.exists():
            contracts_ignore = _make_resource_filter({".md"})
            dest_pkg_docs = (
                Path(self.build_lib) / "src" / "_packaged_resources" / "docs" / "contracts"
            )
            dest_pkg_docs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(contracts_src, dest_pkg_docs, dirs_exist_ok=True, ignore=contracts_ignore)

        # 3. Create __init__.py in _packaged_resources
        pkg_init = (
            Path(self.build_lib) / "src" / "_packaged_resources" / "__init__.py"
        )
        pkg_init.parent.mkdir(parents=True, exist_ok=True)
        if not pkg_init.exists():
            pkg_init.write_text(
                '"""Packaged non-code resources for FUWForge."""\n', encoding="utf-8"
            )


if __name__ == "__main__":
    setup(cmdclass={"build_py": CustomBuildPy})
