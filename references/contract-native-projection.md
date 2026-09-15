# 契约：原生投影与授权（contract-native-projection）

状态：**契约文档（PR0），尚未实现**。实现见 PR2（`scripts/native_state_parser.py`）。

定位：本契约是**在现有项目指令与不变量约束下的实现与评审依据**，不覆盖已有更严格的规则；与 `references/state-adapter-bmad.md`、`references/state-adapter-contract.md`、`references/action-authorization.md` 冲突时以更严格者为准。

信任边界声明：本契约只保证**所有受支持的写入入口都自行走同一可信解析流程**。它不提供、也不声称提供对同进程任意代码的防伪造保证；真正需要独立信任边界的强制门禁应由受保护的 CI / 原生系统 / 隔离执行器承担。

---

## 1. 单一校验入口

**N1**：原生状态只能由 `resolve_projection(repo, state)` 产生。它在**操作时刻**：
1. 读取项目**实际配置**的原生来源（复用/扩展 `scripts/state_provider_detector.py` 的定位能力）；
2. 定位当前 story / work item；
3. 解析**该版本支持**的原生状态字段与任务进度（不猜历史 BMAD 格式）；
4. 计算规范化投影。

**N2（单次读取、同一份字节）**：一次读取后，同一份字节既用于 `content_digest` 也用于状态解析。禁止"先哈希、后读取"两份可能不同的内容。

**N3**：入参 `phase` / `status` / `progress` 只作为**预期值**，必须与解析结果一致；不一致即拒绝。更合适时由解析结果直接生成。

---

## 2. 结构化诊断码

| 码 | 触发 |
| --- | --- |
| `NATIVE_SOURCE_UNCONFIGURED` | 项目未配置原生来源，或来源越界（不在项目内） |
| `NATIVE_WORK_ITEM_AMBIGUOUS` | 无法唯一定位当前 story / work item |
| `NATIVE_FORMAT_UNKNOWN` | 原生格式不被该版本支持 |
| `NATIVE_STATUS_UNSUPPORTED` | 解析出的状态值不在该版本支持集合内 |
| `NATIVE_TASK_MISSING` | 缺失任务记录，无法计算进度 |
| `NATIVE_SOURCE_CHANGED` | 提交前重读发现来源已变化（见 N6） |

诊断必须含 `error` + `next_action` + 缺失输入清单；不得猜测或回退到"某个历史格式"。

---

## 3. 无令牌

**N4**：不实现任何跨进程"原生验证令牌"。不接受外部传入的 `verified=true`、普通字典、或任何序列化后重新导入的"已验证投影"。"已验证投影"只能是**本次调用**产生的结果。

**N5（`NativeProjection` 的定位）**：`NativeProjection` 是**内部结果类型**，不提供外部序列化/反序列化导入入口。
- 不声称"进程内不可伪造"（普通 Python 类无法对同进程代码提供该保证）；
- **禁止用 `isinstance()` / 类型检查代替来源验证**；
- 所有受支持的写入入口必须自行调用 `resolve_projection()`。

---

## 4. 投影绑定与提交前重读

**N6**：投影必须绑定本次操作的：
`work_item_id`、`requirement_revision`、`source_ref`（配置来源）、`expected_state_revision`（预期状态版本）。

写入前调用 `detect_source_change()` 重读来源 digest / 状态版本并与投影绑定值比对；不符则报 `NATIVE_SOURCE_CHANGED` 并**重新判定**，不得写入。

**一致性程度**：这是 **best-effort 的并发冲突检测**，用于避免"解析后、提交前原生来源再次变化"导致的错误投影；**不是安全边界承诺**，也不保证分布式/跨进程互斥。契约中不把它宣传为不可绕过。

---

## 5. 所有入口必须收敛

**N7**：下列入口必须共用同一校验函数（先 `resolve_projection`，再消费本次结果）：
- 前端 `native-sync`（`scripts/coding_orchestrator.py:853`）
- 底层 `sync-native` 子命令（`scripts/execution_state_manager.py:1529-1537` / `:1603-1604`）
- `sync_native()`（`scripts/execution_state_manager.py:1284`）
- `transition()`（`scripts/execution_state_manager.py:565`）
- `set_progress()`（`:731`）
- `hosts/*` hooks（PR2 须二次确认是否只走 `check`）

**N8**：`--native-confirmed` 退化为**兼容开关**，不代表信任提升；未持有本次解析结果即视为未确认。

**N9**：原生数据通过项目支持的机制更新；不得直接重写 `sprint-status.yml` 等原生状态文件来迎合投影（见 `references/state-adapter-bmad.md`）。

---

## 6. 两条写入路径

| 路径 | 内容 | 授权要求 |
| --- | --- | --- |
| **原生观察** | `authority.native_observation`：work item、phase/status/progress、source_ref、content_digest、native_state_revision、observed_at | 记录事实，不需要治理授权 |
| **治理更新** | 治理 `phase` / `status` 与 `completion_record` | 必须先通过 `action_guard` 统一授权 |

**N10**：允许并必须能表达"原生已推进、治理未满足"。二者不一致时输出明确状态与诊断：
- 不丢弃原生事实；
- 不把原生事实直接转换为治理许可；
- 原生 done **不得**生成 Governance `completion_record`。

**N11（受保护边界，非白名单）**：`implementation` / `review` / `closed` 是**重点用例**，必须各有拒绝测试。
它们**不是**新的授权白名单：`verification` / `release` 及任何其他已有受保护阶段，仍必须继续经过原有规则与 `action_guard` 授权。契约不引入"仅这三类需要授权"的含义。

---

## 7. 验收清单（PR2 逐条打勾）

- [ ] native `ready-for-dev` + CLI 声称 `implementation` 被拒绝
- [ ] 原生实际 `in-progress` 能正确投影
- [ ] 错误 story / 未知格式 / 缺失任务 → 结构化诊断，状态不变
- [ ] 前端 CLI 与底层 `sync-native` 行为等价（任一侧绕过测试失败）
- [ ] 伪造投影、跨工作项复用投影、源变化后重放，均被拒绝
- [ ] `--native-confirmed` 无本次解析结果即视为未确认
- [ ] 提交前源变化 → `NATIVE_SOURCE_CHANGED`
- [ ] 原生 done + 门禁失败 → 不生成 `completion_record`，且能展示差异
- [ ] `implementation` / `review` / `closed` 三边界各有用例；`verification` / `release` 等既有边界回归通过

---

## 8. 现状差距（PR2 实施对照，已实测）

| 现状 | 位置 |
| --- | --- |
| 只比对调用方传入的 `--native-revision` 与 `--native-state-ref` 指向文件的摘要，**不解析原生内容**；`phase/status` 直接来自命令行入参 | `scripts/coding_orchestrator.py:853-876` |
| `sync_native()` 完全信任入参：不校验摘要、不解析原生来源、不比对预期值 | `scripts/execution_state_manager.py:1284-1325` |
| 底层 `sync-native` 子命令直调 `sync_native`，**无任何摘要或人工校验**（旁路） | `scripts/execution_state_manager.py:1529-1537`、`:1603-1604` |
| `native_confirmed` 是纯布尔，仅用于放行 native authority 的 advance/close | `scripts/action_guard.py:349-351`；`scripts/execution_state_manager.py:559-562, 737-738` |
| 无 `native_observation` 结构；`sync-native` 直接改写治理 `phase`/`status` | `scripts/execution_state_manager.py:1301-1303` |
