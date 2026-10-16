# Startup and SDD selection

## Enter when

Bootstrap reports `sdd_select`, `sdd_install_required`, `host_restart_required`, a language error, or a missing host adapter.

## Execute

1. Use the actual CAO command from project instructions or CLI `guidance.command`; `cao` in these guides is shorthand for that invocation.
2. For `mode: sdd_select`, ask the user to explicitly choose the provider and wait. Recommendations, task type, popularity, global CLIs and `auto` are not authorization. If the channel cannot ask, report `SDD_SELECTION_REQUIRED` and stop.
3. After the explicit choice, run `cao sdd install --provider <chosen-provider> --yes`, or `cao sdd select --provider <chosen-provider>` for an installed provider. For Spec Kit pass the actual `--host` or explicitly selected `--integration`; never default to all hosts. For `sdd_install_required`, repair only the Work's already-selected provider.
4. After a new installation or upgrade, finish language configuration and STOP. Report `HOST_RESTART_REQUIRED`, tell the user to restart the host in this project, and end the turn. Do not rerun bootstrap to bypass the boundary or continue intake/planning/development. If hooks are missing, install the matching CAO host adapter before restarting. Only the corresponding host's next primary SessionStart acknowledges resource loading; for Pi it must have native reason `startup` or `reload`, not `new`, `resume` or `fork`. A host restarted into the same conversation may keep its original ID. Subagents, new IDs supplied to bootstrap, repeated installation and bootstrap do not acknowledge it. After restart, rerun bootstrap and resume the existing Work. Selecting an existing provider or an unchanged `already_installed` result creates no new restart requirement. Repair language errors with `cao language apply` before proceeding.
5. Hooks load at process startup. Provision with `cao init --host <claude-code|codex|pi>`. If bootstrap reports `host_adapter.installed: false`, run `cao host install --host <host>`; it takes effect next session. For a pending provider restart, wire the adapter before restarting. For projects without that pending boundary, call resume/checkpoint/handoff and `cao sdd sync` at lifecycle transitions yourself until hooks load.
6. To reduce legacy project instructions, preview `cao instructions update --host <host>`, inspect the proposed content, then apply with `--apply`. Only exact known legacy templates or a marked CAO block can be replaced; custom files stay untouched and backups retain previous bytes. Restart the host to load the new project instructions.

## Exit when

An authorized SDD is ready; rerun bootstrap and follow its current action.

## If blocked

Do not reinstall a different provider, invent a session ID, or begin product implementation to bypass startup.
