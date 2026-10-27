# Command acceptance execution

Applies to unmigrated lifecycle contract 1 only. New Work uses [native completion evidence](native-lifecycle.md); do not configure a second CAO functional executor.


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
    "selector": "cli.test.js: correct CLI result",
    "assertion": "The command returns the specified result",
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

## Share an execution, retain individual outcomes

Mappings may set the same nonempty `shared_run` with identical argv and timeout.
The template executes that command once per acceptance request. Each mapping
still names its own selector, assertion and evidence. For a command returning
several assertion results, set each mapping's `result_key` to its actual result:

```json
{"checks":{"login":{"status":"passed","observed":"valid credentials returned a session"},"lockout":{"status":"failed","observed":"sixth invalid attempt was accepted"}}}
```

For example, AC-LOGIN can select `login`, while AC-LOCKOUT selects `lockout` from
the same run. The command emits this JSON on stdout and diagnostics on stderr;
its checks must actually execute the assertions. With command exit 0, the first
item passes and the second fails; acceptance fails. A missing result key blocks
that item. Nonzero command exits cannot be overridden with a passed assertion.
The per-item receipt includes the current request run, selector, assertion,
observed result and shared execution identity. This mapping does not certify the
semantic adequacy of arbitrary project scripts; review the assertions themselves.
