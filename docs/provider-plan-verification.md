# OpenSpec / Spec Kit requirement-wide verification review

Reviewed 2026-10-13. Sources are upstream documentation, command templates and implementation code, not third-party descriptions.

| Provider | Native execution unit | Completion evidence | CAO association |
| --- | --- | --- | --- |
| OpenSpec | A named change, possibly covering multiple spec capabilities | All tracked task-file checkboxes complete | The current Work's change references and explicitly bound tracked files/patterns; aggregate all related changes |
| Spec Kit | A feature containing setup, shared work and multiple user stories | All implementation tasks in the feature's tasks.md complete | The current Work's feature references and explicitly bound additional feature plans |

## Primary sources

OpenSpec upstream snapshot: `9111a7654d7800391459431fff4eaf66e33a3d2e`.

- [Workflow documentation](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/docs/workflows.md) explains focused changes, concurrent changes, implementation checkboxes, and optional native verification. Native archive can warn rather than block incomplete tasks; CAO's stricter gate is an explicit orchestration policy.
- [Default schema](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/schemas/spec-driven/schema.yaml) uses tasks as an apply prerequisite and tracks tasks.md.
- [Task progress implementation](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/src/utils/task-progress.ts) recognizes unordered and numbered CommonMark list markers, nested tasks, empty/padded boxes and unknown single-character states; only x/X certifies completion. It resolves concrete tracked files through the schema, including glob outputs.
- [Native apply instructions](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/src/core/templates/workflows/apply-change.ts) consume the native instruction payload's task state and context. CAO does not equate planning artifact existence with completed implementation.

Spec Kit upstream snapshot: `6c62e10d5ab1d676c557eb2d0cee86be52c893d2`.

- [Task template](https://github.com/github/spec-kit/blob/6c62e10d5ab1d676c557eb2d0cee86be52c893d2/templates/tasks-template.md) groups tasks by user story and includes shared setup/foundation/polish phases. Independent story testing or an MVP checkpoint does not finish additional tasks still present in the Work's scope.
- [Implement command](https://github.com/github/spec-kit/blob/6c62e10d5ab1d676c557eb2d0cee86be52c893d2/templates/commands/implement.md) requires the feature's tasks/plan, tracks completed tasks via checkboxes, and validates required task completion. Requirements-quality checklists are a distinct reviewer-owned input, not evidence of implementation completion.
- [Feature resolution](https://github.com/github/spec-kit/blob/6c62e10d5ab1d676c557eb2d0cee86be52c893d2/scripts/bash/common.sh) supports explicit feature directory and persisted feature context. CAO uses Work associations, not a mutable project-wide current-feature pointer that may refer to another Work.

## Confirmed CAO defects

Before this repair, `_prepare_verification` consulted only the BMAD aggregate gate. OpenSpec/Spec Kit plan checks occurred mainly at `cao done` on full profiles. Thus configured or ad hoc verification could run while another story/associated plan remained open; light completion could bypass plan completion entirely. Native state projection also considered only a single source and could show a completed last change as requirement-wide review. The old pending parser ignored `+`, numbered lists, unnamed tasks and unfamiliar task markers.

## Repaired behavior and boundaries

The provider-wide gate aggregates every associated task file before any CAO verification script and at completion, including light. Default task files derive only from Work-bound native sources and artifacts, not a scan of unrelated project changes. Discovered associations persist so deleting a known file cannot remove it from scope. Explicit registration adds files and preserves prior scope. Custom OpenSpec schemas must register their actual tracked Markdown outputs, using `--plan <file>` or a quoted change-scoped glob for dynamically added shards; CAO does not parse arbitrary schema dialects or run external native commands during dashboard reads.

Known task IDs and observed task-count minima prevent silently deleting planned tasks to manufacture completion. Empty/unrecognized, missing, cross-provider and product-symlink sources fail closed. Native task states and scope participate in freshness; new tasks, scope additions or regressions during verification produce stale results. Requirements changes remain subject to the existing explicit revision/reanchor policies. CAO/SDD artifact writes, direct developer tests and native plan-free light flows remain available.

## Authority validation

Compared CAO's task-line results with the actual upstream OpenSpec regular expression in the pinned source, executing that expression under Node. All three input corpora matched: marker/link/CRLF edge cases; nested numbered tasks; and the complete pinned Spec Kit tasks template (35 task lines). The permanent regression suite records this source revision and the marker boundary corpus. This validates format compatibility, not execution of the native Agent or semantic satisfaction of requirements.
