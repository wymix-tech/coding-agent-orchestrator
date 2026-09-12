---
name: orchestrating-sdd-coding
version: 6.2
alias: adaptive-sdd-coding-orchestrator
description: Use when coding work should be routed through OpenSpec, BMAD, another SDD system, or Superpowers disciplines with deterministic evidence-backed classification, CBM semantic impact analysis, project Engineering Policy, durable execution state, and snapshot-bound role-aware Context Packs.
---

# Adaptive SDD Coding Orchestrator

## Purpose

Act as the control plane for coding work. Keep the repository's SDD system authoritative for **what/why**, Engineering Policy authoritative for **what implementations are allowed**, Codebase Memory (CBM) authoritative only for structural evidence, the Decision Engine authoritative for **how much process**, Canonical Execution State authoritative for **where/what next**, the V6 Context Plane authoritative only for **which current sources a role should read**, and Superpowers authoritative for **how to work**.

Do not replace OpenSpec, BMAD, project policy, or native execution state. Do not let CBM or an external rule pack select process rigor or silently become a blocking project policy.

## Core Rules

1. **Discover authorities first.** Read repository instructions, active SDD artifacts, Engineering Policy manifest, git state, quality policy, and native execution-status mechanisms.
2. **Load policy progressively.** Bootstrap loads the policy manifest and compact always-on MUST summaries; planning loads relevant summaries; implementation loads exact rules selected by semantic impact; review/verification load rule IDs plus evidence.
3. **Project context, do not dump it.** Build a Context Manifest from SDD, Decision, State, Semantic Impact, Engineering Policy, Verification, and Evidence; give each role/stage only a snapshot-bound Context Pack. Full graph/history/rule corpora stay lazy-retrieved.
4. **Project policy outranks external guidance.** ECC or other imported rules may supply defaults/reference material but MUST NOT lower project `MUST` rules or become blocking without explicit local promotion.
5. **Extract before resolving.** Mechanical proof may set Work Facts; heuristics are hints only.
6. **Measure semantic impact before final scope classification.** For code changes, use the V6 CBM path in `references/semantic-impact-engine.md`.
7. **Route policy from impact.** Use `scripts/policy_engine.py` to select rules by affected files, language, framework, layer, and impact signals.
8. **Provider evidence is structural, not governance authority.** CBM risk labels MUST NOT populate `risk.*`, choose Flow, satisfy policy, or satisfy gates.
9. **Unknown is not false.** Missing CBM edges, policy evidence, or provider availability never proves low impact or compliance.
10. **Compute; do not vibe-score.** Use `scripts/decision_engine.py --strict-evidence`; agents MUST NOT adjust scores or flow manually.
11. **Enforce MUST where possible.** Prefer deterministic tools such as ArchUnit, Semgrep, build/test/contract checks, or V6 policy checks over prompt-only compliance.
12. **Use one requirement authority and one execution-state authority per field.** Native state wins where the SDD tool owns it; augment only missing execution concerns.
13. **Guard every phase advance.** V5 state guards deny illegal transitions and close attempts lacking required policy/quality evidence.
14. **Fresh verification is snapshot-bound.** Material code/evidence/policy changes invalidate stale completion evidence.
15. **Reassess deterministically.** Material discoveries update evidence, rerun semantic impact + policy routing + Decision Engine, and reconcile SDD depth/state.
16. **Preserve invariants.** Required gates, root-cause debugging, traceability, scope discipline, native authority, project MUST policy, and fresh final verification cannot be optimized away.

## Orchestration

1. `DISCOVER` using `references/orchestration-model.md`.
2. `POLICY BOOTSTRAP` from `.orchestrator/policies/manifest.yaml` when present. Use packaged `policies/` only as starter templates.
3. `EXTRACT` repository/request facts using `scripts/fact_extractor.py`.
4. `IMPACT` code changes using CBM through `scripts/cbm_provider.py`; normalize to `semantic-impact.json`.
5. `POLICY ROUTE` with `scripts/policy_engine.py`; produce `policy-plan.json`, compact `policy-context.md`, and policy enforcement requirements. Optional ECC rules are discovered through `scripts/ecc_rules_adapter.py` as non-authoritative guidance.
6. `MAP` structural impact into Work Facts with `scripts/impact_mapper.py`.
7. `RESOLVE` remaining semantic facts with provenance using `scripts/fact_resolver.py`.
8. `CLASSIFY` with strict evidence using the Decision Engine.
9. `PLAN VERIFICATION` using `scripts/verification_planner.py`; merge semantic-impact requirements with project Policy Gates.
10. Select exactly one SDD adapter via `references/adapter-contract.md`.
11. Detect execution-state provider and initialize/resume Canonical Execution State.
12. Attach analysis + policy artifact refs to execution state; record applicable blocking Policy Gates.
13. `CONTEXT` build/refresh `context-manifest.json` and the current role/stage Context Pack using `scripts/context_plane.py`; validate freshness before relying on an older pack.
14. Compose Superpowers via `references/superpowers-policy.md` and implement one traceable slice at a time.
15. Record blockers, cursor, assignments, policy/quality gates, review, verification, snapshots, and current context refs durably.
16. Close only after state guards, project MUST policy, quality gates, SDD alignment, and fresh final verification all pass.

## Engineering Policy

Read `references/engineering-policy-layer.md` before editing project policy. For Spring Boot layered projects the starter policy expresses:

```text
web/controller -> service -> dao/repository
```

and blocks direct `web -> dao`, `service -> web`, and reverse DAO dependencies. `scripts/policy_engine.py evaluate` provides a conservative changed-file Java import check; use ArchUnit as the stronger project gate when available.

To install starter policy files into a repository:

```bash
python scripts/policy_bootstrap.py --repo .
```

ECC integration is optional. Read `references/ecc-rules-integration.md`; external ECC rules remain guidance until explicitly promoted into local machine policy.

## Context Plane

Read `references/context-plane.md`. `context-manifest.json` is an index of current truth; role/stage Context Packs are projections only. Regenerate a pack when its source hashes, execution revision, analysis snapshot, or policy snapshot changes. Never use a stale pack as evidence that the repository/state is unchanged.

## Preferred V6.2 entry point

```bash
python scripts/semantic_intake_pipeline.py \
  --repo . \
  --request-file request.md \
  --resolutions .orchestrator/fact-resolutions.json \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --context-role implementer \
  --context-stage implementation
```

CBM is the only V6 code-intelligence provider. If it is unavailable, fail closed by default; never reinterpret provider absence as low impact.

## Completion Contract

DONE means the authoritative SDD/native work item is complete **and** Canonical Execution State can legally transition to `closed/completed` with all REQUIRED quality and policy gates passing, no active blocker, acceptance satisfied, blocking review findings cleared, requirement → task → test → evidence traceability intact, and fresh verification against the final execution snapshot.
