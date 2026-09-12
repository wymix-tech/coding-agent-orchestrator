# Superpowers Execution Policy

Superpowers is the execution-policy layer. Compose disciplines from the **computed** Work Profile and Flow Profile; do not blindly run every skill and do not reinterpret the Decision Engine's scores.

## Policy router

| Deterministic trigger | Discipline |
|---|---|
| ambiguity score >= 1 | brainstorming / clarification |
| flow >= STANDARD, or FAST work has multiple dependent steps | writing-plans |
| isolation materially reduces risk | using-git-worktrees |
| implementing behavior | test-driven-development |
| 2+ independent, low-coupling tasks | subagent-driven-development / dispatching-parallel-agents |
| failure/root cause is unknown | systematic-debugging |
| flow >= STANDARD and implementation slice complete | requesting-code-review |
| review feedback received | receiving-code-review |
| claiming fixed/done | verification-before-completion |
| branch/change integration is required | finishing-a-development-branch |

Additional profile-derived requirements:

- ambiguity >= 2: implementation is blocked until explicit acceptance criteria/interpretation are resolved and the engine is rerun;
- novelty >= 2: research/spike and assumption validation precede dependent implementation;
- complexity >= 2: explicit design and task/dependency decomposition;
- scope >= 2: impact map and compatibility/contract checks;
- risk >= 2: explicit risk analysis and independent risk-focused review;
- verification difficulty >= 2: explicit verification plan;
- DEEP: adversarial/failure-mode review and release-readiness check.

## Flow composition

### TRIVIAL

Impact check → minimal implementation discipline → targeted check → fresh verification.

### FAST

Mini-spec/AC as needed → TDD proportional to behavior → focused review → fresh verification.

### STANDARD

Written plan/design as computed → TDD → systematic debugging as needed → spec compliance review → code quality review → quality gates → fresh verification.

### DEEP

Discovery/brainstorming → explicit plan/design → isolated/incremental implementation → TDD → systematic debugging → parallel/adversarial review where useful → expanded gates → release-readiness verification.

## Non-negotiable constraints

- Do not bypass authoritative scope because implementation looks obvious.
- Do not manually lower a Decision Engine score/flow.
- For new behavior, derive tests/checks from acceptance criteria before or with implementation.
- Establish an understood baseline before attributing failures to new work.
- Unknown failures require root-cause debugging before speculative fixes.
- Review implementation against the specification before judging elegance.
- Before saying "done", run fresh verification against the final code state.

## Fallback when Superpowers is unavailable

Preserve the required discipline explicitly and record the fallback. Never pretend an unavailable skill ran.
