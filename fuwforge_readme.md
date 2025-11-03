```
 ███████╗██╗   ██╗██╗    ██╗    ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
 ██╔════╝██║   ██║██║    ██║    ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
 █████╗  ██║   ██║██║ █╗ ██║    █████╗  ██║   ██║██████╔╝██║  ███╗█████╗  
 ██╔══╝  ██║   ██║██║███╗██║    ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  
 ██║     ╚██████╔╝╚███╔███╔╝    ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
 ╚═╝      ╚═════╝  ╚══╝╚══╝     ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

# Contract-Driven Multi-Agent Research Pipeline

**A deterministic, adversarial-tested engine for equity analysis that survives audit without sacrificing throughput.**

FUWForge implements a seven-agent pipeline (A1–A7) where each stage operates under explicit contract, writes to cryptographically-checksummed JSON envelopes, and refuses to proceed until the next agent validates its input. The result: financial research that's both repeatable and defensible—no hand-waving, no black boxes.

Built on the filings-first methodology from Fired Up Wealth's COA framework, adapted for academic rigor and production deployment.

---

## Architecture at a glance

```
[Intake] → A1 → A2 → A3 → A4 → A5 → A6 → A7 → [Report]
           ▼    ▼    ▼    ▼    ▼    ▼    ▼
         [Envelope checksum gates at every transition]
```

**Seven agents. Seven contracts. One unbroken chain of provenance.**

| Agent | Domain | Output Artifact |
|-------|--------|-----------------|
| **A1** | Source acquisition & validation | Filing inventory, data integrity checksums |
| **A2** | Market structure & competitive analysis | Peer matrices, market positioning |
| **A3** | Quantitative valuation | DCF models, multiples, technical indicators |
| **A4** | Thesis synthesis | Investment memo spine, narrative scaffolding |
| **A5** | Scenario modeling | Probability-weighted cases (bull/base/bear) |
| **A6** | Risk interrogation | Red-flag taxonomy, adversarial stress tests |
| **A7** | Report assembly | Investor-ready document + audit trail |

---

## Why FUWForge exists

Most research pipelines are academic thought experiments or black-box SaaS tools. FUWForge is neither:

- **Deterministic by design**: Same inputs, same outputs. Every time.
- **Adversarially hardened**: A6 exists solely to attack the thesis built by A1–A5.
- **Filings-first discipline**: Primary sources over aggregated data; provenance over convenience.
- **Audit-native**: Every envelope carries its lineage. No "trust me" artifacts.
- **Dual-mode operation**: Run it automated via orchestrator or manually through CLI—same contracts, same validation.

This isn't a tool for generating content. It's a framework for *defending* analysis.

---

## What's in the box

```
fuwforge/
├── config/          # Depth modes, routing rules, orchestrator wiring
├── docs/            # Agent contracts, runbooks, manual operation guides
│   ├── manual_destroyer/           # Agent-by-agent manual prompts
│   └── i_am_become_the_orchestrator/  # CLI-only control mode
├── schemas/         # JSON Schema validation (envelope_v2_1.json)
├── src/             # Orchestrator, controller CLI, utilities
│   ├── orchestrator.py   # Automated multi-agent coordinator
│   └── controller.py     # CLI for validation, assembly, inspection
└── templates/       # Intake forms, scorecards, dashboards
    ├── inputs_blank.yaml
    ├── danger_zone_dashboard.md
    └── missing_data_table.md
```

---

## Operating modes

### Mode 1: Automated orchestrator

For production runs where you need throughput without sacrificing determinism.

1. Fill `templates/inputs_blank.yaml` with ticker, focus, analysis date, and depth mode.
2. Wire agent endpoints to `FUWForgeOrchestrator` in `src/orchestrator.py`.
3. Execute the run. Each agent writes its envelope, validates the checksum, and hands off.
4. Validate the chain: `python -m src.controller validate <envelope>`
5. Assemble the final report: `python -m src.controller assemble <run_dir> --run-id <id> --ticker <TICK> --analysis-date <YYYY-MM-DD> --depth <mode>`

### Mode 2: "Destroyer Mode" manual control

For when you need to *become* the orchestrator. Useful for learning the framework, adapting contracts, or operating in air-gapped environments.

1. Open `docs/manual_destroyer/prompts.md` (or the full CLI kit in `docs/i_am_become_the_orchestrator/`).
2. Execute A1–A7 prompts sequentially, pasting responses into provided markdown templates.
3. Validate each envelope as you go: `python -m src.controller validate <envelope_file>`
4. Use the Danger-Zone dashboard (`templates/danger_zone_dashboard.md`) to red-team your own work.
5. Chain the artifacts manually—same IDs, same checksums, same provenance as automated mode.

The manual mode exists because sometimes the best way to understand a system is to *be* the system. Plus, it's how you'd operate if your LLM budget was zero or your environment couldn't hit external APIs.

---

## Contract enforcement & provenance

Every agent operates under a written contract specifying:
- Input requirements (expected envelope structure, mandatory fields)
- Processing mandate (what the agent must do, what it must not do)
- Output guarantees (artifact format, checksum computation)
- Failure modes (what happens when inputs are malformed or incomplete)

The contracts live in `docs/` and are version-locked to the envelope schema. If an agent produces invalid output, the next agent in the chain refuses to proceed. No exceptions.

**Provenance chain**: Each envelope includes:
- SHA-256 checksum of its contents
- Reference to the prior envelope's checksum (forming a chain)
- Agent ID, timestamp, and config fingerprint
- Explicit citations for every claim (filing page numbers, data source URLs)

This isn't just for show. If someone challenges your analysis six months later, you can reconstruct the entire chain from inputs to conclusions.

---

## Safety rails

- **Schema validation**: `schemas/envelope_v2_1.json` enforces structure at every stage.
- **Checksum gates**: Tampering breaks the chain immediately; validation fails loudly.
- **Danger-zone dashboards**: A6's adversarial audit gets its own template to prevent burying red flags in prose.
- **Missing data tables**: Explicit documentation of gaps prevents false confidence from incomplete sources.
- **Depth modes**: Pre-configured rigor levels (quick/standard/deep) prevent scope creep while maintaining minimum standards.

---

## Academic context

This repository was developed as part of a research project exploring deterministic, audit-compliant workflows for LLM-driven financial analysis. The challenge: how do you leverage generative models for complex reasoning tasks while preserving the falsifiability and reproducibility standards required in quantitative research?

FUWForge's answer: treat each LLM call as a contract-bound transformation, validate outputs structurally before they propagate, and maintain cryptographic lineage throughout the pipeline. The result is a system where you can trust the process even when you're skeptical of any individual model generation.

If you're reading this as a reviewer, the interesting bits are:
- The envelope schema (`schemas/envelope_v2_1.json`)—see how we enforce citation structure
- Agent contracts in `docs/`—note the explicit failure mode specifications
- A6's adversarial mandate—a dedicated "attack the thesis" stage is unusual in production tools

---

## Quickstart

1. Clone this repository.
2. Read `docs/framework_overview.md` for architectural context.
3. Choose a depth mode from `config/depth_modes.yaml`.
4. Fill `templates/inputs_blank.yaml` with your analysis parameters.
5. Pick your operating mode (automated or manual) and follow the instructions above.
6. Validate, assemble, and commit the full artifact chain.

If you're learning the system, start with manual mode. Run through one ticker end-to-end. Once you understand what each agent does and why, the automated orchestrator will make sense.

---

## Gratitude & attribution

This framework builds directly on Eric's work at Fired Up Wealth—specifically the COA v2.1 methodology developed through hundreds of hours of live research streams on YouTube, refined through community feedback on Patreon and Discord. The filings-first discipline, adversarial testing loops, and mosaic integration patterns you see here are his contributions; FUWForge just adds the infrastructure to make those patterns repeatable at scale.

If you're using this for research, please cite appropriately. If you're adapting it for production, consider contributing improvements back upstream.

---

## License & usage

This repository is provided for academic and research purposes. The framework itself is open for study and adaptation; the specific agent prompts and contracts are derived from publicly-documented methodologies and carry no proprietary restrictions.

If you deploy this in a commercial context, you're responsible for ensuring compliance with relevant financial regulations and data usage agreements. FUWForge provides the scaffolding; it's your job to ensure the agents you wire into it behave legally and ethically.

---

**Questions? Issues? Extensions?**  
Open an issue or submit a PR. The contracts are meant to evolve—if you find a better way to structure an agent's mandate or a smarter validation gate, we want to know about it.