# CAO 完整性修复验证记录

## 结论与边界

本机验证日期：2026-10-12。实施分支：`fix/cao-integrity-repair`，隔离目录：
`.worktrees/cao-integrity-repair`。A1—C5 已独立提交；C6 的文档、协议、CI 配置与本机安装验收
已实现，但真实宿主、完整浏览器和跨平台出口尚未完成，**不得视为全平台集成或发布验收通过**。
首次交付已按用户授权正常推送修复分支；本轮跟进修复尚未再次推送，未合并或发布。
本机未新增在线 SDD 安装；另附用户提供的异机真实 BMAD 安装日志，不与本机夹具混淆。

环境：macOS arm64、Python 3.11.15、Node 26.10.0；Pi 类型/开发依赖固定 1.1.0、TypeScript 5.9.3。
Node 22.18.0 是声明的最低版本和新增 CI 目标，不是本机实际执行版本。
Python 核心仍仅依赖标准库；Node/TypeScript 不成为 Python 核心运行时依赖。

## 正式回归与原审查问题

下表是仓库内回归路径，不以临时复现脚本替代正式测试。成功对照与失败场景随各任务一起验证。

| 问题 | 修复任务 | 主要回归 |
|---|---|---|
| R01 非零原生执行被当成功 | B3 | `tests/test_workflow_execution_integrity.py` |
| R02 不识别真实测试包装器 | B2 | `tests/test_verification_bindings.py` |
| R03 可选失败测试/空操作冒充门禁 | B2 | 同上，显式 required/tests 能力及人工门禁拒绝 |
| R04 无关计划阻塞当前任务 | B4 | `tests/test_plan_scope.py` |
| R05 unblock 产生多个 active | A4 | `tests/test_active_work_integrity.py` |
| R06 重锚后复用旧评审/流程 | B1/B3 | `tests/test_evidence_binding.py`、工作流完整性回归 |
| R07 路径/patch/Shell 绕过状态保护 | B5 | `tests/test_path_boundaries.py` |
| R08 原生 Host 提醒丢失 | C2 | `tests/test_host_lifecycle_contract.py` |
| R09 并发 decision 丢更新 | A2/A3 | `tests/test_state_store.py`、`tests/test_state_transactions.py` |
| R10 原生完成被 UI 当需求完成 | C3 | `tests/test_dashboard_completion_state.py` |
| R11 否定完成表达误阻 Stop | C2 | `tests/test_host_lifecycle_contract.py` |
| R12 产品源码冒充原生产物 | B5 | 路径边界回归，含 symlink/穿越/例外不豁免 |
| R13 重复重锚丢审计操作 | A5 | `tests/test_audit_recovery.py` |
| R14 Pi ID/助手文本载荷错误 | C1 | `tests/test_pi_adapter.mjs`，正式 1.1.0 类型检查 |
| R15 每轮结束被当退出/边界继续错误 | C1/C2 | Node 桥契约、Host 生命周期回归 |

附加加固：C4 的 `tests/test_verification_runner.py` 检查超时、有限输出、真实父/孙 PID 清理、
父先退出、异常路径、锁释放和 timeout 验收绑定；C5 的 `tests/test_snapshot_integrity.py`
检查提前剪枝、普通树哈希兼容、读取失败、文件/目录链接策略，不引入快照缓存。

C6 出口复核还复现了入口例外审计键缺少需求版本/Provider 的碰撞：重锚后同名、同理由
例外与旧事件合并。两项正式 red 回归后，身份和审计载荷加入 requirement hash 与 Provider；
同一作用域的重试仍幂等，不把不同需求/Provider 的例外视为同一次声明（独立提交 `9eb6b15`）。
日志：`/tmp/cao-repair-c6-audit-red.log`、`-audit-targeted.log`。

## 本机实际执行

以下为首次 C6 交付的历史结果；后续三项修复的新鲜结果见下一节。完整日志保存在本机
`/tmp/`，不是跨平台证据；旧 wheel/editable 结果不充当后续代码的重新安装验收。

| 验证 | 实际结果 | 日志/证据 |
|---|---|---|
| Python 全量 | **416 项，119.419 秒，OK** | `/tmp/cao-repair-c6.log` |
| Node Pi 桥契约 | **11 项通过，0 失败/跳过** | `/tmp/cao-repair-c6-node.log` |
| Pi 1.1.0 严格 TS | 通过 | `/tmp/cao-repair-c6-types.log` |
| Host schema/CLI 定向 | 17 项 Host、10 项 CLI 通过 | `/tmp/cao-repair-c6-targeted.log` |
| 出口审计定向 | 18 项通过 | `/tmp/cao-repair-c6-audit-targeted.log` |
| compileall、Shell 语法、diff 空白 | 通过 | 下列命令 |
| wheel 离线构建/临时安装 | 通过 | `/tmp/cao-repair-c6-build.log`、`-install.log` |
| wheel 安装后夹具 | **9 个组合通过** | `/tmp/cao-repair-c6-wheel.log` |
| editable 临时安装后夹具 | **9 个组合通过** | `/tmp/cao-repair-c6-editable.log` |
| C5 大忽略树 | 2000 文件不被扫描；访问 4 目录，定向样本 0.000759 秒 | `/tmp/cao-repair-c5-targeted.log` |

计数基准不使用固定毫秒阈值；以上耗时不是性能承诺。安装夹具脚本是
`tests/installed_package_smoke.py`：在 checkout 外导入安装模块，检查 TS/Hook/Skill/HTML 资源，
执行需求分配、start、未评估产品写提醒、状态写拒绝、两轮 active、显式退出 ended、
本地流程输出、verify/done。BMAD/OpenSpec/Spec Kit 均是**明确标注的本地夹具**；三类 Host
事件由 CLI 注入，**没有启动真实 Claude Code、Codex 或 Pi 宿主**。

wheel 证据中的 runner 路径位于临时 venv 的 `site-packages`，editable 指向授权 worktree。
构建和两个安装环境均位于 `/tmp/cao-repair-c6-package.WVvlRV/`；本次使用离线缓存，
没有新增全局 Python/npm 安装。npm 锁文件更新也使用 `--offline --ignore-scripts`。

核心复验命令（在隔离 worktree）：

```bash
npm run check:pi
CAO_TEST_PYTHON="$(command -v python3.11)" npm run test:pi
env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u PI_CODING_AGENT -u PI_SESSION_ID python3.11 -m unittest discover -s tests -v
python3.11 -m compileall -q scripts tests
sh -n hooks/host-event.sh
git diff --check
uv build --wheel --python python3.11 --offline --out-dir <temporary-dist>
python3.11 tests/installed_package_smoke.py --cao <venv>/bin/cao --python <venv>/bin/python
```

`--help` 只用于确认安装/参数入口，不充当业务测试。配置中的测试门禁运行的是实际 unittest。
协议回归还实际运行无 assessment 的 CLI 工程检查，确认 assessment_revision 可为 null，且 done
仍被阻止；新增 schema 未用非空 revision 限制误拒这种历史/工程检查记录。
旧 A5 安装包对新模块导入失败作为安装对照，未误用旧 wheel 作为修复后的通过证据。

## 异机 Pi 会话发现与三项跟进修复

用户提供的真实会话 `2026-10-12T05-47-50-702Z_01a12ea7-2e2e-75d6-81eb-59184064b245.jsonl`
共 151 条，解析无错误。真实观察到 CAO 启动/提示上下文、对应 Native Session ID、显式选择后的
BMAD `6.12.2` 安装，以及门禁/标准变更/产物变更/计划作用域检查。该会话同时暴露后端崩溃和
伪原生入口，不能判端到端通过。18 条 CAO 消息成 9 对重复；用户确认全局和项目各安装一次，
本轮明确不处理重复加载。

三项均在 `41b0d3c` 复现并转为正式失败回归后修复：

| 修复 | 结果与边界 |
|---|---|
| nullable verification（`e594cc6`） | post_tool/file_changed 支持 null/缺失；passed 仍标记 stale。Node 适配器测试通过实际 Python 核心，保留工具结果且不再产生截图警告；仍是模拟事件，不是重启真实 Pi |
| 绝对路径治理命令（`1ba1668`） | 认可所属 package launcher、sh fallback、canonical alias 和 PATH console；不按任意 basename=cao 豁免；混合 Shell/重定向状态写继续 deny |
| 复合占位入口（本节同一提交） | mkdir/echo 输出路径不再证明原生调用；仅检查 CLI/Skill/模块/脚本位置。真实命令/Skill、常见 runner 和合法前置组合保留；未知/动态入口不能靠 Provider 字符串认证，显式理由例外仍审计 |

正式路径：`tests/test_pi_session_repairs.py`（19 项）和 `tests/test_pi_adapter.mjs` 的真实核心桥回归。
全量首次发现 2 个兼容失败：旧用例以描述性文字充当入口，已改为实际 native 命令；合法
`invoke skill bmad-build` 记法按 Skill 名位置解析，不放宽任意参数。两者均重新验证。
静态入口检查只拒绝可确定的占位/无关形式，不证明任意脚本语义，也不替代真实 Skill 流程验收。

最新本机结果：**435 项 Python，124.345 秒，OK；12 项 Node，0 失败/跳过；严格 TS、compileall、
Shell 语法及 diff 检查通过**。日志 `/tmp/cao-pi-fix3-full.log`、`-node.log`、`-types.log`、
`-targeted.log`；失败证据为 `/tmp/cao-pi-fix1-red.log`、`-fix1-node-red.log`、`-fix2-red.log`、
`-fix3-red.log`、`-fix3-utility-red.log`、`-fix3-skill-red.log`（后几项共同前缀 `/tmp/cao-pi`）。

异机 Maven 日志 L126/L148 为 `Tests run: 0`：旧 Surefire 2.17 使用 JUnit3 provider，未执行
Jupiter 测试。脚本退出 0 能满足显式声明的结构门禁，不等于业务验证通过；该项目应升级
Surefire、启用零测试失败及实际功能测试，不把 Maven 报告解析硬编码到通用核心。
本轮没有改异机应用、复跑其 Maven 或让零测试被重新认证为业务成功。

首次项目技术栈识别及验证基线引导另记任务 #34，**尚未实现**：已有工程优先分析并复用有效
配置；全新工程先询问技术栈，再生成项目拥有的真实验证脚本。先配置验收基线，不要求空工程
在写首组测试前就验证通过；不自动安装依赖、覆盖配置或重锚活动 Work。

## Chrome 实际渲染尝试：有限证据，不判通过

本机 Google Chrome `152.0.7977.82` 以独立临时 profile 访问 loopback Dashboard，实际执行
页面 JavaScript，生成 DOM 和截图。解析 `work-detail` 区域确认出现「可执行 cao done」；
权威 Work Item 尚为 active。这不是 Node 最小 DOM 模拟。

但 headless `--dump-dom`/截图调用在输出后未及时退出，30 秒期限后返回 124。
最小静态 `data:text/html,<h1>CAO probe</h1>` 同样输出 DOM 后超时（8 秒），说明现象不只发生
于 Dashboard；尚未确定 Chrome/宿主环境的退出根因，不通过修改 CAO 或忽略超时掩盖问题。
未观察到该临时 profile 的残留进程。**浏览器 smoke 未通过**；stale/closed、预览/确认
及交互验收尚未完成。已提交的 C3 Node VM 测试也不能替代这些验收。

证据：`/tmp/cao-repair-c6-browser.log`、`/tmp/cao-repair-c6-browser-artifacts/ready.html`、
`ready.png`、`ready.stderr`；临时尝试脚本 `/tmp/cao-repair-c6-browser.py` 不是正式回归依赖。

## CI 与真实宿主：待运行

`.github/workflows/tests.yml` 保留 Ubuntu Python 3.10—3.13，增加：

- Linux/macOS/Windows 的 state_store 和 verification_runner 专项；Windows 不跳过后代回收断言。
- Node **22.18.0**、Pi 1.1.0 的契约与严格 TS 检查；安装脚本禁用。
- 从 checkout 外验证 wheel 模块、资源及 9 个本地夹具组合。

这些是**已配置但尚未远端执行**的 jobs。Python 3.10/3.12/3.13、Linux/Windows、
Windows 文件锁和 kill-on-close Job 没有本轮实机/远端成功结果。本地 macOS 结果不能替代。

本机只找到 Pi 启动器，没有 Claude Code/Codex CLI。异机日志补充了真实 Pi 加载和提醒送入模型
的部分证据，但缺修后实机重测、对应 Session 文件和会话切换/退出验收。异机 Pi/Node 版本及两份
CAO Git SHA 未直接核对；仅声明经类型/模拟契约检查的 Pi API **1.1.0**，不声明整个 1.x。
Codex 无已验证原生 SessionEnd hook：status 明确标注限制，真实退出使用
`cao handoff --host codex --session <id> --end-session`，不能在每次 Stop 调用。

发布前仍需三类真实宿主分别执行：启动 → 需求录入 → 未评估产品写提醒 → 状态写拒绝 →
两轮仍 active → 切换/退出 ended → 原生流程输出 → verify/done。不得借用模拟事件结果签字。
本机真实 SDD 在线安装未执行，须另获授权。用户提供的异机日志记录了经其明确选择后的 BMAD
6.12.2 安装，但该会话没有真正执行 BMAD Skill；不把安装成功或目录夹具当作原生流程验收通过。

## 首次项目验证引导（2026-10-12，本机验收）

用户已确认 Agent 分析/询问技术栈并生成项目脚本，CAO 只引导、配置和记录证据。
新增 `cao verification configure`：配置事务保留语言/SDD/timeout/未知字段；同声明
unchanged 不写，不同非空或非法声明需显式 replace；不安装 Host/依赖、不运行验证、不改 Work。
已有锚变化提示 acceptance_review_required 和显式 reanchor，重试 unchanged 仍提示未解决变化。
bootstrap 在 SDD 授权后、Work select/resume 前检查空/非法/optional-only 配置；有效配置保持原流程。
Session/prompt 提示进入模型；产品写仅提醒，状态 deny 优先，读取/控制命令/intake 仍放行。
直接 start 保留兼容，仅警告；已有决策/执行状态保留。模板不覆盖项目现有说明。

- Task 1 `969139c`：14 定向、449 Python/125.126 秒、12 Node 与严格 TS 通过。
- Task 2 `23bce48`：12 定向、461 Python/127.808 秒、12 Node 与严格 TS 通过。
- Task 3：5 指导/运行回归、**466 Python/128.842 秒**、**13 Node 无失败/跳过**、严格 TS、
  compileall、Shell 语法和 diff 检查通过。提交前验证组合另跑 **62 项/13.689 秒** 通过，
  Node/类型再次通过。文档 RED 为 5 个明确缺少指导的断言失败。
- 新增项目包装器正式运行用例：配置 ready，但收集零测试时包装器非零退出，verify failed/done
  仍失败；这不是核心解析第三方报告。Java/Jupiter 的 Surefire/零测试配置由 Agent/项目负责。
- 新的 Pi 模拟桥调用真实核心：指导在 before_agent_start/steer 中出现且 triggerTurn:false，
  产品 allow/状态 deny，配置前后 Work 字节不变，原锚直到显式 reanchor 才变。Host 事件仍可
  正常登记 last_agent/revision，不把这些合法变动误报为 configure 副作用。
- 离线缓存 wheel 构建和临时安装、editable 临时安装均通过 checkout 外 **各 9 个**
  Provider 本地目录夹具 × Host 模拟事件组合；额外验证 setup→configure→select 和实际广告的
  canonical launcher readonly 分类。本次两个入口均有执行位；不冒称本次实测 wheel 无执行位。
  包环境 `/tmp/cao-first-project-package.c4jYGk`；日志 `/tmp/cao-setup-3-{build,install,wheel,editable}.log`。
- 定向/全量/Node/类型日志 `/tmp/cao-setup-{1,2,3}-*.log`。先前旧夹具与整份 Work 快照时机、
  非 PATH console 发现差异均已定位并校准用例；未放宽任意同名 launcher 或状态保护。

这些证明 CLI/指令契约、实际项目包装器和本机安装，不证明任意模型按指导生成正确工程、
真实 Pi 重测、Java 登录业务测试、真实 SDD 执行、完整浏览器或跨平台通过。未新增模型压力
测试（用户选择不用子代理）。#32 延期验收不变；本轮本地提交未推送、合并或发布。

## 兼容与操作步骤

1. 产品写入采用提醒模式；没有评估/有重大歧义也不由 Hook 阻断。不要误解为已经满足完成条件。
   `.orchestrator/` 派生状态禁止手改；需求 intake 可写；`cao done` 是硬完成权威。
2. required/tests 能力显式配置 `gates: ["tests"]`，或通过 `cao verification configure --test-script`
   安全声明（首次 init 仍支持 --test-script，但更新配置不要使用 init --force）。
   老字符串脚本、一般 `--verify-script`、命令关键词、人工 tests 声明和 CLI override 不补造能力。
3. scripts/required/gates/`timeout_seconds` 变化需先审阅，再显式
   `cao reopen --work <id> --reanchor --reason "Reviewed acceptance changes"`。
   重锚后重做当前评估、必要流程/门禁和配置验证；不能直接改 hash 或倒填时间。
4. 需求变化用 `cao start --requirement <path> --replace`；旧作用域证据失效，决策/历史仍保留。
   provider 变化不得复用不兼容流程证据。人工 gate 绑定需求 hash 和评估 revision。
5. 首次 integrity-v2 升级备份原始字节至 `.orchestrator/backups/integrity-v2/work/<id>.json`，
   不覆盖已有备份；备份失败不升级。Legacy active 需显式重锚/重新评估，completed 仅历史可读，
   不自动重新认证或重开。不得将“旧任务已关闭”解释为满足新标准。
6. 所有关联计划重复 `--plan` 绑定；共享 sprint 明确 `--plan-item '<path>#<story-id>'`。
   真无计划才声明审计 `--no-plan`；unresolved/missing 不能关闭，删除绑定不能隐式缩窄范围。
7. 非零原生运行必须在本次 complete 给出新的失败理由例外；入口例外不能代替失败例外。
   例外不豁免产物真实路径所有权或字节匹配。重新执行清除旧完成/失败例外。
8. 验证命令仍是受信任代码：默认每脚本 300 秒、各流 4000 字节尾、超时 124/error；
   Windows Job 失败不会降级裸执行。POSIX 管理自有进程组，不承诺阻止恶意脱离组的代码。
9. 快照提前剪枝；仓库内文件链接按词法路径哈希，目录链接不跟随，外部文件链接/读取失败
   不认证新鲜度。正常树哈希保持兼容；已有文件链接证据可能需要重验。
10. Shell 动态变量、嵌套/eval/不可解析命令仅可风险提醒，不构成 Shell 沙箱；
    Dashboard 的 ready/review、原生 completed 和步骤文字不等于 Work Item completed。

## 修改边界与后续

新增核心职责限定在 `state_store.py`、`completion_policy.py`、`path_guard.py`、
`verification_runner.py`、`windows_supervisor.py`、`verification_setup.py`；CLI 保留原有路由和 JSON 存储。
相关修改涉及 Host TS/Hook/fragment、Dashboard 投影/页面、正式回归、协议及开发/CI 元数据。
新增代码文件均小于 500 行，没有全面重写大型既有 CLI。

C6 外部验收保持未完成（任务 #29，阻塞任务 #32），不宣称已经达到全平台出口。
交付可以先保留此隔离分支和上述限制；真实宿主、浏览器及远端平台验收补齐后再讨论发布。

## 2026-10-13 light 与异机 Pi 会话修复

本轮基线为 `fix/cao-integrity-repair` 的 `8b144f6`，工作目录为当前源码 checkout。
环境为 Linux、Python 3.12.14、Node 24.19.0；复用已有 npm 开发依赖，无在线 Provider 安装。

已实现：

- light 用当前原生 SPEC／明确非空标准绑定和新鲜项目验证关闭，不强制 workflow、计划/no-plan 或独立 SDD 验收。绑定变化须显式 replace；风险升级、需求、阻塞和项目验证不豁免。
- 合法 `uv run --no-cache` 渲染入口、真实安装 Skill 发现与流程替换检查；select/execute/complete 例外入持久审计并注明 Agent 声明和操作类型。
- 非通过验证优先 stderr；verify 后根据当前完成状态给出下一步；active 重锚不误称完成后重开；退出命令不引导创建需求；缺检查时使用 verification configure，而非 init --force。
- standard/comprehensive 的独立验收声明、显式来源锚定、当前 run、锁外真实命令执行与 Skill begin/submit。精确报告排除、逐项覆盖、证据 hash、并发决策保存、输入变化 stale、失败替换旧成功、重开清 run 均接入完成判定。
- Dashboard、schema、README/DESIGN/SKILL 与宿主模板同步；说明 JUnit 5、用户可感知范围、语言、依赖安装和独立 reviewer 的指导边界。CAO 无法仅凭 JSON 证明模型遵循这些指导。

实际结果：

| 检查 | 结果 | 日志 |
|---|---|---|
| 全量 Python（静态复验） | 533 项通过，240.550 秒 | `/tmp/cao-release-python.log` |
| 迁移定向 | 178 项通过 | `/tmp/cao-migration-tests.log` |
| 最后兼容回归 | 50 项通过，60.071 秒 | `/tmp/cao-final-compatibility.log` |
| 最新原生入口与验收 | 54 项通过 | `/tmp/cao-final-native.log` |
| 指导及审计 | 30 项通过 | `/tmp/cao-final-guidance.log` |
| Dashboard 投影与实际页面 JS | 8 项通过 | `/tmp/cao-final-dashboard.log` |
| Pi 真实核心桥及模拟宿主契约 | 13 项通过，零跳过 | `/tmp/cao-pi-tests.log` |
| Pi TypeScript 严格类型检查 | 通过 | `npm run check:pi` |
| wheel 构建/临时安装/源码目录外测试 | 18 个组合通过 | `/tmp/cao-wheel-build.log`、`/tmp/cao-wheel-smoke.log` |
| editable 临时安装/源码目录外测试 | 18 个组合通过 | `/tmp/cao-editable-smoke.log` |
| compileall、Shell 语法、diff 空白 | 通过 | 实际运行命令 |

安装组合为 BMAD／OpenSpec／Spec Kit × Claude Code／Codex／Pi 模拟事件 × light／standard。
完整路径执行真实 fixture unittest 和验收包装器；Provider 原生入口与宿主事件仍为明确的本地夹具。
没有启动异机 Pi 或真实三类宿主，没有重新执行用户的登录应用，也没有声称浏览器/Windows/macOS 实测通过。
Dashboard 使用页面实际 JS 的 DOM 夹具，不是完整浏览器视觉验收。

首轮523项发现28个旧契约失败，已按新profile语义迁移；保留原计划、路径、失败执行、过期证据等负向检查。
一次并行编辑与测试重叠导致 CLI 读到短暂未写完的源码并报语法错误；当前源码编译正常，最终交付以停止源码修改后的全量复验为准。
Pi 的 missing-console 测试在将 PATH 隐藏后需显式设置绝对 `CAO_TEST_PYTHON`，正确解释器下13项通过；未为该环境问题修改桥实现。

全量通过后补充一个窄兼容修复：显式降为 light 后，技术标准重锚不读取可选的旧独立验收来源；相同场景已扩展到正式降级回归。最后50项兼容检查全部通过，覆盖独立验收、light、技术标准重锚与证据绑定，结果见 `/tmp/cao-final-compatibility.log`。最终 wheel 已重新构建并复验。

本轮在当前分支形成一个本地集成提交；未推送、合并或发布。

## BMAD oneshot 与三种 Provider 的 light 适配（2026-10-13）

用户授权 CAO 适配 BMAD 原生 oneshot，而非要求 BMAD 恢复被删除的 Tasks & Acceptance。
当前实现只在 BMAD/light、未显式提供 criteria selector 时识别独立且字面有效的
`route: oneshot`、原生状态及唯一 Intent/Implementation Notes 标题，并绑定非空 Intent。
状态保存 `spec_binding.mode=bmad_oneshot`、当前需求和文档字节摘要；不修改 SPEC、补造
标准文档或流程/计划记录。它证明原生实现意图结构有效及项目验证新鲜，不声明存在独立
验收清单或已语义覆盖全部约束。显式标准绑定优先，其他 Provider/profile 没有此 fallback。

OpenSpec 指导改为已安装 Agent `/opsx:propose` / `/opsx:apply`，保留 schema 对 tasks
与上游产物的要求；ff 是产物生成捷径，减产物需要显式配置/验证的 schema。
Spec Kit 区分已安装 bug 扩展的 assess/fix/test 与新功能 specify/plan/tasks/implement；
后者保留原生 plan.md/tasks.md，并按安装版本执行 converge。没有自动安装扩展。

入口发现支持 opsx Skill 名，指导区分 CLI setup/status 与 Agent 实现。已知错误
`bmad quick-fix`、`openspec apply`、`specify implement` 等及常见字面 runner 包装
不能默认作为原生执行入口；有理由的自定义集成仍遵循已有例外审计边界。
仅 BMAD Skill 被指导执行其专用 renderer，不把该约定强加给其他 Provider。
协议、Dashboard、宿主模板与 README/DESIGN/SKILL 同步，UI 区分原生 oneshot 意图。

正式回归：`tests/test_provider_light_adaptation.py`（11 项），覆盖不补造产物的
原生成功对照、错误/缺失/重复路由、空/重复 Intent、不同 Provider/profile、显式标准优先、
改动后重绑/重验、需求变化、真实测试失败、CLI/Agent 混淆与安装资产。
原计划完成门禁的本地夹具改用 Agent 调用名，继续保留原负向检查，未把不存在的 CLI
作为成功对照。Dashboard 用实际页面 JavaScript 的 DOM 夹具，并非完整浏览器视觉验收。

定向87项、最后路径指导/oneshot的22项、Pi桥13项及TypeScript检查均通过。
wheel/editable各21个安装组合通过，其中3个新增BMAD oneshot组合覆盖三类模拟宿主。
这些是checkout外的真实包和真实fixture unittest验证，Provider和Host仍为本地模拟，
没有声称在用户异机Pi或真实在线SDD上完成端到端测试。
首轮544项全量只有1项旧提示断言失败（OpenSpec指导漏Provider名称），已补回名称；
最终全量结果记录于后续完成条目。

最终固定源码全量：**544项通过，257.090秒，OK**，日志
`/tmp/cao-oneshot-final-full.log`。Pi的13项零跳过、TypeScript、wheel/editable各21个
安装组合、compileall、Shell语法、diff空白均通过。本轮形成本地提交，未推送、合并或发布。

## 2026-10-13：无 Work 业务写入门禁

用户授权在新项目中严格要求先创建 Work，保留 CAO 和 SDD 产物写入。
本节取代历史“所有业务写入仅提醒”和“验证准备先于 Work 创建”的契约。
证据为异机 `/tmp/demo1` 的 `2026-10-13T04-39-38-641Z_01a1338f-1990-77da-86a8-f5fe858b28b9.jsonl`：
L5 已加载项目模板，L17 Work 为空，L20 发出提醒，L21–42 仍成功写入 11 个业务/测试文件。

- 无活动 Work 的业务代码、配置、测试和验证骨架写入返回 deny，给出实际 CAO 命令和编号需求/start 路径。
- CAO CLI 自己更新派生状态不受阻；需求目录、四类原生 SDD 输出目录保持可写。直接手改派生状态仍禁止。
- 原生 patch 的各宿主形态、复制读取来源、重定向、字面 heredoc、原生元数据工具和已安装的辅助脚本保持通畅。
- 混合业务/原生写入、移动目的地、路径穿越、指向业务目录的软链接和 heredoc 中展开的写入均覆盖负向回归。
- 无 Work bootstrap 返回 select；创建 Work 不依赖验证配置或测试通过。有 Work 后再做验证准备，原生 oneshot 实现仍可进行。
- 非活动 Work 给出恢复指引；Stop/settle 可正常结束回复，桥接故障仍报告诊断并 fail-open，不恢复旧的全工具死锁。

最终全量 **560 项通过**，按文件名互斥分为四组（120/220/198/22），日志 `/tmp/cao-strict-shard-{0,1,2,3}.log`。
Pi **14 项零失败/跳过**、严格 TypeScript、Skill 校验、compileall、Shell 语法和 diff 检查通过。
最新 wheel 在 checkout 外通过 **21 个本地夹具组合**，包括三类 Provider/Host、light、standard 和 BMAD oneshot。
安装验证走 select → numbered requirement → start → configure/reanchor → native workflow → verify/done，覆盖无 Work deny 和原生产物 allow。
Provider、宿主事件及辅助脚本仍为本地夹具；没有声称重跑用户异机的真实模型会话或真实在线 SDD 安装。

## 2026-10-13：绑定 SPEC 的原生阶段同步

依据用户提供的异机 `/tmp/demo2` 完整 Pi 会话
`2026-10-13T05-41-41-527Z_01a133c7-e816-76fa-a2b6-a8b39dbfa60d.jsonl`：
L74 已成功绑定 `_bmad-output/implementation-artifacts/spec-001-login-system.md`，
L77–78 将状态改为 `in-progress`；L174 改为 `in-review`，但 L183 重锚后
`native_ref` 仍为 null。中文需求名与英文 SPEC 名无法通过原有文件名匹配建立关联，
同步器又未使用已存在的 `spec_binding`。该会话是 BMAD dispatch 路径，并非 oneshot。

- 当前 Work 的有效显式 SPEC 绑定优先于旧 native_ref 和文件名推断，兼容中文需求与英文文件名。
- SPEC 的可变原生状态可被读取，但不自动更新验收摘要；改动后的旧绑定仍由完成门禁要求复核、重绑和重验。
- 绑定、宿主写入后、下一次提示/工具前及 Dashboard 读取均修复活动 Work 的阶段；`in-progress` 推进实现，`in-review`/原生完成推进评审，关闭仍要求显式 `cao done`。
- 已进入验证、评审等后续检查点不被较早原生状态覆盖；已完成 Work 不重新认证。
- 缺失、跨 Provider、需求不匹配、越界或软链接到业务目录的绑定不猜测其他文件；重复/畸形状态不推进阶段。
- OpenSpec 嵌套 SPEC 从所属 change 的 tasks.md 读取，Spec Kit 从所属 feature 读取，避免跨任务状态串扰。
- Dashboard 显示原生同步来源与无法同步的原因；仅生成 tasks/story 产物不再代表已开始实现。
- 无 Work 业务写入门禁、CAO/SDD 产物写入许可、light/oneshot/full 完成门禁保持有效。

新增 `tests/test_native_sync_binding.py` 的 **15 项回归**及真实 Pi 桥的绑定状态转换检查。
固定源码全量 **575 项 Python 通过**，四组互斥文件分片为 120/235/198/22，日志
`/tmp/cao-sync-shard-{0,1,2,3}.log`；定向 **76 项通过**。
Pi **15 项零失败/跳过**、严格 TypeScript、Skill 校验、compileall、Shell 语法与 diff 检查通过。
最新 wheel 在 checkout 外通过 **21 个本地夹具安装场景**，日志 `/tmp/cao-sync-installed.log`。
原生 Provider 与宿主事件仍为本地夹具；没有声称重跑用户异机 Pi 模型会话或真实在线 SDD。

## 2026-10-13：BMAD 需求整体完成后才进入 verify

用户要求当前需求在 BMAD 共享 sprint 状态表中的全部执行项为 done 后才能进入 CAO verify。
原契约只在 full 路径的 done 检查关联计划，且只识别 `.yaml`，不能阻止提前运行验证脚本。

- `.yaml`/`.yml` 均支持；`cao sdd plan bind --plan-item <path>#<story-id-or-epic-N>` 在 Work 内保存明确关联，不修改原生文件或验收锚。
- 整个 epic 的选择动态包含新增 story，并保留绑定时的 story 身份；显式重复 workflow complete 或 plan bind 都不能将原需求范围缩成最后一个 story。
- 只检查当前需求的关联 story；其他需求、epic 汇总状态和 optional retrospective 不重复计入。story 本身必须恰好为 done，optional/未知状态不能绕过。
- verify 在配置/CLI 脚本执行之前检查；light、standard、comprehensive 均适用。没有 sprint tracker 的原生 oneshot/light 保持可用。
- 共享表存在但关联缺失、删除文件/已关联 story、重复或畸形状态、跨 Provider/越界/业务软链接来源均不放行；no-plan 不能跳过已存在的 sprint tracker。
- done 同样检查聚合状态；关联范围和选中状态参与验证新鲜度，执行期间的新增 story/状态回退使结果 stale。
- 原生阶段按整个关联范围聚合：全未启动仍为 ready；部分已开始/完成为 in_progress；全 done 才到 review。旧的单 SPEC review 游标在整体未完成时可修复，后续验证检查点仍保留并报告门禁。
- CAO/SDD 写入及直接开发测试没有新增拦截；两类宿主模板与 README/DESIGN 同步指导 Agent 建立完整需求关联。

新增 **15 项** `tests/test_bmad_verify_plan.py` 回归。全量 **590 项通过**，文件互斥四组为
136/157/147/150，日志 `/tmp/cao-bmad-plan-final-shard-{0,1,2,3}.log`。
全量之后补充单 SPEC review 游标的窄修复，最终相关 **72 项通过**，日志 `/tmp/cao-bmad-plan-final-compat.log`。
Pi **15 项零失败/跳过**、TypeScript、compileall、Shell 语法、diff 检查通过。
最新 wheel 在 checkout 外通过既有 **21 个安装夹具组合**，以及新增
**6 个 BMAD tracker 场景**（三种 host 配置 × 两种后缀），实际安装 CLI 验证部分完成拒绝、全部完成执行测试、新增 story 再拒绝。
日志 `/tmp/cao-bmad-plan-installed-final.log` 和 `/tmp/cao-bmad-plan-installed-sprint.log`。
Provider/宿主仍为本地夹具，没有声明真实异机模型或在线 BMAD 端到端实测。

第一轮分片测试从 stdin 启动导致 multiprocessing spawn 找不到 `<stdin>`，改为有 main guard 的文件入口；
原 BMAD 负向夹具假定 pending 计划仍能先 verify，已改为断言 verify 和 done 都拒绝。安装新增夹具最初缺少 `_bmad` marker，补齐本地模拟安装标记后六个场景通过。

## 2026-10-13：OpenSpec / Spec Kit 同类缺口核验与修复

用户要求查询权威资料，验证两种 Provider 是否同样可能在一个 Work 只完成部分原生执行项后进入 CAO verify。
已核对并克隆 upstream 原生源码：OpenSpec `9111a7654d7800391459431fff4eaf66e33a3d2e`，
Spec Kit `6c62e10d5ab1d676c557eb2d0cee86be52c893d2`。完整来源和边界见
[provider-plan-verification.md](provider-plan-verification.md)。

OpenSpec 按 change 及 schema 实际跟踪文件记录执行任务，Spec Kit 在 feature 的 tasks.md 中记录共享任务和多个 user story。
它们不是 BMAD 的项目级共享 sprint 表，但 CAO 原 verify 入口只调用 BMAD 门禁，因此也会提前验证；light 完成检查还可能绕过现有原生计划。
原生 OpenSpec 支持编号/plus/嵌套/未知单字符 checkbox 等格式，旧 CAO 解析器存在漏算。

- 引入 provider-wide verify/完成门禁：当前 Work 的全部关联任务文件全勾选后放行，既有 BMAD 行为保留。
- 从 Work-bound SPEC/native_ref/workflow/artifact 派生 change/feature 的默认 tasks.md，并持久保存关联；不读取项目全局当前 feature 来猜测，也不扫描无关需求。
- `cao sdd plan bind --plan <native-task-file>` 可重复添加所有拆分计划；OpenSpec 自定义 schema 显式绑定实际 tracked Markdown 文件或引号保护的 scope 内 glob，动态包含新增分片。
- 原生聚合状态优先于单个完成计划，绑定/宿主/CLI/Dashboard 边界修复游标。Spec Kit 质量 checklist 不作为实现完成证明。
- 保存已观察的原生任务 ID/最小任务数与任务文件；删除、重复 ID、缺失、空/不可识别来源或业务软链接均不能制造全部完成。
- scope/任务状态参与 freshness；执行途中新增关联或回退状态使验证结果 stale。CAO/SDD 写入和直接开发测试继续可用，无原生计划的 light 路径保留。
- Skill/宿主模板/README/DESIGN 同步。对自定义 schema 不声称自动解析任意 YAML 方言，实际跟踪输出由 Agent 按原生命令返回的结果显式登记。

新增 `tests/test_task_provider_verify.py` **17 项**，覆盖两种 Provider、所有 story/shared task、多个关联 feature/change、后续新增任务与删除文件/任务、普通/CLI 脚本、原生 marker 边界、自定义 glob、无关 checklist 以及聚合 Dashboard。
对照 upstream OpenSpec 实际正则在 Node 中执行，**3 组差分输入完全一致**，包括完整 Spec Kit 模板中的 **35 个任务**。

固定源码全量 **607 项通过**，按文件互斥四组为 131/165/147/164，日志 `/tmp/cao-task-provider-shard-{0,1,2,3}.log`。
定向 **70 项通过**，日志 `/tmp/cao-task-provider-final-target.log`。
Pi **15 项零失败/跳过**与 TypeScript 通过；Skill 校验、compileall、Shell 语法及 diff 检查通过。
最新 wheel 在 checkout 外通过 **36 个安装场景**：21 个既有组合、6 个 BMAD tracker 组合，以及
9 个新的 OpenSpec/Spec Kit 组合（两种 Provider × 三类模拟 host，另有三个 OpenSpec 自定义分片场景）。
实际已安装 CLI 执行 fixture unittest，验证 partial 拒绝、all complete 放行、拆分 scope/新增分片/删除文件再拒绝；日志 `/tmp/cao-task-provider-installed-{baseline,bmad,native}.log`。
没有声称运行真实在线 Provider Agent 或异机模型，Dashboard 检查仍是实际打包 JS/本地 DOM 夹具。

初始定向回归确认提前 verify/缺少通用 plan CLI 的失败；测试矩阵首次在同一临时 repo 重复 start 被既有活动 Work 门禁正确拒绝，改为在同一夹具中切换 Provider。
两个历史负向夹具假定 pending 的 OpenSpec/Spec Kit 计划仍能先 verify，现明确断言 verify 与 done 都拒绝，未删除原失败条件。

## 真实 oneshot 会话中的三次遗漏（2026-10-13）

分析用户提供的 `2026-10-13T08-07-34-908Z_01a1344d-78fb-71fc-9e3a-c0adf70bb67c.jsonl`：
该会话在业务实现前创建了 Work，属于 BMAD oneshot，不包含 sprint tracker；此前的 Work/状态同步路径已生效。
但已渲染的 `step-oneshot.md` 要求独立同模型 Review、Classify 和可用 Git 下的 Commit，日志没有对应执行记录，SPEC 却直接变为 done；
验证配置直到第一次 verify 被拒绝后才建立；手动 smoke 的新服务因端口占用退出，curl 仍访问了旧服务。
本节取代历史“active Work 可在无验证基线时直接实现”及“原生 oneshot 仅凭 Intent + tests 就完成”的契约。

- 增加独立的 `cao sdd review begin/submit`：绑定当前需求、Intent、代码及实际渲染指令摘要，记录每个启用层的独立 reviewer 身份/模型、原始 findings 和逐条 Classify 结果。
  缺失或失效时在 verify/done 拒绝，SPEC done、测试和手工 implementation_review 不能替代。代码/指令/证据变化要求重新评审，CAO 不向 SPEC 补造 Tasks & Acceptance。
- active Work 的业务写入要求配置并接受验证基线；不要求此时测试通过。配置变化明确指向 reopen/reanchor，bootstrap 与宿主上下文同步。
  CAO/SDD 产物、初始测试/验证包装、清单及环境准备保持可写；无 Work 和受保护状态的既有门禁保留。混合业务补丁和通向业务目录的脚手架软链接不豁免。
- Git 可用的 native oneshot 在 done 检查业务/SPEC 是否已经提交，状态/安装/评审文件的日常写入不会造成无休止提交。
  新增 `cao verification smoke`：前台启动独立服务，动态 loopback 端口，检查进程和期望 HTTP 状态，结束后清理拥有的进程树并确认不再返回 HTTP 服务。
  不接受固定端口或失败的新服务；启动/API 行为属于验收时，应显式登记该 smoke 为必需验证脚本。

新增 `tests/test_native_omissions.py` 15 项；旧路径/原生提醒测试明确建立真实基线后再验证原契约，业务无基线写入的旧成功断言改为拒绝，并增加脚手架成功对照。
定向 54 项与路径/原生提醒兼容回归 22 项通过。
新 wheel 的 checkout 外 CLI 额外实测了 configure --work、接受基线前后业务写入、原生/脚手架写入、缺 review 拒绝、记录后实际测试执行、Git 提交前后 done，以及成功/失败/固定端口的 owned smoke。必需 smoke 还实际登记到验证配置并由 `cao verify` 执行。
评审成功夹具执行独立本地 Python 进程，并明确标记为 fixture；没有声称运行真实在线同模型 reviewer。
报告身份/模型及完整性检查是结构和新鲜度校验，不能密码学证明实际模型执行或语义正确性。

最终全量 **622 项通过**，文件互斥四组为 147/153/171/151，日志 `/tmp/cao-omissions-checked-shard-{0,1,2,3}.log`。
Pi **15 项零失败/跳过**与 TypeScript 通过，日志 `/tmp/cao-omissions-checked-pi.log`、`/tmp/cao-omissions-final-ts.log`。
新 wheel 的 **21 个既有安装组合通过**，新增门禁/注册 smoke 实测也通过，日志 `/tmp/cao-omissions-final-installed.log`、`/tmp/cao-omissions-installed-gates.log`。
源码与已安装包的四个变更运行时模块 SHA-256 一致；Skill 校验、compileall、Shell 语法及 diff 检查通过。
临时分组运行器首次漏设源码导入路径导致加载失败，补正 sys.path 后重新执行完整四组；Pi 的 8 秒严格断言未放宽，先前并发负载导致的超时失败在最终检查通过。
这些结果是本机 Linux、实际安装 CLI、模拟 Host 与本地 Provider/独立进程夹具验证；未声称真实在线原生 reviewer 或 Windows 冒烟验收。
