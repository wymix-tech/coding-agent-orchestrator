# Native completion and CAO evidence lifecycle

Contents: Execute; Recovery and migration; Recognized reports and repair
boundaries; Guarded migration and recovery.

New Work uses `lifecycle_contract: 2`. SDD owns planning, implementation, tests,
code review, findings resolution, native acceptance and required native close
steps. These activities remain CAO implementation. CAO reviews process/artifact
evidence, runs the project baseline and closes Work.

Native compilation/test failures do not start CAO review or verification and do
not revoke permission to repair the active Work. Keep the current native step,
edit the implementation, rerun its checks, and finish native review/acceptance/
close duties before binding completion evidence. Failed checks block completion,
not the repair loop. Host observations record failures without certifying results.
Project baseline changes still require their existing review/reanchor; ordinary
product edits and generated build output do not change the verification standard.

## Execute

1. Finish every associated native unit and required approval/review/finalize/
   commit/archive duty at the installed provider's actual step. SPEC done alone
   cannot substitute for unfinished stories or split changes/features.
2. Preserve original native test/acceptance completion reports. Standard and
   comprehensive still record actual workflow execution, artifacts and plans
   through existing `sdd workflow` commands. Light retains its real bound SPEC
   without manufacturing a formal native stage.
3. Bind original results: `cao lifecycle bind --work <id> --report <native-report>`.
   Repeat `--report` for all necessary native outputs. Reports retain their native
   Markdown/JSON/YAML/text format in provider-owned directories. Binding records
   scope, requirement, product snapshot, hashes and existing execution/review
   provenance. It is an evidence declaration, not independent semantic proof;
   never present failed/unfinished execution as a completed native result.
4. Work-specific risk checks remain SDD duties. Declare report coverage with
   repeated `--gate`, for example `--gate security_verification`. Do not add a
   project wrapper solely to give the same test suite another capability label.
5. `cao conformance check` validates declared process and artifact evidence.
   It does not rereview functional code or depend on a future CAO verify run.
6. Run `cao verify` using the approved project baseline, then `cao done`.
   SDD build/test commands remain executable during implementation. They do not
   move CAO into verification. Ordinary Maven logs are baseline output, not JSON
   acceptance reports.

## Recovery and migration

Repair missing references by binding actual reports. Product, requirement or
scope changes require the affected native steps and new evidence. Baseline
wrapper/configuration changes require baseline review and verification; they do
not automatically restart all native reviewers. Exact verification inputs must
first be registered using `cao verification prepare --scope <scope> --path
.orchestrator/scripts/verification/<scope>/<file>`. Other managed state remains
command-owned.

Old contract-1 Work retains its historical executor protocol. Use
`cao lifecycle migrate --work <id>` to continue it under contract 2. Migration
preserves identity, old failures, bindings and history, records prior execution
and acceptance, and is idempotent. It cannot invent completion evidence. Finish
or recover running verification/acceptance first. Completed Work cannot be
migrated; explicitly reopen before current validation. Migration does not approve
a changed baseline, new requirement or provider choice.

`cao acceptance status` shows native completion, conformance and baseline results.
Contract 2 does not use acceptance configure/run/begin/submit/reuse or a second
per-criterion functional executor. Those remain contract-1 tools. Historical
completion results remain frozen; current changes cannot rewrite them.

Do not reuse an original result belonging to another Work. Existing result claims
are checked transactionally. JSON reports must be valid structured data; explicit
failed/pending/running top-level outcomes cannot certify native completion. CAO
still does not infer semantic correctness from arbitrary report prose.

## Recognized reports and repair boundaries

BMAD, OpenSpec and Spec Kit share adapters for explicit Markdown/flat YAML
result fields, structured JSON results, and unittest/Maven/pytest/Node runner
summaries. Explicit failure, zero executed checks in a declared count, conflicting
identities and wrong Work/provider/requirement/product snapshot block binding.
Fenced examples and unknown prose cannot certify success. CAO records the adapter
and parsed observation beside the original hash; it never rewrites the report.
Changing adapter interpretation requires rebind rather than silently upgrading
old evidence. Reports are declarations linked to existing native provenance,
not independent proof that their contents are truthful.

Two failed bindings of unchanged reports and inputs pause further identical
attempts with `NATIVE_COMPLETION_REPAIR_PAUSED`. Correct the original result or
source issue, then bind again. Repeated attempts leave state unchanged; they do
not trigger a new intake or functional review.

Evidence diagnostics include `changed_domains`, `changed_paths`,
`affected_evidence` and `still_valid_evidence`. Product, native source/scope,
original reports, shared native dependencies, policy and baseline inputs are
tracked separately. A report-only edit preserves an unchanged project baseline.
Declare managed shared inputs before running the native step:

```bash
cao lifecycle dependencies --work <id> --path .orchestrator/scripts/verification/test/helper.sh
cao lifecycle dependencies --work <id> --review --path .orchestrator/scripts/verification/test/review-policy.md
```

Literal sourced shell helpers are followed; dynamic imports need explicit input
declarations. Use `--review` only for inputs actually consumed by native reviewers.
Baseline transport changes affect native evidence when it consumed that input.

## Guarded migration and recovery

```bash
cao lifecycle preflight --work <id>
cao lifecycle migrate --work <id> --check
cao lifecycle migrate --work <id>
cao lifecycle rollback --work <id> --check
cao lifecycle rollback --work <id>
```

Checks do not reconcile or mutate state. Migration journals a full prior Work
before writing; interrupted migration/rollback resumes the same checkpoint.
Rollback requires unchanged Work progress and source fingerprints, advances
generation and retains audit history. It refuses to overwrite later work.

New contract-2 Work and migrations use a durable last-authorized-state guard.
Enroll older contract-2 Work with `cao lifecycle protect --work <id>`. Unsupported
writes, contract downgrades and corrupt protected Work are rejected on read.
`cao lifecycle recover --work <id> --check` inspects recovery; `cao lifecycle
recover --work <id>` quarantines the exact unexpected bytes and restores the last
authorized state with a new generation and audit event. Do not hand-edit guards.
This is an authority check in the current engine, not an operating-system write
sandbox: an old binary can alter files physically, but its projection is not
silently accepted by the guarded engine. Stop old writers before migration.

## Failed CAO baseline and return to native repair

`cao next` preserves failed/blocked/error diagnostics rather than suggesting a
blind rerun. Inspect `verification.results` and `cao runtime show`, then record
an evidence-based diagnosis on the same active Work:

```bash
cao lifecycle repair --kind product --reason "Observed regression and reproduction"
# Alternatives: environment, baseline, unknown
```

Product diagnosis returns CAO to implementation and blocks completion until the
affected native tasks, reviews and acceptance are completed. Reopen the actual
provider task through its native workflow. Environment diagnosis retains
verification and existing valid native results; restore services and retry.
Baseline changes retain their normal review/reanchor requirement. Unknown causes
require diagnosis; no automatic replay or relaxed checks are introduced.

After material product/native input changes, merely binding or copying an old
report cannot refresh its execution. Capture the affected **native command**
using the existing runtime command transport, then bind its original output:

```bash
cao lifecycle execute --command "<actual native test/acceptance command>" --report openspec/native-test.log
cao lifecycle bind --report openspec/native-test.log
cao conformance check
cao verify
cao done
```

Use the installed provider's output directory instead of `openspec/` for BMAD or
Spec Kit. The command is chosen from the native workflow, not an additional CAO
functional suite. It must emit a recognized native result on stdout/stderr;
CAO retains those bytes and binds actual execution inputs, generation and report
hash. Execute each required command/report separately, then bind all reports.
Identical output from a fresh execution is valid. Attachment alone, changing a
path, or declaring a new snapshot is not fresh execution. Unchanged-input
registration repairs can still reuse existing valid declarations.

A failed or interrupted native command cannot certify completion. Inspect its
runtime record. An unresolved command is never automatically replayed; after
inspection, explicitly use `--retry-of <native-run-id>` when the old owner is no
longer running. Historical completed Work remains immutable.

These receipts establish observed command execution and inputs, not semantic
truthfulness or independent review identity. External/manual outputs without a
current execution association remain insufficient to refresh materially stale
completion evidence; do not bypass this by attaching them again.

## Result roles and execution provenance

Document `status: done` is workflow metadata, not verification/acceptance evidence.
Native result parsing and gate coverage are separate. Ordinary test output covers
verification; security/integration/data/compatibility and task acceptance require
identified native checks explicitly covering those duties. A CLI gate flag cannot
create coverage. Native checks remain provider-owned; CAO does not add a second
functional executor or semantically certify the assertions.

Prefer reusing an existing current execution receipt. `cao lifecycle executions`
shows bounded receipts, report paths and freshness. Pi bash commands matching an
accepted project check or a declared native command are collected automatically
when the adapter receives correlated start/result events with complete structured
output, exit code and unchanged inputs. Truncated/partial results and generic
runtime diagnostic observations do not certify execution. Other adapters can keep
native scope-identified reports or use `cao lifecycle execute` when necessary.

`cao lifecycle declare --source <bound-spec-or-plan> --command <literal-command>
--reason <native-validation-purpose>` admits a command explicitly written in the
bound source. Declaration never claims execution. A changed source invalidates it.
Actual native execution is captured with `cao lifecycle execute --command
<native-command> --report <provider-owned-log>`; it does not need to equal the
coordinator's `invoke skill ...` workflow invocation.

External native check reports identify `work_id`, `requirement_sha256` and
`code_snapshot`, plus `evidence_type: acceptance_result|security_result|manual_result`
and `checks`. Each check contains `id`, `description`, `status`, `evidence` and
explicit `gates`. Use actual native assertions/results, not an invented summary.
Manual native checks retain declared authority; execution receipts retain observed
authority. Changing report selection preserves old report ownership so historical
logs never suddenly become product source inputs.

## Protocol upgrades and bounded navigation

Existing completed Work remains historical. For active old review tickets, inspect
`cao sdd review migrate --check`, then apply with `--apply` when the original frozen
manifest and finding identities are intact. This upgrades future resolution
requirements, preserves tickets/judgments and records a checkpoint. It never
certifies past context isolation. `--rollback <migration-id>` is allowed only while
the review state still exactly matches the upgrade; later progress cannot be lost.

Pi shell tools inherit agent navigation output. `--agent-json` requests that view
elsewhere; `--full-json` retains the complete machine schema. `--compact-json`
keeps its existing behavior. Errors, readiness, blocker actions and exit status are
preserved. Raw findings/drafts, host events and explicit history queries retain
full data. Use `review status --history --limit <count>` for prior rounds. The native
working diff can be refreshed after patches; keep the per-ticket reviewer diff
immutable and use the separate `native_diff_file` for subsequent working copies.
