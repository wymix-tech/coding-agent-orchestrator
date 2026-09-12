# Deterministic Work-Profile Decision Engine

## Purpose

Turn repository/request evidence into a reproducible Work Profile and Flow Profile. The engine is the final stage of a three-step intake pipeline:

1. **Mechanical extraction:** `fact_extractor.py` collects deterministic observations and only mechanically defensible facts.
2. **Semantic resolution:** unresolved facts are resolved against named evidence using `fact_resolver.py`; heuristic hints cannot finalize facts.
3. **Decision computation:** the same evidence-backed facts always produce the same scores, rules, and flow. Agents MUST NOT hand-adjust the computed result.

The decision is deterministic after fact resolution. Natural-language interpretation can still differ, so every resolved fact is auditable through provenance, unknowns remain `null`, and absence of a signal must not be silently treated as `false`.

## Input contract

Create a `work-facts.json` object with all required fields. Booleans must be `true` or `false`, never omitted. If a fact cannot be established after discovery, set it to `null`; classification is then `NEEDS_EVIDENCE`, not a guessed low score.

```json
{
  "ambiguity": {
    "goal_explicit": true,
    "acceptance_criteria_explicit": true,
    "boundaries_explicit": true,
    "conflicting_requirements": false,
    "multiple_observable_interpretations": false,
    "unresolved_external_contract": false
  },
  "complexity": {
    "predicted_components": 1,
    "architecture_decision_required": false,
    "concurrency_or_transaction": false,
    "distributed_coordination": false,
    "new_state_machine": false
  },
  "scope": {
    "files_estimate": 1,
    "modules_touched": 1,
    "deployable_units": 1,
    "external_consumers": false,
    "cross_team_contract": false,
    "public_contract_change": false
  },
  "risk": {
    "user_visible_failure": false,
    "persistent_data_change": false,
    "security_sensitive": false,
    "authn_authz": false,
    "cryptography_or_secrets": false,
    "payment_or_financial": false,
    "destructive_migration": false,
    "compliance_or_privacy": false,
    "production_infra": false,
    "irreversible_or_hard_to_recover": false
  },
  "novelty": {
    "exact_repo_precedent": true,
    "new_external_dependency_or_protocol": false,
    "first_repo_use": false,
    "unproven_architecture_assumption": false,
    "docs_or_examples_missing": false
  },
  "verification": {
    "deterministic_local": true,
    "requires_integration_boundary": false,
    "async_retry_timing": false,
    "compatibility_or_migration": false,
    "security_properties": false,
    "concurrency_or_distributed_faults": false,
    "performance_or_load": false,
    "special_harness_required": false
  },
  "policy": {
    "minimum_flow": null,
    "fixed_flow": null
  },
  "provenance": {
    "risk.authn_authz": [
      {
        "value": true,
        "source_type": "code_inspection",
        "source": "src/auth/token_validator.py#validate",
        "evidence": "Changed branch decides token validity.",
        "strength": "observed"
      }
    ]
  }
}
```

`provenance` maps fact paths to evidence entries. Accepted final strengths are `authoritative`, `observed`, and `derived`. `heuristic` is hint-only and is rejected by strict evidence validation. For non-authoritative `false` claims, provenance must also contain `negative_proof` describing the bounded scope that was inspected.

Use `references/fact-extractor.md` for the extraction/resolution protocol. Legacy `evidence` maps may be informative but do not satisfy v4 strict evidence mode.

## Fact extraction rules

Use these definitions literally.

### Ambiguity facts

- `goal_explicit=true`: desired observable outcome is stated, not merely a theme such as "optimize security".
- `acceptance_criteria_explicit=true`: success/failure can be checked from stated examples, invariants, or conditions.
- `boundaries_explicit=true`: in-scope and materially relevant out-of-scope behavior are identifiable.
- `conflicting_requirements=true`: two authoritative statements require incompatible observable behavior.
- `multiple_observable_interpretations=true`: at least two implementable interpretations produce different user/system behavior and the requirement does not choose between them. Record both interpretations in provenance evidence.
- `unresolved_external_contract=true`: behavior depends on an external/API/protocol contract that has not been established.

### Complexity facts

`predicted_components` counts logical components that need coordinated code changes, not files. A controller and helper in one cohesive component count as one. Set:

- `architecture_decision_required=true` only when implementation must choose or introduce a boundary, persistence/data-flow model, lifecycle/state model, extensibility mechanism, or cross-component pattern not already dictated by the repository.
- `concurrency_or_transaction=true` for explicit concurrency, locking, transactional consistency, or atomic multi-step behavior.
- `distributed_coordination=true` for consistency/coordination across processes/services/nodes, distributed locks, consensus-like behavior, or cross-service saga/orchestration.
- `new_state_machine=true` when new multi-state lifecycle semantics with meaningful transitions are introduced.

### Scope facts

- `files_estimate`: expected changed production/config/schema files; tests alone do not increase scope class.
- `modules_touched`: independently built/owned code modules or bounded components.
- `deployable_units`: services/apps/jobs/packages independently released or deployed.
- `external_consumers=true`: compatibility can affect consumers outside the repository/team-controlled deployment boundary.
- `cross_team_contract=true`: another team owns a contract or consumer that must coordinate with the change.
- `public_contract_change=true`: externally documented/public API, schema, CLI, SDK, wire format, or compatibility contract changes.

### Risk facts

Risk measures **consequence of a wrong implementation**, not implementation size.

- `user_visible_failure`: wrong behavior would affect users but is readily reversible without lasting data/security impact.
- `persistent_data_change`: wrong behavior can corrupt, lose, misinterpret, or materially mutate persisted data.
- `security_sensitive`: security boundary or control is affected even if not auth-specific.
- `authn_authz`: authentication, authorization, token/session validity, identity, or access decision logic changes.
- `cryptography_or_secrets`: cryptographic algorithms/protocol use, key lifecycle, credential/secret storage or transmission changes.
- `payment_or_financial`: money movement, billing, balances, settlement, pricing/accounting correctness.
- `destructive_migration`: migration can delete/rewrite data or is not safely backward reversible.
- `compliance_or_privacy`: regulated/private data handling or compliance control changes.
- `production_infra`: routing, deployment, cluster, production network, availability, or shared infrastructure control changes.
- `irreversible_or_hard_to_recover`: failure cannot be cheaply restored by rollback/retry/restore.

### Novelty facts

- `exact_repo_precedent=true`: same architectural/implementation pattern already exists in the repository and is applicable.
- `new_external_dependency_or_protocol=true`: introduces a new runtime dependency, external system, wire protocol, or platform mechanism.
- `first_repo_use=true`: this repository has no prior production use of the relevant mechanism.
- `unproven_architecture_assumption=true`: correctness depends on an assumption not established by repository evidence, docs, or a spike.
- `docs_or_examples_missing=true`: authoritative docs/examples needed to implement the mechanism are unavailable or materially incomplete.

### Verification facts

- `deterministic_local=true`: correctness can be demonstrated with deterministic local/unit checks and no external timing/state dependency.
- `requires_integration_boundary=true`: correctness requires exercising DB/network/filesystem/service/API integration.
- `async_retry_timing=true`: correctness depends on asynchronous delivery, retries, idempotency, scheduling, timeouts, or eventual behavior.
- `compatibility_or_migration=true`: backward/forward compatibility, schema evolution, migration, or rollout sequencing must be proven.
- `security_properties=true`: verification must demonstrate a security property or abuse boundary.
- `concurrency_or_distributed_faults=true`: races, process failures, network faults, ordering, partition-like behavior, or distributed coordination need validation.
- `performance_or_load=true`: correctness includes latency, throughput, capacity, or resource constraints.
- `special_harness_required=true`: proof requires a dedicated environment, fault injection, load harness, migration rehearsal, or other nonstandard setup.

## Scoring formulas

Scores are integers `0..3`, mapped to `low | medium | high | critical`. Scope also maps to `local | module | system | ecosystem`.

### Ambiguity

```text
points =
  +2 if goal_explicit == false
  +1 if acceptance_criteria_explicit == false
  +1 if boundaries_explicit == false
  +3 if conflicting_requirements == true
  +2 if multiple_observable_interpretations == true
  +1 if unresolved_external_contract == true

ambiguity = min(3, points)
```

An ambiguity score of `>=2` blocks implementation until clarification/specification reduces ambiguity. Score `3` does not mean "code more carefully"; it means "resolve the contradiction/unknown first".

### Complexity

```text
component_points = 0 if predicted_components <= 1
                   1 if predicted_components in [2,3]
                   2 if predicted_components >= 4

mechanism_points =
  +1 if architecture_decision_required
  +1 if concurrency_or_transaction
  +2 if distributed_coordination
  +1 if new_state_machine

complexity = min(3, component_points + mechanism_points)
```

### Scope

```text
scope = 3  if external_consumers OR cross_team_contract OR public_contract_change
        2  else if deployable_units >= 2 OR modules_touched >= 2
        1  else if files_estimate >= 2
        0  otherwise
```

Labels: `0=local, 1=module, 2=system, 3=ecosystem`.

### Risk

Risk is max-severity, not an average.

```text
risk = 3 if any(
    cryptography_or_secrets,
    payment_or_financial,
    destructive_migration,
    irreversible_or_hard_to_recover
)
else 2 if any(
    persistent_data_change,
    security_sensitive,
    authn_authz,
    compliance_or_privacy,
    production_infra
)
else 1 if user_visible_failure
else 0
```

Repository policy may add stronger domain-specific risk signals.

### Novelty

```text
novelty = 3 if unproven_architecture_assumption
              OR (first_repo_use AND new_external_dependency_or_protocol)
          2 if first_repo_use
              OR new_external_dependency_or_protocol
              OR (docs_or_examples_missing AND NOT exact_repo_precedent)
          1 if NOT exact_repo_precedent
          0 otherwise
```

### Verification difficulty

```text
verification_difficulty = 3 if concurrency_or_distributed_faults
                              OR special_harness_required
                          2 if any(
                              async_retry_timing,
                              compatibility_or_migration,
                              security_properties,
                              performance_or_load
                            )
                          1 if requires_integration_boundary
                              OR NOT deterministic_local
                          0 otherwise
```

## Deterministic flow algorithm

1. Validate Work Fact types. Boolean facts must be JSON booleans and count facts must be non-negative integers; invalid types => `INVALID_FACTS`.
2. Validate all required facts. Any `null`/missing required fact => `NEEDS_EVIDENCE`; do not classify it as low.
3. In strict mode, validate that every resolved fact has matching accepted provenance. Heuristic-only, missing, or improperly supported negative facts => `NEEDS_EVIDENCE`.
4. Compute six scores exactly as above.
5. Map scores to dimension-specific flow floors. For complexity/scope/risk/novelty/verification, use `0→TRIVIAL`, `1→FAST`, `2→STANDARD`, `3→DEEP`. For **ambiguity**, use `0→TRIVIAL`, `1→FAST`, `2→STANDARD`, `3→STANDARD + implementation blocker`. Ambiguity means "clarify first", not automatically "perform deep implementation".
6. Baseline flow = strongest dimension-specific floor.
7. Apply combination rules from `decision-rules.json`:
   - `C1`: four or more dimensions score `>=2` => minimum `DEEP`.
   - `C2`: ambiguity `>=2` and novelty `>=2` => minimum `DEEP`.
   - `C3`: complexity, risk, and verification difficulty all `>=2` => minimum `DEEP`.
8. Apply signal overrides from `decision-rules.json`.
9. Apply repository `policy.minimum_flow` as another floor.
10. If `policy.fixed_flow` is set below the computed minimum, return `POLICY_CONFLICT`. Never weaken safety/project minima.
11. Produce `required_activities`, `implementation_blockers`, and a rule trace.

Tie-breaking is unnecessary because flows are totally ordered: `TRIVIAL < FAST < STANDARD < DEEP`. Ambiguity remains orthogonal through `implementation_blockers`; after clarification, rebuild facts and recompute before coding.

## Required activity derivation

These are deterministic consequences of scores:

| Trigger | Required activity |
|---|---|
| ambiguity >= 1 | clarify missing requirement facts |
| ambiguity >= 2 | explicit acceptance criteria + resolve ambiguity before implementation |
| complexity >= 2 | explicit design + dependency/task decomposition |
| scope >= 2 | impact map + contract/compatibility check |
| risk >= 2 | explicit risk analysis + independent review |
| novelty >= 2 | research/spike + validate assumptions before implementation |
| verification difficulty >= 2 | explicit verification plan |
| flow >= STANDARD | written implementation plan + spec-compliance review + code-quality review |
| flow == DEEP | adversarial/risk-focused review + release-readiness check |

Repository gates remain authoritative and can only add requirements.

## Reassessment

Re-run the **same engine** whenever facts change. Do not create a separate escalation heuristic.

```text
new_flow_rank > old_flow_rank  → ESCALATE
new_flow_rank = old_flow_rank  → CONTINUE
new_flow_rank < old_flow_rank  → DE_ESCALATE only if no unresolved blocker or repository minimum prevents it
null/new unknown critical fact → NEEDS_EVIDENCE / BLOCK implementation
```

Create a new extraction snapshot, record old/new facts and provenance, old/new rule trace, and reason. This makes escalation auditable rather than intuitive.

## Executable reference

Run:

```bash
# Direct strict-mode example
python3 scripts/decision_engine.py examples/work-facts-auth-strict.json --strict-evidence --pretty

# Real intake pipeline
python3 scripts/fact_extractor.py --repo . --request-file request.txt --output .orchestrator/intake/work-facts.draft.json
python3 scripts/fact_resolver.py .orchestrator/intake/work-facts.draft.json fact-resolutions.json --output .orchestrator/intake/work-facts.resolved.json
python3 scripts/decision_engine.py .orchestrator/intake/work-facts.resolved.json --strict-evidence --pretty
python3 -m unittest discover -s tests -v
```

The reference implementation uses only Python's standard library. The JSON rules file is declarative documentation for policy review; the Python implementation mirrors the same formulas and emits rule IDs so deviations are visible in tests/review.
