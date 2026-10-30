# CLI utilities for controller mode validation, assembly, and run auditing.

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import load_mapping
from src.contracts import COMPARISON_FIELD_KEYS, SCORECARD_CATEGORIES
from src.envelope import Envelope
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.run_audit import verify_run
from src.resources import get_default_runs_dir
from src.utils.checksum import envelope_content_checksum
from src.utils.finance import generate_comparison_fields, resolve_weights
from src.validators import validate_checksum


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOW_DEFAULTS_HELP = (
    "Legacy diagnostic mode: permit flagged placeholders; not verified research "
    "(--strict takes precedence)."
)


def _today_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


# ------------------------------------------------------------------ inputs


def _load_input(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    input_path = Path(path)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    data = load_mapping(input_path)
    if not isinstance(data, dict):
        raise SystemExit(f"Input file must contain a mapping: {input_path}")
    if isinstance(data.get("inputs"), dict):
        data = data["inputs"]
    return data


def _resolve_weights_arg(raw: Any) -> Any:
    """Accept 'default', a JSON object string, a mapping, or a YAML/JSON file path."""
    if raw is None:
        return "default"
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() == "default":
            return "default"
        if text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"--weights is not valid JSON: {exc}") from exc
        else:
            path = Path(text)
            if not path.exists():
                raise SystemExit(
                    "--weights must be 'default', a JSON object, or a path to a YAML/JSON file."
                )
            raw = load_mapping(path)
    if not isinstance(raw, dict):
        raise SystemExit("Weights must be a mapping of scorecard category -> weight.")
    resolved, errors = resolve_weights(raw)
    if errors:
        raise SystemExit("Invalid weights:\n- " + "\n- ".join(errors))
    return resolved


def _build_params(args: argparse.Namespace) -> Tuple[OrchestratorParams, bool]:
    input_path = getattr(args, "input", None)
    run_dir = Path(args.run_dir) if getattr(args, "run_dir", None) else None
    if not input_path and run_dir and (run_dir / "input.yaml").exists():
        input_path = str(run_dir / "input.yaml")
    data = _load_input(input_path)
    if not data and run_dir and run_dir.is_dir():
        for ef in sorted(run_dir.glob("*_envelope.json")):
            try:
                env_dict = json.loads(ef.read_text(encoding="utf-8"))
                if isinstance(env_dict.get("params"), dict) and env_dict["params"].get("ticker"):
                    data = dict(env_dict["params"])
                    break
            except Exception:
                pass

    ticker = args.ticker if args.ticker is not None else data.get("ticker")
    if not ticker:
        raise SystemExit("--ticker is required (or provide `ticker` via --input).")
    company = (
        args.company if args.company is not None else (data.get("company") or ticker)
    )
    analysis_date = (
        args.analysis_date
        if args.analysis_date is not None
        else (data.get("analysis_date") or _today_date())
    )
    depth = args.depth if args.depth is not None else (data.get("depth") or "Standard")
    focus = args.focus if args.focus is not None else (data.get("focus") or [])
    weights_raw = (
        args.weights if args.weights is not None else data.get("weights", "default")
    )

    strict = bool(getattr(args, "strict", False))

    params = OrchestratorParams(
        company=company,
        ticker=str(ticker),
        analysis_date=str(analysis_date),
        depth=str(depth),
        focus=list(focus),
        weights=_resolve_weights_arg(weights_raw),
    )
    return params, strict


# ------------------------------------------------------------------ commands


def cmd_validate(args: argparse.Namespace) -> None:
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT)
    data = json.loads(Path(args.envelope).read_text(encoding="utf-8"))
    result = orchestrator.validator.validate(data)
    if not result.valid:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(result.errors))

    if args.expected_checksum:
        envelope = Envelope.from_dict(data)
        checksum_result = validate_checksum(envelope, args.expected_checksum)
        if not checksum_result.valid:
            raise SystemExit(f"Checksum validation failed: {checksum_result.errors[0]}")

    print("Envelope validation succeeded.")


def cmd_assemble(args: argparse.Namespace) -> None:
    params, strict = _build_params(args)
    run_id = args.run_id or Path(args.run_dir).name or f"run_{params.ticker.lower()}"
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, strict=strict)
    summary = orchestrator.run(
        run_id=run_id,
        params=params,
        mode="controller",
        output_root=Path(args.run_dir).resolve(),
    )
    print("Run assembled successfully.")
    print(json.dumps(summary, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    params, strict = _build_params(args)
    # Real research is fail-closed by default. Legacy placeholders require an
    # explicit diagnostic opt-in, never merely forgetting --strict.
    if args.provider != "mock" and not getattr(args, "allow_defaults", False):
        strict = True
    run_id = (
        args.run_id
        or f"run_{params.ticker.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    evidence_pack = Path(args.evidence_pack) if args.evidence_pack else None
    web_setting = getattr(args, "web_search", None)
    web_enabled = args.provider == "gemini" if web_setting is None else web_setting
    if web_enabled and args.provider != "gemini":
        raise ValueError("--web-search requires --provider gemini; other providers never incur Gemini research charges.")
    web_max_sources = getattr(args, "web_max_sources", 4)
    if not 1 <= web_max_sources <= 6:
        raise ValueError("--web-max-sources must be between 1 and 6.")
    if web_enabled and not (evidence_pack or getattr(args, "auto_sources", False)):
        raise ValueError("Gemini web search requires --auto-sources or --evidence-pack for financial inputs; use --no-web-search to disable it.")
    source_root = (Path(args.run_dir).resolve() if args.run_dir else get_default_runs_dir(PROJECT_ROOT) / run_id)
    if not getattr(args, "resume", False) and ((source_root / "run_summary.json").exists() or any(source_root.glob("*_envelope.json"))):
        raise FileExistsError("Existing run will not be overwritten. Use --resume or a fresh run directory.")
    if getattr(args, "resume", False):
        if getattr(args, "auto_sources", False) or web_enabled:
            raise ValueError("Resume cannot repeat automatic/paid source intake. Use --no-web-search and the original --evidence-pack.")
        if evidence_pack is None and (source_root / "evidence" / "evidence.json").is_file():
            evidence_pack = source_root / "evidence" / "evidence.json"
    if getattr(args, "auto_sources", False):
        from src.public_data import collect_public_sources

        evidence_pack = collect_public_sources(params.ticker, params.analysis_date, params.depth, source_root / "retrieved_inputs", getattr(args, "cik", None))
    if web_enabled:
        from src.agents.model import GeminiApiBackend
        from src.web_research import collect_web_sources

        evidence_pack = collect_web_sources(
            ticker=params.ticker, company=params.company, analysis_date=params.analysis_date,
            evidence_path=evidence_pack, output_dir=source_root / "web_research",
            backend=GeminiApiBackend(model=args.model or os.environ.get("FUWFORGE_MODEL")), max_sources=web_max_sources,
            url_context=not getattr(args, "no_url_context", False),
        )
        print("Gemini web intake completed (one shared request; no Deep Research).")
    orchestrator = FUWForgeOrchestrator(
        PROJECT_ROOT,
        provider=args.provider,
        model=args.model,
        randomize=args.random_data,
        strict=strict,
        evidence_pack=evidence_pack,
        resume=getattr(args, "resume", False),
        recovery_choice=getattr(args, "recovery_choice", None),
        independent_bear=getattr(args, "independent_bear", False),
    )
    output_root = Path(args.run_dir).resolve() if args.run_dir else None
    summary = orchestrator.run(
        run_id=run_id,
        params=params,
        mode="automated",
        output_root=output_root,
    )
    print("Automated run completed successfully.")
    print(json.dumps(summary, indent=2))


def cmd_verify_run(args: argparse.Namespace) -> None:
    """
    Audit a run and report each level separately.

    The output deliberately does not collapse to one reassuring word: matching
    bytes, a valid graph, replayable arithmetic and reviewed factual support are
    different claims, and only the first is established by hashing.
    """
    from src.run_audit import semantic_audit

    run_dir = Path(args.run_dir)

    if getattr(args, "integrity_only", False):
        result = verify_run(run_dir, strict=args.strict)
        for warning in result.warnings:
            print(f"[warn] {warning}")
        if not result.valid:
            print("Envelope integrity FAILED:")
            for error in result.errors:
                print(f"- {error}")
            raise SystemExit(1)
        print(f"Run integrity succeeded ({len(result.warnings)} warning(s)).")
        return

    require_source_backed = getattr(args, "require_source_backed", False)
    report = semantic_audit(
        run_dir,
        require_source_backed=require_source_backed,
        strict=args.strict,
    )
    if args.strict:
        report.errors.extend(
            warning
            for warning in report.warnings
            if warning not in report.strict_exempt_warnings
            and warning not in report.errors
        )

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"Run audit: {run_dir}")
        print(f"Overall state: {report.overall_state}")
        assurance = report.assurance
        print(f"Assurance: {assurance['label']}")
        print(f"  means: {assurance['means']}")
        if assurance["unmet_source_backed_conditions"]:
            print("  source-backed conditions NOT met:")
            for condition in assurance["unmet_source_backed_conditions"]:
                print(f"    - {condition}")
        print("\nIndependent levels:")
        for level, state in report.levels.items():
            print(f"  - {level:<24} {state}")
        if report.warnings:
            print("\nWarnings:")
            for warning in report.warnings:
                print(f"  [warn] {warning}")
        if report.unavailable:
            print("\nNot established by this audit (disclosed gaps):")
            for item in report.unavailable:
                print(f"  [unavailable] {item}")
        if report.errors:
            print("\nFindings:")
            for error in report.errors:
                print(f"  - {error}")

    if report.errors:
        if not getattr(args, "json", False):
            print("\nRun audit FAILED:")
            for error in report.errors:
                print(f"- {error}")
        raise SystemExit(1)

    if report.overall_state == "incomplete":
        if not getattr(args, "json", False):
            print(
                "\nAudit passed for the completed subgraph only. This run is NOT a "
                "complete research report."
            )
        raise SystemExit(1)

    if not getattr(args, "json", False):
        print(
            "\nAudit passed. Matching bytes are established; source reconstruction, "
            "arithmetic replay and factual support are reported at their own levels "
            "above and are not implied by this result."
        )
        if report.assurance["label"] != "source_backed":
            print(
                "Assurance level is "
                f"'{report.assurance['label']}': this run is NOT a source-backed "
                "financial validation. Re-run with --require-source-backed to fail "
                "closed on the conditions listed above."
            )


def _changed_manifest_outputs(run_dir: Path) -> List[str]:
    """Return recorded final artifacts whose bytes no longer match the manifest.

    ``seal`` rebuilds ``run_manifest.json`` from what is on disk. Without this
    comparison, editing the report (or the artifact bundle) and re-sealing made
    the audit certify the edited bytes as the run's own output. Only the
    *outputs* are compared: re-sealing legitimately rewrites envelope bytes, so
    envelope digests are expected to change.
    """
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    recorded = manifest.get("outputs")
    if not isinstance(recorded, dict):
        return []
    changed: List[str] = []
    for label, entry in recorded.items():
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path")
        expect = entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(expect, str) or not expect:
            continue
        target = (run_dir / rel).resolve()
        if run_dir.resolve() not in target.parents and target != run_dir.resolve():
            changed.append(f"{rel} ({label}: recorded path escapes the run directory)")
            continue
        try:
            actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            changed.append(f"{rel} ({label}: recorded output is missing)")
            continue
        if actual != expect:
            changed.append(f"{rel} ({label}: recorded {expect[:19]}…, on disk {actual[:19]}…)")
    return changed


def cmd_seal(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    files = sorted(run_dir.glob("*_envelope.json"))
    if not files:
        raise SystemExit(f"No *_envelope.json files found in {run_dir}.")

    force = bool(getattr(args, "force", False))
    payloads: List[Tuple[Path, Dict[str, Any], str]] = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        agent_id = str((data.get("agent") or {}).get("id") or "")
        payloads.append((path, data, agent_id))

    # A pre-existing seal that does not match the bytes is a tampering signal.
    # Refuse once, before we start rewriting anything, so a forced re-seal is an
    # explicit operator decision rather than a side effect of the fixpoint loop.
    for path, data, agent_id in payloads:
        recorded = data.get("content_checksum")
        if recorded and recorded != envelope_content_checksum(data) and not force:
            raise SystemExit(
                f"{path.name}: content checksum mismatch; refusing to re-seal a "
                "modified envelope (use --force to override)."
            )

    # Re-sealing rewrites the manifest, so a report or artifact edited after the
    # run would be re-pinned to its new bytes and then audit as verified. That is
    # laundering, not sealing: compare the recorded final-artifact digests with
    # what is on disk and refuse before touching anything.
    changed_outputs = _changed_manifest_outputs(run_dir)
    if changed_outputs and not force:
        raise SystemExit(
            "Refusing to re-seal: these final artifacts no longer match the "
            "digests recorded in run_manifest.json, so re-sealing would certify "
            "edited output as the run's result:\n- "
            + "\n- ".join(changed_outputs)
            + "\nRestore the original bytes, or pass --force to certify the edited "
            "bytes deliberately."
        )

    checksums: Dict[str, str] = {}
    restored_agents: set[str] = set()
    sealed_agents: set[str] = set()
    for path, data, agent_id in payloads:
        # Missing empty root lineage is recoverable; non-root digests must be recorded.
        if agent_id == "A1":
            data.setdefault("upstream", [])
        lineage = (data.get("artifacts") or {}).get("_execution_lineage")
        if isinstance(lineage, dict) and lineage.get("consumed_upstream") and not data.get("upstream"):
            # Names alone cannot prove historical consumed bytes. Restore ONLY
            # exact digest-bearing references retained by the runner.
            original = lineage.get("upstream")
            if not isinstance(original, list) or {r.get("agent") for r in original if isinstance(r, dict)} != set(lineage["consumed_upstream"]):
                raise SystemExit(f"{path.name}: original consumed digests unavailable; refusing to invent historical lineage.")
            data["upstream"] = original
            restored_agents.add(agent_id)
        checksums[agent_id] = envelope_content_checksum(data)

    # A parent's bytes changed, so every descendant's recorded parent digest is
    # stale. Without --force that is a hard stop: re-sealing would assert a
    # history the run did not produce. With --force the operator has explicitly
    # accepted the edited bytes, so descendants are recomputed against the new
    # parent digests rather than silently re-stamped with the stale ones.
    #
    # Re-stamping one descendant changes that descendant's own digest, which
    # invalidates its children in turn, so this runs to a fixpoint: every
    # descendant of an edited parent is brought onto the recomputed chain.
    restamped_agents: set[str] = set()
    while True:
        changed: set[str] = set()
        for path, data, agent_id in payloads:
            for ref in data.get("upstream") or []:
                parent = ref.get("agent")
                if parent not in checksums:
                    raise SystemExit(
                        f"{path.name}: consumed parent {parent} is not present in this run; "
                        "refusing to seal lineage that references a missing role."
                    )
                if ref.get("content_checksum") == checksums[parent]:
                    continue
                if not force:
                    raise SystemExit(
                        f"{path.name}: consumed parent {parent} no longer matches original bytes; "
                        "recompute descendants instead of re-sealing lineage."
                    )
                ref["content_checksum"] = checksums[parent]
                changed.add(agent_id)
        if not changed:
            break
        restamped_agents |= changed
        # These envelopes' bytes just changed; re-derive their own digests so the
        # next pass sees the propagated values. Parent sets are untouched — only
        # the digest stamps move.
        for path, data, agent_id in payloads:
            if agent_id in changed:
                checksums[agent_id] = envelope_content_checksum(data)

    written = 0
    for path, data, _agent_id in payloads:
        # Seal every envelope, including ones this command did not need to
        # modify: an envelope that was never stamped (or whose stamp was
        # stripped) must still end up sealed.
        data["content_checksum"] = envelope_content_checksum(data)
        checksums[str((data.get("agent") or {}).get("id") or "")] = data[
            "content_checksum"
        ]
        sealed_agents.add(str((data.get("agent") or {}).get("id") or ""))
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        written += 1

    # Re-sealing changes envelope bytes, so any manifest sealed alongside them is
    # now stale. Rebuild it from the newly sealed envelopes; leaving a stale
    # manifest behind would report the run as unverifiable for no good reason.
    manifest_rebuilt = _rebuild_run_manifest(
        run_dir, resealed_outputs=changed_outputs if force else None
    )

    print(
        f"Sealed {len(sealed_agents)} envelope(s) in {run_dir}"
        + (
            f"; restored lineage for {len(restored_agents)} envelope(s) from the "
            "runner's recorded execution lineage"
            if restored_agents
            else ""
        )
        + f". Wrote {written} envelope file(s)."
        + (
            f" Recomputed parent digests for {len(restamped_agents)} descendant "
            "envelope(s) because a parent's bytes changed; this run now certifies "
            "the edited bytes, not the original execution."
            if restamped_agents
            else ""
        )
        + (" Rebuilt run_manifest.json." if manifest_rebuilt else "")
        + (
            " WARNING: --force re-sealed final artifacts that no longer matched the "
            "recorded digests (" + ", ".join(changed_outputs) + "); this run now "
            "certifies the edited bytes, not the original execution."
            if (changed_outputs and force)
            else ""
        )
    )


def _rebuild_run_manifest(
    run_dir: Path, resealed_outputs: Optional[List[str]] = None
) -> bool:
    """Rebuild run_manifest.json from the run's current sealed envelopes."""
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        from src.run_semantic_audit import build_run_manifest

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        envelopes: Dict[str, Envelope] = {}
        for path in sorted(run_dir.glob("*_envelope.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            env = Envelope.from_dict(data)
            envelopes[env.agent.id or path.stem] = env
        if not envelopes:
            return False
        summary_path = run_dir / "run_summary.json"
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.is_file()
            else {}
        )
        first = next(iter(envelopes.values()))
        rebuilt = build_run_manifest(
            run_dir,
            envelopes,
            run_id=str(manifest.get("run_id") or run_dir.name),
            ticker=str(first.params.get("ticker") or manifest.get("ticker") or ""),
            analysis_date=str(
                first.params.get("analysis_date") or manifest.get("analysis_date") or ""
            ),
            status=str(manifest.get("status") or "completed"),
            halt_reason=manifest.get("halt_reason"),
            summary=summary,
        )
        # Preserve the evidence state recorded at run time; sealing does not
        # re-verify source bytes.
        if manifest.get("evidence"):
            rebuilt["evidence"] = manifest["evidence"]
        # A forced re-seal of edited final artifacts is a durable fact about the
        # run, not just a console warning: an auditor reading the manifest later
        # must be able to see that these bytes are not the execution's output.
        if resealed_outputs:
            rebuilt["resealed"] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "changed_outputs": list(resealed_outputs),
                "means": "seal --force certified edited final artifacts; these bytes are not the original execution output",
            }
        manifest_path.write_text(json.dumps(rebuilt, indent=2), encoding="utf-8")
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _load_a7(
    run_dir: Path,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
    envelope: Optional[Dict[str, Any]] = None
    artifacts_files = sorted(run_dir.glob("*_artifacts.json"))
    if artifacts_files:
        payload = json.loads(artifacts_files[0].read_text(encoding="utf-8"))
        envelope = payload.get("A7")
    elif (run_dir / "a7_envelope.json").exists():
        envelope = json.loads((run_dir / "a7_envelope.json").read_text(encoding="utf-8"))
    else:
        print(f"[warn] {run_dir}: no *_artifacts.json or a7_envelope.json found; skipping.")
        return None

    if not isinstance(envelope, dict):
        print(f"[warn] {run_dir}: artifacts bundle has no A7 envelope; skipping.")
        return None
    artifacts = envelope.get("artifacts") or {}
    fields = artifacts.get("comparison_fields")
    scorecard = (
        artifacts.get("Scorecard")
        if isinstance(artifacts.get("Scorecard"), dict)
        else {}
    )
    if not isinstance(fields, dict):
        if scorecard:
            overall = scorecard.get("Overall") or {}
            q_score = overall.get("score", 7.0)
            v_score = (scorecard.get("Valuation") or {}).get("score", 7.0)
            m_score = (scorecard.get("Moat") or {}).get("score", 7.0)
            fields = generate_comparison_fields(
                quality_score_10=float(q_score),
                valuation_score_10=float(v_score),
                expected_5y_irr_base_pct=10.0,
                downside_case_5y_irr_pct=-5.0,
                moat_score_10=float(m_score),
            )
        else:
            print(f"[warn] {run_dir}: A7 has no comparison_fields; skipping.")
            return None
    return envelope, fields, scorecard


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def cmd_screen(args: argparse.Namespace) -> None:
    run_paths: List[Path] = []
    for run in args.runs:
        p = Path(run)
        if p.is_dir():
            if list(p.glob("*_artifacts.json")) or (p / "a7_envelope.json").exists():
                run_paths.append(p)
            else:
                subdirs = [
                    sub
                    for sub in sorted(p.iterdir())
                    if sub.is_dir()
                    and (list(sub.glob("*_artifacts.json")) or (sub / "a7_envelope.json").exists())
                ]
                if subdirs:
                    run_paths.extend(subdirs)
                else:
                    run_paths.append(p)
        else:
            run_paths.append(p)

    rows: List[Dict[str, Any]] = []
    for run_path in run_paths:
        loaded = _load_a7(run_path)
        if not loaded:
            continue
        envelope, fields, scorecard = loaded
        ticker = (envelope.get("params") or {}).get("ticker") or run_path.name
        row: Dict[str, Any] = {"ticker": ticker}
        row.update({key: fields.get(key) for key in COMPARISON_FIELD_KEYS})
        row["rating_band"] = (scorecard.get("Overall") or {}).get("rating_band")
        rows.append(row)

    if not rows:
        raise SystemExit("No screenable runs found.")

    sort_key = args.sort
    rows.sort(key=lambda r: (r.get(sort_key) is None, -(r.get(sort_key) or 0.0)))

    columns = [
        "ticker",
        "quality_score_10",
        "valuation_score_10",
        "moat_score_10",
        "expected_5y_irr_base_pct",
        "downside_case_5y_irr_pct",
        "fcf_yield_ttm_pct",
        "roic_ttm_pct",
        "net_debt_to_ebitda",
        "fragility_score_10",
        "evidence_quality_score_10",
        "data_completeness_score_10",
        "rating_band",
    ]

    if args.format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_fmt(row.get(column)) for column in columns])
        output = buffer.getvalue()
    else:
        header = "| " + " | ".join(columns) + " |"
        divider = "| " + " | ".join("---" for _ in columns) + " |"
        lines = [header, divider]
        for row in rows:
            lines.append(
                "| " + " | ".join(_fmt(row.get(column)) for column in columns) + " |"
            )
        output = "\n".join(lines) + "\n"

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Wrote {len(rows)} row(s) to {args.out}.")
    else:
        print(output, end="")


def cmd_diff(args: argparse.Namespace) -> None:
    loaded_a = _load_a7(Path(args.run_a))
    loaded_b = _load_a7(Path(args.run_b))
    if not loaded_a or not loaded_b:
        raise SystemExit("Both runs must contain A7 artifacts with comparison_fields.")

    env_a, fields_a, score_a = loaded_a
    env_b, fields_b, score_b = loaded_b
    label_a = (
        f"{env_a.get('params', {}).get('ticker', Path(args.run_a).name)} ({args.run_a})"
    )
    label_b = (
        f"{env_b.get('params', {}).get('ticker', Path(args.run_b).name)} ({args.run_b})"
    )

    def delta(x: Any, y: Any) -> str:
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return f"{y - x:+g}"
        return "—"

    print(f"### Scorecard — A: {label_a} · B: {label_b}\n")
    print(f"| Category | A | B | Δ |")
    print("| --- | --- | --- | --- |")
    categories = list(SCORECARD_CATEGORIES) + ["Overall"]
    for category in categories:
        cell_a = score_a.get(category)
        cell_b = score_b.get(category)
        val_a = cell_a.get("score") if isinstance(cell_a, dict) else None
        val_b = cell_b.get("score") if isinstance(cell_b, dict) else None
        print(f"| {category} | {_fmt(val_a)} | {_fmt(val_b)} | {delta(val_a, val_b)} |")

    print(f"\n### Comparison fields\n")
    print("| Metric | A | B | Δ |")
    print("| --- | --- | --- | --- |")
    for key in COMPARISON_FIELD_KEYS:
        val_a = fields_a.get(key)
        val_b = fields_b.get(key)
        print(f"| {key} | {_fmt(val_a)} | {_fmt(val_b)} | {delta(val_a, val_b)} |")

    prov_a = sorted(Path(args.run_a).glob("*_provenance.log"))
    prov_b = sorted(Path(args.run_b).glob("*_provenance.log"))
    if prov_a and prov_b:
        lines_a = [
            line.strip()
            for line in prov_a[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        lines_b = [
            line.strip()
            for line in prov_b[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        def _parse_entry(line: str) -> str:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                return f"[{parts[1]}] {' | '.join(parts[2:-1])}"
            return line

        set_a = {_parse_entry(l) for l in lines_a}
        set_b = {_parse_entry(l) for l in lines_b}
        diff_a = sorted(set_a - set_b)
        diff_b = sorted(set_b - set_a)
        if diff_a or diff_b:
            print(
                f"\n### Provenance differences ({len(lines_a)} vs {len(lines_b)} entries)\n"
            )
            if diff_a:
                print(f"**Only in A ({len(diff_a)}):**")
                for item in diff_a[:8]:
                    print(f"- {item}")
                if len(diff_a) > 8:
                    print(f"- ... and {len(diff_a) - 8} more")
            if diff_b:
                print(f"\n**Only in B ({len(diff_b)}):**")
                for item in diff_b[:8]:
                    print(f"- {item}")
                if len(diff_b) > 8:
                    print(f"- ... and {len(diff_b) - 8} more")


def cmd_monitor(args: argparse.Namespace) -> None:
    from src.sentry import evaluate_document, format_sentry_report

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        raise SystemExit(f"Run directory not found: {run_dir}")

    doc_arg = args.doc
    if not doc_arg:
        raise SystemExit("--doc is required (provide a file path or text string).")

    doc_text = doc_arg
    if len(doc_arg) < 260:
        try:
            doc_path = Path(doc_arg)
            if doc_path.exists() and doc_path.is_file():
                doc_text = doc_path.read_text(encoding="utf-8")
        except OSError:
            pass

    result = evaluate_document(
        run_dir=run_dir,
        doc_text=doc_text,
        provider=args.provider,
        api_key=args.api_key,
        threshold=args.threshold,
        model=getattr(args, "model", None),
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_sentry_report(result))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    if result.requires_rerun:
        raise SystemExit(1)


def cmd_collect_sources(args: argparse.Namespace) -> None:
    from src.public_data import collect_public_sources

    path = collect_public_sources(args.ticker, args.analysis_date, args.depth, Path(args.out), args.cik)
    print(f"Collected public research evidence: {path}")


def cmd_fetch_sources(args: argparse.Namespace) -> None:
    from src.evidence import fetch_evidence

    path = fetch_evidence(Path(args.manifest), Path(args.out))
    print(f"Retrieved evidence pack: {path}")


def cmd_bundle_evidence(args: argparse.Namespace) -> None:
    from src.evidence_bundle import build_bundle

    run_path = Path(args.run_dir)
    if run_path.is_file() and run_path.name == "evidence.json":
        ev_path = run_path
    elif (run_path / "evidence" / "evidence.json").is_file():
        ev_path = run_path / "evidence" / "evidence.json"
    elif (run_path / "evidence.json").is_file():
        ev_path = run_path / "evidence.json"
    else:
        raise SystemExit(
            f"evidence.json not found in {run_path} (checked {run_path / 'evidence' / 'evidence.json'} and {run_path / 'evidence.json'})."
        )

    out_path = Path(args.output)
    try:
        res = build_bundle(ev_path, out_path)
    except Exception as exc:
        raise SystemExit(f"Failed to build evidence bundle: {exc}")

    print("Evidence bundle packaged successfully.")
    print(f"Bundle directory: {res['bundle_dir']}")
    print(f"Files packaged:   {res['verified_files']}")
    print(f"Total bytes:      {res['total_bytes']}")
    print(f"Manifest SHA-256: {res['manifest_sha256']}")
    print("\nIMPORTANT: Record the Manifest SHA-256 digest out-of-band as the trusted root.")
    print("A self-contained bundle cannot prove its own authenticity; verification requires this independently supplied digest.")


def cmd_verify_evidence(args: argparse.Namespace) -> None:
    from src.evidence_bundle import verify_bundle

    bundle_dir = Path(args.directory)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise SystemExit(f"Bundle directory not found: {bundle_dir}")

    try:
        res = verify_bundle(bundle_dir, args.manifest_sha256)
    except Exception as exc:
        raise SystemExit(f"Evidence bundle verification FAILED: {exc}")

    print("Evidence bundle verification succeeded.")
    print(f"Verified files:   {res['verified_files']}")
    print(f"Verified bytes:   {res['verified_bytes']}")
    print(f"Manifest SHA-256: {res['manifest_sha256']}")


def cmd_restore_evidence(args: argparse.Namespace) -> None:
    from src.evidence_bundle import restore_bundle

    bundle_dir = Path(args.directory)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise SystemExit(f"Bundle directory not found: {bundle_dir}")

    target_dir = Path(args.output)
    try:
        pack = restore_bundle(bundle_dir, target_dir, args.manifest_sha256)
    except Exception as exc:
        raise SystemExit(f"Evidence bundle restoration FAILED: {exc}")

    print("Evidence bundle restored successfully.")
    print(f"Target directory: {pack.path.parent}")
    print(f"Ticker:           {pack.data.get('ticker')}")
    print(f"Documents:        {len(pack.documents)}")
    print(f"Pack checksum:    {pack.checksum}")


def cmd_export_packet(args: argparse.Namespace) -> None:
    from src.research_packet import export_packet

    run_dir = Path(args.run_dir)
    if not run_dir.exists() or not run_dir.is_dir():
        raise SystemExit(f"Run directory not found: {run_dir}")

    output_path = Path(args.output)
    try:
        res = export_packet(
            run_dir,
            output_path,
            packet_version=getattr(args, "packet_version", "research_packet_v1"),
        )
    except Exception as exc:
        raise SystemExit(f"Research packet export FAILED: {exc}")

    packet = res.get("packet") or {}
    audit = packet.get("audit") or {}
    overall_state = audit.get("overall_state", "unknown")

    if getattr(args, "strict", False) and overall_state != "verified":
        raise SystemExit(
            f"Strict export failed: run audit state is {overall_state!r} (expected 'verified')."
        )

    print(f"Exported research packet: {res['path']}")
    print(f"External SHA-256:         {res['sha256']}")
    print(f"Audit state:              {overall_state}")
    company = packet.get("company") or {}
    print(f"Company:                  {company.get('ticker')} ({company.get('name')})")
    print(f"Approval status:          {packet.get('approval_status')}")


# ------------------------------------------------------------------ parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input", default=None, help="YAML/JSON input file with run parameters."
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Unique run identifier (defaults to directory name or auto-ID).",
    )
    parser.add_argument(
        "--company", default=None, help="Company name (defaults to ticker if omitted)."
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Ticker symbol (required unless provided via --input).",
    )
    parser.add_argument(
        "--analysis-date",
        default=None,
        help="Analysis date YYYY-MM-DD (defaults to today).",
    )
    parser.add_argument(
        "--depth",
        default=None,
        help="Depth mode (Quick|Standard|Deep, default: Standard).",
    )
    parser.add_argument("--focus", nargs="*", default=None, help="Optional focus tags.")
    parser.add_argument(
        "--weights",
        default=None,
        help="'default', a JSON object, or a path to a YAML/JSON weights file.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on injected defaults and other soft contract warnings.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FUWForge (Fired Up Wealth) controller utilities."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate an agent envelope."
    )
    validate_parser.add_argument("envelope", help="Path to the JSON envelope file.")
    validate_parser.add_argument("--expected-checksum", help="Checksum emitted by A1.")
    validate_parser.set_defaults(func=cmd_validate)

    assemble_parser = subparsers.add_parser(
        "assemble", help="Assemble a run from controller mode outputs."
    )
    assemble_parser.add_argument(
        "run_dir", help="Directory containing a*_envelope.json files."
    )
    _add_run_arguments(assemble_parser)
    assemble_parser.set_defaults(func=cmd_assemble)

    run_parser = subparsers.add_parser(
        "run", help="Execute an automated end-to-end multi-agent run."
    )
    run_parser.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="Directory to save run outputs (defaults to runs/<run_id>).",
    )
    _add_run_arguments(run_parser)
    run_parser.add_argument(
        "--provider",
        default="mock",
        choices=[
            "mock",
            "agy",
            "opencode",
            "opencode-go",
            "opencode_go",
            "openai",
            "gemini",
        ],
        help="Agent execution provider (default: mock).",
    )
    run_parser.add_argument(
        "--model", default=None, help="Model name override for model providers."
    )
    run_parser.add_argument(
        "--random-data",
        action="store_true",
        help="Generate randomized data in mock provider.",
    )
    run_parser.add_argument(
        "--allow-defaults", action="store_true",
        help=ALLOW_DEFAULTS_HELP,
    )
    run_parser.add_argument("--resume", action="store_true", help="Reuse only checksum-verified validated receipts; recompute changed requests and descendants.")
    run_parser.add_argument("--recovery-choice", choices=["retry", "abandon"], help="Resolve uncertain completion explicitly; retry may repeat paid external calls.")
    run_parser.add_argument("--independent-bear", action="store_true", help="Opt-in experiment: one additional blind bear pass, then reconcile; disabled by default.")
    source_mode = run_parser.add_mutually_exclusive_group()
    source_mode.add_argument(
        "--evidence-pack", default=None,
        help="Hash-checked evidence.json created by fetch-sources or collect-sources.",
    )
    source_mode.add_argument("--auto-sources", action="store_true", help="Collect date-appropriate public SEC filings and daily market history before analysis.")
    run_parser.add_argument("--cik", default=None, help="Optional SEC CIK override for public source discovery (ticker pairing is verified).")
    web_mode = run_parser.add_mutually_exclusive_group()
    web_mode.add_argument("--web-search", dest="web_search", action="store_true", help="One shared Gemini Search + URL Context intake before analysis (default for Gemini; requires financial evidence).")
    web_mode.add_argument("--no-web-search", dest="web_search", action="store_false", help="Disable all additional paid Gemini web intake calls.")
    run_parser.set_defaults(web_search=None)
    run_parser.add_argument("--web-max-sources", type=int, default=4, help="Maximum accepted dated web pages, 1–6 (default: 4); not a hard search-query/dollar cap.")
    run_parser.add_argument("--no-url-context", action="store_true", help="Disable URL Context in the shared intake; retain Search and runner downloads.")
    run_parser.set_defaults(func=cmd_run)

    fetch_parser = subparsers.add_parser("fetch-sources", help="Download explicit public source documents into a cached evidence pack.")
    fetch_parser.add_argument("--manifest", required=True, help="JSON manifest with ticker and document URLs/dates.")
    fetch_parser.add_argument("--out", required=True, help="New directory for retrieved bytes, text and evidence.json.")
    fetch_parser.set_defaults(func=cmd_fetch_sources)

    collect_parser = subparsers.add_parser("collect-sources", help="Discover and cache public SEC filings and date-bounded price history.")
    collect_parser.add_argument("--ticker", required=True)
    collect_parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD analysis cutoff.")
    collect_parser.add_argument("--depth", choices=["Quick", "Standard", "Deep"], default="Standard")
    collect_parser.add_argument("--cik", default=None, help="Optional SEC CIK; ticker pairing must match.")
    collect_parser.add_argument("--out", required=True, help="New directory for discovery traces and retrieved evidence.")
    collect_parser.set_defaults(func=cmd_collect_sources)

    verify_parser = subparsers.add_parser(
        "verify-run",
        help="Audit a run across independent levels: byte integrity, source reconstruction, graph/contracts, calculation replay, final-artifact binding.",
    )
    verify_parser.add_argument("run_dir", help="Run directory to audit.")
    verify_parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings and incompleteness as errors.",
    )
    verify_parser.add_argument(
        "--require-source-backed",
        action="store_true",
        help="Require verified evidence bytes, all eight source-bound A3 Beneish inputs, and no disclosed calculation gaps.",
    )
    verify_parser.add_argument(
        "--integrity-only",
        action="store_true",
        help="Only run the legacy hash/schema/lineage integrity check (faster).",
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the audit report as machine-readable JSON.",
    )
    verify_parser.set_defaults(func=cmd_verify_run)

    seal_parser = subparsers.add_parser(
        "seal", help="Seal controller-mode envelopes with content checksums."
    )
    seal_parser.add_argument(
        "run_dir", help="Directory containing a*_envelope.json files."
    )
    seal_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-sealing even if content checksum mismatches.",
    )
    seal_parser.set_defaults(func=cmd_seal)

    screen_parser = subparsers.add_parser(
        "screen", help="Rank completed runs using their standardized comparison fields."
    )
    screen_parser.add_argument("runs", nargs="+", help="Run directories to include.")
    screen_parser.add_argument(
        "--format",
        choices=["markdown", "csv"],
        default="markdown",
        help="Output format (default: markdown).",
    )
    screen_parser.add_argument(
        "--sort",
        default="quality_score_10",
        help="Comparison field to sort by, descending (default: quality_score_10).",
    )
    screen_parser.add_argument(
        "--out", default=None, help="Write output to a file instead of stdout."
    )
    screen_parser.set_defaults(func=cmd_screen)

    diff_parser = subparsers.add_parser(
        "diff", help="Compare two runs on scorecard and comparison fields."
    )
    diff_parser.add_argument("run_a", help="Baseline run directory.")
    diff_parser.add_argument("run_b", help="Comparison run directory.")
    diff_parser.set_defaults(func=cmd_diff)

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Evaluate a document using Jev / TypeSafe, Gemini / Google AI Studio, or OpenCode Go.",
    )
    monitor_parser.add_argument("run_dir", help="Completed run directory.")
    monitor_parser.add_argument(
        "--doc", required=True, help="Path to document file or inline text string."
    )
    monitor_parser.add_argument(
        "--provider",
        default="auto",
        choices=[
            "auto",
            "jev",
            "gemini",
            "opencode",
            "opencode-go",
            "opencode_go",
            "mock",
        ],
        help="Evaluation provider (default: auto; uses Jev if configured, else mock; select gemini explicitly for Google AI Studio).",
    )
    monitor_parser.add_argument(
        "--model",
        default=None,
        help="Model name override for Jev, Gemini, or OpenCode Go.",
    )
    monitor_parser.add_argument(
        "--threshold",
        type=float,
        default=0.75,
        help="Contradiction probability threshold to flag thesis threat (default: 0.75).",
    )
    monitor_parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key override.",
    )
    monitor_parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON."
    )
    monitor_parser.add_argument(
        "--out", default=None, help="Write JSON results to a specified file."
    )
    monitor_parser.set_defaults(func=cmd_monitor)

    bundle_ev_parser = subparsers.add_parser(
        "bundle-evidence",
        help="Package a run's evidence pack into a portable verified bundle.",
    )
    bundle_ev_parser.add_argument(
        "run_dir", help="Run directory (or path to evidence.json)."
    )
    bundle_ev_parser.add_argument(
        "--output", required=True, help="Destination directory for the evidence bundle."
    )
    bundle_ev_parser.set_defaults(func=cmd_bundle_evidence)

    verify_ev_parser = subparsers.add_parser(
        "verify-evidence",
        help="Verify an evidence bundle directory against an independently trusted manifest SHA-256.",
    )
    verify_ev_parser.add_argument(
        "directory", help="Path to the evidence bundle directory."
    )
    verify_ev_parser.add_argument(
        "--manifest-sha256",
        required=True,
        help="Independently trusted 64-character hex SHA-256 manifest digest.",
    )
    verify_ev_parser.set_defaults(func=cmd_verify_evidence)

    restore_ev_parser = subparsers.add_parser(
        "restore-evidence",
        help="Verify and restore an evidence bundle into an active EvidencePack directory.",
    )
    restore_ev_parser.add_argument(
        "directory", help="Path to the source evidence bundle directory."
    )
    restore_ev_parser.add_argument(
        "--manifest-sha256",
        required=True,
        help="Independently trusted 64-character hex SHA-256 manifest digest.",
    )
    restore_ev_parser.add_argument(
        "--output",
        required=True,
        help="Clean destination directory for the restored evidence pack.",
    )
    restore_ev_parser.set_defaults(func=cmd_restore_evidence)

    export_pkt_parser = subparsers.add_parser(
        "export-packet",
        help="Build, validate, and export a versioned research packet from a run.",
    )
    export_pkt_parser.add_argument(
        "run_dir", help="Run directory containing envelopes and artifacts."
    )
    export_pkt_parser.add_argument(
        "--output",
        required=True,
        help="Destination JSON file path for the research packet.",
    )
    export_pkt_parser.add_argument(
        "--packet-version",
        default="research_packet_v1",
        help="Packet schema version (default: research_packet_v1).",
    )
    export_pkt_parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat unverified audit states as fatal.",
    )
    export_pkt_parser.set_defaults(func=cmd_export_packet)

    wizard_parser = subparsers.add_parser(
        "wizard",
        aliases=["guided", "interactive"],
        help="Interactive guided wizard to configure, execute, and audit an analysis run.",
    )
    wizard_parser.set_defaults(func=lambda _args: _run_wizard_entry())

    return parser


def _run_wizard_entry() -> None:
    from src.wizard import run_wizard

    try:
        run_wizard()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")


def main() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        if sys.stdin.isatty():
            _run_wizard_entry()
            return
        parser.print_help()
        sys.exit(2)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
