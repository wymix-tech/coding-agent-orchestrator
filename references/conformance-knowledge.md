# Process conformance and project knowledge

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

## Native review preparation and recovery

Prefer `cao sdd review prepare --instructions <rendered-step> --model <current-model>
--agent-definition <installed-definition>` (one command). Preparation inspects an
actual repository agent definition for shell/read access and the installed host
adapter. Alternatively specify `--external-session` and satisfy the same execution
requirements in a new independent host session; this flag does not certify execution.

Preparation creates every active layer ticket before launch, plus per-layer
`dispatch.json` and `report.json` under `_bmad-output/cao-reviews/`. Use one new
session per layer. Execute its `reviewer_start` command first; successful host-bound
attach supplies real reviewer metadata. Preserve the provider's original findings
format and fill the report with actual findings and classification evidence. Dispatch
requires classifying all layers before patches and resolving all layers afterward.
`cao sdd review import --layer <layer> --report <path>` validates the existing native
report without inventing execution or another code review. A current passed report
can be imported again; preparation returns `already_satisfied` for completed reviews.
Only the coordinator may create/restart tickets. Never restart in a reviewer.

Errors include field diagnostics and a repair identity. On `pause_retry: true`, stop
repeating or delegating the unchanged request. Fix the indicated prerequisite before
retrying. This is an actionable stop signal, not a bypass of native obligations.
A report-format repair alone does not justify rerunning a valid native code review.

BMAD Build SPECs require exactly one nonempty, paired `<frozen-after-approval>`
block. Fix malformed structure before route/approval/review. Notes outside the block
do not change frozen intent. Genuine frozen-input changes still invalidate evidence.

## Optional knowledge collection

Knowledge collection follows successful review resolution/submission/import and task
completion. Starting a Work retries pending collection. Knowledge never supplies
approval, acceptance or completion authority; failure is recorded as `pending` and
can be retried with `cao knowledge refresh` without reopening completed Work.
Set `knowledge.enabled: false` in CAO configuration to disable collection/context use.

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
