# Session Bootstrap & Handoff Context

Session Context provides resumable, prompt-facing projections on top of the Context Plane and Host Enforcement layer.

## Why this exists

A new coding-agent session should not reconstruct project reality from chat history. It should start from the current SDD authority, Decision result, Canonical Execution State, Semantic Impact, Engineering Policy, Context Manifest, and Evidence. Likewise, a role/session handoff should not depend on a long natural-language transcript.

Session Context therefore defines two derived artifacts:

- **Session Bootstrap**: compact cold-start/resume context generated from current project truth.
- **Task Handoff**: explicit role/session transfer artifact generated at a controlled task boundary.

Neither artifact is authoritative. They are views over existing authorities.

## Format contract

The design deliberately uses different formats for different jobs:

| Surface | Format | Reason |
|---|---|---|
| Canonical bootstrap/handoff artifact | JSON | deterministic parsing, hashing, schema validation, cross-host portability |
| Human/project configuration | YAML | readable project configuration and policy tuning |
| Prompt injection | Markdown | high-density human/model-readable projection with minimal syntax noise |
| XML | not used by default | higher token/markup overhead without a current enforcement or schema advantage |

Do not make Markdown the source of truth. `session-bootstrap.md` and `handoff-*.md` are regenerated projections of JSON/state/evidence.

## Artifacts

Default runtime paths:

```text
.orchestrator/
├── session-context.yaml
└── session/
    ├── session-bootstrap.json
    ├── session-bootstrap.md
    ├── latest-handoff.json
    ├── latest-handoff.md
    ├── handoff-<id>.json
    └── handoff-<id>.md
```

Generated session artifacts are excluded from the material working-tree fingerprint. Creating a bootstrap must not make semantic/context evidence stale.

## Session types

### `fresh_project`

Used when Canonical Execution State does not exist yet. Its status distinguishes two cases:

- `UNBOOTSTRAPPED`: project config is missing; Safe Auto bootstrap is the next action.
- `READY_FOR_INTAKE`: project bootstrap is complete but no real work item exists yet.

Production-code mutation remains blocked until intake/classification creates and prepares canonical execution state.

### `session_resume`

Used when durable State exists and no fresh handoff applies. It projects the current work item, Flow, phase, task cursor, blockers, gates, snapshots, Context Manifest/Pack pointers, recent state events, and next action.

**Resume never re-intakes.** A `READY_FOR_WORK` resume means the work item is already classified: continue with the projected phase/task. A rephrased request (“继续”, “continue”, “resume”) is not a requirement change, so re-running `intake` on it is wrong; intake keeps the existing requirement revision when the requirement source is unchanged. Re-run `intake` only when the requirement source itself changed, and then `--revise-current --confirm-reset` is required, because revising discards in-flight implementation state (phase returns to discovery; readiness, gates, and progress are cleared).

### `agent_handoff`

Used when a fresh `latest-handoff.json` is available. It adds the explicit previous-task summary, known risks, assumptions, and handoff next action.

## Handoff truth discipline

A handoff MUST NOT be synthesized from a casual assistant claim such as “looks done”. Generate it explicitly when a task/role/session boundary is actually reached.

A handoff contains:

- from/to role;
- work item and completed task identity;
- concise implementation summary;
- changed files;
- assumptions and known risks;
- pending gates/review/verification;
- next action;
- evidence references;
- execution/analysis/context snapshot bindings.

Handoff freshness is invalidated by a changed execution or analysis snapshot. A mere state revision advancement does not automatically invalidate it, because assignments/metadata can legitimately advance without changing the code/analysis snapshot.

## Injection lifecycle

```text
SessionStart / first prompt / resume / compact
        ↓
Bootstrap Guard (Safe Auto init once when needed)
        ↓
Build Session Bootstrap JSON
        ↓
Render compact Markdown
        ↓
Inject once
        ↓
Normal turns use delta-only state/freshness reminders
```

For subagents, generate a role-aware bootstrap. For Pi, the extension stores the SessionStart bootstrap and injects it once at `before_agent_start`; subsequent turns receive delta-only context.

## Context budget

Default bootstrap prompt budget is 7000 characters. Keep bootstrap content high density:

- current work item/Flow/phase/task/next action;
- active blockers;
- fresh previous handoff when available;
- applicable project MUST policy IDs;
- review/verification/gate summary;
- Context Manifest and role-pack pointers plus compact selected-source summaries.

Never inline the full CBM graph, complete execution history, all policy bodies, or old debug logs. Those remain lazy-retrieved via the Context Plane.

## CLI

Generate/persist a bootstrap:

```bash
python scripts/session_context.py --repo . bootstrap \
  --role implementer \
  --host claude-code \
  --stdout markdown
```

Create an explicit handoff:

```bash
python scripts/session_context.py --repo . handoff \
  --from-role implementer \
  --to-role reviewer \
  --completed-task-id T4.2 \
  --completed-task-title "Implement async report delivery" \
  --summary "Implemented async report delivery" \
  --summary "Added retry handling" \
  --risk "Integration verification is still pending" \
  --next-action "review_task:T4.2"
```

Validate the latest handoff:

```bash
python scripts/session_context.py --repo . validate-handoff
```

## Host integration

Host adapters consume Session Context as follows:

- Claude Code `SessionStart` runs the Bootstrap Guard when needed, then receives Session Bootstrap Markdown.
- Codex `SessionStart` runs the Bootstrap Guard when needed, then receives Session Bootstrap Markdown.
- Pi `session_start` captures the bootstrap and injects it once on the next `before_agent_start`.
- If first bootstrap occurs on `UserPromptSubmit`, that same turn receives the full `READY_FOR_INTAKE` bootstrap; later turns use delta-only context to avoid repeated prompt inflation.

## Invariants

1. Session context is a **projection**, never authority.
2. `Execution State > Handoff > Conversation history` for current progress truth.
3. `SDD > Bootstrap` for requirement truth.
4. `Evidence > Handoff claims` for test/review/verification truth.
5. Unknown/missing previous-task completion MUST NOT be converted into “completed”.
6. Stale handoffs/bootstrap/context packs MUST be regenerated or ignored.
7. Session artifacts MUST NOT dirty the repository semantic snapshot merely by being generated.
