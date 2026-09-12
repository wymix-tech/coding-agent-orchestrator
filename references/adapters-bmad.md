# BMAD Adapter

## Role

Use BMAD as the planning and delivery authority when the project is governed by BMAD artifacts/workflows. Preserve its scale-adaptive behavior: small, clear work should not be inflated into enterprise ceremony; complex work should carry explicit product, architecture, and story context.

## Detection

Strong signals include BMAD installation/runtime artifacts, BMAD-generated briefs/PRDs/architecture/stories, or an active BMAD build/story workflow. Prefer the repository's installed BMAD version and generated guidance over legacy path assumptions.

## Readiness mapping

- Unclear product intent → `NEEDS_DISCOVERY`
- Missing product behavior/scope → `NEEDS_SPEC`
- Cross-cutting technical uncertainty → `NEEDS_DESIGN`
- No implementation-ready story/task → `NEEDS_TASKS`
- Active implementation-ready story with acceptance criteria/context → `READY`

## Planning behavior

Use the BMAD workflow appropriate to the size of the work. Normalize, but do not duplicate:

- brief/intent and scope
- PRD requirements or equivalent product behavior
- architecture decisions/constraints
- story/task acceptance criteria
- implementation context and dependencies
- test/verification expectations


## Adaptive depth

Preserve BMAD's scale-adaptive behavior. TRIVIAL work should not be inflated into product/architecture ceremony. FAST work may use the lightest native planning path that yields implementation-ready acceptance criteria. STANDARD/DEEP work should carry the product, architecture, story, and risk context needed by the Work Profile.

## Superpowers bridge

BMAD answers **what/why/context**; Superpowers governs engineering execution:

- Use `brainstorming` only when BMAD artifacts still contain unresolved ambiguity.
- Use `writing-plans` to translate an implementation-ready BMAD story into code-level execution steps, without changing the story scope.
- Use `test-driven-development` for story implementation.
- Use `systematic-debugging` for unexpected failures rather than speculative edits.
- Use code review and verification before changing a BMAD story to done.

## Scope reconciliation

If an implementation task uncovers a requirement or architecture change, route it back to the appropriate BMAD product/architecture/story artifact. Do not hide scope expansion inside code or test changes.

## Closure

A story/change is complete only when its acceptance criteria are demonstrably satisfied, project gates pass, review blockers are cleared, and BMAD's native progress/status workflow is updated.

## Execution state integration (v5)

Use `references/state-adapter-bmad.md`. When a native sprint-status artifact exists, prefer `native` authority. Do not duplicate writable story phase/status/progress in `.orchestrator`; store only extension state and a canonical projection for resume/guard evaluation.
