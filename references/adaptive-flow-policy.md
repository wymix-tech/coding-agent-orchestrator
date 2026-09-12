# Adaptive Flow Policy

## Goal

Generate the **minimum sufficient development process** from objective facts, not agent instinct or installed-tool ceremony.

## Deterministic classification

Use `decision-engine.md` as the normative decision procedure and `scripts/decision_engine.py` as the executable reference. Do not freehand `low/medium/high` labels.

The Work Profile contains six scored dimensions:

```yaml
work_profile:
  ambiguity: {score: 1, level: medium}
  complexity: {score: 2, level: high}
  scope: {score: 1, level: module}
  risk: {score: 0, level: low}
  novelty: {score: 0, level: low}
  verification_difficulty: {score: 1, level: medium}
  decision_trace: ["complexity: component_points=1 + architecture=1"]
```

Facts must be backed by evidence. Missing facts stay unknown and produce `NEEDS_EVIDENCE`; they must never default to low risk/complexity.

## Flow Profiles

### TRIVIAL

Localized, clear, low-risk work whose six dimensions all score 0 after discovery.

```text
REQUEST → IMPACT CHECK → IMPLEMENT → TARGETED VERIFY → DONE
```

### FAST

At least one dimension scores 1, none scores above 1, and no override raises the floor.

```text
REQUIREMENT → MINI SPEC / AC AS NEEDED → TASK → IMPLEMENT → FOCUSED REVIEW → VERIFY
```

### STANDARD

At least one dimension scores 2, none forces DEEP, or a policy/override sets STANDARD as the minimum.

```text
CLARIFY → SPEC → DESIGN AS REQUIRED → TASKS → PLAN → IMPLEMENT
        → SPEC REVIEW → CODE REVIEW → QUALITY GATES → VERIFY
```

### DEEP

Complexity, scope, risk, novelty, or verification difficulty scoring 3 can force DEEP; combination rules and overrides can also force it. Ambiguity score 3 by itself produces STANDARD planning plus an implementation blocker, then requires reclassification after clarification.

```text
DISCOVERY → REQUIREMENTS/CONSTRAINTS → ARCHITECTURE/ALTERNATIVES
          → SPEC/DESIGN → RISK ANALYSIS → TASK DECOMPOSITION
          → INCREMENTAL IMPLEMENTATION → ADVERSARIAL REVIEW
          → EXTENDED GATES → RELEASE READINESS → VERIFY
```

## Risk and policy overrides

Overrides are floors, not suggestions. Authentication/authorization has minimum STANDARD treatment; cryptography/secrets, payments, destructive migrations, and hard-to-recover changes are DEEP by default. Repository policy may define stricter minima.

A user/requested fixed flow may increase rigor. It may not force a flow below computed or repository minima.

## Reassessment

Re-run the exact same decision engine after material discoveries, failures, completed implementation slices, or scope changes. Compare flow ranks to choose `CONTINUE`, `ESCALATE`, or `DE_ESCALATE`.

De-escalation removes unnecessary **future** ceremony only. It cannot erase evidence, already-applicable REQUIRED gates, or domain-specific minimum flow rules.

## Minimum-sufficient principle

```text
ambiguity ↑               → discovery/clarification depth ↑
complexity ↑              → design/decomposition depth ↑
scope ↑                   → impact/compatibility depth ↑
risk ↑                    → review/gate depth ↑
novelty ↑                 → research/assumption validation ↑
verification difficulty ↑ → verification breadth ↑
```
