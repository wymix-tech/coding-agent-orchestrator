# 契约：需求版本（contract-requirement-version）

状态：**契约文档（PR0），尚未实现**。实现见 PR3（`scripts/requirement_identity.py` identity_version=2）。

定位：本契约是**在现有项目指令与不变量约束下的实现与评审依据**，不覆盖已有更严格的规则。

---

## 1. 四元组

| 字段 | 定义 | 变化含义 |
| --- | --- | --- |
| `requirement_id` | 稳定身份。有 native_id → `provider:native:<id>`；有 source → `provider:source:<posix-path>`；显式 work id → `provider:work:<id>`；否则 `provider:direct:<hash>` | 身份不变，跨修订稳定 |
| `source_revision` | **定义需求内容的文件集合及其内容摘要** | 需求内容变了才算修订 |
| `request_revision` | 请求措辞摘要，**仅用于追踪** | 措辞变化**不**构成需求修订 |
| `native_state_revision` | 原生运行状态版本 | 任务勾选/推进属于这里，**不**构成需求修订 |

**R1（优先级）**：`source_revision` 只能由**内容源**推导。显式 revision 是**外部版本标签**，与内容变化检测结果**并列展示**，不得覆盖、不得充当"内容未变"的证明。

**R2（已完成判定）**：需求是否已完成只看 `requirement_id` + `source_revision`，**不得**混入 `request_revision`。这是"已完成需求因请求措辞变化而复活"的根因。

---

## 2. 无源文档的直接请求

**R3**：纯文本 intake（无 source、无 native_id）时 `source_revision = None`，身份为 `provider:direct:<hash(request_text)>`。
- 不得与有源需求合并；
- 版本判定退化为 `request_revision`；
- 必须明确记录 `source_revision=None` 的语义（"无外部内容源"），不得用空串或占位值冒充有效修订；
- 后续若补充了 source，按显式修订处理，不静默改写历史身份。

---

## 3. 文件集合规则

**R4**：`source_revision` 由确定的文件集合摘要产生，必须规定：
1. **成员清单**：显式列出参与摘要的文件（目录展开规则）；
2. **增删文件**：集合成员变化即内容修订；
3. **排序**：POSIX 路径字典序确定性排序；
4. **路径规范化**：POSIX 相对路径、去除 `./`、统一分隔符；
5. **符号链接**：默认不跟随，记录链接目标；跟随必须由配置显式开启；
6. **运行产物排除清单**：显式列出排除项（生成报告、缓存、临时文件等）。

不可读时返回**结构化诊断**（`SOURCE_UNREADABLE`），不得静默返回 `None` 导致"默认未变更"。

---

## 4. 同文件混合内容（关键）

BMAD story 等单文件可能同时包含：需求描述、验收标准、任务勾选、状态、开发记录、执行备注。整文件入摘要 → 勾选任务即改版本；整文件排除 → 验收标准变更漏检。

**R5（结构化边界）**：已支持格式必须明确"需求内容"与"运行内容"的**结构化边界**（章节/字段级），只对需求内容部分计算 `source_revision`。

**R6（未知格式）**：不认识的格式必须要求配置显式指定来源边界，**或**返回诊断 `SOURCE_MIXED_CONTENT_UNBOUNDED`。
**禁止**用宽泛正则静默删除内容后当作已处理。

**R7（双摘要）**：保留**完整原文摘要** `raw_digest` 用于追踪，与需求内容版本 `source_revision` **分开存储**。

**R8（AC 复选框 ≠ 任务进度）**：验收标准中的复选框属于**需求内容**；普通任务勾选属于 `native_state_revision`。

**成对验收（必须同时成立）**：
- 同一文件内勾选任务 → `source_revision` **不变**；
- 同一文件内修改验收文本 → `source_revision` **必变**。

---

## 5. 修订与重新分析

**R9**：源变化 → 必须走**显式修订**。
**R10**：源未变但代码 / Policy 变化 → **同版本**重新分析，旧结果不得直接复用。
**R11**：显式新 `resolutions` / `base-ref` / 证据 / 重新分析意图，必须进入重新分析，不得被"源未变"的快捷恢复分支吞掉。

**R12（破坏性修订确认）**：确认必须绑定**待重置的需求版本与当前状态**：
`(requirement_id, source_revision, 当前状态)`。
- 状态在确认后发生变化 → 必须**重新确认**；
- 拒绝修订 → **不提前写入新基线**，状态保持不变；
- 不得让 Agent 为解除阻塞自行补充确认参数。

---

## 6. 迁移

**R13**：旧记录缺 `source_revision` 标记 `legacy`，并给出**可执行的迁移命令**；绝不默认"未变更"。
**R14**：保留可审计历史；升级不得复活已完成需求，也不得混入旧证据。

---

## 7. 失败码

| 码 | 含义 |
| --- | --- |
| `SOURCE_UNREADABLE` | 需求源文件不可读（替代静默 None） |
| `SOURCE_MIXED_CONTENT_UNBOUNDED` | 混合内容文件未配置结构化边界 |
| `REVISION_CONFIRMATION_REQUIRED` | 破坏性修订需要显式确认 |
| `REVISION_CONFIRMATION_STALE` | 确认后状态已变化，需重新确认 |
| `IDENTITY_LEGACY_UNMIGRATED` | 旧身份未迁移 |

---

## 8. 验收清单（PR3 逐条打勾）

- [ ] 已完成需求不因请求措辞变化而复活
- [ ] 无源 intake 有明确版本规则，不误伤已有入口
- [ ] 目录 / 多文件集合规则生效（成员、增删、排序、规范化、符号链接、排除项）
- [ ] 同一文件：任务勾选不改 `source_revision`；验收文本改变必改
- [ ] 显式 revision 不掩盖内容变化（两者并列展示）
- [ ] 显式新 resolutions / base-ref / 证据 触发重新分析
- [ ] 破坏性修订确认绑定需求版本与状态；状态变化后重新确认；拒绝不写新基线
- [ ] 旧身份标记 legacy 且给出迁移命令

---

## 9. 现状差距（PR3 实施对照，已实测）

| 现状 | 位置 |
| --- | --- |
| `candidate_identity()` 用 `revision_id(request_text)` 作为 revision —— **请求措辞被混入需求修订判定** | `scripts/requirement_identity.py:62-72`（关键行 `L71`） |
| `source_revision_id()` 只支持**单文件**；不可读时**静默返回 None** | `scripts/requirement_identity.py:28-44` |
| 无 `identity_version`、无 `source_revision` 必填、无 legacy 标记 | `references/requirement-registry.schema.json` |
| `processed()` 以 revision（混入请求文本）判定完成 | `scripts/requirement_identity.py:134-137` |
| 存在"源未变"快捷恢复路径，可能吞掉显式新输入 | `scripts/start_router.py`、`scripts/semantic_intake_pipeline.py`（PR3 开工前须重新核验具体分支） |
