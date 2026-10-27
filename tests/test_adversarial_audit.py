"""Adversarial audit fixtures: every one must fail at its own level.

The layered audit claims six independent things. Each claim has a matching
attack below, and the point of the test is that the attack fails **at the level
that should catch it** — not merely that something failed somewhere.

The fixture set deliberately includes attacks that survive *hash* verification:
the envelope is re-sealed after tampering, so byte integrity passes and only a
semantic check can notice. That is the whole reason the semantic levels exist.

Covered attacks
---------------
- fabricated numeric input with a valid citation  -> calculation_replay
- wrong period                                    -> calculation_replay
- wrong unit                                      -> calculation_replay
- future-dated fact                               -> calculation_replay
- swapped source identity                         -> calculation_replay
- altered calculation witness (arithmetic)        -> calculation_replay
- incomplete provenance (missing source ref)      -> calculation_replay
- edited final report                             -> final_artifacts
- corrupted fact ledger                           -> calculation_replay (not "absent")
- duplicate / missing roles                       -> graph_and_contracts
- stripped lineage                                -> warns, never a false contradiction

All offline; mock provider; no network or credentials.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run_semantic_audit import semantic_audit

TICKER = "SEM"
ANALYSIS_DATE = "2026-02-01"
ACCESSION = "0000320193-25-000079"


def _good_run(tmp: Path) -> Path:
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    orchestrator.run(
        "adversarial",
        OrchestratorParams(
            company="Semantic Co",
            ticker=TICKER,
            analysis_date=ANALYSIS_DATE,
            depth="Standard",
            focus=[],
            weights="default",
        ),
        output_root=tmp,
    )
    return tmp


def _reseal(path: Path) -> None:
    """Recompute the content checksum after an edit: a *consistent* tamper."""
    from src.utils.checksum import envelope_content_checksum

    data = json.loads(path.read_text(encoding="utf-8"))
    data["content_checksum"] = envelope_content_checksum(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _rebind_manifest(run_dir: Path, agent: str, path: Path) -> None:
    """
    Update the manifest's recorded checksum for a re-sealed envelope.

    This models an operator who tampers with an artifact *and* updates the run
    manifest to match, so that the manifest-level role binding does not catch
    it. Only the semantic checks can then reject the run, which is what the
    calculation-level fixtures below need in order to be meaningful.
    """
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest.setdefault("roles", {})[agent] = data["content_checksum"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _cascade_references(run_dir: Path, agent: str) -> None:
    """
    Propagate a changed checksum through the whole dependency graph.

    Tampering with A3 changes its content checksum, which invalidates every
    envelope that records it as a parent — and those envelopes' own checksums
    change in turn, invalidating *their* parents. A thorough attacker (or
    ``seal --force``) repairs that chain to a fixpoint.

    This helper models that, so a fixture can isolate the *semantic* failure
    rather than tripping the graph check first — which is what makes the
    calculation-level tests meaningful: they prove the semantic check catches a
    fabrication even after the entire integrity chain has been made consistent.
    """
    for _ in range(10):  # bounded fixpoint; the graph is small
        changed = False
        # Current checksum of every agent, from its on-disk envelope.
        current = {}
        for path in sorted(run_dir.glob("*_envelope.json")):
            agent_id = path.stem.replace("_envelope", "").upper()
            current[agent_id] = json.loads(path.read_text(encoding="utf-8"))["content_checksum"]
        for path in sorted(run_dir.glob("*_envelope.json")):
            agent_id = path.stem.replace("_envelope", "").upper()
            data = json.loads(path.read_text(encoding="utf-8"))
            edited = False
            for entry in data.get("upstream", []):
                parent = entry.get("agent")
                if parent in current and entry.get("content_checksum") != current[parent]:
                    entry["content_checksum"] = current[parent]
                    edited = True
            if edited:
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                _reseal(path)
                _rebind_manifest(run_dir, agent_id, path)
                changed = True
        if not changed:
            return


def _a3_path(run_dir: Path) -> Path:
    return run_dir / "a3_envelope.json"


def _fact(
    fact_id: str,
    value: float,
    *,
    unit: str = "ratio",
    concept: str = "DSRI",
    company: str = TICKER,
    availability: str = "2025-02-01T23:59:59Z",
    instant: str = "2024-12-31",
    accession: str = ACCESSION,
    source_location=None,
    accounting_basis: str = "US-GAAP",
) -> dict:
    return {
        "schema_version": "financial_fact_v1",
        "id": fact_id,
        "company": company,
        "concept": concept,
        "unit": unit,
        "currency": None,
        "value": value,
        "filing_accession": accession,
        "availability_time": availability,
        "retrieval_time": "2025-02-02T00:00:00Z",
        "source_location": source_location
        if source_location is not None
        else {"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"},
        "accounting_basis": accounting_basis,
        "extractor_version": "sec_companyfacts_v1",
        "kind": "reported",
        "period_start": None,
        "period_end": None,
        "instant": instant,
    }


def _write_ledger(run_dir: Path, facts: list) -> None:
    (run_dir / "facts.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")


def _wire_dsri(run_dir: Path, fact_id: str, input_value: float, rebind_manifest: bool = False) -> None:
    """
    Point A3's dsri witness input at a fact and set the value it uses.

    The score is recomputed from the (possibly tampered) inputs so the
    arithmetic stays internally consistent — otherwise the test would pass on
    arithmetic alone and never exercise the source-binding check.
    """
    from src.utils.beneish import beneish_score

    path = _a3_path(run_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    beneish = data["artifacts"]["BeneishM"]
    witness = beneish["calculation_witness"]
    witness["inputs"]["dsri"] = input_value
    witness["input_fact_ids"] = {"dsri": fact_id}
    recomputed = beneish_score(**{k: float(v) for k, v in witness["inputs"].items()})
    witness["result"] = recomputed
    beneish["raw_score"] = recomputed
    beneish["score"] = round(recomputed, 2)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reseal(path)
    if rebind_manifest:
        _rebind_manifest(run_dir, "A3", path)
        _cascade_references(run_dir, "A3")


# ======================================================= calculation_replay


def test_fabricated_input_with_a_valid_citation_fails():
    """
    The central case: a real fact ID, a fabricated number (150 against a fact
    whose value is 1.0).

    The tamper is repaired all the way down the chain — every downstream
    envelope's parent reference is updated and re-sealed, and the run manifest
    is rebound — so byte integrity and the dependency graph pass. The
    source-binding check is what catches the fabricated number.

    (The 150.0 value is the value mandated by the specification. It is large
    enough to also push the score across the Beneish screening threshold, so the
    contract layer reacts too; the assertion below is specifically about the
    source-binding finding.)
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_fab_"))
    try:
        _good_run(tmp)
        _write_ledger(tmp, [_fact("fact_sem_dsri", 1.0)])
        _wire_dsri(tmp, "fact_sem_dsri", 150.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["envelope_integrity"] == "passed", "hash check must still pass"
        assert report.levels["calculation_replay"] != "passed"
        assert any("150" in e and "1.0" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_correctly_bound_input_passes_the_calculation_level():
    """The positive control: a real value bound to its fact must NOT fail."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_ok_"))
    try:
        _good_run(tmp)
        _write_ledger(tmp, [_fact("fact_sem_dsri", 1.0)])
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] == "passed", report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_wrong_period_fails():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_period_"))
    try:
        _good_run(tmp)
        # The fact is a 2022 instant; the witness claims the value for 2024.
        _write_ledger(tmp, [_fact("fact_sem_dsri", 1.0, instant="2022-12-31")])
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)
        path = _a3_path(tmp)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["artifacts"]["BeneishM"]["calculation_witness"]["input_periods"] = {
            "dsri": {"instant": "2024-12-31"}
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _reseal(path)
        _rebind_manifest(tmp, "A3", path)
        _cascade_references(tmp, "A3")

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("period" in e.lower() for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_wrong_unit_fails():
    """A ratio index backed by a dollar-denominated fact."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_unit_"))
    try:
        _good_run(tmp)
        _write_ledger(tmp, [_fact("fact_sem_dsri", 1.0, unit="USD", concept="Revenues")])
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("ratio" in e and "currency" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_future_dated_fact_fails():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_future_"))
    try:
        _good_run(tmp)
        # Available 2026-06-01, long after the 2026-02-01 research cutoff.
        _write_ledger(
            tmp, [_fact("fact_sem_dsri", 1.0, availability="2026-06-01T23:59:59Z")]
        )
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("cutoff" in e or "look-ahead" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_swapped_source_identity_fails():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_swap_"))
    try:
        _good_run(tmp)
        _write_ledger(tmp, [_fact("fact_sem_dsri", 1.0, company="OTHERCO")])
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("company" in e.lower() for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_incomplete_provenance_fails():
    """A fact with no source reference cannot back a cited number."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_prov_"))
    try:
        _good_run(tmp)
        _write_ledger(
            tmp, [_fact("fact_sem_dsri", 1.0, source_location="")]
        )
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("source_location" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_placeholder_filing_identity_fails():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_placeholder_"))
    try:
        _good_run(tmp)
        _write_ledger(
            tmp, [_fact("fact_sem_dsri", 1.0, accession="0000000000-00-000000")]
        )
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("accession" in e.lower() for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_altered_calculation_witness_fails():
    """Perturbing the recorded result breaks arithmetic, not source binding."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_witness_"))
    try:
        _good_run(tmp)
        path = _a3_path(tmp)
        data = json.loads(path.read_text(encoding="utf-8"))
        beneish = data["artifacts"]["BeneishM"]
        beneish["raw_score"] = -1.71
        beneish["score"] = -1.71
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _reseal(path)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("arithmetic" in e.lower() or "recomputed" in e.lower() for e in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_corrupted_fact_ledger_is_a_defect_not_an_absence():
    """
    A facts.json that exists but cannot be parsed must fail the run.

    It must not silently degrade to "no facts", which would let a fabricated
    input pass as merely unsourced.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_corrupt_"))
    try:
        _good_run(tmp)
        (tmp / "facts.json").write_text("{ this is not json", encoding="utf-8")
        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] != "passed"
        assert any("could not be loaded" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_fact_id_fails():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_nofact_"))
    try:
        _good_run(tmp)
        _write_ledger(tmp, [])
        _wire_dsri(tmp, "fact_sem_dsri", 1.0, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] != "passed"
        assert any("does not exist" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ========================================================== other levels


def test_edited_final_report_fails_final_artifacts():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_report_"))
    try:
        _good_run(tmp)
        report_path = next(tmp.glob("*_report.md"))
        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "\nMANIPULATED CONCLUSION\n",
            encoding="utf-8",
        )
        report = semantic_audit(tmp)
        assert report.levels["final_artifacts"] != "passed", report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_role_fails_graph_and_completeness():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_missing_"))
    try:
        _good_run(tmp)
        (tmp / "a5_envelope.json").unlink()
        report = semantic_audit(tmp)
        assert report.levels["graph_and_contracts"] != "passed"
        assert report.levels["completeness"] != "passed"
        assert any("A5" in e for e in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_duplicate_role_fails_graph():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_dup_"))
    try:
        _good_run(tmp)
        original = json.loads(_a3_path(tmp).read_text(encoding="utf-8"))
        (tmp / "a3_copy_envelope.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
        report = semantic_audit(tmp)
        assert report.levels["graph_and_contracts"] != "passed"
        assert any("duplicate" in e.lower() for e in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tampered_envelope_bytes_fail_integrity():
    """Editing an envelope without re-sealing is caught by hash verification."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_integrity_"))
    try:
        _good_run(tmp)
        path = _a3_path(tmp)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["artifacts"]["BeneishM"]["raw_score"] = -9.99
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Deliberately NOT re-sealed.
        report = semantic_audit(tmp)
        assert report.levels["envelope_integrity"] != "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================== honest handling of stripped data


def test_stripped_lineage_is_unavailable_not_a_false_contradiction():
    """
    Legacy runs with no lineage must be disclosed, not accused.

    A stripped lineage is missing data. The audit warns ("recorded lineage
    appears to have been stripped") instead of failing the run or inventing a
    contradiction. This is the honest treatment: it says what it could not
    verify without claiming something false.

    Modeled by stripping ``upstream`` from every envelope while the execution
    lineage artifact still records the real parents, and rebinding the manifest
    so the role binding is consistent.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_lineage_"))
    try:
        _good_run(tmp)
        for path in sorted(tmp.glob("*_envelope.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["upstream"] = []
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _reseal(path)
            _rebind_manifest(
                tmp, path.stem.replace("_envelope", "").upper(), path
            )

        report = semantic_audit(tmp)
        # Disclosed as unavailable/stripped, NOT failed as a contradiction.
        assert any("stripped" in w.lower() for w in report.warnings), report.warnings
        assert not any("contradict" in e.lower() for e in report.errors), report.errors
        # Because the audit genuinely cannot verify the graph from absent data,
        # it must not claim the graph level verified it either.
        assert report.levels["graph_and_contracts"] == "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_manifest_contradicting_recorded_lineage_is_a_real_error():
    """
    The converse: a manifest that records *no* parents while an envelope's
    execution lineage shows real parents it failed to declare is a genuine
    mismatch, not missing data, and must be a hard error.

    We mutate the manifest graph to drop A3's declared parent while the
    envelope's execution lineage records it.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_contradiction_"))
    try:
        _good_run(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["graph"]["A3"] = []
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["final_artifacts"] != "passed"
        assert any("does not match envelope upstream" in e for e in report.errors), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ==================================================== level independence


def test_each_level_is_reported_separately():
    """
    Levels are independent verdicts, not one aggregate.

    A calculation failure must not be reported as a source-reconstruction or
    byte-integrity failure, and vice versa.

    The fabricated input here is deliberately modest (1.5 rather than 150) so it
    does not push the score across the Beneish screening threshold. Embedded
    source-binding checks now intentionally reject the same defect during
    contract validation and independent calculation replay; unrelated levels
    must still remain separate.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_levels_"))
    try:
        _good_run(tmp)
        _write_ledger(tmp, [_fact("fact_sem_dsri", 1.0)])
        _wire_dsri(tmp, "fact_sem_dsri", 1.5, rebind_manifest=True)

        report = semantic_audit(tmp)
        assert set(report.levels) == {
            "envelope_integrity",
            "source_reconstruction",
            "graph_and_contracts",
            "calculation_replay",
            "final_artifacts",
            "completeness",
        }
        # Runtime contracts and replay both reject the fabricated input.
        assert report.levels["calculation_replay"] == "failed"
        assert report.levels["graph_and_contracts"] == "failed", report.errors
        assert any("A3 contract:" in error and "claimed value" in error for error in report.errors)
        assert report.levels["final_artifacts"] == "passed"
        assert report.levels["completeness"] == "passed"
        assert report.levels["envelope_integrity"] == "passed"
        assert all("1.5" in e for e in report.errors if "claimed value" in e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unsourced_inputs_are_disclosed_as_unavailable():
    """
    The mock run has no citations. Its arithmetic still replays, but the audit
    must disclose that the inputs were not source-backed — "replayed" is not
    "verified against a filing".
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p5_disclose_"))
    try:
        _good_run(tmp)
        report = semantic_audit(tmp)
        assert report.levels["calculation_replay"] == "passed"
        assert report.unavailable, "unverified inputs must be disclosed"
        assert any("no source fact" in u for u in report.unavailable)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)