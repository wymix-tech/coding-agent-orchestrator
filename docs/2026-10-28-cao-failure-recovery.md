# CAO failure recovery repair — 2026-10-28

Base: main e913b6bd3a734c27c28095352c5569f4b5db4244.

## Result

Material native input changes cannot be refreshed by rebinding or copying an old
report. A recognized native command is executed through the existing runtime
transport, with input manifests captured before execution and checked again at
commit. The native report is actual command output; execution failure, input
changes, superseded execution or unresolved execution cannot grant completion.
Repeated native output may be byte-identical and still carry a fresh receipt.
Recorded commands restrict capture to the existing native invocation; copying
old reports with cat/echo is not accepted as native execution.

CAO does not introduce functional acceptance criteria or a second suite. It runs
the native command explicitly requested by the agent, stores its original output
and validates process evidence. Native task/review/finalize obligations remain
independent requirements. No semantic truthfulness or reviewer independence is
claimed by command receipts.

Product repair returns to implementation; next and persisted phase agree after
native evidence becomes stale. Baseline failures expose diagnosis and logs,
with explicit product/environment/baseline/unknown classification. A pending
product repair cannot be waived by reclassifying it as an environment failure.
Environment recovery preserves valid native evidence. Binding history, failed
runs and repair diagnoses are retained on the same Work.

See references/native-lifecycle.md for CLI recovery examples.

## Validation

- Original two regressions failed before repair and passed after repair.
- Final recovery module: 29 tests passed (including imported legacy lifecycle
  coverage); two newly added cases separately passed for byte-identical fresh
  output and product changes during execution.
- Product diagnosis/repair/execute/bind/verify/done CLI regression passed again
  after preventing diagnosis downgrade.
- Full Python module coverage was sharded into six processes. The first calls
  lacked discovery's tests/ import path: 923 cases included 23 module-import
  errors, 4 skips and 896 passing tests. All 23 affected modules were rerun with
  the correct PYTHONPATH: 229 + 66 tests passed. Thus effective full-module
  coverage was 1195 executed cases, 1191 passed and 4 skipped. The two subsequent
  new boundary cases also passed. No assertion failure remained.
- Python compileall and git diff --check passed.
- Pi TypeScript check passed; Pi adapter tests: 39/39 passed.
- Release manifest check passed: 194 files, including the new Python module.

## Limits

Provider/profile compatibility has fixture and shared-core regression coverage;
this is not a real-model online acceptance run of all three installed providers.
External/manual results without a current execution association cannot refresh
materially stale evidence. Unchanged-input registration repair remains supported.
The explicit capture command must match a previously recorded native command;
missing command provenance is diagnosed, not manufactured. Native commands are
trusted project code, as are existing configured verification commands.

Initial Git CLI push lacked credentials. Commits were therefore published using
the connected GitHub Git-data API, with leased branch updates. Local and remote
commit IDs differ due to commit metadata; their source tree hashes are checked
for equality. Main was not merged or modified.
