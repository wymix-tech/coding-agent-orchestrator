# Create or resume Work

## Enter when

Bootstrap reports `select`, or an existing Work must be resumed. Never repeat intake merely because a session or agent changed.

## Execute

1. Inspect `cao list` and `cao requirement list`; use the existing applicable Work/requirement.
2. Resume with `cao resume --host <host> --session <real-session-id>`. Reactivate suspended work with `cao resume --work <id> --reactivate`, then resolve blockers. Do not invent missing runtime IDs.
3. For a new inline requirement, run `cao requirement next-id --title "<short title>"`. Fill the returned numbered path under `.orchestrator/requirements/`, then run `cao start --requirement <returned-path>`.
4. If a requirement file already exists, including a native SDD requirement, use that file directly. Do not create a duplicate or move it solely for CAO. Provider output directories being writable is not a direction to create a second intake document there.
5. Use `--title` for readable Work titles when native filenames are fixed (e.g. `SPEC.md`). Empty verification configuration and failing tests do not prevent start.
6. Rerun bootstrap after start/resume. Create an active Work before product code, configuration, tests or verification scaffolding. CAO requirement intake and provider-native outputs stay writable without Work; direct managed-state edits remain denied. Never move product code into native outputs to bypass the guard.

### Progress and durable choices

After completing a meaningful slice, update CAO:

```bash
cao checkpoint \
  --step "<current step>" \
  --next "<specific next action>" \
  --summary "<what is complete and what remains>"
```

Record durable architectural or product choices immediately:

```bash
cao decision --decision "<choice>" --reason "<reason>"
```

A blocker that prevents useful continuation should be explicit. Use `--suspend` when the work
should leave the active slot:

```bash
cao block --reason "<why blocked>" --suspend
```

### Sessions and transfer

Use `cao sessions list` and `cao sessions show --id <id> --host <host>` to inspect runtime Session records. Session state is stored only in `.orchestrator/sessions/`; Work Item progress remains in the Work Item file. A stale activity label does not mean the Session has ended.

Host adapters create Sessions automatically, but only when the adapter is actually installed. `cao bootstrap`
and `cao start` therefore attach the runtime Session as a fallback whenever the host and its native session id
are visible to the process (`CAO_HOST`, `CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, `PI_SESSION_ID`). If those
are absent, CAO records nothing rather than inventing an `unknown` Session. Prefer `cao resume --host <host>
--session <id>` so the Session is bound explicitly.

Stop/turn_end may save a Handoff but do not end a Session; before_settle only reminds. Actual session_end
(or distinct subagent_stop) ends it. Claude Code uses SessionEnd and Pi 1.1.0 uses session_shutdown;
Codex has no verified native exit hook. Missing native IDs report diagnostics, never a shared default Session.
For an intentional transfer, create an explicit handoff; on actual exit add `--end-session` if needed:

```bash
cao handoff --session <id> --host <host> --reason "transfer to another agent"
```

Every handoff is retained as an immutable history record. Inspect history with `cao handoff list`
and a specific snapshot with `cao handoff show --id <handoff-id>`. The next Agent should consume the
latest handoff through `cao resume` rather than rebuilding context from chat history. Switching hosts
does not create a new Work Item.

## Exit when

The current Work and handoff are loaded. Continue from its state, decisions, blockers and next action.

## If blocked

For a missing explicit requirement path, restore/create that intended file; do not guess another path. Use the recovery guide for actual scope changes.
