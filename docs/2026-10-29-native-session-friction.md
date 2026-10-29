# Native session friction repair

Base: main after PR #38. Branch: fix/native-session-friction.

Implemented:
- Native check gate references require existing repo files and exact SHA256. Technical check references additionally require current native execution receipts.
- Review begin exposes one blocking tasks batch containing exact frozen prompt bytes. Agent names must be supplied from the installed host definitions; the caller must inspect host batch capability.
- Dispatch navigation points to canonical drafts; repeated draft calls preserve judgments.
- Resolution drafts expose review snapshot freshness and nested verification examples. `resolution-draft --refresh` preserves assertions and historical verification, but clears observed results/evidence when the snapshot changes.
- Unchanged requirement-bound SPEC commands can be discovered before the first native execution; source changes revoke discovery. Only familiar build/check tools and check verbs are discovered; custom commands retain explicit declarations.
- Agent output includes lifecycle execution; Maven class summaries plus aggregate are counted once.

Validation:
- 24 focused evidence, resolution and prompt-package tests passed.
- 19 lifecycle/session replay tests passed, including cross-session three-layer NO_VCS completion.
- Pi adapter: 39 passed. TypeScript check and make check passed.

Remaining integration scope (not claimed complete):
- Real installed third-party Pi subagent workflow capability discovery, execution and retained-child ID mapping have not been exercised in this environment. The generated tasks batch is guarded locally, not proof that every installed plugin accepts this interface.
- Native criterion semantic coverage, metadata-only findings extraction, tool-role product inventory filtering, and a dedicated legacy evidence migration remain to be completed before declaring the original six-stage plan fully implemented.
- No live macOS/Java/LLM end-to-end session was executed.
