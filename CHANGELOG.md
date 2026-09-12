# Changelog

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
