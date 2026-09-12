# OpenSpec Execution State Adapter

## Authority mode

Default to `hybrid` when OpenSpec governs the change:

```yaml
provider: openspec
authority_mode: hybrid
```

OpenSpec remains authoritative for change/artifact/task planning state. The orchestrator augments runtime agile state that OpenSpec does not fully represent.

## Native-owned concepts

Prefer native OpenSpec mechanisms for:

- active change identity;
- proposal/spec/design/task artifact readiness;
- task checklist/progress where available;
- verify/archive/completion semantics.

The adapter turns these into readiness evidence such as `sdd_ready` and task progress.

## Orchestrator-owned execution concepts

The canonical state may own:

- implementation/review/verification phase;
- blockers;
- role assignments;
- quality gates;
- review findings;
- fresh verification snapshot;
- next action;
- append-only history.

## Reconciliation

If OpenSpec task/artifact state changes outside the orchestrator, reload it and reconcile before advancing execution state. Native task incompleteness must not be hidden by an orchestrator `implementation_tasks_complete=true` flag.

## Close

Only invoke native OpenSpec completion/archive semantics after canonical close guards pass. Then record the native closure reference in history/evidence.
