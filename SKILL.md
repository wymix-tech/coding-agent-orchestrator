---
name: orchestrating-sdd-coding
alias: adaptive-sdd-coding-orchestrator
description: Use when coding work should be routed through OpenSpec, BMAD, another SDD system, or Superpowers disciplines with deterministic evidence-backed classification, CBM semantic impact analysis, and durable agile execution-state management.
---

# Adaptive SDD Coding Orchestrator

## Purpose

Act as the control plane for coding work. Keep the repository's SDD system authoritative for **what/why**, derive auditable Work Facts, use Codebase Memory (CBM) to measure structural blast radius, compute the smallest sufficient development flow, maintain durable execution state for **where/what next**, and use Superpowers as the engineering discipline for **how**.

Do not replace OpenSpec, BMAD, or another SDD workflow. Do not let CBM select process rigor.

## Core Rules

1. **Discover before classifying.** Read repository instructions, active SDD artifacts, git state, architecture, quality policy, and native execution-status mechanisms.
2. **Extract before resolving.** Mechanical proof may set Work Facts; heuristics are hints only.
3. **Measure semantic impact before final scope classification.** For code changes, use `scripts/semantic_intake_pipeline.py` or the CBM provider path in `references/semantic-impact-engine.md`.
4. **Provider evidence is structural, not governance authority.** CBM risk labels MUST NOT populate `risk.*`, choose Flow, or satisfy gates.
5. **Unknown is not false.** Missing CBM edges/provider availability never prove local-only or low-risk behavior.
6. **Compute; do not vibe-score.** Use `scripts/decision_engine.py --strict-evidence`; agents MUST NOT adjust scores or flow manually.
7. **Use one requirement authority and one execution-state authority per field.** Native state wins where the SDD tool owns it; augment only missing execution concerns.
8. **Select execution-state mode explicitly.** Use `native`, `hybrid`, or `orchestrator` via `references/execution-state-manager.md`.
9. **Guard every phase advance.** `scripts/execution_state_manager.py` denies illegal transitions and close attempts lacking required evidence.
10. **Use optimistic revisions and append-only history.** Concurrent agents must not silently overwrite execution state.
11. **Fresh verification is snapshot-bound.** Material code/evidence changes invalidate previous final verification.
12. **Reassess deterministically.** Material discoveries update evidence, rerun semantic impact + Decision Engine, and reconcile SDD depth and execution state.
13. **Preserve invariants.** Required gates, root-cause debugging, traceability, scope discipline, native authority, and fresh final verification cannot be optimized away.

## Orchestration

1. `DISCOVER` using `references/orchestration-model.md`.
2. `EXTRACT` repository/request facts using `scripts/fact_extractor.py`.
3. `IMPACT` code changes using CBM through `scripts/cbm_provider.py`; normalize to `semantic-impact.json`.
4. `MAP` structural impact into Work Facts with `scripts/impact_mapper.py`.
5. `RESOLVE` remaining semantic facts with provenance using `scripts/fact_resolver.py`.
6. `CLASSIFY` with strict evidence using the Decision Engine.
7. `PLAN VERIFICATION` using `scripts/verification_planner.py`; repository policy may add stricter gates.
8. Select exactly one SDD adapter via `references/adapter-contract.md`.
9. Detect execution-state provider and initialize/resume Canonical Execution State.
10. Attach V6 analysis artifact refs to execution state; update the execution snapshot when material evidence/code changes.
11. Compose Superpowers via `references/superpowers-policy.md` and implement one traceable slice at a time.
12. Record blockers, cursor, assignments, gates, review, verification, and snapshots durably.
13. Close only after state guards, quality gates, SDD alignment, and fresh final verification all pass.

## Preferred V6 entry point

```bash
python scripts/semantic_intake_pipeline.py \
  --repo . \
  --request-file request.md \
  --resolutions .orchestrator/fact-resolutions.json
```

CBM is the only V6 code-intelligence provider. If it is unavailable, fail closed by default; never reinterpret provider absence as low impact.

## Completion Contract

DONE means the authoritative SDD/native work item is complete **and** Canonical Execution State can legally transition to `closed/completed` with all REQUIRED gates passing, no active blocker, acceptance satisfied, blocking review findings cleared, requirement → task → test → evidence traceability intact, and fresh verification against the final execution snapshot.
