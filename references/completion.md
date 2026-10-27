# Review evidence, verify the project and close

## Enter when

The selected SDD has finished the Work's entire associated scope, including
native testing, code review, findings resolution, acceptance and required native
Finalize/Commit/archive duties. SDD in-review remains CAO implementation.

## Execute

New Work follows the [native lifecycle](native-lifecycle.md). Bind original native
completion/test/acceptance reports with `cao lifecycle bind --report <native-report>`
(repeat for necessary outputs). Preserve native formats and independent review
provenance. Declare native risk-check coverage with `--gate <required-duty>`.

Run `cao conformance check` for process/artifact evidence, then `cao verify` for
the approved project baseline. No second CAO functional review or functional
acceptance executor is required. The baseline may run all project tests, including
this Work's new tests. Missing tools, zero tests, failures and timeouts keep their
actual failed/blocked/error result.

Inspect `cao acceptance status` and `cao gate show`; only satisfy manual gates
for observed events. `cao done` reevaluates scope, current evidence and baseline
freshness, and is the only final Work closure operation.

Use `cao verification smoke --server-command '<command with {port}>' --path <endpoint>`
when the project baseline requires HTTP smoke. Native behavior tests use actual
fresh services, isolated fixtures and redacted results. Never mask a failed run.

## Recovery

Follow the reported blocker and changed inputs. Binding repair preserves valid
native code review. Product/requirement changes require affected native steps.
Baseline standard changes retain explicit review/reanchor rules. Git commit
alone does not invalidate content-based evidence. Never restart intake or replace
Work to repair evidence.

Runs remain snapshot/run/generation bound; late results cannot replace a current
run. Knowledge collection is optional and cannot reopen or block completed Work.
See [conformance and knowledge](conformance-knowledge.md).

## Legacy contract 1

Unmigrated older Work retains its recorded acceptance protocol: configure sources
and coverage before reviewer preparation, run `cao verify`, then the declared
command/Skill acceptance and `cao done`. Read [acceptance protocol](sdd-acceptance-protocol.md)
and its mode guide. This legacy order does not apply to contract 2. Active old
Work blocked by the extra executor can use `cao lifecycle migrate --work <id>`,
then bind actual native completion evidence and follow the current lifecycle.

## Exit when

`cao done` succeeds with current native completion/conformance, project baseline
and all required duties. Native done alone cannot close Work.

## If blocked

Follow the specific blocker and the [native lifecycle](native-lifecycle.md) recovery guidance. Preserve the current Work and only refresh affected evidence.
