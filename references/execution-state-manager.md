# Execution State Manager

## Purpose

The Execution State Manager is the durable agile/runtime layer between SDD planning and coding execution. It answers:

- What work item is active?
- Which phase is it in?
- Is it blocked, and why?
- Who owns the current role/task?
- Which native SDD state is authoritative?
- Which quality/review/verification gates have passed?
- What is the next legal action?
- Can a new agent resume safely from repository state alone?

It does **not** replace the requirement/specification source of truth.

## Three authority modes

### `native`
Use when the SDD tool already has an authoritative execution-status mechanism, such as a BMAD sprint-status file.

- Native tool owns canonical phase/status/progress.
- Orchestrator stores extension state: blockers, assignments, quality gates, evidence, verification, cursor, snapshot.
- Phase/status changes must be performed by the native adapter first, then projected with `sync-native`.
- Direct orchestrator transitions require explicit `native_confirmed` evidence and must never silently diverge from native state.

### `hybrid`
Use when the SDD tool owns planning/artifact progress but lacks a complete agile execution state.

Typical OpenSpec mode:

- OpenSpec owns proposal/spec/design/tasks/artifact readiness.
- Orchestrator owns implementation/review/verification execution state, blockers, assignments, gates, and history.
- Native planning state must be reconciled before `sdd_ready=true`.

### `orchestrator`
Use when no usable native execution-state mechanism exists.

- `.orchestrator/execution-state.yaml` is the execution-state authority.
- Existing repository issue/spec artifacts remain authoritative for requirements.

## Canonical state model

Use orthogonal `phase` and `status` fields instead of exploding status names.

Phases:

```text
discovery
specification
design
planning
implementation
review
verification
release
closed
```

Statuses:

```text
pending
ready
in_progress
completed
failed
cancelled
```

`blocked` is a modifier, not a phase. A work item may be:

```yaml
phase: implementation
status: in_progress
blocked: true
```

This preserves where work stopped while recording that it cannot advance.

## Durable repository state

Default layout when the orchestrator owns or augments execution state:

```text
.orchestrator/
├── execution-state.yaml
├── execution-history.jsonl
├── work-facts.json
├── decision.json
└── evidence/
```

`execution-history.jsonl` is append-only. Do not rewrite historical events.

## Optimistic concurrency

Every mutable state document has an integer `revision`.

A state-changing command SHOULD include `expected_revision`. If another agent has advanced the state, the mutation fails with a revision conflict. The agent must reload, reconcile, and retry intentionally.

This prevents multi-agent last-write-wins corruption.

## Transition guards

The reference implementation enforces these minimum guards:

### Enter `implementation`

- SDD/native planning state is `sdd_ready`.
- If behavior changes, acceptance criteria are present.
- No active blocker prevents phase advance.

### Enter `review`

- Implementation tasks are complete.
- No active blocker prevents phase advance.

### Enter `verification`

- Implementation tasks are complete.
- If review is required by the flow, review passed and no blocking finding remains.

### Enter `closed`

- No active blocker remains.
- Implementation tasks are complete.
- Acceptance criteria are satisfied.
- Every REQUIRED quality gate passed.
- Required review passed with no blocker.
- Final verification passed.
- Verification is fresh for the current execution snapshot.

Repository/native policy may add stricter guards but MUST NOT weaken these invariants.

## Flow-aware review

Reference minimum:

```text
TRIVIAL   review optional
FAST      review optional unless repository/risk policy requires it
STANDARD  review required
DEEP      review required
```

Quality policies can raise this floor.

## Fresh verification

`execution_snapshot_id` identifies the current material code/evidence snapshot. Final verification records the snapshot it verified.

If the execution snapshot changes after verification:

```text
verification.fresh = false
```

The work item cannot close until fresh verification is rerun against the new snapshot.

## Blockers

Blockers are first-class, durable records:

```yaml
blocked: true
blockers:
  - id: blk-...
    type: dependency
    reason: auth API contract unresolved
    evidence_ref: openspec/changes/auth/spec.md
```

Resolving a blocker appends a history event. It does not erase the blocker record.

## Assignments

Assignments are role-based rather than assuming one agent owns the entire change:

```yaml
assignments:
  implementer:
    assignee: agent-impl-1
  reviewer:
    assignee: agent-review-2
  verifier:
    assignee: agent-verify-1
```

This prepares the state model for future multi-agent scheduling without coupling v5 to a scheduler.

## Resume contract

A replacement agent should be able to call `resume` and obtain, without relying on chat history:

- active work item and iteration;
- native authority/provider;
- phase/status/blockers;
- assignments;
- current task cursor;
- readiness flags;
- quality/review/verification state;
- next legal action;
- current revision.

Chat history may enrich context but must not be required for execution recovery.

## Reference CLI

```bash
python scripts/state_provider_detector.py .

python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  init --work-id CHG-42 --title "Login reporting" \
  --flow STANDARD --provider openspec --authority-mode hybrid

python scripts/execution_state_manager.py --state .orchestrator/execution-state.yaml resume
```

Every mutating command should pass `--expected-revision` in multi-agent or concurrent environments.

## Native completion vs governance completion

A native provider may report its story/change as done before orchestrator-wide gates are satisfied. Therefore `resume` exposes a separate completion projection:

```yaml
completion:
  native_or_canonical_closed: true
  governance_close_ready: false
  done: false
  close_guard_failures:
    - final verification has not passed
```

Global DONE is true only when the native/canonical work item is closed **and** all orchestrator close guards pass. Never treat a native `done` token alone as sufficient proof of completion.

## V6 analysis attachments

V6 adds an `analysis` projection to durable execution state:

```yaml
analysis:
  semantic_impact_ref: .orchestrator/intake/semantic-impact.json
  work_facts_ref: .orchestrator/intake/work-facts.resolved.json
  decision_ref: .orchestrator/intake/decision.json
  verification_plan_ref: .orchestrator/intake/verification-plan.json
  analysis_snapshot_id: <snapshot>
  provider: codebase-memory-mcp
```

Attach with:

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  analysis \
  --analysis-snapshot-id "$SNAPSHOT" \
  --semantic-impact-ref .orchestrator/intake/semantic-impact.json \
  --work-facts-ref .orchestrator/intake/work-facts.resolved.json \
  --decision-ref .orchestrator/intake/decision.json \
  --verification-plan-ref .orchestrator/intake/verification-plan.json \
  --actor orchestrator
```

A changed analysis snapshot also advances `execution_snapshot_id`. If prior final
verification was against another snapshot, it becomes stale automatically.


## V6 flow reconciliation

When deterministic reassessment changes Flow, reconcile it durably:

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  flow --flow DEEP --actor orchestrator \
  --evidence-ref .orchestrator/intake/decision.json
```

Raising to STANDARD/DEEP activates required review. A later de-escalation does not
automatically erase an already-required review obligation.
