# Quality Gates

## Principle

Project policy owns thresholds. The orchestrator discovers and enforces them; it MUST NOT invent coverage, mutation, lint, or security thresholds.

Adaptive orchestration changes **breadth/depth of applicable checks**, not the pass criteria of REQUIRED gates.

## Gate discovery priority

1. Explicit repository orchestrator config.
2. CI workflow/build scripts.
3. Project test/lint/static-analysis configuration.
4. Contributing/engineering documentation.
5. User-provided thresholds for the current change.

## Common gate families

- build / compile / type-check
- unit / integration / end-to-end tests
- lint / formatting
- static analysis / SAST (for example Semgrep when configured)
- dependency/security checks
- line / branch coverage
- mutation testing
- API/schema compatibility
- migrations and rollback checks
- performance/regression checks
- code review

## Adaptive breadth

Typical routing, unless stricter repository policy exists:

- **TRIVIAL:** targeted build/test/lint checks affected by the change.
- **FAST:** targeted unit/integration checks plus relevant static/compatibility checks.
- **STANDARD:** all relevant repository gates for affected components, plus spec/code review.
- **DEEP:** STANDARD plus risk-driven security, compatibility, migration, performance, failure-mode, or adversarial checks when applicable.

A gate classified REQUIRED by project policy blocks completion at every flow level where it applies.

## Evaluation

```text
REQUIRED    failure blocks completion
ADVISORY    failure must be surfaced but does not block
NOT_APPLICABLE
UNKNOWN     do not guess; report missing policy
```

Run narrow checks during the inner loop and all applicable REQUIRED gates before completion.

## Evidence rules

For every REQUIRED gate record command/workflow, result, relevant summary, freshness when available, and authorized exceptions/waivers.

No green label without executable evidence.

## Execution State Manager integration (v5)

REQUIRED gates must be recorded in Canonical Execution State with status and evidence. A REQUIRED gate can never be recorded as `skipped` or `not_required`. The `closed` transition is denied while any REQUIRED gate is not `passed`.

Final verification is separate from individual gates and is bound to `execution_snapshot_id`; any material snapshot change invalidates previous final-verification freshness.

## Verification Planner

`scripts/verification_planner.py` derives verification **requirements/candidates** from
semantic impact, such as integration, contract compatibility, async failure/timing, or
broader regression checks. It never downgrades repository/native REQUIRED gates.

The distinction is intentional:

```text
Semantic Impact -> what extra verification the change needs
Project Policy  -> what gates are mandatory regardless of impact
```

Both must be satisfied before DONE when classified as REQUIRED.

## Engineering Policy gates

Project Engineering Policy may map `MUST` rules to one or more executable gates. The router emits these in `policy-plan.json`; `verification-plan.json` carries them under `policy_gates`, and Canonical Execution State records blocking gates when state sync is enabled.

Examples:

- layered dependency direction -> ArchUnit and/or `v6-policy-check`;
- controller/service responsibility -> code review or static analysis;
- API compatibility -> contract tooling;
- security invariants -> Semgrep/SAST/security tests.

An external rule source such as ECC is guidance by default. It cannot become a REQUIRED gate merely because it was discovered. Promotion to blocking behavior requires an explicit repository-owned policy mapping.

A project `MUST` that has no executable enforcement is not silently considered satisfied: keep its gate pending and require review/evidence or add a deterministic checker.
