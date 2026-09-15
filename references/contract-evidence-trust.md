# 契约：证据信任（contract-evidence-trust）

状态：**契约文档（PR0），尚未实现**。实现见 PR1（`scripts/evidence_provenance.py`）。

定位：本契约是**在现有项目指令与不变量约束下的实现与评审依据**，不覆盖已有更严格的规则。它与 `references/state-and-evidence.md`、`references/action-authorization.md` 冲突时，以更严格者为准。

---

## 1. 三维模型（不得合并为一个枚举）

每条证据记录由三个**互相独立**的维度描述：

| 维度 | 取值 | 含义 |
| --- | --- | --- |
| `kind`（来源） | `agent_claim` \| `mechanical_observation` \| `human_approval` \| `native_result` | 这条结论从哪一类来源来 |
| `validation_status`（校验状态） | `unverified` \| `verified` \| `invalid` | 验证器是否成功核验了来源与绑定 |
| `outcome`（业务结论） | `passed` \| `failed` \| `approved` \| `rejected` \| `unknown` | 结论本身是什么（按 `claim_type` 收窄允许集） |

**E1**：三个维度必须独立存储与判定。禁止把来源类型编码进校验状态（历史 `TRUST_STATES = {unverified, human_approval, native_result}` 即此错误的产物）。

**E2（有效失败证据）**：来源可信且校验通过、但结论为失败的证据，记为 **`verified` + `failed`**，是**有效证据**，必须能正常阻断授权。
`invalid` 只用于"证据本身不能被采信"，不用于"结论是负面的"。

**E3（invalid 的边界）**：仅下列情形判 `invalid`：
1. `work_item_id` / `requirement_revision` 与当前判断对象不符；
2. 来源不在允许集合内，或格式无法解析；
3. 强制依赖缺失、被替换、或绑定的版本/摘要与当前不一致；
4. 报告内容与声明状态矛盾（如报告 status=failed 但声明 passed）；
5. 证据记录进入自身所依赖的摘要（自失效循环，见 E8）；
6. 报告缺失、`unparsable`、或绑定快照已过期而被当作当前结论使用。

---

## 2. 记录字段

```
evidence_id          # 内容寻址：sha256(canonical(record 去掉 evidence_id/checked_at))
kind, claim_type     # 见第 1 节
validation_status, outcome
work_item_id
requirement_revision # requirement_id + source_revision（见契约 contract-requirement-version）
producer, verifier   # 【记录字段，见 E4】
source: {type, ref}
depends_on: [{object_kind, object_id, revision}]
checked_at, reason_code
```

**E4（记录字段不建立权限）**：`strength=authoritative`、`actor`、`source_type`、`producer`、`verifier`、`evidence_ref` 均为记录字段。它们单独或任意组合，都**不能**把 `unverified` 变成 `verified`，也不能单独构成授权。
测试要求：更换 `kind` / `verifier` / `producer` / 添加审批字段 / 把 `source.type` 写成 `human_approval`，同一条未验证结论**不得**升级为可信。

**E5（升级路径只有两条）**：`agent_claim` 且无可核验支撑时恒为 `unverified`。建立信任只能：
1. 提供可机械复核的支撑材料，转 `mechanical_observation` 并通过复核；或
2. 经校验的真实 `human_approval` / `native_result`（分别校验审批的来源/对象/范围/版本，或经原生适配器读取验证）。

"机器验证不了" **不等于** "降级为人工/原生审批"。

---

## 3. 强制依赖（推导权在验证器）

**E6**：`required_dependencies(*, kind, claim_type, policy) -> frozenset` 由**验证器**依据结论类型 + 项目 Policy 推导最小强制依赖。调用方可以**追加**，不能**删除或替换**。
示例：`test_result -> {code_under_test, policy, requirement_revision}`。

**E7**：每项依赖必须绑定**具体对象 + 版本/摘要**，不能只写类别名称（`{object_kind, object_id, revision}`）。

最低测试（四种都必须无法延长证据有效期）：删除代码依赖 / 删除策略依赖 / 替换依赖对象 / 提交空依赖集合。

**E8（禁止自失效循环）**：证据记录本身不得进入 `depends_on` 中任一依赖的摘要；`evidence_id` 不参与自身内容寻址。

**E9（不做全快照绑定）**：不同结论声明各自实际依赖。需求审批不应因一次无关代码修改失效；测试结果必须绑定被测试代码。

---

## 4. 门禁与验证结果

**E10**：`record_gate` / `record_verification` 的记录必须绑定：
`argv`（参数数组）、`exit_code`、`report_path`、`report_digest`、`execution_snapshot_id`、`producer`。

**E11**：不接受调用方**同时**提交的"退出码 0 + passed"作为通过证明。报告 `status ∈ {failed, missing, unparsable}`、或绑定快照已过期，一律不得变为 `passed`（可记为 `verified + failed`）。

**E12（readiness per-key 来源规则）**：`readiness` 的每个 key 必须有独立来源规则，逐 key 声明允许的 `kind` 与来源。
"验收条目存在" 与 "验收已满足" 是**不同结论**，前者不得充当后者。

---

## 5. 旧证据与 fixture

**E13**：生产入口拒绝 fixture 身份（`source.type ∈ {fixture, synthetic, mock}` 或 producer 命中测试标记），失败码 `EVIDENCE_FIXTURE_IDENTITY_REJECTED`。测试 fixture 只能在测试进程内使用。

**E14**：缺验证来源的旧记录标记 `unverified` + `legacy`，并在诊断中给出**补齐命令**（见"最小重建路径"），既不默认有效，也不静默删除。

---

## 6. 失败码

| 码 | 含义 |
| --- | --- |
| `EVIDENCE_UNVERIFIED` | 仍是未验证的 agent 结论 |
| `EVIDENCE_SCOPE_MISMATCH` | 工作项 / 需求版本不符 |
| `EVIDENCE_DEPENDENCY_MISSING` | 强制依赖缺失 |
| `EVIDENCE_DEPENDENCY_DRIFT` | 依赖对象版本/摘要漂移 |
| `EVIDENCE_REPORT_CONTRADICTION` | 报告与声明状态矛盾 |
| `EVIDENCE_REPORT_UNPARSABLE` | 报告缺失或无法解析 |
| `EVIDENCE_SNAPSHOT_STALE` | 绑定快照已过期 |
| `EVIDENCE_FIXTURE_IDENTITY_REJECTED` | fixture 身份进入生产入口 |
| `EVIDENCE_SELF_REFERENTIAL` | 自失效循环 |

所有诊断必须含 `error` 码 + `next_action` + 缺失输入清单。

---

## 7. 验收清单（PR1 逐条打勾）

- [ ] `verified + failed` 的真实失败报告能阻断授权，且不被判为 `invalid`
- [ ] 伪造 `passed` 声明（无报告 / 报告 failed / 旧快照）被拒绝
- [ ] 更换 `kind` / `verifier` / `producer` / 审批字段，未验证结论不升级
- [ ] 删除代码依赖、删除策略依赖、替换依赖对象、空依赖集合，四种均不延长有效期
- [ ] 证据记录不进入自身依赖摘要
- [ ] readiness 每个 key 有独立来源规则，"存在"≠"已满足"
- [ ] fixture 身份被生产入口拒绝
- [ ] 旧记录标记 legacy 且给出补齐命令

---

## 8. 现状差距（PR1 实施对照，已实测）

| 现状 | 位置 |
| --- | --- |
| `strength=authoritative` 是唯一可跳过反证的特权，但不校验 evidence 是否真实存在 | `scripts/fact_resolver.py:65-66` |
| `source_type` / `evidence` 仅做"键存在"校验，空串可通过 | `scripts/fact_resolver.py:51`（模板 `L150-153` 预填空串） |
| `set_readiness` 只写 `evidence_ref`，无命令/退出码/报告/快照绑定 | `scripts/execution_state_manager.py:691-721` |
| `record_gate` 的 `command` 是调用方传入的字符串声明，无 exit_code、无报告路径 | `scripts/execution_state_manager.py:928-970`（CLI `L1461`） |
| `record_verification` 无命令/退出码/报告；`fresh` 仅由 status + 快照相等决定 | `scripts/execution_state_manager.py:1206-1241` |
| `collect_evidence` 不做"使用时复核"，无证据失效到 reason 的映射 | `scripts/action_guard.py:179-255` |
| 第二条等价事实写入路径不经过 state/evidence 绑定 | `scripts/intake_pipeline.py:36-39` |
