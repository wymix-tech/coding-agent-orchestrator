# Delegation belongs to the installed plugin

Pi does not supply a universal subagent or workflow protocol. CAO prepares
immutable reviewer prompts and validates observed independent sessions and
original outputs. A project explicitly selects its installed plugin contract;
registration alone does not prove installation, version, tools, isolation, or
execution success. Inspect the plugin's effective schema and child hooks first.

Use `cao sdd review prepare --external-session --instructions <native-file>`
for implementations that do not use the supported Markdown agent-definition
format. `--agent-definition` inspects that specific format, not every plugin's
configuration. Independent reviewers must still use the CAO host adapter.

`cao delegation register --manifest <repo-relative-json>` records a declarative
adapter. `cao delegation show` lists registered contracts. Then use
`cao delegation launch --plugin <id> --agent <installed-agent> --work <id>` to
obtain the exact tool name and input to execute. It emits only unbound, running
review layers; retain existing bindings when resuming. All final outputs must
be collected before classification. CAO never silently launches a plugin.

This is an illustrative contract, **not a shipped or auto-detected Pi plugin**:

```json
{
  "version": 1,
  "plugin": "my-installed-plugin-version",
  "tool": "delegate_jobs",
  "match": {"operation": "dispatch"},
  "request": {},
  "tasks_path": "jobs",
  "prompt_field": "message.text",
  "agent_field": "worker.name",
  "blocking_path": "execution.mode",
  "blocking_value": "wait",
  "forbidden_task_fields": ["detached"],
  "result_fields": {"run_id": "job.handle", "session_id": "observer.session"}
}
```

Dotted paths address object fields. Another plugin may use `subagent`,
`args.tasks`, `task`, `agent`, and `async: false`; register those fields only if
that is its actual contract. Matching must be unambiguous. Explicit blocking
mode, frozen prompt bytes, and forbidden per-task overrides are checked before
review dispatch. Unregistered review transports fail with actionable guidance.
Script-only workflows cannot be represented by this data adapter; use observed
independent external sessions instead of guessing a script API. No plugin is
installed, enabled, or required merely because the host is Pi.

`cao delegation decode --plugin <id> --result <json>` maps returned identifiers
without binding them. A plugin run handle is not a CAO session ID. Attach only
independent sessions observed by the host, preserving original reviewer outputs;
a mapped ID or plugin-provided success flag never grants completion authority.
