# Coding work bootstrap

This repository uses Coding Agent Orchestrator (CAO) as its persistent work-control layer.
For repository coding work, CAO is the first project workflow check.

Before editing code:
1. Run `cao bootstrap --repo . --host claude-code`. If the installed `cao` command is unavailable, run `python .agents/skills/coding-agent-orchestrator/scripts/cao.py bootstrap --repo . --host claude-code`.
2. If `bootstrap` reports `mode: sdd_select`, treat it as a hard authorization boundary: ask the user which SDD they want and wait for an explicit answer. Never choose based on a recommendation, task type, popularity, global CLI availability, or Agent preference. Do not run `cao sdd install` or `cao sdd select` before the user answers. If you cannot ask the user, stop and report `SDD_SELECTION_REQUIRED`; do not install or code. After the user explicitly chooses, install only that provider; for Spec Kit pass `--host claude-code` or `--integration <key>`. If `mode: sdd_install_required`, reinstall only the Work Item's already-selected provider. Run bootstrap again after installation.
3. If an existing Work Item is active or resumable, continue it with CAO. Do not start a new requirement merely because the Agent or session changed.
4. Run `cao next` and follow the reported next action.
5. Use the project's SDD for native requirements, plans, specs, and native workflow. CAO stores execution state and references only.
6. Record meaningful progress with `cao checkpoint ...` and durable choices with `cao decision ...`.
7. Before completion, run `cao verify` and then `cao done`.

Explicit user instructions remain authoritative. CAO coordinates continuity; it does not replace the project's SDD or the Agent's normal coding capabilities.

## Language policy

Read `.orchestrator/config.json` → `language.user`. Communicate with the user in that language and write all SDD artifacts and project documentation in the same language, including specifications, proposals, designs, plans, tasks, architecture documents, reviews, and summaries. Keep code symbols, API names, commands, standardized keywords, and file names unchanged where required. If the user explicitly requests a different language for a specific scope, follow that request for that scope. Use `cao language show` to inspect the preference and `cao language set --language <locale>` to change it.

If the user expresses an ongoing language preference that differs from the configured value, update it with `cao language set --language <locale>` before producing more SDD artifacts.
