# Verify, accept and close

## Enter when

All associated native execution tasks and applicable pre-verification review obligations are complete, or the current action is formal verification, final acceptance or done.

## Execute

1. Confirm actual native outputs/scope, review evidence and required Finalize steps. Schedule Commit at its actual native step; it may follow verification. Product changes may invalidate reviews; native SPEC/criteria changes require review/rebind. A Git commit alone does not require reanchoring.
2. For BMAD Build oneshot/dispatch, before native reviewer preparation, configure task acceptance and complete source coverage (including project-bound light Work). Standard/comprehensive preserve independent final acceptance requirements. Reuse the configured declaration at this stage: `cao sdd acceptance configure --declaration <repo-json>`. Review/reanchor changed sources with `cao reopen --work <id> --reanchor --reason "<reviewed changes>"`, honoring baseline approval when required. Light verifies actual native Intent/criteria through the same evidence protocol without manufacturing a formal native checklist; route-specific independent review remains applicable.
3. Run `cao verify` using configured required scripts. Overrides (`--script`) are engineering diagnostics and cannot qualify completion. All current requirement native tasks must already be complete.
4. Run `cao sdd acceptance run` (command mode), or `cao sdd acceptance begin`, execute the installed independent Skill, then `cao sdd acceptance submit --run <id> --report <registered-output.json>` (Skill mode). Use exact declared Work-scoped report outputs; unregistered outputs change the snapshot. Read the shared acceptance protocol for declaration/report shapes, then only the declared command or Skill mode reference linked directly from SKILL.md. Submitting evidence does not prove an independent reviewer ran; preserve native independence or its human-return fallback.
5. Inspect `cao acceptance status` for technical quality, task coverage/freshness and all lifecycle blockers, and `cao gate show`; only satisfy genuinely observed manual gates. Verification/tests/security and `sdd_acceptance` are automatic. Complete any remaining native Commit step, then run `cao done`; only successful authoritative Work `completed` means completion. Commit alone does not require reanchoring; changed input bytes still require fresh evidence. Never push without authorization.

### Real service and acceptance evidence

Use `cao verification smoke --server-command '<project server command with {port}>' --path <endpoint>` for HTTP smoke. Register it as a required script when startup/API behavior is an acceptance criterion. It owns a fresh dynamic-port service, checks liveness/status, and cleans up; never reuse an old server, mask startup failures with `|| true`, or substitute ad hoc curl for configured evidence.

Preserve each entire criterion, establish its preconditions, isolate mutable fixtures, and retain redacted actual run evidence. For deletion, check current valid credentials rather than an already-wrong old password, and test existing-token invalidation when required.

Verification is snapshot-bound. Code, requirements, direct wrappers, acceptance inputs, known task scope and reports may invalidate previous evidence. A newer run supersedes the older run immediately; late results cannot overwrite the current run. Do not fabricate evidence to close.

## Exit when

`cao done` succeeds with current verification, all applicable gates/acceptance satisfied, and no blockers.

## If blocked

Follow the specific blocker and recovery guidance; rerunning verification alone cannot approve a changed requirement or repair missing reviews/tasks.

Project-bound evidence has separate technical and task inputs. Requirement-only changes can preserve applicable original technical runs; source coverage and task evidence still need refresh. Use `cao acceptance criteria` to derive source IDs; optional criterion `covers` maps those IDs without copying the original documents. `cao acceptance reuse --script <name>` consumes actual JSON item results from the same fresh configured technical command when its exact command matches the accepted task executor; it does not invent assertions or rerun the entrypoint.

## Evidence reuse and optional knowledge

Use `cao conformance show` to inspect declared process/artifact obligations and their
current evidence. Native SDD review/approval evidence satisfies matching CAO gates;
remaining verification and acceptance still apply. Knowledge collection after close
is optional and cannot reopen or block a completed Work. See
[conformance and knowledge](conformance-knowledge.md) for recovery and source validity.

For BMAD dispatch, a native SPEC still `in-review` is not terminal completion.
Follow the current installed Finalize step and reach native `done` before CAO
closure. CAO does not silently edit the SPEC to manufacture completion. Refresh
only evidence affected by substantive changes. Under criterion-checks-v1, a
frontmatter status-only transition is lifecycle metadata; body/criteria changes
still invalidate task evidence. NO_VCS does not require an invented commit.
