# Project Architecture Skills implementation plan

Baseline: main 7144ca7. Architecture belongs to accepted project scopes; Markdown
is authoritative, binding receipts contain hashes only. Existing context binding
and readiness checks will carry architecture freshness. Existing products retain
layout; adoption is explicit. Commands never execute instructions in Markdown.

- P0: record design and push this checkpoint.
- P1: bounded Markdown/YAML-compatible JSON frontmatter protocol, discovery,
  template catalog, validation, draft/diff/apply and atomic acceptance receipts.
- P2: scope readiness, Work binding and refresh, initialization guidance.
- P3: context propagation through shared guidance, stale detection and recovery.
- P4: explicit structural checks and review obligations, Java dependency checks.
- P5: behavioral regressions, packaging, documentation, full regression suite.

Acceptance: first and subsequent Work inherit scope rules; root inheritance;
content changes invalidate bindings; unrelated scopes remain fresh; duplicate IDs,
unsafe paths and unresolved variables fail; apply preserves edited files; custom
rules work without core edits; unsupported semantic checks never report passed.

Migration: legacy Work keeps progress and can explicitly adopt using project bind
--refresh. Existing code is inspected and documented before architecture adoption.
No automatic rewrite, template upgrade, global dependency install or command
execution from rule content. Manual semantic review remains provider-owned.

## Implementation checkpoints

P0 saved remotely before implementation. P1 added the protocol, 14 templates,
custom discovery and CLI acceptance. P2/P3 added project readiness, Work binding,
setup-plan binding and shared resume payload/guidance. P4 integrates Java import
and flat-source checks into `cao verify`, retaining provider semantic review.
P5 adds schema, runtime packaging, migration/extension guidance and real CLI tests.

Architecture frontmatter supports JSON (YAML subset) and ordinary safe YAML.
PyYAML is declared as a Python dependency; JSON built-ins need no YAML parser to
load. New explicit init/bootstrap writes architecture_policy=1; pre-adoption
configurations retain legacy readiness until a rule is explicitly adopted.

### Traceability

| Requirement | Implementation / validation |
| --- | --- |
| First Work and later Work | project prerequisite + make_binding; ArchitectureCliFlow |
| Root/module inheritance | snapshot selects root and scoped rules; isolation tests |
| Resume/child context | resume_payload + generated guidance include paths/hashes |
| Rule changes | accepted receipts and Work bindings hash full Markdown |
| Initialization | normalize_plan accepts current rules; plan/apply/state bind hashes |
| User extensions | project custom/*.md catalog; duplicate ID tests |
| Existing files | prepared inputs, explicit apply and expected current hash |
| Flattened Java code | java-source-layout + real failing class regression |
| Forbidden dependencies | Java explicit-import checks with real fail/pass fixtures |
| Unsupported semantics | reported unsupported; provider review guidance |
| Verification freshness | architecture enters technical_policy_sha256 |
| Packaging | existing templates/ archive/npm includes; Python data-files addition |
| Recovery | existing project bind --refresh; no Work phase reset |

Automated dependency analysis is deliberately limited to declared Java explicit
imports and source-root layout. Fully qualified references, reflection and semantic
boundaries are review obligations, as are non-Java dependency rules. Architecture
Skills do not generate entire business modules; initialization uses existing exact
setup plans and the Agent places requirement-owned code using the accepted Skill.

Full regression results will be recorded after the final run; a checkpoint is not
a claim that all acceptance checks passed.
