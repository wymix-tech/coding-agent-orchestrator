# V6.5 Stability Repair Report

This report is the durable repair checkpoint for the T1–T8 stability work. The repaired source tree, schemas, documentation, and tests are packaged together; this report distinguishes local verification from external integrations that were unavailable in the repair environment.

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

The adapter uses bounded execution (`ORCHESTRATOR_CBM_TIMEOUT_SECONDS`, default 120 seconds). Current stdin-JSON invocation is tried first. A runtime/indexing failure, timeout, or success-with-invalid-JSON is terminal and is not retried using another syntax, preventing duplicate side effects and error masking. Compatibility fallback is only allowed for invocation-shape incompatibility, and raw mode is only used when capability probing proves it exists.

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
codebase-memory-mcp cli index_repository --repo-path /absolute/path/to/repo --format json
coding-orchestrator --repo . provider reset codebase-memory-mcp
coding-orchestrator --repo . start
```

The repair container does not contain the real CBM binary, so live-provider success is still **not verified here**. Current affected regression groups total 116 passing tests; the repository discovers 216 tests. A fresh one-process standard discover was not claimed for this follow-up because the local runner repeatedly stalled and one diagnostic run terminated in the Python/PyYAML stack; the prior persisted repair baseline had 211/211 full-suite PASS.
