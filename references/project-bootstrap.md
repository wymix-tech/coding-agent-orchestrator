# Project Bootstrap & Unified CLI

Project Bootstrap productizes the existing governance engines. It does **not** create a new authority.
The goal is one-time repository initialization followed by automatic resume through host
adapters and durable project state.

## Stable entry points

```bash
./coding-orchestrator init
./coding-orchestrator discover
./coding-orchestrator doctor
./coding-orchestrator status
./coding-orchestrator intake "<requirement>"
./coding-orchestrator resume
./coding-orchestrator verify
./coding-orchestrator host install <claude-code|codex|pi|all>
```

Use `--json` before the subcommand for machine-readable output.

## Safe Auto principles

`init` may automatically configure only facts supported by repository evidence.

It MAY automatically detect:

- Git repository state;
- languages and build systems;
- Spring Boot dependency markers;
- OpenSpec/BMAD markers and native sprint state;
- repository-local Claude Code/Codex/Pi host markers;
- CBM binary availability;
- a classic Spring `web/controller -> service -> dao/repository` structure when all layers
  are actually present.

It MUST NOT invent:

- SDD authority when multiple candidates exist;
- architecture style from framework identity alone;
- quality thresholds;
- negative risk facts;
- an active work item;
- native-state readiness;
- completion state.

## First initialization

`init` creates safe infrastructure only:

```text
.orchestrator/
  config.yaml
  bootstrap-report.json
  enforcement.yaml
  session-context.yaml
  policies/
  intake/
  context/
  session/
  evidence/
```

It intentionally does **not** create `execution-state.yaml`. The first real `intake`
creates the canonical active work item.

Existing project-owned configuration and policy files are preserved unless a force/migration
operation is explicitly requested.

## Host detection

Safe Auto installs a host adapter only when the repository already contains the corresponding
project-local host marker (`.claude`, `.codex`, or `.pi`). A binary-only signal is advisory
because executable names can collide and do not prove repository intent.

Explicit installation is always available:

```bash
./coding-orchestrator host install pi
```

## Spring architecture detection

Spring Boot does not imply classic layered architecture. The blocking Spring starter policy is
auto-enabled only when repository structure proves the `web/controller`, `service`, and
`dao/repository` layers. Clean/hexagonal/DDD-style markers suppress automatic classic-layered
policy activation.

## SDD ambiguity

If OpenSpec and BMAD are both present, `init` returns `ACTION_REQUIRED`. Resolve explicitly:

```bash
./coding-orchestrator init --sdd openspec
# or
./coding-orchestrator init --sdd bmad
```

In CI mode, unresolved authority returns a non-zero exit code:

```bash
./coding-orchestrator init --ci
```

## Intake

The first intake creates a provisional canonical state with the minimum neutral flow floor.
Code mutation remains blocked until the Decision Engine returns `CLASSIFIED`. The semantic
pipeline then reconciles the computed flow, attaches analysis/policy/context artifacts, and
builds the session bootstrap.

CBM absence does not block repository initialization. Normal semantic intake remains fail-closed.
`--degraded` is an explicit escape hatch for collecting partial artifacts; it must never turn
missing provider evidence into low impact.

## Doctor

`doctor` checks installation and project health without requiring an active work item. Missing
CBM is a warning; missing project config/policy or unresolved SDD authority is an error.

## Agent-tool independence

Claude Code, Codex, Pi, or a future host must remain replaceable. Host adapters consume the same
repository state and Enforcement Kernel. Project state survives host replacement.
