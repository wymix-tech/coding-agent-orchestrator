# Generic Execution State Adapter

## Authority mode

When no native execution-state mechanism exists:

```yaml
provider: generic
authority_mode: orchestrator
```

`.orchestrator/execution-state.yaml` becomes the execution-state authority, while existing issues/specs/RFCs/ADRs remain requirement authorities.

## Required behavior

- Initialize a canonical state document for the active work item.
- Record every mutation in append-only history.
- Require transition guards before phase advancement.
- Use optimistic revision checks for concurrent/multi-agent writes.
- Record blockers instead of overwriting phase/status with `blocked`.
- Keep final verification tied to the current execution snapshot.
- Use `resume` as the handoff contract between agents/sessions.

If a native project-management system is later connected, migrate field authority explicitly rather than running two writable state systems indefinitely.
