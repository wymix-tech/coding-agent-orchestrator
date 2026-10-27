# Task continuity and evidence transfer

Contents: Requests; Checkpoints; Recovery; Switches; Evidence; Host coverage.

## Requests

“Continue” resumes the current Work. A question or status request is not a new
requirement. For a new request with unclear priority, offer three choices:
suspend/switch, queue as todo, or amend current scope when related. Honor an
explicit user choice immediately; do not ask again. Record the user's request
and the chosen action. Relatedness alone is not authorization to expand scope.

```bash
cao start --defer --requirement <filled-request-file>
cao list --status todo
cao switch --to <work-id> --check
cao switch --to <work-id> --reason "<chosen priority>"
```

Queueing does not run native workflows or occupy the active slot. Activation
checks current project preparation, provider/restart readiness and unresolved
blockers. For actual requirement changes prepare a candidate document, then:

```bash
cao amend --work <id> --requirement <candidate> --request "<original request>" --check
cao amend --work <id> --requirement <candidate> --request "<original request>" --apply --expected-revision <preview-revision>
```

The original document bytes and Work history remain in the operation journal.
Changed requirements are never silently adopted by `start --replace`.
For already externally edited sources use the existing explicit reopen/reanchor
protocol after reviewing the change. Provider selection remains explicit.

## Checkpoints

SDD remains the native task authority. CAO coordination items do not mark native
tasks done. Inspect source-qualified native unit IDs using `resume --check`.

```bash
cao checkpoint --summary "<done and next>" --unit <native-unit-id> --read <required-file>
cao checkpoint --item "<coordination item>"
cao checkpoint --resolve <item-id>
cao checkpoint --cancel <item-id>
cao handoff --reason "<transfer reason>"
cao handoff accept --id <handoff-id> --work <id> --host <host> --session <real-session-id>
```

Handoff v2 captures actual product/requirement observations, native unit/source
references, provider Skill hashes, evidence domains, unresolved runs and workspace
location. `capture_complete: false` and source diagnostics mean information is
missing. Reception records are observations, not approval or proof of understanding.
Old handoffs remain readable; their missing fields are never fabricated.

## Recovery

`cao resume --work <id> --check` and `cao handoff --work <id> --check` are strictly
read-only. They do not repair indexes, guards, outboxes or native files. A prepared
state guard must first be resolved by the existing explicit lifecycle recovery.
Normal resume diagnoses unresolved runs before session attachment. It never
invents a host session ID, replays an unknown side effect or turns a projection
into a passed result. Completed Work stays historical until explicit reopen.

```bash
cao runtime show --work <id> --run <run-id>
cao runtime finalize --work <id> --run <run-id>
cao runtime cancel --work <id> --run <run-id> --reason "<why stop>"
```

Finalize only commits a complete controlled verification receipt and its sealed
admission candidate after rechecking identity, command receipts, generation,
sequence and inputs. It never executes commands. Legacy acceptance/host-only
runs lacking this evidence remain diagnostic and require actual inspection/new
execution through their native protocol. Cancellation is cooperative: the live
executor cleans its own process group; a request is not proof of termination.
Unknown/remote owners are not killed by PID.

If an observed host tool has no matching completion, inspect actual artifacts
and processes. After establishing the result, record the inspection with
`runtime settle-effect --work <id> --host <host> --session <session> --call <call>
--evidence <inspection-file> --reason <basis> --resolution observed-complete`
(or `observed-stopped`). This is explicitly an agent declaration, not a process
receipt or gate approval. Never declare an active process stopped merely to switch.

## Switches

`start --replace` uses the same recoverable switch service. It validates targets
before suspending the source and writes a handoff. Known live/unknown source
runs and uncompleted host effects block switching. Resolve or wait first.

An unfinished journal blocks business writes and normal Work mutations. Recover
with `cao switch recover --operation <id>`. The engine validates journal intent,
all involved Work before/after states and captured product/config inputs before
finishing missing writes. Unknown concurrent changes cause a conflict, never an
overwrite. Do not delete the journal or state lock. Late results stay attached to
the original task; old sessions must explicitly reattach before new task writes.

The default is one active Work in a shared workspace. Handoffs record dirty files
and fingerprints; returning to A after B requires reviewing the input differences.
No automatic stash/reset, branch overwrite or code push is performed by CAO.
Managed worktree isolation remains the separately planned optional P6 enhancement.
Stop old unsupported CAO writers before adopting this protocol; guards are not an
operating-system filesystem sandbox.

## Evidence

```bash
cao evidence attach --kind test --path <original-native-report> --unit <native-unit-id>
cao evidence attach --kind fix --path <original-fix-report> --supersedes <old-evidence-id>
cao evidence list --limit 50 --cursor 0
cao evidence show --id <evidence-id>
cao lifecycle bind --evidence <id> --evidence <id>
```

Artifacts stay in their native format. CAO retains original report bytes in a
content-addressed archive and immutable metadata whose digest is bound by Work.
`show` returns archived bytes as base64, including after source overwrite.
Old declarations without retained bytes report historical content unavailable.
Executable source files are not excluded from product verification inputs.

`--session/--host` can bind a real producer (including a no-shell reviewer whose
coordinator attaches its output); `--run` requires an existing generation-matched
runtime receipt. Without those sources the attachment remains an agent declaration.
The attachment-time input fingerprint is never called an execution-time snapshot.
Final selection rechecks inputs and current original reports; later unresolved
failures cannot be hidden by choosing an old success. Process registration grants
no gates. Finish all native duties, bind actual completion evidence, check
conformance, run project baseline verification and then close Work.

## Host coverage

Pi supports its installed `session_before_compact` event; CAO saves a handoff
before compaction. Existing Session/turn/tool events remain supported for Pi,
Claude Code and Codex. Hosts lacking a compaction event use explicit checkpoint,
handoff and resume; no imaginary hook capability is advertised. Automated host
fixtures and installed CLI smoke are separate from real model/provider dialogue.
