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
cao amend --work <id> --requirement <candidate> --request "<original request>" --apply --expected-token <expected_token>
```

The original document bytes and Work history remain in the operation journal.
Changed requirements are never silently adopted by `start --replace`.
The preview token binds the candidate bytes and business state; host observation
revisions alone do not invalidate it. If the source was edited in place, explicit
amend can recover the retained anchored original; legacy Work without retained
bytes needs its exact original restored from an audited source. Provider selection remains explicit.

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
Normal resume diagnoses unresolved runs before session attachment. Contract 2 also blocks a new verification while its previous run remains unresolved; contract 1 keeps its historical executor semantics until explicit lifecycle migration. It never
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
Unknown/remote owners are not killed by PID. After inspecting an abandoned contract-2 run, use `runtime abandon --run <id> --evidence <inspection-file> --reason <basis>` to preserve outcome_unknown, fence late results and permit a new attempt; this is an inspection declaration, not proof of termination.

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
overwrite. Prepared operations whose inputs remain unchanged can be inspected/aborted with `cao switch abort --operation <id> --check` and then without `--check`; applying operations must be recovered. Do not delete the journal or state lock. Late results stay attached to
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
`show` returns small archived bodies as base64, including after source overwrite. Large bodies are omitted from console output; use `--output <new-provider-file>` to export the original format without overwriting an existing file.
Old declarations without retained bytes report historical content unavailable.
Executable source files are not excluded from product verification inputs.

`--session/--host` can bind a real producer (including a no-shell reviewer whose
coordinator attaches its output); `--run` requires an existing generation-matched
runtime receipt. Without those sources the attachment remains an agent declaration.
Use `--tool-call` with the real producer session to correlate an observed native tool result, or `--attempt` for an explicitly declared native attempt ID. Late reports for suspended Work may be attached with `--historical`; these entries cannot grant current completion. The attachment-time input fingerprint is never called an execution-time snapshot.
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

## Project architecture Skills

Before planning, scaffolding or business implementation, inspect `cao project
architecture show --scope <scope>`. Read every returned Skill path, including
inherited root rules. Use accepted package identity and scope paths. For missing
rules follow [architecture selection and adoption](project-architecture.md).
Do not infer DDD merely from Spring Boot. Plans must place files by responsibility;
provider review must cite applicable ARCH rule IDs and assess semantic boundaries.
Resume, switching and child agents preserve the Work architecture binding. Changed
rules require reviewed `project bind --refresh`; never reset Work progress.

## Switch diagnostics and invocation identity

Trusted CAO control invocations use lifecycle records rather than ordinary
pending product effects. Absolute installed launchers follow the same recognition
rules as the tool gate. Compound commands containing product operations still
retain their effects; a basename alone does not establish trust.

A switch invoked through host hooks retains its observed coordinator identity
for the transaction. Without that context use `--host <host> --session <native-id>`
or an existing host session environment identity. Multiple invoking candidates
require an explicit choice. Idle sessions alone never establish the caller.
Source suspension, target activation and session binding share the recoverable
operation. Do not end the coordinator session before switching. Old results keep
their original start-time Work binding.

Inspect exact pending calls with:

```sh
cao runtime effects --work <source-id> --pending --limit 20
cao switch --to <target-id> --check
```

`runtime status` also includes pending-effect counts. An ended session does not
prove a call stopped. A known live execution must finish or be cancelled using
its actual executor/plugin capability. An unknown legacy call requires explicit
inspection; no arbitrary test log can settle it. `settle-effect --evidence` now
requires a JSON inspection record matching `host`, `session_id`, `tool_call_id`,
`resolution` and a nonempty `observations` string describing the exact checks.
The record is an agent declaration, not a process-termination certificate or
passing execution receipt. Already finished/settled calls return
`already_resolved` without changing state.

Switch/handoff previews and handoff creation show bounded summaries by default.
Use `--full-json` to inspect complete snapshots; full durable handoffs and operation
journals retain original manifests. Installation files and unrelated historical
files may remain in conservative freshness manifests; hiding them in the summary
does not remove them from evidence or silently relax invalidation rules.
