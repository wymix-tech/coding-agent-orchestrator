# BMAD dispatch review evidence

Read for a BMAD dispatch review action. Follow the actual rendered
`step-04-review.md`; these report formats belong to dispatch.

## Collect, classify, patch, resolve

1. Begin EVERY active native layer for the current snapshot with
   `cao sdd review begin --layer <active-layer> --instructions <actual-step-04-review.md> --model <current-model>`.
2. Launch the independent same-model reviewers required by the native prompt
   (or its external-session fallback). Pass each ticket's `reviewer_start` command to a NEW independent host session; that reviewer must execute it before reviewing. It binds the real native session and current code snapshot to the ticket. Never execute it in the primary session or reuse a pre-ticket review. Preserve raw JSON findings under
   `_bmad-output/`; each raw finding has a unique nonempty `id` and its content.
3. Collect all layers and classify every raw ID exactly once, before applying
   product patches: `cao sdd review classify --layer <active-layer> --report <triage-file.json>`.
4. Apply the classified patches and native checks, then resolve every layer:
   `cao sdd review resolve --layer <active-layer> --report <resolved-file.json>`.

Do not remove active layers. Zero findings use empty raw and classified arrays;
never invent findings. Reuse no identity or hash from an older begin ticket.

## Classify report fields

| Field | Value |
| --- | --- |
| `run_id`, `input_sha256` | Exact current begin ticket identity and input digest |
| `reviewer` | `{mode, session_id, model}`; mode `subagent` or `external-session`, real independent session, same current model |
| `findings_file` | Existing Provider-owned raw JSON array file |
| `findings` | Array of `{id, verdict, disposition, evidence}` covering raw IDs exactly once |

`verdict` is `high`, `medium`, `low`, `false` or `maybe-false`.
`disposition` is `patch`, `defer`, `reject`, `bad_spec` or `intent_gap`.
Supply nonempty evidence for each judgment. Do not reject verified high/medium/low
findings. Classify before deduplicating. `defer` requires `scope: outside-intent`
and `violates_frozen_intent: false`; a frozen-intent violation must explicitly
set `violates_frozen_intent: true` and use the native `intent_gap`/`bad_spec`
loopback instead of postponement. Fields cannot replace actual semantic review.

## Resolve report fields

Supply `{run_id, resolved_ids}` with the current layer's run ID and ALL its
`patch` finding IDs after real fixes and checks. CAO fingerprints the original
raw, classify and resolve evidence and binds the repaired code snapshot.
A review round is not a loopback count. Changed sources/evidence require the
applicable fresh review; a manual `implementation_review` gate cannot substitute.
