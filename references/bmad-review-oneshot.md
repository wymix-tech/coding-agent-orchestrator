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
3. Classify and fix findings as the native step requires. Submit actual evidence
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
self-review and fabricated independent sessions do not qualify. Changed code,
intent, instructions or evidence requires fresh applicable review. Complete all
layers before formal verify, then native Finalize/rebind, verification and Commit
in the native order. Never use the dispatch `classify`/`resolve` format here.
