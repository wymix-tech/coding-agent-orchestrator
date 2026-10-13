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
1. Identify the current host (`claude-code`, `codex`, or `pi`) and run `{{CAO_COMMAND}} bootstrap --repo . --host <host>`.
2. If `bootstrap` reports `host_adapter.installed: false`, run `{{CAO_COMMAND}} host install --host <host>`. Host hooks load when the host process starts, so this takes effect from the next session; until then call `{{CAO_COMMAND}} resume`, `{{CAO_COMMAND}} checkpoint`, and `{{CAO_COMMAND}} handoff` yourself.
3. If `bootstrap` reports `mode: sdd_select`, treat it as a hard authorization boundary: ask the user which SDD they want and wait for an explicit answer. Never choose based on a recommendation, task type, popularity, global CLI availability, or Agent preference. Do not run `{{CAO_COMMAND}} sdd install` or `{{CAO_COMMAND}} sdd select` before the user answers. If you cannot ask the user, stop and report `SDD_SELECTION_REQUIRED`; do not install or code. After the user explicitly chooses, install only that provider; for Spec Kit pass `--host <host>` or `--integration <key>`. If `mode: sdd_install_required`, reinstall only the Work Item's already-selected provider. Run bootstrap again after installation.
4. If bootstrap reports `mode: verification_setup`, establish the project's verification baseline before feature development:
   - Existing project: analyze product manifests, source, tests and CI; reuse meaningful checks. Host assets, `.agents`, `.pi`, SDD tools/outputs and caches are not product-stack evidence; current requirements remain relevant.
   - New project: use the user's explicit stack. Otherwise ask the user for language/framework/build/test choices and wait; never guess a default stack.
   - Generate project-owned wrappers or reuse existing scripts. Zero tests, missing tools or missing dependencies must fail/block, never fake success with `echo`/`true`. Preserve the user's selected language, framework and test runner; do not replace JUnit 5 with a main program when dependencies are missing. Java/Jupiter needs JUnit5-capable Surefire and failure on zero tests.
   - Use `{{CAO_COMMAND}} verification configure --test-script "<actual test wrapper>" --verify-script "<actual general check>"`, omitting inapplicable scripts. Register a wrapper that covers both general checks and tests only once as a test script. Never hand-edit config or use `init --force` for this step. Do not overwrite scripts or install dependencies automatically.
   - Rerun bootstrap. Configuration readiness is not verification success; creating the initial skeleton/tests is allowed. Existing Work needs explicit acceptance review/reanchor, not silent replacement.
   If the user stated the requirement inline and no requirement file exists yet, land it as a numbered document before creating a Work Item: run `{{CAO_COMMAND}} requirement next-id --title "<short title>"` — CAO creates the numbered file under `.orchestrator/requirements/` and prints its path. Edit that file with the requirement content, then `{{CAO_COMMAND}} start --requirement <path>`. CAO owns the numbering and the location; do not create the file yourself and do not put requirement documents anywhere else.
5. If an existing Work Item is active or resumable, continue it with CAO. Do not start a new requirement merely because the Agent or session changed.
6. Run `{{CAO_COMMAND}} next` and follow the reported next action.
7. When the next action is path selection, run `{{CAO_COMMAND}} path assess --profile <light|standard|comprehensive> --reason "<why>"`. Risk flags upgrade the profile automatically.
8. Follow the selected provider's installed instructions and preserve its real output paths and configured language. Read the full installed SKILL.md; run a renderer only if that Skill requires one, using its exact path. Rendering loads instructions, it does not finish the workflow. Clarify user-visible delivery choices and declare applicable risks before implementation.
   - `light`: write a minimal native SPEC with explicit acceptance criteria, then `{{CAO_COMMAND}} sdd spec bind --spec <provider-spec.md> --criteria-section "<exact heading>"`. Workflow registration, plans/no-plan and independent final SDD acceptance are optional; do not fabricate a checked plan to close.
   - BMAD light: invoke the installed `bmad-build`, let it select its route and follow its native reviewers. A `route: oneshot` SPEC with nonempty `Intent` and `Implementation Notes` binds with `{{CAO_COMMAND}} sdd spec bind --spec <native-spec.md>` without criteria selectors; CAO uses native Intent and current requirement, does not claim an explicit acceptance checklist, and never restores deleted Tasks & Acceptance. Explicit standards still override this fallback.
   - OpenSpec light: invoke the installed `/opsx:propose` then `/opsx:apply` agent commands; follow the active schema (default apply requires tasks and upstream artifacts). Keep native documents short; `/opsx:ff` generates artifacts and needs the expanded commands installed. Only an explicitly configured/validated custom schema can reduce its requirements. `openspec apply` is not a terminal implementation command.
   - Spec Kit light: for an existing bug use `speckit-bug-assess` → `speckit-bug-fix` → `speckit-bug-test` only when its extension is installed; bind the assessment's explicit expected behavior as repair criteria. For a small new feature follow the installed specify/plan/tasks/implement Skills (and converge where supported), preserving required `plan.md` and `tasks.md`. Do not run `specify implement` or auto-install the bug extension.
   - `standard` / `comprehensive`: select the actual installed native workflow with `{{CAO_COMMAND}} sdd workflow select --name "<native workflow>" --entrypoint "<native command or skill>"`, follow it, record execution with `{{CAO_COMMAND}} sdd workflow execute --command "<actual invocation>"`, then `{{CAO_COMMAND}} sdd workflow complete --artifact <provider-output> --plan <related-tasks.md>` (or `--plan-item <sprint-status.yaml>#<id>`, or `--no-plan` when no plan applies). If the user changes the workflow, select the new workflow before registering its execution.
   Never register a placeholder entrypoint or product source files as native artifacts. Follow native independent reviewer requirements; use the specified human-return path when delegation is unavailable, never replace a reviewer with your own findings.
9. Mark observed manual review gates with `{{CAO_COMMAND}} gate satisfy --gate <name> --evidence "<what you did>"`. Verification/tests and `sdd_acceptance` are automatic and cannot be marked manually.
10. Record meaningful progress with `{{CAO_COMMAND}} checkpoint ...` and durable choices with `{{CAO_COMMAND}} decision ...`.
11. Run `{{CAO_COMMAND}} verify`. For standard/comprehensive, first configure native acceptance via `{{CAO_COMMAND}} sdd acceptance configure --declaration <repo-json>`, explicitly review/reanchor its sources with `{{CAO_COMMAND}} reopen --reanchor --reason "<reviewed scope>"`, and verify the resulting inputs. Then execute `{{CAO_COMMAND}} sdd acceptance run` (command mode), or `{{CAO_COMMAND}} sdd acceptance begin` and follow the installed Skill before `{{CAO_COMMAND}} sdd acceptance submit --run <id> --report <registered-output.json>` (Skill mode). Follow the CLI's current next action. Run `{{CAO_COMMAND}} done` only when all applicable checks are current and blockers resolved.

Everything CAO stores under `.orchestrator/` except `requirements/` is derived state that CAO writes itself (`config.json`, `index.json`, `work/`, `sessions/`, `handoffs/`, `lifecycle/`); never edit those files by hand, and change that state through the matching CAO command instead.

Explicit user instructions remain authoritative. CAO coordinates continuity; it does not replace the project's SDD or the Agent's normal coding capabilities.

## Language policy

Read `.orchestrator/config.json` → `language.user`. Communicate with the user in that language and write all SDD artifacts and project documentation in the same language, including specifications, proposals, designs, plans, tasks, architecture documents, reviews, and summaries. Keep code symbols, API names, commands, standardized keywords, and file names unchanged where required. If the user explicitly requests a different language for a specific scope, follow that request for that scope. Use `{{CAO_COMMAND}} language show` to inspect the preference and `{{CAO_COMMAND}} language set --language <locale>` to change it.

If the user expresses an ongoing language preference that differs from the configured value, update it with `{{CAO_COMMAND}} language set --language <locale>` before producing more SDD artifacts.
