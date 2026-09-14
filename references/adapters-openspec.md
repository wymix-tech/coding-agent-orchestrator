# OpenSpec Adapter

## Role

Use OpenSpec as the requirements/change-lifecycle authority. Its common spec-driven artifact graph is proposal → specs/design → tasks → implementation. Treat requirements as observable behavior; keep implementation mechanisms in design/tasks rather than behavioral specs.

## Detection

Strong signals include an OpenSpec project directory/configuration, OpenSpec-generated skills/commands, or an active OpenSpec change directory. Prefer live repository artifacts over fixed path assumptions.

## Readiness mapping

- Fuzzy intent → `NEEDS_DISCOVERY`
- No change/proposal → `NEEDS_SPEC`
- Behavior/design materially ambiguous → `NEEDS_SPEC` or `NEEDS_DESIGN`
- No executable task breakdown → `NEEDS_TASKS`
- Proposal/spec/design/tasks sufficient → `READY`

## Planning behavior

Use the installed OpenSpec workflow rather than reimplementing it. Typical current workflows may expose explore/propose and expanded change commands, but discover the repository's actual command surface first.

Before implementation, extract:

- Why / intent
- changed capabilities
- behavioral requirements and scenarios
- design constraints/decisions when present
- task list
- verification expectations


## Adaptive depth

Use OpenSpec proportionally. TRIVIAL non-behavioral work may need no new change artifact when repository policy permits. FAST work should use the smallest native change/spec/task set that preserves acceptance criteria and traceability. STANDARD/DEEP work should include the native design/risk detail justified by complexity and risk. Do not create empty ceremony artifacts merely to satisfy a fixed template.

## Superpowers bridge

- OpenSpec exploration can be strengthened by `superpowers:brainstorming` when intent is unclear.
- OpenSpec tasks are the source for `superpowers:writing-plans`; the execution plan may add code-level steps but MUST NOT change product scope.
- Implement each task with TDD.
- Before OpenSpec verification/archive, run `verification-before-completion` and project quality gates.

## Scope reconciliation

If code requires a behavior absent from the spec, update the relevant OpenSpec requirement/scenario first. If only implementation mechanics change, update design/tasks instead of polluting behavioral specs.

## Closure

Only run the repository's normal OpenSpec verify/sync/archive/completion flow after:

1. requirements align with implementation;
2. tasks are complete;
3. tests and gates pass;
4. blocking review findings are resolved;
5. verification evidence is fresh.

## Execution state integration

Use `references/state-adapter-openspec.md`. Default to `hybrid`: OpenSpec owns change/artifact/task planning state, while the Execution State Manager owns missing implementation/review/verification runtime state. Native OpenSpec readiness must be reconciled before implementation advancement.
