# Changelog

## 6.5 — Project Bootstrap & Unified CLI

- Refactored `SKILL.md` to be version-neutral: capability names now replace historical V3/V4/V5/V6.x labels; release history remains in this changelog and `MANIFEST.json`.
- Added `coding-orchestrator` / `coding-orchestrator.cmd` and `scripts/coding_orchestrator.py` as the stable human/agent front controller.
- Added `init`, `discover`, `doctor`, `status`, `intake`, `resume`, `verify`, and `host install` commands.
- Added conservative repository discovery for technology, SDD/state authority, host markers, CBM availability, and architecture evidence.
- Added safe, idempotent project bootstrap that creates governance infrastructure without inventing an active work item.
- Added first-intake creation of the canonical work item; the Decision Engine remains responsible for the real Flow classification.
- Added fail-safe handling for OpenSpec/BMAD authority ambiguity and CI non-interactive failure semantics.
- Added evidence-gated Spring layered-policy auto-enablement; Spring Boot identity alone no longer implies `web -> service -> dao`.
- Added safe host auto-install semantics: repository-local host markers are high-confidence, binary-only detection is advisory.
- Added bootstrap report, project config generation, Doctor health checks, closed-work archival, and unified resume/verification readiness views.
- Added V6.5 bootstrap/CLI tests.
- Refactored `SKILL.md` into a compact runtime router with an explicit Lazy Reference Loading Contract; detailed CLI/examples remain in on-demand references.
- Added `scripts/context_footprint_check.py` and regression guards for Skill size, version-neutrality, and lazy-reference semantics.
- Updated README guidance to avoid sequential eager-loading of the reference corpus.
- Full regression suite is 115 tests.

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
