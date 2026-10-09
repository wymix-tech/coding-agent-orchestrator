# Roadmap

## Implemented

- [x] Agent-neutral `scripts/cao.py` core
- [x] `SKILL.md` Agent operating contract
- [x] Durable Work Items per requirement
- [x] Multiple requirements with `cao list` / `cao status --work`
- [x] Session registry and cross-session attachment
- [x] Automatic Handoff on host stop/session end
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

## Next

1. Formalize optional SDD Provider discovery hooks and configurable provider installation recipes.
2. Add host-session-id extraction tests for each supported host.
3. Add human-readable `cao status` table output alongside JSON.
4. Add optional project-level completion gates without reintroducing an evidence graph.
