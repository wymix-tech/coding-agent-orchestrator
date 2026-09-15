# V6.5 Stability Repair Report

This report is the durable repair checkpoint for the T1–T8 stability work. The repaired source tree, schemas, documentation, and tests are packaged together; this report distinguishes local verification from external integrations that were unavailable in the repair environment.

## Phase A — verifiable evidence, real native projection, migratable identity — 2026-09-15

Phase A turns a rule-complete workflow that could *record* evidence into one where evidence can
be *checked*, native state is projected from real content, and requirement identity can migrate.
It is delivered as stacked PRs on an isolated branch (`PR0` contracts → `PR1` evidence → `PR2`
native projection → `PR3` identity/migration → `PR3-follow-up`), none of which is merged to
`main` here.

| Area | What changed | Where it is proven |
|---|---|---|
| T1 evidence | `scripts/evidence_provenance.py`: `kind` × `validation_status` × `outcome`; `required_dependencies()` is derived by the verifier and each dependency is resolved against the object that really exists (path/tree/object revision, report, approval, native state); immutable content-addressed records with a rebuildable index | `tests/test_evidence_provenance.py`, `tests/test_evidence_enforcement.py` |
| T2 native projection | `scripts/native_state_parser.py`: one read → digest + parse → `NativeProjection` (internal type, no import entry) or a structured diagnostic; `native-sync`, `sync-native`, `transition`, `progress` share it; `--native-confirmed` is a compatibility switch, not trust. Governance `phase/status/completion_record` still require `action_guard.authorize(...)` | `tests/test_native_state_parser.py`, `tests/test_native_entrypoint_equivalence.py`, `tests/test_governed_transition_boundaries.py` |
| T3 identity/migration | `requirement_identity` `identity_version=2` quadruple, deterministic multi-file digests, structured boundaries for mixed-content files, legacy marking instead of "assume unchanged", preview → backup → atomic replace → idempotent re-entry → interrupt recovery | `tests/test_requirement_identity_v2.py`, `tests/test_requirement_revision_confirm.py`, `tests/test_migration_transactions.py`, `tests/test_explicit_input_not_swallowed.py` |
| Follow-up | dependencies bind real objects, tests build real material first (`tests/evidence_factory.py`), readiness is re-checked at use time, `coding-orchestrator evidence show\|verify\|index` and `coding-orchestrator native bind` | `tests/evidence_factory.py` and the suites above |

Verification: `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null python3 -m unittest discover -s
tests -p 'test_*.py'` → **411 tests / OK**; `python3 scripts/context_footprint_check.py --repo .
--json` → **PASS** (SKILL.md 103 lines / 7400 bytes / 918 words). Per-PR results, behaviour changes
in existing tests, and the four minimal rebuild paths are in
`repair/phase-a-verification-summary.json`.

Not run here: real host integration (Pi / Codex / Claude Code / Windows) and a real multi-file CBM
or host directory migration — both are recorded as `NOT_RUN` rather than claimed.

## Git commit/content snapshot follow-up — 2026-09-14

The previous fingerprint mixed HEAD identity with material file content, and the
post-tool hook marked every conservatively classified mutation dirty. An unchanged
commit could therefore discard fresh gate/review/verification evidence.

This revision separates content identity from Git trace metadata across the
repository fingerprint, Work Facts, and CBM normalization. It also preserves
already analyzed deletions through index cleanup and skips no-op code-mutation
dirty marking after checking authority and comparison inputs. Actual edits,
including edits performed by a Git pre-commit hook, remain invalidating.

Explicit comparison bases bind both the base tree and merge-base trees. A changed
or unavailable basis is rejected separately as `COMPARISON_BASE_CHANGED`. With no
explicit base, a newly requested intake still describes outstanding uncommitted
changes; committing can legitimately change that newly collected scope.

Validation status: 21 new real Git regression tests are included, for an expected
284-test suite. Execution is pending GitHub Actions because the local execution
environment is unavailable. Do not treat the earlier 263-test result below as
verification of this revision.

The fingerprint format is versioned. Existing active work must refresh analysis,
context, and affected gates/review/final verification once after upgrading.

## Runtime/governance review follow-up — 2026-09-14

This follow-up corrects the six issues reviewed in commit `6fec51a` and the four
CBM-dependent errors in its newly added retry tests.

| Reviewed issue | Correction and regression evidence |
|---|---|
| Shell governance-path aliases | Normalize literal paths against tool cwd, preserve symlink resolution before collapsing `..`, and protect descendants during parent removal/moves. A ready implementer is still denied the configuration-write variants that previously disabled enforcement. |
| Policy sources outside the default directory | Include the configured manifest and enabled packs plus the currently analyzed manifest/source references, including paths under `docs/` and outside the repository. Direct writes, patches, moves and shell writes reject these targets. |
| Windows recovery entry blocked by missing state | Share the packaged front-controller paths with tool classification and recognize the `.cmd` entry. Before a work item exists, packaged recovery remains available while similarly named foreign scripts and composed mutation commands remain guarded. |
| Old/duplicate host hooks on upgrade | Compare installed adapters with the current runtime/repo, replace managed registrations, preserve unrelated hooks/settings, and keep repeated reconciliation idempotent. Cover Claude Code, Codex and Pi migration. |
| Host path serialization | Parse JSON before substituting arguments; use POSIX shell quoting, encoded argument transport for Windows commands, and JSON-compatible TS literals. Special-character argument round trips execute against a local kernel stub. Explicit `--repo` also remains authoritative inside a parent Git tree. |
| Filled scaffold overwritten by another intake | Create scaffolds exclusively at new paths. Both intake pipelines preserve filled files across repeated runs, including file aliases, and return the actual remaining-facts template path. |

Verification on Linux with Python 3.12.14:

```text
python3 -m unittest discover -s tests -v
Ran 263 tests in 19.346s
OK

Focused review regressions: 14/14 PASS
Retry/scaffold module: 10/10 PASS, fixture-only CBM calls
Skill quick validation: PASS
Context footprint: PASS — 104 lines, 7497 bytes, 940 words
git diff --check: PASS
```

The retry tests now supply `--cbm-fixture`, reject any attempt to instantiate the real
provider, and check the intake exit/status before inspecting warning fields. Provider
incident semantics are preserved. Windows tokenization and encoded command transport
are exercised locally; native Windows host applications and a live CBM release binary
were not exercised in this follow-up. Literal target classification remains a host
guardrail, with the existing state/verification gates and sandbox limitations retained.

## Baseline and preserved design

The repair preserves `scripts/action_guard.py` as the single action authorization evaluator consumed by CLI/state/host/start-resume paths, preserves shared material classification in `repository_snapshot.py`, preserves tool-input normalization in `tool_actions.py`, and keeps native SDD/project Policy authoritative. No quality gate is fabricated as passed.

## T1–T8 status

| Task | Status | Implemented / verified |
|---|---|---|
| T1 State/history transaction boundary | **Fixed + locally verified** | Cross-process lock, optimistic revision check inside the lock, recoverable transaction journal, unique temp files, idempotent history event recovery, dead-owner reclaim, deterministic live-lock timeout. Tests cover two-process revision competition and crash injection after journal/state/history stages. |
| T2 Work identity/revision/artifact isolation | **Fixed + locally verified** | Stable requirement ID separated from content revision; explicit revision invalidates derived readiness/gates/review/verification; active A rejects explicit B before mutating A; immutable `.orchestrator/work-items/<work>/revisions/<revision>/runs/<run>/` analysis; canonical attach is optimistic on starting state revision. |
| T3 Policy semantic snapshot/source tracking | **Fixed + locally verified** | Snapshot binds effective rule content/level/scope/required/command/merge result plus referenced source content. External guidance remains non-blocking unless explicitly promoted. |
| T4 Explicit SDD persistence | **Fixed + locally verified** | Existing config is field-merged for explicit SDD selection; unrelated policy/host/custom config is preserved; repeated selection is idempotent; active native ownership conflict remains explicit and the reported authority matches persisted state. |
| T5 Historical requirement dedupe | **Fixed + locally verified** | Stable requirement identity + revision registry/history prevents A/B completed work reopening after restart; same text under different identities is not merged; legacy ambiguous data is handled conservatively. |
| T6 BMAD work discovery | **Fixed + locally verified** | Discovery follows detected/configured native sprint state and `_bmad-output/implementation-artifacts`; installation templates/examples are excluded; completed work is filtered; unknown format or missing pending story returns sourced action-required diagnostics. |
| T7 Verification obligation lifecycle | **Fixed + locally verified** | Stable obligations are separate from results, scoped to work/revision; plan shrink does not silently delete required obligations; disposition needs reason + authority + evidence; disposition is not reported as `passed`. |
| T8 Cross-module/CBM/host acceptance | **Local contracts + lifecycle verified; live external integration unavailable** | End-to-end empty repo through evidence, implementation, dirty/reanalysis, review, real local verification command, required gates, final verification, close, and next-requirement routing is covered. `check`, PreTool, transition, verify, Stop/start-resume use shared authorization. CBM timeout/nonzero/stdout-stderr/invalid-JSON/index/detect-changes contracts are tested. |

## Historical completion vs current close readiness

A successful local Governance close records a `completion_record`. Once recorded, later repository or next-requirement changes do not resurrect that completed work item. Current `governance_close_ready` is still recomputed from live evidence for work that is attempting to close. A native SDD projecting `closed/completed` without a Governance completion record is not treated as Governance Done. Legacy orchestrator-owned `closed/completed` state is preserved as historical completion for migration compatibility.

## Verification evidence

### Standard regression

```text
python3 -m unittest discover -s tests -v
Ran 211 tests in 72.236s
OK
```

A second discover run using an instrumented `unittest` result also completed all 211 tests with zero failures/errors, no multiprocessing children, and only `MainThread` remaining at exit.

### Context footprint

```text
python3 scripts/context_footprint_check.py --repo . --json
status: PASS
SKILL.md: 100 lines, 7490 bytes, 942 words
```

### Shared Action Guard rejection smoke

A freshly bootstrapped project with no active work item was used intentionally. These are expected denials, not failed tests:

```text
python3 ./coding-orchestrator --repo <temporary-project> --json check --action mutate_code
exit 1 -> STATE_MISSING -> run_intake

python3 ./coding-orchestrator --repo <temporary-project> --json check --action advance --phase review
exit 1 -> STATE_MISSING -> run_intake

python3 ./coding-orchestrator --repo <temporary-project> --json check --action close
exit 1 -> STATE_MISSING -> run_intake
```

The end-to-end T8 regression supplies the complementary allowed path after real lifecycle evidence is established.

## CBM compatibility

The adapter uses bounded execution (`ORCHESTRATOR_CBM_TIMEOUT_SECONDS`, default 120 seconds). Schema flags are used only when the installed tool's help advertises them; otherwise the adapter uses documented stdin JSON. A runtime/indexing failure, timeout, or success-with-invalid-JSON is terminal and is not retried using another syntax, preventing duplicate side effects and error masking. Compatibility fallback is only allowed for invocation-shape incompatibility, and raw mode is only used when capability probing proves it exists.

The CLI contract was subsequently checked against upstream CBM commit `339b3f4097aa6ede22fc382ab7fd320d93c498b8`: `detect_changes.scope` is `files|impact`, `check_index_coverage` requires `paths` or `scopes`, and `index_repository` does not declare a `format` input. Empty BMAD-only greenfield projects now bypass CBM as not applicable instead of failing Git revision resolution and opening a false provider incident.

Tests cover timeout, invalid JSON, nonzero stdout/stderr preservation, `index_repository`, `detect_changes`, modern stdin JSON, conditional inline fallback, and conditional raw fallback.

## External integration status

Repair environment:

```text
Debian GNU/Linux 13 (trixie), Linux x86_64
Python 3.13.5
Git 2.47.3
```

The following executables were **not available** in this repair environment: `codebase-memory-mcp`, `pi`, `codex`, `claude`, `claude-code`. Therefore no claim of live CBM/host integration success is made. Offline protocol/host fixtures are tested. Re-run the following on a target workstation before calling those integrations live-verified:

```bash
codebase-memory-mcp cli --help
codebase-memory-mcp cli list_projects
codebase-memory-mcp cli index_repository --repo-path /path/to/project
codebase-memory-mcp cli list_projects --format json --detail stats

# Then from each actual host:
./.agents/skills/coding-agent-orchestrator/coding-orchestrator --repo . doctor
./.agents/skills/coding-agent-orchestrator/coding-orchestrator --repo . start
```

## Migration

- Existing active work without a reliable stable requirement identity is not auto-merged or declared complete. Re-intake or explicitly migrate the active requirement.
- Requirement revision changes invalidate derived acceptance/task/review/verification evidence and require fresh evidence.
- Legacy orchestrator-owned `closed/completed` work remains historical completion; native-only closed projection without a Governance completion record remains not done.
- Existing verification plans are projected into stable obligations. Required obligations cannot disappear by omitting a plan row or setting `required=false`; use an auditable disposition when genuinely no longer applicable.
- Policy/source content changes intentionally change policy/context bindings and require re-analysis/verification as applicable.
- Generated work-item, registry, host-adapter, session, and Orchestrator-managed activation artifacts remain excluded from business material snapshots; user-authored project instruction content remains material.

## Durable repair baseline

The packaged ZIP containing this report is the repair baseline for subsequent work. Do not resume from an older V6.5 ZIP if a newer repair-baseline/latest archive is available.

## CBM provider resilience follow-up

A later field report showed that a locally installed CBM could still be reported unavailable and cause repeated evidence/re-analysis attempts. The follow-up repair adds three independent protections:

1. **Discovery** — resolve CBM from explicit env configuration and common install paths in addition to the Agent process PATH.
2. **Protocol** — when the installed tool help advertises schema CLI formatting, call `cli <tool> --... --format json`; this prevents compact/tree default output from being treated as malformed provider JSON.
3. **Circuit breaker** — persist the first operational failure under `.orchestrator/providers/`; the same binary/version is not automatically invoked again until explicit reset/retry, while a changed provider identity receives one fresh attempt. Start/resume/action authorization route the condition to `repair_cbm_provider`, not `resolve_fact_evidence` or repeated semantic intake.

Recovery commands on the target workstation:

```bash
coding-orchestrator --repo . --json provider status codebase-memory-mcp
codebase-memory-mcp cli index_repository --repo-path /absolute/path/to/repo
coding-orchestrator --repo . provider reset codebase-memory-mcp
coding-orchestrator --repo . start
```

The CBM upstream source and exact tool schemas were verified at commit `339b3f4`; a native build was attempted but failed during linking because two vendored tree-sitter symbols were unresolved, so execution of the target release binary is still required on the workstation. The repaired Orchestrator suite passes **218/218** with `python -m unittest discover -s tests -v`; the focused CBM/semantic/start/bootstrap groups pass **53/53**.
