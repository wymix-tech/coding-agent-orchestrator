# SDD Adapter Contract

Every SDD adapter answers the same planning questions without erasing the native workflow. The Decision Engine selects process depth; adapters translate that depth into native SDD artifacts. Execution status is delegated to the separate Execution State Adapter contract.

## Required functions

### detect
Return confidence and evidence that this adapter governs the current change.

### locate_active_change
Find the current proposal/change/story/task set. Prefer artifacts already in progress over creating new ones.

### ensure_ready_for_implementation
Determine whether the native SDD system has enough approved/usable information to code safely.

Return:

```text
READY
NEEDS_DISCOVERY
NEEDS_SPEC
NEEDS_DESIGN
NEEDS_TASKS
BLOCKED
```

### normalize
Map native artifacts to the normalized change model without creating a competing source of truth.

### planning_depth
Given the computed Flow Profile and `required_activities`, identify the minimum native artifacts needed for implementation readiness.

### satisfy_requirement_blockers
Map ambiguity/requirement blockers into native clarification/specification actions. After blockers resolve, rerun classification before implementation.

### reassess_readiness
After Work Facts change and the engine recomputes a new profile, determine whether native artifacts remain sufficient.

### next_planning_action
Choose the native SDD action that advances readiness. Do not invent commands unsupported by the installed version.

### task_cursor
Return the next executable native task/story and acceptance criteria.

### record_progress
Delegate execution/status updates to `references/state-adapter-contract.md`. Do not implement a second status authority inside the planning adapter.

### reconcile_scope_change
When implementation discovers a spec gap, identify which native artifact must be updated before coding continues.

### verify_alignment
Check finished behavior against native requirements/stories and acceptance criteria.

### close_change
Use native closure/archive/completion semantics only after canonical execution close guards and quality/verification gates pass.

## Adapter invariants

- Native SDD artifacts remain authoritative for requirements/planning.
- The orchestrator may record facts/decisions/evidence, but not a competing requirements source.
- Execution-state field authority is governed by `state-adapter-contract.md`.
- The adapter MUST consume computed flow/profile data; it MUST NOT rescore work independently.
- Adapter-specific commands are discovered from the repository/tool version when possible.
- Never mark native tasks/story complete before canonical close guards permit completion.
- Repository/native policy may always require stricter artifacts than the computed minimum.
