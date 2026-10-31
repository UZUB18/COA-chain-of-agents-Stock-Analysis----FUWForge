# Targeted Web Search and HTML Presentation — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-10-29-agent-web-search-presentation-design.md`
**Prerequisites (done on `codex/hardening-2026-10`):** execution identity v2 (3.1),
store state machine (3.3), read-only resume (3.4), checksum scheme v2 (2.5) — v2
hashes every non-telemetry metric, so new `search_*` metrics are covered
automatically.

**Rule carried from the spec:** the clean path is unchanged. A run whose agents
pass validation with no concrete problem makes **zero** extra searches and zero
extra completions. Search results are untrusted evidence, never typed facts.

## Slice 1 — Search lifecycle plumbing (no provider calls yet)

- `src/targeted_search.py` (new): `SearchTrigger` (agent id, field paths,
  reason, source type sought, query intents), `SearchOutcome` status enum
  (`not_triggered`, `disabled_by_user`, `not_performed`, `no_accepted_sources`,
  `accepted_sources`, `round_limit_reached`, `search_conflict`, `failed`).
- Append-only bundle writer under `<run>/targeted_search/<execution-id>/`
  (`search_manifest.json`, `S001.bytes`, `S001.txt`) reusing `evidence.py`
  download, URL safety, extraction, hashing and cutoff checks. Bundle digest =
  sha256 of the canonical manifest.
- Envelope metrics (hashed under v2): `search_status`, `search_bundle_checksum`,
  `search_trigger`; volatile provider/usage detail under `metrics.telemetry`.
- Execution identity: fold the bundle digest into `evidence_sha256`
  (`digest([shared_pack_checksum, bundle_checksum])`) only when a bundle exists,
  so no-search receipts keep their keys.

## Slice 2 — Triggers and the one-search / one-revision exchange

- Triggers (only these): (a) a parseable response that fails contract
  validation with field-specific data errors (missing / conflicting / malformed
  value); (b) a structured `search_request` the agent returns with its draft;
  (c) A1 market-binding conflict from Batch C2. Malformed JSON and transport
  errors stay on the existing repair/recovery path.
- In `ModelAgent.run`, after a triggered failure and before the existing single
  repair: one Gemini search through `GeminiApiBackend.research()` (separately
  configured key, any real analysis provider), validated sources into the bundle,
  then the existing single repair call doubles as the one revision, with the
  validated source text added as untrusted data. No paid retries; a second
  request is `round_limit_reached`.
- Durable store: record the search as its own receipt state transition
  (`searching` → `returned`) so an interrupted search is `unknown_completion`
  and needs `--recovery-choice`, never an automatic repeat.
- `--no-web-search`, mock, controller and resume-without-bundle make zero calls.
- Citation allowlist for strict mode = frozen shared pack + this execution's
  accepted bundle + accepted upstream bundles. A2–A5 never see each other's
  bundles.
- A revision that would change accepted upstream core data halts with
  `search_conflict` (shows source, field, quote, period, unit); nothing is
  rewritten.

## Slice 3 — Report, audit, cost disclosure

- Report "Research data gaps" gains a "Targeted search" table: trigger, status,
  accepted/excluded sources, request count, usage (null when unknown).
- Semantic audit: every bundle file exists and matches its manifest hashes; each
  triggered execution has a recorded trigger and ≤1 search, ≤1 revision; the
  bundle digest equals the envelope metric; citations resolve to allowed sources.
- Manifest discovery includes `targeted_search/**`.

## Slice 4 — HTML presentation (independent of slices 1–3)

- `src/presentation.py` (new): deterministic renderer from the sealed report
  text and artifacts → `<TICKER>_<DATE>_report.html`, inline CSS, CSP meta, no
  remote assets, all text escaped, print styles, unavailable values shown as
  unavailable.
- Optional branding: one best-effort Gemini search for brand colors when Gemini
  is configured and web search is not disabled; colors validated as CSS and
  contrast-checked; labelled "approximate" unless official; local palette
  fallback on any failure. Branding never alters financial text.
- Added to final artifact discovery and the final manifest; HTML failure leaves
  the Markdown report intact and records `presentation_unavailable`.
- CLI: `--html/--no-html` on `run` (default on) and `render-html <run_dir>` for
  existing runs.

## Decisions needed before coding

1. Scope order: slice 4 alone first (cheap, no new paid calls except optional
   branding), or slices 1–3 first (the "uncertain data / news" capability)?
2. Allow the triggered Gemini search for non-Gemini analysis providers when a
   Gemini key is configured (spec default), or Gemini runs only?
3. Branding search: on by default when Gemini is configured (spec), or off by
   default behind `--brand-search`?

## Verification (user-run)

Offline fakes only: clean path makes zero calls; each trigger makes exactly one
search and at most one revision; disabled/mock/controller make zero calls;
tampered bundle bytes fail the audit; A2–A5 isolation; conflict halts; HTML
escapes hostile text, has no external assets, matches report values, and falls
back when branding fails.
