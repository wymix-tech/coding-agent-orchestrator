# Changelog

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
