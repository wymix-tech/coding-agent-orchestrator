# Evidence-backed Fact Extractor

## Purpose

The Fact Extractor reduces subjective Work Profile classification by separating **observation**, **semantic resolution**, and **decision computation**.

```text
Request + Repository + Git + SDD + CI
                  ↓
        Mechanical Collectors
                  ↓
      work-facts.draft.json
       ├─ proven facts
       ├─ provenance
       ├─ observations
       ├─ heuristic hints
       └─ resolution_queue
                  ↓
         Semantic Resolver
                  ↓
     work-facts.resolved.json
                  ↓
       Strict Evidence Lint
                  ↓
          Decision Engine
```

The extractor MUST be conservative. A false negative in risk detection is more dangerous than an unresolved fact, so absence of a signal never means `false`.

## Three evidence classes

Final facts may use only:

- `authoritative`: explicit user requirement, active SDD artifact, repository policy, protocol/schema contract.
- `observed`: directly observed Git/repository/CI/code fact.
- `derived`: deterministic derivation from authoritative/observed evidence.

`heuristic` is **hint-only**. Path names, keywords, inferred intent, naming conventions, or fuzzy search results can direct inspection but cannot finalize a Work Fact.

## Phase 1: mechanical collection

Run:

```bash
python scripts/fact_extractor.py \
  --repo . \
  --request-file request.txt \
  --base-ref origin/main \
  --output .orchestrator/intake/work-facts.draft.json
```

The reference collector currently inspects:

- Git working tree / optional base diff;
- changed production/config/schema files;
- nearest build/package module roots;
- deployable markers when mechanically resolvable;
- OpenAPI / Swagger / Protobuf / GraphQL contract artifacts;
- migration files and explicitly destructive SQL operations;
- production infrastructure paths with strong evidence;
- OpenSpec/BMAD markers, candidate change/story/PRD/architecture artifacts, and changed SDD paths;
- repository instruction files such as `AGENTS.md` / `CONTRIBUTING.md`;
- common CI/build signals for test, lint, Semgrep, coverage, and mutation testing;
- request/path keyword **hints** for areas such as auth, security, payment, async, and performance.

Only high-confidence mechanical signals populate facts. Everything else remains `null` and is placed in `resolution_queue`.

## Phase 2: semantic resolution

The Coding Agent resolves queued facts by inspecting authoritative requirements, active SDD artifacts, architecture boundaries, changed symbols, dependencies, and relevant code.

Each resolution is structured:

```json
{
  "path": "risk.authn_authz",
  "value": true,
  "source_type": "code_inspection",
  "source": "src/auth/token_validator.py#validate",
  "evidence": "The changed branch decides JWT expiry validity used by login authorization.",
  "strength": "observed",
  "resolver": "coding-agent"
}
```

For a non-authoritative `false` claim, include a bounded negative proof:

```json
{
  "path": "scope.external_consumers",
  "value": false,
  "source_type": "contract_inventory",
  "source": "openspec/changes/refresh-token/spec.md + dependency inventory",
  "evidence": "Contract is internal and all consumers are in this repository.",
  "strength": "derived",
  "negative_proof": "Reviewed all declared consumers in the active spec and repository dependency graph; none cross the repository/team boundary."
}
```

Apply resolutions with:

```bash
python scripts/fact_resolver.py \
  .orchestrator/intake/work-facts.draft.json \
  fact-resolutions.json \
  --output .orchestrator/intake/work-facts.resolved.json
```

The resolver rejects heuristic evidence as final evidence, rejects non-authoritative `false` facts without `negative_proof`, and validates Work Fact types. Boolean facts must be JSON booleans; structural counts must be non-negative integers. Strings such as `"false"` are invalid.

## Phase 3: strict classification

Run the Decision Engine in strict mode:

```bash
python scripts/decision_engine.py \
  .orchestrator/intake/work-facts.resolved.json \
  --strict-evidence --pretty
```

Strict mode requires every resolved required fact to have matching `authoritative`, `observed`, or `derived` provenance. Missing/weak evidence returns `NEEDS_EVIDENCE` rather than silently classifying low.

For convenience, `scripts/intake_pipeline.py` chains collection, optional resolution, and strict classification.

## Resolution precedence

When sources disagree, use this precedence unless repository policy explicitly defines a stronger authority:

1. Current explicit user requirement for this change.
2. Active authoritative SDD artifact governing the change.
3. Repository policy / public protocol / schema contract.
4. Direct code/Git/CI observation.
5. Deterministic derivation from the above.
6. Heuristic hints, which never resolve a conflict.

If two accepted sources conflict, do not let "last writer wins" decide the fact. Within one extraction snapshot, the resolver refuses to overwrite a mechanically proven fact with a different value and strict evidence validation reports conflicting accepted provenance. Reconcile the sources or create a new evidence snapshot when the underlying repository/request actually changed.

## Negative-proof rule

`false` is a claim, not a default. It must come from either:

- an authoritative statement; or
- a bounded inspection that explains what was checked and why the checked scope is sufficient.

Examples of invalid reasoning:

```text
"No auth file matched"        ⇒ authn_authz=false       # invalid
"No public API keyword found" ⇒ external_consumers=false # invalid
"No test failed"              ⇒ deterministic_local=true # invalid
```

## Reassessment

Do not mutate an old fact merely because implementation moved on. Create a new extraction snapshot after material scope/evidence changes, re-resolve only changed/invalidated facts, and rerun the same Decision Engine. Snapshot IDs include request/base/head plus changed-file content hashes, so another edit to the same filename produces a different snapshot. Record old/new snapshot IDs in the Evidence Ledger.

## Agent convergence contract

Two agents given the same repository snapshot, request, authoritative SDD artifacts, and evidence policy should converge because:

- collectors are deterministic;
- heuristic hints cannot become final facts by themselves;
- semantic resolutions must name evidence;
- negative facts require bounded proof;
- scores and flow are computed by one Decision Engine;
- manual score/flow overrides are forbidden.

Remaining variance is localized to semantic fact resolution and becomes visible in the provenance ledger instead of being hidden inside a subjective score.

## V6 semantic enrichment

V4 file/Git extraction remains the first evidence pass. V6 then runs CBM semantic-impact
analysis before semantic resolution. The impact mapper may raise structural scope estimates
when graph evidence proves a wider blast radius. It must preserve superseded provenance and
must not use missing graph edges as negative proof.
