# CAO Python module boundaries

`scripts/cao.py` is the source-checkout entry point and a compatibility facade.
It reexports the original function objects, constants, and policy helpers so
existing `from scripts import cao` callers continue to work. Argument parsing,
dispatch, restart checks, and error rendering live in `cao_cli.py`. The console
entry point remains `scripts.cao:main`.

New implementation code should import the owning module directly. Do not add
business logic to the facade, import it from implementation modules, forward
its mutable globals, or load source text into a shared namespace.

## Responsibilities

All filenames below are relative to `scripts/`.

| Module | Responsibility |
| --- | --- |
| `cao_defaults.py` | Shared configuration, provider catalog, and execution profiles |
| `cao_runtime.py` | JSON/configuration helpers, repository discovery, identifiers, transaction decorators |
| `cao_snapshot.py` | Source, rule, and verification fingerprints |
| `cao_work_store.py` | Authoritative Work Items, revision checks, index recovery, audit event outbox |
| `cao_requirements.py` | Requirement discovery, evidence, numbered documents, requirement commands |
| `cao_provider_workflows.py` | Provider-owned paths, native Skill discovery, entrypoint validation |
| `cao_language.py` | User language and native provider language files |
| `cao_instructions.py` | Installed resources, project instructions, host adapter setup |
| `cao_providers.py` | Provider discovery, prerequisites, installation, host detection and integration selection |
| `cao_path.py` | Execution profiles, risk minimums, assessment and gate commands |
| `cao_plans.py` | Scoped native task plans and aggregate completion observations |
| `cao_artifacts.py` | Artifact references, confirmation, refresh, and commands |
| `cao_native_sync.py` | Native state reconciliation, route approval, implementation guards |
| `cao_completion.py` | Evidence freshness, acceptance inputs, completion decisions and recovery instructions |
| `cao_guidance.py` | Ordered prerequisites, next actions, rendered context, resume payloads |
| `cao_sessions.py` | Session observation, activity, registration, handoff history |
| `cao_bootstrap.py` | Initialization, bootstrap, host setup commands |
| `cao_workflow_commands.py` | Native workflow selection/execution/completion, spec binding, review commands |
| `cao_verification.py` | Verification configuration, lock-free execution, guarded result commit |
| `cao_work_commands.py` | Work start/resume/checkpoint/block/reopen/done and inspection commands |
| `cao_tool_policy.py` | Tool effects, shell classification, protected paths, setup write exemptions |
| `cao_host_events.py` | Host lifecycle events, session observation, mutation decisions, hook output |
| `cao_cli.py` | CLI argument tree, handler dispatch, restart barrier, error output |

The existing `route_input_commands.py` and `sdd_acceptance_commands.py` import
specific domain functions instead of receiving the entire CLI module as a
service locator. The Dashboard also imports the owners directly.

## Dependencies and state

Configuration/defaults and persistence remain below orchestration. Higher-level
command handlers compose the domain modules. Some feedback paths (for example,
plan observation invoking native reconciliation, or native synchronization
requesting current guidance) use explicit function-local imports to avoid
import-time cycles. Fresh-process import tests ensure no module relies on the
facade having been imported first.

The extraction preserves command options, defaults, handler selection, JSON
responses, decorators, state paths, verification anchors, and audit durability.
State mutations still go through `cao_work_store.py` and the existing
`state_store.repo_transaction` lock. Verification and native external commands
retain their original prepare/execute/commit lock boundaries.

The shell policy must identify `scripts/cao.py` as the trusted entry script.
Moving `_readonly_tokens` must not grant that exemption to its new module.
Source resource lookup continues to resolve the same repository root, and the
wheel includes all modules through the existing `scripts` package.

## Tests and mocking

Patch the dependency where the caller looks it up. For example, inject a runner
failure with `patch("scripts.cao_verification.run_script", ...)`, or a persistence
failure with `patch("scripts.cao_work_store.save_json", ...)`. Replacing a facade
export does not replace the owning module's globals. For a function-local import,
patch the exporting domain module. Prefer temporary environment fixtures when
host detection must agree across multiple consumers.

`tests/test_cao_module_boundaries.py` checks function-object compatibility,
independent imports, the facade boundary, and trusted script classification.
`tests/installed_package_smoke.py` imports the responsibility modules from the
installed wheel and exercises provider/host flows outside the checkout.


Runtime recording is separate from `cao_runtime.py` (configuration/general CLI
utilities). `runtime_store.py` owns safe fact persistence and quotas;
`runtime_logs.py` owns incremental redaction and bounded streams;
`runtime_execution.py` integrates controlled execution and owner locks;
`runtime_host.py` handles best-effort observations and call routing;
`runtime_queries.py`, `runtime_retention.py`, and `runtime_commands.py` implement
queries, disposable projection rebuilding, explicit pruning, and CLI registration.
None decides gate acceptance. See [runtime-records.md](../references/runtime-records.md).

## Native completion lifecycle

`lifecycle_contract.py` owns contract-2 native evidence binding, pre-verification
process checks and completion precedence. `native_completion.py` aggregates
provider completion; `native_reports.py` reads original result declarations and
runner logs. `evidence_domains.py` owns exact invalidation manifests and declared
native dependencies; `completion_repairs.py` bounds retries of unchanged failed
bindings. `lifecycle_migration.py` owns read-only preflight, checkpoint migration
and rollback. `lifecycle_guard.py` protects persisted authority, interrupted writes
and recovery with quarantine. Legacy executor logic stays in `sdd_acceptance_*`;
new Work never requires that executor. CLI, host and dashboard consume the shared
lifecycle policy.
