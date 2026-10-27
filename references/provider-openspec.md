# OPENSPEC native execution

## Enter when

The current Work selects `openspec`. Read only this Provider guide and the installed native instructions required for the current action.

## Execute

Before choosing a product stack or writing technology-dependent plans, apply [technology clarification](work-intake.md#technology-clarification-before-planning-or-scaffolding). An Agent-written proposal/spec is not user authorization; preserve established product choices or ask and wait for missing material choices unless explicitly delegated.

use installed `/opsx:propose` and `/opsx:apply` Agent commands; preserve active schema prerequisites (default apply requires tasks and upstream artifacts). `/opsx:ff` generates those artifacts and requires the expanded commands installed. Reduce process only through an explicitly configured/validated custom schema, not by deleting prerequisites. CLI setup/status is not agent implementation.

Associate the current change and finish ALL native tasks before formal verification, even on light. Default task files beside associated changes are captured automatically; add split changes using repeated `cao sdd plan bind --plan <native-task-file>`. For custom schemas use tracked outputs in native apply instructions, optionally a quoted tracked-file glob for new shards. Do not use the global change list as requirement completion evidence. Preserve plan/task associations during refresh; native done/archive cannot close Work without CAO verification and done. Follow any native archive prerequisites from the installed instructions.

### Archive ordering

CAO recognizes a missing associated change only when exactly one matching native
archive directory exists and all currently bound artifact hashes match. It records
the old/new roots in `sdd.archive_relocations` and emits a lifecycle event. Known
task IDs, minimum counts and split-change associations are preserved. Symlinks,
changed artifacts, ambiguous archives and remaining tasks cannot certify closure.

For active Work, path relocation changes frozen inputs: review the migrated
sources, explicitly `cao reopen --reanchor --reason "Reviewed archived sources"`,
refresh the affected native completion bindings and project verification, then close. Contract-1 acceptance follows its legacy protocol. Reconcile never invents passed runs.
Completed Work retains its immutable acceptance result with `historical: true`;
that result describes completion time, not current repository freshness. Native
artifact references can still follow the recorded archive location. Reopening is
an explicit new validation action and must check current inputs again.

## Exit when

All associated native tasks, testing, reviews, acceptance and required native Commit/close duties are complete; follow completion guidance for CAO evidence review and baseline verification. Standard/comprehensive retain workflow registration; light still honors native prerequisites. Complete native acceptance before CAO review, bind original results through the [native lifecycle](native-lifecycle.md), then run the project baseline.

## If blocked

Inspect `native_sync` diagnostics and associate the actual source. Source sync does not refresh acceptance hashes: review changed SPEC/criteria, explicitly rebind if needed, and reverify. Never invent entrypoints, reports or historical execution.
