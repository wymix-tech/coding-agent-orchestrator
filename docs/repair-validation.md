# CAO 完整性修复验证记录

## 结论与边界

本机验证日期：2026-10-12。实施分支：`fix/cao-integrity-repair`，隔离目录：
`.worktrees/cao-integrity-repair`。A1—C5 已独立提交；C6 的文档、协议、CI 配置与本机安装验收
已实现，但真实宿主、完整浏览器和跨平台出口尚未完成，**不得视为全平台集成或发布验收通过**。
没有自动合并、推送、发布或在线安装 SDD。

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

以下为 C6 当前代码的新鲜结果；完整日志保存在本机 `/tmp/`，不是跨平台证据。

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

本机只找到 Pi 启动器，没有 Claude Code/Codex CLI。Pi 真实多轮加载适配器、提醒送入模型、
会话切换和退出尚未验证；仅声明经类型/模拟契约检查的 Pi API **1.1.0**，不声明整个 1.x。
Codex 无已验证原生 SessionEnd hook：status 明确标注限制，真实退出使用
`cao handoff --host codex --session <id> --end-session`，不能在每次 Stop 调用。

发布前仍需三类真实宿主分别执行：启动 → 需求录入 → 未评估产品写提醒 → 状态写拒绝 →
两轮仍 active → 切换/退出 ended → 原生流程输出 → verify/done。不得借用模拟事件结果签字。
真实 SDD 在线安装未执行，须另获授权并记录具体版本；没有把目录夹具当作已安装运行时。

## 兼容与操作步骤

1. 产品写入采用提醒模式；没有评估/有重大歧义也不由 Hook 阻断。不要误解为已经满足完成条件。
   `.orchestrator/` 派生状态禁止手改；需求 intake 可写；`cao done` 是硬完成权威。
2. required/tests 能力显式配置 `gates: ["tests"]`，或通过 `cao init --test-script` 声明。
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
`verification_runner.py`、`windows_supervisor.py`；CLI 保留原有路由和 JSON 存储。
相关修改涉及 Host TS/Hook/fragment、Dashboard 投影/页面、正式回归、协议及开发/CI 元数据。
新增代码文件均小于 500 行，没有全面重写大型既有 CLI。

C6 外部验收保持未完成（任务 #29，阻塞任务 #32），不宣称已经达到全平台出口。
交付可以先保留此隔离分支和上述限制；真实宿主、浏览器及远端平台验收补齐后再讨论发布。
