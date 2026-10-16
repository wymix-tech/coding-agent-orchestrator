<!-- CAO:BEGIN v2 -->
# Coding work bootstrap

Use CAO first for non-trivial repository coding work. Invoke its installed command exactly:

```
{{CAO_COMMAND}}
```

1. Run `{{CAO_COMMAND}} bootstrap --repo . --host <host>` before code changes.
2. Read the returned `guidance.skill` once, then only `guidance.read_before_action` for the current
   stage. Paths are resolved by CAO; do not guess installation paths or preload all references.
3. Follow bootstrap's `next`; use `{{CAO_COMMAND}} next` during execution and follow its `action`.
   After startup/setup changes rerun bootstrap. If the command/resources are missing, report and
   repair the installation; do not substitute an invented path or workflow.
4. For `mode: sdd_select`, ask the user which Provider they want and wait for explicit selection
   before installing/selecting. A recommendation is not authorization.
5. Resume current Work across sessions. Create Work before product code/configuration/tests/setup;
   empty verification configuration and failing tests do not prevent start. Follow the stage guide
   for numbered intake, baseline setup, assessment, native execution and recovery.
6. Preserve SDD-native prerequisites, approvals, whole-requirement scope and independent reviews.
   Read installed native Skills fully; rendering loads instructions, not completed execution.
7. Record progress/decisions and explicit handoffs. Only successful `{{CAO_COMMAND}} done` closes
   Work; native done or passing tests alone cannot. Never push without authorization.

Everything under `.orchestrator/` except `requirements/` is derived state (`config.json`, `index.json`,
`work/`, `sessions/`, `handoffs/`, `lifecycle/`); never edit those files by hand. Use CAO commands.
Follow the user's explicit instructions and configured `language.user` for communication/documents.
Hooks load at host startup: a missing adapter must be installed for the next session; meanwhile call
resume/checkpoint/handoff and native sync explicitly as directed by the current guide.
Supported hosts: claude-code, codex, pi. Project custom instructions outside this block remain owned
by the user. Preview legacy upgrades with `{{CAO_COMMAND}} instructions update --host <host>`.
<!-- CAO:END -->
