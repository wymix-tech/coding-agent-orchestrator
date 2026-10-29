# BMAD native execution

## Enter when

The current Work selects `bmad`. Read only this Provider guide and the installed native instructions required for the current action.

CAO's default installer is pinned to stable BMAD 6.12.4. A detected `_bmad/`
directory is not a complete installation. `sdd list` reports detection, installed
runtime health and protocol; repair missing assets before planning. This guide
does not support the V7 preview protocol. Keep project configuration and the
current Work when repairing an installation.

## Execute

Before choosing a product stack or writing technology-dependent plans, apply [technology clarification](work-intake.md#technology-clarification-before-planning-or-scaffolding). BMAD routing, defaults and an Agent-written PRD/SPEC do not authorize technology choices; ask and wait for missing material choices unless the user explicitly delegated them.

For a native Build route, use installed `bmad-build`; it selects `oneshot` for suitable small work. If a different native planning workflow is already selected, follow its required steps until an actual native transition calls for a different workflow. Follow the exact renderer and reviewers, not an invented `bmad quick-fix`. A recognized oneshot SPEC intentionally has only metadata, Intent and Implementation Notes. Bind with `cao sdd spec bind --spec <native-spec.md>` without criteria selectors: CAO binds native Intent and the current requirement, preserves the file unchanged and does not claim an explicit acceptance checklist. Normal SPECs or explicitly selected criteria keep the existing structural standard checks. Rebind changed bytes and re-verify; light needs no manufactured workflow/plan or independent final reviewer; project-bound task acceptance evidence remains required.

### Route, approval and implementation

Read the selected native Skill completely; at the Build stage this is the installed `bmad-build` SKILL.md. Run its exact renderer only when required. Renderer success loads instructions; execute the real workflow and record `invoke skill <name>`, never a renderer plus `-> step-01..05` pseudo-command.

Follow the current action from `cao next`: accepted verification baseline, path assessment, native workflow selection (standard/comprehensive), native planning/SPEC preparation, then route declaration. A missing route blocks business implementation; native planning outputs remain writable. Read the selected installed Skill returned in `guidance.read_before_action`; do not search config directories instead of loading the Skill. A bound PRD is not a primary Build SPEC; wait for the real SPEC with native `route: dispatch` or `route: oneshot` metadata before preparing its declaration.

When Build produces its primary SPEC, immediately run `cao sdd artifact bind --artifact <actual-spec.md>`. Run `cao sdd route prepare`; new declarations belong to `.orchestrator/inputs/WORK_ID/route.json`, separate from `_bmad-output/` native artifacts. Fill the actual route fields and use `route rule` to extract literal native evidence, then `route check` before `route propose --declaration <returned-file-path>`. These commands prepare/validate input; they do not execute BMAD or approve a SPEC. Use the route contract for exact arguments and diagnostics.

Use the route declaration contract linked directly from SKILL.md for all fields and real rule hashes/quotes, required artifacts and every omitted document's reason/replacement. BMAD `route` is `direct-build` or `full-planning`; `build_route` is `dispatch` or `oneshot`, selected from installed native rules. Names such as `backend-api` and `full-stack-web-app` are not route values. Three fields (provider/route/reason) are not a complete declaration. Direct Build can retain standard gates. Full planning accepts either the existing `prd`, `architecture`, `epics`, `sprint` set or explicit native-rule-backed `planning_duties` mapping requirements/design/execution to actual bound artifacts. Do not manufacture document types whose duties existing artifacts already fulfill. Direct-build requires explicit omissions and replacement artifacts.

For dispatch approval/clarification/pause actions, read the BMAD approval reference selected by guidance. Approval does not substitute for native planning or implementation.

Follow the actual step-03 implementation handoff; set SPEC `in-progress` and `baseline_commit` (HEAD or NO_VCS) first. Read the full step and use the implementation child session where required and available; use the installed fallback when unavailable. Pi observes the step read and actual child product writes. Custom handoffs follow actual rendered rules. Full planning requires native Finalize, memlog/audits and required PRD/architecture reviews, not draft artifacts.
Before Pi dispatch, check `cao host status --host pi` for `child_hooks_installed`.
Run `cao host install --host pi` and restart when missing: foreground pi-subagents
children do not inherit ambient extensions. The installer merges the CAO child-only
extension default; agents with explicit child extension overrides need CAO listed
there too. Inspect actual agent names/tools first, preserve the native handoff,
and do not substitute a fabricated `task` tool or `general-purpose` agent.
Use the installed step-03 handoff verbatim: put design, acceptance and investigation
details in the SPEC, not additional dispatch instructions. Implementation children
continue their assigned step; the coordinator owns workflow registration. A failed
build/test stays in this implementation loop and must be repaired/retested before
the native review step. Do not rerender/restart Build merely because a child began.

### Native review and close obligations

These obligations follow the actual BMAD oneshot route for light, standard and comprehensive profiles. A route-bound primary SPEC is sufficient to identify them; standard/comprehensive must not bypass them by omitting a light `spec_binding`.

Before business implementation, configure verification and explicitly reanchor an existing Work if needed. The host blocks business writes until that baseline is accepted; native CAO/SDD artifacts, manifests and initial test/verification scaffolding remain writable. Tests do not have to pass before implementation. Do not install dependencies or expand scaffolding into implementation without the applicable authorization.

At a review action read only the route-specific reference returned by guidance: oneshot uses raw Markdown plus `review submit`; dispatch preserves raw Markdown/JSON, classifies ALL layers before patches, then resolves them. Preserve native independent execution, every active layer and current evidence. A manual implementation_review gate cannot replace these obligations. Follow native Finalize/rebind and verification ordering, and the native Commit step when Git is available; uncommitted Work product/SPEC changes block done. Never push unless authorized.

Bind ALL current requirement stories/epics using repeated `cao sdd plan bind --plan-item <sprint-status.yaml-or-yml>#<story-id-or-epic-N>`. A project sprint may include unrelated historical work; do not bind or complete it merely because the file exists. Without a separate plan, Direct Build still must finish its SPEC Tasks & Acceptance; `--no-plan` cannot waive these tasks. oneshot may legitimately have no task plan. Use actual native status/task progress as lifecycle source, regardless of title/filename spelling; native in-review remains CAO implementation; complete all associated native units before CAO evidence review.

For native stories mode, bind `cao sdd plan bind --plan <spec-folder>/stories.yaml`.
The inventory is also discovered beside a bound SPEC or its `stories/<id>-*.md`
record. Every listed ID must have exactly one native Build record with status
`done`; missing, duplicate, newly added or regressed records block closure. The
inventory itself never carries statuses. Finish each story and its native review
before proposing the next story's Build route: CAO retains the previous unit's
approval/review evidence. Do not replace the whole requirement with one story.
For sprint mode, stable Build sets the story to `review` while its SPEC becomes
`done`. Follow the installed `bmad-code-review` Skill to close the matching story
to `done`; do not rerun Build or hand-edit status to bypass that native step.

Planning and implementation output directories follow the merged stable BMAD
project configuration. Custom roots must remain inside the repository and cannot
alias protected state or source roots. Changed output configuration invalidates
the recorded route and requires reviewed rebinding. Disabling every review layer
is a native blocking condition in stable BMAD; enable a layer and render again.

## Exit when

All associated native tasks, testing, reviews, acceptance and required native Commit/close duties are complete; follow completion guidance for CAO evidence review and baseline verification. Standard/comprehensive retain workflow registration; light still honors native prerequisites. Complete native acceptance before CAO review, bind original results through the [native lifecycle](native-lifecycle.md), then run the project baseline.

## If blocked

Inspect `native_sync` diagnostics and associate the actual source. Source sync does not refresh acceptance hashes: review changed SPEC/criteria, explicitly rebind if needed, and reverify. Never invent entrypoints, reports or historical execution.

## Independent plan scope

An accepted Direct Build route resolves the absence of a separate plan early,
with the route and primary artifact recorded as its basis. Native inline tasks
remain subject to their own checks. Existing independent plans and shared story
inventories are never waived. Ambiguous task inventories require explicit current
requirement associations before implementation; inspect the returned candidates.

Use `cao sdd plan bind --no-plan` only when no associated independent plan exists
and the route permits it. Full planning cannot use this to remove sprint/story
obligations. Workflow completion preserves a valid existing scope and rejects an
unresolved scope immediately. Do not bind SPEC as tasks or invent a SPEC#item.
