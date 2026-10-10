# Coding Agent Orchestrator Design

## Product position

Coding Agent Orchestrator (CAO) is a **cross-agent work continuity and execution control plane**.
It is not an SDD framework and not a replacement for Claude Code, Codex, Pi, BMAD, OpenSpec, or CI.

The SDD owns native artifacts and native workflow meaning. CAO owns the execution state needed to
continue a requirement after a session ends, after an Agent changes, or after a new session starts.

## Architectural law

```text
Agent Host
   ↓
Host Adapter
   ↓
CAO Core
   ↓
Work Item / Session / Handoff
   ↓
SDD Provider references native artifacts
```

There is one governance implementation in `scripts/cao.py`, exposed as the standard `cao` console command through `pyproject.toml`. Source-checkout launchers and host adapters delegate to that implementation.

## Core persistent model

### Work Item

One user requirement or engineering request. It records:

```text
status
requirement reference
SDD native reference
current execution step
next action
resume summary
completed/pending notes
important decisions
blockers
verification snapshot
Session runtime association (stored separately)
```

### Session

A temporary Agent runtime attachment. Session records are defined by `protocol/session.schema.json` and stored only under `.orchestrator/sessions/`.

```text
CAO internal session id (stable hash of host + host-native session id)
host / host_session_id
work_item_id
kind: primary | subagent
parent_session_id
status: active | ended
started_at / last_event / last_event_at / ended_at
end_reason / last_handoff_id
```

`last_event_at` records the latest Host or CAO Session event observed by CAO; it is not a heartbeat or proof that the Agent process is alive. `recent` / `stale` is calculated at query time, while lifecycle status remains `active` until an explicit end event is observed.

Session files are the source of truth. The `index.json` and Work Item documents do not duplicate Session arrays. Only records matching the current Session schema are loaded; older Session formats are not migrated.

A Session is disposable. A Work Item is durable. Handoff creation does not itself end a Session; the lifecycle owner decides when to call `end_session`.

### Handoff

The durable bridge from one session to another:

```text
current step
next action
progress summary
completed
pending
decisions
blockers
verification
previous session / host
```

Each Handoff is an immutable point-in-time snapshot stored under
`.orchestrator/handoffs/<work-id>/<handoff-id>.json`. The newest snapshot is derived from that
Work Item's history directory. This history layout is the only supported persistence format;
flat latest-record files and legacy Handoff data are not loaded or migrated.
Use `cao handoff list [--work <work-id>] [--limit N]` to inspect history and
`cao handoff show --id <handoff-id>` to inspect a full record. `cao resume` consumes the latest
record and current Work Item state rather than treating an old Handoff as the sole source of truth.

CAO does not need to own or persist the host's complete chat transcript.

## Repository layout

```text
.orchestrator/
├── config.json
├── index.json
├── work/
│   └── <work-id>.json
├── sessions/
│   └── <session-internal-id>.json
└── handoffs/
    └── <work-id>/
        └── <handoff-id>.json          # immutable history records
```

## State transitions

CAO deliberately uses only four Work Item states:

```text
TODO → ACTIVE → SUSPENDED
             ↓
          COMPLETED
```

Verification is not another long-lived business phase. It is a snapshot-bound property of the Work
Item. This avoids a phase machine becoming a substitute SDD.

## Session inspection

Use `cao sessions list` to inspect recently observed or stale active sessions, and `cao sessions show --id <native-or-internal-id> [--host <host>]` for one session. If a native ID exists under multiple hosts, `--host` disambiguates it. `cao sessions list --stale-after-minutes 30` controls the activity display threshold without changing lifecycle state.

## Resume semantics

### Same Agent, same session

The host may restore its own transcript. CAO reattaches the Session to the Work Item and refreshes the
resume context from durable state.

### New session, same Agent

`cao resume` creates/attaches a new Session and renders the latest Handoff + current Work Item state.

### Different Agent

Exactly the same `cao resume` path is used. No re-intake and no requirement recreation is required.

```text
Claude session A
      ↓
Work Item
      ↓
Handoff
      ↓
Codex session B
      ↓
same Work Item
```

## Next Action

`execution.next_action` is a first-class field. It may be:

```text
agent
verification
sdd
human
none
```

The SDD provider can suggest a native action, but CAO records the resulting next action as execution
continuity, not as an SDD artifact.

## Minimum-safe execution path

CAO chooses the minimum safe process depth independently of any specific SDD. A Work Item receives a
single lightweight assessment with three profiles:

```text
light          clear, local, reversible, low-risk changes
standard       ordinary feature/fix requiring limited analysis/design and review
comprehensive  high-risk, broad, materially ambiguous, or hard-to-reverse changes
```

The Agent supplies the contextual judgment and one short reason. CAO validates the profile and applies
deterministic minimums for a small list of risk flags. Security, data migration, irreversible changes,
cross-system work, and major requirement ambiguity require at least `comprehensive`. External contracts,
multi-module changes, and compatibility risks require at least `standard`. The effective profile and
required gates are persisted under `work.path_assessment` with the current requirement hash.

`major_ambiguity` pauses code mutation until the user clarifies the behavior and the Agent records a
new assessment without that flag. Missing or stale assessments block mutating Host events and prevent
`cao done`. If the requirement changes, the Work Item must follow the established re-anchor procedure
before an assessment can be refreshed.

Each built-in SDD Provider contains lightweight `path_guidance` for `light`, `standard`, and
`comprehensive`. These are provider-specific native-workflow instructions, not a second lifecycle
engine. CAO does not create SDD artifacts, issue a numeric score, scan the repository into a fact model,
or build a provenance graph. Profile selection never waives provider-native prerequisites or configured
Verification scripts.

The CLI is deliberately small:

```bash
cao path assess --profile light --reason "Clear, local, reversible change"
cao path show --work <work-id>
```

## Decisions

Decisions are append-only records intended for future Agents. They are not a knowledge graph.
Typical record:

```json
{
  "decision": "Use Redis for refresh tokens",
  "reason": "Supports revocation",
  "scope": "login"
}
```

## Blockers

Blockers explain why the Work Item cannot make useful progress. A blocker may suspend the active Work
Item so another requirement can become active.

## Verification freshness

Verification stores:

```text
requirement hash
code snapshot
rules snapshot
verification script-set snapshot
command results
overall verification result
```

Verification scripts live in `.orchestrator/config.json` as an ordered list. Each entry has a
`name`, `command`, and optional `required` flag (default `true`). CAO executes the scripts sequentially
and records every result. Project-specific verification logic, thresholds, and report parsing stay
inside the scripts. The script exit-code protocol is `0=pass`, `1=fail`, `2=blocked`, `>=3=error`.
The overall verification passes only when all required scripts return `0`.

`cao done` recomputes the live values. There is no dirty flag to repair and no evidence graph.

## SDD installation gate

Requirement files and an installed SDD are different things. A repository can contain `requirements/`
or other planning documents while having no SDD runtime installed. CAO therefore treats SDD availability
as a startup gate for requirement-driven coding.

Bootstrap follows this decision path:

```text
bootstrap
   ↓
project SDD detected?
   ├─ no → return `sdd_select` → user chooses provider
   │                              ↓
   │                       `cao sdd install`
   │                              ↓
   │                         install + init
   │                              ↓
   └─ yes ←──────────────── bootstrap again
          ↓
   Work Item selection / resume
```

The built-in installation catalog currently provides OpenSpec, BMad Method, and GitHub Spec Kit. The installer owns
provider-specific prerequisites, package installation, and native project initialization. CAO only
records the selected provider after the provider is detected in the project. This keeps installation
policy outside the Work Item state machine.

## SDD compatibility

CAO never requires a universal SDD artifact schema. A Work Item stores references to native artifacts:

```text
provider
native_ref
artifacts[]
```

Provider operations are intentionally small and optional:

```text
detect
discover
goto/inspect
artifacts
next action
mutation (optional)
```

BMAD can keep PRD / Architecture / Story / Ticket artifacts. OpenSpec can keep proposal / spec / design /
tasks. CAO only needs enough references to know which native work item the execution state belongs to.

## Host adapters

Adapters translate native lifecycle events into `host-event` calls. They do not implement Work Item,
Verification, Decision, or Completion rules themselves.

## Agent loading and authority

CAO separates **loading priority** from **instruction authority**. The host's project bootstrap is
used as the always-on trigger; the CAO Skill is loaded when relevant; the SDD provider and task/domain
skills are loaded for the concrete work. User instructions remain authoritative over project workflow
guidance when they explicitly conflict.

Desired startup sequence:

```text
1. Host/project bootstrap
2. `cao bootstrap`
3. Identify the existing Work Item (resume, do not recreate)
4. Follow CAO next action
5. Resolve SDD-native action through the configured provider
6. Apply task/domain skills
```

The bootstrap layer is not a second state machine. It exists only to make the CAO entry point
reliably discoverable at session start.

`templates/AGENTS.md` and `templates/CLAUDE.md` are host-specific project-instruction templates.
When `cao bootstrap --repo <project>` runs, it copies each template into the target project root
only if the destination file is absent; existing project instructions are never overwritten. The
CAO package itself does not ship a nested `.agents/` directory because the whole package is intended
to be installed as `.agents/skills/coding-agent-orchestrator/`. Template files are included in both
source-checkout and Python package installs.

## Work Item title

The requirement filename is only a fallback display title. `cao start --title` can override it without
changing Work Item identity, which is derived from the requirement path. This is important for SDDs that
use fixed filenames such as `SPEC.md`.

## Artifact references

`protocol/artifact-reference.schema.json` describes the lightweight native-artifact reference stored under
`WorkItem.sdd.artifacts`. The CAO CLI provides the minimum writer lifecycle:

```text
cao artifact add
cao artifact list
cao artifact refresh
cao artifact remove
cao artifact confirm
```

CAO stores path, SHA-256, semantic role, native type, and optional native reference. Native file contents
remain owned by the SDD.

## Verification script ownership

CAO deliberately starts with an empty `verification.scripts` list. It does not assume `unittest`,
`pytest`, `mvn test`, coverage tools, or any other project-specific verification command. The project
owns the scripts and their quality rules. Each script returns `0=pass`, `1=fail`, `2=blocked`, or
`>=3=error` and writes a concise reason to stdout/stderr. CAO records the result but does not parse
project-specific metrics. Changing a config-bound verification script set invalidates the previous
verification automatically.

## Index consistency

`.orchestrator/work/<id>.json` is the Work Item source of truth. `index.json` is a projection used for
active-work lookup and fast listing. Every Work Item mutation goes through `save_work()`, which persists
the Work Item and immediately refreshes its index projection. The index can therefore be rebuilt from the
Work Item files if needed.

## BMad non-interactive installation

BMad fresh installation is constructed from the active host or an explicit `--tools` value and always
includes the target repository directory, the BMM module, and non-interactive confirmation:

```text
npx bmad-method install --directory <repo> --modules bmm --tools <tool> --yes
```

This prevents the CAO SDD installation gate from falling into provider prompts.

CAO resolves the BMad Python prerequisite by checking available Python executables for a compatible
version. Versioned executables such as `python3.11` are valid; the generic `python3` alias is not
required.


## Orchestration dashboard

The `cao dashboard` command serves a local control panel using the Python standard library. It is a
projection over the existing `.orchestrator/` files, not a second state store. It visualizes Work Items,
minimum-safe execution profiles, current step and next action, blockers, Verification summaries, Sessions,
SDD detection, Handoff history, and provider-native artifacts. Artifact status is `not_generated`, `generated`,
or `confirmed`; confirmation stores the current artifact hash and review metadata in the CAO Work Item without
editing the SDD-owned file. Markdown and other bounded text files can be previewed directly. Artifact confirmation
is restricted to a loopback-bound dashboard; remote binding requires `--allow-remote` and disables confirmation.
Full repository code snapshots are not recalculated on every browser refresh; `cao done` remains authoritative
for Verification freshness.

## User language policy

CAO stores one project-level preference at `language.user` (default `zh-CN`). User communication and SDD-generated documents follow this preference. Provider adapters apply it through each SDD's native configuration: BMAD communication/document language keys, OpenSpec project context, and Spec Kit constitution language principles. No provider-specific language selection algorithm is maintained. Applying the same preference is idempotent and only replaces CAO-managed sections or the provider's dedicated language keys. Explicit user language requests take precedence for the requested scope.
