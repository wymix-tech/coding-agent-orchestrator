# Project Architecture Skills

Architecture Skills are project contracts under `.orchestrator/project/architecture/`.
New explicit `cao init` projects require accepted rules before initialization plans
and new Work. Pre-upgrade projects retain compatibility until explicit adoption;
no existing code or Work is rewritten automatically.

## Select and adopt

1. Save accepted project technology and identity. Inspect actual existing code.
2. `cao project architecture list` lists 14 built-ins plus project custom templates.
   Select a style using observed user choices; language/framework matching is exact
   after punctuation/case normalization. A framework is not an architecture choice.
3. Draft using repo-relative scope roots and confirmed package identity, for example:

```bash
cao project architecture draft --scope root --template spring-boot-modular-ddd \
  --variable source_root=src/main/java/com/example/app \
  --variable test_root=src/test/java/com/example/app
cao project architecture validate --scope root
cao project architecture diff --scope root
cao project architecture apply --scope root --evidence <observed-user-evidence-id> \
  --expected-sha256 <hash-returned-by-diff>
```

Only edit the returned `architecture-inputs/<scope>.md` input. Apply requires actual
host-observed evidence mentioning the chosen rule ID. Ask the user to select the
concrete recommended ID once; preserve this choice across restarts. The compare
and swap hash protects existing content. Apply does not create business files or
execute commands. Missing variables, conflicting stack/scope paths and malformed
rules fail. Review the diff before acceptance; do not change the chosen stack to
make a template fit.

Then use ordinary setup identity/draft/plan/apply/check and project verification
preparation. Read the architecture Skill when adapting the prepared initialization
input; keep Java entry packages/scanning correct. Create only necessary files.
Start the original business Work, whose project binding records architecture hashes.
Map each planned file to the appropriate feature/layer. Do not flatten controllers,
services and persistence objects into the same package.

## Modules and recovery

The root `SKILL.md` applies to all scopes. `<scope>/SKILL.md` adds module rules;
constraints are cumulative, never last-file-wins. Conflicting rules require an
explicit revised project contract. Module roots must stay within accepted paths.

`show` returns applicable paths and hashes. Read those full files before planning,
writes and provider review. Guidance and resume carry these references; pass the
same context to child execution sessions. A bare filename is not proof it was read.

For changes, edit the prepared input, validate, diff and apply again. Existing Work
with different bindings is stale. Review impacts, then use `cao project bind
--refresh --work <id>`. Native plans/reviews and technical evidence must be refreshed;
old progress and historical runs remain. Unrelated scope changes do not invalidate
module bindings. Root changes affect all descendants.

For existing layouts, draft `generic-custom` and describe observed paths and
boundaries rather than restructuring code. List existing debt in native migration
planning; do not declare violating old code conformant. Adoption is explicit and
checks expose current violations. Existing Work without architecture binding can
adopt using project bind --refresh.

## Custom rules

Add `.orchestrator/project/architecture/custom/<id>.md`. Copy a built-in as a
starting point; JSON frontmatter is valid YAML. Ordinary YAML mappings/lists are
also accepted using PyYAML safe loading; executable tags and references are refused.
The CLI discovers custom files automatically. Duplicate IDs fail deterministically.

Keep name/description and metadata.cao with schema_version=1, rule_id,
rule_version, stack, style, scope, source_roots, test_roots and optional checks.
Template variables use `{{scope}}`, `{{source_root}}`, `{{test_root}}`; custom
variables are allowed but all must be resolved before acceptance. Unknown metadata
fields and reference mechanisms are rejected; recursive include graphs are unsupported.
All eleven required sections are demonstrated in built-ins. Include stable ARCH IDs,
a text directory tree, static dependencies, runtime calls and data mapping.

Checks are declarative, never shell commands. Custom checks use the existing
verification configuration and normal command execution policy. Rule loading does
not run a command or install a tool. Markdown body remains the authoritative
human-readable contract; receipts contain only accepted content hash and evidence.

## Verification support

`cao project architecture check --scope <scope>` executes declared Java explicit
import checks and reports semantic checks as unsupported/review-required. `cao
verify` runs the same structural checks before technical execution. Failed checks
block verification. Successful import checks do not certify complete architecture.

Spring Boot templates include actual forbidden-import checks. They strip comments
and strings, inspect package/import declarations, and report violating paths and
dependencies. Fully-qualified references, reflection, inferred service roles and
cross-feature design require provider review. Other templates currently provide
review contracts, without language-specific automated dependency analysis. Frontend
server/client separation and Django model/service boundaries are review obligations.

Run ordinary build/type/startup/tests as well. Provider review must read applicable
Skills and identify rule IDs, files, violations and justified exceptions. CAO
conformance retains native review evidence; it does not invent a second semantic
review authority. Template presence alone never establishes correctness.

## Upgrade and packaging

Built-ins ship in templates/architecture through Python, npm and runtime archives.
Template updates never overwrite project instances. Regenerate a reviewed input
explicitly when adopting a new template. The accepted hash includes all Markdown
bytes, so an unchanged version number cannot conceal a boundary change.
