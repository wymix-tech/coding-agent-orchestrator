# Orchestration Model

## Control-plane model

The orchestrator has thirteen layers:

1. **Discovery** — repository instructions, active SDD system/change, native execution-state mechanisms, git state, build/test commands, architecture, and quality policy.
2. **Evidence Collection** — deterministic collectors produce repository/Git/SDD/CI observations and mechanically defensible facts.
3. **Semantic Impact Engine** — CBM maps code changes to symbols, blast radius, relationships, runtime boundaries, and contract signals; provider risk opinions are quarantined.
4. **Engineering Policy Layer** — route project coding/architecture constraints from semantic impact; project MUST rules are governance authority, external ECC-style rules are guidance unless locally promoted.
5. **Semantic Fact Resolution** — unresolved facts are resolved against named evidence; heuristics cannot finalize facts and negative claims require bounded proof.
6. **Decision Engine** — deterministically score six dimensions and select TRIVIAL / FAST / STANDARD / DEEP.
7. **SDD Adapter** — translate the repository's native planning artifacts into a normalized change model.
8. **Execution State Manager** — normalize native/hybrid/orchestrator execution state, guard transitions, attach analysis/policy artifacts, record blockers/assignments/gates, append history, and support resume.
9. **Context Plane** — index current authorities/snapshots in a Context Manifest and project minimal role/stage Context Packs without creating a new authority.
10. **Host Enforcement Layer** — bind Context/Decision/State/Policy/Verification constraints to Claude Code, Codex, or Pi lifecycle events through one shared Enforcement Kernel.
11. **Execution Policy** — compose Superpowers disciplines required by the computed flow.
12. **Quality Gates** — evaluate repository-defined automated/human gates, including blocking Engineering Policy gates, without weakening REQUIRED policy.
13. **Evidence Ledger** — tie facts, semantic impact, policies, decisions, state transitions, implementation, review, and verification to auditable evidence.

## Normalized change model

```text
Change
  id
  intent
  requirements[]
  constraints[]
  design_decisions[]
  tasks[]
  acceptance_criteria[]
  risks[]
  verification_plan[]
  extraction_snapshot
  semantic_impact_snapshot
  semantic_impact_ref
  policy_snapshot
  policy_plan_ref
  policy_context_ref
  policy_evaluation_ref
  applicable_policy_ids[]
  work_facts
  fact_provenance
  work_profile
  decision_trace[]
  flow_profile
  required_activities[]
  implementation_blockers[]
  verification_plan_ref
  execution_state_ref
  execution_revision
  state_authority
  context_manifest_ref
  context_snapshot
  current_context_pack_ref
  enforcement_dirty
  host_runtime
  status
```

Adapters map native artifacts into this model without creating a second parallel specification or status authority.

## Discovery order

Inspect, in order:

1. Repository agent instructions and engineering/contributing docs.
2. Git state and changed/untracked SDD artifacts.
3. Explicit orchestrator/project Engineering Policy manifest and machine-enforced constraints.
4. Existing active/in-progress change artifact.
5. OpenSpec/BMAD/other SDD markers and artifacts.
6. Native execution-state mechanisms such as sprint/story status files or issue trackers.
7. Architecture/module/deployment boundaries needed to answer Work Facts.
8. Available Superpowers skills.
9. Build, test, lint, SAST, coverage, mutation, compatibility, migration, performance, and release policy.

## Requirement authority selection

Use the first decisive signal:

1. Existing active/in-progress change artifact governing this work.
2. Explicit user choice for this change.
3. Explicit repository orchestrator configuration.
4. Existing repository convention when one SDD system clearly governs new work.
5. Requirement characteristics only when choosing among allowed repository options.
6. Generic adapter fallback.

Never synchronize two requirement authorities unless explicitly requested.

## Execution-state authority selection

Use `state_provider_detector.py` plus project/native guidance.

```text
Native state exists and is authoritative
  → NATIVE

SDD owns planning/task readiness but not full runtime state
  → HYBRID

No usable native execution state
  → ORCHESTRATOR
```

Field ownership must be explicit. Never make the same state field writable in both native and orchestrator systems.

## Adaptive orchestration loop

```text
REQUEST
  ↓
DISCOVER SDD + NATIVE STATE
  ↓
EXTRACT OBSERVATIONS + PROVEN FACTS
  ↓
CBM SEMANTIC IMPACT
  ├─ PROVIDER UNAVAILABLE → FAIL CLOSED / EXPLICIT FALLBACK
  └─ IMPACT EVIDENCE
        ↓
ROUTE ENGINEERING POLICY
  ├─ POLICY CONFLICT → BLOCK + RESOLVE AUTHORITY
  ├─ APPLICABLE MUST → MAP TO ENFORCEMENT/GATES
  └─ EXTERNAL RULE SOURCE → GUIDANCE ONLY
        ↓
MAP IMPACT INTO WORK FACTS
  ↓
RESOLVE REMAINING SEMANTIC FACTS + PROVENANCE
  ↓
STRICT DECISION ENGINE
  ├─ INVALID_FACTS ──→ FIX INPUT
  ├─ NEEDS_EVIDENCE ─→ COLLECT / RESOLVE MORE
  └─ CLASSIFIED
        ↓
BUILD VERIFICATION PLAN + POLICY GATES
        ↓
SELECT SDD ADAPTER
        ↓
SELECT EXECUTION STATE PROVIDER
        ↓
INIT / RESUME CANONICAL STATE
        ↓
ATTACH ANALYSIS + POLICY REFS + SNAPSHOT
        ↓
BUILD / REFRESH CONTEXT MANIFEST + CURRENT ROLE PACK
        ↓
HOST ENFORCEMENT ACTIVE
  ├─ Session/Prompt → inject compact current context
  ├─ PreTool → deny illegal mutations
  ├─ PostTool/FileChanged → mark semantic/context/verification stale
  └─ Stop → enforce completion contract where host supports continuation
        ↓
REQUIREMENT / POLICY / CONTEXT BLOCKERS?
  ├─ YES → BLOCK + CLARIFY / SPECIFY → RECOMPUTE
  └─ NO
        ↓
RECONCILE SDD READINESS
        ↓
GUARDED TRANSITION → IMPLEMENTATION
        ↓
IMPLEMENT TRACEABLE SLICE
        ↓
MATERIAL MUTATION → DIRTY WINDOW (continued edits allowed; review/verify blocked)
        ↓
UPDATE CURSOR / PROGRESS / SNAPSHOT
        ↓
MATERIAL DISCOVERY?
  ├─ YES → REFRESH CBM → REROUTE POLICY → NEW FACT/IMPACT/POLICY SNAPSHOT → RECLASSIFY → RECONCILE
  └─ NO
        ↓
REVIEW → QUALITY GATES → FRESH VERIFY
        ↓
GUARDED CLOSE
```

## Reassessment outcome

Use flow rank comparison only:

```text
new > old  => ESCALATE planning/execution requirements
new = old  => CONTINUE
new < old  => DE_ESCALATE future rigor only if policy permits
unknown    => NEEDS_EVIDENCE; do not assume low risk
```

A flow change does not erase completed history. It updates future required activities and review/gate floors.

## Resume behavior

At session/agent start, prefer repository state over conversational memory:

1. Load authoritative SDD change/story.
2. Load/sync native execution state when applicable.
3. Load canonical execution state and validate revision/schema.
4. Reconcile drift before coding.
5. Validate host-enforcement/runtime dirty state.
6. Refresh `context-manifest.json` and generate a `resume/resume` Context Pack.
7. Read `resume` summary and execute only the reported legal next action or a stricter project-native action.

## Stop / replan conditions

Stop phase advancement and record a blocker when:

- requirement and implementation disagree;
- native and canonical owned fields drift;
- a new user-visible behavior or contract appears;
- architecture assumptions become false;
- a project MUST Engineering Policy conflicts with the design or implementation;
- policy authority/precedence is ambiguous or attempts to downgrade a stronger MUST;
- failing tests reveal unspecified behavior;
- component/module/service count changes materially;
- risk or verification characteristics change;
- a stale agent revision conflicts with newer state;
- verification cannot demonstrate acceptance criteria.
