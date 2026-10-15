# OpenSpec / Pi 生命周期修复与复验

基线：main `2bd5416652317a092b35882081fcf223bf6ae921`。
修复分支：`fix/openspec-pi-lifecycle`。

## 修复范围

1. OpenSpec 原生 change 移入唯一归档目录时，对当前已绑定文件逐一核验哈希，迁移规格、任务范围、产物和工作流关联并记录生命周期事件。保留任务 ID、数量下限及拆分 change 的关联；不忽略归档里的未完成任务。进行中的验收声明迁移后需要显式重锚定和新验证。已完成的验收按冻结历史展示，不重新认证当前仓库。
2. 无 active Work 时允许 OpenSpec 帮助、元数据查询、原生归档及只读组合；支持安全的 sort、true/false、字面量 npm 前缀、pipefail 和真实 null 设备输出。任意产品重定向、混合写入、危险 sort 参数、其他 store 和解释器环境覆盖仍被拒绝。
3. 可用 OpenSpec CLI 直接用于项目初始化，不强制再次全局 npm 安装。显式 `--upgrade` 可请求升级机器级 CLI。
4. 保存结构化 execution.phase，自定义中文 checkpoint 的 step、next_action、summary 不再因英文关键字匹配而被覆盖。Dashboard 同步使用该阶段。
5. 增加 `cao sdd acceptance protocol`，提供完整请求/报告 Schema、示例和可独立复制的执行器。明确环境变量是 JSON 字符串、criteria 位于 declaration 内、evidence 是非空数组以及输出文件必须实际生成。缺失检查返回 blocked，报告错误返回字段路径诊断。

## 自动化验证

- Python：最终 701 项回归全部通过，按 4 个分片运行，覆盖新增的 18 项用例。
- Pi：TypeScript 检查与 18 项适配器测试通过。
- 安装包：构建 wheel，隔离虚拟环境安装并通过原有 21 个宿主/provider 场景；安装后的 protocol 命令和执行器文件可用。
- 安装复现：使用真实 OpenSpec 1.14.2，在故意不可用的 npm 全局前缀下初始化新项目成功，results 只有 `openspec init --tools pi`，无机器级安装。
- 负向测试：重复归档、内容篡改、符号链接、拆分 change 的未完成任务、源码变更、错误报告和缺失输出均不能获得完成资格。

首轮完整回归为 698 项通过；补齐 reopen/拆分计划等用例后执行最终 701 项分片回归。临时分片 runner 的 spawn 入口曾缺少 main 保护，导致进程锁测试未启动目标进程；修正测试 runner 后，对应 175 项分片及锁测试独立重跑通过，未修改 state_store。

## 真实 Pi 会话复验

运行环境：Pi 1.1.0，固定 `gpt-5.6-luna`、medium thinking，OpenSpec 1.14.2。
使用持久 RPC 会话和本地 package 自动发现，无显式 `-e`，无 `--print`。

会话 ID：`01a13e96-0e23-778c-8f8e-d26dfcf5e2c9`。
样例 Work ID：`001-requirement---json---cli-2026-10-15-4d33f77e`。

实际执行结果：

| 检查 | 结果 |
| --- | --- |
| 原生 `openspec archive ... --yes --json` 先于 CAO 关闭 | 成功，主规格同步、8/8 任务归档 |
| 归档后尝试直接关闭 | 要求显式 reanchor；没有 PLAN_SOURCE_MISSING |
| 原任务 ID、范围和绑定 | 保留并迁移到唯一归档目录 |
| 重锚定后验证 | 4/4 项目测试通过，6/6 命令型 SDD 验收通过 |
| 中文 step + next + summary 后 sdd sync/status | 中文文本和 verification phase 保持 |
| CAO 关闭 | completed，active_work=null |
| 关闭后的 archive --help 与只读组合 | 正常执行 |
| 已完成历史验收查询 | passed，historical=true |
| 上轮已完成且已归档的独立历史副本 | 无需重开，原验收仍作为历史通过结果可读 |
| 已归档完成项显式 reopen 后再次验证、关闭 | 成功，重新执行检查，没有沿用完成资格 |

最后一轮真实验证：

- verification：`verify-413a93abf45c4efda8bf1880daf15d21`。
- SDD acceptance：`sdda-e09258d700f042c28475d27be56e0642`。
- 实际中文 step：`实现、验证与独立验收完成`；next：`关闭当前需求`；summary：`全部检查通过`。

此次复验从上一轮样例快照继续，重点重跑修复涉及的生命周期和门禁，并非再从空项目完整实现一次业务需求。原生归档文件中的旧路径文本和冻结历史保留原貌；CAO 只迁移当前关联，不改写 provider 原始文档。命令型验收不等于独立 AI reviewer。
