# Roadmap

## Implemented

- [x] Agent-neutral `scripts/cao.py` core
- [x] `SKILL.md` Agent operating contract
- [x] Durable Work Items per requirement
- [x] Multiple requirements with `cao list` / `cao status --work`
- [x] Session registry and cross-session attachment
- [x] Host-aware Session identity, lifecycle events, and activity freshness using the current schema only
- [x] Primary/subagent lifecycle separation and `cao sessions list/show` inspection commands
- [x] Automatic Handoff on host stop/session end
- [x] Immutable Handoff history with `cao handoff list/show`; latest Handoff derived from history records without legacy-file compatibility
- [x] `cao resume` for same-agent, new-session, and cross-agent recovery
- [x] Checkpointed step / next action / progress summary
- [x] Append-only important decisions
- [x] Explicit blockers and optional suspension
- [x] Snapshot-bound verification freshness
- [x] SDD-native references without artifact duplication
- [x] Thin Claude Code / Codex / Pi adapters
- [x] Always-on bootstrap with an SDD installation gate
- [x] Install `AGENTS.md` / `CLAUDE.md` templates on bootstrap without overwriting existing files
- [x] SDD detection, user selection, installation, and project initialization for OpenSpec / BMad Method / GitHub Spec Kit
- [x] Non-interactive BMad installation with explicit project directory and host/tool selection
- [x] Explicit Work Item title override
- [x] SDD artifact-reference writer lifecycle
- [x] Work Item → index projection synchronization
- [x] Project-owned verification scripts with no unsafe default
- [x] Minimum-safe execution path selector with three profiles and deterministic risk upgrades
- [x] SDD-specific execution-path guidance for BMAD, OpenSpec, and Spec Kit without duplicating native lifecycles
- [x] Provider-neutral native-workflow completion contract for standard/comprehensive paths, including existing output-file references and a `cao done` completion gate

## Next

1. Formalize optional SDD Provider discovery hooks and configurable provider installation recipes.
2. Add provider-specific workflow-contract fixtures to verify native workflow invocation guidance and expected output paths.
3. Add fixture-based host-session-id extraction tests using real event payloads for each supported host.
4. Add human-readable `cao status` table output alongside JSON.
5. Add optional project-level completion gates without reintroducing an evidence graph.

- [x] Local orchestration dashboard for Work Items, execution path, Sessions, Verification summaries, SDD providers, Handoff history, native SDD artifact inventory/status, safe preview, and hash-bound confirmation.
