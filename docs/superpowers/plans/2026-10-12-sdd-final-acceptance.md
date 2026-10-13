> 2026-10-13 修订：Task 3—6 仅为 standard/comprehensive 接入独立验收；light 完成采用 SPEC/标准绑定与项目验证，无 workflow/plan 硬门禁。本轮执行以 2026-10-13-light-and-pi-repair.md 为准，不照搬旧 worktree 路径。

# standard/comprehensive 独立 SDD 收尾验收实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. 用户已经选择当前会话顺序执行、无子代理，不重复请求执行方式选择。

**Goal:** 技术验证和针对当前实现的新一次 SDD 需求/设计验收同时通过才允许 done；light 前置仅最简原生 SPEC 与标准。

**Architecture:** 纯策略、文件边界、运行状态与 CLI 编排分别放在小模块；现有核心提供事务、快照和 Work 持久化。技术 verify 与 SDD 验收独立记录，共用一致的精确输出排除边界；命令由有界 runner 实际执行，Skill 必须当前 run 提交逐项证据。

**Tech Stack:** Python >=3.10 标准库、unittest、现有 JSON/锁/原子写入，Pi API 1.1.0、Node 契约与严格 TypeScript。

## Global Constraints

- 已批准设计：`docs/superpowers/specs/2026-10-12-sdd-final-acceptance-design.md`；不再等待总体设计审批。
- 当前隔离 worktree `/Users/wymix/Projects/coding-agent-orchestrator/.worktrees/cao-integrity-repair`；顺序 TDD、独立提交、inline 自审，无子代理。
- standard/comprehensive 强制双验收；light 仅规范与技术检查；人工 gate、旧 workflow/报告、例外和 CLI override 不补造 SDD 验收。
- light 前置 SPEC+非空可观察标准即可，不强制 PRD/架构/Story/tasks；原生前置与已绑定计划仍有效。
- 产品/intake 写保持 allow+提醒，内部派生状态 deny；不主动 turn，不调用 LLM，不自动安装/重锚/Work 重建。
- 源文档/产品文件不能作为验收报告排除；精确输出文件受 Provider/Work 范围和真实路径限制，不排除整个目录。
- 长操作不持锁，提交重读输入；不丢并发决策，输入变化 stale，旧成功不复用。
- JSON/标准库/现有 CLI 不重写；每个新增代码文件 <500 行；不猜技术栈或覆盖率百分比，不解析第三方报告。
- 历史 completed 不重新认证；active/reopened 补新契约。真实 Host/浏览器/跨平台仍单独验收。
- 不推送、合并、发布或修改异机应用。任何失败/未完成不能当作通过。

## 文件职责与接口

1. `scripts/sdd_acceptance_policy.py`：纯声明/报告规范化、hash、结果聚合和新鲜度。
2. `scripts/sdd_acceptance_files.py`：来源/证据/精确输出路径验证、正文和读取上限；无 Work 写入。
3. `scripts/sdd_acceptance_runtime.py`：纯 Work 记录配置、重锚、开始和提交，不执行外部命令。
4. `scripts/sdd_acceptance_commands.py`：CLI 编排，调用 core adapter 的既有事务/快照/存储与 runner。
5. `scripts/cao.py`：窄路由、验收输入、重锚/需求失效、完成门禁、轻量指导；不堆新大段业务。
6. 协议、模板/Skill、Dashboard、Python/Node 正式测试、安装冒烟与验证报告：暴露一致契约。

### 固定数据接口

声明 `D`：
```json
{"spec":"_bmad-output/spec.md","sources":[],"criteria":[{"id":"AC-1","source":"_bmad-output/spec.md","kind":"requirement","description":"Implement the declared behavior"}],"executor":{"mode":"command","entrypoint":"python check_acceptance.py","timeout_seconds":300},"outputs":[]}
```
`spec` 是 Provider 原生文件；sources 合并 spec/条目来源并排序去重。kind 为 requirement/design。outputs 可空（纯 stdout 报告）；executor.mode 为 command/skill，timeout 复用 normalize_timeout。标准需至少一个 requirement 条目；light 的必要约束可同 SPEC 用 design 条目表达，不强制独立设计文件。

报告 `R`：
```json
{"run_id":"sdda-current","input_sha256":"frozen-sha","provider":"bmad","items":[{"id":"AC-1","status":"passed","evidence":[{"path":"acceptance-tests/test_acceptance.py","reason":"Actual acceptance assertion passed"}]}]}
```
每个预期 ID 恰好一项，status 为 passed/failed/blocked；每项至少一个可复核证据与非空理由。overall 字段不能覆盖逐项结果。结构检查不宣称机械证明 Agent 真正执行过 Skill。

Work `A = work.sdd.acceptance`：version=1、declaration、declaration_sha256、anchor_sha256、anchor_sources、run。run 含 id/status/inputs/input_sha256/execution/results/evidence_hashes/output_hashes/reason。技术证据仍只在 work.verification。

冻结输入 `I`：现有 verification_inputs 五字段，加 work_id/provider/declaration_sha256/source_hashes。不包含 Work state_revision、时间戳或决策。

## Task 1: 纯声明与结果策略

**Files:** Create `scripts/sdd_acceptance_policy.py`, `tests/test_sdd_acceptance_policy.py`。

**Interfaces:**
- `digest(value: object) -> str`：canonical UTF-8 JSON SHA-256。
- `normalize_declaration(raw: dict) -> dict`：输出 D；校验文本、相对路径、kind/mode/timeout、唯一 ID、来源/输出不重合；不触盘。
- `declaration_hash(declaration: dict) -> str`：规范声明 digest。
- `normalize_report(raw: dict, declaration: dict, run_id: str, input_sha256: str, provider: str) -> list[dict]`：输出按预期 ID 顺序的结果，严格身份/覆盖/证据类型。
- `result_state(results: list[dict], exit_code: int) -> str`：passed/failed/blocked/error，非零执行不可被 all passed 抹掉。
- `is_fresh(run: dict | None, inputs: dict, hashes: dict[str, str]) -> bool`：检查 run passed、输入 hash、记录/当前证据和输出 hash 全等。

- [x] 写 unittest RED，使用 deepcopy 基线声明，覆盖 invalid root/text/path/kind/mode/timeout、空/重复 ID、source/output 重合；报告空/漏/重复/未知 ID、旧身份、空理由、缺证据，且 raw 字节意义不被规范化函数修改。
```python
class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.declaration = {'spec':'_bmad-output/spec.md', 'sources':[], 'outputs':[],
            'criteria':[{'id':'AC-1', 'source':'_bmad-output/spec.md', 'kind':'requirement', 'description':'Declared behavior'}],
            'executor':{'mode':'command', 'entrypoint':'python check_acceptance.py'}}
        self.report = {'run_id':'sdda-current', 'input_sha256':'frozen-sha', 'provider':'bmad',
            'items':[{'id':'AC-1', 'status':'passed', 'evidence':[{'path':'src.py','reason':'Checked implementation'}]}]}
    def test_closed_coverage_not_overall_claim(self):
        d = normalize_declaration(self.declaration)
        r = deepcopy(self.report)
        r['overall'] = 'passed'
        r['items'] = []
        with self.assertRaisesRegex(ValueError, 'SDD_ACCEPTANCE_RESULT_COVERAGE'):
            normalize_report(r, d, 'sdda-current', 'frozen-sha', 'bmad')
    def test_failures_are_not_promoted(self):
        self.assertEqual(result_state([{'status':'passed'}], 1), 'failed')
        self.assertEqual(result_state([{'status':'blocked'}], 0), 'blocked')
```
- [x] `python3.11 -m unittest discover -s tests -p test_sdd_acceptance_policy.py -v`，确认缺模块/函数导致 RED，再实现。
- [x] 使用 canonical digest、集合精确覆盖和声明顺序输出；errors 为 `SDD_ACCEPTANCE_DECLARATION_INVALID`、`SDD_ACCEPTANCE_RESULT_INVALID`、`SDD_ACCEPTANCE_RESULT_IDENTITY`、`SDD_ACCEPTANCE_RESULT_COVERAGE`。
```python
def digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def result_state(results, exit_code):
    if (isinstance(exit_code, bool) or not isinstance(exit_code, int) or not isinstance(results, list)
            or any(not isinstance(item, dict) or not isinstance(item.get('status'), str)
                   or item['status'] not in {'passed', 'failed', 'blocked'} for item in results)):
        raise ValueError('SDD_ACCEPTANCE_RESULT_INVALID')
    if exit_code >= 3 or exit_code < 0:
        return 'error'
    if exit_code == 2:
        return 'blocked'
    if exit_code == 1 or any(item['status'] == 'failed' for item in results):
        return 'failed'
    if not results or any(item['status'] == 'blocked' for item in results):
        return 'blocked'
    return 'passed'

def is_fresh(run, inputs, hashes):
    return bool(run and run.get('status') == 'passed'
                and run.get('input_sha256') == digest(inputs)
                and run.get('inputs') == inputs
                and {**run.get('evidence_hashes', {}), **run.get('output_hashes', {})} == hashes)
```
规范化逐字段校验，不 str() 接受布尔/数字；使用 PurePosixPath，拒绝绝对、反斜线、`.`/`..` 部件。正文含 ID/来源只是机械接口，不解析自然语言含义。未知总体 claim 不具有授权作用。
- [x] 定向 GREEN + 全量 Python/Node/TS/compileall/diff，提交 `feat: define independent SDD acceptance declarations and result policy`。

Task 1 验证：初始15项RED均明确缺模块；额外聚合非法输入7个子例RED。16项定向、482项Python/131.079秒、13 Node无失败/跳过、严格TS、compileall/Shell/diff通过。日志 `/tmp/cao-sdd-acceptance-1-*.log`。目前仅完成纯策略，硬门禁尚未接入。

## Task 2: 来源、正文与精确报告边界

**Files:** Create `scripts/sdd_acceptance_files.py`, `tests/test_sdd_acceptance_files.py`。

**Consumes:** Task 1 D/R/digest，path_guard.repo_file/provider_artifact，现有 Provider output_dirs。
**Produces:**
- `source_hashes(repo: Path, declaration: dict, provider_dirs: list[str]) -> dict[str,str]`：原生来源 hash；SPEC/标准只有标题、空正文或读失败拒绝。
- `output_paths(repo: Path, declaration: dict, provider_dirs: list[str], work_id: str, previous: dict[str,str]) -> set[str]`：精确合法输出；首次须不存在，已有须 previous 记录且内容匹配。
- `report_hashes(repo: Path, results: list[dict], outputs: list[str]) -> dict[str,str]`：证据/输出必须是真实仓库文件，hash 全字节。
- `read_json(path: Path) -> dict`：最多 1 MiB UTF-8，非法/超限/非对象报结构错误，不部分读取当成功。

- [x] 写 RED：同 SPEC 来源有效、只标题拒绝、不强制其他工件；外部/链接别名/目录/输入冒充报告拒绝；新输出在 `<provider-dir>/cao-acceptance/<work-id>/` 下，仅 md/json/txt，未登记变化不能被排除；首次既有文件拒绝，旧已拥有报告允许。
```python
with self.assertRaisesRegex(ValueError, 'SDD_ACCEPTANCE_OUTPUT_INVALID'):
    output_paths(repo, {**d, 'outputs':[d['spec']]}, ['_bmad-output/'], wid, {})
before = source_hashes(repo, d, ['_bmad-output/'])
(repo / d['spec']).write_text('# Title only\n', encoding='utf-8')
with self.assertRaisesRegex(ValueError, 'SDD_ACCEPTANCE_SOURCE_INVALID'):
    source_hashes(repo, d, ['_bmad-output/'])
```
- [x] 跑 `python3.11 -m unittest discover -s tests -p test_sdd_acceptance_files.py -v` RED。
- [x] 用 provider_artifact 检查来源/输出、repo_file 检查证据；拒输出 resolve 后与来源/其他输入相同，拒 symlink 输出，已有输出 byte hash 不匹配拒，全部 lexical 相对路径保留。正文检查只去 BOM/frontmatter/空白/Markdown 标题后至少有正文，不能把长度/标题当作语义验收证明。
```python
MAX_REPORT_BYTES = 1024 * 1024

def read_json(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('SDD_ACCEPTANCE_REPORT_TOO_LARGE')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('SDD_ACCEPTANCE_RESULT_INVALID')
    return value
```
- [x] 定向/全量与路径老回归 GREEN；提交 `feat: bind SDD acceptance sources and safe report outputs`。

Task 2 验证：14项初始RED均缺文件模块，另补大文件证据hash回归RED→GREEN。源正文/JSON读取最多1MiB，证据/原生报告hash按64KiB块覆盖完整字节，不误用JSON长度上限。15项文件定向、31项组合、497项Python/134.128秒、13 Node无失败/跳过、严格TS/compileall/Shell/diff通过。日志 `/tmp/cao-sdd-acceptance-2-*.log`。

## Task 3: Work 运行状态、输入绑定与显式重锚

**Files:** Create `scripts/sdd_acceptance_runtime.py`, `tests/test_sdd_acceptance_runtime.py`; modify `scripts/cao.py` verification_inputs/cmd_reopen; modify `scripts/completion_policy.py` reset_requirement。

**Consumes:** Task 1/2；现有代码/规则 hash、verification_fresh、core.save_work/queue_event/事务。
**Produces:**
- `configure_record(work: dict, declaration: dict) -> bool`：相同不写，不同清旧 run、anchor=None，不影响决策/原生workflow。
- `reanchor_record(work: dict, sources: dict[str,str]) -> None`：显式接受声明/source hashes，清 run。
- `start_run(work: dict, inputs: dict) -> dict`：fresh tech 与配置锚由 caller 检查；新 UUID run、running、旧成功失效。
- `finish_run(work: dict, run_id: str, inputs: dict, report: dict | None, execution: dict, hashes: dict[str,str], error: str | None = None) -> dict`：旧 run拒，输入变化 stale，否则规范结果/聚合，不改变无关更新。
- `acceptance_status(work: dict, inputs: dict, hashes: dict[str,str]) -> dict`：ready/state/reason，派生非人工状态。
- core `sdd_acceptance_inputs(repo, work, cfg) -> dict`：I；`sdd_acceptance_outputs(repo,work) -> set[str]`：当前合法精确输出，不能依赖结果已经 passed 才排除。
- core `sdd_acceptance_current_hashes(repo, work) -> dict[str,str]`：对记录的 results 与 declaration.outputs 调用 report_hashes；无 run 返回空 dict，失败返回派生 blocked，不吞错误成为通过。

- [ ] RED：来源变化/代码变化/技术标准变化/重评估 stale；无关决策 revision 不影响；新 run 失败不复用旧 passed；当前 run 身份变更拒绝；configure 不自动锚且 unchanged 重试还提示审核。
```python
first = start_run(work, inputs)
second = start_run(work, inputs)
with self.assertRaisesRegex(ValueError, 'SDD_ACCEPTANCE_RUN_CHANGED'):
    finish_run(work, first['id'], inputs, report, {'exit_code':0}, hashes)
self.assertEqual(work['sdd']['acceptance']['run']['id'], second['id'])
```
- [ ] 在verification_inputs中将声明/source身份纳入规则快照，技术与原生采用相同 exact outputs。未配置时保留原5字段行为，避免无意义破坏旧工程验证接口；完整路径有配置但来源非法不能 fallback 到无配置；light的可选历史验收不参与其技术输入与关闭硬判定。
```python
inputs = existing_verification_inputs
record = (work.get('sdd') or {}).get('acceptance') or {}
if record.get('declaration'):
    sources = source_hashes(repo, record['declaration'], provider_output_dirs(work['sdd']['provider']))
    inputs['code_snapshot'] = code_snapshot(repo, {work['requirement']['path']} | sdd_acceptance_outputs(repo, work))
    inputs['rules_snapshot'] = digest({'rules':inputs['rules_snapshot'], 'declaration':record['declaration_sha256'], 'sources':sources})
```
`existing_verification_inputs` 指原函数现场构造的5字段 dict，不另建函数。SDD inputs 再合并 work_id/provider/declaration_sha256/source_hashes；JSON digest 不含时间/Work revision。
- [ ] cmd_reopen --reanchor 在已有事务中调用 reanchor_record；reset_requirement 移除旧验收声明/run，保留原决策行为。来源失效需显式重新配置/审核，不自动 accept 新 bytes。
- [ ] 完整定向/全量 GREEN；提交 `feat: track fresh SDD acceptance runs without losing Work updates`。

## Task 4: 真命令执行与 Skill 当前 run 提交 CLI

**Files:** Create `scripts/sdd_acceptance_commands.py`, `tests/test_sdd_acceptance_cli.py`; modify `scripts/cao.py` 新 cmd_sdd_acceptance/parser; modify `scripts/verification_runner.py`、`tests/test_verification_runner.py`。

**Consumes:** D/R/I，Task 1—3 公共函数；adapter 参数为当前 core module，提供 repo_root/ensure_initialized/resolve_work/load_work/save_work/config/verification_inputs/sdd_acceptance_inputs/queue_event/provider_output_dirs/provider_native_skills。不延迟 import 第二份核心。
**Produces:** `dispatch(args, core) -> int`，无外围 locked_command（自己分阶段持锁）；runner `run_script(..., env: dict[str,str] | None=None)` 支持本次 isolated 环境。

CLI：
```text
cao sdd acceptance configure --declaration <repo JSON file> [--work ID] [--replace]
cao reopen --work ID --reanchor --reason "Reviewed SDD acceptance scope"
cao sdd acceptance show [--work ID]
cao sdd acceptance run [--work ID]
cao sdd acceptance begin [--work ID]
cao sdd acceptance submit --run ID --report <repo JSON file> [--exit-code CODE] [--work ID]
```
configure 不执行、不同非空需replace；show只读；run仅command；begin/submit仅skill；全部支持--repo。

- [ ] CLI RED：缺 Work/配置、模式错误、旧/无 run、缺 fresh 技术、旧锚、缺Skill拒绝；configure不执行、不自动reanchor；command真 subprocess 产生marker和全项JSON，缺report/非零/timeout/过大stdout失败；Skill提交精确当前身份。实际command记录不能通过submit伪造。
```python
rc, out = self.run_cli('sdd','acceptance','run')
self.assertEqual(rc, 2)
self.assertFalse(marker.exists())  # 技术还未通过，不启动验收命令
```
- [ ] 新handler只代理 `dispatch(args, sys.modules[__name__])`；parser在现有sdd_sub新增acceptance子命令，每个设置fn=cmd_sdd_acceptance。不套locked_command给长操作。
- [ ] configure/begin/submit用repo_transaction包整段load/check/mutate/save；run先事务start并持久化，锁外执行，末尾事务重读inputs并finish；提交错误也记录 failed/blocked，不遗留旧passed。
- [ ] 真实命令通过环境获取run身份和scope，无全局os.environ修改；stdout须纯JSON，最多1MiB。调用run_script max_bytes=MAX_REPORT_BYTES+1，长度超限拒；存储stdout/stderr仅4000字节尾；无输出/不可解析JSON拒。项目可把详细报告写到已经登记的原生输出路径。
```python
request = {'run_id':run['id'], 'input_sha256':run['input_sha256'],
           'provider':work['sdd']['provider'], 'declaration':record['declaration'], 'inputs':run['inputs']}
env = dict(os.environ)
env['CAO_SDD_ACCEPTANCE_REQUEST'] = json.dumps(request, ensure_ascii=False)
execution = run_script(record['declaration']['executor']['entrypoint'], repo,
                       timeout=record['declaration']['executor']['timeout_seconds'],
                       max_bytes=1024*1024+1, env=env)
```
返回request给Skill begin；拒未安装/不属于Provider的Skill入口；不假装core可证明Skill真实执行。submit来源JSON只作为输入，禁止把普通产品文件登记为输出；证据/已登记报告hash后交finish。
- [ ] runner Popen仅增加env=env；POSIX/Windows监督进程继承本次环境，不改变Job/timeout/有界读逻辑；验收wrapper读取上述JSON，不猜Host变量。
- [ ] 测并发decision与代码改变、第二run取代第一、原子写失败、超时清进程；GREEN全量与Node/TS；提交 `feat: execute command acceptance and collect native Skill results`。

## Task 5: 全 profile 硬门禁、light 基线与兼容夹具

**Files:** Modify `scripts/cao.py` completion_status/AUTO_DERIVED_GATES/PROFILE_BASE_GATES/gate_state/render_context；create `tests/test_sdd_acceptance_completion.py`, `tests/sdd_acceptance_helpers.py`; modify `tests/helpers.py`, `tests/repair_helpers.py` 及原 happy-path done 用例。

**Consumes:** acceptance_status、来源/报告校验与 frozen inputs；现有 workflow_ready/技术fresh/gate派生。
**Produces:** 新 `sdd_acceptance` 自动派生门禁，所有profile独立复查；tests helpers `configure_sdd_acceptance(case,wid) -> dict`、`pass_sdd_acceptance(case,wid) -> dict` 使用真实CLI与可运行夹具，不直接手填passed。

- [ ] 正式RED：standard/comprehensive 在技术passed/nativecompleted/manual reviews齐全时仍需本次独立验收；light 只需有效SPEC/标准与新鲜项目验证；旧报告/全勾选/allow-unverified不能放行，gate satisfy新gate拒；历史completed只读不重认证，reopen后要新验收。
```python
for profile in ('standard','comprehensive'):
    with self.subTest(profile=profile):
        wid = self.start(profile=profile)
        # 用fixture真实命令完成原生workflow和原技术验证，绝不补SDD结果。
        rc, out = self.run_cli('done','--work',wid)
        self.assertEqual(rc, 2, out)
        self.assertIn('SDD', json.dumps(out))
```
- [ ] 新gate只加入standard/comprehensive的PROFILE_BASE_GATES和AUTO_DERIVED_GATES；gate_state显示真实派生结果，gate satisfy不能覆盖；completion_status独立强制调用，不能靠历史assessment列表缺gate绕过。
```python
status = acceptance_status(work, sdd_acceptance_inputs(repo,work,cfg), sdd_acceptance_current_hashes(repo,work))
if not status['ready']:
    return {'ready':False, 'reason':status['reason'], 'sdd_acceptance':status,
            'next':f'Run the configured SDD final acceptance for {work["id"]}; old workflow completion cannot substitute.'}
```
先返回来源/配置/技术/SDD各类准确未满足原因；调用模式为command提示run，skill提示begin→原生Skill→submit。仍不在done自动启动重验。
- [ ] light指导真实Provider最简SPEC+标准；source validation接受同文档，无额外架构/Story/计划。完整路径保留no-plan审计与已有计划冲突；light不要求计划/无计划声明，不用“有一个工件”当SPEC/标准通过。
- [ ] 使用真实CLI实现两个fixture接口：configure_sdd_acceptance 写有正文/标准的原生SPEC和D文件，configure→显式reopen/reanchor，返回声明；pass_sdd_acceptance在已重新登记原生证据后技术verify→command run并返回当前run。原生夹具的SPEC正文至少包含目标/边界与AC-1行为标准，command wrapper从CAO_SDD_ACCEPTANCE_REQUEST读取run_id/input_sha256，实际运行一个项目unittest成功后才输出R，不手填passed。逐项迁移旧happy-path done夹具：显式增加有正文/标准的SPEC、配置与显式重锚、必要重评估/重新登记原生证据、技术verify→真实SDD命令；原错误用例继续验证原错误，不改assert为泛失败。配置重锚不能凭夹具手写避开。
- [ ] 四个模块以上定向/full/Node/TS GREEN；提交 `feat: require fresh SDD final acceptance for full profiles`。

## Task 6: 指导、展示、协议及安装出口

**Files:** Modify `templates/AGENTS.md`, `templates/CLAUDE.md`, `SKILL.md`, `README.md`, `DESIGN.md`, `scripts/dashboard.py`, `scripts/dashboard.html`, `protocol/work-item.schema.json`, `tests/installed_package_smoke.py`, `tests/test_pi_adapter.mjs`, `docs/repair-validation.md`; create `tests/test_sdd_acceptance_guidance.py`, `tests/test_sdd_acceptance_dashboard.py`。

**Interfaces:** 不改Host输运。复用additional_context/steer/triggerTurn:false；Dashboard展示独立 state/reason，只有Work completed关闭。

- [ ] 文档/DOM/协议RED：明确standard/comprehensive双验收、light为SPEC+标准与新鲜项目验证、混合真实执行、旧完成不得替代；UI区分技术passed/SDDpending/failed/stale/ready，不把原生结果passed关闭Work；既有模板不覆盖。
```python
self.assertEqual(overview['status'], 'active')
self.assertEqual(overview['sdd_acceptance']['state'], 'pending')
self.assertFalse(overview['completion']['ready'])
```
- [ ] 指导放在原生流程后、done前；技术fresh→SDD重验→必要技术重验→done。noSkill/no工具/no标准blocked并保留state保护；light不要求原生执行登记或独立验收，只绑定有效原生SPEC与标准。
- [ ] schema保存D/run所有状态与nullable未配置结果；不能以旧字段迁移认定passed。Dashboard只投影相同core接受状态，不新增第二套验收规则。
- [ ] Node真实核心回归当前Skill或真实命令验收提示进入模型且无额外turn，内容/details不变；产品allow/状态deny；安装smoke导入4模块，checkout外3Provider×3模拟Host对照真实执行两个门禁。
- [ ] 离线缓存wheel/editable临时安装，全部Python/Node/TS/编译/Shell/diff；真实Host/平台/浏览器保持原未验边界。无模型压力测试授权不派发代理。
- [ ] 记录实际数字、失败与夹具标签，inline对照设计逐条自审；提交 `docs: guide dual final acceptance and minimal native light workflows`。

## 运行与验收命令

每个Task先新模块RED，再GREEN，再以下完整检查；不靠最后一次全量补前面失败。
```bash
python3.11 -m unittest discover -s tests -p 'test_sdd_acceptance_*.py' -v
CAO_TEST_PYTHON="$(command -v python3.11)" npm run test:pi
npm run check:pi
env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u PI_CODING_AGENT -u PI_SESSION_ID python3.11 -m unittest discover -s tests -v
python3.11 -m compileall -q scripts tests
sh -n hooks/host-event.sh
git diff --check
```

## 自审覆盖与执行状态

- 设计1—4：Tasks 5/6；5：Tasks 1/2/3；6：Tasks 1/3/4；7：Tasks 2/3/4；8：Tasks 3/5/6；9—11：所有Task与出口记录。
- 所有新增函数在接口中定义；D/R/I/Work字段在固定接口定义；现有函数以当前源码为准，若接口差异先更新计划再实现。
- 命令JSON/报告长度、无执行/当前run提交、未知ID、精确输出边界、no-plan与旧证据迁移均有对应RED。
- 已开始执行：Tasks 1/2 纯策略与文件边界已验证；Tasks 3—6 待完成，不能将现有通过结果当作完整新门禁已实现。

## 2026-10-13 实施状态

Task 3—6 已在本次修复集成实现：runtime、configure/show/run/begin/submit、锁外命令执行、当前Skill证据、完整路径独立门禁、Dashboard、schema及wheel/editable安装夹具。light按后续用户决策采用独立SPEC/标准绑定与项目验证；本文件中的旧子提交标题是历史计划，不表示本轮逐一生成同名提交。实际测试与交付记录见 `docs/repair-validation.md` 的2026-10-13章节。
