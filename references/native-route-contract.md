# Native route declaration contract

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

Write the declaration to the Work-specific `declaration_path` returned by
`cao next`, using its actual Work ID. Confirm the write succeeded. Then run:

```bash
cao sdd route propose --declaration <existing-declaration-file>
cao sdd route show
cao sdd sync
```

`--declaration` accepts a file path, not inline JSON. Keep new declarations in
Provider-owned output directories, never invent a project-root write exception.

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
