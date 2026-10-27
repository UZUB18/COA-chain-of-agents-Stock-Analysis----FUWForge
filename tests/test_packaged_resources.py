"""
Tests for reliable packaged resources, non-editable layouts, and wheel/sdist packaging.

Verifies:
1. Single source of truth resource resolution in checkout / editable workspace.
2. Simulated non-checkout / packaged layout resource resolution (fallback to _packaged_resources).
3. Preservation of custom roots while preventing writes to site-packages / installed package root.
4. Schema resolution in run_audit and research_packet.
5. Actual wheel and sdist building, inspection of archive contents, clean install in isolated
   environments (with host dependencies linked via .pth; CI pip-installs full dependencies separately),
   import origin verification outside checkout, strict audit verification, and default runs
   output directory resolution (implicit path) from an isolated cwd.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.research_packet import validate_packet
from src.resources import (
    get_config_dir,
    get_config_path,
    get_contract_doc_path,
    get_default_runs_dir,
    get_resource_path,
    get_schema_dir,
    get_schema_path,
    is_checkout_workspace,
    is_site_packages,
    load_schema,
    read_resource_text,
)
from src.run_audit import verify_run


def _get_clean_env() -> dict[str, str]:
    """
    Return a sanitized environment dictionary that avoids forwarding secrets,
    API keys, tokens, or credentials to test subprocesses.
    """
    sensitive_markers = ("KEY", "SECRET", "TOKEN", "AUTH", "CREDENTIAL", "PASSWORD")
    return {
        k: v
        for k, v in os.environ.items()
        if not any(marker in k.upper() for marker in sensitive_markers)
    }


def test_checkout_resource_resolution():
    """Verify that in the checkout workspace, resources resolve to repo-root files."""
    assert is_checkout_workspace(), "Should detect active checkout workspace"

    config_dir = get_config_dir()
    assert (config_dir / "depth_modes.yaml").is_file()
    assert (config_dir / "focus_routing.yaml").is_file()
    assert (config_dir / "orchestrator.yaml").is_file()

    schema_dir = get_schema_dir()
    assert (schema_dir / "envelope_v2_1.json").is_file()
    assert (schema_dir / "run_manifest_v1.json").is_file()
    assert (schema_dir / "research_packet_v1.json").is_file()

    # Schema helper
    envelope_schema_path = get_schema_path("envelope_v2_1.json")
    assert envelope_schema_path.is_file()

    # Contract helper
    contract_a1 = get_contract_doc_path("A1")
    assert contract_a1.is_file()
    assert "A1" in contract_a1.name

    # Reading helper
    content = read_resource_text("config/depth_modes.yaml")
    assert "Standard" in content or "standard" in content

    # Schema loader
    schema_dict = load_schema("envelope_v2_1.json")
    assert isinstance(schema_dict, dict)
    assert "properties" in schema_dict or "type" in schema_dict


def test_simulated_standalone_package_layout(tmp_path, monkeypatch):
    """
    Simulate a standalone non-checkout package layout where repo-root checkout
    is absent, ensuring resources are resolved from _packaged_resources and
    default runs directory defaults to Path.cwd() / 'runs'.
    """
    import src.resources as res_mod

    fake_src = tmp_path / "site-packages" / "src"
    fake_pkg_res = fake_src / "_packaged_resources"
    fake_config = fake_pkg_res / "config"
    fake_schemas = fake_pkg_res / "schemas"
    fake_contracts = fake_pkg_res / "docs" / "contracts"

    fake_config.mkdir(parents=True)
    fake_schemas.mkdir(parents=True)
    fake_contracts.mkdir(parents=True)

    # Populate fake packaged resource files
    (fake_config / "depth_modes.yaml").write_text("simulated_depth: true\n", encoding="utf-8")
    (fake_config / "focus_routing.yaml").write_text("simulated_focus: true\n", encoding="utf-8")
    (fake_config / "orchestrator.yaml").write_text("simulated_orch: true\n", encoding="utf-8")
    (fake_schemas / "envelope_v2_1.json").write_text('{"title": "SimulatedEnvelope"}', encoding="utf-8")
    (fake_schemas / "research_packet_v1.json").write_text('{"title": "SimulatedPacket"}', encoding="utf-8")
    (fake_contracts / "A1_GOD_Gate.md").write_text("# Simulated Contract A1", encoding="utf-8")

    # Monkeypatch paths in resources module to simulate non-checkout package layout
    monkeypatch.setattr(res_mod, "_CHECKOUT_ROOT", tmp_path / "non_existent_checkout")
    monkeypatch.setattr(res_mod, "_PACKAGED_DIR", fake_pkg_res)
    monkeypatch.setattr(res_mod, "_SITE_PACKAGES_ROOT", tmp_path / "site-packages")

    # In simulated mode, is_checkout_workspace should be False
    assert not res_mod.is_checkout_workspace()

    # Resolution should come from fake_pkg_res
    config_dir = res_mod.get_config_dir()
    assert config_dir == fake_config
    assert (config_dir / "depth_modes.yaml").read_text() == "simulated_depth: true\n"

    schema_file = res_mod.get_schema_path("envelope_v2_1.json")
    assert schema_file == fake_schemas / "envelope_v2_1.json"

    # Contract alias resolution (A1 maps to docs/contracts/A1_GOD_Gate.md, resolves in docs/contracts/)
    contract_file = res_mod.get_contract_doc_path("A1")
    assert contract_file.is_file()
    assert "Simulated Contract A1" in contract_file.read_text()

    # Also test contracts/ alias when resolved directly
    alias_contract = res_mod.get_resource_path("contracts/A1_GOD_Gate.md")
    assert alias_contract.is_file()
    assert alias_contract == fake_contracts / "A1_GOD_Gate.md"

    # Default runs directory in non-checkout layout must default to cwd / 'runs'
    assert res_mod.get_default_runs_dir() == Path.cwd() / "runs"


def test_runs_dir_preserves_custom_root_but_rejects_site_packages(tmp_path):
    """
    Verify runs directory resolution:
    - Explicit test directories are preserved.
    - site-packages / dist-packages are rejected and redirected.
    """
    # 1. Custom root preserved
    custom_runs = get_default_runs_dir(custom_root=tmp_path)
    assert custom_runs == tmp_path / "runs"

    # 2. site-packages rejected: redirected to checkout root in workspace, cwd in packaged
    fake_sp = Path("/opt/venv/lib/python3.12/site-packages")
    assert is_site_packages(fake_sp)
    sp_runs = get_default_runs_dir(custom_root=fake_sp)
    assert sp_runs == get_default_runs_dir()

    # 3. dist-packages rejected
    fake_dp = Path("/usr/local/lib/python3.10/dist-packages")
    assert is_site_packages(fake_dp)
    dp_runs = get_default_runs_dir(custom_root=fake_dp)
    assert dp_runs == get_default_runs_dir()


def test_orchestrator_initialization_with_simulated_package_layout(tmp_path, monkeypatch):
    """
    Verify FUWForgeOrchestrator can initialize and find config/schemas
    without an explicit project_root or when given a custom root without config.
    """
    import src.resources as res_mod

    # Setup simulated package resources
    fake_pkg_res = tmp_path / "site-packages" / "src" / "_packaged_resources"
    (fake_pkg_res / "config").mkdir(parents=True)
    (fake_pkg_res / "schemas").mkdir(parents=True)

    (fake_pkg_res / "config" / "depth_modes.yaml").write_text("quick:\n  skip_sections: []\n", encoding="utf-8")
    (fake_pkg_res / "config" / "focus_routing.yaml").write_text("{}\n", encoding="utf-8")
    (fake_pkg_res / "config" / "orchestrator.yaml").write_text("{}\n", encoding="utf-8")
    (fake_pkg_res / "schemas" / "envelope_v2_1.json").write_text('{"type": "object"}', encoding="utf-8")

    monkeypatch.setattr(res_mod, "_CHECKOUT_ROOT", tmp_path / "absent_repo")
    monkeypatch.setattr(res_mod, "_PACKAGED_DIR", fake_pkg_res)
    monkeypatch.setattr(res_mod, "_SITE_PACKAGES_ROOT", tmp_path / "site-packages")

    # Orchestrator with no project_root specified
    orch_default = FUWForgeOrchestrator(provider="mock")
    assert "quick" in orch_default.depth_modes
    assert orch_default.schema_path == fake_pkg_res / "schemas" / "envelope_v2_1.json"

    # Orchestrator with a custom directory that doesn't have config falls back to packaged
    orch_custom = FUWForgeOrchestrator(project_root=tmp_path / "custom_test", provider="mock")
    assert "quick" in orch_custom.depth_modes


def test_run_audit_and_research_packet_schema_resolution():
    """Verify run_audit and research_packet functions resolve schemas properly."""
    # run_audit verification on empty/non-existent dir
    result = verify_run(Path("/non/existent/run/dir"))
    assert not result.valid
    assert any("not a directory" in err for err in result.errors)

    # research_packet validation
    packet_errors = validate_packet({"schema_version": "research_packet_v1"})
    assert isinstance(packet_errors, list)
    assert len(packet_errors) > 0  # Missing required fields like company, but schema was found


# ---------------------------------------------------------------------------
# Actual Wheel & Sdist Distribution Tests (Offline, Dynamic Python / Sysconfig)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory):
    """
    Build actual wheel and sdist distributions into a temporary directory
    using uv build --offline --python <sys.executable>. Skip if uv is not available
    (mandatory CI python -m build lane tests wheel/sdist when uv is omitted).
    """
    uv_bin = shutil.which("uv")
    if not uv_bin:
        pytest.skip("uv CLI is not available in environment; skipping optional wheel/sdist build tests")

    dist_dir = tmp_path_factory.mktemp("dist")
    repo_root = Path(__file__).resolve().parents[1]

    proc = subprocess.run(
        [uv_bin, "build", "--offline", "--python", sys.executable, "--out-dir", str(dist_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=_get_clean_env(),
    )
    assert proc.returncode == 0, f"uv build failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))
    assert wheels, f"No wheel built in {dist_dir}"
    assert sdists, f"No sdist built in {dist_dir}"

    return {
        "dist_dir": dist_dir,
        "wheel": wheels[0],
        "sdist": sdists[0],
        "uv": uv_bin,
        "repo_root": repo_root,
    }


def _setup_isolated_venv(
    uv_bin: str,
    venv_dir: Path,
    clean_env: dict[str, str],
) -> tuple[Path, Path]:
    """
    Create an isolated virtual environment with exact current python (sys.executable),
    link host dependencies via .pth (satisfying runtime requirements offline without network),
    and return (venv_python, target_site_packages).
    """
    subprocess.run(
        [uv_bin, "venv", str(venv_dir), "--python", sys.executable, "--offline"],
        check=True,
        capture_output=True,
        env=clean_env,
    )
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        venv_python = venv_dir / "Scripts" / "python.exe"

    # Query target venv purelib dynamically
    target_sp_proc = subprocess.run(
        [str(venv_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
        env=clean_env,
    )
    target_site_packages = Path(target_sp_proc.stdout.strip())
    target_site_packages.mkdir(parents=True, exist_ok=True)

    # Link host environment dependencies so runtime requirements are satisfied offline
    # Tests share already-installed host dependencies via .pth (isolated FUWForge package
    # with reused dependencies; CI pip-installs full dependencies separately in its own step)
    host_purelib = Path(sysconfig.get_path("purelib"))
    if host_purelib.is_dir():
        (target_site_packages / "deps.pth").write_text(f"{host_purelib}\n", encoding="utf-8")

    return venv_python, target_site_packages


def test_wheel_archive_contains_required_resources(built_distributions):
    """Verify the built wheel contains all required resources in namespace-isolated paths, and no owned generic root paths."""
    wheel_path = built_distributions["wheel"]
    with zipfile.ZipFile(wheel_path, "r") as zf:
        namelist = set(zf.namelist())

    # Packaged resources inside src/_packaged_resources/
    assert "src/_packaged_resources/config/depth_modes.yaml" in namelist
    assert "src/_packaged_resources/config/focus_routing.yaml" in namelist
    assert "src/_packaged_resources/config/orchestrator.yaml" in namelist
    assert "src/_packaged_resources/schemas/envelope_v2_1.json" in namelist
    assert "src/_packaged_resources/schemas/run_manifest_v1.json" in namelist
    assert "src/_packaged_resources/schemas/research_packet_v1.json" in namelist
    assert "src/_packaged_resources/schemas/financial_fact_v1.json" in namelist
    assert "src/_packaged_resources/docs/contracts/A1_GOD_Gate.md" in namelist
    assert "src/_packaged_resources/__init__.py" in namelist

    # Assert NO owned generic root directories or files in wheel
    assert not any(name.startswith("config/") for name in namelist)
    assert not any(name.startswith("schemas/") for name in namelist)
    assert not any(name.startswith("templates/") for name in namelist)
    assert not any(name.startswith("docs/") for name in namelist)

    # Assert NO duplicate contracts alias directory
    assert not any(name.startswith("src/_packaged_resources/contracts/") for name in namelist)

    # Assert temp-file and cache filters
    assert not any(
        name.endswith("~") or name.endswith(".tmp") or name.endswith(".bak") or name.endswith(".swp")
        for name in namelist
    )
    assert not any("__pycache__" in name or ".pytest_cache" in name for name in namelist)


def test_sdist_archive_contains_required_resources(built_distributions):
    """Verify the built source distribution contains single-source-of-truth resources and build helpers."""
    sdist_path = built_distributions["sdist"]
    with tarfile.open(sdist_path, "r:gz") as tf:
        namelist = set(tf.getnames())

    prefix = sdist_path.name.replace(".tar.gz", "")
    assert f"{prefix}/pyproject.toml" in namelist
    assert f"{prefix}/setup.py" in namelist
    assert f"{prefix}/MANIFEST.in" in namelist
    assert f"{prefix}/config/depth_modes.yaml" in namelist
    assert f"{prefix}/config/focus_routing.yaml" in namelist
    assert f"{prefix}/config/orchestrator.yaml" in namelist
    assert f"{prefix}/schemas/envelope_v2_1.json" in namelist
    assert f"{prefix}/schemas/research_packet_v1.json" in namelist
    assert f"{prefix}/schemas/financial_fact_v1.json" in namelist
    assert f"{prefix}/docs/contracts/A1_GOD_Gate.md" in namelist

    # Assert temp-file and cache filters in sdist
    assert not any(
        name.endswith("~") or name.endswith(".tmp") or name.endswith(".bak") or name.endswith(".swp")
        for name in namelist
    )
    assert not any(
        "/.pytest_cache" in name or "/__pycache__" in name or "/scratch" in name or "/runs" in name
        for name in namelist
    )


def test_wheel_clean_install_and_cli_smoke(tmp_path, built_distributions):
    """
    Install the built wheel into an isolated virtual environment with reused dependencies
    linked via .pth (isolated FUWForge with reused host dependencies, not perfectly clean deps;
    CI pip-installs the full package separately in its own workflow step).

    Executes CLI from an isolated working directory completely outside the repository checkout:
    1. Verifies python import resolves strictly to installed wheel in venv, outside checkout.
    2. Runs CLI with explicit directory, strict verification, and packet export.
    3. Runs CLI with implicit default runs output directory (verifying controller source_root resolution).
    """
    uv_bin = built_distributions["uv"]
    wheel_path = built_distributions["wheel"]
    repo_root = built_distributions["repo_root"]
    clean_env = _get_clean_env()

    venv_dir = tmp_path / "wheel_venv"
    venv_python, target_site_packages = _setup_isolated_venv(uv_bin, venv_dir, clean_env)

    # Install the wheel with --no-deps offline
    install_proc = subprocess.run(
        [uv_bin, "pip", "install", "--offline", "--no-deps", str(wheel_path), "--python", str(venv_python)],
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert install_proc.returncode == 0, f"Wheel install failed: {install_proc.stderr}"

    isolated_cwd = tmp_path / "isolated_workspace"
    isolated_cwd.mkdir()

    # Assert no owned generic root directories or files in site-packages
    assert not (target_site_packages / "config").exists(), "Generic 'config' found in site-packages"
    assert not (target_site_packages / "schemas").exists(), "Generic 'schemas' found in site-packages"
    assert not (target_site_packages / "templates").exists(), "Generic 'templates' found in site-packages"
    assert not (target_site_packages / "docs").exists(), "Generic 'docs' found in site-packages"

    # Assert namespace-isolated packaged resources are all present
    pkg_res_dir = target_site_packages / "src" / "_packaged_resources"
    assert (pkg_res_dir / "config" / "depth_modes.yaml").is_file()
    assert (pkg_res_dir / "config" / "focus_routing.yaml").is_file()
    assert (pkg_res_dir / "config" / "orchestrator.yaml").is_file()
    assert (pkg_res_dir / "schemas" / "envelope_v2_1.json").is_file()
    assert (pkg_res_dir / "schemas" / "run_manifest_v1.json").is_file()
    assert (pkg_res_dir / "schemas" / "research_packet_v1.json").is_file()
    assert (pkg_res_dir / "schemas" / "financial_fact_v1.json").is_file()
    assert (pkg_res_dir / "docs" / "contracts" / "A1_GOD_Gate.md").is_file()

    # Plant adversarial external generic config and schemas in site-packages and cwd
    adversarial_sp_cfg = target_site_packages / "config"
    adversarial_sp_cfg.mkdir(parents=True, exist_ok=True)
    (adversarial_sp_cfg / "depth_modes.yaml").write_text("CORRUPTED_ADVERSARIAL_DEPTH: true\n", encoding="utf-8")

    adversarial_sp_sch = target_site_packages / "schemas"
    adversarial_sp_sch.mkdir(parents=True, exist_ok=True)
    (adversarial_sp_sch / "envelope_v2_1.json").write_text('{"corrupted": true}', encoding="utf-8")
    (adversarial_sp_sch / "financial_fact_v1.json").write_text('{"corrupted": true}', encoding="utf-8")

    adversarial_cwd_cfg = isolated_cwd / "config"
    adversarial_cwd_cfg.mkdir(parents=True, exist_ok=True)
    (adversarial_cwd_cfg / "depth_modes.yaml").write_text("CORRUPTED_CWD_DEPTH: true\n", encoding="utf-8")

    adversarial_cwd_sch = isolated_cwd / "schemas"
    adversarial_cwd_sch.mkdir(parents=True, exist_ok=True)
    (adversarial_cwd_sch / "envelope_v2_1.json").write_text('{"corrupted": true}', encoding="utf-8")
    (adversarial_cwd_sch / "financial_fact_v1.json").write_text('{"corrupted": true}', encoding="utf-8")

    # Verify python resolution in installed venv ignores adversarial files and resolves internal ones
    adv_script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from src.resources import get_config_dir, get_schema_path, get_resource_path, get_contract_doc_path\n"
        "from src.facts import validate_fact, Fact, SCHEMA_PATH\n"
        "cfg = get_config_dir()\n"
        "assert 'src/_packaged_resources/config' in str(cfg).replace('\\\\', '/'), f'Wrong config: {cfg}'\n"
        "env_s = get_schema_path('envelope_v2_1.json')\n"
        "assert 'src/_packaged_resources/schemas' in str(env_s).replace('\\\\', '/'), f'Wrong envelope schema: {env_s}'\n"
        "fact_s = get_schema_path('financial_fact_v1.json')\n"
        "assert 'src/_packaged_resources/schemas' in str(fact_s).replace('\\\\', '/'), f'Wrong fact schema: {fact_s}'\n"
        "assert 'src/_packaged_resources/schemas' in str(SCHEMA_PATH).replace('\\\\', '/'), f'Wrong SCHEMA_PATH: {SCHEMA_PATH}'\n"
        "contract = get_contract_doc_path('A1')\n"
        "assert 'src/_packaged_resources/docs/contracts' in str(contract).replace('\\\\', '/'), f'Wrong contract: {contract}'\n"
        "f = Fact(\n"
        "    id='f1', company='Test', concept='Revenues', unit='USD', filing_accession='0001-20-0001',\n"
        "    availability_time='2024-01-01T00:00:00Z', retrieval_time='2024-01-01T00:00:00Z',\n"
        "    source_location='https://sec.gov', accounting_basis='US-GAAP', extractor_version='1.0',\n"
        "    value=100.0, instant='2023-12-31'\n"
        ")\n"
        "res = validate_fact(f)\n"
        "assert res.valid, f'Fact validation failed: {res.errors}'\n"
        "from src.execution_store import ExecutionStore, validate_receipt, SCHEMA_PATH as RECEIPT_SCHEMA\n"
        "assert 'src/_packaged_resources/schemas' in str(RECEIPT_SCHEMA).replace('\\\\', '/'), f'Wrong receipt schema: {RECEIPT_SCHEMA}'\n"
        "store = ExecutionStore(':memory:')\n"
        "key = store.begin(agent_id='A1', provider='mock', model='offline', prompt_sha256='sha256:' + 'a' * 64, evidence_sha256='sha256:' + 'b' * 64, upstream=())\n"
        "receipt = store.get(key).to_dict()\n"
        "assert validate_receipt(receipt).valid, 'Installed receipt schema validation failed'\n"
        "receipt['state'] = 'invalid_state_unknown'\n"
        "assert not validate_receipt(receipt).valid, 'Installed receipt schema ignored invalid state'\n"
        "store.close()\n"
        "print('ADVERSARIAL_SHADOW_CHECK_OK')\n"
    )
    proc_adv = subprocess.run(
        [str(venv_python), "-c", adv_script],
        cwd=isolated_cwd,
        capture_output=True,
        text=True,
        check=True,
        env=clean_env,
    )
    assert "ADVERSARIAL_SHADOW_CHECK_OK" in proc_adv.stdout

    # Verify import origin is strictly inside target venv site-packages, not repository checkout
    check_import = subprocess.run(
        [str(venv_python), "-c", "import src; print(src.__file__)"],
        cwd=isolated_cwd,
        capture_output=True,
        text=True,
        check=True,
        env=clean_env,
    )
    imported_path = check_import.stdout.strip()
    assert str(venv_dir) in imported_path, f"Expected import from {venv_dir}, got {imported_path}"
    assert not imported_path.startswith(str(repo_root / "src")), f"Import leaked from repo checkout: {imported_path}"

    fuwforge_bin = venv_dir / "bin" / "fuwforge"
    if not fuwforge_bin.exists():
        fuwforge_bin = venv_dir / "Scripts" / "fuwforge.exe"
    assert fuwforge_bin.is_file(), f"CLI binary not found at {fuwforge_bin}"

    # 1. CLI Mock Run (explicit run_dir)
    run_dir = isolated_cwd / "smoke_run"
    cmd_run = [
        str(fuwforge_bin),
        "run",
        str(run_dir),
        "--provider",
        "mock",
        "--ticker",
        "TEST",
        "--company",
        "Test Corp",
        "--analysis-date",
        "2025-01-31",
    ]
    proc_run = subprocess.run(cmd_run, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_run.returncode == 0, f"fuwforge run failed:\n{proc_run.stdout}\n{proc_run.stderr}"
    assert (run_dir / "run_summary.json").is_file(), "run_summary.json not generated"
    assert (run_dir / "run_manifest.json").is_file(), "run_manifest.json not generated"

    # 2. CLI Verify-Run with --strict
    cmd_verify = [str(fuwforge_bin), "verify-run", str(run_dir), "--strict"]
    proc_verify = subprocess.run(cmd_verify, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_verify.returncode == 0, f"fuwforge verify-run --strict failed:\n{proc_verify.stdout}\n{proc_verify.stderr}"
    assert "Overall state: verified" in proc_verify.stdout or "Audit passed" in proc_verify.stdout

    # 3. CLI Export-Packet
    packet_path = isolated_cwd / "research_packet.json"
    cmd_export = [str(fuwforge_bin), "export-packet", str(run_dir), "--output", str(packet_path)]
    proc_export = subprocess.run(cmd_export, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_export.returncode == 0, f"fuwforge export-packet failed:\n{proc_export.stdout}\n{proc_export.stderr}"
    assert packet_path.is_file(), "research_packet.json not created"

    packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet_data.get("schema_version") == "research_packet_v1"
    assert packet_data.get("audit", {}).get("overall_state") == "verified"

    # 4. Default runs output check (implicit path): verifies controller get_default_runs_dir resolution
    cmd_run_implicit = [
        str(fuwforge_bin),
        "run",
        "--provider",
        "mock",
        "--ticker",
        "WHEEL_DEF",
        "--company",
        "Wheel Def Corp",
        "--analysis-date",
        "2025-01-31",
        "--run-id",
        "run_wheel_implicit",
    ]
    proc_implicit = subprocess.run(cmd_run_implicit, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_implicit.returncode == 0, f"Implicit fuwforge run failed:\n{proc_implicit.stdout}\n{proc_implicit.stderr}"
    implicit_run_dir = isolated_cwd / "runs" / "run_wheel_implicit"
    assert implicit_run_dir.is_dir(), f"Expected implicit run directory at {implicit_run_dir}"
    assert (implicit_run_dir / "run_summary.json").is_file(), "Implicit run_summary.json not found"
    assert (implicit_run_dir / "run_manifest.json").is_file(), "Implicit run_manifest.json not found"

    # Strict verify on the implicit output run
    cmd_verify_implicit = [str(fuwforge_bin), "verify-run", str(implicit_run_dir), "--strict"]
    proc_verify_impl = subprocess.run(cmd_verify_implicit, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_verify_impl.returncode == 0, f"Implicit verify-run --strict failed:\n{proc_verify_impl.stdout}\n{proc_verify_impl.stderr}"


def test_sdist_clean_install_and_cli_smoke(tmp_path, built_distributions):
    """
    Install the built source distribution into an isolated virtual environment with reused dependencies
    linked via .pth (isolated FUWForge with reused host dependencies, not perfectly clean deps;
    CI pip-installs the full package separately in its own workflow step).

    Executes CLI from an isolated working directory completely outside the repository checkout:
    1. Verifies python import resolves strictly to installed sdist in venv, outside checkout.
    2. Runs CLI with explicit directory, strict verification, and packet export.
    3. Runs CLI with implicit default runs output directory (verifying controller source_root resolution).
    """
    uv_bin = built_distributions["uv"]
    sdist_path = built_distributions["sdist"]
    repo_root = built_distributions["repo_root"]
    clean_env = _get_clean_env()

    venv_dir = tmp_path / "sdist_venv"
    venv_python, target_site_packages = _setup_isolated_venv(uv_bin, venv_dir, clean_env)

    # Install sdist with --no-deps offline
    install_proc = subprocess.run(
        [uv_bin, "pip", "install", "--offline", "--no-deps", str(sdist_path), "--python", str(venv_python)],
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert install_proc.returncode == 0, f"Sdist install failed: {install_proc.stderr}"

    isolated_cwd = tmp_path / "isolated_sdist_workspace"
    isolated_cwd.mkdir()

    # Assert no owned generic root directories or files in site-packages
    assert not (target_site_packages / "config").exists(), "Generic 'config' found in site-packages"
    assert not (target_site_packages / "schemas").exists(), "Generic 'schemas' found in site-packages"
    assert not (target_site_packages / "templates").exists(), "Generic 'templates' found in site-packages"
    assert not (target_site_packages / "docs").exists(), "Generic 'docs' found in site-packages"

    # Assert namespace-isolated packaged resources are all present
    pkg_res_dir = target_site_packages / "src" / "_packaged_resources"
    assert (pkg_res_dir / "config" / "depth_modes.yaml").is_file()
    assert (pkg_res_dir / "config" / "focus_routing.yaml").is_file()
    assert (pkg_res_dir / "config" / "orchestrator.yaml").is_file()
    assert (pkg_res_dir / "schemas" / "envelope_v2_1.json").is_file()
    assert (pkg_res_dir / "schemas" / "run_manifest_v1.json").is_file()
    assert (pkg_res_dir / "schemas" / "research_packet_v1.json").is_file()
    assert (pkg_res_dir / "schemas" / "financial_fact_v1.json").is_file()
    assert (pkg_res_dir / "docs" / "contracts" / "A1_GOD_Gate.md").is_file()

    # Plant adversarial external generic config and schemas in site-packages and cwd
    adversarial_sp_cfg = target_site_packages / "config"
    adversarial_sp_cfg.mkdir(parents=True, exist_ok=True)
    (adversarial_sp_cfg / "depth_modes.yaml").write_text("CORRUPTED_ADVERSARIAL_DEPTH: true\n", encoding="utf-8")

    adversarial_sp_sch = target_site_packages / "schemas"
    adversarial_sp_sch.mkdir(parents=True, exist_ok=True)
    (adversarial_sp_sch / "envelope_v2_1.json").write_text('{"corrupted": true}', encoding="utf-8")
    (adversarial_sp_sch / "financial_fact_v1.json").write_text('{"corrupted": true}', encoding="utf-8")

    adversarial_cwd_cfg = isolated_cwd / "config"
    adversarial_cwd_cfg.mkdir(parents=True, exist_ok=True)
    (adversarial_cwd_cfg / "depth_modes.yaml").write_text("CORRUPTED_CWD_DEPTH: true\n", encoding="utf-8")

    adversarial_cwd_sch = isolated_cwd / "schemas"
    adversarial_cwd_sch.mkdir(parents=True, exist_ok=True)
    (adversarial_cwd_sch / "envelope_v2_1.json").write_text('{"corrupted": true}', encoding="utf-8")
    (adversarial_cwd_sch / "financial_fact_v1.json").write_text('{"corrupted": true}', encoding="utf-8")

    # Verify python resolution in installed venv ignores adversarial files and resolves internal ones
    adv_script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from src.resources import get_config_dir, get_schema_path, get_resource_path, get_contract_doc_path\n"
        "from src.facts import validate_fact, Fact, SCHEMA_PATH\n"
        "cfg = get_config_dir()\n"
        "assert 'src/_packaged_resources/config' in str(cfg).replace('\\\\', '/'), f'Wrong config: {cfg}'\n"
        "env_s = get_schema_path('envelope_v2_1.json')\n"
        "assert 'src/_packaged_resources/schemas' in str(env_s).replace('\\\\', '/'), f'Wrong envelope schema: {env_s}'\n"
        "fact_s = get_schema_path('financial_fact_v1.json')\n"
        "assert 'src/_packaged_resources/schemas' in str(fact_s).replace('\\\\', '/'), f'Wrong fact schema: {fact_s}'\n"
        "assert 'src/_packaged_resources/schemas' in str(SCHEMA_PATH).replace('\\\\', '/'), f'Wrong SCHEMA_PATH: {SCHEMA_PATH}'\n"
        "contract = get_contract_doc_path('A1')\n"
        "assert 'src/_packaged_resources/docs/contracts' in str(contract).replace('\\\\', '/'), f'Wrong contract: {contract}'\n"
        "f = Fact(\n"
        "    id='f1', company='Test', concept='Revenues', unit='USD', filing_accession='0001-20-0001',\n"
        "    availability_time='2024-01-01T00:00:00Z', retrieval_time='2024-01-01T00:00:00Z',\n"
        "    source_location='https://sec.gov', accounting_basis='US-GAAP', extractor_version='1.0',\n"
        "    value=100.0, instant='2023-12-31'\n"
        ")\n"
        "res = validate_fact(f)\n"
        "assert res.valid, f'Fact validation failed: {res.errors}'\n"
        "from src.execution_store import ExecutionStore, validate_receipt, SCHEMA_PATH as RECEIPT_SCHEMA\n"
        "assert 'src/_packaged_resources/schemas' in str(RECEIPT_SCHEMA).replace('\\\\', '/'), f'Wrong receipt schema: {RECEIPT_SCHEMA}'\n"
        "store = ExecutionStore(':memory:')\n"
        "key = store.begin(agent_id='A1', provider='mock', model='offline', prompt_sha256='sha256:' + 'a' * 64, evidence_sha256='sha256:' + 'b' * 64, upstream=())\n"
        "receipt = store.get(key).to_dict()\n"
        "assert validate_receipt(receipt).valid, 'Installed receipt schema validation failed'\n"
        "receipt['state'] = 'invalid_state_unknown'\n"
        "assert not validate_receipt(receipt).valid, 'Installed receipt schema ignored invalid state'\n"
        "store.close()\n"
        "print('ADVERSARIAL_SHADOW_CHECK_OK')\n"
    )
    proc_adv = subprocess.run(
        [str(venv_python), "-c", adv_script],
        cwd=isolated_cwd,
        capture_output=True,
        text=True,
        check=True,
        env=clean_env,
    )
    assert "ADVERSARIAL_SHADOW_CHECK_OK" in proc_adv.stdout

    # Verify import origin is strictly inside target venv site-packages, not repository checkout
    check_import = subprocess.run(
        [str(venv_python), "-c", "import src; print(src.__file__)"],
        cwd=isolated_cwd,
        capture_output=True,
        text=True,
        check=True,
        env=clean_env,
    )
    imported_path = check_import.stdout.strip()
    assert str(venv_dir) in imported_path, f"Expected import from {venv_dir}, got {imported_path}"
    assert not imported_path.startswith(str(repo_root / "src")), f"Import leaked from repo checkout: {imported_path}"

    fuwforge_bin = venv_dir / "bin" / "fuwforge"
    if not fuwforge_bin.exists():
        fuwforge_bin = venv_dir / "Scripts" / "fuwforge.exe"
    assert fuwforge_bin.is_file(), f"CLI binary not found at {fuwforge_bin}"

    # 1. CLI Mock Run (explicit run_dir)
    run_dir = isolated_cwd / "sdist_smoke_run"
    cmd_run = [
        str(fuwforge_bin),
        "run",
        str(run_dir),
        "--provider",
        "mock",
        "--ticker",
        "SDIST",
        "--company",
        "Sdist Corp",
        "--analysis-date",
        "2025-01-31",
    ]
    proc_run = subprocess.run(cmd_run, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_run.returncode == 0, f"sdist fuwforge run failed:\n{proc_run.stdout}\n{proc_run.stderr}"
    assert (run_dir / "run_summary.json").is_file(), "run_summary.json not generated"
    assert (run_dir / "run_manifest.json").is_file(), "run_manifest.json not generated"

    # 2. CLI Verify-Run with --strict
    cmd_verify = [str(fuwforge_bin), "verify-run", str(run_dir), "--strict"]
    proc_verify = subprocess.run(cmd_verify, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_verify.returncode == 0, f"sdist fuwforge verify-run --strict failed:\n{proc_verify.stdout}\n{proc_verify.stderr}"
    assert "Overall state: verified" in proc_verify.stdout or "Audit passed" in proc_verify.stdout

    # 3. CLI Export-Packet
    packet_path = isolated_cwd / "sdist_packet.json"
    cmd_export = [str(fuwforge_bin), "export-packet", str(run_dir), "--output", str(packet_path)]
    proc_export = subprocess.run(cmd_export, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_export.returncode == 0, f"sdist export-packet failed:\n{proc_export.stdout}\n{proc_export.stderr}"
    assert packet_path.is_file(), "research_packet.json not created"

    packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet_data.get("schema_version") == "research_packet_v1"
    assert packet_data.get("audit", {}).get("overall_state") == "verified"

    # 4. Default runs output check (implicit path): verifies controller get_default_runs_dir resolution
    cmd_run_implicit = [
        str(fuwforge_bin),
        "run",
        "--provider",
        "mock",
        "--ticker",
        "SDIST_IMP",
        "--company",
        "Sdist Implicit Corp",
        "--analysis-date",
        "2025-01-31",
        "--run-id",
        "run_sdist_implicit",
    ]
    proc_implicit = subprocess.run(cmd_run_implicit, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_implicit.returncode == 0, f"Implicit sdist fuwforge run failed:\n{proc_implicit.stdout}\n{proc_implicit.stderr}"
    implicit_run_dir = isolated_cwd / "runs" / "run_sdist_implicit"
    assert implicit_run_dir.is_dir(), f"Expected implicit run directory at {implicit_run_dir}"
    assert (implicit_run_dir / "run_summary.json").is_file(), "Implicit run_summary.json not found"
    assert (implicit_run_dir / "run_manifest.json").is_file(), "Implicit run_manifest.json not found"

    # Strict verify on the implicit output run
    cmd_verify_implicit = [str(fuwforge_bin), "verify-run", str(implicit_run_dir), "--strict"]
    proc_verify_impl = subprocess.run(cmd_verify_implicit, cwd=isolated_cwd, capture_output=True, text=True, env=clean_env)
    assert proc_verify_impl.returncode == 0, f"Implicit sdist verify-run --strict failed:\n{proc_verify_impl.stdout}\n{proc_verify_impl.stderr}"


def test_adversarial_external_generic_files_do_not_shadow_resources(tmp_path, monkeypatch):
    """
    Direct in-process unit test verifying that adversarial generic directories in site-packages
    or cwd cannot shadow packaged resources, while explicit custom roots and checkout remain functional.
    """
    import src.resources as res_mod
    from src.facts import Fact, validate_fact

    fake_sp = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    fake_pkg_res = fake_sp / "src" / "_packaged_resources"
    (fake_pkg_res / "config").mkdir(parents=True)
    (fake_pkg_res / "schemas").mkdir(parents=True)
    (fake_pkg_res / "docs" / "contracts").mkdir(parents=True)

    # Valid packaged files
    (fake_pkg_res / "config" / "depth_modes.yaml").write_text("valid_depth: true\n", encoding="utf-8")
    (fake_pkg_res / "schemas" / "envelope_v2_1.json").write_text('{"title": "ValidEnvelope"}', encoding="utf-8")
    (fake_pkg_res / "schemas" / "financial_fact_v1.json").write_text('{"title": "ValidFact"}', encoding="utf-8")
    (fake_pkg_res / "docs" / "contracts" / "A1_GOD_Gate.md").write_text("# Valid Contract", encoding="utf-8")

    # Adversarial files in site-packages root
    adv_sp_cfg = fake_sp / "config"
    adv_sp_cfg.mkdir()
    (adv_sp_cfg / "depth_modes.yaml").write_text("ADVERSARIAL_SP: true\n", encoding="utf-8")
    adv_sp_sch = fake_sp / "schemas"
    adv_sp_sch.mkdir()
    (adv_sp_sch / "envelope_v2_1.json").write_text('{"adversarial": true}', encoding="utf-8")
    (adv_sp_sch / "financial_fact_v1.json").write_text('{"adversarial": true}', encoding="utf-8")

    # Simulate installed environment outside checkout
    monkeypatch.setattr(res_mod, "_CHECKOUT_ROOT", fake_sp)
    monkeypatch.setattr(res_mod, "_PACKAGED_DIR", fake_pkg_res)
    monkeypatch.setattr(res_mod, "_SITE_PACKAGES_ROOT", fake_sp)

    assert not res_mod.is_checkout_workspace()

    # 1. Without custom_root: must resolve to packaged resources, not adversarial site-packages
    cfg = res_mod.get_config_dir()
    assert cfg == fake_pkg_res / "config"
    assert "valid_depth" in (cfg / "depth_modes.yaml").read_text()

    env_schema = res_mod.get_schema_path("envelope_v2_1.json")
    assert env_schema == fake_pkg_res / "schemas" / "envelope_v2_1.json"
    assert "ValidEnvelope" in env_schema.read_text()

    fact_schema = res_mod.get_schema_path("financial_fact_v1.json")
    assert fact_schema == fake_pkg_res / "schemas" / "financial_fact_v1.json"

    contract_doc = res_mod.get_contract_doc_path("A1")
    assert contract_doc == fake_pkg_res / "docs" / "contracts" / "A1_GOD_Gate.md"

    # Alias contracts/ resolution
    alias_doc = res_mod.get_resource_path("contracts/A1_GOD_Gate.md")
    assert alias_doc == fake_pkg_res / "docs" / "contracts" / "A1_GOD_Gate.md"

    # 2. Even when custom_root is explicitly passed pointing to site-packages (e.g. from PROJECT_ROOT),
    # it must reject site-packages and resolve internal resources
    cfg_sp = res_mod.get_config_dir(custom_root=fake_sp)
    assert cfg_sp == fake_pkg_res / "config"

    env_schema_sp = res_mod.get_schema_path("envelope_v2_1.json", custom_root=fake_sp)
    assert env_schema_sp == fake_pkg_res / "schemas" / "envelope_v2_1.json"

    # 3. Explicit genuine custom_root override (not in site-packages) must still be honored
    genuine_override = tmp_path / "genuine_override"
    (genuine_override / "config").mkdir(parents=True)
    (genuine_override / "config" / "depth_modes.yaml").write_text("override_depth: true\n", encoding="utf-8")
    cfg_override = res_mod.get_config_dir(custom_root=genuine_override)
    assert cfg_override == genuine_override / "config"
    assert "override_depth" in (cfg_override / "depth_modes.yaml").read_text()
