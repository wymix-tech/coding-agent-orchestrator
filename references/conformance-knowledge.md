# Process conformance and project knowledge

Contents: [Review preparation](#native-review-preparation-and-recovery) · [Knowledge](#optional-knowledge-collection)

SDD providers own functional code review. CAO checks their declared process,
artifacts, approvals, scope and current evidence. A current native review satisfies
the matching implementation-review gate; it does not trigger another CAO code review.
Native SPEC approval and design evidence satisfy only their corresponding gates.
Unmapped requirements (including security-specific requirements), verification and
acceptance remain required. Never turn an absent or stale native result into a pass.

Use `cao conformance show --work <id>` for the current requirement/evidence matrix,
including ownership, contract sources and blockers. `cao conformance check` exits 2
when blocked. Required artifacts must exist and contain content. The contract is
computed from current lifecycle/provider declarations; stored summaries are not
completion authority. Completion uses the same evidence checks.

For new Work, follow the [native lifecycle](native-lifecycle.md). Conformance checks
precede formal baseline verification and have no dependency on a future run.

## Native review preparation and recovery

This section applies only to BMAD Build oneshot/dispatch. OpenSpec and Spec Kit use their installed native review instructions; the conformance and knowledge commands remain shared.

For BMAD Build oneshot/dispatch only, prefer `cao sdd review prepare --instructions <rendered-step> --model <current-model>
--agent-definition <installed-definition>` (one command). Preparation inspects an
actual repository agent definition for read access, any declared backend executable,
and the installed host adapter. A read-only reviewer uses coordinator attachment.
For legacy contract-1 project-bound Work only, configure task acceptance and complete source coverage
before preparation; failures create no tickets. Alternatively specify `--external-session` and satisfy the same execution
requirements in a new independent host session; this flag does not certify execution.

Preparation creates every active layer ticket before launch, plus per-layer
`dispatch.json` and `report.json` under `_bmad-output/cao-reviews/`. Use one new
session per layer. Bind it before review: a shell-capable child executes
`reviewer_start`; for a read-only child, the coordinator runs the same attach command
with `--reviewer-session <observed-child-native-id>`. This requires host-observed
parent/child lineage, active status, read tools and matching model; primary-session
identity is never substituted for the reviewer. Missing observations require fixing
the child adapter, not inventing IDs. Successful attach supplies real metadata. Preserve the provider's original findings
format. Run `cao sdd review draft --layer <layer> --source <raw.md-or-json>` to
populate mechanical metadata and complete finding IDs. Fill only actual judgments
and evidence; null draft judgments cannot pass. Heading-based Markdown groups its
explanatory bullets; native JSON without IDs gets deterministic IDs without a copy.
Use the dispatch package's `task_contract` when launching a worker as reviewer:
no product edits, no requirement to implement changes or add tests. Verify actual
backend binary availability, model-override support, active tools and child extensions
before launching; do not assume an agent name or pass unsupported overrides.
Definitions declaring `acceptanceRole: writer` are rejected: install
`templates/pi-reviewer.md` as `.pi/agents/cao-native-reviewer.md` and prepare with
that definition. External-cli runner definitions must use an explicitly verified
`--external-session` handoff; their model-override support is never assumed. Dispatch
requires classifying all layers before patches and resolving all layers afterward.
For a single atomic classification, save a Provider-owned JSON object mapping every
active layer to its triage report path, then run
`cao sdd review classify-batch --report <manifest.json> --compact-json`.
Any invalid layer leaves all classifications unchanged; no shell loop is needed.
`cao sdd review import --layer <layer> --report <path>` validates the existing native
report without inventing execution or another code review. A current passed report
can be imported again; preparation returns `already_satisfied` for completed reviews.
Only the coordinator may create/restart tickets. Never restart in a reviewer.

Errors include field diagnostics and a repair identity. On `pause_retry: true`, stop
repeating or delegating the unchanged request. Fix the indicated prerequisite before
retrying. This is an actionable stop signal, not a bypass of native obligations.
A report-format repair alone does not justify rerunning a valid native code review.
`cao sdd review status --compact-json` exposes layers and blockers without changing state.
Use `--compact-json` on commands instead of piping output through grep/head; boolean
failure and original exit codes are preserved. Cosmetic report edits do not reset
the logical fault count; identical requests pause after repeated failure.

An attached reviewer may run `cao sdd review check --layer <layer> --script <configured-name>`
in a temporary project copy while classification is pending. A coordinator may
add `--reviewer-session <bound-child-id>` to execute this check for its read-only
reviewer; lineage, observed model/tools and the existing ticket are revalidated. This runs an existing
trusted argv-compatible verification wrapper, not arbitrary commands. Generated
files stay in the copy; this is working-directory isolation, not an OS sandbox.
Missing dependencies block the check. Results are review experiments, never final
technical or task acceptance evidence. Direct product writes remain prohibited.

Task acceptance `covers` changes require renewed task acceptance, but do not
invalidate native code review when only those trace links change in the configured
original declaration. Criterion descriptions, executor, other declaration data,
source code, native intent and instructions remain review inputs. Unknown/stale
covers must be repaired before acceptance. Document IDs, dates and statuses are
metadata rather than substantive acceptance requirements.

BMAD Build SPECs require exactly one nonempty, paired `<frozen-after-approval>`
block. Fix malformed structure before route/approval/review. Notes outside the block
do not change frozen intent. Genuine frozen-input changes still invalidate evidence.

## Optional knowledge collection

Knowledge collection follows successful review resolution/submission/import and task
completion. Starting a Work retries pending collection. Knowledge never supplies
approval, acceptance or completion authority; failure is recorded as `pending` and
can be retried with `cao knowledge refresh` without reopening completed Work.
Run `cao knowledge configure --enabled false` to disable collection/context use.

Commands: `cao knowledge collect --work <id>`, `cao knowledge list --work <id>`,
`cao knowledge show --id <id>`, and `cao knowledge refresh`. Inspect CLI `--help` for
candidate import and inactive-record options. External candidates stay inferred,
Work-scoped suggestions; a model statement cannot create project authorization.

The bounded atomic store at `.orchestrator/knowledge/store.json` records source
paths/hashes, scope, authority and origin Work. Accepted project configuration and
verification commands are observed project facts. Approved frozen constraints remain
Work-scoped; code-map file entries may be project-scoped. Changed or unavailable
sources become stale, contradictory current facts become conflicted, and neither is
injected as active knowledge. Context excerpts are bounded and checked against their
sources on read. Knowledge files do not become part of the reviewed product snapshot.

This first implementation uses existing lifecycle contracts for all providers and
adds explicit native evidence mapping/dispatch for BMAD. It does not infer arbitrary
new provider contracts or promote candidate summaries automatically.

Running and classified tickets survive preparation. Classified layers proceed to patches and route-specific finalization; never classify twice. Changed inputs/model/evidence require explicit `--restart`; replaced tickets remain in `sdd.native_review_history`. A recorded intent conflict blocks finalization until native clarification and a fresh reviewed round.

The low-level `review begin` remains available for historical/manual integration and does not perform acceptance-coverage preflight. Legacy contract-1 `prepare` and navigation require project-bound coverage before dispatch. Contract 2 binds original native completion reports after native execution; see [native lifecycle](native-lifecycle.md).
