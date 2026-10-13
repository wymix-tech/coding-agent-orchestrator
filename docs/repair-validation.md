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
