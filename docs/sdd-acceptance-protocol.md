# Command acceptance request and report

Inspect the complete request/report JSON Schemas, examples and the installed
executor template without an active Work:

```bash
cao sdd acceptance protocol
```

`CAO_SDD_ACCEPTANCE_REQUEST` contains a **JSON string**, not a filename. Parse it
once and iterate `request.declaration.criteria`. CAO freezes these inputs before
launching the command. Echo the current run identity, never reuse an old report.

```json
{
  "run_id": "sdda-current-run",
  "input_sha256": "64-character-sha256-from-the-current-request",
  "provider": "openspec",
  "inputs": {"work_id": "the-current-work", "snapshot_format": 3},
  "declaration": {
    "spec": "openspec/changes/add-demo/spec.md",
    "sources": ["openspec/changes/add-demo/spec.md"],
    "criteria": [{"id": "AC-1", "source": "openspec/changes/add-demo/spec.md", "kind": "requirement", "description": "The CLI returns the correct result"}],
    "executor": {"mode": "command", "entrypoint": "python3 scripts/verification/acceptance.py --checks acceptance-checks.json", "timeout_seconds": 300},
    "outputs": ["openspec/cao-acceptance/the-current-work/report.json"]
  }
}
```

Print exactly one report object to stdout. Diagnostics belong on stderr. `items`
must cover every declared criterion exactly once, and `evidence` is a **nonempty
array**, even for one file. Each referenced file must exist inside the repository.

```json
{
  "run_id": "sdda-current-run",
  "input_sha256": "64-character-sha256-from-the-current-request",
  "provider": "openspec",
  "items": [{
    "id": "AC-1",
    "status": "passed",
    "evidence": [{"path": "tests/cli.test.js", "reason": "Executed the criterion-specific CLI test; exit code 0"}]
  }]
}
```

This is a shape example, not a passing result to copy. Status must come from the
actual check. Exit 0 plus all passed items is required; exit 1 fails, exit 2 blocks,
and other nonzero exits indicate an execution error. Every file listed in
`declaration.outputs` must be written before the command exits. If reports are
only printed to stdout and stored in CAO, use `outputs: []`. Do not declare a file
that the executor never creates. Existing report files need CAO-recorded ownership.

The `executor_template` path returned by `protocol` is a standalone Python file.
Copy it to the project's verification directory. It executes argv arrays without
a shell, checks each criterion independently, writes registered reports and emits
the required JSON. An absent criterion mapping returns blocked. Populate a
project-owned checks file with meaningful commands and evidence, for example:

```json
{
  "AC-1": {
    "command": ["node", "--test", "--test-name-pattern", "correct CLI result", "tests/cli.test.js"],
    "evidence": ["tests/cli.test.js"],
    "timeout_seconds": 60
  }
}
```

Ensure the selected command actually exercises its criterion and fails on zero
tests; a command's exit code alone cannot establish semantic coverage. Review and
configure the declaration, explicitly reanchor, run `cao verify`, then run
`cao sdd acceptance run`. Changed scripts/check mappings require fresh verification.
Errors include stable codes and, for malformed report fields, a `diagnostic`
object such as `{"field":"$.items[0].evidence","expected":"a nonempty array..."}`.

## OpenSpec archive ordering

CAO recognizes a missing associated change only when exactly one matching native
archive directory exists and all currently bound artifact hashes match. It records
the old/new roots in `sdd.archive_relocations` and emits a lifecycle event. Known
task IDs, minimum counts and split-change associations are preserved. Symlinks,
changed artifacts, ambiguous archives and remaining tasks cannot certify closure.

For active Work, path relocation changes frozen inputs: review the migrated
sources, explicitly `cao reopen --reanchor --reason "Reviewed archived sources"`,
rerun verification and acceptance, then close. Reconcile never invents passed runs.
Completed Work retains its immutable acceptance result with `historical: true`;
that result describes completion time, not current repository freshness. Native
artifact references can still follow the recorded archive location. Reopening is
an explicit new validation action and must check current inputs again.
