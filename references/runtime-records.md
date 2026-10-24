# Task runtime records

Use `cao checkpoint`, `decision`, `block`, `unblock`, `verify`, and the existing
SDD commands normally. CAO records their history automatically. Never hand-write
runtime JSON or infer an approval from a diagnostic record.

Work Items remain authoritative for task and gate state. Runtime records in
`.orchestrator/runtime/<work-id>/` contain start intentions, executor receipts,
bounded diagnostic logs, and rebuildable result/evidence projections. Use the
original Work ID, without adding another `work-` prefix.

## Inspect an execution

```sh
cao runtime status --work <id>
cao runtime events --work <id> --limit 50
cao runtime runs --work <id> --limit 50
cao runtime show --work <id> --run <run-id>
cao runtime logs --work <id> --run <run-id> --command command-0001 --stream stderr --tail 100
cao runtime recover --work <id> --check
```

`events` merges authoritative lifecycle history and host observations; it does
not trust the disposable `events.jsonl` cache. `events` and `runs` return
`next_cursor`; pass it with `--cursor` to continue (limit 1–500). Log tail reads
at most 1 MiB and 1–1000 requested lines, and reports when that read is limited.
No runtime query executes a command, approves a gate, re-intakes a requirement,
or resets a phase. An explicit recovery check appends a diagnostic
`run.recovery_checked` observation without changing the Work. Legacy tasks without runtime records are supported.

## Interpret recovery

- `orphaned_start`: a start intention exists without a matching authoritative run.
  The outcome is unknown; inspect real processes and artifacts.
- `running`: the local executor's OS owner lock is still held. A PID alone is
  never taken as evidence of a live owner; remote/unavailable owners are unknown.
- `outcome_unknown`: a running authoritative record has no receipt, or a Skill
  submission has no observable controlled process. Inspect before deciding on a
  new invocation.
- `result_uncommitted`: the executor wrote a receipt, but no official finished
  result was found. Exit code 0 does **not** authorize a pass.
- `projection_missing`: authority contains a terminal result; rebuild can repair
  its missing or damaged display projection.

Always show `process_status` separately from `admission_status`. For example,
exit code 0 can coexist with stale or superseded admission. A Skill or workflow
submission is an `agent_declaration`, not proof that CAO launched a subprocess.
Host observations likewise do not become executor receipts. Historical passes
are historical admission, not a certification of current input freshness; use
the ordinary gate/status/verification commands for that determination.

If a new run is appropriate, use the existing `cao verify` or SDD acceptance
entrypoint, which allocates a new ID. Recovery never retries side effects.

## Capture and retention

Optional configuration in `.orchestrator/config.json` (old configurations use
these defaults):

```json
{
  "runtime": {
    "logs": {
      "mode": "bounded",
      "segment_bytes": 1048576,
      "max_bytes_per_stream": 8388608
    },
    "retention": {
      "max_bytes_per_work": 134217728,
      "closed_work_days": 30
    },
    "host_observation": "meaningful",
    "max_observations": 10000,
    "max_observation_bytes": 8388608,
    "redact_values": []
  }
}
```

`logs.mode=off` disables extra stdout/stderr files, while keeping critical run
facts, existing audit events, and safe bounded tails. Redaction remains enabled.
Use `redact_values` for additional known, nonempty, single-line secrets up to
16 KiB each; do not copy environment variables wholesale into configuration.
Common credential assignments, Authorization, command flags, and URL credentials
are sanitized. Arbitrary business secrets cannot be recognized automatically.
Lines over 16 KiB are omitted conservatively, including unterminated output.
Raw bounded stdout used by the SDD JSON protocol is independent of redaction.

Logs rotate and stop at their per-stream budget. The Work quota is a soft
capture ceiling: new optional bodies stop, while critical metadata remains
writable. Reservations serialize concurrent capture budgets. Observation quotas
reserve space before writing; an interrupted write may conservatively consume
quota. Observations stop with a diagnostic at capacity, without changing an
allow/deny decision. `rebuild` recalculates their counters.

```sh
cao runtime rebuild --work <id>
cao runtime prune --work <id> --dry-run
cao runtime prune --work <id> --apply
```

Prune defaults to preview. It deletes only expired diagnostic `.log` bodies for
completed tasks. Current verification/acceptance runs, live owners, and unresolved
runs are protected. Start/receipt/result metadata, lifecycle history, Work Items,
handoffs, and Provider reports are retained. A `pruned.json` marker precedes
body deletion so an interrupted cleanup remains explainable.

## Host coverage

PI forwards native `toolCallId`, session identity, and `isError` without changing
its tool results or compact context display. Claude Code/Codex hooks use the
fields actually provided; missing IDs are marked `partial`. A pre-tool binding
routes a late result back to the original task after a task switch. Unknown
subagent lineage and unmatched results use `_unassigned`; they are never guessed
from the current active task. Routing metadata shares the bounded unassigned
observation spool. Ordinary read/list and CAO control commands are skipped;
writes, build/test/workflow actions, failures, and denials are meaningful.

The diagnostics cover connected hooks and controlled runners only. They do not
certify unobserved operations, contain full conversations, or provide protection
against malicious local processes. POSIX-created diagnostic files are private
(mode 0600); Windows uses the current account's filesystem permissions.

Configure log capture with `cao runtime configure --mode bounded --segment-bytes 1048576 --max-bytes-per-stream 8388608`. Retention uses `--max-bytes-per-work` and `--closed-work-days`. Redaction fields remain installation-managed; no agent command exposes them. Do not edit derived config during a Work.
