# CAO / SDD 生命周期职责边界修复方案与实施计划

日期：2026-10-26（北京时间）  
状态：设计完成，待实施；本文中的新增字段、行为与错误码均为拟议契约。  
目标仓库：wymix-tech/coding-agent-orchestrator  
设计核对基线：main `c9b98e2025425c4975642f8c73b89504ab0075d7`（PR #28 合并后）。

## 1. 目标与最终行为

SDD 负责当前 work 的规划、实现、功能测试、代码评审、问题修复、原生验收与必要收尾；这些动作均在 CAO 的执行阶段内完成。过程中将原生产物和执行结果持续绑定到 CAO。

只有 SDD 确认当前 work 的全部关联范围完成，CAO 才进入自身评审。CAO 评审仅检查过程与产物证据链的完整性、关联性、真实性依据和版本有效性。评审通过后，CAO 执行项目基线验证，最后关闭 work。

核心顺序：

1. 项目背景与技术栈 → 项目骨架 → 项目基线准备、检查。
2. 创建或恢复 work → SDD 原生规划与实施。
3. SDD 原生测试、评审、修复、验收及必需收尾；CAO 保持执行阶段。
4. SDD 完整完成 → CAO 证据链评审。
5. 证据链合规 → CAO 项目基线验证。
6. 原生完成、证据链、基线三者均有效 → `cao done`。

“CAO 仅执行项目基线”允许基线中的 `mvn test` 等命令运行项目全部测试，包括当前功能新增的测试；不再为每个 work 另建一套 CAO 功能验收执行协议。项目基线是标准，执行结果绑定当前 work 和当前代码，不能继承上一个 work 的通过结论。

本次完成设计与实施计划，不修改业务仓库、不推送提交。

## 2. 证据与问题定位

### 2.1 会话证据

来源：`--Users-wymix-Projects-demo--(2).zip` 中主会话 `2026-10-26T14-12-54-021Z_01a1788e-9a84-75da-b0df-65f8fd321c3f.jsonl`。下表为原始 JSONL 行号。

| 编号 | 观察 | 证据 | 判定 |
| --- | --- | --- | --- |
| F1 | 原生 workflow 仍为 executed 时已进入 completion 指引，并先执行正式验证 | 528、531；556 才调用 workflow complete | 收尾推进顺序提前 |
| F2 | CAO 配置并运行功能验收执行器，随后编写逐条 AC 测试映射 | 288、550 以后、612 | CAO 增加了第二套 work 功能验收流程 |
| F3 | 验收器要求 JSON，执行器实际输出 Maven 日志 | 557、597 等返回 JSON 解析错误 | 执行协议不匹配，不能据此认定业务测试失败 |
| F4 | 修复验收配置期间，三个原生评审层因 code_snapshot 改变而 stale | 623 | 已确认失效结果；具体哪些文件造成快照变化须通过差异清单复现，不凭时序断定唯一原因 |
| F5 | 写入 CAO verification 目录的新文件被门禁拦截，随后尝试根目录 scripts/verification | 612—616 | 缺少合法文件准备/恢复指引；不能由此推导必须开放整个目录 |
| F6 | 补配置、reanchor、检查评审状态持续遭遇 stale | 620—637 | 恢复路径没有按证据类别收敛 |

### 2.2 当前 main 已确认的实现原因

| 位置 | 已核对的行为 | 修复含义 |
| --- | --- | --- |
| `scripts/cao_native_sync.py`，`reconcile_native_sdd_state` | 第 797 行附近将 `completed` 与 `in_review` 一起映射为 CAO `review` | SDD 评审确实可以提前改变 CAO 阶段 |
| `scripts/cao_work_store.py`，`execution_phase` | `native-sdd-workflow-completed` 直接映射到 review | 不能只改 native sync；所有阶段派生入口必须统一 |
| `scripts/native_completion.py` | 目前主要针对 BMAD dispatch SPEC done，作为关闭义务检查 | 需要扩展为 provider/route 感知的原生完成条件，并用于进入 CAO review 前 |
| `scripts/cao_verification.py`，`_prepare_verification` | 检查计划、原生评审和规划，但没有统一的完整原生完成 + CAO 合规评审前置条件 | 正式 verify 可以早于原生完整完成 |
| `scripts/acceptance_summary.py` | 独立 task acceptance、逐条 coverage、执行结果共同参与关闭判定 | 必须改为消费原生证据，而不只是隐藏提示 |
| `scripts/cao_completion.py` | 汇总同时拼接新 acceptance、conformance 与 legacy 判定；仍提示 verify 后 acceptance run | 保留多个决策来源会重新引入旧门禁与矛盾下一步 |
| `scripts/conformance.py` | 基于完整 lifecycle obligations 生成合规结果，包含后续门禁 | 若直接前移到 verify 之前会造成循环依赖 |
| `scripts/cao_defaults.py`、`scripts/cao_path.py` | profile/risk gates 与验证能力、原生评审混合 | 需要明确每个义务的 owner 和 evidence source |
| `references/completion.md` | 明确要求 review 前配置 task acceptance、verify 后执行 final acceptance | 与新职责边界冲突，必须同步重写 |
| `scripts/managed_verification.py` | 现有 prepare/registry 采用精确路径授权 | 复用现有机制，补充引导和测试，不做宽泛权限放开 |

注意时间边界：上传会话最后记录约为 22:45:54，当前 main 的 PR #28 于 22:46:38 合并。不能声称压缩包已经运行 PR #28；方案以当前 main 为后续实施起点，保留该 PR 的项目基线独立性修复。

## 3. 不变量与范围

以下是不允许在实施中改变的验收约束：

- SDD `in_review`、发现问题、修复、再次评审、原生验收运行中，均不能使 CAO 进入自身 review/verification。
- 原生任务勾选完成、单一 SPEC done、单一 story done，都不能替代整个 work 的原生完整完成。
- CAO 不重复进行功能代码评审，不统一强加 BMAD 的三层评审给其他 provider。
- CAO 不默认要求每个 work 配置 `acceptance_executor.py`、逐条测试映射或独立 JSON 报告。
- 复用原生结果不等于相信任意 `passed=true`、一句“已完成”或手工修改 done 状态。
- 规划、评审、功能验收仍需符合已接受的原生路由及用户要求；缺证据应补绑定或回到对应原生步骤。
- 项目基线变更仍有版本和现有审批约束；业务需求不能自动覆盖项目基线。
- 保留当前 work ID、需求、计划关联、有效评审记录与执行历史；修复不重新 intake、不回退 discovery。
- 已完成 work 的历史记录不可自动重写；主动 reopen 后按新规则验证。
- BMAD 以当前支持版本和安装契约为准；本次不引入 BMAD V7 适配。
- 适配 BMAD、OpenSpec、Spec Kit 的现有路径，不扩展到新的 provider、技术栈或 workspace 架构。

## 4. 状态与推进策略

### 4.1 CAO 阶段与 SDD 子状态分开

保留 CAO 现有大阶段名称，明确 `implementation` 的含义为“执行 SDD 当前 work”，包括原生测试、评审、修复和验收。界面可显示“执行中（SDD：评审中）”。规划与 ready 阶段仍按现有合法前置条件进入。

| SDD 当前事实 | CAO 阶段/动作 | 说明 |
| --- | --- | --- |
| 规划、待原生批准 | planning / ready 等现有阶段 | 不因生成文件认定执行完成 |
| 实现、测试、评审、修复、原生验收或必要 finalize 未结束 | implementation | `sdd.native_state` 保留细分进度 |
| 单元已完成，但关联集合仍有未完成单元 | implementation | 指向下一未完成单元 |
| 关联范围完成，原生必需步骤结束，存在当前范围的完成声明 | review | 进入 CAO 证据链评审 |
| CAO 证据链通过，尚无当前基线通过结果 | review → verification | 由正式 verify 开始进入 verification |
| 基线失败或环境阻塞 | verification + 原因 | 不自动回退 SDD 评审 |
| 三项均通过、无其他阻塞 | 等待 done → completed | 只有成功 done 才最终关闭 |
| SDD 重新打开或代码实质变更 | 按受影响义务回到 implementation | 保留历史并记录具体失效原因 |

“完成声明存在”与“证据链通过”分开：进入 CAO review 可以发现报告漏绑定；原生仍在评审中则根本不具备进入资格。矛盾或来源不可用不视作完成。

### 4.2 单一推进判定

优先重构现有完成模块，必要时增加一个小型纯策略模块；不要再复制一套 next 规则。策略接受观察结果，输出：

- `cao_phase`、`sdd_substate`。
- 当前阻塞项及 owner：`sdd`、`cao_evidence`、`project_baseline`、`human_decision`。
- 一个优先下一步，带原因和所需证据；其他问题列为 diagnostics。
- 建议的阶段变化及原因，由命令层事务性落盘。

依赖关系必须是单向的：原生完成条件 → 证据链合规 → 正式项目验证 → 最终关闭。原生完成与证据链检查不得依赖本轮 CAO verify；最终关闭才汇总三者。

所有入口共享该策略：bootstrap、next、resume、sdd sync、workflow complete、conformance check、verify、done、host hooks、dashboard。`show/status` 可只读展示准备度，不得通过展示自动授予通过结论。已有 sync 的状态修正通过统一事务入口执行。

### 4.3 事件与一致性

区分 `sdd.review.*`、`sdd.completion.*` 与 `cao.conformance.*`、`cao.verification.*`。旧 `task.review.started` 的兼容投影必须明确 owner 为 SDD，不能继续被 UI 当作 CAO review。

复用已有 work generation、run ID、事务锁与幂等机制。相同观察重复 sync 不重复创建事件。旧 session/旧运行的迟到结果不能覆盖新 generation，也不能重新推进已被撤销的完成声明。

## 5. 原生完成与证据绑定

### 5.1 最小数据模型

优先扩展现有 `sdd.native_state`、`plan_scope`、workflow、route、review bindings，而不是新增另一份需要 Agent 手工维护的 work 数据。实现可采用以下概念结构；字段名以代码重构后的单一 schema 为准。

```json
{
  "contract_version": 2,
  "work_id": "<existing-work-id>",
  "provider": "<selected-provider>",
  "route_id": "<accepted-route>",
  "scope_revision": "<bound-scope-digest>",
  "native_completion": {
    "observed_state": "completed",
    "eligible_for_cao_review": true,
    "source_refs": ["<native-completion-source>"],
    "product_snapshot": "<snapshot>"
  },
  "evidence_bindings": [
    {
      "role": "native_review",
      "owner": "<selected-provider>",
      "unit_ids": ["<associated-unit>"],
      "source_ref": "<native-report>",
      "content_digest": "<hash>",
      "provenance_ref": "<observed-run-or-session>"
    }
  ]
}
```

内部派生记录由 CAO 命令生成。用户/Agent 继续使用现有 artifact/plan/review/workflow 接口提交真实引用，无须手写此 JSON。任何缓存的 eligible/通过值均需按当前输入重新校验。

### 5.2 绑定内容

按路由需要绑定：需求与批准版本、规划产物、执行单元范围、实现快照、原生测试/验收结果、评审报告、问题处置与复核、原生完成声明、必要的提交/归档依据。

同时保留内容指纹与语义指纹：前者用于原始证据完整性；后者仅使用 provider 明确声明的状态字段归一化规则，避免合法 status 更新使需求正文证据失效。不允许任意 Markdown/YAML 都按“只有元数据变化”处理。

### 5.3 Provider 适配

| Provider/路径 | 完成条件与绑定要求 |
| --- | --- |
| BMAD dispatch/oneshot | 遵循对应安装路由的实现、评审、处理和 finalize/commit 约束；按原生能力记录结果，不只检查 frontmatter done |
| BMAD sprint/stories | 汇总当前 work 关联的全部单元；Build SPEC done 但对应 story 仍 review 时，继续原生 code-review；不把其他需求的单元纳入 |
| OpenSpec | 按安装 schema、关联 changes 的 tasks 和实际验证/收尾结果判定；不要求不存在的统一 done 字段；归档要求以实际原生契约为准 |
| Spec Kit | 按关联 feature 的 tasks、规定的检查清单与执行结果判定；不能只凭复选框或虚构终端命令完成 |

没有统一机器完成标识的 provider，可通过现有 workflow complete 记录有来源的原生完成报告，适配器验证执行与范围。原生没有独立 final acceptance 阶段时，绑定其实际实现测试和结束报告；不要额外制造阶段。用户明确要求的验收则必须在 SDD 执行期间完成。

若原生 finalize 要求先执行测试，则在 SDD 执行阶段运行其原生命令或同一项目检查命令，不能强依赖正式 CAO verify 成功，避免循环。原生不可缺少的 commit/archive 应在其合法时点执行；不擅自把它们推到 CAO done 之后。可选发布/知识收集不成为完成阻塞。

## 6. CAO 证据链评审

### 6.1 复用 conformance，拆开最终门禁

保留 `cao conformance show/check`。show 在执行过程中可作为预检，但不会推进到 CAO review。check 在 SDD 完整完成后验证下列内容并记录与当前证据版本绑定的结果：

1. 当前 work、需求版本、provider、route、执行单元关联一致。
2. 必需产物存在、非空且归属正确；豁免有原生规则或已授权依据。
3. 范围完整；没有遗漏、重复认领或偷换执行单元。
4. 原生评审有实际执行来源；独立性仅在路由要求时检查。
5. 评审发现按原生分类处理并完成应有修复/复核；没有未处理的阻塞发现。
6. 功能验收有原生结果，满足对应路由与已接受需求的证据要求。
7. 原生完成结论与全部关联单元状态一致。
8. 证据对应当前有效代码和需求；引用更改、删除或来源错误可定位。

CAO 不重新阅读代码得出第二次功能评审结论。若原生报告本身要求逐条需求覆盖，CAO 检查其绑定覆盖；如果原生采用整体验收报告，则检查报告声明的适用范围和运行依据，不强制转换成 CAO 的逐条可执行测试协议。

内部报告转换由适配器负责，可读取 Markdown、JSON、YAML 和原生运行记录。保留原始报告与定位信息；只有解析结果不等于确实执行过。无法确认来源或含义时明确 blocked/unsupported，不猜测 passed。

### 6.2 失败的修复归属

| 失败原因 | 修复路径 | 是否重做 SDD 功能评审 |
| --- | --- | --- |
| 已有报告漏绑定、路径记录错误 | 补引用、重做合规检查 | 否，前提是原始内容/来源可证明一致 |
| 报告被改写或结果无法证明 | 恢复可验证原证据，或执行缺失的原生步骤 | 按实际受影响证据决定 |
| story/tasks 实际未完成 | 继续该 SDD 单元 | 是其中需要执行的原生步骤 |
| 真实产品代码/需求发生变化 | 原生流程判断影响并完成必要复核 | 按依赖失效，不能一律保留 |
| 项目基线配置/运行失败 | 修复基线配置或环境，重跑基线 | 除非同时改变产品或原生验收依据，否则不重做 |

## 7. 项目验证与旧功能验收协议处理

### 7.1 CAO verify

正式 `cao verify` 前置条件为：active work、有效项目背景与基线、原生完成、CAO 合规结果当前有效。只运行该 work 所关联项目 scope 的已批准基线条目；复用现有 runner、日志、超时、测试发现、运行代次和快照校验。

SDD 阶段仍允许执行编译、单测、功能测试等真实命令；这些运行不把 CAO 推进为 verification。手动 `--script` 继续视为诊断，不得替换已批准基线或授予正式通过。

默认每个 work 需要当前有效的正式基线结果。相同 work 相同输入的有效结果可按现有复用规则保留；新增 work 不直接复用前一个 work 的完成结果。

### 7.2 清理重复功能验收

- 新契约的 `cao acceptance status` 展示“SDD 完成/原生结果、CAO 证据链、项目基线”，不再要求额外 CAO 功能执行器。
- 从新流程的 next、review preflight、completion、profile gates 中移除自动配置/运行独立 acceptance 的义务。
- 旧 `sdd_acceptance` gate 在兼容层映射为“当前原生验收证据有效”，不能简单删掉后默认通过。
- 旧 run/begin/submit/configure/reuse 等命令暂保留在 legacy 契约下解释历史记录；新契约不推荐执行。若原生路由明确使用了原有执行器，则作为 SDD 阶段的原生运行处理，不得依赖正式 CAO verify。
- 将 `acceptance_executor.py` 从默认路径移出；是否保留为可选工具视现有调用和兼容测试决定，不在本次直接删除历史文件。
- 老报告具备可验证来源与范围时可作为原生证据导入；不能把旧失败的 JSON 解析结果改写成通过，也不能因功能测试退出码 0 自动生成 AC 全通过。

### 7.3 Profile/risk gates 的归属

`light/standard/comprehensive` 仍用于选择满足风险要求的原生路径和证据深度，不代表 CAO 再做一次 review/验收。

安全、迁移、兼容性等 work 特有测试及审查要求由 SDD 执行并交付证据。项目已有通用安全/集成检查属于基线。发现普遍适用的基线缺口时，通过现有项目基线变更流程演进，不因“本次是登录需求”自动创建一个重复 `mvn test` 的 security wrapper 来满足标签。

每个 gate 必须具有明确 owner、适用范围和证据来源。人工 gate 只记录真实发生的授权/决策，不能替代自动可验证的原生执行结果。

## 8. 快照与精确失效

使用现有技术输入/任务输入分离机制，进一步明确以下依赖域：

- 产品输入：源码、产品配置、依赖、项目测试及实际执行依赖。
- 需求与原生范围：需求语义、规格、计划关联和路由要求。
- 原生证据：评审/验收报告、来源运行及其关联。
- 基线输入：命令、包装器及被引用帮助文件、配置、scope、相关环境。
- CAO 绑定元数据：索引、路径关联、来源记录及报告解析器版本。

| 变化 | SDD 完成/原生评审 | CAO 合规 | 项目基线结果 |
| --- | --- | --- | --- |
| 产品源码、产品配置或测试改变 | 失效并由原生流程恢复 | 失效 | 失效 |
| 需求/规格/关联范围实质变化 | 相关证据失效 | 失效 | 产品和技术输入未变时可保留原结果，仍不可直接关闭 |
| 仅补绑定现有报告，原文和执行来源一致 | 保留 | 重查 | 保留 |
| 原生报告内容改变 | 对应原生证据失效 | 重查 | 技术输入未变时可保留 |
| 仅调整基线包装器或基线配置 | 不自动全层重审；若也是原生执行依赖，则该证据失效 | 重查受影响引用/标准 | 失效 |
| Provider 定义的状态字段更新 | 保留语义结果，重算完成条件 | 重查 | 技术输入未变时保留 |
| 仅 Git commit、相同文件内容 | 保留 | 更新所需提交证据 | 保留 |
| 原生 reopen、新增任务、完成状态回退 | 撤销完整完成资格 | 失效 | 输入不变时保留运行历史，但不能关闭 |

不能按扩展名把所有 JSON/Markdown/YAML 排除，也不能忽略整个 scripts 目录。验证包装器虽然存于 `.orchestrator`，其内容及执行依赖必须进入基线指纹。声明一个文件为 acceptance-only 不能让它携带可执行产品变更而逃避审查。

失效输出必须列出 changed paths、changed domains、affected evidence、still valid evidence 和一个合法恢复动作。相同未改变的失败输入不触发无穷自动重试。

## 9. 文件归属与恢复门禁

- 项目基线入口继续位于 `.orchestrator/scripts/verification/<scope>/`，复用已有 `verification prepare --scope ... --path ...` 精确授权。
- 当前 work 的功能测试保留项目原生测试目录，原生评审/验收报告保留 provider 原生输出目录。
- CAO 绑定、索引、合规报告和运行历史放在现有 work/runtime 存储中，由命令写入。
- 默认流程不生成 CAO 的 `acceptance-checks.json`。如项目基线确有声明文件依赖，应通过 prepare 注册精确文件并纳入基线指纹。
- 未准备路径的拒绝应返回明确原因与 prepare 指引；来源未知/路径错误则说明修复点，不引导改写根目录 `scripts/verification`。
- 允许旧 work 在 review/verification 阶段执行有限的绑定修复和已登记基线准备命令；不放开任意 `.orchestrator` 写入和产品代码修改。
- 如果旧 gate 在外层拦住了用于修复同一 gate 的合法命令，应修复命令能力分类；不能要求 Agent 使用 Python 或 shell 绕过。

## 10. 兼容、迁移与回滚

1. 为新职责契约增加明确版本；新 work 使用新契约。实现版本和数据版本要可识别，不能依靠字段缺失猜测通过。
2. 活跃旧 work 先生成迁移诊断，盘点原生进度、计划范围、旧验收记录及可保留证据。通过 CAO 的受控命令事务迁移，保存迁移前快照、依据和事件。
3. 已进入 CAO review、原生仍 in_review 的旧 work，仅纠正 CAO 阶段为 implementation；保留需求、路由、审批、已完成单元和评审记录。无需重新询问已授权技术栈/需求。
4. 原生已完成但卡在 CAO 额外 executor 的 work，复用可证明有效的原生报告，进入证据链检查；缺少原生证据时明确返回缺项，不能凭迁移绕过。
5. 旧 verification 结果仅在基线、产品输入、运行来源等仍匹配时保留；不能通过重算当前 hash 伪造旧运行绑定。
6. 已完成 work 保持不可变历史；新显示层注明历史结果。显式 reopen 时检查当前真实输入。
7. 无法识别的旧 schema/路径给出明确不支持或待修复诊断，不自动创建空通过记录，不静默改 provider。
8. 回滚需恢复相容代码和迁移前数据；旧版本不得继续写入无法理解的新契约。迁移后产生新工作记录时，优先前向修复，禁止用旧快照覆盖新成果。

迁移与状态修正属于当前 work 恢复，不新增 blanket 用户审批。涉及新的业务范围、原有需审批的基线标准变更或外部发布时，沿用已有授权规则。

## 11. 实施计划与代码落点

建议从实施时最新 main 建立一个修复分支，先核对与本文基线的差异。按下列依赖顺序提交，整体完成验收后合并；不要先发布只改文档或只删门禁的中间版本。

| 阶段 | 工作 | 主要落点 | 交付与退出条件 |
| --- | --- | --- | --- |
| P0 复现与契约固定 | 最小化会话场景；记录错误状态和验收循环；整理当前 gate owner | 现有 native-sync/lifecycle/acceptance 测试 | 得到可稳定失败的行为用例和状态表，不依赖用户机器绝对路径 |
| P1 统一状态策略 | 分离 SDD 子状态和 CAO 阶段；统一 next/phase；前移原生完整完成条件 | `cao_native_sync.py`、`cao_work_store.py`、`native_completion.py`、`cao_completion.py`、`native_progress.py` | in_review 始终停留 implementation；多单元不能提前推进；所有入口一致 |
| P2 原生证据与合规 | 复用绑定；完成 provider 适配；conformance 与最终门禁拆开 | `conformance.py`、`native_obligations.py`、`cao_plans.py`、`cao_workflow_commands.py`、`review_binding.py` 及相关 review 模块 | 可以消费真实原生报告；缺失/篡改/错范围被阻止；无需重做功能评审 |
| P3 移除重复验收与归属整理 | acceptance summary 消费原生结果；整理 profile/risk gates；verify 只跑基线 | `acceptance_summary.py`、`cao_completion.py`、`cao_path.py`、`cao_defaults.py`、`cao_verification.py`、`sdd_acceptance_*`、`acceptance_trace.py` | 全新 work 无 executor 配置和 AC 映射即可合规关闭；原生缺证据仍阻止关闭 |
| P4 失效与恢复 | 依赖域失效；修复指引；精确路径能力 | `acceptance_inputs.py`、`cao_snapshot.py`、`snapshot_scope.py`、`review_scope.py`、`review_changes.py`、`managed_verification.py`、`cao_tool_policy.py` | 绑定修复不重启三层评审；产品变更可靠失效；准备命令可执行且路径保护有效 |
| P5 兼容迁移 | 活跃旧 work 转换、历史保留、事务/幂等/迟到结果 | work/store/runtime/recovery 模块及 legacy adapter | 不重新 intake；原始记录保留；重复迁移无副作用；旧损坏数据不自动通过 |
| P6 指引与呈现 | 同步技能、按需引用、CLI help、dashboard/host 文案、README 双语 | `SKILL.md`、`references/*`、`cao_guidance.py`、`skill_guidance.py`、host 输出、dashboard | 显示“CAO 执行中 / SDD 评审中”；没有默认二次验收指令；所有命令与真实 CLI 一致 |
| P7 回归与实际演练 | 完成下方矩阵；包安装回归；Pi 会话演练 | tests、发布包 smoke 与实验记录 | 证明从新项目到 done，及旧卡住 work 的恢复；记录真实环境限制，不虚报通过 |

依赖：P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7。每阶段同步添加必要测试，P7 负责跨阶段闭环，不把测试全部留到末尾。

P1—P3 构成必须一起交付的行为修复；P4—P6 是避免本次循环复发的必要范围；P7 是完成判定依据。知识收集、性能大改、更多 provider 和 BMAD V7 不纳入本次。

### 11.1 必须更新的技能引用

`SKILL.md`、`completion.md`、`conformance-knowledge.md`、`execution.md`、`provider-bmad.md`、`provider-openspec.md`、`provider-speckit.md`、`bmad-review-dispatch.md`、`bmad-review-oneshot.md`、`recovery.md`、`verification-setup.md`，以及旧 `sdd-acceptance-*` 文档的 legacy 标记与适用范围。

不得保留以下默认指导：在原生 review 前必须配置 CAO task acceptance；verify 后必须运行独立 acceptance；SDD in_review 等于 CAO review；所有验收配置变化都必须全层 restart；为修复元数据重新 intake。

### 11.2 建议提交边界

1. `fix: separate native execution from CAO conformance stages`：P0/P1 与相应测试。
2. `refactor: consume native completion evidence and project baseline`：P2/P3 与无循环依赖测试。
3. `fix: scope evidence invalidation and recover active legacy work`：P4/P5 与恢复测试。
4. `docs: align host guidance and validate provider completion flows`：P6/P7、演练与验收记录。

提交名称为建议，不要求切成多个独立发布；避免中间提交单独上线导致新旧契约混用。

## 12. 测试与验收矩阵

测试验证行为与边界，不仅断言函数返回某个常量。优先扩展现有测试族，复用 provider fixture；实际 provider 的格式/步骤须来自已支持版本的原生契约。

| ID | 场景 | 必须观察到的结果 |
| --- | --- | --- |
| A01 | SDD 从实现进入 in_review | CAO 保持 implementation；显示原生评审子状态 |
| A02 | 评审发现问题并修复、再次评审 | CAO 不提前 review/verify，原生评审层按真实输入刷新 |
| A03 | SPEC done 但 story review；或一个单元 done、另一个未完成 | 仍执行中，准确定位剩余单元 |
| A04 | 全部已绑定单元及原生必需步骤完成 | CAO 进入证据链 review |
| A05 | 仅执行 workflow complete、仅勾选 tasks、仅手改 status | 缺少应有执行依据时不得取得完整完成资格或关闭 |
| A06 | 原生报告真实存在、格式为 Markdown | 绑定并读取原生结论，无需 Agent 再写 CAO JSON 功能验收报告 |
| A07 | 报告缺失、空白、篡改、跨 work、错误代码版本 | 合规阻塞并给出精确原因 |
| A08 | 仅补绑定原报告 | 合规重查；原生有效评审与基线结果保留 |
| A09 | 没有 CAO acceptance executor/checks.json | 新契约可完成全流程；原生验收义务没有被取消 |
| A10 | 原生仍 in_review 时调用正式 verify/done | 拒绝提前推进；SDD 自身测试命令仍可执行 |
| A11 | 合规检查在首次 verify 之前 | 不依赖验证通过，不能形成循环 |
| A12 | `mvn test` 输出普通日志且成功 | 由项目 runner 正常记录，不按独立验收 JSON 解析 |
| A13 | 基线失败、零测试、工具缺失、超时 | 保持 failed/blocked/error，不以 no-op 或重复标签伪造通过 |
| A14 | 产品源码/测试在评审后改变 | 对应原生、合规、基线证据失效 |
| A15 | 仅基线包装器改变 | 基线失效；仅在它也是原生证据依赖时影响该原生证据 |
| A16 | 合法状态字段变化或纯 commit | 不使语义证据无故全失效；完成状态按实际更新 |
| A17 | SDD done 后新增任务或重新打开 | 撤销推进资格，保留历史并继续正确原生步骤 |
| A18 | prepare 精确注册后写验证输入 | 允许该路径；未注册/越界/软链接路径仍拒绝 |
| A19 | 旧 work 卡在 CAO 二次 acceptance | 保留 ID 和已做工作，可迁移恢复；缺原生证据不可绕过 |
| A20 | 旧 completed work、重复迁移、迁移中断 | 历史不变、幂等、可恢复且不丢新事件 |
| A21 | 两会话并发、旧 run 迟到 | 不能覆盖新 generation 或错误推进状态 |
| A22 | BMAD dispatch/oneshot/full planning 与现有 stories/sprint 路径 | 保留每条路由原生义务，没有只修一种路径 |
| A23 | OpenSpec split changes/归档、Spec Kit split features/checklists | 当前 work 范围完整、按各自契约完成，不强加 BMAD 语义 |
| A24 | light/standard/comprehensive | 证据深度可不同，职责与阶段规则一致 |
| A25 | 新项目 bootstrap 与已有 work 恢复 | 仍先准备项目骨架和基线，保留 PR #28 修复 |
| A26 | 一个失败输入重复 next/sync | 输出稳定恢复动作，无重复 intake、无无限 acceptance/review 循环 |

优先回归测试族：`test_native_route_sync`、`test_native_sync_binding`、`test_lifecycle_completion_matrix`、`test_lifecycle_repair_boundaries`、`test_conformance_knowledge`、`test_plan_scope`、`test_plan_completion_gate`、`test_bmad_stable_compatibility`、`test_review_convergence`、`test_review_round_integrity`、`test_project_context_acceptance`、`test_sdd_acceptance_*`、`test_verification_*`、`test_snapshot_*`、`test_host_*`、`test_project_verification_bootstrap`、`test_protocol_consistency`、`test_release_packaging`。按修改实际影响选择运行，再完成仓库规定的总检查。

## 13. 本次登录实验的复演脚本

1. 以当前支持的 Pi + BMAD 版本建立隔离测试项目，记录版本、安装包来源和脚本路径。
2. Bootstrap 完成项目技术栈、骨架和基线；在第一个 work 之前能看到项目验证配置。
3. 创建登录 work，使用原生规划、审批、实施、测试和评审步骤。
4. 在 SDD in_review 时读取 bootstrap/next/status，三者均显示 CAO implementation；正式 verify 被正确阻止，但 SDD 测试和修复可运行。
5. 完成原生评审及修复，确认关联范围和全部原生必要收尾完成。
6. 绑定原生规划、评审、验收、完成来源；执行 CAO conformance check。
7. 执行正式项目基线；Maven 日志由基线 runner 处理。
8. `cao done` 成功，最终没有默认 CAO 功能执行器配置、AC JSON 映射、重复功能评审或手动修改 managed state。
9. 再准备一个“原生已完成、旧 CAO acceptance 失败”的 work fixture，验证迁移恢复可以闭环，且所有原始证据/失败历史保留。

真实 Pi/provider 环境若不可用，仍应完成可运行的 CLI、状态机、适配器和安装包测试，并将实际会话演练明确标记未完成。单元测试不能冒充真实会话演练。

## 14. 完成标准与交付物

修复完成必须同时满足：

- 当前 main 的错误 `in_review → CAO review` 映射已消除，所有推进入口一致。
- 原生完整完成是 CAO review 的前置条件，多单元/多路径有覆盖。
- CAO 合规检查不再次进行功能代码评审，也不依赖本轮基线通过。
- 正式 CAO verify 仅执行已批准项目基线；功能验收消费 SDD 原生证据。
- 主路径没有重复 acceptance executor/逐条测试 JSON 配置要求。
- 精确失效与合法恢复覆盖本次会话循环，产品变更仍能使必要证据失效。
- 旧 work 可恢复，历史 work 可解释，项目初始化与基线前置无回归。
- 技能、引用、CLI、host 和界面表达一致；测试和演练记录可追溯。

交付：实现提交、契约/迁移说明、更新后的技能与双语使用文档、针对本次场景的自动化回归、实际演练记录或清晰的未执行项、最终验收清单。后续提交/推送/PR 按用户届时授权执行。

## 15. 核对来源

- [设计基线提交](https://github.com/wymix-tech/coding-agent-orchestrator/commit/c9b98e2025425c4975642f8c73b89504ab0075d7)
- [原生状态同步](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/cao_native_sync.py#L797)
- [阶段派生](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/cao_work_store.py#L233)
- [完成判定](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/cao_completion.py)
- [合规检查](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/conformance.py)
- [双验收汇总](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/acceptance_summary.py)
- [验证入口](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/cao_verification.py)
- [精确验证文件授权](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/scripts/managed_verification.py)
- [当前完成指南](https://github.com/wymix-tech/coding-agent-orchestrator/blob/c9b98e2025425c4975642f8c73b89504ab0075d7/references/completion.md)
- 上传会话 ZIP：见第 2.1 节的文件名和原始行号。本文未将用户实验的版本与设计基线混为一谈。
