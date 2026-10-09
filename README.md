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
cao checkpoint --step implement-login-api --next "run integration tests" --summary "JWT endpoint implemented"
cao decision --decision "Use Redis for refresh token" --reason "Supports revocation"
cao block --reason "Need product confirmation" --suspend
cao resume --host codex --session codex-42
cao next
cao verify
cao done
```

## Persistent state

```text
.orchestrator/
├── config.json
├── index.json
├── work/<work-id>.json
├── sessions/<session-id>.json
└── handoffs/<work-id>.json
```

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
