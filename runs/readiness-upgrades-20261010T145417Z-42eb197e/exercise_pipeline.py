"""
Reusable execution and verification script for FUWForge readiness upgrades.
Located under unique scratch directory: runs/readiness-upgrades-20261010T145417Z-42eb197e/
"""

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_DIR = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext, AgentOutput
from src.agents.mock import A3Mock
from src.agents.model import ModelAgent
from src.calculation_witness import beneish_witness
from src.contracts import validate_contract
from src.evidence import EvidencePack
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score, classify
from src.utils.checksum import sha256_digest


def run_cmd(cmd_list, check=True):
    print(f"\n>>> Running: {' '.join(str(c) for c in cmd_list)}")
    res = subprocess.run(cmd_list, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    print(f"Exit code: {res.returncode}")
    if res.stdout:
        print("--- STDOUT ---")
        print(res.stdout.strip())
    if res.stderr:
        print("--- STDERR ---")
        print(res.stderr.strip())
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {res.returncode}: {' '.join(str(c) for c in cmd_list)}")
    return res


class SyntheticBoundA3(A3Mock):
    """
    Offline synthetic A3 agent that dynamically binds the eight Beneish
    input facts from AgentContext.fact_ledger into BeneishM calculation witness.
    """
    def run(self, context: AgentContext) -> AgentOutput:
        output = super().run(context)
        env = output.envelope
        
        # Verify fact_ledger is populated by orchestrator from EvidencePack
        if not context.fact_ledger:
            raise ValueError("context.fact_ledger is empty; expected 8 Beneish facts")
        
        inputs_values = {}
        fact_ids_map = {}
        for key in BENEISH_INPUT_KEYS:
            expected_id = f"fact_synth_{key}"
            if expected_id not in context.fact_ledger:
                raise ValueError(f"Missing expected fact {expected_id} in fact_ledger")
            fact = context.fact_ledger[expected_id]
            inputs_values[key] = float(fact.value)
            fact_ids_map[key] = fact.id

        score = beneish_score(**inputs_values)
        witness = beneish_witness(
            indices=inputs_values,
            fact_ids=fact_ids_map,
            result=score,
        ).to_dict()

        env.artifacts["BeneishM"] = {
            "score": round(score, 2),
            "raw_score": score,
            "classification": classify(score),
            "feasible": True,
            "calculator_version": "2.0.0",
            "provenance": "deterministic",
            "calculation_witness": witness,
        }
        return AgentOutput(env, output.raw_response)


def create_source_evidence_pack(evidence_dir: Path, ticker: str = "SYNTH", analysis_date: str = "2025-06-01") -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    doc_bytes = b"SEC Form 10-K synthetic filing source document for SYNTH."
    (evidence_dir / "doc_10k.bytes").write_bytes(doc_bytes)
    (evidence_dir / "doc_10k.txt").write_bytes(doc_bytes)
    doc_sha = sha256_digest(doc_bytes)

    inputs_values = {
        "dsri": 1.0,
        "gmi": 1.0,
        "aqi": 1.0,
        "sgi": 1.0,
        "depi": 1.0,
        "sgai": 1.0,
        "lvgi": 1.0,
        "tata": 0.0,
    }
    facts_list = []
    for key, val in inputs_values.items():
        f_id = f"fact_synth_{key}"
        facts_list.append({
            "schema_version": "financial_fact_v1",
            "id": f_id,
            "company": ticker,
            "concept": f"Index_{key.upper()}",
            "unit": "ratio",
            "value": val,
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "filing_accession": "0000320193-25-000001",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "US-GAAP",
            "extractor_version": "v1",
            "kind": "reported",
        })

    pack_data = {
        "schema_version": "1",
        "ticker": ticker,
        "documents": [{
            "id": "src_10k_2024",
            "type": "Filing",
            "title": "Annual 10-K",
            "url": "https://sec.gov/edgar/data/320193/000032019325000001.htm",
            "doc_date": "2025-01-15",
            "date_basis": "supplied_manifest",
            "accessed": "2025-01-20T00:00:00Z",
            "content_type": "text/plain",
            "verification": "retrieved",
            "content_sha256": doc_sha,
            "text_sha256": doc_sha,
            "raw_file": "doc_10k.bytes",
            "text_file": "doc_10k.txt",
        }],
        "facts": facts_list,
    }
    ev_json = evidence_dir / "evidence.json"
    ev_json.write_text(json.dumps(pack_data, indent=2), encoding="utf-8")
    return ev_json


def main():
    print("=== FUWForge Readiness Upgrades Pipeline Test ===")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Scratch dir:  {SCRATCH_DIR}")

    python_bin = sys.executable

    # 1. Mock Runs: Quick, Standard, Deep
    runs_to_test = [
        ("Quick", "MOCKQ", "Mock Quick Inc", SCRATCH_DIR / "mock-quick"),
        ("Standard", "MOCKS", "Mock Standard Inc", SCRATCH_DIR / "mock-standard"),
        ("Deep", "MOCKD", "Mock Deep Inc", SCRATCH_DIR / "mock-deep"),
    ]

    for depth, ticker, comp, run_dir in runs_to_test:
        print(f"\n==================== Testing {depth} Mock Run ====================")
        if not (run_dir / "run_summary.json").exists():
            run_cmd([python_bin, "-m", "src.controller", "run",
                     "--provider", "mock",
                     "--ticker", ticker,
                     "--company", comp,
                     "--analysis-date", "2025-06-01",
                     "--depth", depth,
                     str(run_dir)])

        # verify-run --strict (must pass)
        res_strict = run_cmd([python_bin, "-m", "src.controller", "verify-run", "--strict", str(run_dir)])
        assert res_strict.returncode == 0

        # verify-run --require-source-backed (must FAIL and prove sources are unavailable rather than vacuously passing)
        res_req = run_cmd([python_bin, "-m", "src.controller", "verify-run", "--require-source-backed", str(run_dir)], check=False)
        assert res_req.returncode != 0
        assert "Run audit FAILED" in res_req.stdout
        assert "require-source-backed" in res_req.stdout

        # seal
        res_seal = run_cmd([python_bin, "-m", "src.controller", "seal", str(run_dir)])
        assert res_seal.returncode == 0

        # export-packet
        packet_path = run_dir / "packet.json"
        res_export = run_cmd([python_bin, "-m", "src.controller", "export-packet", "--output", str(packet_path), str(run_dir)])
        assert res_export.returncode == 0
        assert packet_path.is_file()

    # 2. Independent-Bear Run
    print("\n==================== Testing --independent-bear Run ====================")
    ib_dir = SCRATCH_DIR / "mock-indbear"
    if not (ib_dir / "run_summary.json").exists():
        run_cmd([python_bin, "-m", "src.controller", "run",
                 "--provider", "mock",
                 "--ticker", "MOCKIB",
                 "--company", "Mock IndBear Inc",
                 "--analysis-date", "2025-06-01",
                 "--depth", "Standard",
                 "--independent-bear",
                 str(ib_dir)])
    run_cmd([python_bin, "-m", "src.controller", "verify-run", "--strict", str(ib_dir)])
    run_cmd([python_bin, "-m", "src.controller", "seal", str(ib_dir)])
    run_cmd([python_bin, "-m", "src.controller", "export-packet", "--output", str(ib_dir / "packet.json"), str(ib_dir)])

    # 3. Resume Run
    print("\n==================== Testing --resume Run ====================")
    res_dir = SCRATCH_DIR / "mock-resume"
    if not (res_dir / "run_summary.json").exists():
        run_cmd([python_bin, "-m", "src.controller", "run",
                 "--provider", "mock",
                 "--ticker", "MOCKRES",
                 "--company", "Mock Resume Inc",
                 "--analysis-date", "2025-06-01",
                 "--depth", "Quick",
                 str(res_dir)])
    # Resume on existing run
    run_cmd([python_bin, "-m", "src.controller", "run",
             "--provider", "mock",
             "--ticker", "MOCKRES",
             "--company", "Mock Resume Inc",
             "--analysis-date", "2025-06-01",
             "--depth", "Quick",
             "--resume",
             str(res_dir)])
    run_cmd([python_bin, "-m", "src.controller", "verify-run", "--strict", str(res_dir)])
    run_cmd([python_bin, "-m", "src.controller", "seal", str(res_dir)])
    run_cmd([python_bin, "-m", "src.controller", "export-packet", "--output", str(res_dir / "packet.json"), str(res_dir)])

    # 4. Synthetic Evidence-Backed Fixture Run
    print("\n==================== Testing Synthetic Evidence-Backed Fixture ====================")
    source_ev_dir = SCRATCH_DIR / "synthetic-source-evidence"
    source_ev_json = create_source_evidence_pack(source_ev_dir, ticker="SYNTH", analysis_date="2025-06-01")

    synth_run_dir = SCRATCH_DIR / "synthetic-run"
    if synth_run_dir.exists():
        shutil.rmtree(synth_run_dir)

    orch = FUWForgeOrchestrator(
        PROJECT_ROOT,
        provider="mock",
        strict=True,
        evidence_pack=source_ev_json,
    )
    orch.registry.register_instance(SyntheticBoundA3())

    params = OrchestratorParams(
        company="Synthetic Corp",
        ticker="SYNTH",
        analysis_date="2025-06-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    synth_run_result = orch.run("synthetic_run", params, output_root=synth_run_dir)
    print("Orchestrator run status:", synth_run_result.get("status"))
    assert synth_run_result.get("status") == "completed"

    # Verify that evidence snapshot exists
    assert (synth_run_dir / "evidence" / "evidence.json").is_file()

    # Demonstrate strict source-backed verification passes!
    res_synth_verify = run_cmd([python_bin, "-m", "src.controller", "verify-run", "--strict", "--require-source-backed", str(synth_run_dir)])
    assert res_synth_verify.returncode == 0
    assert "Overall state: verified" in res_synth_verify.stdout

    # Seal synthetic run
    run_cmd([python_bin, "-m", "src.controller", "seal", str(synth_run_dir)])

    # Export packet
    synth_packet_path = synth_run_dir / "packet.json"
    res_synth_packet = run_cmd([python_bin, "-m", "src.controller", "export-packet", "--strict", "--output", str(synth_packet_path), str(synth_run_dir)])
    assert res_synth_packet.returncode == 0

    packet_data = json.loads(synth_packet_path.read_text(encoding="utf-8"))
    assert len(packet_data.get("facts", [])) == 8, f"Expected 8 facts in packet, found {len(packet_data.get('facts', []))}"
    print(f"Verified packet facts count: {len(packet_data['facts'])}")
    for f in packet_data["facts"]:
        print(f"  - Fact {f['id']}: {f['concept']} = {f['value']} {f['unit']}")

    # 5. Adversarial Copy & Integrity Repair & Audit Rejection
    print("\n==================== Testing Adversarial Copy & Rejection ====================")
    adversarial_dir = SCRATCH_DIR / "synthetic-adversarial"
    if adversarial_dir.exists():
        shutil.rmtree(adversarial_dir)
    shutil.copytree(synth_run_dir, adversarial_dir)

    # Tamper with cited value in a3_envelope.json:
    # Set inputs['dsri'] = 999.0 while citing fact_synth_dsri (which has 1.0)
    # Recompute score with 999.0 so arithmetic alone matches, isolating the source binding failure
    a3_file = adversarial_dir / "a3_envelope.json"
    a3_data = json.loads(a3_file.read_text(encoding="utf-8"))
    beneish_art = a3_data["artifacts"]["BeneishM"]
    witness = beneish_art["calculation_witness"]
    witness["inputs"]["dsri"] = 999.0
    tampered_score = beneish_score(**witness["inputs"])
    witness["result"] = tampered_score
    beneish_art["raw_score"] = tampered_score
    beneish_art["score"] = round(tampered_score, 2)
    a3_file.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")

    # A. Show runtime contract rejection directly via validate_contract with fact_ledger
    from src.envelope import Envelope
    from src.facts import Fact
    from src.run_semantic_audit import _load_fact_ledger
    a3_env = Envelope.from_dict(a3_data)
    problems = []
    fact_ledger = _load_fact_ledger(adversarial_dir, envelopes={"A3": a3_env}, problems=problems, ticker="SYNTH", analysis_date="2025-06-01")
    contract_res = validate_contract(
        a3_env,
        strict=True,
        fact_ledger=fact_ledger,
        expected_company="SYNTH",
        cutoff="2025-06-01",
    )
    print("\nRuntime validate_contract valid:", contract_res.valid)
    print("Runtime validate_contract errors:")
    for err in contract_res.errors:
        print(f"  - {err}")
    assert not contract_res.valid
    assert any("does not equal the referenced fact's value" in err for err in contract_res.errors)

    # B. Also show ModelAgent runtime rejection when fact_ledger is forwarded
    class DummyBackend:
        def generate(self, prompt):
            return json.dumps(a3_data)
    model_agent = ModelAgent("A3", "Quant-Valuation-Technicals", DummyBackend(), max_retries=0, strict=False)
    ev_pack = EvidencePack(adversarial_dir / "evidence" / "evidence.json", ticker="SYNTH", analysis_date="2025-06-01")
    dummy_ctx = AgentContext(
        run_id="adversarial_test",
        params=copy.deepcopy(a3_data["params"]),
        depth_config={},
        focus_overrides={},
        frozen_checksum="",
        workdir=adversarial_dir,
        provenance_path=adversarial_dir / "SYNTH_2025-06-01_provenance.log",
        fact_ledger=copy.deepcopy(fact_ledger),
        source_documents=ev_pack.sources(),
        source_text=ev_pack.prompt_text(),
    )
    try:
        model_agent.run(dummy_ctx)
        raise AssertionError("ModelAgent should have rejected the tampered envelope!")
    except (ValueError, RuntimeError) as exc:
        print(f"\nModelAgent successfully rejected tampered contract at runtime:\n  {exc}")
        assert "does not equal the referenced fact's value" in str(exc)

    # C. Repair integrity of adversarial run using intended CLI API (seal --force)
    # seal --force runs the fixpoint loop, recomputing checksums and updating downstream references across A6, A7 and rebuilding manifest
    res_seal_force = run_cmd([python_bin, "-m", "src.controller", "seal", "--force", str(adversarial_dir)])
    assert res_seal_force.returncode == 0

    # D. Show that audit (both ordinary and source-backed) rejects it at calculation_replay
    res_adv_verify = run_cmd([python_bin, "-m", "src.controller", "verify-run", "--strict", str(adversarial_dir)], check=False)
    assert res_adv_verify.returncode != 0
    assert "Overall state: failed" in res_adv_verify.stdout
    assert "calculation_replay       failed" in res_adv_verify.stdout
    assert "does not equal the referenced fact's value" in res_adv_verify.stdout

    res_adv_backed = run_cmd([python_bin, "-m", "src.controller", "verify-run", "--strict", "--require-source-backed", str(adversarial_dir)], check=False)
    assert res_adv_backed.returncode != 0
    assert "Overall state: failed" in res_adv_backed.stdout

    print("\n=== ALL PIPELINE READINESS CHECKS PASSED SUCCESSFULLY ===")


if __name__ == "__main__":
    main()
