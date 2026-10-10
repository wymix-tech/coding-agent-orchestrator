# Coding work bootstrap

This repository uses Coding Agent Orchestrator (CAO) as its persistent work-control layer.
For repository coding work, CAO is the first project workflow check.

## CAO command

Every command below is written with the real CAO invocation already substituted. Invoke CAO exactly as
it appears here:

```
{{CAO_COMMAND}}
```

Do not guess where CAO is installed, and do not replace it with a bare command name, a relative path, or
any other location. If the command above is not found, stop and report it instead of trying alternatives.

Before editing code:
1. Run `{{CAO_COMMAND}} bootstrap --repo . --host claude-code`.
2. If `bootstrap` reports `host_adapter.installed: false`, run `{{CAO_COMMAND}} host install --host claude-code`. Host hooks load when the host process starts, so this takes effect from the next session; until then call `{{CAO_COMMAND}} resume`, `{{CAO_COMMAND}} checkpoint`, and `{{CAO_COMMAND}} handoff` yourself.
3. If `bootstrap` reports `mode: sdd_select`, treat it as a hard authorization boundary: ask the user which SDD they want and wait for an explicit answer. Never choose based on a recommendation, task type, popularity, global CLI availability, or Agent preference. Do not run `{{CAO_COMMAND}} sdd install` or `{{CAO_COMMAND}} sdd select` before the user answers. If you cannot ask the user, stop and report `SDD_SELECTION_REQUIRED`; do not install or code. After the user explicitly chooses, install only that provider; for Spec Kit pass `--host claude-code` or `--integration <key>`. If `mode: sdd_install_required`, reinstall only the Work Item's already-selected provider. Run bootstrap again after installation.
4. If the user stated the requirement inline and no requirement file exists yet, land it as a numbered document before doing anything else: run `{{CAO_COMMAND}} requirement next-id --title "<short title>"` — CAO creates the numbered file under `.orchestrator/requirements/` and prints its path. Edit that file with the requirement content, then `{{CAO_COMMAND}} start --requirement <path>`. CAO owns the numbering and the location; do not create the file yourself and do not put requirement documents anywhere else.
5. If an existing Work Item is active or resumable, continue it with CAO. Do not start a new requirement merely because the Agent or session changed.
6. Run `{{CAO_COMMAND}} next` and follow the reported next action.
7. When the next action is path selection, run `{{CAO_COMMAND}} path assess --profile <light|standard|comprehensive> --reason "<why>"`. Risk flags upgrade the profile automatically.
8. Follow the SDD's own native workflow for the selected path. Every profile requires it, including `light`. Do not jump from path selection straight to verification.
   - `{{CAO_COMMAND}} sdd workflow select --name "<native workflow>" --entrypoint "<native command or skill>"`
   - actually run that native workflow
   - `{{CAO_COMMAND}} sdd workflow execute --command "<command you actually ran>"`
   - `{{CAO_COMMAND}} sdd workflow complete --artifact <path>` — every path must sit inside the provider's own output directory
   Never pass a placeholder entrypoint (`echo`, `true`, `printf`). Never register source files you wrote yourself as native SDD artifacts.
9. Satisfy each required gate: `{{CAO_COMMAND}} gate satisfy --gate <name> --evidence "<what you did>"`. Verification gates are derived from CAO's own verification state; every other gate is yours to mark.
10. Record meaningful progress with `{{CAO_COMMAND}} checkpoint ...` and durable choices with `{{CAO_COMMAND}} decision ...`.
11. Run `{{CAO_COMMAND}} verify`, then `{{CAO_COMMAND}} done`. `{{CAO_COMMAND}} done` is blocked while the native workflow is incomplete or a required gate is pending.

Everything CAO stores under `.orchestrator/` except `requirements/` is derived state that CAO writes itself (`config.json`, `index.json`, `work/`, `sessions/`, `handoffs/`, `lifecycle/`); never edit those files by hand, and change that state through the matching CAO command instead.

Explicit user instructions remain authoritative. CAO coordinates continuity; it does not replace the project's SDD or the Agent's normal coding capabilities.

## Language policy

Read `.orchestrator/config.json` → `language.user`. Communicate with the user in that language and write all SDD artifacts and project documentation in the same language, including specifications, proposals, designs, plans, tasks, architecture documents, reviews, and summaries. Keep code symbols, API names, commands, standardized keywords, and file names unchanged where required. If the user explicitly requests a different language for a specific scope, follow that request for that scope. Use `{{CAO_COMMAND}} language show` to inspect the preference and `{{CAO_COMMAND}} language set --language <locale>` to change it.

If the user expresses an ongoing language preference that differs from the configured value, update it with `{{CAO_COMMAND}} language set --language <locale>` before producing more SDD artifacts.
