# Execution path and native scope

Contents:

- [Enter when](#enter-when)
- [Execute](#execute)
- [Workflow evidence](#workflow-evidence)
- [Entire requirement scope](#entire-requirement-scope)
- [Progress and observed gates](#progress-and-observed-gates)
- [Exit when](#exit-when)
- [If blocked](#if-blocked)

## Enter when

An active Work has an accepted verification baseline. Assess the path before business writes; resume an existing current assessment unchanged.

## Execute

After creating a Work Item, assess the minimum safe workflow before making code changes. Keep this
assessment lightweight: read the requirement and only the relevant project context, then choose one
profile. Do not create a scorecard, fact inventory, provenance model, or evidence graph.

```text
light          clear, local, low-risk, reversible; targeted verification
standard       ordinary feature/fix requiring some analysis/design, tests and review
comprehensive  major uncertainty, cross-system or high-risk change; explicit planning and stronger gates
```

`light` uses a minimal native SPEC with explicit criteria (or recognized BMAD oneshot Intent) plus fresh project verification. It does not require manufacturing workflow records, plans/no-plan or independent final acceptance. Finish every task in native plans that actually exist before CAO verification, including on light. Standard/comprehensive retain the full native workflow and independent acceptance contract.

Record the assessment in the Work Item:

```bash
cao path assess --profile light --reason "Clear, local, reversible change"
```

Supported risk flags (repeat `--risk-flag` as needed): `security`, `data-migration`,
`irreversible-change`, `cross-system`, `major-ambiguity`, `external-contract`, `multi-module`,
`compatibility`. CAO deterministically upgrades `security`, `data-migration`, `irreversible-change`,
`cross-system`, and `major-ambiguity` to at least `comprehensive`; contract, multi-module, and
compatibility risks require at least `standard`. The saved assessment reports both the requested and
effective profile and adds the relevant gates.

`major-ambiguity` asks for user clarification, then a fresh `cao path assess` after incorporating the answer.
Missing/stale assessment and ambiguity block business writes; native planning outputs and initial verification scaffolding remain writable. `cao done` also remains blocked. If the requirement changes, explicitly re-anchor before reassessment. Never hand-edit CAO state.

The selected SDD Provider supplies profile-specific native-workflow guidance. Follow BMAD, OpenSpec,
Spec Kit, or another provider's own commands, artifacts, and mandatory preconditions; CAO does not
invent a duplicate SDD lifecycle. Reassess only when scope, requirement, or risk materially changes.
The profile never bypasses the project's configured Verification scripts or provider-native gates.

### Workflow evidence

For **standard/comprehensive** profiles with an installed SDD provider, selecting a
path is not proof that its native workflow ran. Before implementation, record the actual provider-native
workflow and entrypoint, then execute it using that provider's installed instructions:

```bash
cao sdd workflow select --name "<native workflow name>" --entrypoint "<native workflow command or skill>"
```

After actually executing the workflow, record the run itself:

```bash
cao sdd workflow execute --command "<command actually run>" --exit-code <code> [--output "<tail>"]
```

Then record existing provider-owned outputs and every related plan: repeat `--artifact` and `--plan <path>`;
shared BMAD sprint work requires `--plan-item '<path>#<story-id-or-epic-N>'`. Use audited `--no-plan` only when no plan applies;
Unresolved execution scope cannot certify completion; preserve prior associations when recording later outputs.

```bash
cao sdd workflow complete --artifact <repo-relative-file>
```

CAO blocks `cao done` until the workflow is selected, executed, and completed with existing output files.
This workflow registration/plan contract applies to standard/comprehensive across providers. For light, bind the native SPEC and explicit criteria with `cao sdd spec bind --spec <provider-spec.md> --criteria-section "<heading>"`, finish all native execution tasks, then verify; do not manufacture a plan or no-plan declaration.

CAO rejects workflow records it can verify as fake:

- a placeholder entrypoint (`echo`, `true`, `printf`, ...) is refused;
- an entrypoint that does not reference the selected provider is refused;
- output files outside the provider's own directory (BMAD `_bmad-output/`, OpenSpec `openspec/`,
  Spec Kit `specs/` and `.specify/`) are refused — **your own product source files are not SDD artifacts**.

Do not fabricate artifact paths, do not record a command you did not run, and do not mark a workflow
completed before running it. If a provider-native workflow genuinely cannot satisfy this contract, do not
bypass the gate silently: pass `--allow-unverified --reason "<why>"`. That exception is recorded in the
Work Item and appended to the lifecycle event log for audit. A nonzero execution needs a separate, new
`--allow-unverified --reason` when completing; an entry exception does not waive failed execution.
Completion binds requirement/provider and output bytes; later changes invalidate that evidence.

### Entire requirement scope

Associate ALL native execution units belonging to the current Work; never treat one completed story/change as the whole requirement. For BMAD, use `cao sdd plan bind --plan-item <sprint-status.yaml-or-yml>#<story-id-or-epic-N>` for every associated story or entire epic. For OpenSpec/Spec Kit, default task files beside the Work's associated change/feature are captured automatically; add split changes/features with repeated `cao sdd plan bind --plan <native-task-file>`. For OpenSpec custom schemas, use actual tracked outputs from native apply instructions, optionally a quoted tracked-file glob to include new shards. Do not use a global change/feature list or Spec Kit quality checklists as completion evidence.

Finish ALL associated native tasks before `cao verify`; a CLI script override cannot skip this check. Preserve known task IDs/files and existing associations; restore removed work or handle a confirmed requirement revision instead of shrinking scope, marking unrelated work done, or inventing no-plan. New tasks, files or status regressions invalidate earlier verification. Continue direct development tests and CAO/SDD artifact writes while native implementation is unfinished. Native light/oneshot without an execution plan remains available; final close still requires `cao done`.
For an accepted BMAD Direct Build route, finish every task in its bound SPEC Tasks & Acceptance sections; `--no-plan` only means no separate plan file. Bind related sprint items explicitly; unrelated historical project sprints do not belong to this Work merely because their files exist.

### Progress and observed gates

Follow the selected Provider guide and installed native instructions. CAO profile and native route are separate: a direct native route does not downgrade CAO quality gates. Bind the actual outputs and preserve associated task IDs/files across later recordings. Run `cao sdd sync` at transitions when hooks are unavailable; source synchronization observes progress, not approval or completion.

Record meaningful slices with `cao checkpoint --step "<step>" --next "<next action>" --summary "<done and remaining>"`; record durable choices with `cao decision --decision "<choice>" --reason "<why>"`.

Only mark gates actually observed by the Agent with `cao gate satisfy --gate <gate> --evidence "<what was done>"`; inspect `cao gate show`. Verification/tests/security and `sdd_acceptance` derive from their real configured runs, never manual satisfaction. Human evidence binds the requirement hash and assessment revision.

## Exit when

Every associated native task and applicable pre-verification review obligation is complete. Read completion guidance before formal verification; perform any later native Commit at its required close step.

## If blocked

Unresolved ambiguity requires clarification and fresh assessment. A stale requirement, route, approval or review requires its own recovery; never restart intake or shrink known execution scope to clear a gate.
