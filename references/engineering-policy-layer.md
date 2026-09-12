# Engineering Policy Layer

## Purpose

Engineering Policy answers **what implementations are allowed**. Keep it separate from:

- SDD: what/why to build.
- Decision Engine: how much process is required.
- Semantic Impact: what is affected.
- Superpowers: how the agent works.
- Execution State Manager: whether the work may advance/close.

## Storage

Project-owned policy lives under `.orchestrator/policies/` (the packaged `policies/` directory is a starter template):

```text
.orchestrator/policies/
  manifest.yaml
  common-engineering.yaml
  spring-boot-layered.yaml
```

Prefer stable rule IDs such as `ARCH-SPRING-LAYER-001` so planning, review, CI, and evidence can reference the same invariant.

## Levels

- `MUST`: blocking invariant. It must have executable evidence or an explicit approved waiver mechanism outside this policy-layer scope.
- `SHOULD`: review-level expectation; deviations require rationale but do not automatically block.
- `PREFER`: advisory convention.

Higher-precedence policy may strengthen a rule. A lower level MUST NOT silently downgrade an existing stronger rule.

## Loading model

Do not put the full policy corpus in every prompt.

1. **Bootstrap:** load the manifest and compact always-on MUST summaries.
2. **Planning:** route policies using Work Facts + semantic impact; load relevant rule summaries.
3. **Implementation:** use changed/affected files, language, framework, layer, and impact signals to load exact rules.
4. **Review:** load applicable rule IDs plus enforcement results/violations.
5. **Verification:** require evidence for blocking policy gates before DONE.

This is `manifest -> route -> exact rules -> enforcement evidence`, not `all rules -> prompt`.

## Enforcement

Policy definitions may map to multiple enforcement engines:

- `v6-policy-check`: lightweight repository checks shipped here.
- `archunit`: recommended for Java/Spring dependency direction.
- `semgrep` / static analysis.
- project tests / contract checks.
- code review for semantic responsibilities that are difficult to prove mechanically.

A project SHOULD prefer a dedicated deterministic tool (for example ArchUnit for Java layering) over relying on prompt compliance alone.

## Spring layered example

Canonical direction:

```text
web/controller -> service -> dao/repository
```

Forbidden examples include `web -> dao`, `service -> web`, and `dao -> service/web`.

The starter `spring-boot-layered.yaml` expresses these as machine policy. `scripts/policy_engine.py evaluate` contains a conservative Java import checker for changed files, while ArchUnit remains the stronger recommended enforcement gate.

## Context authority

Policy text does not replace requirement authority. If an SDD requirement conflicts with a project MUST, the orchestrator must surface the conflict and block implementation until it is resolved; it must not silently choose one.
