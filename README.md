# Coding Agent Orchestrator

A small control plane for coding-agent work continuity.

## What it does

CAO keeps a durable state for every requirement so work can survive:

- a session ending;
- a new session starting;
- switching Claude Code → Codex → Pi;
- a requirement being temporarily suspended;
- verification becoming stale after code changes.

CAO does **not** replace the project's SDD. BMAD, OpenSpec, and other SDDs keep their own artifacts.
CAO stores references to those artifacts and owns only execution continuity.

### Path protection limits

Direct edits to CAO-derived `.orchestrator/` state are denied; actual requirement documents remain writable.
Targets are resolved, including `..`, symlinks, structured patch moves, Shell redirects and simple compound commands.
Native workflow outputs must stay in the current provider's real output directories, even with `--allow-unverified`.
That flag documents an entrypoint or failed-execution exception; it does not turn product files into SDD artifacts.
Product writes remain allowed with governance reminders.

This is **not a Shell sandbox**. Dynamic variables, command substitution, evaluated/nested scripts and unparseable
commands cannot be reliably mapped to file targets; detectable cases receive a risk reminder, not a claim of isolation.
Verification and installation commands are trusted project code and may have side effects.

## Installation

CAO exposes a standard `cao` command through its Python package metadata. Python 3.10 or newer is required.

Install the CLI from a repository checkout:

```bash
python -m pip install .
cao --help
cao bootstrap
cao dashboard --open
```

For development, use an editable install so changes to `scripts/cao.py` are immediately available:

```bash
python -m pip install -e .
make check
make test
```

The repository also includes a `./cao` source-checkout launcher for shell-based development and Agent plugin integrations. The generated `cao` command is the preferred interface for regular use. When invoking the implementation directly from a checkout, use `python scripts/cao.py ...`.

## Core commands

```bash
cao init --host <claude-code|codex|pi>
cao host install --host <host>
cao host status
cao bootstrap
cao sdd list
cao sdd install --provider openspec --yes
cao sdd install --provider bmad --host claude-code --yes
cao list
cao start --requirement requirements/login.md --title "User login" --sdd-ref changes/add-login
cao path assess --profile standard --reason "A small API change that needs a spec and integration tests"
cao path show
cao sdd workflow select --name "<native workflow>" --entrypoint "<native command or skill>"
cao sdd workflow execute --command "<command actually run>" --exit-code 0
cao sdd workflow complete --artifact _bmad-output/requirements.md
cao gate satisfy --gate scope_review --evidence "Reviewed the requirement scope"
cao gate show
cao checkpoint --step implement-login-api --next "run integration tests" --summary "JWT endpoint implemented"
cao decision --decision "Use Redis for refresh token" --reason "Supports revocation"
cao block --reason "Need product confirmation" --suspend
cao resume --host codex --session codex-42
cao sessions list
cao sessions show --id <session-id> --host codex
cao handoff --reason "transfer to another agent"
cao handoff list --work <work-id>
cao handoff show --id <handoff-id>
cao dashboard --open
cao next
cao verify
cao done
```

## Orchestration dashboard

Launch a local visual control panel for Work Items, execution paths, SDD providers, Agent Sessions,
Verification results, blockers, and Handoff history:

```bash
cao dashboard --open
# Or choose repository, port, and refresh interval explicitly:
cao dashboard --repo . --port 8765 --refresh 10 --open
```

The dashboard reads the existing `.orchestrator/` JSON files directly; it does not create a second
state store. It displays native artifacts for BMAD, OpenSpec, and Spec Kit as available: missing expected
artifacts are marked `未生成`, present files `已生成`, and explicitly reviewed files `已确认`. Markdown
and text artifacts can be previewed in the browser. Confirmation stores the current file SHA-256 and
confirmation metadata in the CAO Work Item; it never edits the original SDD file. A later content change
invalidates that confirmation. Verification snapshot freshness is still authoritatively checked by `cao done`.

For safety, the server binds to `127.0.0.1` by default. Artifact confirmation is enabled only for a local
loopback-bound server and validates the request origin; non-loopback binding requires explicit
`--allow-remote` and disables confirmation. Remote access has no built-in authentication, so use it only
on a trusted network. Press `Ctrl+C` to stop the server.

## Persistent state

```text
.orchestrator/
├── config.json
├── index.json
├── work/<work-id>.json
├── sessions/<session-internal-id>.json
├── lifecycle/events.jsonl             # append-only lifecycle event log
├── requirements/
│   └── 001-requirement-<slug>-<date>.md   # numbered requirement documents
└── handoffs/
    └── <work-id>/<handoff-id>.json    # immutable handoff history
```

Requirement documents stated inline must be landed here before a Work Item can be created. CAO owns the
location and numbering; the Agent writes the content:

```bash
cao requirement next-id --title "用户登录"   # creates .orchestrator/requirements/001-requirement-用户登录-2026-10-10.md
# edit that file with the requirement content, then:
cao start --requirement .orchestrator/requirements/001-requirement-用户登录-2026-10-10.md
cao requirement list                        # what already exists
```

Numbers are derived from existing files so they never collide. This directory is exempt from the
PreToolUse mutation gate, because a requirement must exist before `cao start` can create a Work Item —
blocking those writes deadlocks intake. The rest of `.orchestrator/` holds CAO state the Agent must not
hand-edit.

### Session lifecycle and inspection

Session files are the single source of truth for Agent runtime sessions. They use a stable internal ID derived from the host name and host-native session ID, so identical native IDs from different hosts do not collide. Session records store the host, associated Work Item, primary/subagent relationship, lifecycle status, latest observed event, and handoff reference; they do not duplicate execution progress or next-action state.

```bash
cao sessions list
cao sessions list --host codex --status active
cao sessions list --stale-after-minutes 30
cao sessions show --id <session-id> --host codex
```

`recent` and `stale` describe observed activity, not process liveness. Stop/turn_end (including Pi agent_end)
may create a Handoff but keep the Session active. Only session_end or a distinct subagent_stop ends it;
before_settle only reminds. Missing IDs produce diagnostics, never a shared default Session. Claude Code
uses SessionEnd; Pi 1.1.0 uses session_shutdown. Codex has no verified native SessionEnd integration:
use `cao handoff --host codex --session <id> --end-session` on actual exit, not each turn.

Host adapters register Sessions through `host-event`, which only happens when the adapter is installed. As a
fallback, `cao bootstrap` and `cao start` attach the runtime Session whenever the host and its native session
id are visible (`CAO_HOST`, `CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, `PI_SESSION_ID`); otherwise CAO records
nothing rather than inventing an `unknown` Session.

### SDD lifecycle reconciliation

CAO reconciles native SDD state when `cao bootstrap`, `cao status`, `cao list`, `cao resume`/`cao next`, or the Dashboard reads an active Work Item. In particular, bootstrap reconciles before it builds a new-session context, so a stale CAO phase cursor is repaired before the Agent resumes. The adapters for BMAD, OpenSpec, and Spec Kit bind only artifacts matched to the current requirement/work-item identity. Exact identity matches take precedence over partial matches; ambiguous BMAD candidates are not used to infer state or bind `native_ref`. A file's existence can establish an artifact reference, but it does **not** by itself mean implementation has started. BMAD requires an explicit machine-readable state; OpenSpec and Spec Kit use task checkbox progress (`all unchecked` = ready, `some checked` = in progress, `all checked` = implementation complete). Native implementation completion projects CAO to `review`, never directly to `done`; `cao done` still enforces CAO completion gates.

Observed state transitions and CAO creation/closure events are appended to `.orchestrator/lifecycle/events.jsonl` with idempotency keys. Inspect them with:

```bash
cao lifecycle events
cao lifecycle events --work <work-id> --limit 50
```

This is query/command-triggered reconciliation, not a permanent background file watcher. Native changes are observed on the next status/list/resume/next or Dashboard refresh; a failed or bypassed framework command is therefore recovered by subsequent reconciliation.

### Minimum-safe execution path

Every new Work Item starts with a lightweight path assessment before code mutation. The Agent chooses
one profile based on the requirement, relevant project context, scope, uncertainty, risk, reversibility,
and how the change can be verified. CAO does not use numeric scoring or a fact/evidence graph.

| Profile | Typical use | Minimum process |
|---|---|---|
| `light` | Clear, local, low-risk, reversible change | Inspect relevant code, make the change, targeted verification |
| `standard` | Ordinary feature or bug fix with limited design work | Scope/design as needed, tests, implementation review, verification |
| `comprehensive` | High-risk, broad, materially ambiguous, or hard-to-reverse change | Requirements and design review, staged implementation, stronger verification |

Every profile — including `light` — must run the provider's own native workflow before `cao done`. CAO
rejects placeholder entrypoints (`echo`, `true`, ...) and rejects output files outside the provider's own
directory, so product source files cannot be recorded as SDD artifacts. A genuine exception requires
`--allow-unverified --reason "<why>"`, which is recorded in the Work Item and the lifecycle event log.

Create or refresh the assessment:

```bash
cao path assess --profile light --reason "Clear, local, reversible change"
cao path show --work <work-id>
```

Risk flags are intentionally few and deterministic. `security`, `data-migration`, `irreversible-change`,
`cross-system`, and `major-ambiguity` require at least `comprehensive`; `external-contract`,
`multi-module`, and `compatibility` require at least `standard`. CAO upgrades a lower requested profile
when needed and records the reason, required gates, requirement hash, and selected provider guidance.

`major-ambiguity` asks the Agent to clarify behavior and reassess. Missing/stale assessments and material
ambiguity cause **reminders, not blocked product writes**. Direct state edits and `cao done` remain hard
constraints. Requirement changes need explicit re-anchoring before reassessment. Provider guidance does
not replace native prerequisites or waive configured Verification scripts.

Intake is exempt from this gate: writes inside a provider's native output directory
(`_bmad-output/`, `openspec/`, `specs/`, `.specify/`) are allowed without an active Work Item, because a
requirement/PRD must exist before `cao start` can create one — blocking those writes deadlocks intake.
Product writes elsewhere are allowed with reminders when an active, assessed Work Item is missing.

### Handoff history and inspection

Every Handoff is stored as an immutable record at `.orchestrator/handoffs/<work-id>/<handoff-id>.json`. The newest record is derived from the history directory. This directory layout is the only supported Handoff storage format; older flat-file Handoff records are not loaded or migrated.

```bash
cao handoff --reason "transfer to another agent"
cao handoff list
cao handoff list --work <work-id> --limit 10
cao handoff show --id <handoff-id>
```

`list` returns the newest records first; `show` prints the full snapshot. `cao resume` uses the newest record found in the Work Item's history directory.

### Ownership boundary

```text
SDD
 ├─ What / Why
 ├─ native artifacts
 └─ native workflow

CAO
 ├─ where the requirement is now
 ├─ next action
 ├─ resume point
 ├─ decisions
 ├─ blockers
 ├─ sessions / handoffs
 └─ verification freshness
```

## Agent integration

The repository uses a three-level loading model:

```text
Project bootstrap instructions (installed from templates/)
  ├─ AGENTS.md            -> Codex / compatible hosts
  └─ CLAUDE.md            -> Claude Code
           ↓
CAO Skill (SKILL.md), installed at .agents/skills/coding-agent-orchestrator/
           ↓
SDD provider + task/domain skills
```

`templates/AGENTS.md` and `templates/CLAUDE.md` are templates, not project-level instructions for
this CAO source repository. On `cao init`, CAO copies **only the template the provisioned host reads**
(`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex and Pi) into the target project root, and only when
that file does not already exist, rendering the known host into the instructions. When no host can be
detected, both are installed as a fallback. `cao bootstrap` re-checks them idempotently. Existing user
instructions are never overwritten.
The CAO package deliberately does not contain a nested `.agents/` tree because its installation
directory is already the Skill directory.

The bootstrap is deliberately small. It tells the Agent to run `cao bootstrap` before repository
coding work. Bootstrap first checks whether a project SDD is installed. When none is present, CAO
stops the coding flow, asks the user to choose a provider, and exposes the exact installation
command. After installation and initialization, bootstrap continues into Work Item selection/resume.
The CAO Skill then governs resume, next action, checkpoint, decision, blocker, verification, done,
and handoff. The SDD remains authoritative for its native artifacts and workflow.

The package includes host adapters for Claude Code, Codex, and Pi. All adapters invoke the same `cao` CLI entry point when installed, and fall back to the source-checkout launcher when used directly from a plugin checkout.

### Host hooks are wired during `cao init`, not `cao bootstrap`

Host hooks are **process-startup integrations**: Claude Code reads its settings file and Pi loads its
extensions when the host process starts. Installing them from `cao bootstrap` is too late — that command
already runs inside a session whose `SessionStart` hook was missed, so no host event would be delivered
until the *next* session.

`cao init` therefore wires the adapter as part of provisioning:

```bash
cao init --host claude-code      # -> .claude/settings.local.json
cao init --host codex            # -> .codex/hooks.json
cao init --host pi               # -> pi install --local <cao-package> (.pi/settings.json)
cao host install --host <host>   # backfill an already-initialized project
cao host status                  # inspect whether each adapter is wired
```

If no host can be determined, `cao init` still succeeds and reports `host_adapters.status: host_required`
with the exact `cao host install --host ...` command. Initialization is never blocked by an unknown host.

Installation is idempotent and non-destructive: existing host settings are deep-merged, never rewritten,
and an already-registered CAO command is not added twice. Claude Code hooks go to `settings.local.json`
(usually git-ignored) so teammates who do not use CAO are never forced to load them.

Even with hooks installed, the current session cannot receive its own `SessionStart`. `cao bootstrap` and
`cao start` therefore attach the runtime Session as a fallback whenever the host and its native session id
are visible, which covers that first session. The two mechanisms are complementary, not alternatives.

## Why the core is intentionally small

There is no Fact Resolver, evidence provenance graph, second state store, or provider-specific governance
engine. `state_store.py` provides atomic JSON and repository transactions; SDD compatibility is reference-driven.
The execution-path selector follows the same constraint: a short Agent assessment, a three-profile
policy, a small hard-risk map, and Provider guidance. It does not add semantic scoring or evidence
infrastructure.

## SDD installation gate

CAO treats an SDD as a prerequisite for requirement-driven repository work. Having a Markdown
requirement file alone does not count as having an installed SDD.

Current built-in providers are OpenSpec, BMad Method, and GitHub Spec Kit. `cao sdd list` detects project installation;
`cao sdd install --provider <provider> --yes` performs provider installation/initialization and records
the selected provider only after the provider is detected in the project.

## GitHub Spec Kit

GitHub Spec Kit is supported as a first-class SDD provider. CAO detects a Spec Kit project through `.specify/`, installs the `specify` CLI with `uv tool install specify-cli`, and initializes the current repository with the selected Spec Kit agent integration. Spec Kit supports Claude Code (`claude`), Codex CLI (`codex`), Pi (`pi`), GitHub Copilot (`copilot`), and other integrations.

Examples:

```bash
cao sdd install --provider speckit --host claude-code --yes
cao sdd install --provider speckit --host codex --yes
cao sdd install --provider speckit --host pi --yes
cao sdd install --provider speckit --integration copilot --yes
```

Spec Kit keeps its native `.specify/`, `specs/`, and agent integration files. CAO records the provider and native references without copying or reimplementing Spec Kit's workflow.

#
## User language and SDD artifacts

Conversation with the user and all SDD-produced artifacts follow the project user-language preference in `.orchestrator/config.json`:

```json
{
  "language": { "user": "zh-CN" }
}
```

Set it at initialization or change it later:

```bash
cao init --language zh-CN
cao language show
cao language set --language en-US
cao language apply
```

The default is Simplified Chinese (`zh-CN`). `language.user` controls both user-facing communication and generated SDD documentation. CAO applies the preference through provider-native configuration: BMAD language config keys, OpenSpec `openspec/config.yaml` context, and Spec Kit `.specify/memory/constitution.md`. Bootstrap, provider selection, and installation reapply the configured preference. Managed language sections are idempotent and preserve unrelated project settings and content. Explicit user instructions to use another language for a specific scope take precedence.

## Verification configuration

CAO only orchestrates project verification. The project owns the actual verification logic by
providing one or more scripts in `.orchestrator/config.json`. A script returns an exit code and a
human-readable reason on stdout/stderr; CAO does not parse coverage, test reports, mutation reports,
or other tool-specific metrics.

Exit-code protocol:

```text
0     PASS
1     FAIL
2     BLOCKED / CANNOT VERIFY
>=3   ERROR IN VERIFICATION SCRIPT
```

Example:

```json
{
  "verification": {
    "timeout_seconds": 300,
    "scripts": [
      {"name": "unit-tests", "command": "./scripts/verify-unit-tests.sh", "required": true, "gates": ["tests"]},
      {"name": "coverage", "command": "./scripts/verify-coverage.sh", "required": true},
      {"name": "security", "command": "./scripts/verify-security.sh", "required": true}
    ]
  }
}
```

A unit-test script can enforce 100% success itself:

```bash
#!/usr/bin/env bash
mvn -q test
if [ $? -eq 0 ]; then
  echo "Unit tests passed: 100% success"
  exit 0
fi
echo "Unit tests failed: at least one test failed"
exit 1
```

A coverage script can enforce any project-specific threshold without CAO knowing what coverage means:

```bash
if [ "$coverage" -ge 80 ]; then
  echo "Line coverage ${coverage}% >= 80%"
  exit 0
fi
echo "Line coverage ${coverage}% < 80%"
exit 1
```

Use exit code `2` when verification cannot currently be completed, for example because a required
service is unavailable:

```bash
echo "Blocked: Redis is not running"
exit 2
```

CAO runs all scripts sequentially; all required scripts must pass. `gates: ["tests"]` explicitly declares
test capability: command keywords, optional checks and manual `gate satisfy --gate tests` cannot supply it.
Each script defaults to a 300-second deadline (`timeout_seconds`, finite and positive); timeout returns
124/error and reclaims its process tree. Each output tail is bounded to 4000 UTF-8 bytes. These trusted
commands are not sandboxed. Results also record `timed_out`; optional failures do not fail the overall run.

Fresh projects have no verification scripts. Repeat `--test-script` for required tests, or `--verify-script`
for general checks without test capability:

```bash
cao init --force \
  --test-script "./scripts/verify-unit-tests.sh" \
  --verify-script "./scripts/verify-coverage.sh"
```

For a one-off verification run without changing project configuration:

```bash
cao verify --script "./scripts/verify-unit-tests.sh"
```

CLI overrides are engineering checks, not config-bound completion evidence. Run `cao verify` from the
anchored config after stabilizing native artifacts. Changing scripts, required/tests capabilities or timeout
requires `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`, then fresh assessment,
workflow/gates as needed, and verification. Requirement changes use `cao start --requirement <path> --replace`;
old evidence is invalidated, not copied forward. Legacy completed records remain historical, not recertified.
First integrity upgrade preserves exact bytes under `.orchestrator/backups/integrity-v2/work/<id>.json`.

Native completion checks execution exit code and artifact bytes/ownership. A nonzero run needs a **new**
`--allow-unverified --reason` at complete; entry exceptions do not waive failed execution. Bind every related
plan with repeatable `--plan <path>`; shared sprint stories need `--plan-item '<path>#<id>'`. Explicit
`--no-plan` is audited; unresolved scope blocks done. Plans cannot silently narrow by deleting a binding.

Code snapshots prune ignored trees before traversal, follow only repository-contained file links (hashed
under lexical names), and never follow directory links. Read failures or external file links prevent freshness
certification. Dashboard ready/review is not done: only the authoritative completed Work Item is closed.

### Work Item titles and SDD artifacts

Use `--title` when the requirement filename is not a useful human title (for example BMad `SPEC.md`):

```bash
cao start --requirement SPEC.md --title "User login"
```

CAO persists SDD artifact references without copying their contents:

```bash
cao artifact add --path specs/auth/spec.md --role requirement --native-type spec
cao artifact list
cao artifact refresh
cao artifact remove --path specs/auth/spec.md
cao artifact confirm --path specs/auth/spec.md
```

`.orchestrator/work/<id>.json` is the Work Item source of truth. `index.json` is a projection and is
refreshed automatically after every Work Item mutation.

## Development

Requirements: Python 3.10 or newer and Git for the integration tests. The CAO core has no third-party Python dependencies.

Run the checks locally:

```bash
make check
make test
```

CI defines Ubuntu Python 3.10–3.13, three-platform state/runner checks, wheel smoke, and Pi contract/type
checks on Node 22.18.0. Pi metadata is restricted to API 1.1.0; Node/TypeScript are development/host dependencies,
not Python-core requirements. Run `npm ci --ignore-scripts`, `npm run check:pi`, and `npm run test:pi`.
Configured CI is not proof of a successful remote run. See [repair validation](docs/repair-validation.md) for
executed commands, compatibility steps and unverified real-host/browser/platform coverage. Generated bytecode,
caches and runtime state are ignored; project `.orchestrator/config.json` remains trackable.
