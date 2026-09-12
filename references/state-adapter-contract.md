# Execution State Adapter Contract

The SDD adapter governs planning semantics. The **Execution State Adapter** governs how native execution/progress state maps to the canonical Execution State Manager.

## Required functions

### `detect_state_provider`
Return provider, authority mode, confidence, native state references, and evidence.

### `load_native_state`
Read the tool's native state without mutating it.

### `project_to_canonical`
Map native status/progress into canonical `phase`, `status`, and progress fields. Preserve native identifiers and source revision/hash.

### `propose_native_transition`
Translate a desired canonical transition into the native action required by the installed tool/version.

### `apply_native_transition`
Perform the native state transition only through the supported native mechanism. Never edit a native state file by guessing its schema.

### `sync_extension_state`
Update only orchestrator-owned extension fields: blockers, assignments, gates, evidence, review, verification, cursor, snapshot.

### `reconcile`
Detect drift between native and canonical projection. Drift must become a conflict/blocker, never last-write-wins synchronization.

### `resume`
Produce a canonical resume summary from native state plus orchestrator extension state.

## Authority invariant

Exactly one authority owns each field.

```text
Native Authority
  phase/status/progress -> native SDD
  gates/evidence/blockers -> orchestrator extension

Hybrid Authority
  planning readiness -> native SDD
  execution phase/status/gates -> orchestrator

Orchestrator Authority
  execution state -> orchestrator
  requirements/specs -> existing SDD/project artifact
```

Do not duplicate the same writable field in two systems.

## Conflict policy

If native and extension state disagree on a native-owned field:

1. stop automatic advancement;
2. record a state-sync conflict blocker;
3. reload native state;
4. reconcile explicitly;
5. append the reconciliation event.

Never silently overwrite native authority.
