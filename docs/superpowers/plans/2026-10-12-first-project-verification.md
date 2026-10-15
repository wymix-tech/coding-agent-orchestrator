# First-project Verification Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. User confirmed the written design and chose current-session inline execution, without subagents.

**Goal:** 在需求开发前暴露缺失的验收配置，由 Agent 识别/询问技术栈并生成项目验证，CAO 提供无副作用的配置 CLI 与前置指导。

**Architecture:** 新增 `scripts/verification_setup.py`，承载兼容脚本读取、纯状态检查、声明构造和无损配置候选。CLI 保持读改写事务，bootstrap/Host 复用指导。核心不调用 LLM、不维护栈模板或解析第三方测试报告。

**Tech Stack:** Python >=3.10 标准库、unittest、Node/Pi 1.1.0 契约与严格 TypeScript。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-10-12-first-project-verification-design.md`，用户已确认。
- 当前隔离 worktree、顺序 TDD、独立提交；不使用子代理，不合并/推送/发布。
- JSON/原子写入/仓库锁不变；新增代码文件 <500 行，不全面重写大 CLI。
- 产品写保持 allow+提醒，内部状态 deny 与 done 硬契约不变；不自动 reanchor。
- 不覆盖已有有效脚本/未知配置字段，不借 init --force 重建配置，不安装 Host/依赖。
- 已有工程读产品清单并复用验证；新工程先采用用户明确栈，未明确才询问。
- 配置就绪不等于验证通过；零测试不得假成功，缺工具/测试须由项目脚本失败或阻塞。
- 历史完成记录、跨平台/真实 Host/浏览器验收保持边界，不用模拟结果签字。

---

## Task 1: Pure setup state and safe configuration command

**Files:** Create `scripts/verification_setup.py`, `tests/test_verification_setup.py`; modify `scripts/cao.py` imports, verification_scripts, parser, standalone whitelist and new cmd_verification_configure.

**Interfaces:**
- `configured_scripts(cfg: dict) -> list[dict]` consumes normalize_scripts; preserves command/commands compatibility.
- `setup_state(cfg: dict) -> dict` returns ready/reason/scripts/tests_bound, invalid declarations as a non-ready diagnostic.
- `declared_scripts(general: list[str], tests: list[str]) -> list[dict]` produces existing script-i/test-i names and explicit required/tests.
- `prepare_config(raw: dict, scripts: list[dict], replace: bool, timestamp: int) -> tuple[dict, bool]` creates a candidate without mutation; identical declarations return unchanged; different nonempty or invalid old declarations require explicit replace.
- `cmd_verification_configure(args) -> int` emits configured/unchanged JSON, inspection, and acceptance_review_required; no Work mutation.

- [x] Write RED CLI tests. RepairCase starts with a valid configured suite, so replacement requires --replace. Create separate uninitialized temp repositories for first configure.

```python
rc, out = self.run_cli('verification', 'configure', '--replace', '--test-script', self.test_command)
self.assertEqual(rc, 0, out)
self.assertTrue(out['verification_setup']['ready'])
before = (self.repo / '.orchestrator/config.json').read_bytes()
rc, out = self.run_cli('verification', 'configure', '--test-script', self.test_command)
self.assertEqual(out['status'], 'unchanged')
self.assertEqual((self.repo / '.orchestrator/config.json').read_bytes(), before)
```

Cover config preservation (language/SDD/timeout/unknown values), missing/blank parameters, changed declarations without replace, invalid old declarations, first configure without creating Work/Host files, and active acceptance anchors remaining byte-identical. Add no-external-call mocks at the CLI handler boundary and a concurrent language/configure CLI test. Invalid arguments must fail before atomic write; persistence fault injection must retain old bytes.

- [x] Run `python3.11 -m unittest discover -s tests -p test_verification_setup.py -v`; expect missing command/feature failures.
- [x] Implement pure helpers with the following complete state/config algorithm (no file I/O in this module):

```python
from copy import deepcopy
from scripts.completion_policy import normalize_scripts


def configured_scripts(cfg):
    verification = cfg.get('verification') or {}
    if not isinstance(verification, dict):
        raise ValueError('VERIFICATION_CONFIG_INVALID')
    raw = verification.get('scripts')
    if raw is None:
        raw = verification.get('commands') or []
        if not raw and verification.get('command'):
            raw = [{'name': 'default', 'command': str(verification['command']), 'required': True}]
    return normalize_scripts(raw)


def setup_state(cfg):
    try:
        scripts = configured_scripts(cfg)
    except ValueError as exc:
        return {'ready': False, 'reason': 'invalid verification declarations',
                'scripts': [], 'tests_bound': False, 'error': str(exc)}
    ready = any(script['required'] for script in scripts)
    return {'ready': ready, 'reason': None if ready else 'no required verification scripts configured',
            'scripts': scripts, 'tests_bound': any(script['required'] and 'tests' in script['gates'] for script in scripts)}


def declared_scripts(general, tests):
    if not general and not tests:
        raise ValueError('VERIFICATION_SCRIPT_REQUIRED')
    items = [{'name': f'script-{i}', 'command': command, 'required': True} for i, command in enumerate(general, 1)]
    items += [{'name': f'test-{i}', 'command': command, 'required': True, 'gates': ['tests']} for i, command in enumerate(tests, 1)]
    return normalize_scripts(items)


def prepare_config(raw, scripts, replace, timestamp):
    if not isinstance(raw, dict):
        raise ValueError('VERIFICATION_CONFIG_INVALID')
    try:
        previous = configured_scripts(raw)
    except ValueError:
        previous = None
    if previous == scripts:
        return raw, False
    if (previous or previous is None) and not replace:
        raise ValueError('VERIFICATION_CONFIG_REPLACE_REQUIRED')
    candidate = deepcopy(raw)
    if not isinstance(candidate.get('verification'), dict):
        candidate['verification'] = {}
    candidate['verification']['scripts'] = scripts
    candidate['verification']['configured_at'] = timestamp
    return candidate, True
```

Keep the existing config(repo) migration behavior before inspection. The CLI reads actual raw JSON for persistence, rejects malformed whole JSON/root types, and never writes the merged defaults back into unrelated config. On replace errors include current and requested state plus the explicit --replace next step.

- [x] Add `verification configure` parser with repeatable --test-script/--verify-script, --replace and --repo; keep existing verify unchanged. Add verification to safe standalone CAO commands. The locked handler uses:

```python
repo = repo_root(args.repo)
path = repo / CONFIG_REL
scripts = declared_scripts(args.verify_script or [], args.test_script or [])
raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
candidate, changed = prepare_config(raw, scripts, args.replace, now())
effective = json.loads(json.dumps(DEFAULT_CONFIG))
deep_merge(effective, candidate)
wid = load_index(repo).get('active_work')
work = _read_work(repo, wid) if wid else None
review = bool(work and work.get('status') == 'active' and
              (work.get('acceptance') or {}).get('config_sha256') != verification_snapshot(effective))
if changed:
    save_json(path, candidate)
out = {'ok': True, 'status': 'configured' if changed else 'unchanged',
       'config': str(path), 'verification_setup': setup_state(effective),
       'acceptance_review_required': review}
if review:
    out['next'] = action('human', f'Review changed acceptance and explicitly run cao reopen --work {wid} --reanchor --reason "Reviewed acceptance changes".')
print(json.dumps(out, ensure_ascii=False, indent=2))
return 0
```

Wrap pre-write parsing/normalization errors into exit=2 JSON; do not catch/relabel storage failures as success. Always report anchor mismatch for an existing active Work, including unchanged declarations following an earlier config change; read the active index on every invocation, not only changed writes.

- [x] Run targeted/full Python + Node/TS/compileall/diff; commit `feat: configure project verification without resetting orchestration state`.

## Task 2: Bootstrap and model-visible preflight

**Files:** Modify `scripts/verification_setup.py` guidance, `scripts/cao.py` bootstrap/render_context/host_event/start advisory; create `tests/test_verification_preflight.py`.

**Interfaces:** `setup_instruction(command: str) -> str` supplies one shared Agent instruction. setup_state from Task 1 determines readiness. New bootstrap mode verification_setup retains applicable context/session/host/language/SDD fields.

- [x] Write real CLI RED tests: empty/optional/invalid setup takes priority after SDD authorization, valid setup retains select/resume; no Work automatically created; active work/decisions/step preserved; scripts unchanged; product write allowed with setup reminder; state write denied even with invalid config. Confirm start remains allowed with an explicit setup warning, then later configure reports reanchor, not silent acceptance.

```python
cfg = cao.config(self.repo)
cfg['verification']['scripts'] = []
cao.save_json(self.repo / '.orchestrator/config.json', cfg)
rc, out = self.run_cli('bootstrap', '--host', 'pi')
self.assertEqual(rc, 0, out)
self.assertEqual(out['mode'], 'verification_setup')
self.assertIn('tech stack', out['next']['instruction'])
self.assertIsNone(out['work_item'])
```

- [x] Run targeted tests RED.
- [x] Add the shared instruction, naming the actual resolved command:

```python
def setup_instruction(command):
    return (
        'CAO verification setup: establish a configured acceptance baseline before feature development. '
        'Run bootstrap first and honor explicit SDD selection/install authorization. '
        'For an existing project, analyze product manifests, source, tests and CI; reuse meaningful verification. '
        'Do not infer the product tech stack from .orchestrator derived state, .agents, .pi, host assets, SDD tools/outputs or caches; current user requirements remain relevant. '
        'For a new project, use the tech stack already specified by the user; otherwise ask the user for language/framework/build/test choices and wait. Do not guess a stack if unable to ask. '
        'Generate project-owned verification scripts or reuse suitable existing scripts. Zero tests, missing tools or missing dependencies must fail/block, not echo/true a pass. For Java/Jupiter use JUnit5-capable Surefire and fail on zero tests. '
        'Configure through ' + command + ' verification configure --test-script "project test wrapper" --verify-script "general check"; do not hand-edit config or use init --force. '
        'Do not overwrite existing scripts or install dependencies automatically. Configuration readiness is not verification success: initial scaffolding/tests may be written before tests pass. '
        'Rerun bootstrap after configuration; if an active Work already has an acceptance anchor, review and explicitly reanchor instead of silently changing it.'
    )
```

- [x] Before the existing work/select branch, compute inspection; use verification_setup for non-ready, otherwise retain old branches. Shared output tail adds inspection/session/host/language/templates. For invalid declarations never call completion calculations; preserve Work continuity through summary plus execution/resume/decisions/blockers rather than certifying completion.

```python
inspection = setup_state(cfg)
if not inspection['ready']:
    next_out = {'ok': True, 'mode': 'verification_setup',
                'work_item': work_summary(work) if work else None,
                'sdd': sdd_status(repo), 'context': verification_setup_context(work),
                'next': action('agent', setup_instruction(cao_invocation()['command']))}
# Change the following existing work branch to elif work; keep its body.
# Add inspection in the existing shared output tail.
next_out['verification_setup'] = inspection
```

`verification_setup_context(work: dict | None) -> dict | None` is the complete narrow snapshot:

```python
def verification_setup_context(work):
    if not work:
        return None
    return {key: work.get(key) for key in ('id', 'title', 'status', 'requirement', 'sdd',
            'execution', 'resume', 'decisions', 'blockers')}
```

- [x] render_context inspects first; non-ready returns setup instruction plus JSON snapshot and native Session id, avoiding malformed config completion errors; ready uses the existing rendering unchanged. Session_start always calls render_context. pre_tool appends setup instruction only to mutating non-intake product calls after protected-state checks. start adds an advisory and inspection without blocking/rewriting its anchor.
- [x] Preserve old fixture intent: tests asserting valid select/resume must explicitly configure their original verification baseline. Tests exercising empty initialization should assert new setup mode, not disable new behavior.
- [x] Run targeted/full Python + Node/TS/compileall/diff; commit `feat: guide verification setup before selecting project work`.

## Task 3: Agent instructions and real-core adapter contract

**Files:** Modify `templates/AGENTS.md`, `templates/CLAUDE.md`, `SKILL.md`, `README.md`, `DESIGN.md`, `tests/test_pi_adapter.mjs`; create `tests/test_verification_guidance.py`; modify `tests/installed_package_smoke.py` and `docs/repair-validation.md`.

**Interfaces:** No SDK/Host transport changes. Reuse Pi additional_context with steer/triggerTurn:false and the real-core fixture added for prior null-verification repair.

- [x] Add RED instruction assertions and a real-core Node case: before_agent_start without scripts receives setup guidance; tool_call to product code remains unblocked; configuring a real suite removes setup mode/guidance; configure leaves Work untouched, with the acceptance anchor unchanged until explicit reanchor. Host events may still update Session/last_agent metadata. Preserve content/details and no second turn.
- [x] Guide the Agent in both templates and Skill before start:

```text
If bootstrap reports verification_setup: analyze existing product manifests/tests/CI and reuse verified commands. For a new product, use the user's explicit stack or ask before choosing. Create real project-owned wrappers that fail/block on zero tests or missing tools; declare required/tests through verification configure. Never hand-edit config, use init --force for this step, auto-install dependencies, or silently reanchor. Rerun bootstrap before starting feature work; configuration readiness is not a passing test result.
```

- [x] Update README/DESIGN with exact new CLI, configuration-vs-pass distinction, explicit replace and reanchor behavior. Do not overwrite previously generated project templates.
- [x] Extend installed smoke import to verification_setup; explicitly test first-project configure and canonical launcher classification from outside checkout. Label all Provider/runtime and Host fixtures simulated. No online SDD installation.
- [x] Run complete Python, Node/TS, compileall/Shell/diff; offline wheel build and temporary installed smoke if cached environment remains available. Record actual outcomes and deferred real-host/platform limitations in repair-validation.
- [x] Inline diff/spec coverage review; commit `docs: establish first-project verification guidance and integration contracts`.

## Execution evidence

- Task 1: `969139c`, 14 targeted / 449 full Python, 12 Node and strict TS passed.
- Task 2: `23bce48`, 12 targeted / 461 full Python, 12 Node and strict TS passed. Nine empty-baseline fixtures were migrated without weakening their original assertions.
- Task 3: 5 guidance/runtime / 466 full Python (128.842s), 13 Node without skips and strict TS passed. Docs RED had 5 missing-instruction failures. A project-owned zero-test wrapper fails verify/done despite configured readiness.
- Offline wheel/editable installations passed 9 local Provider × simulated Host combinations each outside checkout. Smoke uses the actual advertised invocation, not a guessed non-PATH console. Both actual launchers had executable bits; no fresh claim for wheel no-exec fallback.
- Pi integration uses real Python core with simulated callbacks, not a real host launch. Fresh-model pressure tests, real hosts, browser, platform and Java business validation remain deferred; no automatic push/merge/release.
- Logs: `/tmp/cao-setup-{1,2,3}-*.log`; packages: `/tmp/cao-first-project-package.c4jYGk`.

## Verification

```bash
python3.11 -m unittest discover -s tests -p 'test_verification_*.py' -v
CAO_TEST_PYTHON="$(command -v python3.11)" npm run test:pi
npm run check:pi
env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u PI_CODING_AGENT -u PI_SESSION_ID python3.11 -m unittest discover -s tests -v
python3.11 -m compileall -q scripts tests
sh -n hooks/host-event.sh
git diff --check
```

Each commit requires fresh success evidence. Keep failed/partial tasks in_progress. No automatic push; no real-host/browser/platform pass inferred from unit or installation fixtures.
