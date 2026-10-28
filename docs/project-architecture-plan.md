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
