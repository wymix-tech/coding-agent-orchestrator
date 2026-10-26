# BMAD oneshot review evidence

After `review begin`, pass its `reviewer_start` command to a new independent same-model host session with a CAO adapter. Bind the real reviewer before inspecting code: the shell child runs it, or its coordinator adds `--reviewer-session <host-observed-child-id>` for a read-only child. The host must observe that child, its lineage, read capability and matching model. Existing sessions and pre-ticket results cannot be reused. Use the host-native session ID returned by attach in the report; keep any separate subagent run ID as supplementary information.

Read for a BMAD oneshot review action, for any CAO quality profile.
Follow EVERY active Review layer and Classify step in the actual rendered
`step-oneshot.md`; a route-bound primary SPEC also identifies these obligations.

Prefer batch `cao sdd review prepare` to create all tickets and report paths before
launching reviewers. Read [conformance and knowledge](conformance-knowledge.md) for
capability preflight, one-session-per-layer handoff, existing-report import and
unchanged-error retry handling. The commands below remain available for manual
handoff. Current native evidence is reused; CAO does not add another code review.

## Begin and submit

1. Run `cao sdd review begin --layer <active-layer> --instructions <actual-step-oneshot.md> --model <current-model>` for each active layer.
2. Execute the native prompt with the required independent same-model reviewer.
   Use its external-session handoff when subagents are unavailable. Preserve the
   original findings as a nonempty Provider-owned Markdown file. Then use
   `cao sdd review draft --layer <layer> --source <raw.md-or-json>` for mechanical
   triage metadata; judge findings rather than inventing report identities.
3. Before patches, run `cao sdd review classify --layer <layer> --report <triage.json>` for all layers. Preserve that classification file unchanged. Fix findings as the native step requires; create a separate final report with the same judgments and `resolved: true` for patches. Submit actual evidence
   with `cao sdd review submit --layer <active-layer> --report <provider-report.json>`.

## Submit report fields

| Field | Value |
| --- | --- |
| `run_id`, `input_sha256` | Exact current begin ticket identity and input digest |
| `reviewer` | `{mode, session_id, model}`; mode `subagent` or `external-session`, real independent session, same current model |
| `findings_file` | Existing raw Markdown file from the reviewer |
| `findings` | Nonempty array of `{id, verdict, disposition, evidence}`; unique nonempty IDs and one classified item per complete native finding (problem heading or top-level list item) |

`verdict` is `high`, `medium`, `low`, `false` or `maybe-false`.
`disposition` is `patch`, `defer` or `reject`. Evidence must be nonempty;
`patch` also requires `resolved: true` after the real fix. Verified high/medium
findings cannot be rejected. Follow native Classify for the actual disposition;
do not manufacture bullets or findings to satisfy the report shape. If genuine
returned evidence cannot satisfy the interface, retain the blocker and report it.

CAO checks identity, freshness, independent reviewer identity and report shape;
it cannot establish semantic review from an Agent-written report. Primary
self-review and fabricated independent sessions do not qualify. Normal product fixes after all layers are classified retain the original tickets; final submit binds the repaired snapshot. Changed intent, instructions, acceptance standards or frozen evidence require recovery or a fresh applicable review. Complete all
layers before formal verify, then native Finalize/rebind, verification and Commit
in the native order. Classify uses oneshot dispositions; finalization uses submit, never dispatch resolve.


## Round consistency and recovery

Use `cao sdd review status` before resuming. All active layers belong to one model
and native instruction contract. Model, shared input or contract changes require
`cao sdd review prepare --restart --instructions <actual-step-oneshot.md> --model <current-model> --external-session`
(or the installed `--agent-definition`); the old round is retained in history.
For a damaged single layer with unchanged common inputs, restore its exact original
bytes or explicitly `begin --restart --layer <layer>` with the same instructions and
model. Completed classified reviewers may exit normally. For a running reviewer
that has exited, classify its original ticket-bound delivery if available; otherwise
restore that session or explicitly restart the layer. Never attach a replacement
identity to the old ticket.

All active layers must be collected and classified before product fixes. An
unresolved frozen intent conflict blocks patches and submit; restarting alone does
not resolve it. Keep raw and classification reports unchanged, and submit a separate
final report. Oneshot `low + reject` needs concrete evidence but no dispatch-only
`reject_reason`. The unchanged-code direct submit compatibility entry uses the same
judgment and ID policy, and cannot omit required active tickets.

New tickets also require the returned `task_marker` in the independent reviewer's
initial host-observed prompt. Bind by actual task/session metadata, never session
list order. The child's adapter records its final output; preserve that original
before draft/submit. The same task and raw-output provenance rules as dispatch
apply, while oneshot keeps its own native prompts and report obligations.

Default reviewer model comes from current host observation (`model_source`), not
worker environment defaults. New tasks bind their round/layer/run/input digest to
an observed child ticket and original output hash. Missing runtime association
remains pending; do not infer it from session order. Frozen product manifests and
observed round/task counts follow the same provenance rules as
[dispatch review](bmad-review-dispatch.md#observed-identities-and-frozen-change-manifest).
