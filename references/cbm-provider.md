# CBM Provider

## Role

`codebase-memory-mcp` is the Primary Code Intelligence Provider. The adapter uses CBM
as a local structural-analysis backend and normalizes its output into the Orchestrator's
canonical semantic-impact model.

## Why CBM

The Semantic Impact capability focuses on change impact, not generic knowledge retrieval. CBM directly exposes:

```text
Git diff -> changed symbols -> impacted callers / blast radius
call/dependency graph
cross-service HTTP relationships
async relationships
data-flow relationships
index coverage
```

This is closer to the required structural evidence than implementing a new parser/call-graph stack.

## Invocation

Reference implementation:

```bash
python scripts/cbm_provider.py health
python scripts/cbm_provider.py capabilities
python scripts/cbm_provider.py collect --repo . --scope all --depth 3 \
  --output .orchestrator/intake/semantic-impact.json
```

The adapter first inspects the installed tool-specific CLI help. On schema-generated CLI builds
that advertise `--format`, it invokes `cli <tool> --... --format json`; this avoids mistaking
CBM's compact/tree human output for a provider failure. Older builds fall back to JSON on stdin,
then legacy inline JSON only after a proven invocation-shape error. `--raw` is attempted only
when help proves that the installed build supports it. Runtime/indexing failures are terminal and
are never overwritten by compatibility fallbacks. Each invocation is bounded by
`ORCHESTRATOR_CBM_TIMEOUT_SECONDS` (default 120 seconds).

Binary discovery does not rely only on the Agent process PATH. It also honors
`ORCHESTRATOR_CBM_BINARY` / `CBM_BINARY` and checks common installer paths such as
`~/.local/bin/codebase-memory-mcp`, `/opt/homebrew/bin`, and `/usr/local/bin`.
Before impact collection the adapter refreshes the repository index by default so newly-created
files are not silently absent.

For branch impact:

```bash
python scripts/cbm_provider.py collect \
  --repo . --scope branch --base-branch main --depth 3
```

## Trust model

CBM structural evidence can be accepted as `observed`/`derived` depending on the claim.
CBM's own risk classification is retained under:

```text
provider_opinion.risk_labels
```

with:

```text
authoritative_for_flow: false
```

It MUST NOT populate Orchestrator risk dimensions.

## Coverage and freshness

`check_index_coverage` is collected when available. Missing/partial coverage weakens the
ability to prove **absence**, but does not invalidate positive observed graph relations.

The semantic snapshot binds:

- Git HEAD;
- current bytes of changed files visible in the CBM result;
- normalized CBM payload;
- provider version/project.

Changing source bytes therefore changes the impact snapshot.

## CLI compatibility diagnostics

`python scripts/cbm_provider.py health` reports the detected CBM version and CLI protocol.
`coding-orchestrator doctor` also performs a non-mutating `list_projects` smoke probe.

For manual verification on current CBM builds:

```bash
codebase-memory-mcp cli index_repository --repo-path /absolute/path/to/repo --format json
```

Do not treat allocator/version-cohort informational lines on stderr as the failure by themselves.
When a command fails, preserve the first real tool/indexing error; do not replace it with a
secondary compatibility error such as `unknown tool: --raw`.

## Failure policy

The semantic-impact pipeline is fail-closed for the provider by default:

```text
CBM first operational failure -> PROVIDER_UNAVAILABLE + persisted provider incident
repeat automatic attempt       -> PROVIDER_BLOCKED / repair_cbm_provider
```

The incident is persisted at `.orchestrator/providers/codebase-memory-mcp.json`. The same
binary/version is not automatically retried again, so provider failure cannot become an endless
`NEEDS_EVIDENCE -> semantic intake -> provider failure` loop. After fixing the local provider,
run `coding-orchestrator provider reset codebase-memory-mcp`; installing a different CBM binary
or version also permits one fresh attempt automatically. `coding-orchestrator provider status
codebase-memory-mcp` exposes the resolved binary, version, CLI probe, and current incident.

The graph is still fail-closed: provider failure never becomes evidence of low impact. A caller
may explicitly use `--allow-cbm-unavailable`, but unresolved semantic facts remain conservative.

## Known limitation boundary

CBM is an evidence source, not an oracle. Parser/index gaps, unresolved dynamic calls,
and framework-specific relationships can exist. Therefore:

- never infer negative facts from a missing edge;
- retain index-coverage information;
- allow source inspection / semantic resolution to add or challenge facts;
- let Decision Engine handle evidence conflicts rather than picking the more convenient
  answer.
