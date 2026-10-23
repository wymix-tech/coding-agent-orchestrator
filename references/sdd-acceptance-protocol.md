# Independent SDD acceptance contract

Read when configuring or repairing independent final acceptance for
standard/comprehensive Work. This contract applies to all Providers; native
review and archive rules remain in their Provider guides. Light does not need
manufactured independent acceptance records.

## Inspect the authoritative protocol

Run `cao sdd acceptance protocol` without an active Work to obtain the current
`request_schema`, `report_schema`, valid examples and installed `executor_template`.
Use those schemas as the field authority. The example identity is illustrative;
actual reports must echo the current run's identity.

## Configure a declaration

Use repository-relative paths and `cao sdd acceptance configure --declaration
<existing-repo-json>`. Required fields are `spec`, nonempty `criteria` and
`executor`. Each criterion has a unique `id`, real native `source`,
`kind: requirement` or `kind: design`, and nonempty `description`. At least one
criterion must be a requirement. Optional `sources` adds frozen native inputs.

`executor.mode` is `command` or `skill`; `entrypoint` is the actual command or
installed native Skill identity, respectively. `timeout_seconds` is positive.
`outputs` lists the exact Provider-owned Work-scoped files that the executor will
write, or `[]` for command stdout-only results. Do not overwrite unowned existing
reports or register inputs as outputs. Review the declaration and sources,
explicitly reanchor, then run `cao verify` before starting acceptance.

## Request and report

Both modes receive `{run_id, input_sha256, provider, inputs, declaration}`.
`input_sha256` is the current 64-character lowercase hexadecimal digest.
Evaluate every `request.declaration.criteria` entry against the frozen inputs.

Return `{run_id, input_sha256, provider, items}` with the exact current identity
and exactly one item per criterion. Each item needs `id`, `status` (`passed`,
`failed`, `blocked`) and a nonempty `evidence` array of `{path, reason}` objects.
Every evidence path must be a real repository file; explain the actual check.
Never copy a sample passing result. Every declared output must really be written.

Use the command or Skill guide selected for the declared executor mode. Source,
code, checks and evidence changes can invalidate the snapshot; reanchor/reverify
as directed. Submitting a well-formed report does not prove an independent
reviewer ran. Preserve native independence and its human-return fallback.
Malformed fields expose a stable error code and actionable `diagnostic`.

## Illustrative request and blocked report

These are schema-valid examples from `protocol`, not execution evidence. Replace
sample identities/paths with the current request and actual evidence.

```json
{"run_id": "sdda-example", "input_sha256": "0000000000000000000000000000000000000000000000000000000000000000", "provider": "openspec", "inputs": {}, "declaration": {"spec": "openspec/changes/add-demo/spec.md", "sources": ["openspec/changes/add-demo/spec.md"], "criteria": [{"id": "AC-1", "source": "openspec/changes/add-demo/spec.md", "kind": "requirement", "description": "Real CLI behavior is correct"}], "executor": {"mode": "command", "entrypoint": "python3 scripts/verification/acceptance.py --checks acceptance-checks.json", "timeout_seconds": 300}, "outputs": ["openspec/cao-acceptance/<work-id>/report.json"]}}
```

```json
{"run_id": "sdda-example", "input_sha256": "0000000000000000000000000000000000000000000000000000000000000000", "provider": "openspec", "items": [{"id": "AC-1", "status": "blocked", "evidence": [{"path": "openspec/changes/add-demo/spec.md", "reason": "Example only; configure and execute a real check"}]}]}
```

## Project-bound dual acceptance

Use `cao acceptance criteria` to obtain stable source IDs from the confirmed requirement and native criteria/Intent. A declared criterion may include `covers: ["src-..."]` to map original source items; exact source/text matches are also recognized. Reports still cover every declared criterion exactly once with real evidence. Source mapping alone does not establish semantic correctness or independent execution. In light, reuse the bound native Intent rather than manufacturing a formal checklist. Inspect both parts with `cao acceptance status`; technical results, task evidence and original native obligations must all be valid before done.

For an already bound native SPEC/Intent, `cao acceptance configure --executor-command "<actual check>"` or `--executor-skill <native-entrypoint>` derives the declaration from existing source items, preserving IDs and coverage mappings. Use --spec for an explicit native source and --output for exact Work-scoped reports. This is configuration only; actual run/begin/submit or validated reuse is still required.
