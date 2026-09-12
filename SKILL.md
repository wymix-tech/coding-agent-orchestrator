---
name: orchestrating-sdd-coding
alias: adaptive-sdd-coding-orchestrator
description: Use when coding work should be governed through an existing or generic SDD flow with evidence-backed classification, semantic impact, Engineering Policy, durable execution state, minimal role-aware context, resumable session context, and runtime enforcement.
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
- **Enforcement Kernel** guards host actions; state/CI remain final boundaries.
- **Superpowers** governs implementation discipline.

## Lazy Reference Loading Contract

Treat this Skill package as a knowledge store, not a prompt bundle.

1. **Never preload all references.** Load only the reference needed for the current decision or stage.
2. **Normally load at most 1–2 references at a time.** Add another only when the current reference explicitly requires it or evidence is insufficient.
3. **Do not recursively follow every link.** References are retrieval targets, not an import graph.
4. **Prefer current project artifacts over generic reference text.** State, SDD, Policy, Context Pack, and Evidence are current facts; references explain rules.
5. **Keep heavy material lazy.** Full code graphs, execution history, policy corpora, test logs, and old handoffs stay out of prompt context unless directly needed.
6. **Discard stale projections.** Context Packs, Session Bootstrap, Handoff, semantic analysis, and verification must match current authoritative snapshots before reliance.

## Invariants

1. **Bootstrap conservatively.** Never invent SDD authority, architecture style, quality thresholds, or an active work item.
2. **One authority per concern.** Preserve repository-native SDD/state authority; augment only missing concerns.
3. **Unknown is not false.** Missing evidence never proves low risk, low impact, or compliance.
4. **Evidence before classification.** Mechanical facts may classify directly; semantic claims require provenance.
5. **Compute process rigor.** Agents must not manually vibe-score or override deterministic Flow selection.
6. **Semantic impact precedes final scope classification** when code changes are involved.
7. **Project Policy outranks imported guidance.** External rules are non-blocking until explicitly promoted locally.
8. **Enforce MUST rules mechanically where possible.** Prefer architecture, static-analysis, build, test, and contract gates over prompt-only compliance.
9. **Freshness is snapshot-bound.** Material requirement, code, policy, evidence, or state changes invalidate stale analysis or verification as applicable.
10. **Host hooks are guardrails, not final authority.** Transition guards, CI, and merge protection must still prevent invalid completion.
11. **Session/Context artifacts are projections, not truth.** Rebuild them from authoritative state; never infer completion from chat claims.
12. **DONE is earned by evidence.** Required policy/quality gates, review findings, acceptance, traceability, and fresh final verification cannot be optimized away.

## Stage Router

Load references lazily according to the current need:

| Current need | Read |
|---|---|
| New repository / initialization | `references/project-bootstrap.md` |
| Workflow discovery or reconciliation | `references/orchestration-model.md` |
| SDD authority selection | `references/adapter-contract.md`, then exactly one selected `adapters-*.md` |
| Fact extraction / unresolved evidence | `references/fact-extractor.md` |
| Process classification / Flow | `references/decision-engine.md` or `references/adaptive-flow-policy.md` |
| Code-change impact | `references/semantic-impact-engine.md`; read provider details only when debugging/integrating the provider |
| Project rules / policy routing | `references/engineering-policy-layer.md`; load framework-specific or ECC material only when applicable |
| Execution state / transitions | `references/execution-state-manager.md`; load exactly one state adapter when needed |
| Role/stage context or stale context | `references/context-plane.md` |
| Cold start / resume / handoff | `references/session-context.md` |
| Implementation discipline | `references/superpowers-policy.md` |
| Host hook/extension behavior | `references/host-enforcement.md`; consult `host-capabilities.yaml` only for host-specific capability questions |
| Review / verification / closure | `references/quality-gates.md` and, when traceability/evidence is the issue, `references/state-and-evidence.md` |

Do not load framework-specific examples for unrelated stacks. Do not load all SDD/state adapters to compare them after authority is already known.

## Runtime Workflow

Use the unified front controller for normal operation:

```bash
./coding-orchestrator init
./coding-orchestrator intake "<requirement>"
./coding-orchestrator status
./coding-orchestrator resume
./coding-orchestrator verify
```

Operate through these phases:

1. **BOOTSTRAP**: discover repository, SDD/state authority, technology, host, and safe project policy. Stop at `ACTION_REQUIRED` when authority is ambiguous.
2. **INTAKE**: extract facts and evidence, measure semantic impact when relevant, route policy, classify process rigor, and plan verification.
3. **CONTEXT**: build/refresh the Context Manifest and minimal role/stage Context Pack; on cold start or handoff, project compact Session Context from authoritative state.
4. **IMPLEMENT**: work in traceable slices under project Policy and Superpowers discipline. Runtime hooks perform cheap checks and mark affected analysis/verification stale after material mutations.
5. **REVIEW / VERIFY**: refresh required semantic/policy evidence, clear blocking findings, run required gates, and bind final verification to the current execution snapshot.
6. **CLOSE**: allow closure only when the Completion Contract passes.

Use lower-level scripts only for debugging, custom integration, or explicit automation. If required code-intelligence evidence is unavailable, fail closed; never reinterpret provider absence as low impact.

## Completion Contract

DONE means the authoritative work item is complete **and** Canonical Execution State can legally transition to `closed/completed` with:

- no active blocker;
- acceptance satisfied;
- required project Policy and quality gates passing;
- blocking review findings cleared;
- requirement → task → test → evidence traceability intact; and
- fresh final verification against the final execution snapshot.
