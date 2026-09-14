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

State and history are committed together through a short cross-process critical section plus a durable transaction journal. The journal is recovered before state is observed, so an interrupted commit resolves to one complete revision rather than a state/history split. Lock acquisition is bounded by `ORCHESTRATOR_STATE_LOCK_TIMEOUT` (default 5 seconds); dead owners are reclaimed. CBM, tests, and other long-running work must execute outside this commit lock.

## Optimistic concurrency

Every mutable state document has an integer `revision`.

A state-changing command SHOULD include `expected_revision`. If another agent has advanced the state, the mutation fails with a revision conflict. The agent must reload, reconcile, and retry intentionally.

This prevents multi-agent last-write-wins corruption.

## Transition guards

`transition_guard`, committed transitions, `completion_status`, CLI `check`/`verify`,
host hooks, and resume/start guidance consume `scripts/action_guard.py`.
Use [Action Authorization](action-authorization.md) for the shared action contract,
requirements by phase, denial codes, and migration rules. Do not implement a second
set of transition conditions in an adapter.

Pass the repository root to read-only Python helpers so they can verify live inputs;
a state-only query cannot establish permission or completion. A denied transition
returns the same structured `authorization` result as the corresponding `check`.
Recheck during the actual transition; an earlier successful check is not a permit.

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

`execution_snapshot_id` identifies the current execution snapshot. Analysis also
binds a `repository_snapshot_id` and hashes of authoritative inputs into an
`evidence_snapshot_id`. Each gate, review, and final-verification result records both
the execution and evidence snapshot it covers. Reusing an analysis ID does not make
old evidence valid after requirement, code, or policy content changes.

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
Only records without `resolved_at` remain active; resolved history must not block resume.

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

This prepares the state model for future multi-agent scheduling without coupling the state model to a scheduler.

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
  historically_completed: false
  completion_recorded: false
  governance_close_ready: false
  done: false
  close_guard_failures:
    - final verification has not passed
```

A legal Orchestrator close writes `completion_record` with work/requirement identity, execution/analysis/repository snapshots, actor, time, and the allow decision. After that transition, `done` is a historical fact and later repository changes do not resurrect the work item; `governance_close_ready` may still become false because it evaluates live evidence. Native authority merely reporting `closed/completed` without a Governance completion record is **not** Global Done. For migration, legacy orchestrator-owned canonical `closed/completed` states remain historically complete; native-only legacy closed states remain not done.

## Analysis attachments

Durable execution state includes an `analysis` projection:

```yaml
analysis:
  semantic_impact_ref: .orchestrator/intake/semantic-impact.json
  work_facts_ref: .orchestrator/intake/work-facts.resolved.json
  decision_ref: .orchestrator/intake/decision.json
  verification_plan_ref: .orchestrator/intake/verification-plan.json
  analysis_snapshot_id: <snapshot>
  repository_snapshot_id: worktree:<content-hash>
  evidence_snapshot_id: <bound-inputs-hash>
  authority_hashes: {<source-ref>: <content-hash>}
  requirement_ref: <authoritative-requirement>
  decision_status: CLASSIFIED
  semantic_complete: true
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
Attaching `NEEDS_EVIDENCE` or incomplete semantic impact preserves dirty state and
does not authorize implementation or phase advancement. Refresh the Context Manifest
after attaching analysis; state/history projections are checked directly from current
state and do not invalidate their own authority inputs.


## Flow reconciliation

When deterministic reassessment changes Flow, reconcile it durably:

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  flow --flow DEEP --actor orchestrator \
  --evidence-ref .orchestrator/intake/decision.json
```

Raising to STANDARD/DEEP activates required review. A later de-escalation does not
automatically erase an already-required review obligation.

## Engineering Policy integration

When policy is enabled, `analysis` may include `policy_plan_ref`, `policy_evaluation_ref`, `policy_context_ref`, and `policy_snapshot_id`. These are Orchestrator-owned metadata even when native SDD state is authoritative for phase/status.

Blocking policy enforcement is recorded through normal `quality_gates` with stable names such as:

```text
policy:ARCH-SPRING-LAYER-001:architecture-layers
```

A REQUIRED policy gate behaves exactly like any other REQUIRED gate: it cannot be skipped or marked not-required, and close is denied until it passes. Policy routing/evaluation artifacts explain why the gate exists; the gate evidence proves that the implementation complies.

## Context Plane integration

`analysis` may also carry stable references to the current Context Manifest and the pack generated for the current role/stage:

```yaml
analysis:
  context_manifest_ref: .orchestrator/intake/context-manifest.json
  context_pack_ref: .orchestrator/intake/context-pack.implementer.implementation.json
```

These refs are navigation metadata only. The Context Manifest/Pack do not own phase, status, requirement, policy, or verification truth. Before a new agent relies on a pack, refresh or validate it with `scripts/context_plane.py`; a stale pack must not be treated as current repository state.
