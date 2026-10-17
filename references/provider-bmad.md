# BMAD native execution

## Enter when

The current Work selects `bmad`. Read only this Provider guide and the installed native instructions required for the current action.

## Execute

Before choosing a product stack or writing technology-dependent plans, apply [technology clarification](work-intake.md#technology-clarification-before-planning-or-scaffolding). BMAD routing, defaults and an Agent-written PRD/SPEC do not authorize technology choices; ask and wait for missing material choices unless the user explicitly delegated them.

For a native Build route, use installed `bmad-build`; it selects `oneshot` for suitable small work. If a different native planning workflow is already selected, follow its required steps until an actual native transition calls for a different workflow. Follow the exact renderer and reviewers, not an invented `bmad quick-fix`. A recognized oneshot SPEC intentionally has only metadata, Intent and Implementation Notes. Bind with `cao sdd spec bind --spec <native-spec.md>` without criteria selectors: CAO binds native Intent and the current requirement, preserves the file unchanged and does not claim an explicit acceptance checklist. Normal SPECs or explicitly selected criteria keep the existing structural standard checks. Rebind changed bytes and re-verify; no separate workflow/plan/final-acceptance record is required for light.

### Route, approval and implementation

Read the selected native Skill completely; at the Build stage this is the installed `bmad-build` SKILL.md. Run its exact renderer only when required. Renderer success loads instructions; execute the real workflow and record `invoke skill <name>`, never a renderer plus `-> step-01..05` pseudo-command.

Follow the current action from `cao next`: accepted verification baseline, path assessment, native workflow selection (standard/comprehensive), native planning/SPEC preparation, then route declaration. A missing route blocks business implementation; native planning outputs remain writable. Read the selected installed Skill returned in `guidance.read_before_action`; do not search config directories instead of loading the Skill. A bound PRD is not a primary Build SPEC; wait for the real SPEC with native `route: dispatch` or `route: oneshot` metadata before preparing its declaration.

When Build produces its primary SPEC, immediately run `cao sdd artifact bind --artifact <actual-spec.md>`. Run `cao sdd route prepare`; new declarations belong to `.orchestrator/inputs/WORK_ID/route.json`, separate from `_bmad-output/` native artifacts. Fill the actual route fields and use `route rule` to extract literal native evidence, then `route check` before `route propose --declaration <returned-file-path>`. These commands prepare/validate input; they do not execute BMAD or approve a SPEC. Use the route contract for exact arguments and diagnostics.

Use the route declaration contract linked directly from SKILL.md for all fields and real rule hashes/quotes, required artifacts and every omitted document's reason/replacement. BMAD `route` is `direct-build` or `full-planning`; `build_route` is `dispatch` or `oneshot`, selected from installed native rules. Names such as `backend-api` and `full-stack-web-app` are not route values. Three fields (provider/route/reason) are not a complete declaration. Direct Build can retain standard gates. BMAD full-planning requires the native `prd`, `architecture`, `epics` and `sprint` roles; direct-build requires explicit omissions and replacement artifacts.

For dispatch approval/clarification/pause actions, read the BMAD approval reference selected by guidance. Approval does not substitute for native planning or implementation.

Follow the actual step-03 implementation handoff; set SPEC `in-progress` and `baseline_commit` (HEAD or NO_VCS) first. Read the full step and use the implementation child session where required and available; use the installed fallback when unavailable. Pi observes the step read and actual child product writes. Custom handoffs follow actual rendered rules. Full planning requires native Finalize, memlog/audits and required PRD/architecture reviews, not draft artifacts.
Before Pi dispatch, check `cao host status --host pi` for `child_hooks_installed`.
Run `cao host install --host pi` and restart when missing: foreground pi-subagents
children do not inherit ambient extensions. The installer merges the CAO child-only
extension default; agents with explicit child extension overrides need CAO listed
there too. Inspect actual agent names/tools first, preserve the native handoff,
and do not substitute a fabricated `task` tool or `general-purpose` agent.

### Native review and close obligations

These obligations follow the actual BMAD oneshot route for light, standard and comprehensive profiles. A route-bound primary SPEC is sufficient to identify them; standard/comprehensive must not bypass them by omitting a light `spec_binding`.

Before business implementation, configure verification and explicitly reanchor an existing Work if needed. The host blocks business writes until that baseline is accepted; native CAO/SDD artifacts, manifests and initial test/verification scaffolding remain writable. Tests do not have to pass before implementation. Do not install dependencies or expand scaffolding into implementation without the applicable authorization.

At a review action read only the route-specific reference returned by guidance: oneshot uses raw Markdown plus `review submit`; dispatch uses raw JSON, classifies ALL layers before patches, then resolves them. Preserve native independent execution, every active layer and current evidence. A manual implementation_review gate cannot replace these obligations. Follow native Finalize/rebind and verification ordering, and the native Commit step when Git is available; uncommitted Work product/SPEC changes block done. Never push unless authorized.

Bind ALL current requirement stories/epics using repeated `cao sdd plan bind --plan-item <sprint-status.yaml-or-yml>#<story-id-or-epic-N>`. A project sprint may include unrelated historical work; do not bind or complete it merely because the file exists. Without a separate plan, Direct Build still must finish its SPEC Tasks & Acceptance; `--no-plan` cannot waive these tasks. oneshot may legitimately have no task plan. Use actual native status/task progress as lifecycle source, regardless of title/filename spelling; native done means review, not CAO completion.

## Exit when

Current requirement tasks and applicable pre-verification obligations are complete; follow completion guidance and the native order for any remaining Commit/close steps. Standard/comprehensive retain workflow registration and independent final acceptance; light still honors native prerequisites.

## If blocked

Inspect `native_sync` diagnostics and associate the actual source. Source sync does not refresh acceptance hashes: review changed SPEC/criteria, explicitly rebind if needed, and reverify. Never invent entrypoints, reports or historical execution.
