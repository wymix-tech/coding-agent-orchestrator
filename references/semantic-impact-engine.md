# V6 Semantic Impact Engine

## Goal

Reduce subjective scope/verification classification by answering:

> What does this source change structurally affect?

The engine is a radar, not a workflow authority.

## Pipeline

```text
Git / source snapshot
      |
      v
CBM index + detect_changes
      |
      v
CBM Provider Adapter
      |
      v
semantic-impact.json
      |
      +--> Impact Mapper --> evidence-backed Work Facts
      |
      +--> Verification Planner
      |
      v
V3 Decision Engine
      |
      v
V5/V6 Execution State Manager
```

## Canonical impact concepts

### Change

- changed files;
- changed symbols;
- symbol kind / qualified name / source path.

### Blast radius

- impacted symbols;
- direct/transitive dependents when distance is available;
- affected files/modules/services/projects.

### Boundary evidence

- call/import relationships;
- HTTP/service relationships;
- async producer/consumer relationships;
- data-flow/read/write relationships;
- cross-project relationships.

### Contract evidence

- changed Route nodes;
- OpenAPI/Swagger/Protobuf/GraphQL artifacts.

## Work-Fact mapping

V6 may resolve/raise only facts supported by structural evidence, such as:

```text
complexity.predicted_components
complexity.distributed_coordination
scope.modules_touched
scope.deployable_units
scope.external_consumers
scope.public_contract_change
verification.deterministic_local
verification.requires_integration_boundary
verification.async_retry_timing
verification.compatibility_or_migration
```

It MUST NOT infer authentication, payment, privacy, cryptography, business consequence,
or acceptance semantics from graph topology alone.

## Superseding narrower estimates

Example:

```text
V4 changed-file scan:
  modules_touched = 1

CBM blast radius:
  affected modules = 3
```

V6 raises the scope value to 3, marks the previous evidence `superseded`, and records the
CBM snapshot as the replacement provenance. This is an evidence refinement, not manual
score adjustment.

## Verification planning

The planner derives checks from semantic facts:

```text
changed executable symbol -> targeted unit/component checks
runtime boundary          -> integration checks
public contract            -> compatibility/contract checks
async boundary             -> async failure/timing checks
security Work Fact         -> security regression checks
data/migration Work Fact   -> migration/recovery checks
large/standard/deep impact -> broader regression
```

Repository quality policy can add stronger REQUIRED gates. The planner cannot downgrade
existing REQUIRED gates.

## Provider risk quarantine

Any CBM risk label is provider opinion only. It is useful for troubleshooting or reviewer
context, but flow selection always comes from deterministic Work Facts and V3 rules.

## Pagination / completeness invariant

Current CBM `detect_changes` can page changed files, impacted symbols, and summaries.
Therefore V6 records:

```yaml
completeness:
  status: complete | partial
  complete: true | false
  continuation_signals: []
```

A partial page is positive evidence for relationships it contains, but its counts are not
safe upper bounds. V6 therefore does not finalize numeric component/module/deployable
estimates from a partial result and the semantic intake pipeline defaults to
`NEEDS_EVIDENCE` until continuation pages are collected. `--allow-partial-impact` is an
explicit escape hatch, never the default.
