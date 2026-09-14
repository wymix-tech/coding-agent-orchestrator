# Changelog

## 6.5 revision — Real CBM CLI Contract and Empty Greenfield Bootstrap

- Verified the CLI/tool schemas against upstream CBM source commit `339b3f4097aa6ede22fc382ab7fd320d93c498b8` (2026-09-13).
- Corrected `detect_changes`: CBM accepts `scope=files|impact`; Orchestrator scopes are no longer passed through.
- Corrected `check_index_coverage`: every call now includes the concrete evidence `paths` required by CBM.
- Corrected the index smoke command: current `index_repository` does not declare `--format`; JSON formatting is used on tools that advertise it.
- Added empty-greenfield handling. BMAD/Orchestrator/Agent installation metadata is excluded from product-code detection, so an empty project does not invoke CBM, require a Git `HEAD`, or open a false provider incident.
- For product files before the first commit, indexing still runs and all product files are conservatively treated as new until CBM `detect_changes` has a valid Git baseline.
- Added regressions for real scope mapping, exact-path coverage, and a BMAD-only empty project.

## 6.5 revision — CBM Provider Resilience and Retry Circuit

- Modern CBM schema CLI builds now use explicit `--format json`, preventing compact/tree stdout from being misclassified as a provider outage.
- CBM binary discovery now honors `ORCHESTRATOR_CBM_BINARY` / `CBM_BINARY` and common installer paths (`~/.local/bin`, Homebrew, `/usr/local/bin`) when an Agent process inherits a reduced PATH.
- Added durable `.orchestrator/providers/codebase-memory-mcp.json` provider incidents. The first operational failure remains fail-closed; identical automatic retries are circuit-broken and route to `repair_cbm_provider` instead of repeatedly requesting unrelated Work Fact evidence.
- Added `coding-orchestrator provider status/reset codebase-memory-mcp`; a changed CBM binary/version automatically receives one fresh attempt.
- Provider incident runtime files are excluded from material repository fingerprints, so recording an outage cannot make semantic evidence stale by itself.
- Added provider-incident schema and five resilience regression tests. Affected CBM/Start/CLI/Action/Session/Enforcement regression groups: **116 tests PASS**. The suite now discovers **216 tests**.
- The current repair container did not complete a new one-process standard discover reliably: repeated attempts stalled and one diagnostic run terminated in the local Python/PyYAML stack. The previous persisted baseline remains 211/211 full-suite PASS; this revision records the current targeted validation explicitly rather than claiming 216/216.

## 6.5 revision — Stability and Lifecycle Repair (T1–T8)

- T1: made Execution State + append-only history commits cross-process coordinated and recoverable with a transaction journal; stale revisions conflict explicitly, interrupted commits recover idempotently, stale dead-owner locks are reclaimed, and live-lock timeout is deterministic.
- T2: separated stable requirement/work-item identity from requirement revision; isolated semantic intake into immutable per-work/per-revision run directories; stale concurrent analysis cannot overwrite newer canonical state, and explicit requirement revision invalidates derived acceptance/task/review/verification evidence.
- T3: changed Policy snapshot identity from mostly rule IDs/paths to effective semantic rule content plus referenced policy source hashes, including packs outside the default directory; external guidance remains non-authoritative unless explicitly promoted.
- T4: made explicit `init --sdd ...` resolution persist by field-level merge while preserving unrelated project configuration; active native work ownership conflicts remain explicit instead of silently rewriting authority.
- T5: added stable requirement history/registry semantics so completed A/B work does not reopen after restart; content hashes identify revisions, not requirement identity.
- T6: aligned BMAD Requirement Discovery with detected/configured native sprint state and `_bmad-output` artifacts; templates/install resources are excluded, completed items are filtered, and unknown formats or missing story artifacts produce sourced action-required diagnostics.
- T7: separated Verification Obligations from execution results; required obligations cannot disappear through plan shrinkage or `required=false`, and `superseded` / `not_applicable` / `waived` require auditable reason, authority, and evidence rather than masquerading as passed tests.
- T8: added cross-module authorization, real CLI rejection-path, CBM timeout/JSON/runtime contract, lifecycle, concurrency, and end-to-end close/next-requirement regression coverage. Historical governed completion is now distinct from current close readiness; native SDD closed status alone is not Governance Done.
- Standard regression command `python3 -m unittest discover -s tests -v`: **211 tests pass**. Context Footprint: **100 lines / 7490 bytes / 942 words, PASS**.
- Live CBM/Pi/Codex/Claude Code integration was not executed in the repair environment because those binaries were unavailable; offline provider/host contract fixtures remain covered and `REPAIR_REPORT.md` records reproduction commands and limitations.

## 6.5 revision — Current-Agent Host Auto Selection

- Changed Safe Auto host selection from repository-marker inference to **current Agent runtime detection**.
- Added `scripts/host_runtime.py`: explicit `--host` wins; otherwise nearest recognized process/runtime evidence is used, with `CODEX_THREAD_ID` and `PI_CODING_AGENT` as positive runtime signals.
- Added deterministic Claude Code fallback when the current Agent cannot be identified reliably.
- Added lightweight Host Reconcile for already-bootstrapped projects: switching to Pi/Codex/Claude installs only the missing current-host adapter without rerunning project bootstrap or recreating the active work item.
- Kept `.claude/.codex/.pi`, packaged Skill files, and Orchestrator-managed activation blocks out of material code fingerprints so host integration changes do not invalidate semantic analysis; user-owned AGENTS/CLAUDE content still participates.
- Added current-host/adapter diagnostics to `discover` and `doctor`.
- Added runtime-host selection and host-switch regression coverage.
- Full regression suite: 185 tests pass.

## 6.5 revision — CBM CLI Compatibility

- Hardened the CBM adapter across current/intermediate/legacy CLI generations.
- Prefer `codebase-memory-mcp cli <tool>` with JSON on stdin; fall back only on proven invocation-shape incompatibilities.
- Probe `--raw` support before using it, so real indexing failures are never overwritten by a secondary `unknown tool: --raw` error.
- Added CBM CLI protocol information to provider health and a non-mutating `list_projects` smoke probe to `coding-orchestrator doctor`.
- Added compatibility regression coverage for modern stdin JSON, legacy inline JSON, conditional raw fallback, and preservation of real runtime failures.
- Full merged regression suite: 177 tests pass.

## 6.5 revision — Shared Action Authorization

- Centralized execution, phase advance, release, and close eligibility in `action_guard.py`; state/CLI/host/start adapters share stable reasons and recovery actions.
- Added read-only `check --action` and live material/authority input checks independent of host hook delivery.
- Kept requirement preparation and tracked implementation edit windows usable while denying stale/unclassified advancement.
- Bound gate/review/final-verification evidence to execution and content snapshots; synchronized required impact-plan gates and prohibited result-based gate downgrades.
- Resolved blockers no longer stop resume; optional review findings remain blocking; exhausted Stop retries remain denied.
- Normalized raw patches, moves, shell payloads, and `cmd`/`command`; separated role outcome reporting from global completion.
- Added migration guidance and removed obsolete host permission override settings from starters.
- Added 32 regression tests; 172 tests pass. Earlier release counts below are historical.

## 6.5 — Project Bootstrap & Unified CLI

- Refactored `SKILL.md` to be version-neutral: capability names now replace historical V3/V4/V5/V6.x labels; release history remains in this changelog and `MANIFEST.json`.
- Added `coding-orchestrator` / `coding-orchestrator.cmd` and `scripts/coding_orchestrator.py` as the stable human/agent front controller.
- Added `init`, `discover`, `doctor`, `status`, `intake`, `resume`, `verify`, and `host install` commands.
- Added conservative repository discovery for technology, SDD/state authority, host markers, CBM availability, and architecture evidence.
- Added safe, idempotent project bootstrap that creates governance infrastructure without inventing an active work item.
- Added first-intake creation of the canonical work item; the Decision Engine remains responsible for the real Flow classification.
- Added fail-safe handling for OpenSpec/BMAD authority ambiguity and CI non-interactive failure semantics.
- Added evidence-gated Spring layered-policy auto-enablement; Spring Boot identity alone no longer implies `web -> service -> dao`.
- Added Safe Auto host installation; later V6.5 revisions refined selection to current-Agent runtime detection with Claude Code fallback.
- Added a project Activation Layer: idempotent managed `AGENTS.md` routing, optional `CLAUDE.md` routing, and Pi session-start activation so vague prompts such as `开始`, `继续`, `start`, `continue`, and `resume` can reliably enter the Orchestrator workflow.
- Added a self-bootstrap `Bootstrap Guard`: once the Skill or an installed host lifecycle adapter is activated, missing `.orchestrator/config.yaml` triggers Safe Auto `init` exactly once, then the same user turn continues without requiring a restart.
- Added deterministic `start` intent routing: `开始` / `继续` / `start` / `continue` / `resume` now resolve project state first, resume active work, surface blockers, discover requirements, auto-intake exactly one high-confidence candidate, ask when none exists, and stop for selection when several exist.
- Added conservative Requirement Discovery for selected SDD artifacts and generic requirement/spec/PRD sources; Skill/host/runtime directories and ordinary install READMEs are excluded, and dedicated requirement documents outrank README fallback.
- Split first-project lifecycle state into `UNBOOTSTRAPPED -> READY_FOR_INTAKE -> READY_FOR_WORK`; initialization no longer conflates a valid project with a missing active work item.
- Invalid existing config and unresolved SDD authority remain `ACTION_REQUIRED`; self-bootstrap never overwrites or guesses.
- Activation stubs preserve existing project instructions and remain routing-only; `.orchestrator/`, SDD, Policy, State, and Evidence remain authoritative.
- Renamed documentation so `README.md` is the default English README and `README-zh.md` is the Simplified Chinese README.
- Added bootstrap report, project config generation, Doctor health checks, closed-work archival, and unified resume/verification readiness views.
- Added V6.5 bootstrap/CLI tests.
- Refactored `SKILL.md` into a compact runtime router with an explicit Lazy Reference Loading Contract; detailed CLI/examples remain in on-demand references.
- Added `scripts/context_footprint_check.py` and regression guards for Skill size, version-neutrality, and lazy-reference semantics.
- Updated README guidance to avoid sequential eager-loading of the reference corpus.
- Full regression suite is 140 tests.

## 6.4 — Session Bootstrap & Handoff Context

- Added `scripts/session_context.py` with canonical JSON Session Bootstrap and Task Handoff artifacts plus compact Markdown prompt projections.
- Added YAML session-context configuration and explicit format separation: JSON for machine truth/projections, YAML for human config, Markdown for model injection; XML is not used by default.
- Added `fresh_project`, `session_resume`, and `agent_handoff` bootstrap modes.
- Added explicit handoff freshness binding to work item, execution snapshot, analysis snapshot, and Context snapshot.
- Added latest-handoff persistence without treating casual assistant completion claims as authoritative task completion.
- Updated Claude Code/Codex SessionStart integration and Pi pending-bootstrap injection so the full bootstrap is injected once; subsequent prompt events are delta-only.
- Excluded generated `.orchestrator/session/` artifacts from material working-tree fingerprints to prevent self-induced staleness.
- Added Session Bootstrap / Task Handoff JSON Schemas, examples, documentation, tests, and pressure scenarios.
- Added 13 V6.4 session-context/enforcement tests; full regression suite is 100 tests.

## 6.3 — Host Enforcement Adapters

- Added one host-neutral `scripts/enforcement_kernel.py` instead of duplicating governance logic in each coding agent.
- Added Claude Code and Codex hook templates plus a Pi TypeScript extension adapter.
- Added `scripts/install_host_adapter.py` to merge/install repository-local adapters explicitly.
- Added runtime guards for missing/unclassified state, illegal code mutation phase, reviewer/verifier write separation, stale Context Manifest, and completion claims.
- Added dirty-window semantics: material mutation invalidates semantic/context/final-verification freshness immediately but does not force heavyweight re-analysis after every edit; review/verification/close remain blocked until re-analysis.
- Added V5 execution-state enforcement metadata and transition guards for stale semantic/policy evidence.
- Added external working-tree change detection at the next lifecycle event.
- Added host capability matrix documenting hard/soft enforcement differences and Codex/Pi caveats.
- Added 11 runtime enforcement tests; full regression suite is 87 tests.

## 6.2 — Context Manifest / Role-aware Context Packs

- Added V6 Context Plane to unify SDD/request, Work Facts, Decision, Execution State, Semantic Impact, Engineering Policy, Verification, and Evidence references.
- Added `context-manifest.json` with source hashes, authority metadata, snapshot binding, blockers, and freshness warnings.
- Added role/stage Context Packs for planner, implementer, reviewer, verifier, debugger, and resume workflows.
- Added context budgets that defer low-relevance sources without dropping mandatory authority context.
- Added manifest freshness validation and stale-source detection.
- Added context refs to V5 analysis state and integrated pack generation into the semantic intake pipeline.
- Added Context Manifest/Pack JSON Schemas, examples, documentation, tests, and pressure scenarios.

## 6.1 — Engineering Policy Layer + ECC Rule Source

- Added project-owned Engineering Policy as a first-class V6 context and governance layer.
- Added progressive policy loading: manifest -> planning summaries -> exact implementation rules -> review/verification evidence.
- Added `MUST` / `SHOULD` / `PREFER` levels, precedence, and illegal-downgrade conflict detection.
- Added Spring Boot layered starter policy (`web/controller -> service -> dao/repository`) and lightweight Java import enforcement.
- Added Policy Gate projection into Verification Plan and V5 Execution State.
- Added optional ECC `rules/` adapter as non-authoritative external guidance; no ECC rule bodies are redistributed.
- Added policy bootstrap, compact policy context packs, third-party attribution, and policy tests.

## 6.0 — Semantic Impact & CBM Provider

- Added `CodeIntelligenceProvider` SPI with one implementation: `codebase-memory-mcp`.
- Added CBM health/capabilities/index/`detect_changes` collection and stable semantic-impact normalization.
- Added provider-risk quarantine: CBM risk labels are retained but never authoritative for Work Facts or Flow.
- Added semantic blast-radius mapping into scope/complexity/verification Work Facts with auditable supersession.
- Added evidence-linked Verification Planner.
- Added end-to-end `semantic_intake_pipeline.py`.
- Added Execution State `analysis` attachments for semantic-impact, Work Facts, decision, and verification-plan references.
- Added CBM/provider pressure tests and offline fixtures.

## 5.0 — Execution State Manager

- Added Canonical Execution State with `phase × status` and blocker modifiers.
- Added native/hybrid/orchestrator authority modes for BMAD, OpenSpec, and generic SDD.
- Added transition guards, optimistic revisions, append-only history, resume, native sync, gates, review, and snapshot-bound verification.
- Added native-completion vs governance-completion separation.

## 4.0 — Evidence-backed Fact Extractor

- Added conservative mechanical Work Fact collection, semantic resolver, strict evidence lint, snapshots, and conflict protection.

## 3.0 — Deterministic Decision Engine

- Replaced subjective Work Profile scoring with executable facts, scoring, combination rules, and risk/policy overrides.

## 2.0 — Adaptive Flow

- Added six-dimensional Work Profile, TRIVIAL/FAST/STANDARD/DEEP flow profiles, and deterministic reassessment/escalation concepts.

## 1.0

- Initial SDD adapter + Superpowers + quality-gate orchestration model.
