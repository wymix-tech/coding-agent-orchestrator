# Project Bootstrap & Unified CLI

Project Bootstrap productizes the existing governance engines. It does **not** create a new authority.
The goal is self-bootstrap on first activation, followed by automatic resume through host adapters and durable project state.

## Stable entry points

The recommended project-local Skill location is `.agents/skills/coding-agent-orchestrator/`.
On first Skill activation, the Bootstrap Guard checks `.orchestrator/config.yaml`. If missing, it invokes the packaged Safe Auto `init` once and continues the same request. Manual initialization remains available from the project root:

```bash
./.agents/skills/coding-agent-orchestrator/coding-orchestrator --repo . init
```

Examples below use the shorter Skill-root form:

```bash
./coding-orchestrator init
./coding-orchestrator discover
./coding-orchestrator doctor
./coding-orchestrator status
./coding-orchestrator start
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
- repository-local Claude Code/Codex/Pi host markers as diagnostic evidence;
- the currently running Agent host from runtime/process evidence;
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

## Bootstrap Guard

Bootstrap states are:

```text
UNBOOTSTRAPPED -> Safe Auto init -> READY_FOR_INTAKE -> intake -> READY_FOR_WORK
```

A valid existing config is never regenerated. Invalid config or unresolved authority returns `ACTION_REQUIRED`; the guard does not overwrite, guess, or create a fake active task.

## Start intent routing

`start`, `continue`, `resume`, `开始`, `继续`, and similar short continuation prompts are control intents, not requirements. The stable CLI is:

```bash
./coding-orchestrator start
```

The router resolves the next legal action from authoritative project state:

```text
UNBOOTSTRAPPED  -> Safe Auto init -> re-evaluate
READY_FOR_INTAKE + 0 actionable requirements -> ask for the first requirement
READY_FOR_INTAKE + 1 high-confidence requirement -> canonical intake
READY_FOR_INTAKE + multiple requirements -> ACTION_REQUIRED / select one
READY_FOR_WORK -> resume the current work item
BLOCKED -> surface blockers before continuing
completed work -> discover the next requirement; never invent it
```

Requirement Discovery is conservative. Native SDD artifacts win when an SDD authority is selected. Generic projects may use dedicated requirement/spec/PRD documents, or a root README only when it contains an explicit requirements/goals/features section with actionable language. `.agents/`, `.orchestrator/`, host config, build output, and ordinary installation READMEs are excluded. A bare start intent never creates production code or a fake work item.

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

## Project activation

Skill discovery and Skill activation are separate concerns. A host may discover
`.agents/skills/coding-agent-orchestrator/SKILL.md` without selecting it for a vague prompt.
If the Skill is selected, the Bootstrap Guard can self-initialize immediately. The first successful `init` also installs a tiny project activation layer by default so future vague resumes are more reliable:

- `AGENTS.md`: a host-neutral managed block that routes repository coding/resume work to the Skill;
- `CLAUDE.md`: an additional managed block when Claude Code is explicitly installed/detected;
- Pi: the extension activates through `session_start`, with `AGENTS.md` as a portable fallback.

The managed block recognizes short continuation prompts such as `开始`, `继续`, `接着做`,
`start`, `continue`, and `resume`. It instructs the host to load the Skill before acting.
It does not duplicate Skill rules or project state. Existing text outside the managed markers is preserved,
and repeated initialization replaces the managed block rather than appending duplicates.

Use `--no-activation` only when the repository intentionally manages activation elsewhere.
`doctor` treats a missing managed `AGENTS.md` block as an installation error when activation is enabled.

## Host selection and reconciliation

`--host auto` selects the adapter for the **currently running Agent**, not the repository's historical host markers. The selection order is:

```text
explicit --host
  -> nearest recognized Agent process / runtime signal
  -> Codex or Pi host-specific environment signal
  -> Claude Code fallback
```

Pi exposes `PI_CODING_AGENT=true` to subprocesses. Codex exposes `CODEX_THREAD_ID` to shell/tool executions. Process ancestry is also inspected so nested/bridged launches prefer the nearest actual Agent. Repository-local `.claude`, `.codex`, and `.pi` markers remain discovery/diagnostic evidence only; they do not decide Safe Auto selection.

On first bootstrap, exactly the selected host adapter is installed. On later activations, a valid bootstrapped project performs a lightweight Host Reconcile: if the current Agent's adapter is missing, only that adapter and activation stub are added. Project policy, SDD authority, execution state, and active work items are not reinitialized.

If the runtime cannot be identified reliably, Safe Auto deliberately installs the Claude Code adapter as the fallback. Explicit installation/override remains available:

```bash
./coding-orchestrator host install pi
./coding-orchestrator init --host codex
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

Requirement identity is stable and separate from content revision. Active A cannot silently accept explicit intake for different B. A changed revision of the same requirement requires explicit revision handling and invalidates derived acceptance/task/gate/review/verification evidence. Analysis runs are isolated under `.orchestrator/work-items/.../revisions/.../runs/...`; only a run based on the current state revision may attach as canonical. Completed requirement identities/revisions are retained in `.orchestrator/requirements/registry.json` so older completed work is not reopened after later work or process restart.

For BMAD, requirement discovery starts from configured/detected native sprint state (including `_bmad-output` or an explicit `native_state_ref`) and maps native work-item IDs/statuses to story artifacts. Installation templates/examples are excluded. Unsupported native formats or missing pending story artifacts return sourced `ACTION_REQUIRED` diagnostics instead of being treated as “no requirement.”

CBM absence does not block repository initialization. Normal semantic intake remains fail-closed.
`--degraded` is an explicit escape hatch for collecting partial artifacts; it must never turn
missing provider evidence into low impact.

## Doctor

`doctor` checks installation and project health without requiring an active work item. Missing
CBM is a warning; missing project config/policy or unresolved SDD authority is an error.

## Agent-tool independence

Claude Code, Codex, Pi, or a future host must remain replaceable. Host adapters consume the same
repository state and Enforcement Kernel. Project state survives host replacement.
