# BMAD Execution State Adapter

## Authority mode

When an active BMAD sprint-status file exists, use:

```yaml
provider: bmad
authority_mode: native
```

BMAD native sprint/story status remains authoritative for fields it owns. The orchestrator must not maintain a competing writable story status.

## Projection

The adapter should map the installed BMAD version's native story/sprint lifecycle to canonical phase/status. Do not hard-code one historical BMAD schema when the repository contains version-specific guidance.

Example conceptual projection:

```text
backlog / ready-for-dev  -> planning / ready
in-progress              -> implementation / in_progress
review                   -> review / in_progress
done                     -> closed / completed
```

This is a conceptual mapping only. Discover the repository's actual native values before writing.

## Mutation

For a requested phase/status change:

1. evaluate canonical transition guards;
2. invoke the native BMAD workflow/state mechanism;
3. reload native status;
4. verify that native state reflects the intended transition;
5. call canonical `sync-native` with the native reference/revision;
6. append evidence/history.

Do not directly rewrite `sprint-status.yml` unless the installed BMAD workflow explicitly defines that as the supported update mechanism.

## Extension state

Store only data BMAD does not authoritatively own, such as:

- quality-gate details;
- fresh verification snapshot;
- additional blockers/evidence;
- orchestrator role assignments;
- next-action cursor;
- fact/decision snapshot references.

## Close

A BMAD story may only be reflected as canonically closed after both:

- BMAD's native completion state is confirmed; and
- orchestrator close guards pass.
