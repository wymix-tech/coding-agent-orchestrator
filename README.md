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
cao init
cao bootstrap
cao sdd list
cao sdd install --provider openspec --yes
cao sdd install --provider bmad --host claude-code --yes
cao list
cao start --requirement requirements/login.md --title "User login" --sdd-ref changes/add-login
cao path assess --profile standard --reason "A small API change that needs a spec and integration tests"
cao path show
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
└── handoffs/
    └── <work-id>/<handoff-id>.json    # immutable handoff history
```

### Session lifecycle and inspection

Session files are the single source of truth for Agent runtime sessions. They use a stable internal ID derived from the host name and host-native session ID, so identical native IDs from different hosts do not collide. Session records store the host, associated Work Item, primary/subagent relationship, lifecycle status, latest observed event, and handoff reference; they do not duplicate execution progress or next-action state.

```bash
cao sessions list
cao sessions list --host codex --status active
cao sessions list --stale-after-minutes 30
cao sessions show --id <session-id> --host codex
```

`recent` and `stale` are calculated from `last_event_at`; stale means “no recent event observed”, not “the Agent has ended”. Only an explicit end event closes a Session. Creating a Handoff alone does not end a Session; main-session stop events create a Handoff and then close that Session, while subagent stop events close only the subagent Session.

### Minimum-safe execution path

Every new Work Item starts with a lightweight path assessment before code mutation. The Agent chooses
one profile based on the requirement, relevant project context, scope, uncertainty, risk, reversibility,
and how the change can be verified. CAO does not use numeric scoring or a fact/evidence graph.

| Profile | Typical use | Minimum process |
|---|---|---|
| `light` | Clear, local, low-risk, reversible change | Inspect relevant code, make the change, targeted verification |
| `standard` | Ordinary feature or bug fix with limited design work | Scope/design as needed, tests, implementation review, verification |
| `comprehensive` | High-risk, broad, materially ambiguous, or hard-to-reverse change | Requirements and design review, staged implementation, stronger verification |

Create or refresh the assessment:

```bash
cao path assess --profile light --reason "Clear, local, reversible change"
cao path show --work <work-id>
```

Risk flags are intentionally few and deterministic. `security`, `data-migration`, `irreversible-change`,
`cross-system`, and `major-ambiguity` require at least `comprehensive`; `external-contract`,
`multi-module`, and `compatibility` require at least `standard`. CAO upgrades a lower requested profile
when needed and records the reason, required gates, requirement hash, and selected provider guidance.

`major-ambiguity` pauses implementation until the user clarifies the material behavior and the Agent
reassesses without that flag. Host Hooks deny code mutation while an assessment is missing/stale or a
material ambiguity remains. A requirement-file change must follow CAO's re-anchor procedure before a
new assessment. Provider-specific mapping remains guidance: BMAD, OpenSpec, and Spec Kit keep their
native workflow and mandatory preconditions. Profiles do not waive configured Verification scripts.

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
this CAO source repository. On `cao bootstrap`, CAO copies either template into the target project
root only when that file does not already exist. Existing user instructions are never overwritten.
The CAO package deliberately does not contain a nested `.agents/` tree because its installation
directory is already the Skill directory.

The bootstrap is deliberately small. It tells the Agent to run `cao bootstrap` before repository
coding work. Bootstrap first checks whether a project SDD is installed. When none is present, CAO
stops the coding flow, asks the user to choose a provider, and exposes the exact installation
command. After installation and initialization, bootstrap continues into Work Item selection/resume.
The CAO Skill then governs resume, next action, checkpoint, decision, blocker, verification, done,
and handoff. The SDD remains authoritative for its native artifacts and workflow.

The package includes host adapters for Claude Code, Codex, and Pi. All adapters invoke the same `cao` CLI entry point when installed, and fall back to the source-checkout launcher when used directly from a plugin checkout.

## Why the core is intentionally small

There is no Fact Resolver, evidence provenance graph, independent state-manager module, or provider-
specific governance engine. The core state is plain JSON, and SDD compatibility is reference-driven.
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

### Verification configuration

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
    "scripts": [
      {"name": "unit-tests", "command": "./scripts/verify-unit-tests.sh", "required": true},
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

CAO runs required scripts sequentially. Verification passes only when every required script returns
`0`. Each result records the exit code, normalized status, reason, stdout tail, and stderr tail.
Optional scripts (`required: false`) are still executed and recorded but do not fail the overall
verification.

Fresh projects start with an empty `verification.scripts` list. Use `--verify-script` repeatedly when
initializing a project:

```bash
cao init --force \
  --verify-script "./scripts/verify-unit-tests.sh" \
  --verify-script "./scripts/verify-coverage.sh"
```

For a one-off verification run without changing project configuration:

```bash
cao verify --script "./scripts/verify-unit-tests.sh"
```

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

The GitHub Actions workflow runs the unittest suite on Python 3.10 through 3.13. Generated bytecode, caches, and `.orchestrator/` runtime state are excluded from Git. The project-level `.orchestrator/config.json` remains trackable so teams can share verification and SDD settings.
