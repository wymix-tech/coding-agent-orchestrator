# Complete lifecycle repair implementation

Baseline: main cd700566d348925c1ae6cc9779b72ad54834f702.
Scope: [accepted task](2026-10-25-complete-lifecycle-repair-prompt.md), A01–A32 and B01–B35.

Implementation order:
1. Reviewer task/session/output provenance and unified batch preparation (B01–B06, B15).
2. Criterion-specific acceptance receipts and current-error diagnostics (B17–B23, B29).
3. Scope-owned initialization plan/apply/check without a business Work (A01–A32).
4. Route evidence recovery, invalidation, control-command access and native closure (B07–B14, B24–B35).
5. Host/provider regressions, actual toolchain execution, packaging and accurate result matrix.

Do not equate unit/fixture execution with a real model-host E2E. Missing toolchains or host runtimes remain unverified, not passed.

## Recovery and publication status — 2026-10-26

This PR preserves the recoverable working-tree changes from an earlier workspace
snapshot. The previously reported local commit `fc1c5b5` is absent from the
restored Git object database and reflog; the remote repair branch did not exist.
This is a new commit and must not be presented as that previously tested version.

Current validation, repeated against this exact recoverable source:

- `python -m compileall -q scripts`: passed.
- `python -m unittest tests.test_project_setup_lifecycle tests.test_review_task_provenance tests.test_lifecycle_repair_boundaries -v`: 21 passed.
- `git diff --check`: passed.

The previous 996-case, installed-package and real-toolchain results cannot be
carried forward as evidence for this restored snapshot. A fresh complete
regression and release validation are required before marking this PR ready.

Known later changes missing from this snapshot include complete rendered native
contract resolution (`native_contracts.py`), narrowly versioned terminal metadata
hashing (`native_metadata.py`), scope-filtered snapshots and initialization
recovery refinements, generated-source output grants, and the optional real
compiler matrix (`test_setup_toolchains.py`). Later fixture fixes and the complete
A01–A32/B01–B35 result table also require restoration or reimplementation.

Publish this snapshot as a **draft PR**. The accepted complete lifecycle prompt
remains the target; this recovery publication does not claim complete acceptance.

## Completion work resumed — 2026-10-26

The PR source branch now restores complete rendered native contract binding,
versioned primary-SPEC terminal metadata, setup recovery and generated-output
boundaries. It also adds host-observed default reviewer models, structural source
categories, distinct shared-execution assertion outcomes, and dependency/empty-test
classification. The earlier recovery-only status above is historical.

Current requested exclusions: actual model-host E2E, framework-combination adapters
and multi-module workspace scenarios. These exclusions do not weaken shared gates
or exempt other provider regressions, actual language/toolchain checks or packaging.

Phase checkpoint: 49 existing core tests passed before the new shared-executor
extensions; 43 setup/native/snapshot/protocol boundary tests passed afterward.
Complete regression, packaging and actual toolchain validation remain in progress.

Second implementation checkpoint restores actual host model/provider observation
(including default-model selection without worker environment assumptions), explicit
review round/task counts, a complete product input manifest and a Work-start
baseline for NO_VCS projects. Git inventories include renames/deletions and
untracked resources. Input manifest tampering blocks affected reviews.

Exact planned-file repairs are now permitted without Work while initialization
is incomplete; ready projects and plan-external writes remain denied. Confirmed
identity fields reuse their original per-field user provenance. Adapter version,
runner compatibility and mandatory capability changes cannot silently reuse ready.
Runtime instructions, templates and quick starts now describe this same sequence.

Fresh execution results so far: 91 provenance/lifecycle regressions passed, 65
provider/recovery regressions passed, and all four actual compiler matrix cases
(Go, TypeScript, Java/Maven and Java/Gradle) passed. Pi typecheck and 37 adapter
tests passed. A fresh full regression and installed-package validation are running.
