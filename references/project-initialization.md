# Scope-owned initialization before business Work

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
2. Confirm applicable identity, versions and layout with the user, or reuse actual
   scoped delegation. Obtain the host-observed evidence ID from project inspect.
3. Draft a built-in plan, for example:

   ```bash
   cao project setup draft --rule python-unittest --identity '{"name":"demo-dist","import_name":"demo_import"}' --evidence <observed-user-id>
   ```

   `draft` returns an exact `.orchestrator/inputs/project-<scope>/setup.json` path.
   Only this prepared file is Agent-editable. Repeated draft preserves edits.
   Adapt framework and application/library shape instead of silently using an
   inappropriate library template. When applicable, record `framework_support`
   and `delivery_support` with the exact accepted context values. These labels
   do not certify correctness: actual framework loading/startup must be checked.
4. For another stack/combination use `cao project setup draft --scope <scope>` and
   fill the same schema: version 1, rule `{id: explicit, version: 1}`, nonempty
   `basis`, identity/evidence/source, exact files with content and purposes
   (manifest/entry/test/resource/verification), checks with name/command/capability,
   required_capabilities, and exact generated outputs. No new plugin is needed.
   Files and check cwd must be within the accepted scope. Prefix module paths in
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

After ready, rerun bootstrap and create the original business Work. It inherits
project context and verification declarations. Initialization results never fill
Work verification, native review, or requirement acceptance gates. Ordinary later
business edits do not reset initialization; context or baseline changes require
rechecking the affected project preparation.

Existing active Work may use `cao verification check --work <id>` before native
review or baseline acceptance to trial its current declared checks. This is a
preparation result, not final `cao verify` or SDD acceptance. Keep formal gates.
