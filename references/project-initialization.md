# Scope-owned initialization before business Work

Contents: Identity interview and scaffold; Exact plans and checks; Independent project baseline; Recovery.

Honor provider installation and host restart first. Restore the original request,
then use project inspect/context to record actual technology choices. Confirm only
missing applicable identity and layout choices; never infer user authorization
from generated files. A package/distribution name, namespace/import name, module
path and local directory are different identities.

For empty runtime products, no business Work is needed or allowed until setup is
ready. No temporary Work, no blanket src/ exemption, no application functionality
in provider outputs. Existing products are reused and active Work retains progress.

1. `cao project setup rules` lists built-in identity/layout conveniences. These are
   not a language whitelist. Inspect the required fields and capabilities.
2. Collect applicable identity, versions and layout with a sequential interview.
   Run `cao project setup identity --rule <rule> --scope <scope>`; ask only the
   returned `next_field`, wait for the user to enter it, then save with the same
   command plus `--field <field> --value <answer> --evidence <observed-user-id>`.
   Repeat until ready. Reuse saved answers after restart. Do not invent a bundle
   of names, group IDs or namespaces and ask for blanket acceptance; do not ask
   users to type JSON or internal field strings. Explain derived package/path
   suggestions and confirm them individually. For another stack, use
   `--rule explicit --fields <field>` repeatedly to declare its ordered fields.
   Obtain each host-observed evidence ID from project inspect. Draft automatically
   reuses the collected values and per-field evidence for built-in and explicit
   plans; complete the explicit plan using its exact prepared input.
   Framework-aware rules return additional fields and `field_guidance`. For
   Spring Boot, save its exact `framework_version`; no default/latest release is
   authorized. Existing confirmed framework versions and their evidence are
   reused. A JUnit generation answer refines a previously confirmed generic JUnit
   choice during unbound initialization. `junit_version=5` means Spring Boot's
   BOM-managed JUnit 5; without such management provide an exact dependency
   release (major.minor.patch), never a major number as a Maven coordinate.
3. Draft a built-in plan, for example:

   ```bash
   cao project setup draft --rule python-unittest
   ```

   `draft` returns an exact `.orchestrator/inputs/project-<scope>/setup.json` path.
   Only this prepared file is Agent-editable. Repeated draft preserves edits.
   Adapt framework and application/library shape instead of silently using an
   inappropriate library template. When applicable, record `framework_support`
   and `delivery_support` with the exact accepted context values. These labels
   do not certify correctness: actual framework loading/startup must be checked.
   Maven/Gradle Spring Boot drafts already include the confirmed framework,
   application entry and a real context-load test. To regenerate an old prepared
   draft from saved answers, inspect it first and use explicit `draft --refresh`;
   this replaces only the prepared input and does not overwrite product files.
4. For another stack/combination use `cao project setup draft --scope <scope>` and
   fill the same schema: version 1, rule `{id: explicit, version: 1}`, nonempty
   `basis`, identity/evidence/source, exact files with content and purposes
   (manifest/entry/test/resource/verification), checks with name/command/capability,
   required_capabilities, and exact generated outputs. No new plugin is needed.
   Product files and check cwd must be within the accepted scope. Exact verification inputs may additionally live in `.orchestrator/scripts/verification/<scope>/` with purpose `verification`. Prefix module paths in
   file/output entries; check cwd defaults to that module's first root.
5. Review the concrete plan, then run:

   ```bash
   cao project setup plan --scope <scope>
   cao project setup apply --scope <scope>
   cao project setup check --scope <scope>
   cao project setup status --scope <scope>
   ```

Plan/apply validate paths, existing content, symlinks, collisions, user identity
sources and context revision. Conflicts preserve user files. Apply creates only
exact planned files and updates the existing project verification declarations;
it does not execute checks or claim ready. Checks use the ordinary process
permissions of trusted project commands, not an OS sandbox.

Checks run outside state locks, with timeout/process cleanup and bounded output.
Generated/cache outputs are declared before execution; tracked inputs stay inputs.
Required tests must have nonempty actual discovery evidence. A zero exit code,
`true`, empty suite or import-only result cannot prove tests. Missing tools,
failed commands, changed inputs and zero tests do not establish ready. Repair the
actual scaffold/test/entry, then rerun check. Normal dependency resolution stays
within the agreed project toolchain; global installation is not authorized.

After apply and before ready, host write/edit/patch may repair only the exact
planned scaffold files. Plan-external business files remain denied. Then rerun
`project setup check`; do not reapply an old version over the repair. Prepared
setup input is independently editable, but derived setup state is command-owned.

Identity fields retain individual evidence in `identity.sources`. A later plan
reuses unchanged values and their original proof; changed values require observed
user evidence covering them. Use the optional `identity_evidence` field to bind
different fields to different observed evidence IDs instead of asking again for
the whole identity.

Generated source suffix grants use `generated_output_suffixes`, keyed by a
declared output directory, for example `{"dist/": [".js"]}`. They never authorize
preexisting product source or protected manifests/checker scripts as disposable
outputs. Tracked inputs remain inputs. Rule version changes invalidate readiness
and require a new applicable plan/check.

An interrupted check records its exact run ID and process owner. Inspect status;
after that owner has exited, recover only that run with
`cao project setup check --recover-run <run-id>`. A live owner is not overridden.
Unsuccessful post-run input reads persist stale and release the running owner;
changed configuration or product inputs cannot create a fresh ready result.

Initialize all applicable capabilities: build/syntax/type/load/startup/test.
Non-runtime artifacts need explicit applicability and evidence; never force a
server entry into a library or demand compilation from an interpreted package.
Built-in examples are drafts, not an exhaustive framework generator.

After scaffold ready, establish the independent project script baseline BEFORE
creating the original business Work. Inspect the checks against the accepted
technology and project quality standards, run `cao verification materialize
--scope <scope>` to persist them under `.orchestrator/scripts/verification/`,
then run `cao verification check --project --scope <scope>`. Alternatively reserve,
write and configure actual project wrappers before that check. Missing wrappers,
failed checks and changed standards block Work. Initialization success cannot
substitute for this separate preparation receipt. Rerun bootstrap only after it
passes; the Work inherits project context and verification declarations. Initialization results never fill
Work verification, native review, or requirement acceptance gates. Ordinary later
business edits do not reset initialization; context or baseline changes require
rechecking the affected project preparation.

Existing active Work may use `cao verification check --work <id>` before native
review or baseline acceptance to trial its current declared checks. This is a
preparation result, not final `cao verify` or SDD acceptance. Keep formal gates.

Existing products establish actual required entries and run `cao verification check` before creating new Work. For wrapper preparation, migration and exact write grants see [verification baseline](verification-setup.md#project-preparation-before-work).

## Project architecture Skills

Before planning, scaffolding or business implementation, inspect `cao project
architecture show --scope <scope>`. Read every returned Skill path, including
inherited root rules. Use accepted package identity and scope paths. For missing
rules follow [architecture selection and adoption](project-architecture.md).
Do not infer DDD merely from Spring Boot. Plans must place files by responsibility;
provider review must cite applicable ARCH rule IDs and assess semantic boundaries.
Resume, switching and child agents preserve the Work architecture binding. Changed
rules require reviewed `project bind --refresh`; never reset Work progress.
