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
├── requirements/
│   └── 001-requirement-<slug>-<date>.md
└── handoffs/
    └── <work-id>/
        └── <handoff-id>.json          # immutable history records
```

## Requirement documents

A requirement stated inline in conversation has no file, and `cao start` requires one. CAO owns the
location (`.orchestrator/requirements/`) and the numbering (`NNN-requirement-<slug>-<YYYY-MM-DD>.md`);
the Agent owns the content. `cao requirement next-id` allocates the next path and creates a skeleton;
the Agent edits it rather than squeezing the text through a CLI argument. Intake remains writable without
an active Work Item; product writes also remain allowed, with governance reminders.
`requirement_files()` always includes `.orchestrator/requirements/**/*.md`, so requirement discovery
works in projects initialized before the glob existed in the default config.

These documents are CAO-owned intake records, not SDD artifacts: provider output directories
(`_bmad-output/`, `openspec/`, `specs/`, `.specify/`) remain the only places `cao sdd workflow complete`
accepts native artifacts from. Requirement documents live outside the code snapshot
(`SNAPSHOT_IGNORE_DIRS` contains `.orchestrator`), so landing or editing one does not invalidate
verification freshness.

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

`major_ambiguity` requests clarification and reassessment. Missing/stale assessment or ambiguity causes
Host reminders, never denied product writes; it still prevents `cao done`. Direct state edits stay denied.
Intentional requirement changes use `cao start --requirement <path> --replace` before reassessment.

Each built-in SDD Provider contains lightweight `path_guidance` for `light`, `standard`, and
`comprehensive`. These are provider-specific native-workflow instructions, not a second lifecycle
engine. CAO does not create SDD artifacts, issue a numeric score, scan the repository into a fact model,
or build a provenance graph. Profile selection never waives provider-native prerequisites or configured
Verification scripts.

The profile also never waives the native SDD workflow: `light`, `standard`, and `comprehensive` all
require the provider's own workflow to be selected, executed, and completed. `light` only reduces how
much of that workflow runs. Exempting `light` previously let an Agent go straight from path selection to
verification, skipping native planning, implementation, and review entirely.

Each provider also declares `output_dirs` (the directories it owns) and `workflow_hints` (tokens that
identify its own CLI/skill entrypoints). Both are used only for validation; CAO still never writes SDD
artifacts itself.

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
`name`, `command`, optional `required` (default `true`) and explicit `gates: ["tests"]` capability.
`cao init --test-script` declares required tests; `--verify-script` declares general checks. CAO executes scripts sequentially
and records every result. Project-specific verification logic, thresholds, and report parsing stay
inside the scripts. The script exit-code protocol is `0=pass`, `1=fail`, `2=blocked`, `>=3=error`.
The overall verification passes only when all required scripts return `0`.

`cao done` recomputes live values. Assessment revision and normalized scripts/required/gates plus `timeout_seconds` are acceptance inputs, not timestamp guesses.
Required successful config-bound tests alone supply the tests gate; manual claims and CLI overrides cannot. Execution runs outside the repository transaction;
commit re-reads current inputs and preserves concurrent decisions, marking changed inputs stale. Each script defaults to 300 seconds, bounded output and process-tree cleanup.
POSIX uses its own process group; Windows supervisor enters a kill-on-close Job before spawning. Job setup failure cannot fall back to direct execution. This is not a sandbox.
Changing standards needs explicit `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`, not silent anchor replacement.
Snapshots prune ignored directories, skip directory links, and hash internal file links by lexical path. External file links/read failures invalidate certification; no snapshot cache.

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
   required verification declarations ready?
   ├─ no → `verification_setup` → Agent analyzes/asks product stack
   │                              ↓
   │                     project wrappers + safe configure
   │                              ↓
   │                         bootstrap again
   └─ yes → Work Item selection / resume
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
Verification, Decision, or Completion rules themselves. Stop/turn_end (Pi agent_end) keep Sessions active; before_settle is a reminder boundary.
Only session_end or a distinct subagent_stop ends it with the original reason. Missing/colliding IDs diagnose and skip lifecycle changes, never create default identity.
Claude Code uses SessionEnd; Pi 1.1.0 uses session_shutdown with bounded async transport. Codex has no verified native SessionEnd; actual exit uses `cao handoff --end-session`.
Adapter failures fail open with diagnostics; managed-state edits remain the hard write restriction.

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
They are read when a session starts, so like the host hooks they are provisioned by `cao init`, which
copies only the template the provisioned host reads — `CLAUDE.md` for Claude Code, `AGENTS.md` for
Codex and Pi, both when no host is known — into the target project root if the destination file is
absent, and renders the known host into the instructions. `cao bootstrap` re-checks them idempotently so a deleted file is
restored and a project provisioned earlier still gets them. Existing project instructions are never
overwritten. The
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

After SDD authorization, bootstrap reports `verification_setup` for empty, invalid or optional-only
scripts. The Agent analyzes product manifests/source/tests/CI, reuses existing checks, or uses the user's
explicit stack for new products; otherwise it asks and waits. Host/SDD assets and caches are not product
stack evidence. CAO does not call an LLM or maintain framework templates. Project wrappers must fail/block
on zero tests or missing tools/dependencies; core never parses test reports. Configuration readiness is
not verification success. Scaffolding/test creation remains allowed and product writes are only reminded.

`cao verification configure --test-script "<tests>" --verify-script "<general check>"` performs a locked
atomic config-only update. Required tests are explicit; general checks do not gain tests capability.
It preserves language, SDD, timeout and unrelated raw fields. Equivalent declarations return `unchanged`
without a write; different nonempty/invalid old declarations require `--replace`. Whole malformed config
is rejected. It never installs adapters/dependencies, executes scripts, creates Work or changes its anchor.
Do not use `init --force` here. Active acceptance changes request explicit review/reanchor, including on
an unchanged retry; suspended/completed Work is not silently recertified. Setup context preserves existing
identity, execution, decisions and blockers without evaluating malformed completion configuration.

## Index consistency

`.orchestrator/work/<id>.json` is the Work Item source of truth. `index.json` is a projection used for
active-work lookup and fast listing. Every Work Item mutation goes through `save_work()`, which persists
the Work Item under a repository transaction with revision CAS, then refreshes its index projection. Missing/damaged/stale indexes rebuild from Work Items.
Multiple historical active items are reported, never arbitrarily selected; explicit suspension repairs conflicts. Atomic JSON uses same-directory replace and fsync.
Pending audit events live in committed Work Items, drain to the fsynced log and recover on reads. First upgrade backs up exact bytes once
under `.orchestrator/backups/integrity-v2/work/<id>.json`; failure prevents upgrade. Legacy active evidence needs explicit re-anchoring; completed history is not recertified.

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
Active/suspended freshness is recomputed from live inputs on overview. Only Work Item `completed` projects done; ready/native completion/step keywords cannot close it.
Entry and failure exceptions, legacy evidence, pending gates and stale results remain distinct. `cao done` is still authoritative.

## User language policy

CAO stores one project-level preference at `language.user` (default `zh-CN`). User communication and SDD-generated documents follow this preference. Provider adapters apply it through each SDD's native configuration: BMAD communication/document language keys, OpenSpec project context, and Spec Kit constitution language principles. No provider-specific language selection algorithm is maintained. Applying the same preference is idempotent and only replaces CAO-managed sections or the provider's dedicated language keys. Explicit user language requests take precedence for the requested scope.


## Native SDD workflow completion contract

For **every** profile (`light`, `standard`, `comprehensive`) on a Work Item using an installed SDD Provider, a successful code/test verification is not sufficient to complete the Work Item. The Agent records the provider-native workflow name and entrypoint with `cao sdd workflow select`, records that it actually ran the workflow with `cao sdd workflow execute --command ...`, and records existing repository-relative output files with `cao sdd workflow complete --artifact ...`. `cao done` requires a requirement/provider-bound run, acceptable exit code and matching artifact bytes/real paths. Plans are Work Item-bound:
repeat `--plan` for related changes, `--plan-item '<path>#<id>'` for shared sprint stories, or audit `--no-plan`. Unresolved/missing scope cannot certify completion. This is provider-neutral orchestration metadata, not a replacement SDD workflow; native artifacts remain owned by the provider. Verification should run after native artifacts have stabilized so snapshot freshness remains valid.

The contract is verifiable, not self-certifying. CAO refuses:

- a placeholder entrypoint (`echo`, `true`, `printf`, ...) — it satisfies every structural check while executing nothing;
- an entrypoint that does not reference the selected provider (matched against `SDD_CATALOG[provider].workflow_hints`);
- output files outside the provider's own directories (`output_dirs`: BMAD `_bmad-output/`, OpenSpec `openspec/`, Spec Kit `specs/` and `.specify/`) — product source files are not SDD artifacts.

A provider that genuinely cannot satisfy the contract is handled by an explicit, auditable exception: `--allow-unverified --reason "<why>"`. The exception is stored in `sdd.workflow.unverified` and appended to `.orchestrator/lifecycle/events.jsonl` as `workflow.unverified_exception`. It is never an ownership exemption. Nonzero execution needs a new reason in `workflow.failure_exception`, audited separately; re-execution clears completion hashes/exceptions and done rechecks paths/bytes.

## Execution gates

`path_assessment.required_gates` is enforced at done. Verification-flavoured gates and `tests` derive from fresh config-bound evidence; tests needs explicit required test capability.
Other gates use `cao gate satisfy --gate <name> --evidence "<what was done>"`, bound to requirement hash and assessment revision.
Reassessment invalidates old claims. Done returns pending gates and an actionable next instruction.

### Intake exemption in the PreToolUse gate

PreToolUse reminds on product writes without current assessment; it denies direct edits to managed state.
Writes inside requirement intake or a provider-owned SDD output directory
(`SDD_CATALOG[*].output_dirs`: `_bmad-output/`, `openspec/`, `specs/`, `.specify/`) are therefore exempt
for every mutating branch of the gate — with or without a Work Item. Requirement, PRD, and spec
artifacts are prerequisites of the lifecycle, not product code, and the same directories are the only
ones `cao sdd workflow complete` accepts artifacts from, so the exemption cannot be used to smuggle
product source past completion checks.

## Host hook provisioning

Host hooks are process-startup integrations, so they belong to provisioning rather than to the
per-session read path. Claude Code reads its settings file and Pi loads its extensions when the host
process starts; installing an adapter from `cao bootstrap` would therefore always be one session too
late, because the session running bootstrap has already missed its own `SessionStart`.

`cao init --host <host>` wires the adapter: it renders the shipped fragment with the real install path
(replacing `/PATH/TO/coding-agent-orchestrator` and `${PLUGIN_ROOT}` while leaving runtime variables such
as `${CLAUDE_PROJECT_DIR}` intact), then deep-merges it into the host's own configuration. Claude Code
targets `.claude/settings.local.json` so the integration stays local and does not force teammates to load
it. Pi registers a package instead of a JSON fragment and uses `pi install --local`.

The merge is idempotent and non-destructive: existing entries are preserved, an already-registered CAO
command is not duplicated, and matchers are kept separate. An unknown host never fails initialization; it
is reported as `host_adapters.status: host_required` with the exact remediation command, and
`cao host install` backfills an already-initialized project.

Because the current session can never receive its own `SessionStart`, `cao bootstrap` reports adapter
status instead of installing, and the Session fallback below covers that first session.

## Session registration fallback

Host adapters register Sessions through `host-event`, but that only works when the adapter is installed. `cao bootstrap` and `cao start` therefore call `register_session` as a fallback whenever `host_session_identity()` can resolve both a host and a native session id from the environment (`CAO_HOST`, `CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, `PI_SESSION_ID`). When neither is visible, CAO records nothing instead of inventing an `unknown` Session. This keeps Session continuity from depending on a single integration point.


## SDD lifecycle reconciliation and event log

Provider adapters discover work-item-scoped artifacts and extract native status evidence. They do not infer `in_progress` merely from a generated Markdown file. BMAD uses explicit machine-readable status on the matched artifact; OpenSpec and Spec Kit use task checkbox state. Unknown status remains unknown. Native `completed` means the native implementation plan is complete and projects CAO to `review`; it never closes a Work Item.

A successful observation updates `WorkItem.sdd.native_ref` when one unique, requirement-matched entry artifact is available, persists the latest normalized native state, and appends an idempotent event to `.orchestrator/lifecycle/events.jsonl`. Exact artifact identity wins over partial-slug matches; ambiguous BMAD candidates are ignored until the Work Item can be bound safely. Event append is guarded by an exclusive lock and flushed to disk before the command returns. Repeated polling of the same native state does not emit a second transition event. `cao lifecycle events` provides an audit view.

Reconciliation is invoked at bootstrap/status/list/resume/next and Dashboard refresh boundaries rather than relying on all SDD systems to expose the same native Hook. Bootstrap performs the read before constructing the resumed session context. This is a compensating-read model: provider-specific Hooks or command wrappers can be added later without changing the canonical CAO event names. It does not claim immediate background observation when no CAO command or Dashboard refresh is running.
