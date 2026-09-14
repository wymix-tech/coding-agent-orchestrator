# Host Enforcement + Session Context

Host Enforcement combines runtime enforcement with snapshot-bound Session Bootstrap / Handoff context injection. Session artifacts remain projections; host adapters remain thin. Host adapters MUST stay thin: they translate events and outputs but do not duplicate Decision, Policy, Context, or State logic.

`tool_actions.py` translates payloads to actions; `action_guard.py` makes every
eligibility decision. The kernel returns that result under `metadata.authorization`.
See [Action Authorization](action-authorization.md) for the shared contract also used
by CLI checks, state transitions, verification, and resume/start guidance.

## Current-host auto selection

Self-Bootstrap uses runtime host identity, not stale repository markers. Explicit `--host` wins; otherwise the nearest recognized Agent process/runtime signal is selected. Pi and Codex expose positive subprocess signals, and an unrecognized runtime falls back to Claude Code. A later switch to another supported Agent triggers adapter reconciliation only; it must not reinitialize project governance state.

## Activation vs enforcement

Project activation and runtime enforcement are separate. `AGENTS.md` / `CLAUDE.md` managed blocks route repository coding/resume work to the Skill before normal execution begins. Pi performs equivalent activation through its session extension. After the Skill is active, host hooks/extensions enforce lifecycle constraints through the shared kernel. Activation stubs never own project truth.

## Event contract

| Canonical event | Purpose |
|---|---|
| `session_start` | build/persist/inject Session Bootstrap once for cold start/resume/compact |
| `prompt_submit` | inject delta-only state/freshness context; do not repeat the full bootstrap every turn |
| `pre_tool` | block illegal production-code mutation before side effects |
| `post_tool` | mark semantic/context/verification evidence stale after material mutation |
| `file_changed` | catch external/on-disk changes when the host exposes them |
| `subagent_start` | build role-specific bootstrap and include a fresh handoff when applicable |
| `subagent_stop` | require role outcome/evidence before a reviewer/verifier exits |
| `stop` | block completion claims when governance completion is not ready |

## Enforcement levels

- `ADVISORY`: context only.
- `CHECK`: post-action feedback; does not undo side effects.
- `BLOCK`: pre-action runtime deny.
- `FINAL_GATE`: execution-state transition guard / CI / branch protection. Never rely on a host hook alone.

## Dirty-window rule

A recorded material mutation invalidates analysis and final-verification freshness.
Further implementation edits are allowed only while the live fingerprint matches
the last recorded tool mutation and requirement/decision/policy/context sources stay
current. An additional external edit is not covered by this window. Advance and close
require fresh semantic intake and context. Generated intake/runtime/session/state
projections are excluded from the code fingerprint; their authoritative inputs are
hashed separately. CLI and state checks compute the same live fingerprint even if
no host event was delivered.

A `post_tool` event alone is not proof of a material edit. For a conservatively
classified code mutation such as `git add` or `git commit`, the kernel preserves
current evidence when the live content and bound authority/comparison inputs
still match. This does not clear an existing dirty state or exempt Git hooks
that actually modify files. Governance mutations retain their dirty marking.

## Configuration and completion

The supported hook settings are `enabled`, `post_mutation_policy_feedback`,
`completion_claim_only`, and `max_stop_blocks_per_session` (plus schema `version`).
Legacy `mode`, `require_*_for_code_mutation`,
`require_fresh_context_before_first_mutation`, `allowed_code_mutation_phases`,
`source_roots`, and `non_code_prefixes` no longer override shared authorization.
Existing files can retain these keys during migration; new starters omit them.
`enabled: false` disables this hook adapter, while CLI/state authorization still applies.

Exhausting the stop retry budget keeps the decision `deny` and sets
`retry_recommended: false`; Pi stops requesting automatic continuation. A reviewer
or verifier can report a failed outcome and finish their role without claiming global
completion. Role completion never substitutes for `close` authorization.

The payload adapter handles `cmd`/`command`, raw patches, moves, and shell operators.
Unknown commands/tools require code-mutation authorization. Exact packaged governance
commands and document preparation remain available for recovery. Shell classification
is a workflow heuristic, not a general command sandbox; actual host event delivery
and CI remain separate integration boundaries.

## Install

Use `scripts/install_host_adapter.py --repo . --host <claude-code|codex|pi|all> --apply` from the Skill root. Review generated hook definitions before trusting/enabling them.

## Session-context rule

Canonical session artifacts are JSON, configuration is YAML, and host prompt injection is compact Markdown. See `references/session-context.md`. Generated `.orchestrator/session/` files are excluded from the material worktree fingerprint.

## Compatibility references

- Claude Code Hooks reference: https://code.claude.com/docs/en/hooks
- Codex Hooks reference: https://developers.openai.com/codex/hooks
- Pi extension lifecycle reference: https://github.com/open-gsd/gsd-pi/blob/main/packages/pi-coding-agent/docs/extensions.md

The host capability matrix is intentionally conservative. If a host adds/removes lifecycle guarantees, update only the adapter/capability declaration; do not move governance authority out of the Decision, Evidence, Execution State, or Semantic Impact layers.
