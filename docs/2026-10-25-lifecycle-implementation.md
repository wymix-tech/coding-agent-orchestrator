# CAO 完整生命周期修复实施与验收记录

依据：[统一方案提示词](2026-10-25-complete-lifecycle-repair-prompt.md)。基线为 `main@cd700566d348925c1ae6cc9779b72ad54834f702`，交付继续使用 PR #25 的 `fix/complete-lifecycle-integrity` 源分支。

## 实施结论与边界

项目初始化、原生契约与评审来源、技术验证、逐项任务验收及原生终结的共同门禁均已实施。初始化不再依赖虚构业务 Work；通过初始化不会填充业务验证或验收结果。运行时使用精确计划与当前输入绑定，而非扩大 src/scripts 白名单。

用户指定排除实际模型宿主 E2E、框架组合和多模块 workspace 等当前无法进行的实际组合场景。本记录将这些与 fixture/单元回归明确区分。A20、A29 排除；A16/A21/A22/B05/B11/B12 等保留共享逻辑回归，但不声称真实宿主或真实 SDD 安装 E2E 已通过。A26 覆盖 JS/TS，不扩张为 Kotlin/任意框架支持声明。九种内置规则是可审查草稿，不是九套完整框架生成器。

## 问题、根因与修复

| 问题 | 当前行为与根因处理 |
| --- | --- |
| 空项目需要先建 Work 才能准备骨架，测试又被正式评审挡住 | 新增 scope-owned setup plan/apply/check；已有 Work 使用独立 preparation probe。两者都不授予正式 gate。 |
| 硬编码项目名或重复问已确认身份 | 规则分别处理发布名、导入名、namespace/module/本地路径；每字段保存实际用户来源，未变字段复用证据。 |
| 未 ready 的合法骨架修复被禁止 | 仅放行已 apply 且当前有效计划内的确切文件；业务文件、运行中、ready 后和派生状态仍拒绝。 |
| 原生契约到实现后才发现不完整 | route 阶段解析当前 renderer 的完整实现、评审层和引用终结步骤，并绑定哈希；等价迁移须验证下游全部对应文件。 |
| 子会话按顺序错绑，任意报告被接受 | 新任务票据绑定 run/layer/digest；host 观察 child prompt 与原始输出哈希，attach 和报告接收重复校验；缺关联保持 blocked。 |
| 批量准备和逐层入口条件不同 | 复用单层准备校验，先构造完整候选再落盘；失败不遗留半套活动工单。模型来源取实际宿主观察。 |
| 输入清单漏资源/删除或 NO_VCS 基准不明 | 复用产品快照产生完整 manifest，持久化 Work-start baseline；Git 明确 rename/delete/untracked。旧基准缺失显式标识，清单篡改会阻断。 |
| 一次 security 脚本退出 0 广播全部标准通过 | 新声明要求逐项检查回执；标准执行器支持一次 shared_run 的不同真实断言结果、缺项 blocked。源目录分类不冒充功能条数。 |
| executor/报告/路径修复导致过度失效 | 标准语义与传输分离，acceptance-only executor 仅按明确声明缩减产品评审范围，仍绑定技术与验收；诊断列出变化及受影响层。 |
| 原生 SPEC 尚未终结却 done | BMAD dispatch 原生终结为独立义务；限定当前主 SPEC 的版本化状态元数据策略避免终结自身无限 stale，不宽泛忽略 Markdown 内容。 |

协议有效不等于任意用户脚本语义正确；结构化源项映射不等于自动证明需求完整；host hook 是流程控制而非操作系统沙箱。缺少真实来源的历史证据不被重新认证。

## 可复用的验证入口

- `python -m unittest discover -s tests -v`：全量标准库回归，真实工具链缺失时显式 skip。
- `python -m unittest tests.test_lifecycle_completion_matrix tests.test_lifecycle_repair_boundaries tests.test_review_task_provenance tests.test_project_setup_lifecycle -v`：恢复修改的拒绝与恢复边界。
- `python -m unittest tests.test_setup_toolchains -v`：在具备工具的环境实际执行各栈公共 CLI 的 plan/apply/check/ready、首个 Work 和 preparation probe；没有工具不能记 pass。
- `npm run check:pi`、`npm run test:pi`：Pi 类型检查及适配器测试。
- `python tools/release/build_release.py --check`、构建 wheel 后运行 `tests/installed_package_smoke.py --cao <installed-cao> --python <installed-python>`：发布裁剪、外部安装导入、三个模拟 host 的空项目初始化及三 provider 业务路径。

真实工具链使用任务内隔离的 JDK/Maven/Gradle/Go 及已有 Node/TypeScript，不做全局安装。下载工具包均核对官方发布校验值。JVM 依赖下载通过本机 HTTP 传输到官方 Maven Central，编译器、构建器、JUnit 和实际项目命令没有替换为 mock。Shell 显式计划实际执行 shell 断言，说明未知规则同样走通生命周期。

## 67 场景实施矩阵

“实现并回归”只描述表内行为及对应测试证据，不扩大为排除场景的真实 E2E。下表测试名省略 `tests/` 和 `.py`；精确测试 ID 及本次执行结果见同目录验证记录 JSON。

| 编号 | 状态 | 实施与验证边界 | 证据模块 |
| --- | --- | --- | --- |
| A01 | 实现并回归 | 产品文件与工具目录分离；空项目无 Work 准备 | test_project_context_acceptance, test_project_setup_lifecycle |
| A02 | 实现并回归 | rules 返回适用及缺失身份字段；draft 复用已确认值 | test_project_setup_lifecycle, test_lifecycle_completion_matrix |
| A03 | 实现并回归 | 生态身份、路径、保留字与发布名/导入名分别校验 | test_project_setup_lifecycle |
| A04 | 实现并回归 | 复用每字段原用户证据；变更值必须被新证据覆盖 | test_lifecycle_completion_matrix |
| A05 | 实现并回归 | 无 Work 的 exact plan/apply，成功后才 ready | test_project_setup_lifecycle |
| A06 | 实现并回归 | 计划外业务文件和派生 CAO 状态拒绝 | test_project_setup_lifecycle, test_no_work_guard |
| A07 | 实现并回归 | 相对路径、scope、符号链接与准备输入边界 | test_project_setup_lifecycle, test_path_boundaries |
| A08 | 实现并回归 | apply 预检冲突、保留用户文件、同内容幂等 | test_project_setup_lifecycle |
| A09 | 实现并回归 | 受控入口执行声明命令；不靠命令名放行 | test_verification_bindings, test_setup_toolchains |
| A10 | 实现并回归 | opaque wrapper 与解释器参数按声明执行 | test_verification_bindings, test_verification_setup |
| A11 | 实现并回归 | 测试非通用只读；混合产品写入仍受门禁 | test_lifecycle_repair_boundaries, test_route_inputs |
| A12 | 实现并回归 | 缺工具/依赖 blocked，断言失败 failed，超时清理，零测试不 ready | test_lifecycle_completion_matrix, test_verification_runner, test_test_discovery |
| A13 | 实现并回归 | 未 ready 且已 apply 的确切计划文件可修复后重查 | test_project_setup_lifecycle, test_lifecycle_completion_matrix |
| A14 | 实现并回归 | 运行前后输入绑定，后读失败落 stale 并释放 owner | test_lifecycle_completion_matrix, test_verification_bindings |
| A15 | 实现并回归 | 有界输出声明；已跟踪输入和检查器不被输出吞掉 | test_snapshot_scope, test_lifecycle_completion_matrix, test_review_convergence |
| A16 | 实现并回归；宿主用 fixture | 锁、精确死 owner 恢复、provider 重启先决条件 | test_lifecycle_completion_matrix, test_provider_restart, test_state_transactions |
| A17 | 实现并回归 | 真实公共 CLI ready→首个 Work→准备 probe；不授予最终验证 | test_project_setup_lifecycle, test_setup_toolchains, installed_package_smoke |
| A18 | 实现并回归 | 已有 Work 准备试跑不改变 verification/gates/execution | test_lifecycle_repair_boundaries |
| A19 | 实现并回归 | 已有产品复用；历史 Work 不批量重写或倒退 | test_project_context_acceptance, test_repair_r1_r9 |
| A20 | 本次排除 | 多模块新增模块/workspace 的实际组合验证未开展 | 用户指定排除 |
| A21 | 实现并回归；provider 用 fixture | 三 provider、不同 profile 保留各自原生义务 | test_provider_light_adaptation, test_review_round_integrity, installed_package_smoke |
| A22 | 实现并回归；host 用模拟事件 | Pi/Claude Code/Codex 事件形状与受控写入一致 | test_host_adapters, test_host_lifecycle_contract, test_pi_adapter.mjs, installed_package_smoke |
| A23 | 实现并回归 | 背景继承、标准变化、真实审批与 reanchor 分离 | test_acceptance_integrity, test_project_context_acceptance, test_review_followup |
| A24 | 实现并安装包验证 | 144 文件运行时清单、wheel 外部 CLI、空项目与业务 fixture | test_release_packaging, installed_package_smoke |
| A25 | 实现并真实工具链验证 | Java/Maven 与 Java/Gradle 各自编译、测试及首个 Work | test_setup_toolchains |
| A26 | 实现并真实工具链验证；更广组合排除 | JS/TS 共用 npm，TS 单独类型编译；不宣称 Kotlin/framework 组合验证 | test_project_setup_lifecycle, test_setup_toolchains |
| A27 | 实现并规则回归 | Python、npm scope、Go module、Rust crate 使用独立身份约束 | test_project_setup_lifecycle |
| A28 | 实现并真实执行 | Python/JS/Shell 无强制编译，库入口不强制服务启动 | test_project_setup_lifecycle, test_setup_toolchains |
| A29 | 本次排除 | 多语言 monorepo/workspace 实际组合验证未开展 | 用户指定排除 |
| A30 | 实现并真实执行 | 无内置 Shell 规则，显式计划走同一公共 CLI 至 ready/首个 Work | test_setup_toolchains |
| A31 | 实现并回归 | 文件系统大小写冲突、保留路径与适配器版本失效 | test_lifecycle_completion_matrix, test_project_setup_lifecycle |
| A32 | 实现并回归；框架组合排除 | 显式计划遵循用户布局；已接受 runner 冲突不静默替换 | test_project_setup_lifecycle, test_project_context_acceptance |
| B01 | 实现并回归 | 原三 session 后缀循环错绑逐个拒绝，正确映射接受 | test_review_task_provenance |
| B02 | 实现并回归 | 第二轮拒绝旧 ticket；反序遍历与全错层组合 | test_review_task_provenance |
| B03 | 实现并回归 | layer/run/digest/session/model 绑定，错证据不覆盖 | test_review_task_provenance, test_review_round_integrity |
| B04 | 实现并回归 | 接收报告再次核对实际 reviewer 原始输出哈希 | test_review_task_provenance |
| B05 | 实现并回归；实际 host E2E 排除 | 协调器 attach；未观测模型/子任务不猜测 | test_review_convergence |
| B06 | 实现并回归 | 共享 begin/prepare 校验，完整候选通过后才保存批次 | test_review_convergence, test_protocol_consistency |
| B07 | 实现并回归 | 执行前解析当前完整 renderer 的实现/评审/引用 step05 | test_lifecycle_completion_matrix, test_local_experiment_repairs |
| B08 | 实现并回归 | 字节等价迁移保留 route/批准/handoff；旧 template 严格逆映射 | test_lifecycle_repair_boundaries |
| B09 | 实现并回归 | 下游义务或意图变化拒绝等价迁移，正常 loopback | test_lifecycle_repair_boundaries, test_review_round_integrity |
| B10 | 实现并回归 | 请求先绑定；旧问题、旧 Work、替换版本不能批准新意图 | test_review_followup, test_pi_log_contract |
| B11 | 实现并回归；实际 host E2E 排除 | 按原生批准/暂停/恢复契约，保留当前任务 | test_pi_session_repairs, test_intake_route_recovery |
| B12 | 实现并回归；实际 host E2E 排除 | 真实任务关联持久化；等价路径迁移不抹掉 handoff | test_local_experiment_repairs, test_handoffs, test_lifecycle_repair_boundaries |
| B13 | 实现并回归 | renderer-only 与缺失 handoff 不冒充实现；例外单列 | test_workflow_execution_integrity, test_local_experiment_repairs |
| B14 | 实现并回归 | Git rename/delete/untracked 与 NO_VCS Work-start 完整清单 | test_lifecycle_completion_matrix |
| B15 | 实现并回归 | 原文、哈希、task/layer 保留；triage 另存 | test_review_task_provenance, test_review_convergence |
| B16 | 实现并回归 | 精确 resolve 草稿、空结果与旧轮次/IDs 拒绝 | test_protocol_consistency, test_review_round_integrity |
| B17 | 实现并回归 | 目录分类与所需标准分离，保留负向约束和位置 | test_lifecycle_completion_matrix, test_review_convergence |
| B18 | 实现并回归 | 新声明要求逐项 request-bound receipt，裸广播 pass 拒绝 | test_acceptance_protocol_template |
| B19 | 实现并回归 | shared_run 同 argv/timeout 一次执行，独立断言结果 | test_acceptance_protocol_template |
| B20 | 实现并回归 | 缺映射、缺结果、全跳过、旧 run 不 pass | test_acceptance_protocol_template, test_sdd_acceptance_runtime, test_test_discovery |
| B21 | 实现并回归 | 同次真实运行一项 pass 一项 fail，总结果失败 | test_acceptance_protocol_template |
| B22 | 实现并回归 | stdout 协议错误精确字段；stderr 单独记录 | test_acceptance_protocol_template, test_sdd_acceptance_cli |
| B23 | 实现并回归 | stdout-only 和注册输出实际写入/归属校验 | test_sdd_acceptance_files, test_sdd_acceptance_cli |
| B24 | 实现并回归 | 规范哈希与传输分离；acceptance-only executor 仍是技术输入 | test_lifecycle_completion_matrix, test_review_convergence |
| B25 | 实现并回归 | 代码/标准/测试/意图变化按依赖失效并展示 changed inputs | test_project_context_acceptance, test_review_round_integrity, test_snapshot_scope |
| B26 | 实现并回归 | reanchor 不补批准；commit/重启不自行授予或清除证据 | test_acceptance_integrity, test_native_sync_binding, test_native_omissions |
| B27 | 实现并回归 | 阻塞时合法控制入口可达，未授权产品写入仍拒绝 | test_review_convergence, test_route_inputs, test_no_work_guard |
| B28 | 实现并回归 | 单条/复合/循环/重定向解析不产生写入豁免 | test_pi_log_contract, test_route_inputs, test_repair_r1_r9 |
| B29 | 实现并回归 | 当前协议错误优先，生命周期阻塞独立显示 | test_acceptance_protocol_template, test_review_convergence |
| B30 | 实现并回归 | BMAD dispatch in-review 阻止 done，明确原生终结 | test_lifecycle_repair_boundaries, test_lifecycle_completion_matrix |
| B31 | 实现并回归 | 限定版本主 SPEC 状态元数据避免自我 stale；实质变化仍失效 | test_lifecycle_completion_matrix, test_state_transactions |
| B32 | 实现并回归；原生 provider 用 fixture | Git/NO_VCS 与 provider 自己的 finalize/commit/archive | test_native_omissions, test_openspec_lifecycle_repairs, installed_package_smoke |
| B33 | 实现并回归 | 原生审查满足匹配 gate，独立安全义务保留差额 | test_conformance_knowledge, test_provider_light_adaptation |
| B34 | 实现并回归 | 历史完成记录只读诊断，不回填伪造来源 | test_audit_recovery, test_repair_r1_r9 |
| B35 | 实现并回归 | 摘要区分层/轮/任务、源项类别、例外、原生完成 | test_lifecycle_completion_matrix, test_review_round_integrity |

## 恢复与持久化说明

此前工作区回滚使本地 `fc1c5b5` 及其报告无法作为交付凭据；没有沿用此前“996 项”的结论。可恢复代码先保存为 PR 草稿，再通过 `1804847`、`2a7eeaf` 和 `8ebc6e7` 分阶段重做并推送。第二次执行环境恢复又回到旧本地快照，因此从远端 `2a7eeaf` 建立独立 worktree，保留旧目录，重新补最后修改与验证。

历史恢复缺口列表已经由上述新实现替代。本记录采用本次重新执行的日志与结果；阶段数不相加伪装为一次全量通过，跳过项目只在真实补跑成功后才记通过。PR 未合并。

## 本次最终执行结果

待验证进程结束后填写；尚未结束的运行不能记为通过。
