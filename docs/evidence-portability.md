# Evidence Portability and Verification Policy

## 1. What is an Evidence Bundle?

An **Evidence Bundle** is a self-contained, content-addressed directory containing:
- Raw retrieved document bytes (`*.bytes`)
- Extracted text files (`*.txt`)
- Pipeline attachments and search traces (e.g., `web_research_trace.json`)
- Pack configuration (`evidence.json`)
- A hashed bundle manifest (`manifest.json`) listing every contained file's relative POSIX path, exact byte size, and SHA-256 digest, alongside extractor version and source metadata.

Evidence bundles make research runs reproducible across machines and over time, ensuring that downstream agent deductions and financial calculations can be audited against the exact source bytes available at execution time.

---

## 2. What Verification Proves and What it Does NOT Prove

### What it proves:
- **Byte integrity:** The exact byte sequence of every retrieved document and extracted text file matches the cryptographic SHA-256 digests in the bundle manifest.
- **Containment and completeness:** No unlisted, modified, missing, duplicate, or directory-escaping files exist inside the bundle tree.
- **Restoration safety:** The directory tree contains only real files and directories (no symlinks, special device files, or non-canonical paths).

### What it does NOT prove:
- **Factual truth:** A SHA-256 hash proves only that the bytes match what was retrieved. It does not verify the accuracy, fairness, or legal compliance of claims made by corporate filings or external sources.
- **Re-creation of missing bytes:** A cryptographic digest cannot recreate missing payload bytes. If source bytes are lost or omitted, verification fails at the source reconstruction level.
- **Authenticity from self-hashes:** A self-consistent rewritten bundle can carry its own valid hashes. Therefore, a self-hash or co-located digest file is **never** proof of authorship or authenticity. Verification requires comparing the manifest SHA-256 against an **independently trusted root** recorded out-of-band (e.g., a release ledger, run receipt, or authenticated run record).
- **Archive safety / Signature verification:** The verifier inspects a quiescent local directory. It is not an archive extractor (tar/zip) and does not verify cryptographic public-key signatures. Defending against concurrent filesystem mutation requires OS-level process isolation (read-only mounts / sandbox).

---

## 3. Storage and Redistribution Guidance

- **Keep bundles outside ordinary git history:** Because full evidence bundles contain binary filings, PDF reports, and extensive text caches, committing them directly to git repositories bloats repository history.
- **Content-addressed artifact storage:** Store complete bundles in private content-addressed object stores, release artifact registries, or dedicated cold storage, indexed by their trusted manifest SHA-256 digest.
- **Respect redistribution and licensing restrictions:** Do not publish or redistribute proprietary third-party market data, paywalled research, or private corporate filings without explicit license permissions.
- **Distinguish audit states:** Verification and audit tools must report distinct audit states:
  - `source_bytes_verified`: Source bytes exist and match trusted hashes.
  - `source_bytes_unavailable`: Source body files are absent (cannot reconstruct).
  - `extraction_mismatch`: Pipeline extraction version differs from expected version.
  - `semantic_failure`: Malformed structure or broken schema.
  - `untrusted_root`: Manifest hash differs from trusted out-of-band root.

---

## 4. Size Budgets

- **Production Size Budget:** `DEFAULT_BUNDLE_MAX_TOTAL_BYTES = 512 MiB` (`536,870,912` bytes) for a complete supported evidence pack across all documents and attachments.
- **Demo / Verifier Default Limit:** `DEMO_BUNDLE_MAX_TOTAL_BYTES = 64 MiB` (`67,108,864` bytes) applied by default in `verify_bundle` to catch runaway or oversized demo artifacts.
- **Per-Document Limit:** Individual documents remain subject to the `MAX_DOCUMENT_BYTES` limit (8 MiB per document).

---

## 5. Operator Workflow

```text
[Producer System]
       │
       ▼
 1. Build Bundle:
    build_bundle("runs/.../evidence.json", "output/bundle_aapl")
       │
       ▼
 2. Record Trusted Root Out-of-Band:
    Log manifest_sha256 in immutable run ledger / release record
       │
       ▼
[Transport to Clean / Verifier Machine]
       │
       ▼
 3. Verify Bundle Integrity:
    verify_bundle("output/bundle_aapl", expected_manifest_sha256=TRUSTED_ROOT)
       │
       ▼
 4. Check Extraction Version:
    check_extraction_version("output/bundle_aapl", expected_version="2.1")
       │
       ▼
 5. Restore and Re-derive:
    restore_bundle("output/bundle_aapl", "clean_run/pack", expected_manifest_sha256=TRUSTED_ROOT)
    -> Runs EvidencePack constructor, re-derives text from raw bytes, validates checksums.
```
