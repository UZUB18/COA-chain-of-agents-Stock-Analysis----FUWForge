"""
CLI workflow tests:
1. `--input` YAML supplies run parameters (with CLI flags overriding).
2. Invalid `--weights` overrides are rejected; valid ones change the scorecard.
3. `seal` + `verify-run` close the loop on controller-mode envelopes.
4. `screen` and `diff` consume comparison_fields across runs.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import controller
from src.controller import build_parser
from src.envelope import Envelope
from src.run_semantic_audit import build_run_manifest
from src.utils.checksum import envelope_content_checksum


def _cli(args, timeout=None):
    return subprocess.run(
        [sys.executable, "-m", "src.controller", *args],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _snapshot_modes(root: Path) -> dict[str, int]:
    return {
        str(path.relative_to(root)): path.stat().st_mode & 0o7777
        for path in root.rglob("*")
        if path.is_file()
    }


def _snapshot_xattrs(root: Path) -> dict[str, dict[str, bytes]]:
    return {
        str(path.relative_to(root)): {
            name: os.getxattr(path, name) for name in os.listxattr(path)
        }
        for path in root.rglob("*")
        if path.is_file()
    }


def _sealed_synthetic_run(
    run_dir: Path,
    *,
    envelope_agents=("A1", "A7"),
    manifest_agents=None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    envelopes = {}
    payloads = {}
    for agent in envelope_agents:
        data = {
            "schema_version": "2.1",
            "agent": {"id": agent, "name": f"Agent {agent}"},
            "timestamp": "2026-02-01T00:00:00Z",
            "params": {
                "ticker": "SEAL",
                "analysis_date": "2026-02-01",
                "depth": "Quick",
            },
            "gates": {"passed": True},
            "sections_md": {f"SECTION_{agent}": f"{agent} section"},
            "artifacts": {},
            "sources": [],
            "warnings": [],
            "metrics": {},
            "confidence": 0.8,
            "upstream": [],
        }
        if agent == "A7":
            a1_checksum = payloads["A1"]["content_checksum"]
            data["upstream"] = [{"agent": "A1", "content_checksum": a1_checksum}]
        data["content_checksum"] = envelope_content_checksum(data)
        payloads[agent] = data
        (run_dir / f"{agent.lower()}_envelope.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        envelopes[agent] = Envelope.from_dict(data)

    summary = {
        "status": "completed",
        "provider": "mock",
        "params": {
            "ticker": "SEAL",
            "analysis_date": "2026-02-01",
            "depth": "Quick",
        },
    }
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (run_dir / "SEAL_2026-02-01_report.md").write_text(
        "Synthetic sealed report.\n", encoding="utf-8"
    )
    (run_dir / "SEAL_2026-02-01_artifacts.json").write_text(
        json.dumps({"A7": payloads["A7"]}, indent=2), encoding="utf-8"
    )
    (run_dir / "SEAL_2026-02-01_provenance.log").write_text(
        "[2026-02-01T00:00:00Z] [ORCH] synthetic provenance\n",
        encoding="utf-8",
    )
    recorded = set(manifest_agents or envelope_agents)
    manifest = build_run_manifest(
        run_dir,
        {agent: env for agent, env in envelopes.items() if agent in recorded},
        run_id=run_dir.name,
        ticker="SEAL",
        analysis_date="2026-02-01",
        status="completed",
        summary=summary,
    )
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def test_run_with_input_file_and_weight_override():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_input_"))
    try:
        input_file = tmp / "input.yaml"
        input_file.write_text(
            "inputs:\n"
            "  company: Input Corp\n"
            "  ticker: INPUT\n"
            "  analysis_date: 2026-02-01\n"
            "  depth: Standard\n",
            encoding="utf-8",
        )
        res = _cli(
            [
                "run",
                str(tmp / "run_in"),
                "--input",
                str(input_file),
                "--provider",
                "mock",
            ]
        )
        assert res.returncode == 0, res.stderr
        assert (tmp / "run_in" / "INPUT_2026-02-01_report.md").exists()

        # CLI flags override the input file.
        res2 = _cli(
            [
                "run",
                str(tmp / "run_override"),
                "--input",
                str(input_file),
                "--ticker",
                "OVERRIDE",
                "--depth",
                "Quick",
                "--provider",
                "mock",
            ]
        )
        assert res2.returncode == 0, res2.stderr
        assert (tmp / "run_override" / "OVERRIDE_2026-02-01_report.md").exists()

        # Missing ticker anywhere is an error.
        empty = tmp / "empty.yaml"
        empty.write_text("inputs: {}\n", encoding="utf-8")
        res3 = _cli(
            ["run", str(tmp / "run_bad"), "--input", str(empty), "--provider", "mock"]
        )
        assert res3.returncode != 0
        assert "ticker" in (res3.stderr + res3.stdout)

        # Invalid weights are rejected before any agent runs.
        res4 = _cli(
            [
                "run",
                str(tmp / "run_weights"),
                "--ticker",
                "BADW",
                "--weights",
                '{"Financials": 0.9}',
            ]
        )
        assert res4.returncode != 0
        assert "weights" in (res4.stderr + res4.stdout).lower()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_weights_override_changes_scorecard():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_weights_"))
    try:
        res = _cli(
            [
                "run",
                str(tmp),
                "--ticker",
                "WEIGHTED",
                "--analysis-date",
                "2026-02-01",
                "--provider",
                "mock",
                "--weights",
                '{"Financials": 0.3, "Moat": 0.15, "Growth": 0.15, "Valuation": 0.15, "MgmtGov": 0.1, "RiskInverse": 0.1, "ESG": 0.025, "Market": 0.025}',
            ]
        )
        assert res.returncode == 0, res.stderr
        a7 = json.loads((tmp / "a7_envelope.json").read_text(encoding="utf-8"))
        assert a7["artifacts"]["Scorecard"]["Financials"]["weight"] == 0.3
        assert abs(a7["artifacts"]["Scorecard"]["Overall"]["score"] - 7.9) < 0.01
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_seal_and_verify_controller_envelopes():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_seal_"))
    try:
        res = _cli(
            [
                "run",
                str(tmp),
                "--ticker",
                "SEALME",
                "--analysis-date",
                "2026-02-01",
                "--provider",
                "mock",
            ]
        )
        assert res.returncode == 0, res.stderr
        for path in tmp.glob("*_envelope.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("content_checksum", None)
            data.pop("upstream", None)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        assert _cli(["verify-run", str(tmp)]).returncode == 0
        assert _cli(["verify-run", str(tmp), "--strict"]).returncode != 0

        strict_json = _cli(["verify-run", str(tmp), "--strict", "--json"])
        assert strict_json.returncode != 0
        audit = json.loads(strict_json.stdout)
        assert audit["overall_state"] == "failed"
        assert audit["errors"]

        seal = _cli(["seal", str(tmp), "--force"])
        assert seal.returncode == 0, seal.stderr
        assert _cli(["verify-run", str(tmp), "--strict"]).returncode == 0

        # Modifying an envelope causes normal seal to refuse, but --force succeeds
        a2_file = tmp / "a2_envelope.json"
        a2_data = json.loads(a2_file.read_text(encoding="utf-8"))
        a2_data["artifacts"]["MoatTrend"] = "Stable"
        a2_file.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")

        refuse = _cli(["seal", str(tmp)])
        assert refuse.returncode != 0
        assert "refusing to re-seal" in (refuse.stderr + refuse.stdout)

        forced = _cli(["seal", str(tmp), "--force"])
        assert forced.returncode == 0, forced.stderr
        assert _cli(["verify-run", str(tmp), "--strict"]).returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_seal_refuses_edited_leaf_with_checksum_stripped(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("edited after sealing")
    a7["extension_payload"] = {"preserve": "unknown raw key"}
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path)])

    assert refused.returncode != 0
    message = refused.stdout + refused.stderr
    assert "A7" in message
    assert "manifest" in message.lower()
    assert "refusing to re-seal" in message.lower()
    assert _snapshot_tree(tmp_path) == before

    forced = _cli(["seal", str(tmp_path), "--force"])
    assert forced.returncode == 0, forced.stderr
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    sealed_a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    assert manifest["roles"]["A7"] == sealed_a7["content_checksum"]
    assert sealed_a7["extension_payload"] == {"preserve": "unknown raw key"}
    assert "A7" in manifest["resealed"]["changed_roles"]
    assert "digest" in manifest["resealed"]["means"].lower()


def test_seal_refuses_missing_manifest_recorded_role(tmp_path):
    _sealed_synthetic_run(tmp_path)
    (tmp_path / "a7_envelope.json").unlink()
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path)])

    assert refused.returncode != 0
    assert "A7" in (refused.stdout + refused.stderr)
    assert _snapshot_tree(tmp_path) == before

    forced = _cli(["seal", str(tmp_path), "--force"])
    assert forced.returncode == 0, forced.stderr
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert "A7" not in manifest["roles"]
    assert manifest["resealed"]["removed_roles"] == ["A7"]


def test_seal_refuses_unrecorded_role_addition(tmp_path):
    _sealed_synthetic_run(tmp_path, manifest_agents=("A1",))
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extension_provenance"] = {"preserve": "manifest extension"}
    manifest["resealed"] = {
        "at": "2026-02-01T00:00:00Z",
        "changed_outputs": ["recorded earlier"],
        "means": "recorded provenance",
    }
    custom_output = tmp_path / "custom.note"
    custom_output.write_text("custom output\n", encoding="utf-8")
    custom_record = {
        "path": custom_output.name,
        "sha256": "sha256:" + hashlib.sha256(custom_output.read_bytes()).hexdigest(),
        "bytes": custom_output.stat().st_size,
    }
    manifest["outputs"]["custom"] = custom_record
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path)])

    assert refused.returncode != 0
    assert "A7" in (refused.stdout + refused.stderr)
    assert _snapshot_tree(tmp_path) == before

    forced = _cli(["seal", str(tmp_path), "--force"])
    assert forced.returncode == 0, forced.stderr
    assert "certifies the edited bytes" in (forced.stdout + forced.stderr)
    rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "A7" in rebuilt["roles"]
    assert rebuilt["extension_provenance"] == {"preserve": "manifest extension"}
    assert rebuilt["outputs"]["custom"] == custom_record
    assert rebuilt["resealed"]["added_roles"] == ["A7"]
    assert rebuilt["resealed"]["previous"] == manifest["resealed"]


def test_force_refreshes_changed_custom_output_record(tmp_path):
    _sealed_synthetic_run(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    custom_path = tmp_path / "custom.note"
    custom_path.write_text("original custom output\n", encoding="utf-8")
    manifest["outputs"]["custom"] = {
        "path": custom_path.name,
        "sha256": "sha256:" + hashlib.sha256(custom_path.read_bytes()).hexdigest(),
        "bytes": custom_path.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    custom_path.write_text("edited custom output\n", encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path)])
    assert refused.returncode != 0
    assert "custom" in (refused.stdout + refused.stderr).lower()
    assert _snapshot_tree(tmp_path) == before

    forced = _cli(["seal", str(tmp_path), "--force"])
    assert forced.returncode == 0, forced.stderr
    rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert rebuilt["outputs"]["custom"]["sha256"] == (
        "sha256:" + hashlib.sha256(custom_path.read_bytes()).hexdigest()
    )
    assert any(
        "custom" in item
        for item in rebuilt["resealed"]["changed_outputs"]
    )


@pytest.mark.parametrize("label", ["report", "artifacts", "provenance", "summary"])
def test_seal_refuses_unanchored_final_output_but_force_records_it(
    tmp_path, label
):
    _sealed_synthetic_run(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["outputs"].pop(label)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    output_path = tmp_path / record["path"]
    output_path.write_bytes(output_path.read_bytes() + b" ")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path)])

    assert refused.returncode != 0
    message = (refused.stdout + refused.stderr).lower()
    assert label in message
    assert "not recorded" in message or "unanchored" in message
    assert _snapshot_tree(tmp_path) == before

    forced = _cli(["seal", str(tmp_path), "--force"])
    assert forced.returncode == 0, forced.stderr
    rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert rebuilt["outputs"][label]["sha256"] == (
        "sha256:" + hashlib.sha256(output_path.read_bytes()).hexdigest()
    )
    assert any(label in item for item in rebuilt["resealed"]["changed_outputs"])


@pytest.mark.parametrize(
    ("label", "substitute"),
    [
        ("report", "000_report.md"),
        ("artifacts", "000_artifacts.json"),
        ("provenance", "000_provenance.log"),
    ],
)
def test_seal_refuses_lexically_earlier_output_substitute(
    tmp_path, label, substitute
):
    _sealed_synthetic_run(tmp_path)
    (tmp_path / substitute).write_text("unanchored substitute\n", encoding="utf-8")
    original_record = json.loads(
        (tmp_path / "run_manifest.json").read_text(encoding="utf-8")
    )["outputs"].get(label)
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path)])

    assert refused.returncode != 0
    message = refused.stdout + refused.stderr
    assert substitute in message
    assert "not recorded" in message.lower() or "unanchored" in message.lower()
    assert _snapshot_tree(tmp_path) == before

    forced = _cli(["seal", str(tmp_path), "--force"])
    assert forced.returncode == 0, forced.stderr
    rebuilt = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert rebuilt["outputs"][label]["path"] == substitute
    assert any(
        substitute in item
        for item in rebuilt["resealed"]["changed_outputs"]
    )
    if original_record is not None:
        assert (tmp_path / original_record["path"]).exists()


@pytest.mark.parametrize("label", ["report", "artifacts", "summary", "provenance"])
@pytest.mark.parametrize("extra", [[], ["--force"]], ids=["plain", "forced"])
def test_seal_never_certifies_missing_completed_output(tmp_path, label, extra):
    _sealed_synthetic_run(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["outputs"].pop(label)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (tmp_path / record["path"]).unlink()
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path), *extra])

    assert refused.returncode != 0
    message = (refused.stdout + refused.stderr).lower()
    assert "completed" in message
    assert label in message
    assert _snapshot_tree(tmp_path) == before


def test_seal_detects_cycles_before_force_fixpoint(tmp_path):
    for agent, parent in (("A2", "A3"), ("A3", "A2")):
        data = {
            "schema_version": "2.1",
            "agent": {"id": agent, "name": f"Agent {agent}"},
            "params": {"ticker": "CYCLE", "analysis_date": "2026-02-01"},
            "gates": {},
            "sections_md": {},
            "artifacts": {},
            "sources": [],
            "warnings": [],
            "metrics": {},
            "upstream": [{"agent": parent, "content_checksum": "sha256:" + "0" * 64}],
        }
        (tmp_path / f"{agent.lower()}_envelope.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    try:
        result = _cli(["seal", str(tmp_path), "--force"], timeout=3)
    except subprocess.TimeoutExpired:
        pytest.fail("seal hung instead of rejecting the upstream cycle")

    assert result.returncode != 0
    assert "cycle detected" in (result.stdout + result.stderr).lower()


def test_seal_rebuild_failure_changes_no_files(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires rebuild")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    (tmp_path / "run_summary.json").write_text("{invalid", encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path), "--force"])

    assert refused.returncode != 0
    assert "could not rebuild" in (refused.stdout + refused.stderr).lower()
    assert _snapshot_tree(tmp_path) == before


def test_seal_refuses_malformed_manifest_even_with_force(tmp_path):
    _sealed_synthetic_run(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text("{invalid", encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    for extra in ([], ["--force"]):
        refused = _cli(["seal", str(tmp_path), *extra])
        assert refused.returncode != 0
        message = (refused.stdout + refused.stderr).lower()
        assert "malformed" in message
        assert "cannot rebuild safely" in message
        assert _snapshot_tree(tmp_path) == before


@pytest.mark.parametrize("target", ["run_manifest.json", "a7_envelope.json"])
@pytest.mark.parametrize("extra", [[], ["--force"]])
def test_seal_refuses_invalid_utf8_json_without_writes(tmp_path, target, extra):
    _sealed_synthetic_run(tmp_path)
    (tmp_path / target).write_bytes(b"\xff")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path), *extra])

    assert refused.returncode != 0
    assert "could not read envelope" in (refused.stdout + refused.stderr).lower() or (
        "unreadable run manifest" in (refused.stdout + refused.stderr).lower()
    )
    assert _snapshot_tree(tmp_path) == before


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    "target", ["run_manifest.json", "a7_envelope.json", "run_summary.json"]
)
@pytest.mark.parametrize("extra", [[], ["--force"]], ids=["plain", "forced"])
def test_seal_rejects_nonstandard_json_constants(tmp_path, constant, target, extra):
    _sealed_synthetic_run(tmp_path)
    path = tmp_path / target
    data = json.loads(path.read_text(encoding="utf-8"))
    data["extension_metric"] = float(constant)
    path.write_text(json.dumps(data, indent=2, allow_nan=True), encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path), *extra])

    assert refused.returncode != 0
    assert "non-standard json constant" in (
        refused.stdout + refused.stderr
    ).lower()
    assert _snapshot_tree(tmp_path) == before


@pytest.mark.parametrize("overflow", ["1e9999", "-1e9999"])
@pytest.mark.parametrize(
    "target", ["run_manifest.json", "a7_envelope.json", "run_summary.json"]
)
@pytest.mark.parametrize("extra", [[], ["--force"]], ids=["plain", "forced"])
def test_seal_rejects_overflow_json_numbers(tmp_path, overflow, target, extra):
    _sealed_synthetic_run(tmp_path)
    path = tmp_path / target
    data = json.loads(path.read_text(encoding="utf-8"))
    data["extension_overflow"] = "__OVERFLOW_SENTINEL__"
    path.write_text(
        json.dumps(data, indent=2).replace(
            '"__OVERFLOW_SENTINEL__"', overflow
        ),
        encoding="utf-8",
    )
    before = _snapshot_tree(tmp_path)

    refused = _cli(["seal", str(tmp_path), *extra])

    assert refused.returncode != 0
    assert "non-finite json number" in (
        refused.stdout + refused.stderr
    ).lower()
    assert _snapshot_tree(tmp_path) == before


def test_noop_seal_preserves_every_run_file(tmp_path):
    _sealed_synthetic_run(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resealed"] = {
        "at": "2026-02-01T00:00:00Z",
        "changed_outputs": ["recorded earlier"],
        "means": "recorded provenance must survive a no-op seal",
    }
    manifest["extension_provenance"] = {"preserve": "manifest extension"}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    result = _cli(["seal", str(tmp_path)])

    assert result.returncode == 0, result.stderr
    assert _snapshot_tree(tmp_path) == before


def test_seal_without_manifest_repairs_legacy_envelopes(tmp_path):
    _sealed_synthetic_run(tmp_path)
    (tmp_path / "run_manifest.json").unlink()
    for path in tmp_path.glob("*_envelope.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("content_checksum")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    result = _cli(["seal", str(tmp_path)])

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "run_manifest.json").exists()
    for path in tmp_path.glob("*_envelope.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["content_checksum"]


@pytest.mark.parametrize("mode", [0o644, 0o444], ids=["writable", "read-only"])
def test_seal_rolls_back_envelope_writes_if_commit_fails(
    tmp_path, monkeypatch, mode
):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    manifest_path = tmp_path / "run_manifest.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    xattrs_available = all(
        hasattr(os, name)
        for name in ("setxattr", "getxattr", "listxattr", "removexattr")
    )
    if xattrs_available:
        try:
            os.setxattr(a7_path, "user.fuwforge_rollback", b"preserve")
            os.setxattr(manifest_path, "user.fuwforge_rollback", b"preserve")
        except OSError:
            xattrs_available = False
    a7_path.chmod(mode)
    before = _snapshot_tree(tmp_path)
    before_modes = _snapshot_modes(tmp_path)
    before_mtimes = {
        str(path.relative_to(tmp_path)): path.stat().st_mtime_ns
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    before_xattrs = _snapshot_xattrs(tmp_path) if xattrs_available else None
    before_owners = {
        str(path.relative_to(tmp_path)): (
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    original_replace = Path.replace

    def fail_manifest_replace(self, target):
        if Path(target).name == "run_manifest.json":
            raise OSError("injected manifest commit failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    with pytest.raises(SystemExit, match="could not commit"):
        controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))

    assert _snapshot_tree(tmp_path) == before
    assert _snapshot_modes(tmp_path) == before_modes
    assert {
        str(path.relative_to(tmp_path)): path.stat().st_mtime_ns
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before_mtimes
    if xattrs_available:
        assert _snapshot_xattrs(tmp_path) == before_xattrs
    assert {
        str(path.relative_to(tmp_path)): (
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before_owners


@pytest.mark.skipif(
    not all(
        hasattr(os, name)
        for name in ("setxattr", "getxattr", "listxattr", "removexattr")
    ),
    reason="extended attributes are not available on this platform",
)
def test_seal_rewrites_readonly_file_with_user_xattr(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    attribute = "user.fuwforge_readonly"
    try:
        os.setxattr(a7_path, attribute, b"preserve")
    except OSError as exc:
        pytest.skip(f"filesystem does not support user xattrs: {exc}")
    a7_path.chmod(0o444)

    result = _cli(["seal", str(tmp_path), "--force"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(a7_path.read_text(encoding="utf-8"))["content_checksum"]
    assert a7_path.stat().st_mode & 0o7777 == 0o444
    assert os.getxattr(a7_path, attribute) == b"preserve"
    assert set(os.listxattr(a7_path)) == {attribute}


def test_seal_rolls_back_on_interrupt_during_later_replacement(
    tmp_path, monkeypatch
):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    manifest_path = tmp_path / "run_manifest.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    a7_path.chmod(0o444)
    before = _snapshot_tree(tmp_path)
    before_modes = _snapshot_modes(tmp_path)
    before_owners = {
        str(path.relative_to(tmp_path)): (
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    original_replace = Path.replace
    interrupted = False

    def interrupt_manifest_once(self, target):
        nonlocal interrupted
        if Path(target) == manifest_path and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("injected interrupt during manifest replacement")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", interrupt_manifest_once)
    with pytest.raises(KeyboardInterrupt, match="injected interrupt"):
        controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))

    assert _snapshot_tree(tmp_path) == before
    assert _snapshot_modes(tmp_path) == before_modes
    assert {
        str(path.relative_to(tmp_path)): (
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before_owners


def test_seal_rolls_back_when_replace_renames_then_raises(
    tmp_path, monkeypatch
):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    manifest_path = tmp_path / "run_manifest.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    before = _snapshot_tree(tmp_path)
    before_modes = _snapshot_modes(tmp_path)
    original_replace = Path.replace
    raised_after_rename = False

    def rename_then_fail_once(self, target):
        nonlocal raised_after_rename
        original_replace(self, target)
        if Path(target) == manifest_path and not raised_after_rename:
            raised_after_rename = True
            raise OSError("injected failure after successful rename")

    monkeypatch.setattr(Path, "replace", rename_then_fail_once)
    with pytest.raises(SystemExit, match="could not commit"):
        controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))

    assert _snapshot_tree(tmp_path) == before
    assert _snapshot_modes(tmp_path) == before_modes


def test_seal_preserves_existing_file_modes(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    manifest_path = tmp_path / "run_manifest.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    a7_path.chmod(0o640)
    manifest_path.chmod(0o604)
    before_modes = _snapshot_modes(tmp_path)

    result = _cli(["seal", str(tmp_path), "--force"])

    assert result.returncode == 0, result.stderr
    assert _snapshot_modes(tmp_path) == before_modes


def test_seal_successful_rewrite_keeps_fresh_mtime(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    old_timestamp_ns = 946684800_000_000_000
    os.utime(a7_path, ns=(old_timestamp_ns, old_timestamp_ns))

    result = _cli(["seal", str(tmp_path), "--force"])

    assert result.returncode == 0, result.stderr
    assert a7_path.stat().st_mtime_ns > old_timestamp_ns


def test_seal_applies_all_metadata_before_any_replacement(tmp_path, monkeypatch):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    before_files = _snapshot_tree(tmp_path)
    before_inodes = {
        path: path.stat().st_ino for path in tmp_path.rglob("*") if path.is_file()
    }
    original_apply = controller._apply_file_metadata
    calls = 0

    def fail_second(path, metadata, *, restore_times=False):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-file metadata failure")
        return original_apply(path, metadata, restore_times=restore_times)

    monkeypatch.setattr(controller, "_apply_file_metadata", fail_second)
    with pytest.raises(SystemExit, match="could not commit"):
        controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))

    assert _snapshot_tree(tmp_path) == before_files
    assert {
        path: path.stat().st_ino for path in tmp_path.rglob("*") if path.is_file()
    } == before_inodes


def test_seal_skips_chown_when_owner_and_group_match(tmp_path, monkeypatch):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")

    def unexpected_chown(*_args, **_kwargs):
        raise OSError("chown must be skipped when ownership already matches")

    monkeypatch.setattr(controller.os, "chown", unexpected_chown)
    controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))
    assert json.loads(a7_path.read_text(encoding="utf-8"))["content_checksum"]


@pytest.mark.skipif(
    not all(hasattr(os, name) for name in ("setxattr", "getxattr", "removexattr")),
    reason="extended attributes are not available on this platform",
)
def test_seal_preserves_file_xattrs_ownership_and_group(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    manifest_path = tmp_path / "run_manifest.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    attribute = "user.fuwforge_task24"
    try:
        os.setxattr(a7_path, attribute, b"envelope")
        os.setxattr(manifest_path, attribute, b"manifest")
    except OSError as exc:
        pytest.skip(f"filesystem does not support user xattrs: {exc}")
    before = {
        path: (path.stat().st_uid, path.stat().st_gid, os.getxattr(path, attribute))
        for path in (a7_path, manifest_path)
    }

    result = _cli(["seal", str(tmp_path), "--force"])

    assert result.returncode == 0, result.stderr
    after = {
        path: (path.stat().st_uid, path.stat().st_gid, os.getxattr(path, attribute))
        for path in (a7_path, manifest_path)
    }
    assert after == before


@pytest.mark.skipif(
    not all(
        hasattr(os, name)
        for name in ("listxattr", "getxattr", "setxattr", "removexattr")
    )
    or not shutil.which("setfacl"),
    reason="extended ACLs are not available on this platform",
)
def test_seal_removes_directory_inherited_acl_from_staged_files(tmp_path):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    manifest_path = tmp_path / "run_manifest.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    configured = subprocess.run(
        [
            shutil.which("setfacl"),
            "-d",
            "-m",
            "u:nobody:r",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if configured.returncode != 0:
        pytest.skip(f"filesystem cannot set a default ACL: {configured.stderr}")
    before = {
        path: {
            name: os.getxattr(path, name) for name in os.listxattr(path)
        }
        for path in (a7_path, manifest_path)
    }

    result = _cli(["seal", str(tmp_path), "--force"])

    assert result.returncode == 0, result.stderr
    after = {
        path: {
            name: os.getxattr(path, name) for name in os.listxattr(path)
        }
        for path in (a7_path, manifest_path)
    }
    assert after == before


@pytest.mark.skipif(
    not all(hasattr(os, name) for name in ("setxattr", "getxattr", "removexattr")),
    reason="extended attributes are not available on this platform",
)
def test_seal_metadata_copy_failure_happens_before_replacement(
    tmp_path, monkeypatch
):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    attribute = "user.fuwforge_task24"
    try:
        os.setxattr(a7_path, attribute, b"must-copy")
    except OSError as exc:
        pytest.skip(f"filesystem does not support user xattrs: {exc}")
    before = _snapshot_tree(tmp_path)

    def fail_setxattr(*_args, **_kwargs):
        raise OSError("injected xattr copy failure")

    monkeypatch.setattr(controller.os, "setxattr", fail_setxattr)
    with pytest.raises(SystemExit, match="could not commit"):
        controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))

    assert _snapshot_tree(tmp_path) == before


def test_seal_cleans_staged_temp_on_fsync_failure(tmp_path, monkeypatch):
    _sealed_synthetic_run(tmp_path)
    a7_path = tmp_path / "a7_envelope.json"
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["warnings"].append("requires repair")
    a7.pop("content_checksum")
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    a7_path.chmod(0o640)
    (tmp_path / "run_manifest.json").chmod(0o604)
    before_files = _snapshot_tree(tmp_path)
    before_modes = _snapshot_modes(tmp_path)

    def fail_fsync(_descriptor):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(controller.os, "fsync", fail_fsync)
    with pytest.raises(SystemExit, match="could not commit"):
        controller.cmd_seal(argparse.Namespace(run_dir=str(tmp_path), force=True))

    assert _snapshot_tree(tmp_path) == before_files
    assert _snapshot_modes(tmp_path) == before_modes


def test_assemble_auto_discovers_params():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_assemble_"))
    try:
        # Run a baseline mock run
        res = _cli(
            [
                "run",
                str(tmp),
                "--ticker",
                "AUTOPARAM",
                "--company",
                "AutoParam Inc",
                "--analysis-date",
                "2026-03-01",
                "--provider",
                "mock",
            ]
        )
        assert res.returncode == 0, res.stderr

        # Remove the report so assemble recreates it
        report_file = tmp / "AUTOPARAM_2026-03-01_report.md"
        assert report_file.exists()
        report_file.unlink()

        # Assemble without --ticker or --input flags; should auto-discover from envelopes
        res_assemble = _cli(["assemble", str(tmp)])
        assert res_assemble.returncode == 0, res_assemble.stderr
        assert report_file.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_screen_and_diff_commands(capsys):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_screen_"))
    try:
        for ticker in ("ALPHA", "BETA"):
            res = _cli(
                [
                    "run",
                    str(tmp / ticker),
                    "--ticker",
                    ticker,
                    "--analysis-date",
                    "2026-02-01",
                    "--provider",
                    "mock",
                ]
            )
            assert res.returncode == 0, res.stderr

        # Test screen on explicit dirs
        parser = build_parser()
        args = parser.parse_args(["screen", str(tmp / "ALPHA"), str(tmp / "BETA")])
        args.func(args)
        output = capsys.readouterr().out
        assert "ALPHA" in output and "BETA" in output
        assert "quality_score_10" in output

        # Test screen discovering runs inside a parent directory
        args_parent = parser.parse_args(["screen", str(tmp)])
        args_parent.func(args_parent)
        parent_out = capsys.readouterr().out
        assert "ALPHA" in parent_out and "BETA" in parent_out

        args = parser.parse_args(
            ["screen", str(tmp / "ALPHA"), str(tmp / "BETA"), "--format", "csv"]
        )
        args.func(args)
        csv_out = capsys.readouterr().out
        assert csv_out.splitlines()[0].startswith("ticker,")

        args = parser.parse_args(["diff", str(tmp / "ALPHA"), str(tmp / "BETA")])
        args.func(args)
        diff_out = capsys.readouterr().out
        assert "Scorecard" in diff_out
        assert "Comparison fields" in diff_out
        assert "Provenance differences" in diff_out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
