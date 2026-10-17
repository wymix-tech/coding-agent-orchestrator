# Verification baseline

## Enter when

An active Work exists and bootstrap reports `mode: verification_setup`, or accepted verification declarations/wrappers have changed.

## Execute

After honoring SDD selection/install authorization and creating or resuming an active Work, `mode: verification_setup` means the verification
configuration is empty, invalid or optional-only. Before feature development:

1. Existing project: analyze product manifests, source, tests and CI; reuse meaningful checks. Host assets,
   `.orchestrator` derived state, `.agents`, `.pi`, SDD tools/outputs and caches do not identify the product
   stack; current requirements remain relevant, including intake documents.
2. Apply [technology clarification](work-intake.md#technology-clarification-before-planning-or-scaffolding) before generating wrappers/manifests. New project: honor explicit choices or scoped delegation; otherwise ask the user about the remaining material language/framework/build/test choices and wait. Agent-written requirements, SPECs and newly generated manifests do not establish user authorization. Existing product evidence is different from scaffolding generated for this request. If unable to ask, report `TECH_STACK_CLARIFICATION_REQUIRED` and stop stack-dependent work. Restore recorded choices after restart instead of asking again.
3. Generate project-owned wrappers or reuse existing scripts. Zero tests, missing tools or missing
   dependencies must fail/block, never produce a fake `echo`/`true` pass. Java/Jupiter requires
   JUnit5-capable Surefire and failure on zero tests. Do not overwrite scripts or install dependencies automatically.
4. Declare only applicable scripts through the safe command:

   ```bash
   cao verification configure --test-script "<actual project test wrapper>" --verify-script "<actual general check>"
   ```

   Different nonempty/invalid declarations need explicit `--replace`. Identical declarations are unchanged.
   Never hand-edit derived config or use `init --force` to configure verification.
5. Rerun bootstrap before business implementation. Configuration readiness is not verification success:
   within an active Work, initial scaffolding and tests may be written before tests pass. Keep setup limited
   to build/test scaffolding and verification wrappers; follow path assessment and native SDD instructions
   before business implementation.
   The first valid baseline on a Work started with empty verification is established without reopening. Existing accepted baselines keep their anchor; review any changed standard and explicitly reanchor with
   `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`. Do not replace the Work or its decisions.

CAO does not call an LLM, generate stack-specific templates, parse test reports or detect zero tests;
those checks belong to the project's wrappers. `--verify-script` does not automatically declare tests.

Put new verification wrappers in `scripts/verification/`; `scripts/` alone is not an allowed setup root. Setup-only roots are `tests/`, `test/`, `acceptance-tests/`, `src/test/`, `scripts/verification/`, `.venv/` and `venv/`; supported build/test manifests remain writable with an active Work. Preserve the user's selected language, framework, runner and delivery shape. Do not substitute main programs for JUnit 5 when dependencies are missing.

Register a combined tests/general wrapper only once as a test script. For security checks use `cao verification configure --security-script <wrapper>`; the same exact command can declare both tests and security without duplicate execution. Same-path wrapper content changes require baseline review too. Installed BMAD Build requires host-observed `批准验证基线` (approve verification baseline); Agent reanchor reason text is not approval. Repeating configuration can request the current approval; only then reanchor. Active reanchor preserves business phase and decisions.

After path assessment, configure any required security capability before implementation, rather than discovering it during final verification. A baseline approval can be a direct user reply or a bound `ask_user_question` answer: create the current request first, show the changes, then ask once. Include “验证基线” or “verification baseline” in that question so it binds to the baseline instead of a SPEC request. A plain “批准” is accepted when no competing SPEC approval is pending; otherwise use the explicit baseline response. Results from questions asked before the request, replaced baselines, other Works, subagents or unrelated sessions cannot approve it.
Use exact approval labels. A selected structured action may include a UI
`(Recommended)` / `（推荐）` badge; CAO removes that badge when matching, while
preserving the original host response as evidence. Inspect the approval status
before asking again; an already recorded approval needs reanchor, not a second vote.

For an existing verification wrapper, a literal `chmod +x scripts/verification/<wrapper>`
is allowed during active Work setup. Recursive/reference permission operations and product/state
targets are not setup exceptions.

### Script contract

CAO does not define project-specific quality metrics and does not parse tool-specific reports. Put the
verification logic in user-owned scripts and configure their commands in `.orchestrator/config.json`:

```json
{
  "verification": {
    "timeout_seconds": 300,
    "scripts": [
      {"name": "unit-tests", "command": "./scripts/verify-unit-tests.sh", "required": true, "gates": ["tests"]},
      {"name": "coverage", "command": "./scripts/verify-coverage.sh", "required": true},
      {"name": "security", "command": "./scripts/verify-security.sh", "required": true}
    ]
  }
}
```

Use this exit-code protocol for every verification script:

```text
0     PASS
1     FAIL
2     BLOCKED / CANNOT VERIFY
>=3   ERROR
```

The script should print a concise reason to stdout or stderr. CAO records that reason together with
the exit code and command output. Verification passes only when every required script returns `0`.
Thresholds such as 100% test success, 80% line coverage, 80% branch coverage, or mutation kill rate
are implemented by the project script itself.

Use `cao verification configure --test-script` for required test capability, or `--verify-script` for general checks.
`cao verify --script` is a one-off engineering check and cannot satisfy completion evidence. Scripts are
trusted, not sandboxed; each has a finite positive `timeout_seconds` (default 300), process-tree cleanup,
4000-byte output tails and a `timed_out` result (124/error on timeout). Input changes during execution
mark the saved result stale. Snapshots prune ignored directories; internal file links use lexical names,
directory links are not followed, and external file links/read failures cannot certify freshness.

## Exit when

The required baseline is accepted. Rerun bootstrap, then follow path assessment and native prerequisites before business implementation. Configuration readiness is not verification success; initial skeletons/tests may fail.

## If blocked

Missing tools, missing dependencies and zero tests must fail/block. Ask for missing stack choices; do not install dependencies, overwrite scripts, fake a pass or use `init --force`.
