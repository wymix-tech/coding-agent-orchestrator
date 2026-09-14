---
name: coding-agent-orchestrator
description: Coding workflow for start/resume/continue, implementation, debugging, review, and verification, including 开始, 继续, start, continue, and resume. Uses SDD authority, evidence-backed classification, semantic impact, Policy, state, and minimal context.
---

# Adaptive SDD Coding Orchestrator

## Purpose

Act as the control plane for coding work without replacing project-owned engineering systems.

- **SDD** owns what and why.
- **Engineering Policy** owns what implementations are allowed.
- **Code intelligence** supplies structural evidence only.
- **Decision Engine** owns required process rigor.
- **Execution State** owns where work is and what comes next.
- **Context Plane** owns which current sources a role should read.
- **Session Context** projects compact cold-start, resume, and handoff state.
- **Action Guard** decides execution, advance, and closure; host/state adapters consume it.
- **Superpowers** governs implementation discipline.
- **Activation stubs** load this Skill; they never own project truth.

## Runtime Entry Point

The Skill directory name is a deployment choice; never hardcode it. The front controller sits next to this `SKILL.md`: `<skill-root>/coding-orchestrator --repo . where` prints the resolved paths and commands; without an executable launcher use `python3 <skill-root>/scripts/coding_orchestrator.py`.

## Bootstrap & Start Intent Guard

Ensure `.orchestrator/config.yaml` exists; if missing, Safe Auto initialize once and continue the same request. For `开始`, `继续`, `start`, `continue`, or `resume`, run `start`. Follow its route: resume active work, surface blockers, auto-intake one high-confidence requirement, ask when none exists, or require selection when several exist. Never invent scope, fake a work item, or code from a bare start intent. On `ACTION_REQUIRED`, stop mutation and surface the decision.

## Lazy Reference Loading Contract

Treat this Skill package as a knowledge store, not a prompt bundle.

1. **Never preload all references.** Load only the reference the current decision needs.
2. **Normally load at most 1–2 references at a time.** Add more only when evidence is insufficient.
3. **Do not recursively follow every link.** References are retrieval targets, not an import graph.
4. **Prefer current project artifacts.** State, SDD, Policy, Context Pack, and Evidence supply facts; references explain rules.
5. **Keep heavy material lazy.** Code graphs, history, policy corpora, test logs, and old handoffs stay out of context unless directly needed.
6. **Discard stale projections.** Context Packs, Session, Handoff, and verification must match current authoritative snapshots before reliance.

## Invariants

1. **Bootstrap conservatively.** Never invent SDD authority, architecture style, quality thresholds, or an active work item.
2. **One authority per concern.** Preserve repository-native SDD/state authority; augment only missing concerns.
3. **Unknown is not false.** Missing evidence never proves low risk, low impact, or compliance.
4. **Evidence before classification.** Mechanical facts may classify directly; semantic claims require provenance.
5. **Compute process rigor.** Agents must not manually vibe-score or override deterministic Flow selection.
6. **Semantic impact precedes final scope classification** when code changes are involved.
7. **Project Policy outranks imported guidance.** External rules are non-blocking until explicitly promoted locally.
8. **Enforce MUST rules mechanically where possible.** Prefer architecture, static-analysis, build, test, and contract gates over prompt-only compliance.
9. **Freshness is snapshot-bound.** Material requirement, code, policy, evidence, or state changes invalidate stale analysis or verification.
10. **Host hooks are guardrails, not final authority.** Transition guards, CI, and merge protection must still prevent invalid completion.
11. **Session/Context artifacts are projections, not truth.** Rebuild them from authoritative state; never infer completion from chat claims.
12. **DONE is earned by evidence.** Required gates, findings, acceptance, traceability, and fresh final verification cannot be optimized away.

## Stage Router

Load references lazily according to the current need:

| Current need | Read |
|---|---|
| New repository / initialization | `references/project-bootstrap.md` |
| Workflow discovery or reconciliation | `references/orchestration-model.md` |
| SDD authority selection | `references/adapter-contract.md`, then exactly one selected `adapters-*.md` |
| Fact extraction / unresolved evidence | `references/fact-extractor.md` |
| Process classification / Flow | `references/decision-engine.md` or `references/adaptive-flow-policy.md` |
| Code-change impact | `references/semantic-impact-engine.md`; provider details only when debugging |
| Project rules / policy routing | `references/engineering-policy-layer.md`; framework-specific material only when applicable |
| Execution state / transitions | `references/execution-state-manager.md`; one state adapter when needed |
| Whether execution, advance, or closure is allowed | `references/action-authorization.md` |
| Role/stage context or stale context | `references/context-plane.md` |
| Cold start / resume / handoff | `references/session-context.md` |
| Implementation discipline | `references/superpowers-policy.md` |
| Host hook/extension behavior | `references/host-enforcement.md`; consult `host-capabilities.yaml` only for host-specific capability questions |
| Review / verification / closure | `references/quality-gates.md` and, for traceability/evidence, `references/state-and-evidence.md` |

Do not load framework-specific examples for unrelated stacks or all SDD/state adapters after authority is known.

## Runtime Workflow

Use the packaged front controller for normal operation; lower-level scripts are for debugging.

Operate through these phases:

1. **BOOTSTRAP / START**: run the Bootstrap & Start Intent Guard first; resolve current state before intake or resume, and stop at `ACTION_REQUIRED` when authority or requirement selection is ambiguous.
2. **INTAKE**: extract facts and evidence, measure semantic impact when relevant, route policy, classify process rigor, and plan verification.
3. **CONTEXT**: build/refresh the Context Manifest and minimal role/stage Context Pack; on cold start or handoff, project compact Session Context.
4. **IMPLEMENT**: use `check --action mutate_code`; work in traceable slices under Policy and Superpowers. Hooks mark material mutations stale.
5. **REVIEW / VERIFY**: refresh required semantic/policy evidence, clear blocking findings, run required gates, and bind final verification to the current execution snapshot.
6. **ADVANCE / CLOSE**: consume Action Guard results via `check --action advance --phase <phase>` or `check --action close`; never derive permission from status labels alone.

If required code-intelligence evidence is unavailable, fail closed; never reinterpret provider absence as low impact.

## Completion Contract

DONE means the authoritative work item is complete **and** Canonical Execution State can legally transition to `closed/completed` with:

- no active blocker;
- acceptance satisfied;
- required project Policy and quality gates passing;
- blocking review findings cleared;
- requirement → task → test → evidence traceability intact; and
- fresh final verification against the final execution snapshot.
