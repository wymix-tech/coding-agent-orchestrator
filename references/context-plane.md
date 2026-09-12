# Context Plane

## Purpose

The Context Plane turns the growing set of authoritative project artifacts into a small, current, role-aware context surface for coding agents.

It answers two different questions:

- **Context Manifest:** where is the current truth, who owns it, and which snapshots/revisions bind it?
- **Context Pack:** what does this role need to see for this stage right now?

The Context Plane is a projection layer. It MUST NOT become a new requirement, policy, state, or evidence authority.

## Inputs

A Context Manifest may index:

- authoritative SDD/request artifact;
- evidence-backed Work Facts;
- Decision Engine result;
- CBM Semantic Impact;
- Engineering Policy plan/context/evaluation;
- Verification Plan;
- Canonical Execution State and append-only history;
- quality/review/final-verification status from execution state.

Each file-backed source records a SHA-256 digest. The manifest also records analysis, execution, semantic, policy, and verification snapshots where available.

## Output layout

Recommended project layout:

```text
.orchestrator/intake/
  context-manifest.json
  context-pack.implementer.implementation.json
  context-pack.implementer.implementation.md
```

Generate future role packs at the stage boundary rather than pre-generating them at intake. A reviewer pack created before implementation completes is already conceptually stale even if its files have not changed.

## Context tiers

- **Tier 0:** current task authority/state and exact constraints. Normally load.
- **Tier 1:** direct semantic/decision/verification context. Load when relevant to the role/stage.
- **Tier 2:** supporting manifests/configuration. Usually reference, not inline.
- **Tier 3:** history and broad corpora. Lazy retrieval only.

The full CBM graph, complete execution history, and complete external rule corpus MUST remain external unless the current step explicitly needs them.

## Canonical role projections

### Planner / planning

Prioritize requirement authority, Work Facts, Decision trace, semantic impact, policy plan, and current execution state. Verification detail is supporting context.

### Implementer / implementation

Prioritize requirement authority, current execution state/next action, semantic impact, exact Engineering Policy context, Decision Flow, and Verification Plan.

### Reviewer / review

Prioritize requirement/acceptance context, current diff/semantic impact, policy evaluation, state, Verification Plan, and Decision rationale. Execution history is secondary and should be pulled only when needed.

### Verifier / verification

Prioritize acceptance/requirement authority, current execution snapshot, Verification Plan, Policy Evaluation, quality gates, review outcome, and final-verification freshness.

### Debugger

Prioritize current failure/state, semantic impact, Work Facts, relevant policy, and verification/test evidence. Do not automatically load historical speculation.

### Resume

Prioritize Canonical Execution State, current requirement authority, Decision, semantic snapshot, and the legal next action. Repository state outranks conversation memory.

## Freshness

A Context Pack is valid only for the snapshot metadata in its `valid_for` block. Regenerate when any material source changes, especially:

- execution revision/next action changes;
- analysis or semantic snapshot changes;
- policy snapshot changes;
- requirement/SDD artifact changes;
- verification becomes stale;
- a source content hash no longer matches the manifest.

Use:

```bash
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json
```

`STALE` means regenerate the manifest/pack before relying on it.

## Context budgets

Budget selection by metadata/items, not by dumping all available text. Mandatory context is never silently dropped to satisfy a budget. Lower-relevance sources become deferred lazy retrieval entries.

The default pack deliberately contains compact summaries and references. Agents may retrieve referenced source ranges/symbols when the current action requires deeper context.

## CLI

Build/refresh a manifest and current pack:

```bash
python scripts/context_plane.py build \
  --repo . \
  --intake-dir .orchestrator/intake \
  --state .orchestrator/execution-state.yaml \
  --sdd-provider openspec \
  --sdd-ref openspec/changes/my-change \
  --role implementer \
  --stage implementation
```

Project a new role/stage pack from an already-current manifest:

```bash
python scripts/context_plane.py pack \
  --manifest .orchestrator/intake/context-manifest.json \
  --role reviewer \
  --stage review \
  --output .orchestrator/intake/context-pack.reviewer.review.json \
  --markdown-output .orchestrator/intake/context-pack.reviewer.review.md
```

Before a new agent/session starts, prefer `build` so source hashes and current state are refreshed.

## Authority rule

The pack may say "read this" but it may never say "this projection overrides its source". Resolve conflicts at the owning authority:

- SDD owns requirement intent/acceptance behavior;
- Engineering Policy owns allowed implementation constraints;
- Decision Engine owns Flow classification;
- Canonical Execution State owns project runtime state by field authority;
- CBM owns only structural graph evidence;
- fresh verification evidence owns proof of completion.
