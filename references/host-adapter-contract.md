# Host Adapter Contract

Coding Agent Orchestrator treats Claude Code, Codex, Pi, and future coding agents as **hosts**.
A host adapter is a transport layer. It MUST NOT implement its own Decision, Policy, SDD,
Execution State, Semantic Impact, or Completion logic.

## 1. Architecture

```
AI Agent Host
   │
   │ host-specific lifecycle/tool events
   ▼
Thin Host Adapter
   │
   │ Canonical Event
   ▼
Enforcement Kernel
   │
   ├── Decision
   ├── Policy
   ├── Evidence / Semantic Impact
   ├── Context Plane
   ├── Execution State
   └── Completion / Verification
```

The rule is:

> **Host differences belong at the edge. Governance truth belongs in the core.**

## 2. Canonical Event Envelope

Adapters should normalize host payloads to this logical shape:

```json
{
  "event": "session_start",
  "host": "claude-code",
  "session_id": "optional",
  "cwd": "/workspace/project",
  "role": "optional",
  "tool": "optional",
  "payload": {}
}
```

Supported canonical events:

| Event | Meaning | Typical enforcement |
|---|---|---|
| `session_start` | New/resumed/compacted session | Bootstrap + context |
| `prompt_submit` | User/agent prompt boundary | Delta context + start routing |
| `pre_tool` | Tool about to execute | BLOCK illegal mutation |
| `post_tool` | Tool finished | Invalidate stale evidence |
| `file_changed` | External/on-disk change observed | Mark freshness dirty |
| `subagent_start` | Reviewer/planner/verifier starts | Role context |
| `subagent_stop` | Subagent attempts to finish | Role completion guard |
| `stop` | Main agent attempts to finish | Completion Contract |

Unknown lifecycle events should be ignored or logged unless they are explicitly promoted to
a canonical event by the kernel.

## 3. Canonical Output

The kernel returns:

```json
{
  "event": "pre_tool",
  "decision": "allow",
  "reason": null,
  "additional_context": null,
  "actions": [],
  "metadata": {}
}
```

`decision` values are:

- `allow`: host may continue.
- `deny`: host must block/continue/retry according to that host's lifecycle semantics.

Adapters MUST map this result to native host behavior without changing its meaning.

## 4. Host Mapping

### Claude Code

```
SessionStart      → session_start
UserPromptSubmit  → prompt_submit
PreToolUse        → pre_tool
PostToolUse       → post_tool
SubagentStart     → subagent_start
SubagentStop      → subagent_stop
Stop              → stop
FileChanged       → file_changed   (when configured)
```

Claude Code's native hook response should be used for runtime blocking and additional context.

### Codex

```
SessionStart      → session_start
UserPromptSubmit  → prompt_submit
PreToolUse        → pre_tool
PostToolUse       → post_tool
SubagentStart     → subagent_start
SubagentStop      → subagent_stop
Stop              → stop
```

Codex may require explicit project trust/review before project hooks execute. This is a
host deployment concern, not a reason to weaken governance.

### Pi

```
session_start      → session_start
before_agent_start → prompt_submit
tool_call          → pre_tool
tool_result        → post_tool
agent_end          → stop
```

Pi has extension-defined capabilities for some events. The adapter should use the native
extension lifecycle and inject context into the system prompt when the host exposes that
capability.

## 5. Recovery Contract

Every BLOCK should be actionable.

The core should return:

```text
reason + next_action + executable recovery command(s)
```

The host adapter MUST preserve these diagnostics. It must not replace a concrete recovery
path with generic text such as "try again".

This is especially important for resume/retry loops: a blocked action without an executable
recovery path can turn governance into an agent deadlock.

## 6. Enforcement Hierarchy

```
Prompt / Session Context
        ↓
Host Runtime Hook / Extension
        ↓
Execution State Guard
        ↓
Required CI / Quality Gate
        ↓
Merge / Branch Protection
```

A host with weaker runtime capabilities uses stronger downstream enforcement. The reverse is
not allowed.

```
Host capability gap
      ≠
Governance requirement downgrade
```

## 7. Context Injection Rules

- Cold start/resume/compact: inject one full Session Bootstrap.
- Normal prompt turns: inject delta-only state/freshness information.
- Subagents: inject role-specific context plus a fresh handoff when applicable.
- Do not repeatedly inject the full policy corpus or historical chat.
- Generated session artifacts are projections, not authority.

## 8. Freshness Rules

A material repository mutation invalidates analysis/semantic-impact/context/final-verification
freshness. Consecutive implementation edits may remain inside the Dirty Window, but
review/verification/close still require fresh evidence.

Adapters MUST NOT clear dirty state merely because their own callback succeeded.

## 9. Adding a New Agent

A new host requires only:

1. Add a host adapter under `hosts/<agent>/`.
2. Declare lifecycle capabilities in `references/host-capabilities.yaml`.
3. Normalize native events to the canonical event set.
4. Call `scripts/enforcement_kernel.py host ...`.
5. Add adapter contract tests.
6. Do not copy Decision/Policy/State logic into the adapter.

A useful adapter target is **under 150 lines** for a simple extension-based host and under
300 lines when the host requires compatibility plumbing.

## 10. Non-goals

This contract does not attempt to standardize:

- the agent's model/provider;
- the host's native permissions UI;
- the host's own planning methodology;
- a replacement for CI;
- a global multi-agent scheduler.

The orchestrator standardizes the **control plane** around coding work, not the internals of
the agent itself.
