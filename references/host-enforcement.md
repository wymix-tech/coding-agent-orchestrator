# V6.4 Host Enforcement + Session Context

V6.4 retains V6.3 runtime enforcement and adds snapshot-bound Session Bootstrap / Handoff context injection. Session artifacts remain projections; host adapters remain thin. Host adapters MUST stay thin: they translate events and outputs but do not duplicate Decision, Policy, Context, or State logic.

## Event contract

| Canonical event | Purpose |
|---|---|
| `session_start` | build/persist/inject V6.4 Session Bootstrap once for cold start/resume/compact |
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
- `FINAL_GATE`: V5 transition guard / CI / branch protection. Never rely on a host hook alone.

## Dirty-window rule

A successful material mutation immediately invalidates semantic/context/final-verification freshness. V6.3 deliberately allows further implementation edits in the same dirty window. It blocks review/verification/close until V6 semantic intake and Context Plane refresh have reconciled the new snapshot. This avoids running CBM/full tests after every keystroke while preventing stale evidence from crossing a phase boundary.

## Install

Use `scripts/install_host_adapter.py --repo . --host <claude-code|codex|pi|all> --apply` from the Skill root. Review generated hook definitions before trusting/enabling them.

## V6.4 session-context rule

Canonical session artifacts are JSON, configuration is YAML, and host prompt injection is compact Markdown. See `references/session-context.md`. Generated `.orchestrator/session/` files are excluded from the material worktree fingerprint.

## Compatibility references used for V6.4

- Claude Code Hooks reference: https://code.claude.com/docs/en/hooks
- Codex Hooks reference: https://developers.openai.com/codex/hooks
- Pi extension lifecycle reference: https://github.com/open-gsd/gsd-pi/blob/main/packages/pi-coding-agent/docs/extensions.md

The host capability matrix is intentionally conservative. If a host adds/removes lifecycle guarantees, update only the adapter/capability declaration; do not move governance authority out of V3/V4/V5/V6.
