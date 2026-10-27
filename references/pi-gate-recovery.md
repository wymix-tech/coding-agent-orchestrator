# Pi gate failure and recovery

A successful `allow` or `deny` from CAO remains authoritative. An enabled
project with no trustworthy pre-tool decision blocks write/edit/patch tools,
unknown tools and arbitrary shell commands. This includes process failures,
missing executables, malformed responses, oversized output and timeouts.
The model receives the fault and concrete recovery guidance; repeated guidance
and UI diagnostics are deduplicated per session.

A project is enabled when its `.orchestrator` state directory is present,
including when its configuration is missing or corrupt. Nested working
directories are checked up to their Git boundary. Permission/probe errors are
unknown and therefore enforced. A session/directory once observed enabled does
not downgrade merely because state was deleted. A genuinely uninitialized
project without state is not enrolled: bridge failures are diagnosed but do
not install a gate automatically. Restarting after deleting all state loses
this in-memory enrollment; use normal filesystem permissions to protect state.

When the bridge is unavailable, `read`, `ls`, `grep`, `find`, `search_code`,
bounded `contact_supervisor` messages (`need_decision`/`progress_update`) and
`subagent` status requests remain available. These exact bash commands also have
an offline exception:

```text
pwd
cao --help
cao status
cao host status
cao host status --host pi
cao host install --host pi
```

The running adapter's own absolute bundled `cao` launcher (direct or through
`sh`, with literal path quoting) accepts the same exact CAO suffixes. `status
--work <id>` also accepts a literal alphanumeric/dot/underscore/hyphen Work ID.
An arbitrary path with the basename `cao` is not trusted.

There is no general shell parser, alternate working-directory or environment
override. Other arguments, whitespace variants, aliases,
redirects, pipelines, newlines, substitutions and chained commands are not
exceptions. This fallback deliberately does not reproduce the healthy core's
broader command classifier. A normal `deny` never uses the offline exception.
The commands may still fail if the CLI is broken, but are permitted so repaired
installations can be inspected/rewired without bypassing product gates.

- `STATE_LOCK_TIMEOUT`: let the owning operation finish and retry. Do not delete
  `state.lock`; its persistent filename is not ownership evidence.
- Missing CLI/interpreter: inspect using read tools and repair the installation
  from the host terminal, then inspect status and reinstall host wiring if needed.
- Protocol/output errors: inspect bounded CLI diagnostics and repair the backend.
- Invalid project configuration: inspect state, use supported configure commands
  once the core responds, and follow the existing explicit reanchor/approval flow.
- `SNAPSHOT_*`: inspect the named path and declared outputs. A restart does not
  repair an invalid snapshot scope. Healthy core pre-tool inspection/control
  commands are classified before evidence refresh, so reads and parent messages
  cannot be blocked by snapshot computation. Business writes still fail closed
  on a genuine snapshot fault. JVM config resources under declared Maven/Gradle
  resource output directories are generated output when their build manifest is
  present; real source resources remain hashed, tracked outputs are rejected,
  and links/explicit rule or verification inputs retain their protection.
  `cao status` returns stored Work state with `inspection_only: true` and the
  snapshot diagnostic when refresh is unavailable; it does not certify freshness.

After recovery continue the existing SDD step's build/test repair loop. Native
failure alone never advances CAO to verification. Complete native obligations,
bind current completion reports and check conformance before `cao verify`.

The bridge deadline is 8 seconds, leaving headroom over the core's default
5-second state-lock wait. There are no automatic event retries. A timeout
terminates the owned POSIX process group; Windows requests process-tree cleanup.
Notification/result events keep their non-blocking semantics and preserve tool
result payloads. This is orchestration enforcement, not an OS sandbox for tools
or verification scripts.
