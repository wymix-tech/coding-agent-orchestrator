# BMAD dispatch review evidence

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
For a native intent_gap/bad_spec loopback, complete its clarification/design and
code re-derivation first, then use `begin --restart` for all active layers. This
starts the new review round rather than reusing the classified loopback tickets.

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
