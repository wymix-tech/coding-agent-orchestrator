# State and Evidence

## Two independent state dimensions

Do not conflate specification readiness with execution progress.

```text
SDD Planning State                 Agile Execution State
proposal/spec/design/tasks         phase/status
artifact dependencies              blockers
native story readiness             assignments
                                   task cursor
                                   quality gates
                                   review
                                   verification
```

The SDD adapter owns planning semantics. The Execution State Manager owns or projects execution semantics according to field authority.

## Canonical execution state

```text
phase × status + blocked modifier
```

Phases:

```text
discovery → specification → design → planning → implementation → review → verification → release → closed
```

`blocked` never overwrites the current phase.

## State authority

```text
BMAD with sprint-status     -> native
OpenSpec                    -> hybrid by default
Generic/no native status    -> orchestrator
```

This is a default mapping, not permission to ignore repository-specific tooling.

## Transition guards

- Enter implementation only when native/SDD planning is ready and behavior changes have acceptance criteria.
- Enter review only when implementation tasks are complete.
- Enter verification only when required review has passed and no blocking review finding remains.
- Enter closed only when acceptance is satisfied, REQUIRED gates pass, blockers are clear, and fresh verification matches the current execution snapshot.

## Durable history

Every mutation appends an event to `execution-history.jsonl` with:

```text
event_id
timestamp
actor
work_item_id
revision_before
revision_after
event type
reason/evidence/native reference as applicable
```

History is append-only evidence, not a second source of requirements.

## Concurrency invariant

Every mutable state has a monotonic `revision`.

```text
read revision 12
Agent A writes expected_revision=12 -> revision 13
Agent B writes expected_revision=12 -> CONFLICT
```

Agent B must reload/reconcile instead of overwriting Agent A.

## Fresh verification invariant

```text
execution_snapshot_id = S42
verification.snapshot_id = S42
verification.status = passed
verification.fresh = true
```

If code/evidence changes to `S43`, the state manager invalidates freshness until verification runs again.

## Evidence ledger

```markdown
## Change Evidence
- SDD authority: <tool + native change/story id>
- Execution state authority: <provider + native|hybrid|orchestrator>
- Execution state revision: <n>
- Native state reference/revision: <ref>
- Extraction snapshot: <snapshot_id>
- Work Facts + provenance: <path/hash>
- Work Profile: <six deterministic dimensions>
- Flow profile: <TRIVIAL|FAST|STANDARD|DEEP>
- Decision trace: <BASE/C*/O*/P* rule IDs>
- Active phase/status: <phase>/<status>
- Blockers: <active blocker IDs or none>
- Assignments: <role -> assignee>
- Active task cursor: <task id/title>
- Quality gates: <required status + evidence>
- Review: <status + blocking findings + evidence>
- Verification: <status + snapshot + freshness + evidence>
- Reassessment history: <old/new facts and flow>
- Final status: IN_PROGRESS | BLOCKED | VERIFIED/CLOSED
```

## Traceability invariant

```text
Requirement / Story
      ↓
Acceptance Criterion
      ↓
Implementation Task
      ↓
Test / Check
      ↓
Fresh Verification Evidence
      ↓
Legal CLOSED transition
```

A broken link means the work item is not ready to close.

## V6 semantic-impact lineage

For code changes, extend the traceability chain:

```text
Requirement / SDD
  -> Git/source snapshot
  -> CBM changed symbol / graph edge / blast radius
  -> semantic-impact snapshot
  -> Work Fact + provenance
  -> Decision rule / Flow
  -> Verification Plan
  -> Task / test / gate evidence
  -> Fresh final verification
```

`semantic-impact.json` is evidence, not a requirement source and not a state authority.
CBM provider opinions never replace Orchestrator Work Facts.
