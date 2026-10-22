# Runtime 记录实施与验证

日期：2026-10-22（Asia/Shanghai）。实施分支：`fix/work-runtime-records`。

从设计基线 `main @ a0f25e6` 更新到 `main @ 2593b91` 后继续实施；最新基线的
PI 上下文摘要展示保持不变。本次没有改动需求身份、SDD 路由审批、原生任务范围、
验收标准或完成门禁。以下“通过”仅指列明的自动化/本地验证，不等同于真实模型会话验收。

## 已实现

| 计划阶段 | 交付 |
| --- | --- |
| P0 合同 | runtime-event / runtime-run v1 schema；使用原始 Work ID；Work 与执行事实的权威边界 |
| P1 历史 | checkpoint、decision、block/unblock、handoff、workflow 事件复用 Work outbox；记录提交 revision；按任务查询生命周期历史 |
| P2 执行与日志 | 复用 verify run ID、generation、sequence；start / command receipt / run receipt / result 投影；本机 owner OS lock；两路日志脱敏、轮转、配额、尾部与故障降级 |
| P3 SDD | command / Skill 验收复用原运行 ID；原始有界 stdout JSON 通道独立于脱敏日志；workflow execute 标记声明来源；原生状态事件补充任务范围/观测来源；证据引用索引 |
| P4 宿主与恢复 | host + session + tool-call ID 关联；跨任务结果回到启动时任务；缺少身份/父会话时保留 partial / unassigned；显式恢复检查与诊断记录；不自动重跑 |
| P5 查询与保留 | status / events / runs / show / logs / recover / rebuild / prune；游标分页；日志有界读取；只清理超期且未受保护的日志正文；安装包与使用文档 |

`runtime_store.py` / `runtime_logs.py` / `runtime_execution.py` 分别负责事实、日志、
执行集成；`runtime_host.py` 负责宿主观测；`runtime_queries.py` /
`runtime_retention.py` / `runtime_commands.py` 负责查询、清理和 CLI。
`cao_runtime.py` 保持原有通用工具职责。

## 一致性和失败处理

- 关键启动记录与 Work 的 running 提交完成之前，不启动子进程；执行发生在仓库锁外。
- stdout/stderr 采集故障不停止管道排空、不改变执行退出码；日志元数据报告故障。
- 命令结束的 receipt 与正式业务结果分开。业务提交失败时不宣告验证成功。
- result / events / evidence 索引是可重建投影；不能用于授予通过。
- start 孤立、owner 消失、receipt 未提交、已被新 generation 替代、投影缺失分别显示。
- 已完成 Work 不接受旧执行回写；查询历史 admission 与当前输入新鲜度是不同判断。
- 日志正文配额采用先预留后采集；观测配额先预留再写事实，避免崩溃后少计容量。
- 查询长历史和日志时释放仓库写锁；rebuild / prune 仍在锁内处理其互斥边界。
- `verify` / `done` 的前置拒绝和宿主拒绝保存诊断；无任务记录可用
  `cao runtime events --work _unassigned` 查看，不创建虚拟 Work。

## 自动化验证

最终结果：

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 828 项通过，430.571 s |
| PI 接入测试 | 36 项通过；包含真实本地处理器至 CLI 的新增关联场景 |
| PI TypeScript 类型检查 | 通过 |
| 独立 venv / 仓库外 wheel 冒烟 | 21 个场景全部完成 |
| npm 包内容预览 | 103 个文件；七个 runtime 模块、使用合同及两个 schema 均在包内 |
| 补丁空白检查 | `git diff --check` 通过 |

相关命令：

```sh
python -m unittest discover -s tests -v
npm run check:pi
npm run test:pi
python tests/runtime_benchmark.py
```

新增 runtime 测试覆盖安全路径、符号链接、幂等冲突、原子写失败、并发进程、观测配额、
历史事件、快照隔离、跨 chunk 中文和凭据、带空格凭据、超长无换行输出、日志写失败、
启动失败禁止执行、协议隔离、真实退出 owner 锁、未提交结果、损坏投影重建、清理保护、
跨任务调用、拒绝与恢复检查的记录。

SDD 测试补充“脱敏配置不改变原始验收协议”与“Skill 不伪造进程回执”。
PI 测试补充真实扩展处理器到 Python CLI 的跨任务关联，并检查原始工具结果和完整模型
上下文保持。该测试使用真实本地处理器与 CLI，不调用模型。

安装包在独立 venv、仓库外目录验证；BMAD / OpenSpec / Spec Kit 与三个宿主的
本地产物及模拟 hooks 共 21 个场景，包括 light、standard、BMAD oneshot。
这些场景验证资源打包和 CLI 合同，不宣称安装或实际执行了外部 Provider 产品。

## 本地性能测量

Linux / Python 3.12.14，日志和查询测量开启 `tracemalloc`；运行时存在其他测试负载。
每条流均输出指定容量，所有输出均排空。单次测量用于容量/内存与回归诊断，不用于
推导所有机器上的固定耗时或严格吞吐 SLA。

| 每路输出 | off 耗时 | bounded 耗时 | bounded 留存总量 | bounded Python 追踪峰值 |
| --- | --- | --- | --- | --- |
| 1 MiB | 0.179 s | 0.246 s | 2 MiB | 103 KiB |
| 10 MiB | 2.109 s | 1.886 s | 16 MiB | 107 KiB |
| 100 MiB | 15.191 s | 15.363 s | 16 MiB | 99 KiB |

六个独立采集 worker 的进程 RSS 峰值均约 15.1 MiB。输出从每路 1 MiB 增加到
100 MiB 后，采集内存未随总输出线性增长；100 MiB 场景到达文件上限后仍正常完成。

| 并发观测线程 | 操作数 | p50 | p95 |
| --- | --- | --- | --- |
| 1 | 64 | 2.25 ms | 3.03 ms |
| 4 | 64 | 10.12 ms | 11.49 ms |
| 16 | 64 | 38.95 ms | 40.62 ms |

另有 16 次真实子进程写入、最大 4 并发的持久化测试。上述线程测量不是跨机器测试。

| 观测记录数 | 查询最新 50 条 | Python 追踪峰值 |
| --- | --- | --- |
| 100 | 15.0 ms | 47 KiB |
| 1,000 | 104.6 ms | 52 KiB |
| 10,000 | 896.0 ms | 52 KiB |

查询仍扫描源记录，耗时随记录数量增长；分页结果内存有界。首次调优前万条查询约
5.70 s、追踪峰值约 1.66 MiB；减少重复路径检查、按事实实际大小读取、流式遍历后得到
上表结果。只读扫描不再持有仓库写锁。

25 次真实 CLI pre-tool 调用：关闭观测 p95 100.05 ms，默认观测 p95 109.03 ms，
增量约 8.98 ms，低于设计中暂定的本地 20 ms 增量预算。该结果包含 Python 启动和
原有门禁开销，不把总 hook 时间误写成记录模块耗时。

可重跑脚本：`tests/runtime_benchmark.py`；原始数据：
[runtime-records-benchmark-2026-10-22.json](runtime-records-benchmark-2026-10-22.json)。

## 验收边界

本次实现统一保存在 `fix/work-runtime-records` 分支；下列边界描述本轮实际验证范围。

- 本轮执行 Linux 自动化及本地故障注入；未在 macOS / Windows 主机实际重跑。
- 未运行带真实模型响应的 PI 会话；模型自主使用新查询/恢复指令仍需实际试验。
- 覆盖真实进程退出和选定写入故障；没有声称穷尽每一个 OS 崩溃/断电窗口。
- 未启动真实外部 BMAD / OpenSpec / Spec Kit 工作流；现有任务范围回归与安装包合同
  使用明确的本地 fixtures。运行记录不会把 fixture / Agent 声明升级为真实执行证明。
- `logs.mode=off` 关闭额外正文；关键元数据和安全尾部继续记录。Work 软配额到达后
  不再采集新正文，关键元数据不会自动删除。观测达到配额会停止新增并提示。
- 普通 read/list 与 CAO 控制命令不产生工具观测；宿主缺失字段时不能推造完整覆盖。

使用说明：[runtime-records.md](../references/runtime-records.md)。
