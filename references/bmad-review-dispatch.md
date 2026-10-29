# BMAD dispatch review evidence

For lifecycle contract 2, native tests/review/acceptance remain CAO implementation. Follow [native lifecycle](native-lifecycle.md) for completion evidence, migration and precise recovery; separate functional executor guidance applies only to legacy contract 1.


Contents: [Collect and classify](#collect-classify-patch-resolve) · [Classify fields](#classify-report-fields) · [Resolve fields](#resolve-report-fields) · [Recovery](#round-consistency-and-recovery)

Read for a BMAD dispatch review action. Follow the actual rendered
`step-04-review.md`; these report formats belong to dispatch.

Prefer batch `cao sdd review prepare` to create all tickets and report paths before
launching reviewers. Read [conformance and knowledge](conformance-knowledge.md) for
capability preflight, one-session-per-layer handoff, existing-report import and
unchanged-error retry handling. The commands below remain available for manual
handoff. Current native evidence is reused; CAO does not add another code review.

## Collect, classify, patch, resolve

1. Begin EVERY active native layer for the current snapshot with
   `cao sdd review begin --layer <active-layer> --instructions <actual-step-04-review.md> --model <current-model>`.
2. Inspect actual host tools and installed agent definitions before launching. Use an
   available agent with read access and the CAO adapter loaded. A shell-capable
   reviewer runs `reviewer_start`; a read-only reviewer asks the coordinator to run
   that attach command with `--reviewer-session <observed-child-id>` before review.
   The coordinator binds host-observed child identity/model, never its own identity. Pi foreground children require
   explicit child extensions: run `cao host install --host pi` before restarting to
   register CAO in `subagents.defaultSubagentOnlyExtensions`. Preserve agent-specific
   extension overrides; include CAO there too or use a capable external session.
   Use the runtime model identity (`PI_MODEL`, optionally qualified by `PI_PROVIDER`);
   independent means a different session with the same model capability.
   Launch the independent same-model reviewers required by the native prompt
    (or its external-session fallback). Pass each ticket's `reviewer_start` command to a NEW independent host session; bind that child before reviewing, through self-attach or observed coordinator attach. It binds the real native session and current code snapshot to the ticket. Never self-attach the primary session or reuse a pre-ticket review. Preserve the unmodified native findings under
   `_bmad-output/`, in Markdown when the native prompt requests Markdown, or JSON
   when it requests JSON. Never rewrite native prompts to satisfy a CAO format.
   Run `cao sdd review findings --source <raw.md-or-json>` to inspect the ID mapping:
   Markdown problem headings (or top-level list findings when no headings exist)
   get `finding-1`, `finding-2`, etc.; explanatory bullets and fenced examples stay
   within their finding. JSON keeps original IDs, deriving IDs only when absent.
   Prefer `cao sdd review draft --layer <layer> --source <raw.md-or-json>` to create
   the triage skeleton without converting or rewriting the native report.
3. Collect all layers and classify every raw ID exactly once, before applying
   product patches: `cao sdd review classify --layer <active-layer> --report <triage-file.json>`.
4. Apply the classified patches and native checks, then resolve every layer:
   `cao sdd review resolve --layer <active-layer> --report <resolved-file.json>`.

Do not remove active layers. Zero findings use empty raw and classified arrays;
never invent findings. Reuse no identity or hash from an older begin ticket.
After interruption inspect the current action and reuse its tickets. `begin` is
idempotent for a current running/classified dispatch ticket; use `--restart` only
to explicitly replace a failed ticket. Classified is not resolved. If a worker or
provider fails, inspect partial edits, resume the same worker (or apply the native
fallback), rerun checks and resolve remaining layers. Do not restart all reviews
or report old tests as validation of the partially patched tree.
For a native intent_gap/bad_spec loopback, first classify every active layer, then
run `cao sdd review loopback --reason "<native cause>" --keep "<KEEP instructions>"
--path <product-file>` with repeated exact `--path` values for all affected files.
This archives the old round and records the original baseline and bounded repair
scope; it does not undo code or count as a new review. Before replanning, restore
recorded existing files with `git restore --source <returned-baseline-commit>
--worktree -- <exact-files>`; delete only returned `added_files` with `rm -- <files>`.
Unbounded reset/restore commands are not allowed. Without Git, exact original
content writes are allowed when their SHA-256 matches a recorded baseline receipt.
Resolve intent gaps with the human (old approval is revoked), refresh changed
route/approval evidence, then follow native step-03 and its real implementation
handoff to rederive the code. Reimplementation is limited to the recorded paths;
accepted configured verification commands remain available. Prepare a fresh
review round after reimplementation; old classified tickets cannot certify it.
If the native replan identifies another necessary product file, repeat the
loopback command with `--extend --reason "<why needed>" --keep "<preserved KEEP>"
--path <new-file>`. This appends an audited scope change without resetting the
loopback, approvals or history; it cannot include provider outputs or CAO state.

## Classify report fields

| Field | Value |
| --- | --- |
| `run_id`, `input_sha256` | Generated from current ticket when omitted; explicit mismatches fail |
| `reviewer` | Generated from actual attach when omitted; explicit conflicts fail |
| `findings_file` | Existing Provider-owned, unmodified native Markdown list or JSON array |
| `findings` | Array of `{id, verdict, disposition, evidence}` covering raw IDs exactly once |

`verdict` is `high`, `medium`, `low`, `false` or `maybe-false`.
`disposition` is `patch`, `defer`, `reject`, `bad_spec` or `intent_gap`.
Supply nonempty evidence for each judgment. Do not reject verified high/medium
findings. A native low finding unlikely in everyday use whose fix adds complexity
may be rejected with `reject_reason: unlikely-use-nontrivial-fix` and concrete evidence.
Classify before deduplicating. `defer` requires `violates_frozen_intent: false` and
`defer_reason: pre-existing|unverified|agent-context`; `unverified` requires verdict
`maybe-false`. Missing from the plan/acceptance list, inconvenience or test changes
alone are not deferral reasons. Verify claims against actual code/dependencies;
do not promote a reviewer's untested library assumption to a confirmed defect.
A frozen-intent violation must explicitly
set `violates_frozen_intent: true` and use the native `intent_gap`/`bad_spec`
loopback instead of postponement. Fields cannot replace actual semantic review.
Check frozen constraints against each other and the I/O matrix. Include any known
contradictions in `intent_conflicts: ["<contradiction and locations>"]`; this blocks
patches/closure even when findings arrays are empty. A note in Implementation Notes
does not resolve a contradiction. Use the native human clarification loopback.

## Resolve report fields

Supply `{run_id, resolved_ids}` with the current layer's run ID and ALL its
`patch` finding IDs after real fixes and checks. CAO fingerprints the original
raw, classify and resolve evidence and binds the repaired code snapshot.
A review round is not a loopback count. Changed sources/evidence require the
applicable fresh review; a manual `implementation_review` gate cannot substitute.


## Round consistency and recovery

All active layers share one model and native instruction contract. Inspect
`cao sdd review status`; only running layers need live reviewer sessions. Classified
reviewers may exit, but their original raw and triage evidence must remain intact.
Status, prepare, writes and finalization check the same evidence freshness.

Model, common input or native contract changes require explicit whole-round
`cao sdd review prepare --restart --instructions <actual-step-04-review.md> --model <current-model> --external-session`
(or the installed `--agent-definition`). Removed layers are retained in history and
no longer block the active round; newly required layers must complete. A damaged
single layer under an unchanged contract can restore its original bytes or use
`begin --restart --layer <layer>` with the same instructions/model. Preserve other
valid layers. Restart alone never resolves a known frozen-intent conflict.

## Task and original-output provenance

New tickets return `task_marker`, an exact `CAO_REVIEW_TICKET {...}` line. Include
that line in the actual independent reviewer's initial task prompt. The child's
host adapter records the ticket from the prompt before attach. Coordinator attach
checks this task-to-layer binding as well as parent/model/Work/input identity.
Never map sessions by list order, task timing, or return order. An external
reviewer needs the same ticket in its real initial host prompt.

The host records the bound reviewer's final textual output. Preserve those bytes
(as Markdown or JSON) in the raw file before `draft`/`classify`; mismatched or
unobserved originals cannot be imported as current findings. This establishes
source provenance, not proof of semantic review quality. A read-only reviewer
needs no shell: the coordinator attaches its observed task/session mapping and
copies the unmodified output.

## Observed identities and frozen change manifest

Omit `--model` to use the current session's host-observed model; the result names
`model_source`. A stale worker environment is not evidence. An explicit model
must be observed on the actual reviewer, including provider-qualified aliases
when the host supplies provider identity. Missing observation remains blocked.

Each new task binds its round/layer/run/input digest to an observed ticket in the
actual child prompt and the original final-output hash. The coordinator performs
attach for read-only reviewers using that association. Session order, equal
models and return order cannot establish role identity.

New reviews freeze a complete product manifest, including non-code resources,
removals and Git renames. NO_VCS uses a persisted Work-start baseline; unavailable
historical baselines are labeled explicitly. Manifest tampering blocks affected
reviews. Context provided to each layer still follows its native independence
contract. Review status and acceptance summary expose layer, task and observed
round counts; historical tasks without a round are reported separately.

## Executable native input packages

New tickets expose `prompt_file`, `diff_file` and a hashed context policy. Send the
exact prompt file as the independent child task. Do not ask every child to read
the whole review step, SPEC, architecture or earlier findings. The layer's native
prompt is preserved after runtime path substitution. Pi launch calls must set
`async: false`; `worktree`, when supported, is a top-level host argument. Launch
all blocking reviewers and await all final outputs before classification.

Blind reads only its frozen diff and own prompt. Other layers may read declared
product files and their own instruction files. Edge claims remain unavailable
until observed diff/instruction/product reads. Broad shell interpreters and
unscoped searches cannot enforce this boundary; use explicit scoped read tools
and ask the coordinator for disposable native checks. Tool path/order checks
are process evidence, not proof of semantic tracing quality. The host does not
inject the coordinator's project/requirement/history context into child sessions.

NO_VCS Work records retain baseline bytes in the bounded runtime archive. Legacy
current-tree fallback is explicitly labeled best-effort when no historical
baseline exists; it never claims a reconstructed historical change set.

## Cross-session resume

Run `cao sdd review resume` before restarting interrupted reviews. It reuses
current tickets, selects immutable collected findings and creates missing drafts.
Existing triage judgments, classified layers and resolved layers are retained.
`draft --layer <layer>` defaults to canonical collected bytes; `--source` remains
available for an explicit attested artifact.

For older bound sessions with only an observed final-message hash, resume searches
the recorded Pi archive or that repository's native Pi session directory. Use
`--transcript <bound-session.jsonl>` for an exported archive. Only assistant text
from the identified session matching the original full-message hash can recover
findings. Missing original/binding reports a layer-specific blocker; it cannot
retroactively bind identities. Inspect those gaps before restarting only the
unrecoverable layer. Replacing observed evidence for unchanged inputs requires
`--restart-reason`; the reason is retained in history.
