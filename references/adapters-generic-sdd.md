# Generic SDD Adapter

## Role

Use when no supported SDD system clearly governs the change, or when the repository uses another specification system.

Do not install or impose OpenSpec/BMAD automatically.

## Artifact discovery

Search for existing sources of truth such as:

- feature specs / RFCs / ADRs
- issue or story files
- architecture docs
- acceptance criteria
- task/checklist files
- test plans

Select the smallest existing artifact set that can serve as the authoritative change definition.

## Minimal readiness contract

Before non-trivial implementation, ensure the project has enough information to answer:

1. What observable behavior changes?
2. What is explicitly out of scope?
3. What constraints/architecture decisions apply?
4. What acceptance criteria prove completion?
5. What executable tasks or implementation slices exist?
6. How will each criterion be verified?

If these answers are absent, create or extend the repository's normal planning artifact rather than inventing a new framework directory.


## Adaptive depth

For TRIVIAL/FAST work, prefer the repository's smallest existing artifact that can hold scope and acceptance criteria. For STANDARD/DEEP work, add design, risk, and verification artifacts only where the Work Profile justifies them. Never create a framework-shaped document set solely for completeness.

## Superpowers bridge

Use Superpowers to provide missing process discipline:

- `brainstorming` for requirement discovery;
- `writing-plans` for executable task breakdown;
- TDD/systematic debugging during coding;
- code review and verification before completion.

The resulting design/plan may be the primary specification only when the repository has no stronger SDD authority.

## Execution state integration

Use `references/state-adapter-generic.md`. If no native execution-state authority exists, `.orchestrator/execution-state.yaml` becomes the durable execution authority while existing project specs/issues remain the requirement authority.
