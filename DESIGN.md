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
session history
```

### Session

A temporary Agent runtime attachment:

```text
host
session id
work item
started / last seen / ended
last action
```

A Session is disposable. A Work Item is durable.

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

CAO does not need to own or persist the host's complete chat transcript.

## Repository layout

```text
.orchestrator/
├── config.json
├── index.json
├── work/
│   └── <work-id>.json
├── sessions/
│   └── <session-id>.json
└── handoffs/
    └── <work-id>.json
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
