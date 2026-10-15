# Pi Real Session Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. User chose inline execution without subagents.

**Goal:** 修复异机真实 Pi 会话暴露且在 41b0d3c 复现的三项缺陷。

**Architecture:** 保持已有 Host fail-open 提醒、权威 Work 和显式完成契约。安全读取 nullable verification；只认可实际 CAO launcher/console 的独立治理命令；按 Shell 命令位置识别原生入口，不把输出目录或说明文字作为 Provider 调用证据。

**Tech Stack:** Python >=3.10 标准库、unittest、Pi 1.1.0 TypeScript adapter、Node 契约测试。

**Execution:** 顺序完成红绿回归；最终 435 项 Python / 124.345 秒、12 项 Node、严格 TS、compileall、Shell/diff 均通过。完整证据见 `docs/repair-validation.md`；外部验收仍延期。

## Global Constraints

- 复用 `.worktrees/cao-integrity-repair`，顺序执行，不使用子代理。
- 不处理已确认的全局/项目重复扩展安装，不实现新的技术栈/verify 初始化功能。
- 保持 JSON 存储、现有 CLI、产品写提醒模式及内部状态硬保护。
- 不自动安装依赖、重锚 Work、合并、推送或发布。
- 新增代码文件少于 500 行；不重构已有大文件。
- 未执行的真实 Host、完整浏览器、跨平台出口仍不标通过。

---

## Task 1: Nullable post-tool verification

**Files:** Modify `scripts/cao.py:host_event`; create `tests/test_pi_session_repairs.py`; modify `tests/test_pi_adapter.mjs`.

**Interfaces:** Preserve `host-event` allow JSON and Pi tool-result content. `verification` accepts missing/null/dict; only a passed dict triggers the existing stale resume summary.

- [x] Write real CLI tests using `RepairCase.start()`, followed by Pi-shaped post_tool/file_changed write events. Check exit=0, decision=allow, native Session remains active, and verification stays null. Add missing/pending/passed controls; passed comes from real configured verification.

```python
wid = self.start()
rc, out = self.run_cli('host-event', '--host', 'pi', payload={
    'event': 'post_tool', 'session_id': 'native-pi',
    'toolName': 'write', 'input': {'path': 'src.py', 'content': 'x'},
})
self.assertEqual(rc, 0, out)
self.assertEqual(out['decision'], 'allow')
self.assertIsNone(self.read_work(wid)['verification'])
```

- [x] Add a Node adapter integration test whose temporary `cao` bridge executes the actual Python core rather than fake reply JSON. Prepare a temporary BMAD directory fixture and real Work through CLI; assert tool_result returns undefined, content/details survive, and no diagnostic is emitted with null verification. This remains simulated Pi event delivery, not a real Pi host launch.
- [x] Run `python3.11 -m unittest discover -s tests -p test_pi_session_repairs.py -v` and `CAO_TEST_PYTHON="$(command -v python3.11)" npm run test:pi`; confirm the new CLI assertion and real-core bridge diagnostic fail for the known null bug.
- [x] Apply the one-line implementation:

```python
if (work.get('verification') or {}).get('status') == 'passed':
```

- [x] Re-run targeted tests, all Python tests, Node contracts, strict TS, compileall and diff checks.
- [x] Commit `fix: handle nullable verification in post-tool host events` with tests and this plan.

## Task 2: Real absolute CAO invocations

**Files:** Modify `scripts/cao.py:_readonly_tokens`; extend `tests/test_pi_session_repairs.py`.

**Interfaces:** Keep `readonly_shell(command: str) -> bool` and `tool_is_mutating(raw: dict) -> bool` unchanged. Existing bare/relative CAO and Python-script forms remain compatible. Accept the canonical package-root launcher, its sh fallback, canonical aliases, and the resolved PATH console-script path, followed by an existing safe control subcommand.

- [x] Add failure cases for the actual absolute package launcher and `sh <absolute launcher>`; assert no product reminder on pre_tool. Add canonical symlink and PATH console controls. Unknown `/tmp/other/cao`, unknown subcommands and direct state redirections must remain mutating/denied.

```python
command = shlex.join([str(ROOT / 'cao'), 'path', 'assess', '--profile', 'standard'])
self.assertTrue(cao.readonly_shell(command))
self.assertFalse(cao.tool_is_mutating({'toolName': 'bash', 'input': {'command': command}}))
```

- [x] Run targeted regression and path/host guard tests; confirm absolute invocation classification fails before implementation.
- [x] Preserve existing branches; in the final fallback gather owned launcher and resolved PATH console candidates. Compare resolved absolute target paths (not arbitrary basename `cao`), consume one target token or `sh` plus target, then reuse the existing control-command whitelist.

```python
root = package_root_dir()
launchers = {str((root / 'cao').resolve())} if root else set()
console = shutil.which('cao')
if console:
    launchers.add(str(Path(console).resolve()))
index = 1 if tokens[0] == 'sh' else 0
if len(tokens) <= index or not Path(tokens[index]).is_absolute():
    return False
if str(Path(tokens[index]).resolve()) not in launchers:
    return False
command_tokens = tokens[index + 1:]
```

Use the module's existing command-resolution import pattern; add `shutil` only if needed. Keep the earlier operator rejection and every Shell segment check.

- [x] Re-run targeted/path/host/full Python, Node, TS and diff verification.
- [x] Commit `fix: recognize owned absolute CAO control invocations`.

## Task 3: Compound placeholder native entrypoints

**Files:** Modify `scripts/cao.py:validate_workflow_entrypoint` and focused command-identity helper; extend `tests/test_pi_session_repairs.py` and `docs/repair-validation.md`.

**Interfaces:** Keep `validate_workflow_entrypoint(provider, name, entrypoint) -> {ok, reason}` and `NATIVE_WORKFLOW_ENTRYPOINT_INVALID`. Explicit `allow-unverified` with a reason remains an auditable exception; no path ownership exemption.

- [x] Add a real CLI rejection for the logged `mkdir -p _bmad-output && echo '# Login System Specification' > _bmad-output/spec.md`, asserting workflow state did not advance. Table-test all three providers: provider output paths passed to mkdir/touch/cp/echo are not invocation identities; named CLI/Skill identities remain valid, including setup plus a real native command.
- [x] Cover actual command positions through npx/uv/Python runners, nested static `sh -c`, quoted data and redirection operands. Unknown/dynamic wrappers do not become verified merely by mentioning a Provider. Keep explicit exception/reason and valid Python provider-fixture-script controls.

```python
entry = "mkdir -p _bmad-output && echo '# Login System Specification' > _bmad-output/spec.md"
rc, out = self.run_cli('sdd', 'workflow', 'select', '--name', 'specify', '--entrypoint', entry)
self.assertEqual(rc, 2, out)
self.assertEqual(out['error'], 'NATIVE_WORKFLOW_ENTRYPOINT_INVALID')
```

- [x] Run the table and CLI regression RED; confirm the known compound/data cases are accepted by old code.
- [x] Extract only executable/Skill identities from `_shell_segments`, using bounded recursion for literal shell wrappers. Unwrap supported runners and their recognized options; Python `-m` uses the module identity, `-c` has no statically verified identity. Exclude setup/placeholder utilities. Compare Provider hints against these identities, never all arguments/output paths. Retain declared workflow-name validation but do not let a name substitute for an invocation identity.
- [x] Test explicit audited exception and valid standard completion through real configured verification. Re-run all Python/Node/TS/compileall/Shell/diff checks; review changes against the three requirements.
- [x] Update `docs/repair-validation.md` with actual real-session negative evidence, repairs and fresh counts; preserve all deferred acceptance limitations.
- [x] Commit `fix: reject compound placeholder native workflow entrypoints`.

### Task 3 implementation notes

`workflow_command_identities(entrypoint: str, depth: int = 0) -> list[str]` 位于 `scripts/cao.py`；
消费 `_shell_segments(command: str) -> list[list[str]]`。8 层静态 shell/runner 上限、明确的
`invoke skill <name>` 名位置、Python module/script 位置和 npx/uv runner 参数消费均已正式回归。
校验器只比对 executable/Skill 名称或 Provider 自身 scripts 子目录，不用无关父目录名称认证。
`project-local BMAD workflow/skill` 描述性旧测试夹具已迁移为真实入口，新增描述文字负例。

```python
identities = workflow_command_identities(entrypoint)
markers = [str(marker).strip('/') for marker in (meta.get('project_markers') or [])]
referenced = False
for identity in identities:
    path = identity.replace('\\', '/').lower()
    native_script = any(f'/{marker}/scripts/' in '/' + path for marker in markers)
    if any(hint in path.rsplit('/', 1)[-1] for hint in hints) or native_script:
        referenced = True
        break
```

失败、成功对照、理由例外审计和 standard `verify/done` 完整覆盖于
`tests/test_pi_session_repairs.py:CompoundEntrypointTests`。

## Verification commands

```bash
CAO_TEST_PYTHON="$(command -v python3.11)" npm run test:pi
npm run check:pi
env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u PI_CODING_AGENT -u PI_SESSION_ID python3.11 -m unittest discover -s tests -v
python3.11 -m compileall -q scripts tests
sh -n hooks/host-event.sh
git diff --check
```

Expected: no failures/errors, explicit test counts, no fabricated real-Host/Windows/browser claims. Do not treat the existing zero-test Maven run as successful business verification; that project needs a proper JUnit5 provider and zero-test-fails wrapper separately.
