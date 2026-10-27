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
