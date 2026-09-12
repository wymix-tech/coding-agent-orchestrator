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

The adapter invokes CBM CLI mode with JSON input. Before impact collection it refreshes
the repository index by default so newly-created files are not silently absent.

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

## Failure policy

The semantic-impact pipeline is fail-closed for the provider by default:

```text
CBM unavailable -> PROVIDER_UNAVAILABLE
```

It does not convert the missing graph into a low-impact result. A caller may explicitly
use `--allow-cbm-unavailable`; then mechanically extracted Work Facts remain conservative and unresolved semantic
facts must still be resolved through ordinary evidence.

## Known limitation boundary

CBM is an evidence source, not an oracle. Parser/index gaps, unresolved dynamic calls,
and framework-specific relationships can exist. Therefore:

- never infer negative facts from a missing edge;
- retain index-coverage information;
- allow source inspection / semantic resolution to add or challenge facts;
- let Decision Engine handle evidence conflicts rather than picking the more convenient
  answer.
