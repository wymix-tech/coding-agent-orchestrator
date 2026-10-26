# Command acceptance execution

Read only for `executor.mode: command`. Use the shared acceptance contract for
declaration/report fields; command-mode transport does not apply to Skill mode.

## Prepare the executor

`cao sdd acceptance protocol` returns `executor_template`, a standalone Python
file. Copy it to the project's verification directory. It runs argv arrays
without a shell, checks each criterion independently, writes all registered
reports and emits one report object. An absent criterion mapping blocks.
Populate a project-owned checks file with actual commands and evidence:

```json
{
  "AC-1": {
    "command": ["node", "--test", "--test-name-pattern", "correct CLI result", "tests/cli.test.js"],
    "evidence": ["tests/cli.test.js"],
    "timeout_seconds": 60
  }
}
```

Ensure the selected command really exercises its criterion and fails on zero
tests; an exit code alone cannot establish semantic coverage. Configure the
actual entrypoint, e.g. `python3 scripts/verification/acceptance.py --checks
acceptance-checks.json`. Review/reanchor changed scripts/check mappings and
rerun formal verification before acceptance.

## Run and report

Run `cao sdd acceptance run`. The command receives
`CAO_SDD_ACCEPTANCE_REQUEST` as a JSON STRING, not a file path. Parse it once,
evaluate its declared criteria, and echo its current identity in the report.
Print exactly one JSON report to stdout; diagnostics belong on stderr. Write
every registered output before exit, or declare `outputs: []` for stdout only.

Exit 0 plus all passed items qualifies; exit 1 fails, exit 2 blocks and other
nonzero exits indicate an execution error. CAO independently validates coverage,
identity, actual evidence/output files and current frozen inputs. Do not invoke
`begin`/`submit` to substitute for the configured command execution.

For criterion-checks-v1 also supply `selector` (the relevant test/assertion/check
identity) and `assertion` (the behavior to verify) in every mapping. A bare command
exit is insufficient. The executor emits request-bound `check` receipts per item;
logs remain on stderr and stdout remains one JSON report. An unmapped item blocks.
