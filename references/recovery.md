# Recover without restarting intake

## Enter when

The current Work is blocked, suspended/completed, has changed scope/requirements/standards, stale evidence, missing native sources, or an abandoned verification.

## Execute

Use formal `cao verify` only on active Work with an accepted required baseline. Treat CLI overrides
as engineering diagnostics. A new verification supersedes an older run and invalidates its completion
qualification immediately; a late result cannot overwrite the current run. After an abandoned run,
rerun `cao verify`; manage the old trusted process before retrying commands with external effects.
For completed Work explicitly `cao reopen --work <id> --reason "<why>"`; for suspended Work use
`cao resume --work <id> --reactivate` and resolve blockers. Never restart intake to recover verification.

Restore deleted known Story entries; observations, including failed verify, only expand bound scope.
For an approved reduction, revise the requirement with rationale, explicitly reanchor and rebind the
revised native scope. Rerun verification after snapshot-format upgrades, execution-bit/link changes,
or repaired external/broken/cyclic source links. Follow any stale native review/acceptance guidance.
For oneshot Commit, explicitly stage and commit the intended SPEC; do not force-add or edit ignores
automatically. Git commit alone does not require reanchoring.

If `cao done` reports that the requirement changed, do not blindly rerun `cao verify`. Review the
requirement change; when the change is intentional, re-anchor the Work Item with
`cao start --requirement <path> --replace`. The `--replace` action changes the active
execution anchor; it does not mean that the original requirement was superseded. Old requirement-scoped
assessment, gate, verification and workflow evidence must be refreshed. Changed acceptance scripts/capabilities
or `timeout_seconds` require `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`.
Legacy completed history remains readable, not automatically recertified; legacy active work needs explicit
re-anchoring. First integrity upgrade backs up exact bytes in `.orchestrator/backups/integrity-v2/work/<id>.json`.

Use `cao block --reason "<why>" --suspend` for an explicit suspension, record a checkpoint and handoff, and resolve the blocker before reactivation. Restore missing known task entries. Only an actual confirmed requirement reduction can justify revised scope: record rationale, reanchor and rebind it; observations and failed verification preserve known work.

If a native source cannot be observed, inspect `native_sync`, restore the source or associate the actual current artifact. Do not guess a second SPEC, edit derived state or use a checkpoint to invent native completion. Requirement, intent, route/rule, code and report changes invalidate the applicable evidence; status/checkbox-only progress need not change frozen intent approval.

Legacy completed history remains readable but is not automatically recertified. Explicitly reanchor active legacy work, refresh assessment/gates/workflow/reviews as required, and verify. Integrity upgrades preserve backups under `.orchestrator/backups/`.

Inspect sessions with `cao sessions list/show`, handoffs with `cao handoff list/show`, and native references with `cao artifact add/list/refresh/remove/confirm`. Confirmations bind current file hashes and do not replace verification. `cao dashboard --open` can inspect these records and preview bounded native artifacts; only loopback dashboards enable artifact confirmation.

### Native host execution recovery

If the Pi bridge itself is unavailable, follow [Pi gate failure and recovery](pi-gate-recovery.md).
Enabled projects block dangerous operations without a trusted decision; the offline recovery
exceptions are deliberately narrower than the healthy core policy.

For unavailable historical BMAD workflow/implementation observation, use only the
existing audited `--allow-unverified --reason` exception where the CLI supports
it; do not invent historical execution or use a renderer as workflow execution.
Preserve Work progress while recording current reviewed evidence.

Pi allows bounded CAO bootstrap/list/help, descriptor redirection and limited Git
inspection before Work exists; associated product or managed-state writes remain
gated. Native BMAD preparation helpers are allowed only by supported identity,
arguments and output paths (configuration, memlog, architecture lint, sprint
planning under `_bmad-output`). A failed helper does not mean all native scripts
are forbidden; inspect its actual diagnostic. These gates are not a shell sandbox.
Pi child sessions remain children even if the parent session file is unreadable;
use the primary session for user approval and primary handoffs.

## Exit when

The original Work is active with corrected scope and fresh applicable evidence; rerun bootstrap to select the next action.

## If blocked

If genuine user input/authorization or a native independent reviewer is required and unavailable, record the exact blocker and wait. Do not replace the Work or create false approval/review evidence.
