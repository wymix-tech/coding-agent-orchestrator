# Pi native-route regression maintenance

This is a CAO development note. Runtime instructions ship under `references/`;
installed clients must not need this checkout's `docs/` directory.

Keep regression coverage at the layer that owns the behavior:

| Behavior | Development test surface |
| --- | --- |
| Bootstrap and native preparation write boundaries | `tests/test_pi_adapter.mjs`, host policy tests |
| Route preparation sequence and Work-specific output paths | `tests/test_pi_route_guidance.py` |
| Installed stage/Provider/action resource selection | `tests/test_skill_guidance.py` |
| Native review report validation and freshness | Native obligation and native route tests |
| Acceptance examples, real executors and identity/coverage | `tests/test_acceptance_protocol_template.py` and acceptance policy tests |
| Wheel resources outside a source checkout | `tests/installed_package_smoke.py` |

CLI fixture tests establish routing and validation behavior. They do not establish
that a real model followed the native prompts, ran independent reviewers or made
semantically correct judgments. Keep claims about actual host/model sessions tied
to their original logs; do not convert synthetic fixtures into execution history.

When adding a regression, preserve existing Work identity, accepted technology
choices, native task scope and provenance. Publish only runtime assets in wheel/npm;
inspect actual archives rather than relying solely on source-relative links.
