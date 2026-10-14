# R1–R9 integrity repair

Baseline: `fix/cao-integrity-repair`, `47ac94337793b3a97c0921bbf507237a0559b451`.
This repair preserves native provider ownership, profile layering, Work identity and history.

| Issue | Implementation | Regression evidence |
|---|---|---|
| R1 Optional-only completion | Shared required-result policy, setup presence, accepted baseline reused by formal verify/completion | `VerifyIntegrity.test_r1_empty_and_optional_baselines_never_grant_done`; existing required/optional/test-capability bindings |
| R2 Dynamic Story deletion | Pure BMAD effective scope; locked scope observation before verification early returns; native reconcile consumes the same scope | `ScopeIntegrity` failed-verify, all observation boundaries, concurrent status/idempotence; existing multi-epic/sprint/task-provider tests |
| R3 Static Shell side effects | `tool_effect` unifies mutation and targets; find delete/exec/output, sed write/exec, Git output, redirects and compounds | `ToolIntegrity.test_r3_static_mutation_is_not_readonly`; existing host gate/readonly/patch tests |
| R4 Host workdir | Per-tool workdir → host cwd → known invocation cwd; internal cd and alias resolution; shared normalized targets | Both R4 ToolIntegrity tests; installed-package negative events and Pi payload cwd |
| R5 Snapshot omissions | Explicit format 3; bytes/type/execute bits/link identity; bounded directory-link traversal and external/cycle/broken rejection | SnapshotIntegrity; VerifyIntegrity chmod/legacy reverify; updated snapshot integrity suite |
| R6 Ignored SPEC | Separate tracked/HEAD/index/cleaned-content checks; Git normalization; retain existing business Commit scope | Both CommitIntegrity tests (中文/space path, CRLF, subrepo, worktree); existing oneshot verify→commit→done |
| R7 Late verification | Locked run registration, UUID/sequence/input/generation, new-run supersession, history | Real processes in VerifyIntegrity (both success/failure orderings, running done, decision, crash, reanchor/suspend); existing timeout/input changes |
| R8 Completed mutation | Active-only start and generation/identity checks at finish | Completed/suspended unchanged, old-generation rejection, legal recovery in VerifyIntegrity |
| R9 Python fixture PATH | Resolve/validate Python before PATH mutation; quoted launcher env; cleanup and bridge-first diagnostic | Three Pi fallback/interpreter tests including spaces and unavailable interpreter |

All new issue regressions live in `tests/test_repair_r1_r9.py`. They use local acceptance/review fixtures;
they do not certify real business functionality or an online reviewer. Existing package smoke uses
installed CLI executions, local provider fixtures and simulated host events, not live hosts.

## State compatibility

Work JSON retains `integrity_version: 2`. Additive fields are `execution_generation`,
`verification_sequence`, and `verification_history`. Missing generation/sequence defaults to zero.
Status, accepted requirement or acceptance-anchor changes advance the execution generation;
explicit reopen/reanchor advances it even with identical content. Ordinary decisions/handoffs/heartbeats
retain the generation. Work revision conflicts remain guarded by the existing persistence mechanism.

A running verification records `run_id`, `sequence`, `generation`, `inputs`, `source`, `at`, and
`status: running`. Final format-2 verification records additionally carry snapshot format 3 and the
same run identity. The evidence-envelope version remains 2; snapshot format is separately explicit.
Old snapshot evidence cannot pass the new freshness policy. Completed history is not recertified.
Superseded results are retained as non-authoritative history for unfinished Work, without rewriting
progress; completed records are left immutable. A prior running record can remain in history even
when its former process never reports a final result.

A new verify always replaces the current run; no timestamp order or arbitrary state revision decides
which result wins. Supersession does not stop an old process and does not isolate external effects.
After interruption, inspect/manage the old trusted process as appropriate, then rerun the existing
verify command. Unknown results never become passed. No new recovery command is required.

Native plan parsing is pure. BMAD returns effective known scope even when an observed known ID is
missing. Lock-protected verification observation and native reconciliation persist it. All existing
status/bootstrap/resume/next/Dashboard/host-event boundaries already reconcile and use this scope.
A previously observed Story disappearing does not imply permission to reduce scope. An intentional
reduction requires an actual approved requirement edit with rationale, explicit existing reanchor,
then path reassessment and revised native binding; an identical reanchor alone cannot shrink scope.

## Commands and diagnostics

- Restore known missing Story/native task sources, finish the real native tasks, then `cao verify`.
- Configure required scripts using `cao verification configure --test-script "<real wrapper>" --replace`;
  review and accept changes using `cao reopen --work <id> --reanchor --reason "<reviewed change>"`.
- Completed: `cao reopen --work <id> --reason "<why>"`. Suspended:
  `cao resume --work <id> --reactivate`; remaining blockers require `cao unblock --work <id>` after resolution.
- Running/abandoned: rerun `cao verify`. Late results return `superseded` (exit 2).
- Legacy snapshot: rerun `cao verify`; rerun stale native review/independent acceptance as instructed.
- External/broken/cyclic links: restore repository-owned readable sources, remove the invalid alias or
  repair its target, then verify. No external-source registration exception is added in this repair.
- SPEC Commit: inspect Git tracking/diffs, explicitly stage and commit intended SPEC/business changes.
  Reasons distinguish ignored, untracked, absent HEAD and uncommitted content. Never force-add automatically.

Windows snapshots do not treat POSIX execute bits as portable facts. Traversal bounds are 100,000
entries and 100 directory levels. Lexically named dependency caches remain excluded; a source alias
into a cache is scanned. No mtime/size content cache is used. Ordinary commits/Git metadata stay excluded.

## Verification boundaries and deferred work

Run full unittest discovery, default Pi tests, TypeScript, compileall and diff checks; build and install
a wheel outside checkout and run installed_package_smoke. New core guards are covered through the
installed package too. The platform CI matrix now includes R1–R9 regressions; adding it is not evidence
that macOS/Windows have executed successfully in this session.

Deferred: rendered native Review empty-findings contracts; large cao.py decomposition; Dashboard
cross-request caching; event indexing. Do not accept/reject empty native results without version-specific
source evidence and do not reuse stale evidence for speed.

Unknown programs and expanded variable targets cannot acquire a no-Work or baseline-scaffold exemption merely by running under a native/test directory. Explicit installed native helpers and approved scaffold runners retain their existing dedicated policies; active accepted Work keeps the existing unknown-command policy.
