# Native review evidence and loop recovery

## Failure and behavior

Pi's child final message can contain a trailing `acceptance-report`, while its output file contains findings alone. A child can also write detailed findings and return a shorter summary. Comparing those files to the final-message hash rejected valid independent review and encouraged repeated rounds.

The host now binds a ticket-marked, observed child before inspection using the existing parent/model/tools/input validators. A failed startup binding blocks inspection and exposes a diagnosis; no historical identity is invented. Explicit attach remains supported for independent external hosts.

The collector preserves the original message and separately extracts only the explicit trailing acceptance envelope (collector version 1). Successful, host-observed direct write tools can attest an independent findings file: its bytes must match the write payload and the path must be a provider artifact. Shell-created outputs without an observed write receipt are not silently certified; use the exact observed final message instead. Collected files live under the provider review bundle and remain subject to byte hashes and path boundaries. A changed collected snapshot is rejected.

New tickets require both the complete findings file digest and each finding's original-content digest. `review draft` writes this metadata. Copying numbered judgments between different outputs fails even if their IDs happen to match. Old tickets retain compatibility; they are not retroactively certified.

## Operator recovery

1. Run `cao sdd review diagnose --layer <layer>` with the active Work. It reports the binding, session status, original message digest, artifact integrity, and effective phase: prepared, bound, collected, classified, or resolved.
2. Use an intact collected artifact path with `cao sdd review draft --layer <layer> --source <path>`. Fill the generated report's verdict, disposition, and evidence without replacing source or content digests.
3. For legacy evidence, export the exact bound reviewer's final message (including its original acceptance envelope) into a provider artifact, then run `cao sdd review recover --layer <layer> --source <original-message>`. Recovery checks the previously observed full-message digest before extracting findings. Edited summaries and unrelated files cannot be recovered as originals. No execution binding is added retrospectively.
4. Discard classifications built against another output. Classify all active layers before patches. Dispatch uses classify/resolve; oneshot uses classify/submit with a separate resolved JSON report.
5. Restart only when trusted original evidence cannot be recovered or inputs actually changed. Repeated unchanged output-provenance failures block restart and point to diagnosis/recovery.

Diagnostic and recovery commands use the existing exact CAO control-command policy; arbitrary Python/Perl/shell expressions remain conservatively gated. A denied diagnostic shell command now points to the supported diagnosis command. No broad interpreter exception is introduced.

Fault identity excludes command action and generated run ID, so moving between draft/classify or restarting does not reset the same input/layer fault count. Corrected evidence changes the concrete request identity and permits another attempt. Diagnosis and recovery stay accessible while a failed request is paused.

## Validation

Regression coverage includes acceptance envelopes, distinct detailed artifacts and summaries, modified snapshots, wrong-ticket output, cross-round finding-content mismatch, exited reviewers, legacy recovery, startup binding, observed writes, and retries across actions/runs. Existing integration fixtures explicitly include the new digest metadata.

The uploaded demo archive is diagnostic history, not the live demo project. Repository changes provide recovery tooling; they do not claim to have modified the user's live `.orchestrator` state.

### Final local results

- Python discovery: 1,233 cases, including 24 duplicate imported identities; all 1,209 distinct test identities were accounted for against the discovery list.
- 1,205 distinct cases passed; four environment-dependent cases skipped (Maven/JDK, Gradle/JDK, Go, and the optional official BMAD v6.12.4 source checkout). No remaining failures or missing identities.
- Tests were executed in module groups, with `PYTHONPATH=tests:.` for modules importing sibling test fixtures. The initial grouped launch lacked that path for some modules; all affected modules were rerun successfully. A two-layer fixture was updated to include the now-required source digest and rerun successfully.
- `make check`: passed.
- `npm run check:pi`: passed.
- `npm run test:pi`: 39 passed, zero failed/skipped, including the actual Pi runtime contract test.
- The local commit trees were compared with the GitHub-created commit trees and match exactly. GitHub Actions cross-version/platform jobs are tracked separately from these local results.
