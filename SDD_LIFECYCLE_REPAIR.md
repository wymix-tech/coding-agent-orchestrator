# SDD Lifecycle Reconciliation Repair

## Behavior changed

- Reconcile a Work Item's native SDD state before `cao bootstrap` creates a resumed/new-session context.
- Continue reconciliation at `cao status`, `cao list`, `cao resume`, `cao next`, and Dashboard state refresh boundaries.
- Bind `sdd.native_ref` only when a unique work-item-matched entry artifact is identified. Exact requirement identity takes priority over partial slug matches. Ambiguous BMAD candidates are not used as state evidence.
- Treat artifact discovery and lifecycle state as separate evidence. A generated Markdown file can be registered as a native reference without advancing CAO execution.
- Map explicit BMAD frontmatter status and OpenSpec/Spec Kit task-checkbox progress into CAO's canonical status view. Native implementation completion moves CAO to review, not to closed/done.
- Persist normalized state snapshots and idempotent lifecycle events in `.orchestrator/lifecycle/events.jsonl`.

## Core regression scenarios

- BMAD `ready_for_dev` -> `in_process` updates CAO from a planning cursor to `implementation`.
- A unique BMAD artifact binds `native_ref`; an artifact without a machine-readable status does not start implementation.
- Multiple partially matching BMAD artifacts do not bind or advance the Work Item.
- `cao bootstrap` reconciles state before building the session context.
- OpenSpec and GitHub Spec Kit task-checklist progress drives their shared CAO phase projection.
- Repeated reads do not duplicate the same lifecycle transition event.
- Dashboard artifact catalog/preview/confirmation regression suite remains green.

## Verification completed in this repair environment

Passed:

- `python -m py_compile scripts/cao.py scripts/dashboard.py tests/test_core.py`
- JSON parse validation for `protocol/work-item.schema.json`
- BMAD native state transition and native-ref tests
- BMAD no-status test
- BMAD ambiguous-artifact safety test
- Bootstrap-before-session-context reconciliation test
- OpenSpec status reconciliation test
- GitHub Spec Kit status reconciliation test
- lifecycle event test
- Dashboard test module: 19 tests passed

The complete repository test suite is **not claimed as passed**. Some long/combined test invocations timed out in this execution environment; tests relevant to this repair were run individually where needed.

## Scope and limitations

- Reconciliation is query/command-triggered, not a permanent background filesystem watcher. Changes are observed on bootstrap/status/list/resume/next or Dashboard refresh.
- The durable lifecycle JSONL is an event journal and audit/integration foundation. This repair does not yet include an arbitrary user-configurable event-handler executor with retries, nor does it claim every SDD framework has a native lifecycle Hook.
- BMAD state detection uses explicit machine-readable state on the uniquely matched artifact. If the installed BMAD workflow stores authoritative status elsewhere, that provider-specific state source must be added based on the actual installed version instead of guessing from file existence.
- OpenSpec and Spec Kit phase projections use task checklist state; this is a useful unified projection, not an assertion that the native tools expose a shared state machine.
