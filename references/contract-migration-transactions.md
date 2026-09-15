# 契约：迁移与事务（contract-migration-transactions）

状态：**契约文档（PR0），尚未实现**。实现随 PR1（证据）/ PR2（原生）/ PR3（身份迁移）分批交付。

定位：本契约是**在现有项目指令与不变量约束下的实现与评审依据**，不覆盖已有更严格的规则。它不要求把所有文件纳入一个大事务；重点是回答三个问题。

---

## 1. 三个必答问题（逐 PR）

| PR | 哪个记录是权威 | 哪些中间状态合法 | 重启后如何收敛 |
| --- | --- | --- | --- |
| **PR1 证据** | 内容寻址的**不可变证据记录**是权威；`.orchestrator/evidence/index.json` 只是**可重建索引** | 孤立证据（已写入、未绑定状态）**合法**；索引缺失**合法** | `rebuild_index()` 从不可变记录重建；孤立证据保留并可审计清理 |
| **PR2 原生** | `execution-state.yaml`（revision + 乐观锁）是治理权威；`native_observation` 是原生事实记录 | 原生观察已写、治理更新未提交**合法**（即"原生已推进、治理未满足"） | 重新 `resolve_projection()`；`expected_revision` 冲突 → 重新判定，不覆盖 |
| **PR3 身份迁移** | `registry.json`（`identity_version=2` + `source_revision`）是身份权威 | 迁移前的 legacy 记录**合法**；迁移中（有 backup、有 journal）**合法** | preview → backup → 原子替换 → **幂等重入**；半途中断按 journal 继续或回滚 |

---

## 2. 提交顺序

**T1**：跨文件写入按固定顺序，任何一步失败都留下可收敛状态：
1. 写**不可变**证据记录（内容寻址，写完不再修改）；
2. 更新索引（可重建）；
3. 绑定/更新状态（带 `expected_revision` 乐观锁）；
4. 更新 registry（身份）。

**T2**：绑定时必须检查**预期状态版本**；冲突后重新采集或重新判定，不静默覆盖。

**T3**：可恢复日志（journal）记录每一步的意图与目标，支持中断后继续；journal 本身不是权威，只是恢复依据。

---

## 3. 故障注入清单（必须可测）

| 场景 | 期望 |
| --- | --- |
| 证据写入成功、状态绑定失败 | 孤立证据保留；索引可重建；状态不变 |
| 状态绑定成功、registry 更新失败 | 状态有效；registry 可通过重入补齐；不产生重复身份 |
| 原生解析完成后，规范状态已被另一进程修改 | `expected_revision` 冲突；重新判定，不覆盖 |
| 迁移进行到一半进程退出 | 从 backup + journal 继续或回滚；重入幂等 |
| 索引文件损坏/缺失 | `rebuild_index()` 恢复；不产生第二套权威 |

**T4**：验收必须包含上述故障注入与并发冲突。**"迁移说明文档存在"不能替代这些测试。**

---

## 4. 迁移流程

**T5**：`preview → backup → 原子替换 → 幂等重入 → 中断恢复`。
- preview 必须输出可审阅的影响清单；
- backup 在原子替换前完成；
- 原子替换沿用仓库现有 `_atomic_json`（临时文件 + `os.replace`）风格；
- 同一输入重复执行结果一致（幂等）。

**T6**：旧身份缺 `source_revision` → 标记 `legacy` + 给出迁移命令，**绝不默认"未变更"**。

---

## 5. 最小可操作重建路径（A 阶段自带）

旧证据被判 `unverified` 后，必须有合法方式重新建立证据。PR3 必须端到端跑通**四条**路径（"旧项目 → 迁移 → 重新验证 → 继续实现"），并在诊断中给出缺失输入与补齐命令：

1. **重新采集机械证据**：哪个已实现命令重新采集、写入什么记录；
2. **导入并验证真实报告**：报告路径/摘要/退出码如何绑定，如何区分 `verified+failed` 与 `invalid`；
3. **记录真实人工/原生审批**：如何校验审批来源、对象、范围、版本；
4. **迁移旧身份**：legacy 标记 → preview → backup → 替换 → 验证。

只返回 `error` + `next_action` 名称视为**未完成**。

---

## 6. 验收清单

- [ ] PR1：证据记录不可变、内容寻址；孤立证据允许存在；索引可重建；故障注入 1/2/5 通过
- [ ] PR2：`expected_revision` 冲突可测；原生观察与治理更新的提交顺序明确；故障注入 3 通过
- [ ] PR3：preview/backup/幂等重入/中断恢复可测；故障注入 4 通过
- [ ] 四条重建路径各跑通一次，并记录实际命令
- [ ] 不出现第二套可独立修改的权威记录

---

## 7. 现状差距（实施对照，已实测）

| 现状 | 位置 |
| --- | --- |
| 已有 revision + 乐观锁 + `_durable_transaction` 事务/日志机制，但**只覆盖 execution-state**，不覆盖新增的证据记录与 registry | `scripts/execution_state_manager.py:496-511`（`_durable_transaction`）、`:514-534`（`_commit`） |
| 已有原子写 JSON 的既有风格，可复用 | `scripts/requirement_identity.py:75-84`（`_atomic_json`） |
| 证据目前内联在状态里，无独立不可变记录，无索引 | `scripts/execution_state_manager.py:691-721`、`:928-970`、`:1206-1241` |
| registry 无 `identity_version`、无迁移标记 | `references/requirement-registry.schema.json` |
