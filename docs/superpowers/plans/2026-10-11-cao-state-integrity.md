# CAO State Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline; use superpowers:subagent-driven-development only after explicit operator authorization. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 R05/R09/R13，保证并发更新不丢失、最多一个活动任务、可恢复的状态与审计。

**Architecture:** 保留 JSON 权威记录，增加仓库级可重入事务锁、原子替换和状态修订号。index 为可重建投影，关键状态事件使用记录内待发送队列，不引入第二套业务数据库。

**Tech Stack:** Python >=3.10、unittest、fcntl/msvcrt、现有 CAO CLI。

## Global Constraints

- Python >=3.10；Python 核心仅使用标准库，不新增第三方运行时依赖。
- 保留 `.orchestrator/work/`、`sessions/`、`handoffs/` 与 `lifecycle/events.jsonl`；不迁移到第二套状态库。
- 新增代码文件不超过 500 行；不为满足行数限制一次性重写现有大型文件。
- UTF-8 文档；保留项目语言偏好与 Provider 原生文件所有权。
- 缺少有效评估或重大歧义时仅提醒；内部状态保护和 `cao done` 保持硬约束。
- 本计划只在执行方式被用户确认后实施；不自动启动子代理。

---

## 执行基线与文件结构

设计：`docs/superpowers/specs/2026-10-11-cao-integrity-repair-design.md`，文档基线 `fe57f6e`，代码基线 `8ac90f3`。所有源码行号仅用于初始定位；实施时先核对函数名。知识图谱项目为 `Users-wymix-Projects-coding-agent-orchestrator`，2026-10-11T13:38:16Z 代，相关代码路径未记录覆盖缺口；不能把这个信号当作完整性证明。

新增 `scripts/state_store.py` 管理锁、原子写入与备份；新增 `tests/repair_helpers.py` 提供真实验收夹具。核心领域转换继续位于 `scripts/cao.py`。新增测试模块对应下面任务，避免向已经很大的 `test_core.py` 继续集中追加。

统一命令：`PYTHON=python3.11`（本机已验证可用；CI 使用其矩阵 Python）。全量回归：

```bash
env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u PI_CODING_AGENT -u PI_SESSION_ID "$PYTHON" -m unittest discover -s tests -v
"$PYTHON" -m compileall -q scripts tests
git diff --check
```

每个任务定向测试及全量回归通过才提交。下面代码块是待实施的代码，不是在编写计划时创建源文件。

## A1：真实验收夹具与环境隔离

**Files:** Create `tests/repair_helpers.py`, `tests/test_repair_helpers.py`; Modify `tests/test_repairs.py:40-43`, `tests/test_dashboard.py` 的 HTTP 测试。`tests/helpers.py` 在 B1 才变更。

**Interfaces:** 提供 `RepairCase.run_cli(*args, payload=None) -> tuple[int, dict]`、`start(title='a', profile='light') -> str`、`read_work(wid) -> dict`；B/C 使用这些接口。原始 232 项测试先保持通过。

- [ ] **1. 写夹具测试，并验证当前失败。** 新建 `tests/test_repair_helpers.py`：

```python
from repair_helpers import RepairCase
class HelpersTests(RepairCase):
    def test_real_test_suite_runs(self):
        wid = self.start()
        rc, out = self.run_cli('verify', '--work', wid)
        self.assertEqual(rc, 0, out)
        self.assertIn('Ran 1 test', out['verification']['results'][0]['stderr_tail'])
```

运行：`"$PYTHON" -m unittest discover -s tests -p test_repair_helpers.py -v`。预期 ImportError：repair_helpers 尚不存在。

- [ ] **2. 创建完整夹具。** `tests/repair_helpers.py`：

```python
import json, os, pathlib, shlex, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
HOST_KEYS = ('CAO_HOST', 'CLAUDE_CODE', 'CLAUDE_CODE_SESSION_ID',
             'CLAUDE_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_SANDBOX',
             'CODEX_HOME', 'PI_SESSION_ID', 'PI_CODING_AGENT')
class RepairCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name)
        tests = self.repo / 'acceptance-tests'
        tests.mkdir()
        (tests / 'test_acceptance.py').write_text(
            'import unittest\nclass Acceptance(unittest.TestCase):\n'
            ' def test_ok(self): self.assertTrue(True)\n', encoding='utf-8')
        argv = [sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests', '-v']
        self.test_command = (subprocess.list2cmdline(argv) if os.name == 'nt'
                             else shlex.join(argv))
        rc, out = self.run_cli('init', '--host', 'codex', '--sdd', 'bmad',
                               '--verify-script', self.test_command)
        self.assertEqual(rc, 0, out)
        (self.repo / '_bmad').mkdir()
        cfg_path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        cfg['verification']['scripts'][0]['gates'] = ['tests']
        cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    def run_cli(self, *args, payload=None):
        env = dict(os.environ)
        for key in HOST_KEYS:
            env.pop(key, None)
        env['NO_PROXY'] = env['no_proxy'] = 'localhost,127.0.0.1,::1'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/cao.py'),
            *map(str, args), '--repo', str(self.repo)], text=True,
            capture_output=True, env=env, timeout=30,
            input=json.dumps(payload) if payload is not None else None)
        try:
            out = json.loads(result.stdout)
        except json.JSONDecodeError:
            out = {'raw': result.stdout, 'stderr': result.stderr}
        return result.returncode, out
    def start(self, title='a', profile='light'):
        req = self.repo / f'.orchestrator/requirements/{title}.md'
        req.parent.mkdir(exist_ok=True)
        req.write_text(f'# {title}\nOriginal scope\n', encoding='utf-8')
        rc, out = self.run_cli('start', '--requirement', req.relative_to(self.repo))
        self.assertEqual(rc, 0, out)
        wid = out['work_item']['id']
        rc, out = self.run_cli('path', 'assess', '--work', wid,
                             '--profile', profile, '--reason', 'bounded repair')
        self.assertEqual(rc, 0, out)
        return wid
    def read_work(self, wid):
        return json.loads((self.repo / f'.orchestrator/work/{wid}.json')
                          .read_text(encoding='utf-8'))
```

- [ ] **3. 隔离环境。** 在 RepairTests 的 host 缺失用例内 `with mock.patch.dict(os.environ, {key: '' for key in HOST_KEYS})` 调用原断言；导入 `os` 和 `HOST_KEYS`。Dashboard HTTP 用例使用 `build_opener(ProxyHandler({})).open`，不依赖机器代理设置。保留验证 Host/Origin 被拒绝的原断言。
- [ ] **4. 定向及全量验证。** helpers/repairs/dashboard 三个模块通过；输出必须显示夹具真正运行一个测试。现有 `verify_via_config` 的全面迁移放 B1，不能在这里补造新版证据。
- [ ] **5. 提交。** `git add tests/repair_helpers.py tests/test_repair_helpers.py tests/test_repairs.py tests/test_dashboard.py && git commit -m "test: add real acceptance fixtures and isolate host environment"`。

## A2：原子 JSON 与可重入跨进程锁

**Files:** Create `scripts/state_store.py`, `tests/test_state_store.py`; Modify `scripts/cao.py:865-867`。

**Interfaces:** 提供 `repo_transaction(repo: Path, timeout: float = 5.0)` 上下文管理器、`atomic_json(path: Path, value: Any) -> None`。A3/A4/A5 和 B/C 使用它们。

- [ ] **1. 新建失败测试。** `tests/test_state_store.py`：

```python
import json, pathlib, tempfile, unittest
import multiprocessing as mp
from unittest import mock
from scripts.state_store import atomic_json, repo_transaction
class StoreTests(unittest.TestCase):
    def test_replace_failure_keeps_old_json(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / 'state.json'
            atomic_json(p, {'v': 1})
            with mock.patch('scripts.state_store.os.replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError): atomic_json(p, {'v': 2})
            self.assertEqual(json.loads(p.read_text()), {'v': 1})
            self.assertEqual(list(p.parent.glob('*.tmp')), [])
    def test_lock_reenters(self):
        with tempfile.TemporaryDirectory() as td:
            with repo_transaction(pathlib.Path(td), timeout=.2):
                with repo_transaction(pathlib.Path(td), timeout=.2): pass
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_state_store.py -v`，预期新增模块缺失。

- [ ] **2. 实现锁与原子写入。** `scripts/state_store.py` 的完整基础代码：

```python
import errno, json, os, tempfile, threading, time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
_registry_guard = threading.Lock()
_registry = {}
_local = threading.local()
def _lock(fd):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
def _unlock(fd):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
@contextmanager
def repo_transaction(repo: Path, timeout: float = 5.0):
    key = str(repo.resolve())
    with _registry_guard:
        mutex = _registry.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    if not mutex.acquire(timeout=max(0, deadline-time.monotonic())):
        raise RuntimeError('STATE_LOCK_TIMEOUT')
    depths = getattr(_local, 'depths', {})
    _local.depths = depths
    fd = None
    acquired = False
    try:
        if not depths.get(key):
            folder = Path(key) / '.orchestrator'
            folder.mkdir(parents=True, exist_ok=True)
            fd = os.open(folder / 'state.lock', os.O_CREAT | os.O_RDWR, 0o600)
            if os.fstat(fd).st_size == 0: os.write(fd, b'0')
            while True:
                try:
                    _lock(fd)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise RuntimeError('STATE_LOCK_TIMEOUT') from exc
                    time.sleep(.025)
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
            if not depths[key]: depths.pop(key)
    finally:
        if fd is not None:
            try:
                if acquired: _unlock(fd)
            finally: os.close(fd)
        mutex.release()
def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2,
                                    sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            dfd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
    finally:
        if os.path.exists(name): os.unlink(name)
```

将 `save_json` 函数体替换为 `atomic_json(path, value)`，导入上述函数。锁文件不删除；操作系统释放持有者死亡后的锁，不根据文件年龄抢锁。

- [ ] **3. 补充可运行的进程测试。** 在 `tests/test_state_store.py` 增加顶层 worker：

```python
def hold_lock(repo, ready, release):
    with repo_transaction(pathlib.Path(repo)):
        ready.set()
        release.wait(10)
```

再加入完整进程测试，使用 Event 而非 sleep 假定时序：

```python
class ProcessLockTests(unittest.TestCase):
    def test_timeout_and_owner_death(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = mp.get_context('spawn')
            ready, release = ctx.Event(), ctx.Event()
            child = ctx.Process(target=hold_lock, args=(td, ready, release))
            child.start()
            try:
                self.assertTrue(ready.wait(5))
                with self.assertRaisesRegex(RuntimeError, 'STATE_LOCK_TIMEOUT'):
                    with repo_transaction(pathlib.Path(td), timeout=.1): pass
            finally:
                child.terminate(); child.join(5)
                if child.is_alive(): child.kill(); child.join(5)
            self.assertFalse(child.is_alive())
            with repo_transaction(pathlib.Path(td), timeout=1): pass
```
- [ ] **4. 验证。** state_store 定向、A1、全量检查通过；同步失败属于持久化错误，不返回假成功。
- [ ] **5. 提交。** `git add scripts/state_store.py scripts/cao.py tests/test_state_store.py && git commit -m "fix: make JSON writes atomic and serialize repository transactions"`。

## A3：完整读改写、陈旧写入防护与投影恢复

**Files:** Modify `scripts/cao.py:1647-1663`、所有读取后修改入口、`scripts/dashboard.py:375-388,446-616`; Create `tests/test_state_transactions.py`。

**Interfaces:** `save_work(repo, work)` 增加 `state_revision`；提供 `mutate_work(repo: Path, wid: str, update: Callable[[dict], None]) -> dict`、`recover_index(repo: Path) -> dict`。

- [ ] **1. 写陈旧写入与真实并发测试。** 新模块：

```python
from concurrent.futures import ThreadPoolExecutor
from repair_helpers import RepairCase
from scripts import cao
class TransactionsTests(RepairCase):
    def test_stale_save_is_rejected(self):
        wid = self.start()
        a, b = cao.load_work(self.repo, wid), cao.load_work(self.repo, wid)
        a['resume']['summary'] = 'first'; cao.save_work(self.repo, a)
        b['resume']['summary'] = 'second'
        with self.assertRaisesRegex(RuntimeError, 'STALE_WORK_REVISION'):
            cao.save_work(self.repo, b)
    def test_concurrent_cli_decisions_are_all_kept(self):
        wid = self.start()
        def decide(n): return self.run_cli('decision', '--work', wid, '--decision', f'd-{n}')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(decide, range(12)))
        self.assertTrue(all(rc == 0 for rc, out in results), results)
        self.assertEqual(len(self.read_work(wid)['decisions']), 12)
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_state_transactions.py -v`；旧代码陈旧写入不会拒绝，并发可丢记录。

- [ ] **2. 实现保存与领域更新入口。** 添加 typing 导入 Callable；替换 save_work：

```python
def save_work(repo, work):
    with repo_transaction(repo):
        current = load_work(repo, work['id'])
        revision = int((current or {}).get('state_revision', 0))
        if int(work.get('state_revision', 0)) != revision:
            raise RuntimeError('STALE_WORK_REVISION')
        work['state_revision'] = revision + 1
        work['updated_at'] = now()
        save_json(work_path(repo, work['id']), work)
        sync_index_for_work(repo, work)
def mutate_work(repo, wid, update):
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work is None: raise RuntimeError('WORK_ITEM_NOT_FOUND')
        update(work)
        save_work(repo, work)
        return work
def recover_index(repo):
    with repo_transaction(repo):
        works = [load_json(p, None) for p in sorted((repo / WORK_DIR).glob('*.json'))]
        works = [w for w in works if isinstance(w, dict)]
        active = [w['id'] for w in works if w.get('status') == 'active']
        index = {'version': 1, 'active_work': active[0] if len(active) == 1 else None,
                 'work_items': [work_summary(w) for w in works]}
        save_index(repo, index)
        if len(active) > 1: raise RuntimeError('MULTIPLE_ACTIVE_WORK_ITEMS: ' + ','.join(active))
        return index
```

- [ ] **3. 将锁放在读取前，而非只在 save 内。** 短 CLI 操作采用下面完整包装器，注册到 parser 的 fn；原始函数直接单元调用也应包裹。外部安装、SDD inspect/transition 及验证 subprocess 不包在整个命令锁中：它们分为锁内准备、锁外执行、锁内重新读取/校验/保存。不得将下面包装器直接套在这些长命令上。

```python
from functools import wraps
def locked_command(fn):
    @wraps(fn)
    def call(args):
        with repo_transaction(repo_root(args.repo)):
            return fn(args)
    return call
```

具体入口清单：checkpoint、decision、block、unblock、path assess、gate satisfy、artifact mutations、handoff、reopen、host-event、requirement allocation、language/config 更新；status/list/bootstrap/resume/next 与 Dashboard 同步的读改写也包裹。`register_session`、`update_session_event`、`create_handoff` 和 `reconcile_native_sdd_state` 在事务内重新载入 Work Item，不以调用者的旧字典覆盖；调用者随后重新 load。在 `confirm_artifact` 和 Dashboard 原生同步边界使用 `with repo_transaction(repo)`。

`cmd_start` 将旧任务挂起、选定需求、创建/更新目标及会话关联放同一锁内；原生 inspect 放锁外。`cmd_done` 锁内判定，锁外 transition，再锁内重读与判定后关闭。transition 造成的新鲜度变化应报告，不用旧判定强行完成。B2 提供 verify 的完整提交协议。

- [ ] **4. 故障恢复测试。** 在 TransactionsTests 写坏/删除 index，调用 status/list，断言从权威记录恢复；多个 active 报冲突，不任选一个。模拟 save_index 失败后重读恢复，模拟 Host 和 Dashboard 确认与 decision 并发，决策及确认均保留。定向与全量回归通过。
- [ ] **5. 提交。** `git add scripts/cao.py scripts/dashboard.py tests/test_state_transactions.py && git commit -m "fix: protect complete state mutations and recover index projections"`。

## A4：唯一活动任务与合法恢复通道

**Files:** Modify `scripts/cao.py:3531-3631,3909-3922,3999-4030,4235-4304,1647-1657`; Create `tests/test_active_work_integrity.py`。

**Interfaces:** 提供 `other_active_work(repo: Path, wid: str) -> list[str]`。所有激活入口调用；普通保存只能保持/清除指针，不抢占其他活动项。

- [ ] **1. 写 unblock 回归。** 新模块：

```python
from repair_helpers import RepairCase
class ActiveTests(RepairCase):
    def test_unblock_does_not_steal_active_work(self):
        a = self.start('a')
        self.run_cli('block', '--work', a, '--reason', 'waiting', '--suspend')
        b = self.start('b')
        rc, out = self.run_cli('unblock', '--work', a)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(a)['status'], 'suspended')
        self.assertEqual(self.read_work(b)['status'], 'active')
        self.assertEqual(self.read_work(a)['blockers'], [])
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_active_work_integrity.py -v`；旧代码 A 被错误激活。

- [ ] **2. 实现统一检查。** 新增函数与替换 unblock 的状态条件：

```python
def other_active_work(repo, wid):
    return [w['id'] for p in sorted((repo / WORK_DIR).glob('*.json'))
            if (w := load_json(p, None)) and w.get('status') == 'active' and w['id'] != wid]
```

```python
if work.get('status') == 'suspended' and not work['blockers']:
    if not other_active_work(repo, work['id']): work['status'] = 'active'
```

在 start/resume/reactivate/reopen 的 active 赋值前，如果 other_active_work 非空且未显式 replace，返回 `WORK_ITEM_ALREADY_ACTIVE` 和候选 id。replace 顺序为旧任务 suspended→Handoff→新任务 active。在 `sync_index_for_work` 中不得默默覆盖其他 active；清空/设置活动项后核对唯一性。
- [ ] **3. 覆盖所有入口和中断。** 增加 resume/reopen 冲突、checkpoint 不抢占、replace 两步之间异常后零 active、历史冲突使用 `block --work ID --suspend` 修复的用例。冲突模式下允许这个显式修复操作，不能因恢复检查而把修复入口一起锁死。
- [ ] **4. 验证。** A3/A4、sessions、handoffs、全量测试通过。
- [ ] **5. 提交。** `git add scripts/cao.py tests/test_active_work_integrity.py && git commit -m "fix: enforce a single active work item across activation paths"`。

## A5：状态事件恢复、幂等与首次升级备份

**Files:** Modify `scripts/state_store.py`, `scripts/cao.py:1776-1813,4235-4304,4307-4329,2655-2694`; Create `tests/test_audit_recovery.py`。

**Interfaces:** `queue_event(work: dict, event: dict) -> None`、`drain_events(repo: Path, work: dict) -> None` 在 cao；`backup_once(repo: Path, path: Path) -> None` 在 state_store。普通 revision 保存不等同于需求版本升级。

- [ ] **1. 新建审计失败用例。**

```python
from repair_helpers import RepairCase
class AuditTests(RepairCase):
    def test_two_reanchors_produce_two_events(self):
        wid = self.start()
        for reason in ('first', 'second'):
            rc, out = self.run_cli('reopen', '--work', wid, '--reanchor', '--reason', reason)
            self.assertEqual(rc, 0, out)
        rc, out = self.run_cli('lifecycle', 'events', '--work', wid)
        events = [e for e in out['events'] if e['event'] == 'task.reopened']
        self.assertEqual(len(events), 2)
        self.assertEqual(len({e['idempotency_key'] for e in events}), 2)
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_audit_recovery.py -v`，旧代码只记录一次。

- [ ] **2. 实现事件队列。** 在关键转换保存前 queue，保存后 drain：

```python
def queue_event(work, event):
    key = uuid.uuid4().hex
    work.setdefault('pending_events', []).append({**event,
        'event_id': 'evt-' + key, 'idempotency_key': key})
def drain_events(repo, work):
    with repo_transaction(repo):
        fresh = load_work(repo, work['id'])
        for event in fresh.get('pending_events', []): _append_lifecycle_event(repo, event)
        if fresh.get('pending_events'):
            fresh['pending_events'] = []
            save_work(repo, fresh)
```

_append_lifecycle_event 使用 A2 同一仓库锁；仅非空且相等的 idempotency_key 去重，去掉旧的年龄抢锁实现。所有 keyless 新事件赋唯一键；原生状态现有确定性键保留。在读取/后续修改前 drain 未完成事件，先写权威记录再写日志；日志写成功、清队列失败时重试不会重复。

- [ ] **3. 首次升级保留原记录。** state_store 增加：

```python
def backup_once(repo: Path, path: Path) -> None:
    import shutil
    with repo_transaction(repo):
        target = repo / '.orchestrator/backups/integrity-v2' / path.relative_to(repo / '.orchestrator')
        if not path.exists() or target.exists(): return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix='.tmp', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as output, path.open('rb') as source:
                shutil.copyfileobj(source, output)
                output.flush(); os.fsync(output.fileno())
            os.replace(name, target)
            if os.name != 'nt':
                dfd = os.open(target.parent, os.O_RDONLY)
                try: os.fsync(dfd)
                finally: os.close(dfd)
        finally:
            if os.path.exists(name): os.unlink(name)
```

在持锁的首次新增 state_revision/协议版本写入前调用。复制失败中断升级，不改旧记录；临时备份不能代表升级已备份。异常清理临时文件，进程中断遗留的 tmp 不当作最终备份，重试完成原子替换后才开始迁移。增加备份失败、重复升级不覆盖备份、旧 completed 不自动重开测试。B1 才处理证据协议，不在本任务补造证据。
- [ ] **4. 故障注入验证。** JSONL fsync 后、清队列前异常重试仍只有一个事件；进程重启补写队列；两个 Work Item 重开互不去重；并发 Handoff 序号唯一、相同快照去重。定向及全量通过。
- [ ] **5. 提交。** `git add scripts/state_store.py scripts/cao.py tests/test_audit_recovery.py && git commit -m "fix: recover lifecycle events and preserve pre-upgrade state"`。

## A 出口检查

- [ ] R05/R09/R13 对应测试全部通过；并发用例重复 10 轮，无错误成功或丢记录。
- [ ] `state_store.py` 不超过 500 行；安装包可导入新增 scripts 模块。
- [ ] Linux/macOS/Windows 的锁、replace 和进程死亡专项 CI 在 C6 落地；本机单平台结果不得冒充跨平台验证。
- [ ] 记录最后通过的命令、输出和提交，再进入 B；提交记录只证明本计划范围，不宣称全部修复完成。
