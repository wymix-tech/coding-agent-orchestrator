# Coding work bootstrap

This repository uses Coding Agent Orchestrator (CAO) as its persistent work-control layer.
For repository coding work, CAO is the first project workflow check.

Before editing code:
1. Identify the current host (`codex` or `pi`) and run `cao bootstrap --repo . --host <host>`. If the installed `cao` command is unavailable, run `python .agents/skills/coding-agent-orchestrator/scripts/cao.py bootstrap --repo . --host <host>`.
2. If `bootstrap` reports `mode: sdd_select` or `mode: sdd_install_required`, stop before coding. Ask the user to choose an SDD and complete it with `cao sdd install --provider <openspec|bmad|speckit> --yes` (or run `python .agents/skills/coding-agent-orchestrator/scripts/cao.py sdd install ...`). For Spec Kit, pass `--host <host>` or an explicit `--integration <key>`. Run `cao bootstrap` again after installation.
3. If an existing Work Item is active or resumable, continue it with CAO. Do not start a new requirement merely because the Agent or session changed.
4. Run `cao next` and follow the reported next action.
5. Use the project's SDD for native requirements, plans, specs, and native workflow. CAO stores execution state and references only.
6. Record meaningful progress with `cao checkpoint ...` and durable choices with `cao decision ...`.
7. Before completion, run `cao verify` and then `cao done`.

Explicit user instructions remain authoritative. CAO coordinates continuity; it does not replace the project's SDD or the Agent's normal coding capabilities.
