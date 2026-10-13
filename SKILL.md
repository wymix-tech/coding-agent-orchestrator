---
name: coding-agent-orchestrator
description: Coordinate repository coding work as a persistent work item across sessions and AI agents. Use this skill before starting, resuming, modifying, verifying, handing off, or completing a non-trivial coding requirement. It tracks execution state, next action, decisions, blockers, handoff, and verification while leaving SDD artifacts and native workflow to the project's SDD provider.
---

# Coding Agent Orchestrator

CAO is the repository's work continuity layer. It is not an SDD and not a coding agent.

## First action

This skill is normally triggered by the project-level Agent instructions. The `AGENTS.md` and
`CLAUDE.md` files are shipped as templates under `templates/`; `cao init` installs the one the
provisioned host reads (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex and Pi, both when no host
can be detected); `cao bootstrap` re-checks them idempotently. Existing project instructions are preserved. The Skill itself is installed at
`.agents/skills/coding-agent-orchestrator/` and intentionally contains no nested `.agents/` tree.
At the start of repository coding work, run exactly:

```bash
cao bootstrap
```

Host hooks (SessionStart, PreToolUse, Stop) are process-startup integrations and are wired by
`cao init --host <claude-code|codex|pi>`, not by `cao bootstrap` — a bootstrap-time install would miss
the current session's own `SessionStart`. If `cao bootstrap` reports `host_adapter.installed: false`, run
`cao host install --host <host>`; it takes effect from the next session. Hooks only remind about completion;
`cao done` is the hard gate even without hooks. Run `cao resume`, `cao checkpoint`, and `cao handoff`
yourself when lifecycle events cannot be observed.

### Landing a one-sentence requirement

A requirement stated inline has no file yet, and `cao start` needs one. CAO owns the location and
numbering; you own the content:

```
cao requirement next-id --title "<short title>"
```

creates the numbered file and returns its `path` (for example
`.orchestrator/requirements/001-requirement-用户登录-2026-10-10.md`). CAO creates it with a skeleton
to allocate a stable intake path. Requirement intake stays writable without a Work. Product code,
configuration, tests and verification scaffolding require an active Work; missing assessment on an
active Work still yields reminders. Edit that file with the requirement content,
then `cao start --requirement <path>`. Do not create the file yourself and do not place requirement
documents anywhere else — CAO allocates numbers from existing files so they never collide, and
`cao requirement list` shows what already exists.

Create or resume Work before verification setup. Empty verification configuration and failing tests
do not prevent `cao start`. If work is already active or resumable, continue it rather than creating
another requirement.

### First-project verification baseline

After honoring SDD selection/install authorization and creating or resuming an active Work, `mode: verification_setup` means the verification
configuration is empty, invalid or optional-only. Before feature development:

1. Existing project: analyze product manifests, source, tests and CI; reuse meaningful checks. Host assets,
   `.orchestrator` derived state, `.agents`, `.pi`, SDD tools/outputs and caches do not identify the product
   stack; current requirements remain relevant, including intake documents.
2. New project: use the user's explicit stack; otherwise ask the user for language/framework/build/test
   choices and wait. Do not guess a stack when the channel cannot ask.
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
   Existing Work keeps its acceptance anchor; review any changed standard and explicitly reanchor with
   `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`. Do not replace the Work or its decisions.

CAO does not call an LLM, generate stack-specific templates, parse test reports or detect zero tests;
those checks belong to the project's wrappers. `--verify-script` does not automatically declare tests.

### Intake from a one-sentence requirement

The first requirement usually does not exist as a file yet. Writes inside a provider's native output
directory (`_bmad-output/`, `openspec/`, `specs/`, `.specify/`) are allowed **without** an active Work
Item, so create the requirement/PRD there and then run `cao start --requirement <path>`. Product writes
elsewhere require an active Work; direct managed-state edits remain denied. Do not move product
code into native output directories to fake evidence — `cao done` still enforces native artifact ownership.
`cao start` reports `REQUIREMENT_FILE_MISSING` when an explicit requirement path does not exist yet:
create the file first, do not guess another path.

Bootstrap initializes CAO state when needed, verifies that a project SDD is installed, guides requirement selection/start before verification configuration, then reports the active Work Item and next action.
It never creates a Work Item and never reruns requirement intake because an Agent or session changed.
If no SDD is installed, bootstrap stops before coding. If `cao bootstrap` reports `mode: sdd_select`, this is a hard authorization boundary, not a recommendation. You MUST ask the user directly which provider they want and wait for an explicit answer before running any `cao sdd install` or `cao sdd select` command. Never choose a provider yourself based on a recommendation label, popularity, task type, installed global CLI, or personal preference. Do not treat `auto` as a user choice. If the current interface cannot ask the user, stop and report `SDD_SELECTION_REQUIRED`; do not install anything or begin coding. A recommendation is informational only and is never the default or authorization. After the user explicitly chooses, install only that provider and rerun bootstrap.

## Resume before restarting work

A new session or a different agent MUST resume existing work before starting another requirement:

```bash
cao resume --host <claude-code|codex|pi> --session <host-session-id>
```

`resume` reads the persistent Work Item, latest Handoff, execution step, next action, decisions,
blockers, SDD reference, and verification state. It does not re-run intake merely because the
Agent session changed.

## Work lifecycle

```text
START → CHECKPOINT → VERIFY → DONE
           ↑
         RESUME
```

Useful commands:

```bash
cao bootstrap
cao sdd list
# Run install only after the user explicitly chooses a provider:
cao sdd install --provider <user-chosen-provider> --yes
# Spec Kit:
cao sdd install --provider speckit --host <claude-code|codex|pi> --yes
# Or use any supported Spec Kit integration:
cao sdd install --provider speckit --integration <key> --yes
cao sdd select --provider <provider>
cao list
cao start --requirement <path> [--title <human title>] [--sdd-ref <native-ref>]
cao path assess --profile <light|standard|comprehensive> --reason "<short reason>" [--risk-flag <flag>]
cao path show --work <id>
cao sdd spec bind --spec <provider-spec.md> --criteria-section "<heading>"
cao sdd acceptance show
cao sdd workflow select --name "<native workflow>" --entrypoint "<native command or skill>"
cao sdd workflow execute --command "<command actually run>" --exit-code <code>
cao sdd workflow complete --artifact <repo-relative-file>
cao gate satisfy --gate <gate> --evidence "<what was done>"
cao gate show
cao checkpoint --step <step> --next "<next action>" --summary "<progress>"
cao decision --decision "<decision>" --reason "<why>"
cao block --reason "<blocker>" [--suspend]
cao unblock --work <id>
cao handoff --session <id> --host <host>
cao handoff list --work <id>
cao handoff show --id <handoff-id>
cao next
cao verify
cao done
```

## Minimum-safe execution path

After creating a Work Item, assess the minimum safe workflow before making code changes. Keep this
assessment lightweight: read the requirement and only the relevant project context, then choose one
profile. Do not create a scorecard, fact inventory, provenance model, or evidence graph.

```text
light          clear, local, low-risk, reversible; targeted verification
standard       ordinary feature/fix requiring some analysis/design, tests and review
comprehensive  major uncertainty, cross-system or high-risk change; explicit planning and stronger gates
```

`light` uses a minimal native SPEC with explicit criteria (or recognized BMAD oneshot Intent) plus fresh project verification. It does not require workflow registration, plans/no-plan or independent final acceptance. Standard/comprehensive retain the full native workflow and independent acceptance contract.

Record the assessment in the Work Item:

```bash
cao path assess --profile light --reason "Clear, local, reversible change"
```

Supported risk flags (repeat `--risk-flag` as needed): `security`, `data-migration`,
`irreversible-change`, `cross-system`, `major-ambiguity`, `external-contract`, `multi-module`,
`compatibility`. CAO deterministically upgrades `security`, `data-migration`, `irreversible-change`,
`cross-system`, and `major-ambiguity` to at least `comprehensive`; contract, multi-module, and
compatibility risks require at least `standard`. The saved assessment reports both the requested and
effective profile and adds the relevant gates.

`major-ambiguity` asks for user clarification, then a fresh `cao path assess` after incorporating the answer.
Missing/stale assessment and ambiguity yield reminders, not blocked product writes; `cao done` remains
blocked. If the requirement changes, explicitly re-anchor before reassessment. Never hand-edit CAO state.

The selected SDD Provider supplies profile-specific native-workflow guidance. Follow BMAD, OpenSpec,
Spec Kit, or another provider's own commands, artifacts, and mandatory preconditions; CAO does not
invent a duplicate SDD lifecycle. Reassess only when scope, requirement, or risk materially changes.
The profile never bypasses the project's configured Verification scripts or provider-native gates.

For **standard/comprehensive** profiles with an installed SDD provider, selecting a
path is not proof that its native workflow ran. Before implementation, record the actual provider-native
workflow and entrypoint, then execute it using that provider's installed instructions:

```bash
cao sdd workflow select --name "<native workflow name>" --entrypoint "<native workflow command or skill>"
```

After actually executing the workflow, record the run itself:

```bash
cao sdd workflow execute --command "<command actually run>" --exit-code <code> [--output "<tail>"]
```

Then record existing provider-owned outputs and every related plan: repeat `--artifact` and `--plan <path>`;
shared sprint stories require `--plan-item '<path>#<id>'`. Use audited `--no-plan` only when no plan applies;
unresolved scope blocks done and deleting a binding cannot silently shrink the scope.

```bash
cao sdd workflow complete --artifact <repo-relative-file>
```

CAO blocks `cao done` until the workflow is selected, executed, and completed with existing output files.
This workflow registration/plan contract applies to standard/comprehensive across providers. For light, bind the native SPEC and explicit criteria with `cao sdd spec bind --spec <provider-spec.md> --criteria-section "<heading>"`, then verify; do not manufacture a plan or no-plan declaration.

CAO rejects workflow records it can verify as fake:

- a placeholder entrypoint (`echo`, `true`, `printf`, ...) is refused;
- an entrypoint that does not reference the selected provider is refused;
- output files outside the provider's own directory (BMAD `_bmad-output/`, OpenSpec `openspec/`,
  Spec Kit `specs/` and `.specify/`) are refused — **your own product source files are not SDD artifacts**.

Do not fabricate artifact paths, do not record a command you did not run, and do not mark a workflow
completed before running it. If a provider-native workflow genuinely cannot satisfy this contract, do not
bypass the gate silently: pass `--allow-unverified --reason "<why>"`. That exception is recorded in the
Work Item and appended to the lifecycle event log for audit. A nonzero execution needs a separate, new
`--allow-unverified --reason` when completing; an entry exception does not waive failed execution.
Completion binds requirement/provider and output bytes; later changes invalidate that evidence.

After the workflow, satisfy the gates only the Agent can observe:

```bash
cao gate satisfy --gate <gate> --evidence "<what was done>"
cao gate show
```

Verification-flavoured gates and `tests` derive from config-bound `cao verify`; `sdd_acceptance` derives from the independent final run. Never mark these gates manually. Tests additionally requires a successful required script with `gates: ["tests"]`; command names
are not proof. Standard/comprehensive also configure `cao sdd acceptance configure --declaration <repo-json>`, explicitly reanchor reviewed sources, verify, then `cao sdd acceptance run` or `begin`/`submit` for an installed Skill. Use exact declared Work-scoped report outputs; unregistered reports change the snapshot. Full declaration/report contracts are in README. Skill submissions are evidence, not proof that an independent reviewer executed. Follow native reviewer instructions or their human-return fallback.

Human gate evidence binds requirement hash and assessment revision. Done requires every gate.

## State ownership

CAO owns:

```text
Work Item
Execution State
Next Action
Decisions
Blockers
Session
Handoff
Verification freshness
```

The SDD owns:

```text
PRD / Proposal / Spec / Design / Tasks / Story / Ticket / Native workflow
```

Never copy an SDD artifact into a second CAO-owned artifact just to make it "uniform". Store native
SDD paths/references instead.

## Progress discipline

After completing a meaningful slice, update CAO:

```bash
cao checkpoint \
  --step "<current step>" \
  --next "<specific next action>" \
  --summary "<what is complete and what remains>"
```

Record durable architectural or product choices immediately:

```bash
cao decision --decision "<choice>" --reason "<reason>"
```

A blocker that prevents useful continuation should be explicit. Use `--suspend` when the work
should leave the active slot:

```bash
cao block --reason "<why blocked>" --suspend
```

## Verification

Before claiming completion:

```bash
cao verify
cao done
```

Verification is snapshot-bound. A later material code or requirement change makes the previous
verification stale automatically. Do not fake a new verification record merely to close the work.

If `cao done` reports that the requirement changed, do not blindly rerun `cao verify`. Review the
requirement change; when the change is intentional, re-anchor the Work Item with
`cao start --requirement <path> --replace`. The `--replace` action changes the active
execution anchor; it does not mean that the original requirement was superseded. Old requirement-scoped
assessment, gate, verification and workflow evidence must be refreshed. Changed acceptance scripts/capabilities
or `timeout_seconds` require `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`.
Legacy completed history remains readable, not automatically recertified; legacy active work needs explicit
re-anchoring. First integrity upgrade backs up exact bytes in `.orchestrator/backups/integrity-v2/work/<id>.json`.

## Session inspection

Use `cao sessions list` and `cao sessions show --id <id> --host <host>` to inspect runtime Session records. Session state is stored only in `.orchestrator/sessions/`; Work Item progress remains in the Work Item file. A stale activity label does not mean the Session has ended.

Host adapters create Sessions automatically, but only when the adapter is actually installed. `cao bootstrap`
and `cao start` therefore attach the runtime Session as a fallback whenever the host and its native session id
are visible to the process (`CAO_HOST`, `CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, `PI_SESSION_ID`). If those
are absent, CAO records nothing rather than inventing an `unknown` Session. Prefer `cao resume --host <host>
--session <id>` so the Session is bound explicitly.

## Session handoff

Stop/turn_end may save a Handoff but do not end a Session; before_settle only reminds. Actual session_end
(or distinct subagent_stop) ends it. Claude Code uses SessionEnd and Pi 1.1.0 uses session_shutdown;
Codex has no verified native exit hook. Missing native IDs report diagnostics, never a shared default Session.
For an intentional transfer, create an explicit handoff; on actual exit add `--end-session` if needed:

```bash
cao handoff --session <id> --host <host> --reason "transfer to another agent"
```

Every handoff is retained as an immutable history record. Inspect history with `cao handoff list`
and a specific snapshot with `cao handoff show --id <handoff-id>`. The next Agent should consume the
latest handoff through `cao resume` rather than rebuilding context from chat history. Switching hosts
does not create a new Work Item.

## SDD installation gate

A repository may contain requirements without having an SDD installed. That is not sufficient for CAO to start implementation.

The startup sequence is:

```text
bootstrap
   ↓
SDD installed? ── no ──→ ask user to choose OpenSpec, BMad Method, or GitHub Spec Kit
   │                         ↓
  yes                  cao sdd install --provider ... --yes
   ↓                         ↓
select/resume ←──── bootstrap again after installation
```

Use `cao sdd list` to inspect detection and `cao sdd install` to perform the provider's installation/initialization.
The provider installer remains responsible for its own native files and workflow. CAO only records the selected provider.

## SDD compatibility

CAO supports SDDs through references and an intentionally small provider protocol. It does not require
BMAD, OpenSpec, Spec Kit, or another specific framework.

Typical pattern:

```text
Native SDD artifacts
        ↓
SDD Provider / configuration
        ↓
Work Item.sdd.native_ref + artifact references
        ↓
CAO execution state
```

When an SDD has a native CLI, CAO may invoke configured `inspect`, `next`, or `transition` commands,
but CAO does not reimplement the SDD's artifact lifecycle.

## Host adapters

Claude Code, Codex, Pi, and future hosts are transport adapters only. They translate native lifecycle
events to:

```bash
cao host-event --host <host> --repo <repo>
```

The adapter MUST NOT maintain a second copy of Work/Decision/Verification rules.

## Verification scripts

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

Repeat `cao init --test-script` for required test capability, or `--verify-script` for general checks.
`cao verify --script` is a one-off engineering check and cannot satisfy completion evidence. Scripts are
trusted, not sandboxed; each has a finite positive `timeout_seconds` (default 300), process-tree cleanup,
4000-byte output tails and a `timed_out` result (124/error on timeout). Input changes during execution
mark the saved result stale. Snapshots prune ignored directories; internal file links use lexical names,
directory links are not followed, and external file links/read failures cannot certify freshness.

## Work Item titles

The requirement filename is only a fallback title. Use `--title` when the SDD uses fixed filenames such as `SPEC.md`.

## SDD artifact references

CAO can persist native SDD artifact references with `cao artifact add/list/refresh/remove/confirm`. It stores path,
SHA-256, semantic role, native type, and optional native reference. Explicit confirmation binds the review to the
current file hash. It does not copy or edit native SDD content. The Dashboard shows expected/discovered artifacts
as `未生成`, `已生成`, or `已确认`, and permits previewing supported text files.

## Orchestration visualization

For a visual overview of Work Items, execution paths, Sessions, Verification summaries, Handoff history, and provider-native SDD artifacts, run `cao dashboard --open`. The dashboard can preview bounded text/Markdown artifacts and confirm an artifact by recording its current hash in CAO metadata; it never edits the SDD-owned source file. Artifact confirmation is enabled only on a loopback-bound dashboard. Only authoritative Work Item `completed` is done, not
ready/review or a step-name keyword. `cao done` remains responsible for authoritative Verification freshness checks.

## User language policy

The project language preference is `.orchestrator/config.json` → `language.user` (default `zh-CN`). Use it for communication with the user and for every SDD-produced artifact, regardless of provider: BMAD, OpenSpec, Spec Kit, or future providers. Preserve code identifiers, API names, commands, standardized tokens, and file names where required. An explicit user request for a different language takes precedence for the requested scope.

Use `cao language show` to inspect the setting, `cao language set --language <locale>` to update it and apply it to installed SDDs, or `cao language apply` to reapply the current preference. Bootstrap and SDD selection/installation apply the preference automatically.

If the user expresses an ongoing language preference that differs from `language.user`, update the project setting with `cao language set --language <locale>` before producing further SDD artifacts; do not change the preference merely for a one-off explicitly requested artifact.

Preserve user-selected language/framework/test runner and delivery shape through specification, implementation and acceptance. Missing dependencies block the selected checks; do not silently substitute main programs for JUnit 5 or install system dependencies. Configure wrappers before feature work; the initial skeleton may fail. Register a combined tests/general wrapper once. Read installed native Skills completely, including their exact renderer; a render exit 0 only loads instructions. Re-select when the workflow changes.

## Provider-specific light execution

BMAD: use installed `bmad-build`; it selects `oneshot` for suitable small work. Follow its exact renderer and reviewers, not an invented `bmad quick-fix`. A recognized oneshot SPEC intentionally has only metadata, Intent and Implementation Notes. Bind with `cao sdd spec bind --spec <native-spec.md>` without criteria selectors: CAO binds native Intent and the current requirement, preserves the file unchanged and does not claim an explicit acceptance checklist. Normal SPECs or explicitly selected criteria keep the existing structural standard checks. Rebind changed bytes and re-verify; no separate workflow/plan/final-acceptance record is required for light.

OpenSpec: use installed `/opsx:propose` and `/opsx:apply` Agent commands; preserve active schema prerequisites (default apply requires tasks and upstream artifacts). `/opsx:ff` generates those artifacts and requires the expanded commands installed. Reduce process only through an explicitly configured/validated custom schema, not by deleting prerequisites. CLI setup/status is not agent implementation.

Spec Kit: existing-behavior bugs may use `speckit-bug-assess` → `speckit-bug-fix` → `speckit-bug-test` only if the extension is installed. Bind assessment scope and explicit expected behavior; do not auto-install. New features use installed specify/plan/tasks/implement Skills, keeping required `plan.md` and `tasks.md` concise, and converge where supported. `specify implement` is not a terminal workflow command. Native prerequisites still apply even when CAO light has no plan-registration gate.
