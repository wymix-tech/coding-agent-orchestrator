# Verify, accept and close

## Enter when

All associated native execution tasks and applicable pre-verification review obligations are complete, or the current action is formal verification, final acceptance or done.

## Execute

1. Confirm actual native outputs/scope, review evidence and required Finalize steps. Schedule Commit at its actual native step; it may follow verification. Product changes may invalidate reviews; native SPEC/criteria changes require review/rebind. A Git commit alone does not require reanchoring.
2. For standard/comprehensive configure the independent native acceptance declaration before final verification: `cao sdd acceptance configure --declaration <repo-json>`. Review/reanchor changed sources with `cao reopen --work <id> --reanchor --reason "<reviewed changes>"`, honoring baseline approval when required. Light does not manufacture independent final acceptance.
3. Run `cao verify` using configured required scripts. Overrides (`--script`) are engineering diagnostics and cannot qualify completion. All current requirement native tasks must already be complete.
4. For standard/comprehensive run `cao sdd acceptance run` (command mode), or `cao sdd acceptance begin`, execute the installed independent Skill, then `cao sdd acceptance submit --run <id> --report <registered-output.json>` (Skill mode). Use exact declared Work-scoped report outputs; unregistered outputs change the snapshot. Read the acceptance protocol linked directly from SKILL.md for declaration/report shapes. Submitting evidence does not prove an independent reviewer ran; preserve native independence or its human-return fallback.
5. Inspect `cao gate show`; only satisfy genuinely observed manual gates. Verification/tests/security and `sdd_acceptance` are automatic. Complete any remaining native Commit step, then run `cao done`; only successful authoritative Work `completed` means completion. Commit alone does not require reanchoring; changed input bytes still require fresh evidence. Never push without authorization.

### Real service and acceptance evidence

Use `cao verification smoke --server-command '<project server command with {port}>' --path <endpoint>` for HTTP smoke. Register it as a required script when startup/API behavior is an acceptance criterion. It owns a fresh dynamic-port service, checks liveness/status, and cleans up; never reuse an old server, mask startup failures with `|| true`, or substitute ad hoc curl for configured evidence.

Preserve each entire criterion, establish its preconditions, isolate mutable fixtures, and retain redacted actual run evidence. For deletion, check current valid credentials rather than an already-wrong old password, and test existing-token invalidation when required.

Verification is snapshot-bound. Code, requirements, direct wrappers, acceptance inputs, known task scope and reports may invalidate previous evidence. A newer run supersedes the older run immediately; late results cannot overwrite the current run. Do not fabricate evidence to close.

## Exit when

`cao done` succeeds with current verification, all applicable gates/acceptance satisfied, and no blockers.

## If blocked

Follow the specific blocker and recovery guidance; rerunning verification alone cannot approve a changed requirement or repair missing reviews/tasks.
