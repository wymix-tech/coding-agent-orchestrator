# Native route declaration contract

Contents: Prepare and propose; Prepare a CAO input; Repair evidence errors; Declaration fields; Freshness and recovery.

Read when the current action requires declaring or repairing a native route.
Use the selected Provider guide for native route values and obligations. This
contract does not require other Providers to adopt BMAD's workflow or artifacts.

## Prepare and propose

Follow `cao next`: establish the verification baseline and path assessment first.
For standard/comprehensive, select the actual installed workflow and execute its
planning instructions before declaring a route. Bind the actual primary output
with `cao sdd artifact bind --artifact <native-file>` as soon as it exists.
Do not turn a bound PRD into a Build SPEC or fabricate workflow execution.
Light follows the installed native workflow without manufacturing full-profile
records. Automatic Build route prerequisites apply to installed BMAD Build;
other Providers retain their own native prerequisites.

If preparation reports `NATIVE_SPEC_PREPARATION_REQUIRED`, repair its named
`diagnostics` in the bound native source and rebind it. A BMAD Build route must
be in the template's YAML frontmatter; translated body labels do not count.
Binding an artifact records its identity, not successful preparation or approval.

## Prepare a CAO input

Run `cao sdd route prepare` after native preparation. It creates or preserves
`.orchestrator/inputs/WORK_ID/route.json`, using the actual Work ID. This is
Agent-authored CAO input, not a BMAD/native artifact or an accepted decision.
Only this active Work's exact input file is writable; config, Work, sessions and
other derived state remain protected. Never put product files in this directory.
The draft copies known identity/SPEC metadata; blank version/route/reason and
missing obligations must be filled from real native evidence, not guessed.
Repeated prepare preserves edits. To copy an old declaration without destroying
it, use `cao sdd route prepare --from <existing-declaration-file>`.
The CLI creates input parent directories. Do not create or remove the inputs
tree manually to work around a preparation error.

Generate evidence from actual file lines, preserving Markdown and internal line
breaks. Never summarize, insert ellipses or reconstruct a hash manually:

```bash
cao sdd route rule --source <installed-native-rule-file> --start-line <first> --end-line <last>
cao sdd route check
cao sdd route propose --declaration .orchestrator/inputs/WORK_ID/route.json
cao sdd route show
cao sdd sync
```

Line numbers are inclusive and one-based. `rule` replaces the same source path
or appends; use `--index <zero-based-index>` to repair a named existing entry.
It writes the source's actual hash and literal excerpt into the draft. Select
rules that actually justify the route; file existence alone is not evidence.
Installed rule sources include a provider skill's step files, templates and
references beside `SKILL.md`, plus provider-owned native rule roots. Absolute
paths inside the repository are accepted and normalized to repository-relative
paths. Generated SPECs and Agent-created business rules are not native rules.
`check` validates without accepting decisions or refreshing gates. `propose`
accepts an existing file path, not inline JSON. Existing legacy declaration
paths remain readable; new drafts use the CAO input path returned by `next`.

## Repair evidence errors

Inspect ALL `diagnostics`, not only the first `diagnostic`: each names its
`field` (e.g. `$.rules[1].quote`) and source path. Hash mismatches return the
actual file hash; quote mismatches say whether the hash already matches.
Repair every named entry with `rule`, then check again. Correcting a later entry
cannot fix an earlier invalid rule. Do not resubmit unchanged evidence, keep
recomputing already-correct hashes, add unrelated rules or change native source
text to make an invented quote match. If the source really changed, reread it
and regenerate its evidence. Preparation and validation do not approve a SPEC.

## Declaration fields

| Field | Required value |
| --- | --- |
| `provider` | Current Work's selected Provider |
| `provider_version` | Nonempty actual installed version |
| `requirement_sha256` | Current Work requirement's actual SHA-256 |
| `profile` | Current path assessment profile |
| `workflow` | Selected native workflow identity; BMAD light can use installed `bmad-build` |
| `route` | Native route supported by installed rules; see Provider guide |
| `build_route` | BMAD Build mode, when applicable; see Provider guide |
| `reason` | Evidence-backed reason for this choice |
| `primary_artifact` | Existing Provider-owned primary output, also in `required_artifacts` |
| `required_artifacts` | Nonempty array of `{path, role}` for real unique owned artifacts |
| `rules` | Nonempty array of `{path, sha256, quote}` from actual installed native rules |
| `omitted` | Array of `{name, reason, replaced_by}` for omitted duties; replacement must be a required artifact |

Use repository-relative paths. Each rule hash must match the current file and
its quote must occur in that file. A `sprint` role accepts only native
`sprint-status.yaml` or `sprint-status.yml`. Provider-specific role requirements
belong to that Provider; PRD/architecture/epics/sprint are not universal roles.
Do not copy example hashes or invent rule evidence.

## Freshness and recovery

Accepted decisions are recorded in `sdd.route_history` and the current
`route_decision`, retaining predecessor, requirement/assessment, rules, artifacts
and omissions. A new decision invalidates old approval, verification, manual gates
and workflow completion; rerun the affected operations without deleting history.

Changed requirement, rules, route or frozen intent requires the applicable
reassessment and approval. Status/checkbox progress alone does not change intent.
Missing sources fail closed; restore or associate the actual current artifact
rather than silently selecting another SPEC. Preserve current Work and progress
when adding missing bindings; require current review instead of inventing past
approvals. Native status synchronization records progress; it cannot close Work.
