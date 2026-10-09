---
name: coding-agent-orchestrator
description: Coordinate repository coding work as a persistent work item across sessions and AI agents. Use this skill before starting, resuming, modifying, verifying, handing off, or completing a non-trivial coding requirement. It tracks execution state, next action, decisions, blockers, handoff, and verification while leaving SDD artifacts and native workflow to the project's SDD provider.
---

# Coding Agent Orchestrator

CAO is the repository's work continuity layer. It is not an SDD and not a coding agent.

## First action

This skill is normally triggered by the project-level Agent instructions. The `AGENTS.md` and
`CLAUDE.md` files are shipped as templates under `templates/` and installed by `cao bootstrap` when
missing; existing project instructions are preserved. The Skill itself is installed at
`.agents/skills/coding-agent-orchestrator/` and intentionally contains no nested `.agents/` tree.
At the start of repository coding work, run exactly:

```bash
cao bootstrap
```

Bootstrap initializes CAO state when needed, verifies that a project SDD is installed, reports the active/resumable Work Item, and returns the next action.
It never creates a Work Item and never reruns requirement intake because an Agent or session changed.
If no SDD is installed, bootstrap stops before coding, asks the user to choose an SDD, and requires the Agent to complete installation before proceeding.

## Resume before restarting work

A new session or a different agent MUST resume existing work before starting another requirement:

```bash
cao resume --host <claude-code|codex|pi> --session <host-session-id>
```

`resume` reads the persistent Work Item, latest Handoff, execution step, next action, decisions,
blockers, SDD reference, and verification state. It does not re-run intake merely because the
Agent session changed.

## Work lifecycle

```text
START → CHECKPOINT → VERIFY → DONE
           ↑
         RESUME
```

Useful commands:

```bash
cao bootstrap
cao sdd list
cao sdd install --provider <openspec|bmad|speckit> --yes
# Spec Kit:
cao sdd install --provider speckit --host <claude-code|codex|pi> --yes
# Or use any supported Spec Kit integration:
cao sdd install --provider speckit --integration <key> --yes
cao sdd select --provider <provider>
cao list
cao start --requirement <path> [--title <human title>] [--sdd-ref <native-ref>]
cao checkpoint --step <step> --next "<next action>" --summary "<progress>"
cao decision --decision "<decision>" --reason "<why>"
cao block --reason "<blocker>" [--suspend]
cao unblock --work <id>
cao handoff --session <id> --host <host>
cao next
cao verify
cao done
```

## State ownership

CAO owns:

```text
Work Item
Execution State
Next Action
Decisions
Blockers
Session
Handoff
Verification freshness
```

The SDD owns:

```text
PRD / Proposal / Spec / Design / Tasks / Story / Ticket / Native workflow
```

Never copy an SDD artifact into a second CAO-owned artifact just to make it "uniform". Store native
SDD paths/references instead.

## Progress discipline

After completing a meaningful slice, update CAO:

```bash
cao checkpoint \
  --step "<current step>" \
  --next "<specific next action>" \
  --summary "<what is complete and what remains>"
```

Record durable architectural or product choices immediately:

```bash
cao decision --decision "<choice>" --reason "<reason>"
```

A blocker that prevents useful continuation should be explicit. Use `--suspend` when the work
should leave the active slot:

```bash
cao block --reason "<why blocked>" --suspend
```

## Verification

Before claiming completion:

```bash
cao verify
cao done
```

Verification is snapshot-bound. A later material code or requirement change makes the previous
verification stale automatically. Do not fake a new verification record merely to close the work.

If `cao done` reports that the requirement changed, do not blindly rerun `cao verify`. Review the
requirement change; when the change is intentional, re-anchor the Work Item with
`cao start --requirement <path> --replace`. The `--replace` action changes the active
execution anchor; it does not mean that the original requirement was superseded.

## Session handoff

When a session ends, the Host Adapter may call `host-event` and CAO records a Handoff automatically.
For an intentional transfer, create an explicit handoff first:

```bash
cao handoff --session <id> --host <host> --reason "transfer to another agent"
```

The next Agent should consume that handoff through `cao resume` rather than rebuilding context from
chat history. Switching hosts does not create a new Work Item.

## SDD installation gate

A repository may contain requirements without having an SDD installed. That is not sufficient for CAO to start implementation.

The startup sequence is:

```text
bootstrap
   ↓
SDD installed? ── no ──→ ask user to choose OpenSpec, BMad Method, or GitHub Spec Kit
   │                         ↓
  yes                  cao sdd install --provider ... --yes
   ↓                         ↓
select/resume ←──── bootstrap again after installation
```

Use `cao sdd list` to inspect detection and `cao sdd install` to perform the provider's installation/initialization.
The provider installer remains responsible for its own native files and workflow. CAO only records the selected provider.

## SDD compatibility

CAO supports SDDs through references and an intentionally small provider protocol. It does not require
BMAD, OpenSpec, Spec Kit, or another specific framework.

Typical pattern:

```text
Native SDD artifacts
        ↓
SDD Provider / configuration
        ↓
Work Item.sdd.native_ref + artifact references
        ↓
CAO execution state
```

When an SDD has a native CLI, CAO may invoke configured `inspect`, `next`, or `transition` commands,
but CAO does not reimplement the SDD's artifact lifecycle.

## Host adapters

Claude Code, Codex, Pi, and future hosts are transport adapters only. They translate native lifecycle
events to:

```bash
cao host-event --host <host> --repo <repo>
```

The adapter MUST NOT maintain a second copy of Work/Decision/Verification rules.

## Verification scripts

CAO does not define project-specific quality metrics and does not parse tool-specific reports. Put the
verification logic in user-owned scripts and configure their commands in `.orchestrator/config.json`:

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

Use this exit-code protocol for every verification script:

```text
0     PASS
1     FAIL
2     BLOCKED / CANNOT VERIFY
>=3   ERROR
```

The script should print a concise reason to stdout or stderr. CAO records that reason together with
the exit code and command output. Verification passes only when every required script returns `0`.
Thresholds such as 100% test success, 80% line coverage, 80% branch coverage, or mutation kill rate
are implemented by the project script itself.

`cao init --verify-script` may be repeated to configure multiple scripts. `cao verify --script` may be
used for a one-off override without changing the project configuration.

## Work Item titles

The requirement filename is only a fallback title. Use `--title` when the SDD uses fixed filenames such as `SPEC.md`.

## SDD artifact references

CAO can persist native SDD artifact references with `cao artifact add/list/refresh/remove`. It stores path, SHA-256,
semantic role, native type, and optional native reference. It does not copy native SDD content.
