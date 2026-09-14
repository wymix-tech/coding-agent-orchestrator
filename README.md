# Coding Agent Orchestrator

[中文](README-zh.md)

> Auto Bootstrap + Unified CLI + Adaptive SDD + Evidence + Semantic Impact + Engineering Policy + Context Plane + Runtime Enforcement

**Current version: V6.5**

## Shared execution, advance, and close authorization

All permission decisions use `scripts/action_guard.py`. Host hooks, state transitions,
CLI verification, and start/resume views consume the same result and reason codes.
Inspect a decision without modifying the project:

```bash
python3 ./coding-orchestrator --repo /path/to/project --json check --action mutate_code
python3 ./coding-orchestrator --repo /path/to/project --json check --action advance --phase review
python3 ./coding-orchestrator --repo /path/to/project --json check --action close
```

Exit `0` allows and exit `1` denies. The result includes `allowed`, `reason_codes`,
`reasons`, and `next_action`; the actual execution boundary rechecks permission.
A successful `verify` checks closure readiness; it does not run tests or close work.

The shared policy checks active blockers, classified/complete/current evidence,
SDD readiness, phase and role restrictions, required gates/review, and final verification.
Gate/review/verification evidence binds both the execution snapshot and actual input
content. Requirement or policy changes invalidate old results even if an analysis ID
is reused. Missing plan gates cannot be omitted to pass closure, and stop retry limits
never grant permission.

Existing projects need fresh intake for the same active requirement, resolved fact
evidence, refreshed context, and newly recorded applicable gate/review/verification
results when old records lack content bindings. See
[Action Authorization](references/action-authorization.md) for the action contract,
tracked edit windows, native authority, migration, and implementation boundaries.

### V6.5 durability and lifecycle hardening

The current V6.5 line also hardens the control plane around concurrency and long-lived work:

- Execution state/history commits use a cross-process lock plus a recoverable transaction journal; stale revisions fail explicitly instead of losing updates.
- Requirements have a stable identity separate from their content revision. Intake artifacts live under `.orchestrator/work-items/<work>/revisions/<revision>/runs/<run>/`; a stale long-running analysis cannot replace newer canonical state.
- Completed work is a historical fact distinct from **current** close readiness. A later repository change cannot resurrect an already governed `closed/completed` work item; native SDD `done` alone still does not imply Governance Done.
- Policy snapshots bind effective rule semantics and all referenced project-owned packs, including packs outside the default policy directory.
- BMAD discovery follows configured/detected native sprint state and fails with sourced diagnostics for unknown formats or missing story artifacts instead of guessing from installation templates.
- Required verification obligations survive plan shrinkage until explicitly disposed as `superseded`, `not_applicable`, or `waived` with reason, authority, and evidence. These dispositions are not reported as test passes.
- CBM CLI calls have a bounded timeout (`ORCHESTRATOR_CBM_TIMEOUT_SECONDS`, default 120s); timeout/runtime/JSON-contract failures are terminal for that invocation and are not masked by syntax fallbacks.

Migration notes: legacy active work without stable requirement identity is not guessed and requires explicit migration/closure. Legacy orchestrator-owned `closed/completed` state remains historically complete; native-only closed state without a Governance completion record remains not done. Generated work-item/registry artifacts are control-plane material and do not dirty the business-code snapshot. Detailed repair status, migration notes, verification commands, and external-integration limitations are recorded in [REPAIR_REPORT.md](REPAIR_REPORT.md).

Coding Agent Orchestrator is an engineering control plane for AI coding agents. It does not replace OpenSpec, BMAD, Superpowers, CI, or code-graph tools. Instead, it composes them into a development lifecycle that is auditable, resumable, adaptive, and enforceable at runtime.

The core problem is not whether an agent can write code. The project is designed to answer a broader set of engineering questions:

- How much process does this requirement actually need?
- What evidence supports that decision?
- Where is the work currently in the development lifecycle?
- Which modules, services, contracts, and tests are affected by the change?
- Which architecture and coding constraints apply to the implementation?
- What context should the current agent actually see?
- Are those constraints enforced, or merely mentioned in a prompt?
- When is the system allowed to declare the work DONE?

---

## 1. Project Positioning

Coding Agent Orchestrator decomposes AI-assisted software development into control layers with explicit responsibilities:

```text
Project Bootstrap / Unified CLI
        │
        ▼
Requirement / SDD
        │
        ▼
Evidence-backed Work Facts
        │
        ▼
Deterministic Decision Engine
        │
        ├── TRIVIAL
        ├── FAST
        ├── STANDARD
        └── DEEP
        │
        ▼
CBM Semantic Impact
        │
        ▼
Engineering Policy
        │
        ▼
Context Plane
        │
        ▼
Execution State Manager
        │
        ▼
Session Bootstrap / Handoff
        │
        ▼
Host Enforcement Kernel
        │
   ┌────┼────┐
   ▼    ▼    ▼
Claude Codex Pi
Code
        │
        ▼
Superpowers / Coding Execution
        │
        ▼
Quality + Policy + Verification Gates
        │
        ▼
       DONE
```

Each layer has a strict responsibility boundary:

| Layer | Question it answers | Authority |
|---|---|---|
| SDD | What are we building, and why? | OpenSpec / BMAD / Generic SDD |
| Work Facts | What can currently be proven? | Repository / Git / SDD / CI / Evidence |
| Decision Engine | How much process is required? | Deterministic rules |
| Semantic Impact | What is affected by the change? | CBM structural evidence |
| Engineering Policy | What implementation patterns are allowed? | Project-owned policy |
| Context Plane | What should this role see right now? | Manifest + role/stage projection |
| Execution State | Where are we, and what happens next? | Native / Hybrid / Orchestrator state |
| Session Context | What should a cold-start/resumed/handoff agent see first? | JSON projection over State + Context Plane |
| Host Enforcement | Is the current action allowed? | Shared Enforcement Kernel |
| Superpowers | How should the agent perform the work? | Execution discipline |
| Quality Gates | Are completion conditions satisfied? | Tests / Static analysis / Review / Verification |

A central principle is that **no single tool is allowed to become the global authority for everything**.

For example, CBM may prove that a cross-service call exists, but it cannot decide whether the flow must be STANDARD or DEEP. ECC may provide useful engineering guidance, but it does not automatically become a project-level MUST rule. Claude Code, Codex, and Pi hooks can block actions at runtime, but final completion authority still belongs to Execution State plus CI and required gates.

---

## 2. Version Evolution

Coding Agent Orchestrator evolved from an SDD orchestration skill into a broader coding-governance plane:

| Version | Primary capability |
|---|---|
| V1 | OpenSpec / BMAD / Generic SDD adapters + Superpowers + Quality Gates |
| V2 | Adaptive Flow: select TRIVIAL / FAST / STANDARD / DEEP from work characteristics |
| V3 | Deterministic Decision Engine: remove subjective agent scoring |
| V4 | Evidence-backed Fact Extractor: facts require evidence |
| V5 | Execution State Manager: phases, tasks, blockers, gates, resume, and history |
| V6.0 | CBM Semantic Impact: changed-symbol and blast-radius analysis |
| V6.1 | Engineering Policy Layer: project-level architecture and development constraints |
| V6.2 | Context Manifest / Context Pack: unified project context plane |
| V6.3 | Claude Code / Codex hooks + Pi extension runtime enforcement |
| V6.4 | Session Bootstrap / Task Handoff for cold start, resume, and role transfer |
| **V6.5** | **Project Bootstrap + Unified CLI: one-time initialization, discovery, Doctor, and stable human/agent entry points** |

V6.5 adds no new governance authority. It productizes V3~V6.4 behind stable entry points: `init / discover / doctor / status / start / intake / resume / check / verify / host install`. A repository is initialized once; later sessions recover through host adapters and the Context Plane.

V6.4 builds on V6.3 runtime enforcement by solving how a new session/agent reconstructs current engineering reality without relying on old chat history. The defining V6.3 transition is from:

```text
"Please follow these rules"
```

to:

```text
Prompt-guided
    ↓
Runtime-enforced
    ↓
State-guarded
    ↓
CI-verified
```

---

## 3. Core Design Principles

### 3.1 SDD Owns What / Why

The Orchestrator does not invent a fourth SDD system. If a repository already uses OpenSpec or BMAD, its native requirement, specification, design, and task artifacts remain authoritative.

```text
OpenSpec / BMAD / Generic
          │
          ▼
Orchestrator Adapter
          │
          ▼
Unified Execution Model
```

### 3.2 The Decision Engine Owns How Much Process

Not every request should go through a heavyweight lifecycle.

The Decision Engine computes six dimensions from evidence-backed facts:

```text
Ambiguity
Complexity
Scope
Risk
Novelty
Verification Difficulty
```

It then selects one of:

```text
TRIVIAL
FAST
STANDARD
DEEP
```

Agents are not allowed to directly edit the computed scores or manually choose a lighter flow.

### 3.3 Unknown != False

Absence of evidence is not evidence of absence.

For example:

```text
No external consumer was found
```

does not automatically imply:

```text
external_consumers = false
```

A negative proof or authoritative evidence is required.

### 3.4 CBM Provides Structural Evidence Only

The only Code Intelligence Provider currently integrated in V6 is **Codebase Memory MCP (CBM)**.

CBM is used to provide structural evidence such as:

```text
changed symbols
call relationships
imports
cross-service edges
HTTP / async / data-flow relationships
blast radius
```

CBM's own risk labels are not governance authority:

```text
CBM: HIGH RISK
```

must not directly become:

```text
Flow = DEEP
```

Structural evidence first becomes Work Facts. The Decision Engine then classifies the work.

### 3.5 Project Policy > External Guidance

The project-owned `.orchestrator/policies/` directory is the authority for engineering constraints.

External rule sources such as ECC remain guidance unless a rule is explicitly promoted into project policy.

### 3.6 State over Chat History

Engineering truth should not depend on a single agent session.

A new agent should be able to recover the current work from repository state:

```text
SDD
Decision
Execution State
Semantic Impact
Engineering Policy
Context Manifest
Evidence
```

It should not need to reread an entire historical conversation.

### 3.7 DONE Must Be Proven

```text
DONE != code was written
DONE != unit tests are green
DONE != the agent says "finished"
```

DONE requires, at minimum:

```text
SDD / Native Work Item Complete
+
Acceptance Criteria Satisfied
+
Required Review Passed
+
Required Quality Gates Passed
+
Required Policy Gates Passed
+
No Blocking Finding
+
Fresh Verification
+
Verification Snapshot == Current Execution Snapshot
```

---

## 4. Repository Structure

```text
coding-agent-orchestrator/
├── README.md              # English (default)
├── README-zh.md           # 中文
├── coding-orchestrator
├── coding-orchestrator.cmd
├── SKILL.md
├── CHANGELOG.md
├── MANIFEST.json
├── THIRD_PARTY_NOTICES.md
│
├── scripts/
│   ├── coding_orchestrator.py
│   ├── project_discovery.py
│   ├── project_bootstrap.py
│   ├── project_activation.py
│   ├── fact_extractor.py
│   ├── fact_resolver.py
│   ├── decision_engine.py
│   ├── code_intelligence_provider.py
│   ├── cbm_provider.py
│   ├── impact_mapper.py
│   ├── verification_planner.py
│   ├── semantic_intake_pipeline.py
│   ├── execution_state_manager.py
│   ├── state_provider_detector.py
│   ├── policy_bootstrap.py
│   ├── policy_engine.py
│   ├── ecc_rules_adapter.py
│   ├── context_plane.py
│   ├── session_context.py
│   ├── enforcement_kernel.py
│   └── install_host_adapter.py
│
├── policies/
│   ├── manifest.yaml
│   ├── common-engineering.yaml
│   └── spring-boot-layered.yaml
│
├── hosts/
│   ├── claude-code/
│   │   └── hooks.template.json
│   ├── codex/
│   │   └── hooks.template.json
│   └── pi/
│       └── coding-orchestrator.template.ts
│
├── references/
│   ├── project-bootstrap.md
│   ├── decision-engine.md
│   ├── semantic-impact-engine.md
│   ├── engineering-policy-layer.md
│   ├── context-plane.md
│   ├── session-context.md
│   ├── execution-state-manager.md
│   ├── host-enforcement.md
│   ├── host-capabilities.yaml
│   └── ...
│
├── examples/
│   ├── orchestrator-config.yaml
│   ├── enforcement.yaml
│   ├── session-context.yaml
│   ├── session-bootstrap-example.json
│   ├── task-handoff-example.json
│   ├── work-facts-*.json
│   ├── semantic-impact-cbm-example.json
│   ├── context-manifest-example.json
│   ├── context-pack-implementer-example.json
│   └── execution-state-*.yaml
│
├── evals/
│   └── pressure-scenarios.md
│
└── tests/
    ├── test_decision_engine.py
    ├── test_fact_extractor.py
    ├── test_execution_state_manager.py
    ├── test_semantic_impact.py
    ├── test_policy_engine.py
    ├── test_context_plane.py
    ├── test_session_context.py
    ├── test_enforcement_kernel.py
    ├── test_project_activation.py
    └── test_v65_bootstrap_cli.py
```

---

## 5. Runtime Requirements

### Required

- Python 3
- A Git repository
- A well-defined project working directory

The Python implementation primarily uses the standard library; `PyYAML` is required because execution state, engineering policy, and repository configuration use YAML.

### Recommended for V6 Semantic Impact

Install and expose the CBM CLI/binary corresponding to `codebase-memory-mcp`.

The default V6 policy is: **fail closed when CBM is unavailable**, because an unavailable provider must not be interpreted as evidence of low impact.

For offline tests, use the included fixture:

```bash
--cbm-fixture examples/cbm-detect-changes-fixture.json
```

### Recommended for Spring Boot Projects

For architecture layering constraints, add ArchUnit to the project as the final REQUIRED architecture gate.

The built-in V6 policy checker provides fast feedback. ArchUnit provides stronger repository-level proof.

### Optional Host Enforcement Targets

V6.4 retains the V6.3 host integrations and adds Session Bootstrap/Handoff injection for:

- Claude Code hooks
- Codex hooks
- Pi extension

Host hooks are never the only security boundary. Keep V5 state guards, CI, and merge/branch protection in place.

---

## 6. Quick Start

### 6.0 Recommended path: self-bootstrap on first activation

The recommended project-local installation is:

```text
project/.agents/skills/coding-agent-orchestrator/
```

Normally, you no longer need to remember a separate first-run step. When this Skill is selected for the first time, its **Bootstrap Guard** checks `.orchestrator/config.yaml`; if missing, it runs the packaged Safe Auto `init` once and then continues the same user request.

Manual initialization remains available from the **project root**:

```bash
./.agents/skills/coding-agent-orchestrator/coding-orchestrator --repo . init
```

Windows:

```bat
.agents\skills\coding-agent-orchestrator\coding-orchestrator.cmd --repo . init
```

If the Skill directory itself is your current working directory, the shorter form still works:

```bash
./coding-orchestrator init
```

`init` conservatively performs:

```text
Repository Discovery
  -> technology / build / framework
  -> SDD authority detection
  -> current Agent host detection
  -> CBM availability detection
  -> architecture evidence
  -> safe Engineering Policy bootstrap
  -> enforcement/session config
  -> project Activation Stub (AGENTS.md; CLAUDE.md when applicable)
  -> current Agent host adapter installation (Claude Code fallback)
  -> fresh-project Session Bootstrap
  -> READY_FOR_INTAKE
```

**It does not invent an active task.** The first real `execution-state.yaml` is created by the first `intake`.

Safe-auto rules:

- Auto-configure only facts that can be proven.
- Multiple SDD authorities produce `ACTION_REQUIRED`; the tool does not guess.
- Detecting Spring Boot alone does not prove classic `web -> service -> dao`; that policy is auto-enabled only when repository structure proves it.
- Safe Auto selects the **currently running Agent host**, not whichever host folders/binaries happen to exist in the repository. Pi is detected from its runtime signal/process ancestry, Codex from its runtime signal/process ancestry, and an unrecognized runtime deterministically falls back to Claude Code.
- When a bootstrapped project is later opened from a different supported Agent, Bootstrap Guard reconciles only that host adapter; it does not rerun project initialization or rebuild the active work item.
- Missing CBM does not block bootstrap, but normal Semantic Intake still fails closed.
- Existing project policy/config is preserved by default.

#### Cold-start activation

Placing the Skill under `.agents/skills/coding-agent-orchestrator/` makes it discoverable. If a vague first prompt such as `start`, `continue`, `resume`, `开始`, or `继续` selects this Skill, the Bootstrap Guard automatically runs Safe Auto `init` once, creates the activation layer, and continues that same turn. After that first successful bootstrap, `AGENTS.md` / host integration makes future vague resumes much more reliable.

There is still one unavoidable first-use boundary: if a host does **not** select the Skill at all for an extremely vague prompt before activation exists, the Skill cannot execute code it has not loaded. In that case, mention `coding-agent-orchestrator` explicitly once or run the manual `init` command above.

`init` idempotently creates or updates a managed block in `AGENTS.md`. With `--host auto` (the default), it installs exactly one adapter for the **current Agent runtime**. Pi installs the Pi extension, Codex installs Codex hooks, and an unrecognized runtime installs Claude Code hooks plus the managed `CLAUDE.md` fallback. Existing project instructions outside managed blocks are preserved.

The activation files only answer **which workflow to load**. They never become a source of project truth. Once activated, the Skill must resume from `.orchestrator/`, SDD authority, Policy, and Evidence.

#### What a bare `start` means

A short prompt such as `start`, `continue`, `resume`, `开始`, or `继续` is treated as a control intent, not as a product requirement. The activation layer routes it to:

```bash
./.agents/skills/coding-agent-orchestrator/coding-orchestrator --repo . start
```

The command then deterministically chooses the next legal action:

```text
UNBOOTSTRAPPED -> Safe Auto init
active work -> resume current work
blocked work -> surface blockers
no active work + no actionable requirement -> ask the user for the first requirement
no active work + one high-confidence requirement -> auto intake
no active work + multiple requirements -> ACTION_REQUIRED / ask the user to select
```

The router never starts production coding merely because the user said `start`. Generic Requirement Discovery ignores `.agents/`, `.orchestrator/`, host config, build output, and ordinary install-only READMEs.

Recommended project layout after initialization:

```text
project/
├── AGENTS.md                         # generated/merged activation stub
├── CLAUDE.md                         # only when Claude Code applies
├── .agents/
│   └── skills/
│       └── coding-agent-orchestrator/
│           ├── SKILL.md
│           ├── references/
│           ├── scripts/
│           └── ...
├── .orchestrator/                    # project runtime truth
├── .claude/ .codex/ .pi/             # host integration only
└── src/
```

After initialization, from the project root:

```bash
ORCH=./.agents/skills/coding-agent-orchestrator/coding-orchestrator
$ORCH --repo . doctor
$ORCH --repo . status
$ORCH --repo . start
$ORCH --repo . intake "Add a user lookup REST API"
$ORCH --repo . resume
$ORCH --repo . verify
```

Host switching is normally automatic on the next Bootstrap Guard activation. Explicit installation remains available when you want to preinstall or override detection:

```bash
$ORCH --repo . host install pi
$ORCH --repo . host install claude-code
$ORCH --repo . host install codex
```

Machine/CI usage:

```bash
$ORCH --repo . --json discover
$ORCH --repo . --json doctor
$ORCH --repo . init --ci
```

`--ci` returns non-zero when authority ambiguity cannot be resolved safely.

> Sections 6.1~6.6 retain the internal/manual path for engine debugging and custom integrations. Normal repositories should prefer the V6.5 Unified CLI.


The examples below assume the Orchestrator package is accessible from the project repository.

### 6.1 Install Project Engineering Policy

```bash
python scripts/policy_bootstrap.py --repo .
```

Existing files are not overwritten by default.

To force replacement of starter policies:

```bash
python scripts/policy_bootstrap.py --repo . --force
```

This creates or completes:

```text
.orchestrator/
└── policies/
    ├── manifest.yaml
    ├── common-engineering.yaml
    └── spring-boot-layered.yaml
```

These starter files should be adapted to the actual architecture of the repository rather than kept unchanged indefinitely.

### 6.2 Detect the Execution State Provider

```bash
python scripts/state_provider_detector.py .
```

Typical recommendations:

| Project | Recommended mode |
|---|---|
| BMAD + sprint-status | `native` |
| OpenSpec | `hybrid` |
| No native execution state | `orchestrator` |

### 6.3 Initialize Execution State

For example, in an OpenSpec repository:

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  init \
  --work-id CHG-42 \
  --title "Login reporting" \
  --flow STANDARD \
  --provider openspec \
  --authority-mode hybrid
```

View the resume summary:

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  resume
```

### 6.4 Run the Full Semantic Intake Pipeline

In V6.5 this remains the internal/advanced semantic-intake entry point:

```bash
python scripts/semantic_intake_pipeline.py \
  --repo . \
  --request-file request.md \
  --resolutions .orchestrator/fact-resolutions.json \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --state .orchestrator/execution-state.yaml \
  --sync-state \
  --sdd-provider openspec \
  --sdd-ref openspec/changes/login-reporting \
  --context-role implementer \
  --context-stage implementation
```

The pipeline performs:

```text
Fact Extraction
      ↓
CBM Semantic Impact
      ↓
Policy Routing
      ↓
Impact → Work Facts
      ↓
Semantic Resolution
      ↓
Decision Classification
      ↓
Verification Planning
      ↓
Execution State Sync
      ↓
Context Manifest
      ↓
Role/Stage Context Pack
```

### 6.5 Install Host Enforcement Adapters

Dry-run first:

```bash
python scripts/install_host_adapter.py \
  --repo . \
  --host all
```

Then apply:

```bash
python scripts/install_host_adapter.py \
  --repo . \
  --host all \
  --apply
```

Install a single host if preferred:

```bash
python scripts/install_host_adapter.py --repo . --host claude-code --apply
python scripts/install_host_adapter.py --repo . --host codex --apply
python scripts/install_host_adapter.py --repo . --host pi --apply
```

The installer creates or merges host configuration and produces backups when necessary.

---

## 7. Adaptive Flow

The Orchestrator does not force every task through the same process depth.

### TRIVIAL

Suitable for:

```text
copy changes
simple configuration
clear, local, non-risk adjustments
```

Typical flow:

```text
Impact Check
  ↓
Implement
  ↓
Targeted Verify
  ↓
Done
```

### FAST

Suitable for well-defined features or bug fixes with limited impact.

```text
Mini Spec / AC
  ↓
Tasks
  ↓
Implementation
  ↓
Targeted Tests
  ↓
Verification
```

### STANDARD

Suitable for normal feature work, multi-file or multi-module changes, APIs, or data-model changes.

```text
Spec
  ↓
Design
  ↓
Tasks
  ↓
Implementation
  ↓
Review
  ↓
Quality Gates
  ↓
Verification
```

### DEEP

Suitable for high-risk or high-complexity work, cross-service changes, authentication/authorization, and destructive migrations.

```text
Discovery
  ↓
Architecture / Alternatives
  ↓
Risk Analysis
  ↓
Detailed Spec
  ↓
Incremental Implementation
  ↓
Adversarial Review
  ↓
Security / Compatibility / Migration Checks
  ↓
Fresh Verification
```

### Dynamic Escalation and De-escalation

If facts change during implementation, re-run the analysis:

```text
FAST
 ↓
Cross-service dependency discovered
 ↓
Recompute Work Facts + CBM Impact
 ↓
STANDARD
```

The reverse is also allowed when investigation proves that a seemingly complex issue is actually a local bug. However, previously established security and quality obligations are not silently removed.

---

## 8. Deterministic Decision Engine

Since V3, agents are not allowed to score work subjectively.

Run:

```bash
python scripts/decision_engine.py \
  .orchestrator/intake/work-facts.resolved.json \
  --strict-evidence \
  --pretty
```

The engine uses structured Work Facts, combination rules, and override rules.

For example:

```text
Only two lines of code changed
but the change is on an authentication path
```

must not become TRIVIAL merely because the textual diff is small.

A risk override may state:

```text
authentication / authorization
→ minimum STANDARD

critical risk
→ force DEEP
```

Ambiguous work also does not automatically become DEEP. High ambiguity first creates an implementation blocker:

```text
Clarify
  ↓
Complete Acceptance Criteria
  ↓
Regenerate Work Facts
  ↓
Reclassify
```

---

## 9. Evidence-backed Work Facts

The Fact Extractor is deliberately conservative:

```text
Mechanical Evidence
→ may directly produce a Fact

Heuristic
→ may only produce a Hint

Unknown
→ remains Unknown
```

For example, a path such as:

```text
src/auth/token_validator
```

may suggest:

```text
possibly authentication-related
```

but may not directly assert:

```json
{"risk": {"authn_authz": true}}
```

The semantic resolver must add code inspection or other authoritative evidence.

All facts are also type-checked so that malformed values such as:

```json
{"authn_authz": "false"}
```

cannot be accidentally interpreted as truthy values by Python.

---

## 10. CBM Semantic Impact

V6 currently integrates a single Code Intelligence Provider: **Codebase Memory MCP**.

The provider abstraction lives in:

```text
scripts/code_intelligence_provider.py
```

The current implementation is:

```text
scripts/cbm_provider.py
```

Inspect capabilities:

```bash
python scripts/cbm_provider.py capabilities
```

Check health:

```bash
python scripts/cbm_provider.py health
```

The health output includes the detected CBM CLI protocol. `coding-orchestrator doctor` also runs
a non-mutating CLI smoke probe. For modern schema CLI builds the adapter requests explicit JSON
with `cli <tool> --... --format json`; older builds use stdin/inline JSON compatibility only after
protocol detection. It also checks common install locations such as `~/.local/bin` when an Agent
process has a reduced PATH. Runtime/indexing failures are never masked by syntax fallbacks. On
current CBM builds, a direct index smoke test is:

```bash
codebase-memory-mcp cli index_repository --repo-path /absolute/path/to/repo
codebase-memory-mcp cli list_projects --format json --detail stats
```

Current CBM accepts `detect_changes --scope files|impact` (not the Orchestrator's intake scope
names), and `check_index_coverage` requires concrete `paths` or `scopes`. A project containing
only BMAD/Orchestrator metadata is treated as an empty greenfield project: CBM is not invoked
until product code exists, and the absence of a Git `HEAD` is not misreported as a provider outage.

A CBM operational failure now opens a persistent provider incident instead of triggering endless
semantic-intake retries. Inspect/reset it with:

```bash
coding-orchestrator --repo . provider status codebase-memory-mcp
coding-orchestrator --repo . provider reset codebase-memory-mcp
```

The first failure stays fail-closed; repeated automatic attempts return `PROVIDER_BLOCKED` with
`next_action=repair_cbm_provider`. A binary/version change automatically permits one fresh attempt.

CBM helps the Orchestrator derive:

```text
Git Change
   ↓
Changed Symbols
   ↓
Call / Import / HTTP / Async / Data Relationships
   ↓
Affected Modules / Services / Projects
   ↓
Blast Radius
```

### Provider Risk Quarantine

CBM's own risk classification is retained only as provider opinion:

```yaml
provider_opinion:
  risk_labels:
    - HIGH
  authoritative_for_flow: false
```

The Decision Engine remains the only authority for Flow classification.

### Pagination Completeness

If a CBM blast-radius response contains a continuation cursor or partial result, the Orchestrator does not treat the first page as a complete upper bound.

```text
partial semantic impact
→ NEEDS_EVIDENCE
```

unless explicitly overridden with:

```bash
--allow-partial-impact
```

This flag is an escape hatch, not the recommended default.

---

## 11. Engineering Policy

Engineering Policy expresses:

> What is allowed in this project.

This is distinct from SDD:

```text
SDD
→ What / Why

Engineering Policy
→ What is allowed
```

### Policy Manifest

Default entry point:

```text
.orchestrator/policies/manifest.yaml
```

Rules support three levels:

```text
MUST
SHOULD
PREFER
```

Recommended semantics:

| Level | Behavior |
|---|---|
| MUST | May map to a blocking gate |
| SHOULD | Review finding / warning |
| PREFER | Advisory only |

### Spring Boot Layered Policy

The starter policy models:

```text
web/controller -> service -> dao/repository
```

and forbids:

```text
web -> dao
service -> web
dao -> service/web
```

For example:

```java
@RestController
class UserController {
    UserDao dao;
}
```

triggers:

```text
ARCH-SPRING-LAYER-001
web -> dao forbidden
```

### Policy Routing

Select only policies that are actually applicable to the current Semantic Impact:

```bash
python scripts/policy_engine.py route \
  --repo . \
  --impact .orchestrator/intake/semantic-impact.json \
  --manifest .orchestrator/policies/manifest.yaml \
  --stage implementation \
  --output .orchestrator/intake/policy-plan.json
```

Run lightweight policy checks:

```bash
python scripts/policy_engine.py evaluate \
  --repo . \
  --plan .orchestrator/intake/policy-plan.json \
  --manifest .orchestrator/policies/manifest.yaml \
  --output .orchestrator/intake/policy-evaluation.json
```

### ECC Integration

ECC `rules/` may be used as an external source of engineering guidance.

Default status:

```text
ECC rules
→ external guidance
→ non-blocking
```

Only rules explicitly converted into `.orchestrator/policies/*.yaml` may become blocking project-level MUST rules.

This prevents external rule updates from silently changing project gates.

---

## 12. Context Plane

V6.2 moves durable project context out of chat history and into a dedicated Project Context Plane.

### Context Manifest

`context-manifest.json` answers:

```text
Where is the authoritative Requirement?
Where is the Decision?
Where is Execution State?
Where is Semantic Impact?
Where is Policy?
Where is Verification?
Which hash/snapshot does each source belong to?
```

It is an index, not another Source of Truth.

### Context Pack

A Context Pack answers:

> What should this role, in this stage, actually see?

Supported roles:

```text
planner
implementer
reviewer
verifier
debugger
resume
```

Supported stages:

```text
bootstrap
planning
implementation
review
verification
resume
```

An Implementer pack emphasizes:

```text
Requirement
Current Task
Execution State
Semantic Impact
Exact Engineering Policies
Relevant Verification Requirements
```

A Reviewer pack emphasizes:

```text
Requirement / AC
Diff / Semantic Impact
Policy Violations
Review Requirements
Verification Plan
```

A Verifier pack emphasizes:

```text
Acceptance Criteria
Execution Snapshot
Quality Gates
Policy Gates
Review Result
Fresh Verification
```

### Build the Context Plane

```bash
python scripts/context_plane.py build \
  --repo . \
  --state .orchestrator/execution-state.yaml \
  --sdd-provider openspec \
  --sdd-ref openspec/changes/login-reporting \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --role implementer \
  --stage implementation
```

Validate freshness:

```bash
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json
```

Context Packs are bound to:

```text
source hashes
execution revision
analysis snapshot
policy snapshot
```

When an important source changes, the old pack is no longer considered current truth.

### Context Budget

The Context Plane does not dump the full CBM graph, complete execution history, or the entire ECC rule corpus into the prompt.

Principle:

```text
Graph is storage, not prompt.
```

Mandatory context is never silently dropped because of budget pressure. Lower-relevance material moves to lazy retrieval.

---

## 12.5 V6.4 Session Bootstrap / Handoff Context

V6.4 prevents new sessions, new agents, and compact/resume events from rebuilding project truth from chat history.

```text
SDD / Decision / State / Semantic Impact / Policy / Evidence
                         │
                         ▼
                  Session Bootstrap
                         │
              JSON canonical artifact
                         │
                         ▼
               compact Markdown prompt
                         │
                         ▼
                       Agent
```

Bootstrap modes:

```text
fresh_project  → no State yet; discovery/intake only
session_resume → durable State exists; resume current task/cursor/next action
agent_handoff  → fresh handoff adds previous-task summary, risks, and transfer action
```

Default files:

```text
.orchestrator/session/
├── session-bootstrap.json
├── session-bootstrap.md
├── latest-handoff.json
├── latest-handoff.md
└── handoff-<id>.json/.md
```

A handoff cannot manufacture task completion from casual natural-language claims. Only an explicit handoff operation may populate `completed_task`. Handoffs bind to Work Item, Execution/Analysis snapshots, and a Requirement/Decision/Policy/Evidence authority fingerprint; material authority changes make old handoffs stale.

See `references/session-context.md`.

---

## 13. Execution State Manager

V5 provides durable execution state and answers:

```text
Where are we now?
Who is working on it?
Is it blocked?
What happens next?
Which gates have passed?
How does a new agent resume?
```

### Canonical State

The state model uses:

```text
Phase × Status + Blocked Modifier
```

Phases:

```text
discovery
specification
design
planning
implementation
review
verification
release
closed
```

Statuses:

```text
pending
ready
in_progress
completed
failed
cancelled
```

Blocked is represented separately:

```yaml
phase: implementation
status: in_progress
blocked: true
```

This preserves the phase in which the work is blocked.

### Authority Modes

#### native

Appropriate for systems such as BMAD that already own execution state.

The native tool owns:

```text
phase / status / progress
```

The Orchestrator augments it with:

```text
blockers
assignments
gates
evidence
verification
context refs
```

#### hybrid

Appropriate for OpenSpec.

OpenSpec owns:

```text
proposal / spec / design / tasks readiness
```

The Orchestrator owns:

```text
implementation / review / verification runtime state
```

#### orchestrator

When no native state system exists:

```text
.orchestrator/execution-state.yaml
```

becomes the execution-state authority.

### Optimistic Revision

Execution State uses a revision to prevent multi-agent last-write-wins behavior.

```text
Agent A reads revision 12
Agent B reads revision 12

A update -> revision 13
B update expected 12 -> conflict
```

Concurrent state mutations should always include the expected revision.

### Append-only History

Default history:

```text
.orchestrator/execution-history.jsonl
```

It records events such as:

```text
STATE_INITIALIZED
TRANSITION
BLOCKED
UNBLOCKED
QUALITY_GATE_RECORDED
REVIEW_RECORDED
VERIFICATION_RECORDED
...
```

History should be appended, not rewritten.

---

## 14. Host Enforcement (V6.3 Runtime + V6.4 Session Context)

V6.3 adds a shared Runtime Enforcement Kernel; V6.4 adds Session Bootstrap/Handoff on the same lifecycle events:

```text
Host Event
  ↓
Enforcement Kernel
  ↓
Decision / State / Context / Policy
  ↓
allow | deny | context | stale
```

All host adapters are thin event translators. Governance rules are not copied into each host.

### Claude Code

Template:

```text
hosts/claude-code/hooks.template.json
```

Primary events:

```text
SessionStart
UserPromptSubmit
PreToolUse
PostToolUse
SubagentStart
SubagentStop
Stop
```

### Codex

Template:

```text
hosts/codex/hooks.template.json
```

The primary event set is similar to Claude Code.

Project-level hooks are runtime guardrails. Final safety still comes from V5 plus CI.

### Pi

Template:

```text
hosts/pi/coding-orchestrator.template.ts
```

Primary events:

```text
session_start
before_agent_start
tool_call
tool_result
agent_end
```

Pi's `tool_call` can block mutations before execution. `agent_end` is best treated as a best-effort completion continuation point. Final DONE authority remains with State / CI.

---

## 15. Runtime Enforcement Rules

### Session Start

Run:

```text
detect project
  ↓
load state
  ↓
check context freshness
  ↓
load current Context Pack
  ↓
inject compact context
```

Do not inject complete history, the full CBM graph, or the full policy corpus.

### Pre Tool

Normalize the tool payload, then call the shared Action Guard. `read` and document/
governance `prepare` actions remain available; `mutate_code` requires current analysis,
ready SDD, no active blocker, the implementation phase, and a writable role. The
tracked edit-window exception applies only to code freshness, not changed authority
inputs. See [Action Authorization](references/action-authorization.md) for the exact
contract and diagnostic command; adapters must not recreate these checks.

### Post Tool

After a material mutation:

```text
semantic_fresh = false
context_fresh = false
verification.fresh = false
```

Lightweight policy feedback may also run here.

### Stop

Normal intermediate responses are not blocked.

The Completion Contract is checked only when the agent explicitly claims something such as:

```text
Done
Finished
Ready to merge
```

or the state is already in a verification / release / closed completion context.

---

## 16. Dirty Window

V6.3 does not rerun the entire heavyweight analysis stack after every single line edit.

After a mutation, the system enters a Dirty Window:

```text
Implementation
   ↓
edit
   ↓
edit
   ↓
edit
```

Continuous implementation edits may proceed.

However, the first material mutation immediately invalidates:

```text
Semantic Impact
Context freshness
Policy proof
Final Verification
```

Before transitioning to:

```text
review
verification
closed
```

the State Guard requires renewed evidence:

```text
Semantic Intake
CBM refresh
Policy routing/evaluation
Decision reassessment
Context refresh
Verification
```

The design avoids both extremes:

```text
Extreme A: run PIT/CBM/full tests after every line change
Extreme B: make many changes and declare DONE using stale evidence
```

---

## 17. Enforcement Configuration

Starter configuration:

```text
examples/enforcement.yaml
```

Example:

```yaml
version: 1
enabled: true
post_mutation_policy_feedback: true
completion_claim_only: true
max_stop_blocks_per_session: 3

```

After host adapters are installed, a project may maintain:

```text
.orchestrator/enforcement.yaml
```

Permission conditions are defined by the shared Action Guard. Legacy `require_*`,
phase/path override, and `mode` settings no longer weaken those conditions.
`enabled: false` disables the host hook only; state/CLI checks still apply.
The retry budget limits automatic continuation, never closure requirements.

---

## 18. Recommended Spring Boot Setup

A typical Spring Boot project may use:

```text
web/controller
    ↓
service
    ↓
dao/repository
```

Use three enforcement layers.

### Context Layer

Before an agent edits a controller, inject into the Context Pack:

```text
ARCH-SPRING-LAYER-001
web may depend on service, not dao
```

### Runtime Layer

After code is written, the V6 Policy Check detects direct cross-layer dependencies quickly.

### CI Layer

ArchUnit executes the repository-level architecture gate.

Recommended relationship:

```text
Prompt / Context
→ Prevent forgetting

V6 Hook / Policy Check
→ Fast feedback

ArchUnit / CI
→ Final proof
```

---

## 19. Verification Planner

The Verification Planner combines Semantic Impact, Engineering Policy, and project quality policy to determine what must be verified.

Typical derivations:

```text
changed executable symbol
→ targeted unit/component checks

runtime boundary
→ integration checks

public contract
→ contract / compatibility checks

async boundary
→ retry / timing / failure checks

security Work Fact
→ security regression checks

data migration
→ migration / rollback checks
```

Existing REQUIRED gates cannot be downgraded by the planner.

For example, if the project requires:

```text
Semgrep REQUIRED
PIT mutation >= 80% REQUIRED
```

those gates remain mandatory even when the selected flow is FAST.

---

## 20. Recommended Project Configuration

See:

```text
examples/orchestrator-config.yaml
```

It covers:

```text
Evidence
CBM
Engineering Policy
Context Plane
Decision Engine
SDD
Execution State
Execution Discipline
Quality Gates
```

Copy the example into your own project configuration and replace real commands and thresholds. Do not leave placeholders such as:

```text
<project-test-command>
<project-archunit-test-command>
```

in production repositories.

---

## 21. Recommended Daily Development Workflow

### New Feature

```text
1. Create or locate the SDD Change / Story
2. Initialize or resume Execution State
3. Run Semantic Intake
4. Let the Decision Engine determine Flow
5. Let the Policy Router select engineering constraints
6. Generate Planner / Implementer Context Packs
7. Transition → implementation
8. Develop under Host Hook enforcement
9. Complete one traceable slice within the Dirty Window
10. Re-run Semantic Intake
11. Transition → review
12. Run Review / Policy / Quality Gates
13. Transition → verification
14. Produce Fresh Verification
15. Transition → closed
```

### Clear Bug Fix

```text
Bug
 ↓
Work Facts
 ↓
FAST / STANDARD
 ↓
Systematic Debugging
 ↓
Targeted Fix
 ↓
Relevant Tests
 ↓
Semantic Reanalysis
 ↓
Verification
```

Unknown failures should enter root-cause debugging rather than speculative patching.

### Resume

When a new agent takes over:

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  resume
```

Then generate a Context Pack with:

```text
role = resume
stage = resume
```

instead of asking the new agent to rescan an entire chat history.

---

## 22. Recommended `.orchestrator/` Layout

A real project should typically maintain:

```text
.orchestrator/
├── policies/
│   ├── manifest.yaml
│   ├── common-engineering.yaml
│   └── spring-boot-layered.yaml
│
├── intake/
│   ├── work-facts.draft.json
│   ├── work-facts.resolved.json
│   ├── semantic-impact.json
│   ├── policy-plan.json
│   ├── policy-evaluation.json
│   ├── verification-plan.json
│   ├── decision.json
│   ├── context-manifest.json
│   └── context-pack-*.json
│
├── evidence/
│   └── ...
│
├── execution-state.yaml
├── execution-history.jsonl
└── enforcement.yaml
```

Exact filenames may vary with pipeline parameters, but keep these responsibility boundaries stable.

---

## 23. Source of Truth Rules

Avoid dual authorities by preserving the following precedence.

### Requirement Authority

```text
Existing SDD
> Explicit project configuration
> Generic fallback
```

### Execution State Authority

```text
Native SDD State
> Project-defined State
> Orchestrator-owned State
```

### Engineering Policy Authority

```text
Project Policy
> Framework / Language local pack
> External guidance such as ECC
```

### Semantic Structure Authority

```text
CBM structural evidence
```

CBM still does not override Requirement, Policy, or Decision authority.

### Completion Authority

```text
V5 Transition Guards
+
Required CI/Gates
+
Fresh Verification
```

A host agent is not Completion Authority.

---

## 24. Host Capability Differences

The three hosts are not identical. See:

```text
references/host-capabilities.yaml
```

Design rule:

```text
A weaker host capability
!=
a weaker governance requirement
```

Instead:

```text
Host Guard weaker
→ State / CI Guard stronger
```

Do not remove final completion gates merely because a host lacks a hard Stop blocker.

---

## 25. FAQ

### Q1: Can the system be used without CBM?

Parts of V3/V4/V5, Policy, and Context remain usable, but V6 Semantic Impact fails closed by default.

For real code changes, do not interpret "CBM unavailable" as "low risk".

Fixtures may be used for tests.

### Q2: Does every requirement need OpenSpec or BMAD?

No.

TRIVIAL and FAST work can use Generic or lightweight SDD, while still obeying project Engineering Policy and required Verification.

### Q3: Why not put every rule into the prompt?

Because rule volume creates context pollution.

This project uses:

```text
Manifest
→ Relevant Summary
→ Exact Applicable Rules
→ Machine Enforcement
```

instead of loading the entire rule corpus at once.

### Q4: Why can an external ECC rule not directly BLOCK work?

Because updates to an external rule source must not silently change project governance behavior.

A rule must be explicitly promoted into Project Policy before it can become a MUST or Gate.

### Q5: Why does Semantic Impact become stale after code changes?

Because the previous CBM graph, Context Pack, and Verification refer to an older snapshot.

New code must re-prove its impact and verification state.

### Q6: Why do hooks not run the complete test suite after every edit?

Synchronous hooks must stay cheap enough to preserve development usability.

Heavy proof belongs at slice boundaries, phase transitions, and final gates.

### Q7: Can an agent mark the state DONE by itself?

It cannot bypass Transition Guards.

Even if a native SDD reports `done`, required gates and Fresh Verification may still be incomplete:

```text
Native Done != Governance Done
```

### Q8: Is a full Multi-Agent Scheduler already included?

No.

V6.5 already contains foundations for multi-agent work, including revisions, assignments, role-aware context, reviewer/verifier write separation, and subagent hooks, but the complete V7 scheduler is not part of this version.

---

## 26. Tests

Run the full suite:

```bash
python3 -m unittest discover \
  -s tests \
  -p 'test_*.py'
```

Current V6.5 package:

```text
177 tests
```

Coverage includes:

```text
Decision Engine
Evidence Fact Extraction
Execution State
CBM Semantic Impact
Engineering Policy
Context Plane
Session Bootstrap / Handoff
Host Enforcement
Project Activation
```

Pressure scenarios:

```text
evals/pressure-scenarios.md
```

---

## 27. Troubleshooting

### Code mutation is denied

Check:

```text
1. Does execution-state.yaml exist?
2. Is the Decision CLASSIFIED?
3. Is the current phase implementation?
4. Is the Context Manifest stale?
5. Is the current role allowed to modify production code?
```

### Cannot transition to Review

Common causes:

```text
implementation tasks incomplete
active blocker
semantic evidence stale
policy evidence stale
```

After code changes, Semantic Intake usually needs to be rerun.

### Cannot transition to CLOSED

Check:

```text
Acceptance Criteria
Required Review
Blocking Findings
Required Quality Gates
Required Policy Gates
Final Verification
Verification Freshness
Execution Snapshot Match
```

### Context Pack is stale

Run:

```bash
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json
```

Then rebuild the Context Plane.

### Policy conflict

Project Policy does not allow an existing MUST to be silently downgraded to SHOULD/PREFER through a higher-priority configuration layer.

If `ILLEGAL_DOWNGRADE` occurs, resolve the policy authority conflict rather than skipping the check.

---

## 28. Security and Governance Boundaries

V6.4 uses defense in depth. It does not assume that any single hook is impossible to bypass.

Recommended chain:

```text
Layer 1  Context / Prompt
         Tell the agent the correct constraints early

Layer 2  Host Hook / Extension
         Block obviously invalid actions in real time

Layer 3  Execution State Guard
         Prevent illegal phase transitions

Layer 4  CI / Git / Quality Gate
         Repository-level machine proof

Layer 5  Merge / Branch Protection
         Final delivery boundary
```

Do not treat the V6.4 host adapter as the only security mechanism.

---

## 29. Extending a New SDD

Implement the contract described in:

```text
references/adapter-contract.md
```

Preserve the boundary:

```text
SDD owns What / Why
Orchestrator owns execution governance
```

Do not fully duplicate existing SDD artifacts into Orchestrator-owned state.

---

## 30. Extending a New Code Intelligence Provider

CBM is currently the only provider.

A future provider should implement:

```text
scripts/code_intelligence_provider.py
```

and emit Canonical Semantic Impact.

The Decision Engine must not depend directly on provider-private structures.

```text
Provider
  ↓
Adapter
  ↓
Canonical Evidence
  ↓
Work Facts
```

---

## 31. Extending a New Host

When adding a host adapter:

1. Read `references/host-enforcement.md`.
2. Describe host capabilities in `references/host-capabilities.yaml`.
3. Convert host events into Canonical Enforcement Events.
4. Call `scripts/enforcement_kernel.py`.
5. Do not duplicate Decision / Policy / State rules in the host adapter.
6. Where the host cannot hard-block an action, rely on V5/CI final gates.

Correct relationship:

```text
New Host
   ↓
Thin Adapter
   ↓
Shared Enforcement Kernel
```

not:

```text
New Host
   ↓
Reimplement the Orchestrator
```

---

## 32. Where Superpowers Fits

Superpowers does not decide whether a requirement is FAST or DEEP. It constrains how an agent performs the work.

Typical routing:

```text
Ambiguity
→ brainstorming

Planning required
→ writing-plans

Behavior change
→ test-driven-development

Unknown failure
→ systematic-debugging

Independent tasks
→ parallel/subagent disciplines

Implementation complete
→ requesting-code-review

Before DONE
→ verification-before-completion
```

Therefore:

```text
SDD
→ What / Why

Decision Engine
→ How much process

Engineering Policy
→ What is allowed

Superpowers
→ How to work

State + Gates
→ Can it be done
```

---

## 33. Current V6.5 Boundaries

V6.5 closes the main coding-governance loop, Session Context, and project bootstrap/unified-entry-point gap, but it is not:

- a Jira/Linear replacement
- a CI/CD replacement
- a CodeQL/SonarQube replacement
- a complete Multi-Agent Scheduler
- an organization-level governance dashboard
- a complete code knowledge-graph implementation

It is better understood as a **control plane and protocol layer** between those systems.

Possible future directions include:

```text
Real Project Validation / Calibration
Golden Cases
Spring-specific semantic enrichment
Multi-Agent Scheduling
Organization-level Governance Control Plane
```

Before adding more features, validate the system on real repositories: false blocks, missed blocks, CBM accuracy, Context Pack quality, and Verification Recall.

---

## 34. Recommended Real-project Validation Metrics

For a Spring Boot repository, prepare Golden Cases covering:

```text
local change
REST API change
service / dao change
cross-module change
Feign cross-service call
Kafka producer/consumer
Spring Security / token validation
DB migration
public DTO / SDK contract
bug fix
large refactor
```

Measure:

```text
Impact Recall
Impact Precision
Flow Accuracy
Verification Recall
Policy Violation Recall
False Blocking Rate
Context Pack Relevance
Context Token Cost
```

Prioritize:

```text
Verification Recall
Policy MUST Recall
High-risk Impact Recall
```

It is usually better to run one extra relevant check than to miss a critical production risk.

---

## 35. Core Command Reference

```bash
# Detect state Provider
python scripts/state_provider_detector.py .

# Install starter policy
python scripts/policy_bootstrap.py --repo .

# Decision classification
python scripts/decision_engine.py work-facts.json --strict-evidence --pretty

# Full V6 semantic intake
python scripts/semantic_intake_pipeline.py \
  --repo . \
  --request-file request.md \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --context-role implementer \
  --context-stage implementation

# Resume
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  resume

# Context freshness
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json

# Session bootstrap
python scripts/session_context.py --repo . bootstrap --role implementer --stdout markdown

# Task/role handoff
python scripts/session_context.py --repo . handoff --from-role implementer --to-role reviewer --summary "Current task completed"

# Host adapter dry-run
python scripts/install_host_adapter.py --repo . --host all

# Host adapter install
python scripts/install_host_adapter.py --repo . --host all --apply

# Tests
python3 -m unittest discover -s tests -p 'test_*.py'
```

---

## 36. Core Philosophy

Coding Agent Orchestrator is not designed to make AI coding more bureaucratic. Its goal is to match process depth to real engineering risk.

```text
Simple requirement
→ Simple process

Complex requirement
→ Deeper planning

Ambiguous requirement
→ Clarify first

High-risk requirement
→ Stronger verification

Code changes
→ Previous evidence becomes stale

Project rules
→ Machine-enforceable

Context
→ Precisely projected by role and stage

Agent claims completion
→ Fresh Evidence required
```

The target outcome is:

> **Let the agent see the right context at the right time, work at the right process depth, and declare completion only when sufficient evidence exists.**

---

## 37. On-demand Reading and Context Budget

`SKILL.md` is a runtime router, not a preload list for the documentation corpus. **Do not read every reference sequentially.** Load only the 1–2 documents needed for the current stage or decision.

Typical routing:

```text
New project          → references/project-bootstrap.md
Workflow / SDD       → orchestration-model.md / adapter-contract.md
Facts / classification → fact-extractor.md / decision-engine.md
Code impact          → semantic-impact-engine.md
Engineering rules    → engineering-policy-layer.md
Execution state      → execution-state-manager.md
Role context         → context-plane.md
Cold start / handoff → session-context.md
Host hooks           → host-enforcement.md
Review / verify      → quality-gates.md
```

Run the context-footprint guard with:

```bash
python scripts/context_footprint_check.py --repo .
```

The default limits keep `SKILL.md` from growing back into a handbook. The full reference corpus may remain rich because it is lazy-retrieved knowledge, not always-on prompt context.

For a specific SDD:

```text
references/adapters-openspec.md
references/adapters-bmad.md
references/adapters-generic-sdd.md
```

For ECC integration:

```text
references/ecc-rules-integration.md
```

For Superpowers routing:

```text
references/superpowers-policy.md
```

---

## License / Third-party

This package contains integration designs for third-party tools and rule sources. See:

```text
THIRD_PARTY_NOTICES.md
```

External rule sources are never automatically promoted to project governance authority.
