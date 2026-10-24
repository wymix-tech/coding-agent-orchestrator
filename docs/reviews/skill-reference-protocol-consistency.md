# Skill/reference protocol consistency repair

Baseline: main 67d2ba242369e4ec0e8a201ea0fc96f1e94ea4c2.

- Running review tickets now survive repeated preparation on both Build routes. Bound reviewers continue their original ticket.
- Dispatch finalizes through resolve. Oneshot can classify native findings before patches, then submit a separate resolved report. The original raw and classification files remain immutable and are retained in final freshness evidence. Intent/instruction/acceptance-standard changes still invalidate the round. Native instructions remain authoritative about when another review is required.
- The unchanged-code oneshot submit interface remains available for existing callers; finding IDs must now match raw findings exactly. Incorrect legacy IDs require metadata repair, not another code review.
- Compact output preserves structured context/results and unknown fields, bounding only known stdout/stderr text while retaining navigation, ticket identity and gate diagnostics.
- Acceptance declarations/executors and coverage are prepared before reviewer dispatch; formal execution remains after verification.
- Controlled configuration: knowledge configure --enabled true|false; runtime configure --mode off|bounded, --segment-bytes, --max-bytes-per-stream, --max-bytes-per-work, --closed-work-days; verification configure --timeout-seconds N --replace. Runtime and knowledge configuration do not recertify any evidence. Verification changes retain existing review/reanchor semantics.
- Light still needs actual project-bound task acceptance evidence; independent final reviewers are a separate requirement.

## Validation boundaries

New integration regressions execute real local CLI commands, observed fixture-session attachment, classification, a product change and final submission. They do not claim real-model semantic review or a replay on the user's Mac.

Validation: Python full discovery 933 passed (544.260s); final targeted guidance/review regressions 37 passed; final protocol suite including invalid finding ID 4 passed; Pi 37 passed; TypeScript check, make check, relative guidance file links and git diff --check passed. The full discovery started before the fourth protocol case was added; that case was verified in the final separate four-case run.

## Follow-up review fixes

- Route-aware next actions now cover classify, batch classification, preparation and status navigation. Oneshot resolve is explicitly rejected.
- Recorded intent conflicts block submission and readiness even if a legacy record says passed.
- Classified tickets resume patch/finalization rather than collecting/classifying again; unavailable bound sessions receive recovery instructions.
- Input/evidence/model changes require explicit restart; replaced records retain their original reviewer and evidence under native_review_history.
- Disposable review-check compact results retain bounded stdout/stderr diagnostics.
- Runtime navigation routes missing project-bound acceptance coverage to the shared acceptance protocol. Manual begin remains a documented low-level compatibility API; prepare enforces preflight.
- Protocol tests now acquire running tickets through begin/attach, without editing Work state to turn passed runs back into running.

Follow-up validation: full discovery ran 938 tests and exposed two compatibility failures (invalid native source masking route diagnostics, and stale-state wording). Both were corrected; the route-input, session-integration, protocol and guidance modules were rerun successfully. Pi 37 tests, TypeScript, compileall and diff checks passed. No real-model end-to-end replay is claimed.

## R1–R8 cross-provider convergence (2026-10-24)

| Item | Implementation | Regression evidence |
| --- | --- | --- |
| R1 | `_contract.model` and shared read-only integrity checks bind active layers to one model; changed model, instructions/layers or common inputs require explicit whole-round restart. Replacement archives original tickets, contract, timestamp and reason. Batch planning validates all layers before creating dispatch files. | Mixed historical models fail readiness; model/common-input restarts archive the old round; repeat preparation preserves identity. |
| R2 | The existing host patch guard now applies to both BMAD Build routes. All active layers must be classified with current evidence before patches. | Two-layer oneshot via real CLI and Pi/Codex/Claude Code pre-tool events: partial classification denies writes, full classification permits patches and final submission. |
| R3 | Active native layers, rather than all dictionary entries, determine completion and model checks. Oneshot derives layers from its rendered contract. | Inactive legacy records do not block submit; missing active layers remain required; changed contracts archive the prior round. |
| R4 | Only running layers require a live reviewer; classified/passed evidence retains identity/freshness validation without demanding a permanently active session. | A classified reviewer is never included in the running-session liveness lookup; running exited reviewers receive delivery-or-restart guidance. |
| R5 | Status, navigation, preparation, finalization and product-write checks reuse round/evidence integrity. Classified records retain pre-patch input; final records bind the post-patch snapshot. | Raw/triage/final tampering returns stale/recovery, prevents patching and cannot be reused; restoration of exact original bytes restores applicable evidence. |
| R6 | Classify and compatibility submit share finding-ID mapping and judgment/disposition/conflict policy. Oneshot low/reject does not inherit dispatch-only reject_reason. | Both oneshot entry sequences enforce IDs, evidence and verdict policy; dispatch preserves its native defer/reject/loopback rules. |
| R7 | Shared instructions identify BMAD Build oneshot/dispatch explicitly. Other providers reject ticket preparation before ticket creation and retain native navigation. | OpenSpec/Spec Kit × light/standard/comprehensive navigation and compact project-context checks; explicit inapplicable prepare has no ticket side effects. |
| R8 | Compact keeps structured context/results and unknown fields; only known stdout/stderr text is tail-bounded, with truncation markers. | Nested status/errors/revisions survive; full output is not mutated; review-check diagnostics stay available. |

### Final command sequences

Both BMAD routes prepare applicable task acceptance declarations/executors/coverage
before dispatch. Prefer `sdd review prepare --instructions <native-step> --model
<current-model> --agent-definition <installed-reviewer>` or `--external-session`.
Attach each new observed reviewer (coordinator `--reviewer-session` for read-only
children), preserve original Markdown/JSON and draft actual classifications.

- Dispatch: prepare → attach each layer → collect → classify each layer (or classify-batch) → patch/check → resolve each active layer → remaining native obligations → verify → formal task acceptance → remaining native Finalize/Commit → done.
- Oneshot: prepare → attach each layer → collect → classify each layer (or classify-batch) → patch/check → submit separate final reports → remaining native obligations → verify → formal task acceptance → remaining native Finalize/Commit → done.
- Unchanged-code oneshot direct submit remains a compatibility entry; it shares base judgment/identity/mapping checks and requires every active ticket. It does not allow unclassified patch changes.
- OpenSpec/Spec Kit follow their installed native workflows and shared verification/acceptance. They do not need BMAD tickets, layer names or generated directories.

### Recovery and compatibility

Queries do not migrate or recertify old records. Consistent legacy model/evidence
records remain readable without the new contract model field. Inconsistent or
unverifiable records return a blocker. Historical inactive records are excluded
from current completion; controlled whole-round restart archives all old layers.
Single-layer evidence damage with unchanged common inputs may restore original
bytes or explicitly restart that layer, retaining other valid evidence. Common
input/model/contract changes require whole-round replacement. Restart alone cannot
remove a recorded frozen intent conflict; native clarification must change the
applicable intent/requirement first.

The restart conflict check includes removed layers from the previous round.
Changing the rendered layer list cannot retire a frozen-intent conflict. CLI
regressions also exercise same-contract model replacement rejection and the full
remove-layer → archive → classify → submit sequence for a non-conflicted layer.

No dependencies were added. This work preserves prior knowledge/runtime/timeout
configuration commands and their existing evidence-invalidation boundaries.

### Final verification

Final Python discovery: 956 distinct tests executed using six module-preserving
process shards. Five shards (796 tests) passed initially. The remaining 160-test
shard passed in full after correcting the temporary runner's missing `__main__`
guard (132.034s). The initial spawn-based lock test failure was caused by the
runner reexecuting tests inside its child process; the 12-test state-store module
also passed directly with unittest (0.399s). No product-lock changes, weakened
assertions or extended timeouts were used. Final coverage is 956/956 passed;
this is not a claim of a single uninterrupted sequential discovery pass.

Commands: `python -m unittest discover -s tests -v` (sequential run interrupted
to switch to module-preserving shards); complete discover/loadTestsFromNames via
the temporary guarded Python runner; `PYTHONPATH=.:tests python -m unittest
test_state_store -v`; `npm run test:pi`; `npm run check:pi`; `make check`;
`git diff --check`. The three additional round-boundary regressions also passed
in a direct targeted unittest run. Repeated runs are not added to the distinct
956-test count.

Pi: 37 passed; TypeScript check passed. Compileall and diff checks passed.
Skill/reference/template relative link scan: 24 files, 30 links, no errors;
review/runtime/knowledge/verification configure CLI help parsing passed.
The tests use real local CLI and fixture host observations. They do not constitute
real-model review or upstream BMAD/OpenSpec/Spec Kit Agent end-to-end replay.
