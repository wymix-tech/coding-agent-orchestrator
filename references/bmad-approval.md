# BMAD dispatch approval

Read for `NATIVE_SPEC_CLARIFICATION_REQUIRED`, `NATIVE_SPEC_APPROVAL_REQUIRED`
or `NATIVE_IMPLEMENTATION_PAUSED`. These rules apply to BMAD dispatch; oneshot
and other Providers do not inherit this approval requirement.

## Request and observe

Resolve every native Open Question first. Present the complete current SPEC
summary and path, then run `cao sdd approval request` and inspect
`cao sdd approval show`. The request binds current Work, route decision,
requirement and SPEC frozen intent.
Before requesting approval, check that frozen constraints and the I/O matrix are
mutually satisfiable. If they conflict, ask the human to resolve the intent before
freezing; do not invent an exception. If discovered after approval, use `cao block
--reason "<actual contradiction>"` and the native intent_gap loopback. Recording a
note or giving one frozen clause priority does not resolve it. Re-read the current
SPEC before acting on approval and refresh route/approval evidence after intent changes.

Wait for a host-observed standalone message `批准并继续` / `批准并停止`
(or `Approve and continue` / `Approve and stop`). Additional explanation in the
same message is not a standalone approval. Pi can also observe an actual
structured `ask_user_question` result. Generic continue, technology-choice answers,
Agent statements, CLI reasons and edited status fields cannot approve.
Keep action labels exact; presentation badges `(Recommended)` / `（推荐）` are
ignored only for bound structured answers, never free-form messages.
Answering Open Questions does not approve the completed SPEC or authorize marking
it ready-for-dev as if approved. Follow the native approval checkpoint separately.
Approve-and-stop keeps implementation paused until explicit `继续实现`.

## Session and freshness

Approval binds Work ID and the associated primary host session. A new session
must receive current Work context before approving. A structured question needs
observed pre-tool and post-tool events with the same tool-call ID and approval
request ID; replacing a request or route invalidates the old question's result.
Never fabricate host events. Without host observation leave approval pending.

For historical approval lacking these bindings, issue a fresh request and obtain
current host-observed approval. Verification-baseline approval has its own request
and `批准验证基线` response; use the verification setup guide for that action.
Intent changes require new approval; status/task-checkbox progress does not.
Approval cannot replace native tasks, independent reviews or formal verification.
