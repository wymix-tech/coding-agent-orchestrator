# V6.3 Host Enforcement Adapters

V6.3 attaches existing governance decisions to host lifecycle events. Host adapters MUST stay thin: they translate events and outputs but do not duplicate Decision, Policy, Context, or State logic.

## Event contract

| Canonical event | Purpose |
|---|---|
| `session_start` | inject compact resume/current-context summary |
| `prompt_submit` | remind/inject current context before a new turn |
| `pre_tool` | block illegal production-code mutation before side effects |
| `post_tool` | mark semantic/context/verification evidence stale after material mutation |
| `file_changed` | catch external/on-disk changes when the host exposes them |
| `subagent_start` | project role-specific context |
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

## Compatibility references used for V6.3

- Claude Code Hooks reference: https://code.claude.com/docs/en/hooks
- Codex Hooks reference: https://developers.openai.com/codex/hooks
- Pi extension lifecycle reference: https://github.com/open-gsd/gsd-pi/blob/main/packages/pi-coding-agent/docs/extensions.md

The host capability matrix is intentionally conservative. If a host adds/removes lifecycle guarantees, update only the adapter/capability declaration; do not move governance authority out of V3/V4/V5/V6.
