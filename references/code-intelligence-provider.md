# Code Intelligence Provider Contract

## Purpose

Semantic Impact uses external structural analyzers behind a stable provider boundary. A provider may
answer **what code is connected to this change**; it must not decide **how much process**
the change requires.

Current implementation: `codebase-memory-mcp` only.

## Required provider surface

```text
capabilities()
health()
collect_impact(repo, scope, base_branch, depth, refresh_index)
```

The canonical result is `semantic-impact.json` and MUST contain:

- provider identity/version and structural-only authority declaration;
- snapshot bound to current source bytes + provider payload;
- changed symbols/files;
- impacted symbols / blast radius;
- observed relationship types;
- module/service/project boundary evidence when available;
- public-contract signals;
- provider coverage metadata;
- provider opinions separated from accepted evidence.

## Authority boundary

Provider-owned:

```text
symbol discovery
call/dependency relationships
cross-service/async/data-flow graph edges
blast-radius evidence
index coverage metadata
```

Orchestrator-owned:

```text
Work Facts
risk classification
TRIVIAL / FAST / STANDARD / DEEP
quality policy
execution state
DONE
```

A provider risk label MUST NOT be copied to `risk.*`, used as a flow override, or treated
as a quality gate result.

## Evidence behavior

Positive structural evidence may resolve or raise scope/verification facts. Provider
absence or failure never means `false`, low impact, or local-only behavior.

When a richer semantic impact supersedes a narrower changed-file count, the previous
provenance is retained with strength `superseded`; it is not silently deleted.

## Capability evolution

The current implementation intentionally has one provider implementation. Future providers may implement this
contract without changing Decision Engine, SDD adapters, or Execution State Manager.
