# Action Authorization

Use `scripts/action_guard.py` as the single executable policy for permission to
execute, advance, and close. `collect_evidence(repo, state)` reads live authorities;
`evaluate(state, action, evidence=...)` is pure and deterministic. `authorize(...)`
combines them. Host payload normalization belongs in `tool_actions.py`, not in policy.

## Action contract

| Action | Conditions |
|---|---|
| `read` | Read-only inspection remains available without an active work item. |
| `prepare` | Requirement documents, SDD artifacts (`docs/`, `openspec/`), and packaged recovery commands remain available. |
| `mutate_code` | Classified, complete, current analysis; ready SDD; required acceptance criteria; no active blocker; implementation phase with ready/in-progress status; writable role. |
| `mutate_governance` | **Denied by default.** Authority config (`.orchestrator/config.yaml`), enforcement config, execution state, policy sources, and requirement identity are not agent-writable: they are what *grants* authority, so an agent must not be able to widen its own authority by editing them. Change them through the orchestrator CLI (classified as `prepare`) or an explicit operator action with `--allow-governance-mutation`. |
| `advance` | Shared readiness/freshness rules when entering implementation or later; completed tasks for review or later; resolved findings and any required current review for verification or later. |
| `close` | All shared checks plus acceptance satisfaction, all required current gates/review, and current passed final verification. Release applies the same final evidence requirements. |
| `finish_role` | Reviewer/verifier records a passed or failed outcome with evidence. This authorizes reporting the role outcome, not declaring the work done. |

Preserve native SDD ownership: native phase/status changes must be made by its adapter
and then projected. A native completion token alone does not establish governance
completion. `native_confirmed` is an adapter assertion, not permission to skip gates.

## Inspect a decision

Run the packaged front controller from the skill directory. An explicit Python
interpreter also works where the skill directory cannot execute launchers directly:

```bash
python3 ./coding-orchestrator --repo /path/to/project --json check --action mutate_code
python3 ./coding-orchestrator --repo /path/to/project --json check --action advance --phase review
python3 ./coding-orchestrator --repo /path/to/project --json check --action close
```

`check` is read-only: exit `0` allows, exit `1` denies. The returned object contains
`action`, `target_phase`, `allowed`, `decision`, `reason_codes`, detailed `reasons`,
`next_action`, `state_revision`, and `work_item_id`. Reasons have stable machine codes,
a message, and a recovery action. Multiple failures remain visible in deterministic
order. The actual mutation/transition must recheck; a prior check is only a diagnostic.

| Consumer | Shared action/result |
|---|---|
| Host `pre_tool` | Normalized `read`, `prepare`, `mutate_code`, or `mutate_governance`; result in `metadata.authorization`. |
| State transition | `advance` or `close`; denial includes the full `authorization`. |
| CLI `verify`, global host `stop`, completion status | `close`; these do not manufacture passing evidence. |
| CLI `check` | Requested action; same result as the corresponding enforcement boundary. |
| Start/resume | Shared next action, active blockers, and authorization projections. A resume route by itself grants no write permission. |
| Reviewer/verifier `subagent_stop` | `finish_role`; global completion still uses `close`. |

## Current evidence and edit windows

Bind analysis to a live repository fingerprint, authoritative requirement/decision/
impact/facts/plan/policy content hashes, and a Context Manifest with current sources.
The evidence binding includes actual content, so reusing an analysis ID cannot reuse
old passing gates or reviews after inputs change. Only unresolved blockers count as
active. Missing/partial impact and `NEEDS_EVIDENCE` remain unready.

Exclude generated `.orchestrator/intake`, runtime, session, context, evidence, archive,
execution state/history, and bootstrap reports from the material code fingerprint.
Hash referenced authority inputs separately. Read state directly; do not use its own
manifest hash to create a self-invalidating state update loop. Refresh role Context
Packs separately before relying on them.

After a host records a code edit, allow the next implementation edit only if live code
still matches the last recorded mutation and authority inputs remain current. Any
additional external edit requires reconciliation. Dirty analysis never authorizes
phase advance or closure. A policy violation may be repaired in implementation;
unresolved policy conflicts deny execution, and violations still block advancement.

Required gates are derived from both state and current plans. Individual gate/review/
verification results bind `snapshot_id` and `evidence_snapshot_id`; a required gate
cannot be downgraded by recording a result. Stop retries never convert a denial into
permission, and state-only helpers cannot prove freshness without the repository.

## Existing-project migration and limits

Keep schema version `1` and legacy command interfaces. Old analysis or result records
without content bindings fail closed: rerun intake for the same active requirement,
resolve missing evidence to `CLASSIFIED`, refresh context, rerun affected required
gates/review/final verification, then inspect `check`. Do not copy freshness flags or
old pass labels forward. Hook eligibility override keys are obsolete; see
[Host Enforcement](host-enforcement.md) for supported settings.

Execution State now uses cross-process optimistic commits with a recoverable state/history transaction journal. This policy still does not implement general command sandboxing, independent native-provider truth validation, or CI test execution. Result commands record provided evidence references; they do not independently establish that tests ran. Keep host integration and repository CI/merge protection as separate boundaries.

Long-running intake/CBM/test work occurs outside the state commit lock. It captures a starting state revision and may attach canonical results only if that revision is still current; otherwise the run remains diagnostic and returns `STATE_CONFLICT`.
