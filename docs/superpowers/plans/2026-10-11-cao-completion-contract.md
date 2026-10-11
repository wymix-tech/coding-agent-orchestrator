# CAO Completion Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline; use superpowers:subagent-driven-development only after explicit operator authorization. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 R01/R02/R03/R04/R06/R07/R12，确保完成证据属于当前需求、验收标准和原生计划。

**Architecture:** 在 A 的事务边界上引入 completion_policy 与 path_guard，统一证据、配置及门禁判定。保留 CLI 和 Provider 自主执行，仅校验显式声明与执行记录，不构造语义事实图谱。

**Tech Stack:** Python >=3.10、标准库、unittest、现有 JSON 协议。

## Global Constraints

- Python >=3.10；Python 核心仅使用标准库，不新增第三方运行时依赖。
- UTF-8 文档；保留项目语言偏好与 Provider 原生文件所有权。
- 新增代码文件不超过 500 行；不为满足行数限制一次性重写现有大型文件。
- 保留 `cao`、JSON 布局；产品代码写入仅提醒，内部状态与 `cao done` 保持硬约束。
- A1—A5 验证通过后实施 B；禁止在编写计划时修改业务代码。

---

## 基线与共用接口

设计：`docs/superpowers/specs/2026-10-11-cao-integrity-repair-design.md`。A 提供 `repo_transaction(repo, timeout=5.0)`、`mutate_work(repo, wid, update)`、`queue_event(work,event)`、`RepairCase`。规范化证据版本为 2，状态 revision 与需求哈希分离。

新增 `scripts/completion_policy.py` 放不依赖 CLI/HTTP 的判定（产物读取显式接收 repo）；`scripts/path_guard.py` 放真实路径及目标解析。cao 保留 Provider 文本解析。每任务运行其定向测试、`"$PYTHON" -m unittest discover -s tests -v`、compileall 和 diff 检查；用 A 的无代理/Host 污染运行方式。`PYTHON=python3.11` 是本机选择，不是项目最低版本变化。

## B1：需求版本、验收锚点和旧记录兼容

**Files:** Create `scripts/completion_policy.py`, `tests/test_evidence_binding.py`; Modify `scripts/cao.py:1518-1521,3531-3631,3664-3722,3742-3800,4235-4304`; Modify `protocol/work-item.schema.json`, `tests/helpers.py`, `tests/test_acceptance_integrity.py`, `tests/test_core.py`, `tests/test_workflow_contract.py`；B2 接续迁移计划测试。

**Interfaces:** `acceptance_hash(scripts: list[dict]) -> str`、`anchor_acceptance(work: dict, scripts: list[dict]) -> None`、`reset_requirement(work: dict, requirement: dict) -> bool`、`evidence_matches(work: dict, evidence: dict) -> bool`。normalize_scripts 在 B2 定义，本任务先使用现有 verification_scripts 结果。

- [ ] **1. 编写失败回归。** `tests/test_evidence_binding.py`：

```python
from repair_helpers import RepairCase
class EvidenceTests(RepairCase):
    def test_changed_requirement_drops_review(self):
        wid = self.start(profile='standard')
        self.run_cli('gate', 'satisfy', '--work', wid, '--gate', 'scope_review', '--evidence', 'old')
        req = self.repo / self.read_work(wid)['requirement']['path']
        req.write_text('# New scope\n', encoding='utf-8')
        rc, out = self.run_cli('start', '--requirement', req.relative_to(self.repo), '--replace')
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        self.assertEqual(work.get('gate_status'), {})
        self.assertIsNone(work.get('path_assessment'))
        self.assertFalse((work.get('sdd', {}).get('workflow') or {}).get('execution'))
    def test_standard_anchor_exists_before_verify(self):
        work = self.read_work(self.start())
        self.assertEqual(work['acceptance']['version'], 2)
        self.assertEqual(len(work['acceptance']['config_sha256']), 64)
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_evidence_binding.py -v`；旧代码保留 review 且无 acceptance。

- [ ] **2. 实现纯证据函数。** `scripts/completion_policy.py`：

```python
import hashlib, json, time
SNAPSHOT_KEYS = ('requirement_sha256', 'code_snapshot', 'rules_snapshot',
                 'verification_config_sha256', 'assessment_revision')
def acceptance_hash(scripts):
    encoded = json.dumps(scripts, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
def anchor_acceptance(work, scripts):
    work['acceptance'] = {'version': 2, 'config_sha256': acceptance_hash(scripts),
                          'anchored_at': int(time.time())}
def reset_requirement(work, requirement):
    old = work.get('requirement') or {}
    changed = any(old.get(k) != requirement.get(k) for k in ('path', 'sha256'))
    work['requirement'] = requirement
    if changed:
        work['path_assessment'] = None
        work['gate_status'] = {}
        work['verification'] = None
        sdd = work.setdefault('sdd', {})
        previous = sdd.get('workflow') or {}
        sdd['workflow'] = {k: previous[k] for k in ('name', 'entrypoint') if k in previous}
        if sdd['workflow']: sdd['workflow']['status'] = 'selected'
        sdd.pop('plan_scope', None)
        sdd.pop('native_state', None)
        hint = sdd.pop('native_ref', None)
        if hint: work.setdefault('resume', {})['native_ref_hint'] = hint
        if sdd.get('artifacts'):
            work.setdefault('resume', {})['previous_sdd_artifacts'] = sdd['artifacts']
            sdd['artifacts'] = []
    return changed
def evidence_matches(work, evidence):
    assessment = work.get('path_assessment') or {}
    return bool(evidence and evidence.get('requirement_sha256') ==
        (work.get('requirement') or {}).get('sha256') and
        evidence.get('assessment_revision') == assessment.get('revision') and
        assessment.get('revision') is not None)
def verification_fresh(work, current):
    v = work.get('verification') or {}
    anchor = work.get('acceptance') or {}
    return bool(v.get('version') == 2 and v.get('source') == 'config' and
        v.get('status') == 'passed' and anchor.get('version') == 2 and
        anchor.get('config_sha256') == current['verification_config_sha256'] and
        all(v.get(k) == current.get(k) for k in SNAPSHOT_KEYS))
```

- [ ] **3. 接入明确锚定入口。** 新 Work Item 创建时 anchor；existing 先比较 reset_requirement 再赋值，实际需求变化才清证据并重新 anchor。不变的旧记录缺 acceptance 时提示 `cao reopen --reanchor`，不自动造一个当前锚点。Provider 改变清 workflow/plan 与评估。`path assess` 为每次重新评估设置递增 revision；`gate satisfy` 保存：

```python
record = {'satisfied': True, 'evidence': evidence, 'at': now(), 'by': args.satisfied_by,
          'requirement_sha256': work['requirement']['sha256'],
          'assessment_revision': work['path_assessment']['revision']}
work.setdefault('gate_status', {})[name] = record
```

reopen/reanchor 使用当前规范化脚本重新 anchor，清 gates/verification；需求与 Provider 不变时保留 workflow。work schema 新增 acceptance.version/config_sha256、state_revision、assessment.revision、gate_status 绑定字段；保留 additionalProperties 与旧记录可读，不自动重新认证 completed。

- [ ] **4. 迁移测试与夹具。** 首次证据升级调用 A5 backup_once；测试旧 active 的明确指引、旧 completed 状态不变且 legacy_evidence 显示、备份不覆盖。测试只改 configured_at 不能绕过标准哈希，标准同秒修改仍拒绝。`tests/helpers.py` 去掉时间戳倒填和 --help；在任务 start 前写真实验收套件及 gates。必须晚配置的特定用例明确 reanchor，不伪造早配置。全量通过才提交。
- [ ] **5. 提交。** `git add scripts/completion_policy.py scripts/cao.py protocol/work-item.schema.json tests/helpers.py tests/test_evidence_binding.py tests/test_acceptance_integrity.py tests/test_core.py tests/test_workflow_contract.py && git commit -m "fix: bind completion evidence to requirement and acceptance versions"`。

## B2：显式 tests 能力、统一新鲜度与验证提交

**Files:** Modify `scripts/completion_policy.py`, `scripts/cao.py:902-948,338-356,2417-2522,4071-4175,4735-4849`; Create `tests/test_verification_bindings.py`; Modify `tests/test_workflow_contract.py`, `tests/test_plan_completion_gate.py`, `tests/repair_helpers.py`, `examples/config.json`。

**Interfaces:** `normalize_scripts(raw: list) -> list[dict]`、`tests_satisfied(verification: dict) -> bool` 在 policy；`verification_inputs(repo,work,cfg) -> dict` 在 cao。输入/结果均保留 gates；verification_snapshot 使用相同规范化结果。

- [ ] **1. 新建失败测试。**

```python
import unittest
from scripts.completion_policy import normalize_scripts, tests_satisfied
class VerificationBindingsTests(unittest.TestCase):
    def test_wrapper_keeps_declared_tests_gate(self):
        scripts = normalize_scripts([{'name':'unit', 'command':'./scripts/verify-unit-tests.sh',
                                     'required':True, 'gates':['tests']}])
        self.assertEqual(scripts[0]['gates'], ['tests'])
    def test_optional_failed_tests_never_satisfy_gate(self):
        self.assertFalse(tests_satisfied({'results':[
            {'required':True,'gates':[],'status':'passed'},
            {'required':False,'gates':['tests'],'status':'failed'}]}))
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_verification_bindings.py -v`，预期新增接口缺失。

- [ ] **2. 添加规范化与测试门禁。**

```python
def normalize_scripts(raw):
    if not isinstance(raw, list): raise ValueError('verification.scripts must be a list')
    result, names = [], set()
    for i, item in enumerate(raw, 1):
        if isinstance(item, str): item = {'command':item}
        if not isinstance(item, dict): raise ValueError('VERIFICATION_SCRIPT_INVALID')
        command = str(item.get('command') or '').strip()
        name = str(item.get('name') or f'script-{i}').strip()
        required = item.get('required', True)
        gates = item.get('gates', [])
        if not command or not name or name in names or not isinstance(required, bool):
            raise ValueError('VERIFICATION_SCRIPT_INVALID')
        if not isinstance(gates, list) or any(g not in {'tests'} for g in gates):
            raise ValueError('VERIFICATION_GATE_INVALID')
        names.add(name)
        result.append({'name':name,'command':command,'required':required,
                       'gates':sorted(set(gates))})
    return result
def tests_satisfied(verification):
    results = [r for r in verification.get('results', [])
               if r.get('required') and 'tests' in r.get('gates', [])]
    return bool(results) and all(r.get('status') == 'passed' for r in results)
```

v2 仅新增 tests 专项声明；普通 verification/targeted_verification 仍来自必需项整体结果。保留 legacy command 配置迁移，随后统一 normalize_scripts。删除关键词权威判断及其误导注释。

新增 `init --test-script`（append），和 verify-script 合并为命名唯一的项；test-script 写 required true/gates tests。示例配置显式声明 tests。添加 `tests` 到 AUTO_DERIVED_GATES，禁止手工 satisfy tests；其他人工门禁使用 B1 evidence_matches。替换 gate_state：

```python
def gate_state(work, fresh):
    required = (work.get('path_assessment') or {}).get('required_gates', [])
    satisfied, pending = [], []
    for gate in required:
        if gate == 'tests':
            ok = fresh and tests_satisfied(work.get('verification') or {})
        elif gate_is_auto_derived(gate): ok = fresh
        else:
            evidence = (work.get('gate_status') or {}).get(gate) or {}
            ok = bool(evidence.get('satisfied') and evidence_matches(work, evidence))
        (satisfied if ok else pending).append(gate)
    return {'gates':list(required),'satisfied':satisfied,'pending':pending}
def verification_inputs(repo, work, cfg):
    return {'requirement_sha256':sha256_file(repo / work['requirement']['path']),
            'code_snapshot':code_snapshot(repo, {work['requirement']['path']}),
            'rules_snapshot':rules_snapshot(repo, cfg),
            'verification_config_sha256':verification_snapshot(cfg),
            'assessment_revision':(work.get('path_assessment') or {}).get('revision')}
```

- [ ] **3. 将 verify 分成三个阶段。** 锁内选择任务、规范化配置、检查 acceptance hash 和必需 tests 绑定，记录 inputs；锁外执行原脚本循环，将每条 gates 复制到结果。锁内提交完整函数：

```python
def commit_verification(repo, wid, inputs, scripts, results, source, started):
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if not work: raise RuntimeError('WORK_ITEM_NOT_FOUND')
        current = verification_inputs(repo, work, config(repo))
        changed = current != inputs
        status = overall_verification_status(results)
        verification = {'version':2, 'source':source, 'at':started, **inputs,
                        'scripts':scripts, 'results':results,
                        'status':'stale' if changed else status, 'run_status':status}
        work['verification'] = verification
        if not changed:
            work['execution']['step'] = 'verification'
            work['execution']['next_action'] = action('agent',
                'Run `cao done`.' if status == 'passed' else 'Fix verification and rerun `cao verify`.')
        save_work(repo, work)
        return verification
```

准备阶段 config hash 缺失/不符返回 `VERIFICATION_STANDARD_CHANGED` 和 reanchor 指引；需 tests 但没有必需绑定返回 `VERIFICATION_GATE_UNBOUND`。cli override 可执行，但 source cli 不获完成资格。提交不拿运行前整个 work 覆盖新 work；stale 退出码 2。`completion_status`、gate show、next、Dashboard 使用 verification_inputs + verification_fresh，不能只看 status passed。
- [ ] **4. 端到端验证。** RepairCase 配置真正运行一个成功/失败 unittest 的包装脚本；配置在 start 前存在，或通过显式 reanchor。验证必需失败、可选失败、无绑定、多个绑定之一失败、CLI override、命令中出现 help/test 不自动获得 tests 能力、验证期间 decision 保留、期间需求/配置/评估/代码修改变 stale。同步旧 tests 门禁用例：改为自动派生且不能手动赋值，不能削弱完成约束。
- [ ] **5. 提交。** `git add scripts/completion_policy.py scripts/cao.py examples/config.json tests/repair_helpers.py tests/test_verification_bindings.py tests/test_workflow_contract.py tests/test_plan_completion_gate.py && git commit -m "fix: bind tests gates to required fresh verification results"`。

## B3：失败工作流与原生产物证据

**Files:** Modify `scripts/completion_policy.py`, `scripts/cao.py:3193-3307,2417-2522`; Create `tests/test_workflow_execution_integrity.py`。

**Interfaces:** `workflow_ready(work: dict, repo: Path) -> tuple[bool,str|None]` 在 policy；workflow 新增 requirement_sha256/provider/artifact_hashes，failure_exception 与旧的入口例外分开。

- [ ] **1. 写非零退出回归。**

```python
from repair_helpers import RepairCase
class WorkflowExecutionTests(RepairCase):
    def test_failed_execution_cannot_complete(self):
        wid = self.start()
        self.run_cli('sdd','workflow','select','--work',wid,'--name','bmad-build','--entrypoint','bmad-build')
        self.run_cli('sdd','workflow','execute','--work',wid,'--command','bmad-build','--exit-code','1')
        artifact = self.repo / '_bmad-output/story.md'
        artifact.parent.mkdir(); artifact.write_text('# story\n', encoding='utf-8')
        rc, out = self.run_cli('sdd','workflow','complete','--work',wid,'--artifact','_bmad-output/story.md')
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['error'], 'NATIVE_WORKFLOW_EXECUTION_FAILED')
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_workflow_execution_integrity.py -v`，旧代码 rc 为 0。

- [ ] **2. complete 拒绝失败，例外显式分类。**

```python
execution = workflow.get('execution') or {}
if execution and execution.get('exit_code') != 0:
    if not allow_unverified:
        print(json.dumps({'ok':False,'error':'NATIVE_WORKFLOW_EXECUTION_FAILED'})); return 2
    workflow['failure_exception'] = {'reason':unverified_reason,'at':now(),
                                     'exit_code':execution['exit_code']}
```

select/execute/complete 绑定 requirement sha 和 provider；complete 记录真实文件哈希。失败例外只能本次 complete 用 `--allow-unverified --reason` 创建，并由 A queue_event 审计；之前入口无法识别的 unverified 不能自动豁免非零退出。

- [ ] **3. done 使用统一检查。** policy 加入完整谓词：

```python
from pathlib import Path
def workflow_ready(work, repo):
    sdd = work.get('sdd') or {}; wf = sdd.get('workflow') or {}
    if wf.get('status') != 'completed': return False, 'native SDD workflow is not completed'
    if wf.get('requirement_sha256') != work['requirement']['sha256'] or wf.get('provider') != sdd.get('provider'):
        return False, 'native SDD workflow evidence is stale'
    execution = wf.get('execution') or {}
    if not execution and not (wf.get('unverified') or {}).get('allowed'):
        return False, 'native SDD workflow has no execution'
    if execution and execution.get('exit_code') != 0 and not (wf.get('failure_exception') or {}).get('reason'):
        return False, 'native SDD workflow execution failed'
    paths = wf.get('artifacts') or []; hashes = wf.get('artifact_hashes') or {}
    if not paths: return False, 'native SDD workflow has no artifacts'
    for relative in paths:
        p = (repo / relative).resolve()
        if not p.is_relative_to(repo.resolve()) or not p.is_file(): return False, 'workflow artifact missing'
        if hashes.get(relative) != hashlib.sha256(p.read_bytes()).hexdigest(): return False, 'workflow artifact changed'
    return True, None
```

Provider 目录归属仍由 B5 检查；done 再检查而不只相信旧记录。missing/changed 返回完整产物路径及重新完成记录指引。例外展示由 C3 接入。
- [ ] **4. 验证。** 非零不能普通 complete/done；零正常；显式失败例外带原因和审计；空原因拒绝；旧入口例外不豁免失败；文件删除/修改、需求变化、Provider 变化失效。
- [ ] **5. 提交。** `git add scripts/completion_policy.py scripts/cao.py tests/test_workflow_execution_integrity.py && git commit -m "fix: reject unsuccessful or stale native workflow completion"`。

## B4：绑定当前需求的多产物计划

**Files:** Modify `scripts/cao.py:2360-2414,3193-3307,4735-4849`, `protocol/work-item.schema.json`, `tests/test_plan_completion_gate.py`; Create `tests/test_plan_scope.py`。

**Interfaces:** `bind_plan_scope(repo, work, plan_paths: list[str], selections: dict[str,list[str]], no_plan: bool) -> dict`；`provider_pending_plan_items(repo, work) -> list[str]`；cao 保留现有 checkbox/sprint 解析。scope 为 `{version:1, mode:'bound'|'none'|'unresolved', plans:[{path,item_ids}]}`。

- [ ] **1. 写作用域回归。** 在 `tests/test_plan_scope.py` 用 RepairCase 构建两个 change 并显式写 work.sdd.provider/native_ref（测试建模，不是正式 CLI 手工修改状态）：

```python
from repair_helpers import RepairCase
from scripts import cao
class PlanScopeTests(RepairCase):
    def test_unrelated_tasks_do_not_block(self):
        work = self.read_work(self.start())
        for name, text in [('a','- [x] A\n'),('b','- [ ] B\n')]:
            p = self.repo / f'openspec/changes/{name}/tasks.md'
            p.parent.mkdir(parents=True); p.write_text(text, encoding='utf-8')
        work['sdd'] = {'provider':'openspec','native_ref':'openspec/changes/a',
            'plan_scope':{'version':1,'mode':'bound','plans':[{'path':'openspec/changes/a/tasks.md','item_ids':[]}]}}
        self.assertEqual(cao.provider_pending_plan_items(self.repo, work), [])
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_plan_scope.py -v`，旧签名只接受 Provider，不满足该契约。

- [ ] **2. 实现有界查询。**

```python
def provider_pending_plan_items(repo, work):
    scope = (work.get('sdd') or {}).get('plan_scope') or {}
    if scope.get('mode') == 'none': return []
    if scope.get('mode') != 'bound': raise ValueError('PLAN_SCOPE_UNRESOLVED')
    pending = []
    for plan in scope.get('plans', []):
        p, relative = resolve_artifact_path(repo, plan['path'])
        if not p.is_file(): raise ValueError('PLAN_SOURCE_MISSING: ' + relative)
        if p.name == 'sprint-status.yaml':
            ids = plan.get('item_ids') or []
            if not ids: raise ValueError('SHARED_PLAN_ITEM_BINDING_REQUIRED')
            states = {}
            for line in p.read_text(encoding='utf-8').splitlines():
                match = SPRINT_STATUS_LINE.match(line)
                if match: states[match.group('name').strip().strip("\"'")] = match.group('state').strip()
            if any(i not in states for i in ids): raise ValueError('PLAN_ITEM_MISSING')
            pending.extend(f'{relative}#{i} ({states[i]})' for i in ids
                           if states[i] not in SPRINT_DONE_STATES and not states[i].startswith('optional'))
        elif p.name == 'tasks.md':
            pending.extend(f'{relative}: {item}' for item in _pending_checkbox_tasks(p))
        else: raise ValueError('PLAN_FORMAT_UNSUPPORTED')
    return pending
```

- [ ] **3. 绑定及 CLI。** complete 增加重复 `--plan PATH`、`--plan-item PATH#ID`、互斥 `--no-plan`。计划路径检查当前 Provider 真实目录；sprint ids 必须存在且不能用 epic/retrospective 聚合标记代替真实故事。输入 selections 按 `rsplit('#',1)` 聚合。显式多路径完整保存；无显式参数时只从唯一绑定的 native_ref/change/feature 自动找 tasks，不能全目录猜。BMAD 共享计划没有明确条目则 unresolved。无计划仅在 `--no-plan` 明确声明时保存 none 并审计；存在已绑定有效计划时不能单凭 no-plan 擦除范围，需需求/计划重新锚定。

bind_plan_scope 在 complete 的产物引用写入内存之后、保存之前调用。使用下列完整绑定函数；路径检查不依赖 B5 尚未交付的模块，B5 再统一提取：

```python
def bind_plan_scope(repo, work, plan_paths, selections, no_plan):
    repo = repo.resolve()
    sdd = work.get('sdd') or {}
    roots = [(repo / d).resolve() for d in provider_output_dirs(sdd.get('provider'))]
    paths = set(plan_paths) | set(selections)
    anchors = set()
    ref = sdd.get('native_ref')
    if ref:
        p, _ = resolve_artifact_path(repo, ref)
        if p.exists(): anchors.add(p if p.is_dir() else p.parent)
    for relative in (sdd.get('workflow') or {}).get('artifacts', []):
        p, _ = resolve_artifact_path(repo, relative)
        anchors.add(p.parent)
    discovered = set()
    ambiguous = False
    public_roots = set(roots) | {repo / 'openspec/changes', repo / 'specs'}
    for anchor in anchors:
        for name in ('tasks.md', 'sprint-status.yaml'):
            p = anchor / name
            if p.is_file():
                discovered.add(p.relative_to(repo).as_posix())
                if anchor in public_roots: ambiguous = True
    old = sdd.get('plan_scope') or {}
    if no_plan:
        if paths or discovered or old.get('mode') == 'bound':
            raise ValueError('PLAN_SCOPE_REMOVAL_REQUIRES_REANCHOR')
        return {'version':1, 'mode':'none', 'plans':[]}
    if not paths and ambiguous:
        return {'version':1, 'mode':'unresolved', 'plans':[]}
    paths = paths or discovered
    if not paths:
        return {'version':1, 'mode':'unresolved', 'plans':[]}
    plans = []
    for value in sorted(paths):
        p, relative = resolve_artifact_path(repo, value)
        if not p.is_file() or not any(p.is_relative_to(root) for root in roots):
            raise ValueError('PLAN_SOURCE_INVALID')
        ids = sorted(set(selections.get(value, [])))
        if p.name == 'sprint-status.yaml':
            states = {}
            for line in p.read_text(encoding='utf-8').splitlines():
                match = SPRINT_STATUS_LINE.match(line)
                if match: states[match.group('name').strip().strip("\"'")] = match.group('state').strip()
            if not ids: raise ValueError('SHARED_PLAN_ITEM_BINDING_REQUIRED')
            if any(i not in states or any(m in i.lower() for m in SPRINT_IGNORED_NAMES) for i in ids):
                raise ValueError('PLAN_ITEM_INVALID')
        elif p.name != 'tasks.md' or ids:
            raise ValueError('PLAN_FORMAT_UNSUPPORTED')
        plans.append({'path':relative, 'item_ids':ids})
    return {'version':1, 'mode':'bound', 'plans':plans}
```

输入计划路径先规范化，再规范化 selections 的键，防止 `./path` 与 `path` 的 ID 映射丢失。仅从当前原生引用和当前工作流产物的直属目录发现计划，不递归搜索 Provider 全树；明确知道其他关联目录时通过重复 --plan 输入。自动路径若是 Provider 公共根且无法建立需求身份，返回 unresolved，不能据目录位置假定关联。none 的声明由 A 的事件队列审计。

未自动唯一绑定时返回 unresolved，不生成空 bound。completion_status 捕获绑定/缺失错误并给 complete --plan/--plan-item 指引。更新旧 NoFalsePositives 用例：明确 --no-plan 才无计划完成；有计划仍不可绕过。tests 中三 Provider 的标准配置先于 start，tests gate 自动派生。
- [ ] **4. 验证。** 同需求多 change 均检查；B 不影响 A；BMAD 13 个关联故事不可只选第一个；共享文件无关故事不计；不明确绑定阻断；计划删除、unknown ID、归档、明确无计划均有正反例。全量通过。
- [ ] **5. 提交。** `git add scripts/cao.py protocol/work-item.schema.json tests/test_plan_scope.py tests/test_plan_completion_gate.py && git commit -m "fix: scope native plan completion to bound work items"`。

## B5：真实路径、patch 和受保护状态

**Files:** Create `scripts/path_guard.py`, `tests/test_path_boundaries.py`; Modify `scripts/cao.py:312-330,3320-3328,4398-4406,4446-4486,4541-4578,4581-4705`。

**Interfaces:** `repo_file(repo: Path,value: str) -> Path`、`inside(path: Path,root: Path) -> bool`、`provider_artifact(repo,value,dirs) -> Path`、`patch_targets(text: str) -> list[str]`。

- [ ] **1. 写失败测试。**

```python
from repair_helpers import RepairCase
class BoundaryTests(RepairCase):
    def test_requirement_traversal_cannot_edit_config(self):
        self.start()
        rc, out = self.run_cli('host-event','--host','pi', payload={'event':'pre_tool',
            'session_id':'guard','tool_name':'write','tool_input':{
            'path':'.orchestrator/requirements/../config.json','content':'{}'}})
        self.assertEqual(out['decision'], 'deny', out)
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_path_boundaries.py -v`，旧代码 allow。

- [ ] **2. 实现路径核心。**

```python
import re
from pathlib import Path
def inside(path, root): return path.resolve().is_relative_to(root.resolve())
def repo_file(repo, value):
    p = Path(value)
    p = (p if p.is_absolute() else repo / p).resolve()
    if not inside(p, repo): raise ValueError('PATH_OUTSIDE_REPOSITORY')
    return p
def provider_artifact(repo, value, dirs):
    p = repo_file(repo, value)
    if not any(inside(p, repo / directory) for directory in dirs):
        raise ValueError('NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS')
    return p
def patch_targets(text):
    return [m.group(1).strip() for m in re.finditer(
        r'^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$', text, re.M)]
```

validate_workflow_artifacts 改为接收 repo 并调用 provider_artifact；complete/done 双重检查真实目录。protect：target 在 `.orchestrator` 内而不在真实 requirements 子树中则 deny；保护原生输出目录符号链接本身时检查原始链接路径，不能因链接指向仓库外就当无状态目标。
- [ ] **3. 接入全部目标。** `_collect_values_for_keys` 提取 patch 字符串后 patch_targets；处理 apply_patch input 字符串与对象。Shell 使用 shlex 分词识别重定向、分隔符和明确路径，不因命令前缀认定只读；复合命令逐段检查，合法独立 CAO 治理调用可通行。不能解析的动态命令提醒风险，不宣称全面隔离。产品写仍 allow+context；混合 SDD/产品目标不能整体豁免。
- [ ] **4. 验证。** 补充原生产物 `_bmad-output/../src.py`、symlink、绝对路径、不存在目标父目录、patch 多文件/移动目标、`cat /dev/null > .orchestrator/config.json`、`ls; rm`、正常 requirement/intake/CAO 命令及产品写提醒。结构化保护全部通过，动态 Shell 限制在 README 明示。
- [ ] **5. 提交。** `git add scripts/path_guard.py scripts/cao.py tests/test_path_boundaries.py && git commit -m "fix: enforce canonical provider and managed-state path boundaries"`。

## B 出口检查

- [ ] R01/R02/R03/R04/R06/R07/R12 新旧规则有明确正反例；旧 --help 不用于授予 tests。
- [ ] 配置/协议/验证输出保留 gates；接受标准哈希在所有使用者完全相同。
- [ ] 所有新函数由本计划或 A 定义；旧 CLI 参数保留，新增 tests 与 plan 配置入口有 help/测试。
- [ ] `done` 输出 pending_gates、计划路径、变更产物及具体可执行下一步；不能仅返回概括错误。
- [ ] 全量回归后进入 C，不把缺少正式宿主验证记成已通过。
