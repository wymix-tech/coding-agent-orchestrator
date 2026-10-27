# Bootstrap identity, independent baseline and requirement recovery validation

Date: 2026-10-27 (Asia/Shanghai)
Base: main `0030f5c3e9c7957d3c235c13e9f5ef5b1730ab11` (PR #30).
Branch: `fix/bootstrap-identity-baseline-recovery`.

## Completed behavior

- Project identity uses `project setup identity --rule <rule>` and one user answer at a time. Each field retains host-observed provenance, validates built-in field syntax before saving, and survives host/session changes. Built-in and explicit plans inherit collected values and individual sources. User-entered identities are not replaced by inferred defaults.
- Scaffold initialization and the independent project baseline have separate readiness records. Scaffold success alone cannot activate a business Work. `verification materialize` persists existing project commands under `.orchestrator/scripts/verification/<scope>/`; alternatively the existing prepare/write/configure protocol supports custom wrappers. A successful current project probe is required before activation/resumption.
- Materialized scripts retain original command identities. Original script changes, wrapper changes, missing inputs and changed standards invalidate readiness. Re-declaring an unchanged original command preserves its wrapper. Existing Work progress and baseline approval/reanchor requirements remain intact; the actual migration owner may prepare its affected baseline while unrelated Work stays blocked.
- Requirement previews return a token binding business state, candidate bytes and request. Storage revisions, outbox bookkeeping and host attribution alone do not invalidate it. Changed business state or candidate content is rejected. No guessed revision increments or compound-shell bypasses are needed.
- New and queued Work retain anchored requirement bytes. Explicit amend handles accidental in-place edits while preserving the original in history. Legacy Work can supply `--original <recovered-file>`; the original must match the anchored hash. Previews do not rewrite the source, and journal recovery retains existing conflict checks.
- CLI, host recovery guidance and reference documents direct requirement changes to amend rather than the obsolete same-Work `start --replace` loop. CAO initial risk assessment is distinguished from SDD detailed business clarification.

## Validation results

- Complete Python suite: 93 test files, 1,134 cases; 4 environment/platform skips; no remaining failures. The suite was run per file, with affected files rerun after corrections. Test counts include the eight new session-regression cases.
- New regression suite: 8 passed, including sequential/resumed input, custom identity fields, scaffold/baseline separation, failing or changed scripts, actual host pre/post callbacks, concurrent business edits, candidate edits, in-place amendments and hash-checked legacy originals.
- Project-context/dual-acceptance/migration suite: 40 passed.
- Pi TypeScript check: passed. Pi adapter tests: 38 passed.
- Wheel build and isolated installation: passed. Installed-package multi-host/provider fixture matrix: 24 completed cases, covering bmad, openspec and speckit. Final wheel imports and the new CLI contracts were separately checked outside the checkout.
- Real Python, Node, TypeScript and Shell initialization/baseline flows were exercised. Maven/JDK, Gradle and Go are unavailable in this environment; their compiler tests explicitly skip. The remaining skip is platform-specific. These skips are not represented as successful compiler runs.
- Python compileall and git diff whitespace checks: passed.

## Use after updating

For a new project: provider installation/restart → accepted technology → sequential identity answers → scaffold plan/apply/check → independent verification scripts → `verification check --project` → business Work.

For an existing project or Work with direct command declarations, inspect them, run `verification materialize --scope <scope>`, then `verification check --project --scope <scope>`. Existing formal baseline review/reanchor requirements still apply; no task progress is reset.

For changed requirements: save a candidate, run `amend --check`, inspect it, then apply with `--expected-token`. Keep the exact original for legacy Work; use the optional hash-checked `--original` input when the original source was already overwritten.
