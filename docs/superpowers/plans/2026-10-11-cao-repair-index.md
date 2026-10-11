# CAO Repair Implementation Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline; use superpowers:subagent-driven-development only after explicit operator authorization. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已确认的提醒模式分阶段修复 CAO 正确性、完成契约及主机集成。

**Architecture:** JSON 与 CLI 不重写；先建立状态事务，再绑定完成证据，最后修复 Host/UI 与资源限制。以下三份计划按 A→B→C 执行，任务之间有独立测试和提交。

**Tech Stack:** Python >=3.10 标准库、unittest、TypeScript、Pi 1.1.0、Node 22.18+。

## Global Constraints

- Python >=3.10；Python 核心仅使用标准库，不新增第三方运行时依赖。
- 新增代码文件不超过 500 行；不为满足行数限制一次性重写现有大型文件。
- UTF-8 文档；保留项目语言偏好与 Provider 原生文件所有权。
- 缺少有效评估/重大歧义只提醒；CAO 内部状态保护及 `cao done` 为硬约束。
- 本次交付的是待执行计划，不是完成修复；不自动执行、合并、推送或启动子代理。

---

## 设计与执行顺序

书面设计：[`2026-10-11-cao-integrity-repair-design.md`](../specs/2026-10-11-cao-integrity-repair-design.md)，用户已确认；文档提交 `fe57f6e`，代码基线 `8ac90f3`。

| 顺序 | 计划 | 任务 |
|---|---|---|
| A | [状态完整性](2026-10-11-cao-state-integrity.md) | A1 真实夹具；A2 原子写入/锁；A3 读改写/投影；A4 单 active；A5 审计/备份 |
| B | [完成契约](2026-10-11-cao-completion-contract.md) | B1 需求/验收绑定；B2 显式 tests；B3 工作流成功；B4 计划作用域；B5 路径保护 |
| C | [Host 与可用性](2026-10-11-cao-host-usability.md) | C1 Pi；C2 生命周期/提醒；C3 Dashboard；C4 验证执行器；C5 快照；C6 发布验收 |

全部 16 项任务的步序为：失败测试→最小实现→专项边界验证→定向/全量回归→独立提交。A1 不提交使既有测试失败的未完成夹具迁移；B 才变更验收规则。测试中断或未运行不能标记任务完成。

## 审查问题覆盖矩阵

| 问题 | 对应任务 | 必须验证的失败场景 |
|---|---|---|
| R01 | B3 | 工作流 exit 1 不能普通 complete/done |
| R02 | B2 | 明确声明 tests 的包装脚本不被名称误拒绝 |
| R03 | B2 | 可选失败测试不能满足必需 tests 门禁 |
| R04 | B4 | A 只检查关联计划，无关 B 不计入 |
| R05 | A4 | unblock/resume/reopen 不产生第二个 active |
| R06 | B1/B3 | 需求变化不能继承旧评审/工作流完成证据 |
| R07 | B5/C2 | traversal、patch、重定向不能直接改内部状态 |
| R08 | C2 | Claude/Codex allow 输出保留 additionalContext |
| R09 | A2/A3 | 12 个并发 decision 全部保留，旧 revision 拒绝 |
| R10 | C3 | active+native workflow completed 不显示 done |
| R11 | C2 | “尚未完成”不阻断停止，不关闭宿主会话 |
| R12 | B5/B3 | Provider 前缀加 ../src.py 不能冒充原生产物 |
| R13 | A5 | 两次重开产生两个事件，同一操作恢复不重复 |
| R14 | C1 | 正式 Pi 载荷传正确会话 ID 和助手消息 |
| R15 | C1/C2 | agent_end 保持 active，shutdown 才 ended，纠正最多一次 |

设计非单一缺陷要求：A2/A3/A5 覆盖中断与并发；B1 覆盖迁移和同秒标准变更；B2/C3 覆盖统一新鲜度；B4 覆盖同需求多故事；C4/C5 覆盖性能；C6 覆盖文档、包与真实平台/主机验证。

## 代码/接口交接

- A2：`repo_transaction(repo, timeout=5.0)`、`atomic_json(path,value)`。
- A3：`mutate_work(repo,wid,update)`、`recover_index(repo)`、`save_work` 的 state_revision。
- A5：`queue_event(work,event)`、`drain_events(repo,work)`、`backup_once(repo,path)`。
- B1/B2：`acceptance_hash`、`normalize_scripts`、`anchor_acceptance`、`evidence_matches`、`verification_fresh`、`tests_satisfied`；C4 同时扩展 hash 输入以绑定 timeout。
- B2：`verification_inputs(repo,work,cfg)`、`commit_verification(repo,wid,inputs,scripts,results,source,started)`。
- B3：`workflow_ready(work,repo)`；B4：`bind_plan_scope`、以 work 为输入的 `provider_pending_plan_items`。
- B5：`repo_file`、`inside`、`provider_artifact`、`patch_targets`；C4：`run_script`。

后续任务不能另起同名不同语义的判定；需要变更签名时同步调用者、测试、协议和本索引。新增源文件尚未创建；本文引用其路径表示执行交付目标。

## 启动前和完成条件

- [ ] 用户明确选择当前会话顺序执行或授权子代理执行；默认不委派。
- [ ] 执行时按 using-git-worktrees 技能准备隔离工作区；确认当前修改归属，不覆盖用户改动。
- [ ] 使用知识图谱确认最新代和覆盖，再阅读当前函数；历史行号与审查结果不替代最新源码。
- [ ] 运行现有测试基线，区分代理/Host 环境污染与真实项目失败。
- [ ] 每个任务记录失败复现、修复后结果和提交，审查对应矩阵项后进入下一项。
- [ ] 真实宿主/平台不可用时列为未验证/阻塞，不用模拟通过替代；不宣称发布条件已满足。
- [ ] 全部发布门槛真实满足后交付兼容说明与验证报告；合并/发布另行授权。

## 暂存纪律

每任务示例 git add 指向该任务文件。若既有夹具规则变化导致相邻测试文件必须调整，只暂存实际受影响文件，并说明新旧契约变化。禁止 `git add .` 混入用户或其他任务改动。
