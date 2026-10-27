# Native completion and CAO evidence lifecycle

New Work uses `lifecycle_contract: 2`. SDD owns planning, implementation, tests,
code review, findings resolution, native acceptance and required native close
steps. These activities remain CAO implementation. CAO reviews process/artifact
evidence, runs the project baseline and closes Work.

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
