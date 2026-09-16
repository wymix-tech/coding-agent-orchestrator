# Collecting evidence and authenticating its source

All receipt consumers use the same rules: the exact target, work item, requirement
revision, and code before/after execution must match. A new evidence ID or refreshed
dependency list cannot update an old execution. A run that changes the code is a run
log, not verification of the final code; run verification again after changes finish.

## Execute and consume a result

```bash
python3 scripts/coding_orchestrator.py --repo /project evidence run \
  --target gate:unit --work-item W-1 -- python3 -m unittest discover -s tests
```

The current requirement revision is read before execution from the selected work item
and its source. `--requirement-revision` is an optional assertion, checked against that
version. Without a current state, supply a concrete revision explicitly. Missing or
conflicting scope is rejected before running the command.

Use the returned receipt path with the state CLI:

```bash
python3 scripts/execution_state_manager.py --state /project/.orchestrator/execution-state.yaml \
  gate --name unit --required true --status passed --actor runner --evidence-ref RECEIPT_PATH
```

For review and final verification, collect separate `--target review` and
`--target verification` runs. A unit receipt cannot fulfill either target. A process
exiting successfully proves that execution succeeded; projects remain responsible for
selecting appropriate tests and review programs.

Progress observers print an object such as `{"tests":{"completed":1,"total":2}}`.
The captured JSON output is part of the receipt identity. A `task_progress` evidence
record references that receipt; extra fields in a wrapper cannot change the count.
Do not synthesize test/task counts from a command that did not observe them.

## Signed approvals

Unsigned local approval/event files remain unverified. The runtime verifies externally
issued Ed25519 events using `cryptography` from requirements.txt. It never creates or
stores approval signing keys. Configure public keys in the protected project policy:

```yaml
orchestrator:
  evidence:
    approval_authorities: [reviewer-1]
    approval_audience: example-project
    approval_keys:
      issuer-key-2026:
        public_key: BASE64_RAW_ED25519_PUBLIC_KEY
        approvers: [reviewer-1]
        channels: [external_adapter]
```

The trusted approval service signs this complete event after authenticating the person
and recording their decision:

```json
{
  "id": "event-123",
  "event": "approval",
  "key_id": "issuer-key-2026",
  "audience": "example-project",
  "channel": "external_adapter",
  "approver": "reviewer-1",
  "subject": "W-1",
  "work_item_id": "W-1",
  "requirement_revision": "rev-current",
  "decision": "approved",
  "fact_path": null,
  "value": null,
  "signature": "BASE64_SIGNATURE"
}
```

Signing input: bytes `orchestrator.approval.v1\0` followed by UTF-8 JSON of **all event
fields except signature**, with sorted keys, separators `(',', ':')`, and
`ensure_ascii=False` (see `approval_auth.signing_bytes`). The signature is Ed25519 over
those bytes. Only the external issuer holds the private key; a key shipped with the
project or accessible to an agent submitting claims would not authenticate a person.
The default audience is the resolved project path; configure a stable audience for
approved portability across checkouts.

Import the issued event:

```bash
python3 scripts/coding_orchestrator.py --repo /project evidence approve \
  --approver reviewer-1 --subject W-1 --work-item W-1 \
  --requirement-revision rev-current --channel external_adapter --channel-ref signed-event.json
```

The command checks the event before writing. Its returned approval ID and path can be
referenced by a `human_approval` record with matching scope. For a semantic decision,
the issuer must sign `fact_path` and the JSON `value`; import those same values with
`--fact-path` and `--value`. A general approval cannot be relabelled as an observation
of a different predicate or value. Consumer-specific evidence rules still apply.

Consumption re-reads the signed source and configured public keys. Removing an event
from the authoritative channel artifact or removing its public key invalidates its
use. An append-only local hash chain is an audit mechanism, not authentication. If
there is no trusted issuer integration yet, use a supported native approval source or
leave the claim unverified; do not generate a replacement approval inside the agent.

## Semantic observers

Running a command that prints `false` does not prove absence of risk. A semantic
observer must be explicitly selected by project policy for its predicate:

```yaml
orchestrator:
  evidence:
    observers:
      risk.security_sensitive:
        argv: [python3, tools/security-observer.py, requirements/story.md]
        files:
          tools/security-observer.py: SHA256_OF_REVIEWED_OBSERVER
        inputs: [requirements/story.md]
```

The policy owner must review the observer's meaning and supported format. Include all
program/configuration files on which its behavior depends in `files`, and all inputs
in `inputs`; the active requirement source/members must be covered. There is no
default observer capable of proving arbitrary requirements safe.

`evidence run --fact-path risk.security_sensitive --target fact:risk.security_sensitive`
accepts only the exact configured argv and program digests. The observer must read
those inputs and print `{"fact_path":"risk.security_sensitive","value":false}`.
The policy rule, input digests, output, and code snapshot are bound to the receipt and
rechecked on use. Changing the program, policy, or input invalidates the observation.
Use an actual signed decision when the predicate is not mechanically decidable.

## Upgrade

Re-run old mechanical results whose execution binding is absent/stale. Reimport
approvals from a configured signed channel. Recollect old progress reports that did
not capture counts in execution output. Keep original records for audit; do not
rewrite receipts or silently mark legacy claims verified. Project policy and the
receipt store retain their existing protected-workspace trust boundary; arbitrary
same-process code or an owner who can rewrite all trusted configuration is outside it.
