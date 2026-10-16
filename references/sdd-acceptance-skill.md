# Native Skill acceptance execution

Read only for `executor.mode: skill`. Use the shared acceptance contract for
declaration/report fields; do not treat command-mode environment/stdout as the
Skill-mode interface.

## Begin, execute, submit

1. Configure the actual installed native acceptance Skill identity as
   `executor.entrypoint`, its native criteria/sources and exact Work-scoped outputs.
   Review/reanchor the declaration and run formal verification.
2. Run `cao sdd acceptance begin`. Preserve its returned `request`, including
   current run ID, input digest, Provider, declaration and frozen inputs.
3. Read and execute the exact installed Skill, including all independent-reviewer
   requirements or its native external-session/human-return fallback. Rendering
   instructions only loads them; it does not execute acceptance.
4. Evaluate every criterion, preserve authentic evidence and write the complete
   report to a declared Provider-owned output file. Write every declared output.
5. Run `cao sdd acceptance submit --run <current-run-id> --report <registered-output.json>`.

The report has the same identity, exact criterion coverage and evidence-array
requirements as command mode. Submit never runs the native Skill for you and
cannot prove reviewer independence. Do not fabricate reviewer execution, reports
or passed criteria. If native independent execution is unavailable, preserve
that blocker or use the native fallback.

A newer run supersedes the old ticket. Changed inputs make the old report stale;
review/reanchor/reverify as directed and begin a fresh run. Do not send a late
old result against a new Work/run or use `acceptance run` for Skill mode.
