# Startup and SDD selection

## Enter when

Bootstrap reports `sdd_select`, `sdd_install_required`, a language error, or a missing host adapter.

## Execute

1. Use the actual CAO command from project instructions or CLI `guidance.command`; `cao` in these guides is shorthand for that invocation.
2. For `mode: sdd_select`, ask the user to explicitly choose the provider and wait. Recommendations, task type, popularity, global CLIs and `auto` are not authorization. If the channel cannot ask, report `SDD_SELECTION_REQUIRED` and stop.
3. After the explicit choice, run `cao sdd install --provider <chosen-provider> --yes`, or `cao sdd select --provider <chosen-provider>` for an installed provider. For Spec Kit pass the actual `--host` or explicitly selected `--integration`; never default to all hosts. For `sdd_install_required`, repair only the Work's already-selected provider.
4. Rerun `cao bootstrap` after selection/install. Fix reported language configuration failures before proceeding.
5. Hooks load at process startup. Provision with `cao init --host <claude-code|codex|pi>`. If bootstrap reports `host_adapter.installed: false`, run `cao host install --host <host>`; it takes effect next session. Until then call resume/checkpoint/handoff and `cao sdd sync` at lifecycle transitions yourself.
6. To reduce legacy project instructions, preview `cao instructions update --host <host>`, inspect the proposed content, then apply with `--apply`. Only exact known legacy templates or a marked CAO block can be replaced; custom files stay untouched and backups retain previous bytes. Restart the host to load the new project instructions.

## Exit when

An authorized SDD is ready; rerun bootstrap and follow its current action.

## If blocked

Do not reinstall a different provider, invent a session ID, or begin product implementation to bypass startup.
