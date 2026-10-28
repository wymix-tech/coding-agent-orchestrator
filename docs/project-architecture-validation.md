# Project Architecture Skills validation — 2026-10-28

Phases P0–P5 are complete on feat/project-architecture-skills. Every phase was
saved to GitHub; local/remote implementation blob hashes matched.

## Executed validation

| Check | Result |
| --- | --- |
| compileall and git diff --check | passed |
| Full unittest discovery by file, six isolated subprocesses | 96 modules; 1,168 tests; zero failures/errors; five skipped |
| Architecture behavioral suite | 16 tests passed |
| Python wheel build | passed; 14 templates and schema included |
| Runtime tar.gz/zip build | passed |
| Outside-checkout installed wheel smoke | 24 scenarios completed |
| Independent Skill usage exercise | complete create-order file map, dependencies, conversions and tests |

The full runner executes each test_*.py discovery pattern once. Each module has
its own subprocess; fixtures use temporary repositories. Architecture cases cover
real CLI adoption and first Work binding, content changes, root inheritance,
module isolation, YAML, duplicate IDs, path safety, compare-and-swap, edited-input
revalidation, forbidden Java imports, domain persistence imports and flat classes.

Installed smoke uses a separate virtual environment and cwd outside the checkout.
The harness imports fixture helpers from the checkout; tested CLI subprocesses
execute the installed wheel. Coverage includes empty initialization, architecture
adoption, project preparation, native completion and legacy light/standard flows.
Simulated hosts: Claude Code, Codex, Pi. Local provider fixtures: BMAD, OpenSpec,
Spec Kit. These are not claims of live host/provider runtime integration.

Independent forward use found an ambiguous application-result directory and
repeated check IDs. Both were corrected and the final checks passed.

## Commands

```bash
python -m compileall -q scripts tests
python -m unittest discover -s tests -p '<each test_*.py>' -q
python -m unittest tests.test_project_architecture -q
python -m pip wheel --no-deps --no-build-isolation -w /tmp/architecture-wheel .
python tools/release/build_release.py --output /tmp/architecture-release --version architecture-validation
python tests/installed_package_smoke.py --cao <installed-cao> --python <installed-python>
```

## Coverage boundaries

Five optional tests skipped: four actual toolchain cases and official BMAD stable
checkout cross-source verification. This run does not establish actual Maven,
Gradle, Go or TypeScript compilation. No global tools were installed.

Automatic architecture analysis supports Java explicit imports and direct base
package placement. Fully qualified references, reflection and semantic roles
require provider review. Non-Java templates provide review contracts; unsupported
semantic checks are never labeled passed. Framework-specific validation stays in
the configured project verification toolchain.

Rules are bound into project readiness, setup plans, Work, shared resume context,
technical evidence, native reviews and native completion/policy evidence. Existing
project configurations retain compatibility until adoption. Changed rules preserve
Work progress while requiring acceptance, refreshed bindings and affected evidence.

See references/project-architecture.md for commands/extensions/migration and
examples/project-architecture-spring-boot.md for the first Work walkthrough.
