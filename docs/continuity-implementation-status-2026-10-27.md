# CAO 连续性方案实施与验收记录

日期：2026-10-27。分支：`fix/task-continuity-evidence`。原始基线：`main@223e353`。

本记录取代第一批 checkpoint 的进度说明。功能实现、模拟/本地验证、真实模型会话分别记录，不把代码存在或测试夹具通过等同生产可靠性证明。

## 实施范围

| 阶段 | 实施结果 |
| --- | --- |
| P0 | 保留生命周期契约 2；新增 HANDOFF、过程证据、操作日志协议与回归；契约 1 保留旧执行语义 |
| P1 | 结构化协调事项、legacy pending 迁移/关闭、当前原生单元及必读文件、HANDOFF v2、接收记录、严格只读预览；恢复上下文与 dashboard 共用有界视图 |
| P2 | 增量原始产物登记；Work 绑定不可变元数据摘要；原始字节归档；生产者 Session/受观察工具调用/运行来源与声明的 attempt；挂起任务可显式保留历史附件；分页查询；最终证据选择及失败优先检查 |
| P3 | 统一恢复检查、当前输入/原生范围/依赖域差异；完整受控验证回执 finalize；协作式取消；契约 2 未决执行阻止新尝试；显式 inspection/abandon；相同冲突重试暂停 |
| P4 | todo 入队；列表关联；显式 amend 预览/版本检查/原字节保留；持久化跨 Work 切换、恢复/撤销、写屏障、旧 Session 隔离、迟到结果原任务归属及工作区漂移检查 |
| P5 | Pi 压缩/会话切换/分叉事件、现有三 Host 的手动兜底、渐进式指南、安装资源检查、长历史探测和本地安装矩阵；真实模型会话仍受环境阻塞 |
| P6 | 方案明确定义的可选后续 managed worktree，不包含在本轮 P0–P5 首期实现范围内 |

## 关键行为

- `resume --check`、`handoff --check`、`switch --check` 不修复 Work/index/guard/outbox 或改变原生文件。
- 普通恢复/显式切换可以完成此前已授权但中断的 guard 写入；未授权内容仍由既有 lifecycle recover 隔离恢复。
- 未完成操作阻止新业务写入。所有 before/after 状态、输入和 intent 指纹吻合才继续写缺失步骤；冲突不覆盖。尚未应用的 prepared 操作可显式 abort。
- 长任务进展仍来自 SDD。CAO 协调事项不能标记原生任务完成；legacy pending 已关闭后不会重新进入新 HANDOFF 的有效清单。
- 报告字节、声明来源、执行回执和正式完成资格分开；无 shell 子代理由主 Agent 代登记真实生产者；缺乏执行回执时明确保留声明/观察等级。旧 generation 回执可以保留历史，不能作为当前完成证据。
- 原始报告覆盖/丢失时，可查看保留的原始字节；元数据或正文损坏可见。报告正文不是源码副本，也不替代原生任务源。
- `lifecycle bind --report` 和 `--evidence` 均不能绕过已登记的当前未解决失败；完成绑定保存选择的证据 ID。每次读取完成资格也重新检查这些记录。
- 项目基线仍独立；SDD 内部评审仍属于 implementation。没有新增第二次功能评审/功能 executor。
- 默认共享工作区串行执行；返回旧任务时展示产品、需求、范围、依赖、provider Skill、报告和基线变化，不自动 stash/reset/覆盖文件。
- 契约 2 的未知运行不能直接被下一次 verify 覆盖。取消请求只由持有原 subprocess 的执行器处理，不凭可复用 PID 杀未知进程。显式 abandon/settle 是检查声明，不冒充进程终止或功能通过。

## 命令与运行时文档

完整操作说明随安装包发布于 [Task continuity](../references/task-continuity.md)。新增/扩展入口包括：

```bash
cao resume --work <id> --check
cao checkpoint --item "<coordination item>"
cao checkpoint --resolve <item-id>
cao checkpoint --unit <native-unit-id> --read <required-file>
cao handoff --check --work <id>
cao handoff accept --work <id> --id <handoff-id> --host <host> --session <actual-session>
cao evidence attach --kind test --path <original-report>
cao evidence list --limit 50 --cursor 0
cao evidence show --id <evidence-id>
cao lifecycle bind --evidence <evidence-id>
cao start --defer --requirement <request-file>
cao switch --to <id> --check
cao switch --to <id> --reason "<user choice>"
cao switch recover --operation <operation-id>
cao switch abort --operation <operation-id> --check
cao amend --work <id> --requirement <candidate> --request "<user request>" --check
cao runtime finalize --work <id> --run <run-id>
cao runtime cancel --work <id> --run <run-id> --reason "<reason>"
cao runtime abandon --work <id> --run <run-id> --evidence <inspection-file> --reason "<basis>"
```

## 自动化验证

- 全量按模块发现/执行：92 个模块，1124 项测试，全部模块退出成功；跳过 4 项；原始模块结果见 [validation JSON](continuity-validation-2026-10-27.json)。
- 全量之后补充的生产者关联、挂起任务历史证据等用例，连同相关模块定向复跑 47 项通过；最终归档导出/禁止覆盖检查所在的 25 项连续性用例也通过。当前发现 1126 项，不把定向重跑重复计入全量测试数。
- Pi adapter 与事件桥：38 项通过，TypeScript 类型检查通过。新增压缩/会话转移事件验证属于模拟 Host 事件，不是模型对话。
- `make check`、`git diff --check` 通过。
- 独立 wheel 构建及虚拟环境安装通过；离开源码目录的完整安装矩阵 **24/24 通过**，新模块/协议/引用均可用。矩阵使用真实本地 CLI 与本地 provider/Host fixtures。
- 原始完整回归曾发现 legacy 并发执行语义冲突，已限定契约 2 的新未决运行约束，保留方案要求的契约 1 兼容行为；相关原有 16 项和新增恢复用例复测通过。

新增故障与状态覆盖集中在 `tests/test_continuity_operations.py`，包括：

1. preview 零文件内容变化、todo 不动当前任务；A→B→A 与同目标幂等。
2. 切换 journal 的三次写入、Work/index 的六次写入，每个位置的写入前/后故障注入，共 18 个子场景；源/目标均采用契约 2 时也能恢复 prepared guard。
3. 半完成操作不允许无关写入；有新决策时恢复冲突不能覆盖；重复冲突暂停。
4. prepared switch 的只读 abort 预检与撤销；未完成事务期间迟到结果仍记录在原 Work。
5. 原始报告覆盖后的字节恢复、归档损坏、后续失败不能被旧成功隐藏。
6. 受控完整回执恢复、不接受伪造结果投影、不重放未知执行、协作式取消不启动已取消命令。
7. HANDOFF 接收的 Work/Session 校验、旧 Session 不可写新任务、legacy pending 关闭不复活。

## C01–C45 覆盖索引

此表是实现与本地自动化的可追溯索引，不将所有组合宣称为真实模型端到端通过。

| 场景 | 实现/验证位置 |
| --- | --- |
| C01–C03 | `continuity_view`、`coordination_items`、`cao_sessions`；continuity/Session/HANDOFF tests；Pi compaction bridge test |
| C04 | `native_units` 的稳定任务 ID、文本指纹和歧义拒绝；既有原生 scope/source 约束 |
| C05–C09 | 既有 native lifecycle、plan scope、BMAD compatibility、OpenSpec/Spec Kit 单元聚合与归档测试；新增 source-qualified unit 定位 |
| C10–C14 | implementation evidence/archive；continuity operations 与 native lifecycle 的增量绑定及后续失败反例 |
| C15–C18 | Handoff v1/v2、接收记录、只读预览、Session 测试；项目准备上下文仍保持最小暴露 |
| C19–C23 | runtime recovery/finalize、verification runner、契约 2 未决运行与 generation 测试 |
| C24 | producer Session/parent 引用与协调者代登记；原有 reviewer provenance/无 shell 交接流程 |
| C25–C27 | recovery plan 与现有 evidence domains/lifecycle guards；输入变化、历史冻结及回滚测试 |
| C28–C32 | deferred Work、显式 amend 与 revision 检查；continuity operations 与需求变更回归 |
| C33–C39 | work operations、host continuity、runtime effect resolution；恢复/并发约束、18 子场景故障矩阵、旧 Session 和迟到结果用例 |
| C40–C42 | 现有 runtime retention/store/snapshot 回归与新 evidence archive；本轮合成长历史探测。非 Git 不宣称可恢复文件树 |
| C43–C44 | 现有三 provider 生命周期回归、三 Host 安装/模拟事件、wheel 和 release archive 检查；安装矩阵仍是 fixtures |
| C45 | **未通过真实模型会话验收：环境阻塞，见下节** |

## 长历史探测

可复现脚本：`python tests/continuity_benchmark.py`。原始结果：[continuity-scaling-2026-10-27.json](continuity-scaling-2026-10-27.json)。

使用 100/1000 个实际 OpenSpec fixture 任务、100/1000 份交接、1000/10000 条观察，测量有界摘要、原生任务解析、最新交接读取及事件分页。每项 5 次；报告 p50、样本最大值作为本小样本 p95 估计、峰值内存和输出字节。结果用于本机规模对照，不承诺跨机器固定时延；历史读取仍会扫描相关记录，未宣称常数时间索引性能。

## 明确保留的验收限制

本轮实际调用已安装 Pi 1.1.0 CLI 的非交互模型探测，返回：

```text
No API key found for the selected model.
```

因此无法在本环境完成 C45 的真实 Pi + 实际模型 + 官方 provider 对话，也没有把 fixtures 当成它的替代验收。具备模型登录的环境仍需执行跨会话、强制中断、A→B→A、原生评审/完成报告交接，并保存真实主/子会话记录。

P6 managed worktree 是方案单列的后续可选阶段，本轮保持单工作区、单活跃 Work。当前引擎的 guard/操作屏障也不能物理阻止旧版进程或外部程序写文件；使用新协议前必须停止不支持它的旧 writer。未知外部副作用需要实际检查，CAO 不将一份检查声明包装为独立执行证明。
