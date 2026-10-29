# Receipt-backed native completion reports

Reserve a new provider-owned JSON output **before** executing native validation:

```sh
cao lifecycle report-prepare --output openspec/security-check.json
cao lifecycle execute --command 'python security_checks.py' --report openspec/security-run.log
cao lifecycle report-draft --output openspec/security-check.json --log openspec/security-run.log --check-id security-1 --description 'Native security criterion' --gate security_verification
```

Use the active provider's output directory and the actual declared validation
command. Preparation records the output path so creating the report does not
invalidate its own code snapshot. Existing source files cannot be registered
retroactively; repeated preparation of the same reserved output is idempotent.
Drafting requires a current successful execution receipt for the referenced log.
The draft's overall and individual check statuses remain `pending`.

Inspect actual criterion results, complete the report judgments and evidence,
then bind the report through `cao lifecycle bind --report <path>`. Conformance
still validates scope, hashes, gates and receipt freshness. Draft generation
never grants a pass, overwrites an existing judgment, or fabricates old receipts.

For an old unsupported report, retain it, reserve a new output, execute current
validation and create a new draft. Failed validation stays failed and must be
repaired before producing passing evidence. Continue `cao conformance check`,
`cao verify`, and `cao done` only when each command's prerequisites hold.
