# 原生路线协商与进度同步

CAO 的 `light / standard / comprehensive` 决定质量门禁。BMAD 的完整规划或直接 Build
决定原生产物与执行步骤；Build 内部再选择 `dispatch / oneshot`。两者分别保存。
直接 Build 可以保留 standard。路线接口不能修改档位、删减门禁或替代原生审批。

## 使用顺序

1. 创建或恢复 Work，建立首次验证基线，完成 `cao path assess`。
2. standard/comprehensive 选择实际原生工作流，读取安装的 Skill 和原生步骤。
3. 执行原生规划，创建 SPEC。立即 `cao sdd artifact bind --artifact <spec>`。
4. 根据原生规则提交路线声明。CAO 校验证据来源、指纹、引用与需求身份。
5. dispatch 展示完整规格摘要及路径，`cao sdd approval request`，等待用户明确批准。
6. 原生状态转换后 `cao sdd sync`。宿主 post-tool、状态查询、恢复及仪表盘也执行同步。
7. 完成全部原生实现与审查后，按现有 workflow execute/complete、verify 和验收接口关闭。

运行时 `bootstrap`、`next`、start/评估响应与宿主提醒共用当前动作的前置顺序：先恢复 Work、
处理 blocker/验证基线，再评估路径，最后准备原生工作流、SPEC 和路线。`NATIVE_ROUTE_REQUIRED`
表示业务实现仍受门禁约束，不代表必须跳过工作流/SPEC 准备直接提交一个声明。
`guidance.read_before_action` 返回当前阶段指南、契约和实际已安装的 native Skill 路径；应先读取再执行。

BMAD light 无须为了路线声明制造完整 workflow 记录；默认原生工作流身份为
`bmad-build` / `invoke skill bmad-build`。其他 provider 也可以主动记录自己的路线声明；
当前自动强制路线协商的能力检测针对已安装的 `bmad-build`。

## 声明格式

以下示例中 hash、版本、文件、引用和理由必须来自本次真实需求与安装，不能照抄占位值。

```json
{
  "provider": "bmad",
  "provider_version": "6.12.2",
  "requirement_sha256": "<当前 Work 需求 SHA-256>",
  "profile": "standard",
  "workflow": "bmad-build",
  "route": "direct-build",
  "build_route": "dispatch",
  "reason": "<原生路线判断及需求规模、风险、规划充分性的具体依据>",
  "primary_artifact": "_bmad-output/implementation-artifacts/spec-login-system.md",
  "required_artifacts": [
    {
      "path": "_bmad-output/implementation-artifacts/spec-login-system.md",
      "role": "spec"
    }
  ],
  "rules": [
    {
      "path": "<实际安装的 Skill 或渲染步骤路径>",
      "sha256": "<该文件 SHA-256>",
      "quote": "<支持本次判断的原文片段>"
    }
  ],
  "omitted": [
    {
      "name": "<省略的独立规划文档或职责>",
      "reason": "<为何允许省略>",
      "replaced_by": "_bmad-output/implementation-artifacts/spec-login-system.md"
    }
  ]
}
```

`required_artifacts` 必须已存在且位于 provider 产物目录。full-planning 声明还需包含
`prd`、`architecture`、`epics`、`sprint` 角色。共享 sprint 的当前需求范围仍需显式使用
`cao sdd plan bind --plan-item <path>#<id>` 绑定；不能由路线声明猜测全部项目属于当前需求。
版本值是执行者声明，规则文件路径和内容指纹是可验证证据；CAO 不声称自动判断理由的语义正确性。

`--declaration` 只接受**已存在的 JSON 文件路径**，不是 JSON 字符串。先执行 `cao next`，
使用当前动作返回的 `declaration_path`：BMAD 为 `_bmad-output/cao/WORK_ID/route.json`
（将 WORK_ID 替换为实际 Work ID）；其他 provider 使用其原生输出目录内的同类 Work 路径。
这些文件由 Agent 撰写，不属于 CAO 托管状态；原生输出写入在业务实现被拦截时仍可进行。
根目录的 `route.json` 不享有此准备通路。写入失败不代表文件已创建，必须检查工具返回值。
现有合法声明文件仍可按原路径读取；此次不扩大项目根目录 JSON 的写入权限。

```bash
cao next
# 使用 next 返回的真实文件路径，替换下例的 WORK_ID；先成功写入完整声明：
cao sdd route propose --declaration _bmad-output/cao/WORK_ID/route.json
cao sdd route show
cao sdd approval request
cao sdd approval show
cao sdd sync
```

BMAD `route` 仅为 `direct-build` / `full-planning`，`build_route` 仅为 `dispatch` / `oneshot`。
`backend-api`、`full-stack-web-app` 不是原生路线名。路径正确也不代表声明有效：需求身份、
原生 workflow、真实规则引用/指纹以及已生成产物都必须通过既有校验。

## 决策与审批证据

接受的声明写入 Work 的 `sdd.route_history`，并以 `route_decision` 引用当前版本。
记录前序决策 ID、之前路线、需求/评估版本、档位/门禁、原生规则、产物及省略职责。
首次选择没有虚构的前序路线。每次接受新的路线清除旧审批、验证和手工门禁证据，
并使旧 workflow 完成记录失效。历史原样保留，重新运行才可以再次登记完成。

dispatch 在所有 Open Questions 解决后才进入审批；仍有开放问题时，CAO 保留先澄清的下一步，并拒绝审批请求。请求绑定当前需求、路线决策和 SPEC 的 frozen intent 指纹。
用户通过宿主**单独发送一条消息**，内容为 `批准并继续` 或 `批准并停止`（对应英文同样支持），不要在同一条消息附加解释；带其他说明的消息不会被当作批准。Pi 的
ask_user_question 结构化实际返回也可观察。CAO 保存原始回答、摘要、宿主、会话和时间。
审批请求同时绑定 Work ID 和已关联的主会话。新会话先接收当前 Work 的宿主上下文，
然后才可以批准；旧会话的迟到回复不会直接批准新任务。结构化提问必须由宿主先报告
pre-tool，再报告带相同 tool-call ID 的 post-tool；该调用与当时的审批 ID 绑定，
替换路线或审批请求后，旧提问的结果不能复用。验证基线批准也使用同一会话归属检查。
历史审批缺少这些绑定时，应重新 `cao sdd approval request` 并由当前宿主观察新批准。
旧验证基线请求可重复运行相同的 `cao verification configure` 重建；显式 reanchor
也会重建缺少绑定的 pending 请求，但必须观察到新的用户批准才继续，不继承旧批准。
`继续`、技术选项回答和状态字段修改均不成立。批准并停止保持暂停，用户明确
`继续实现` 后才解除。宿主事件是信任边界；本机制不能防御人为伪造宿主事件。
宿主未观察到审批时须保留 pending，不能通过 CLI 的理由字段补造批准记录。

## 状态与阻塞

原生 `in-progress` 映射到实现，`in-review/done` 映射到审查；done 不关闭 CAO。
多 Story/task 范围依照已有的需求级计划聚合。原生来源有状态但未批准时，记录真实
观察和实现阶段，同时显示阻塞原因，禁止业务写入、正式 verify 和 done。
构建清单、测试脚手架、需求和原生产物保留写入通路，避免初始化死锁。

Direct Build 检查路线绑定的 SPEC `Tasks & Acceptance`，全部任务完成后才允许 verify/done。
`--no-plan` 只声明没有独立计划文件，不能忽略 SPEC 内的任务。已观察的任务保留在
当前路线的 `spec_task_scope` 中，删除任务不能缩小验收范围；需求或路线真正变更时
重新提交证据。未关联的历史项目 sprint 文件不阻塞独立 Direct Build，显式绑定的
sprint 或路线声明的 sprint 仍按当前需求的所有 story 聚合检查。
oneshot 的独立审查与本地 Git 提交义务按实际 Build route 启用，适用于所有 CAO 档位。
dispatch 也必须登记当前渲染快照的全部原生审查层，不能用手工 implementation_review 替代。
原生阻塞解除时只清除 `native-sdd-state` 自动 blocker，保留人工 blocker 并记录解除事件。

修改需求、规则、路线或 frozen intent 必须重新评估/批准；单纯状态及任务勾选不使
批准意图失效。源文件消失或关联改变失败关闭，不自动采用另一份 SPEC。
已有 Work 可以补建实际绑定和当前路线证据，保留已有代码/进度；历史审批无法还原时
要求当前复核，不制造历史批准，不重复 intake。

新 Work 以空验证配置开始时，首次有效配置直接建立基线，不执行 reopen。
已有基线修改继续要求显式审核/重锚定；安装了 BMAD Build 时，须由宿主观察到用户
`批准验证基线`（approve verification baseline），不能仅靠 Agent 填写理由通过。active reanchor 保留业务阶段及开始时间，
清除旧验证证据并重算原生下一步。

## Pi 本地实验回归约束

合法的 CAO bootstrap/list/help、描述符重定向 `2>&1`、`echo "EXIT=$?"` 和受限 Git rev-parse
不需要先建立 Work。附带产品写入或写 CAO 托管状态仍会拒绝。门禁不是通用 Shell 沙箱。
BMAD 准备脚本只按受限身份、参数与路径放行：配置解析、原生 memlog、架构 lint、sprint 计划；
输出限制在 `_bmad-output`。不要因某个 helper 失败就宣告所有原生脚本禁用。

验证基线包含直接项目 wrapper 的内容指纹。同路径修改也属于标准变化；再次运行相同
`verification configure` 可建立新的审核请求，按宿主观察的 `批准验证基线` 重锚。
安全风险需显式声明能力，例如：

```sh
cao verification configure --test-script scripts/verification/test.sh --security-script scripts/verification/test.sh --replace
```

相同 wrapper 合并执行一次，覆盖 tests 与 security_verification；配置声明不证明其语义正确。

dispatch 审查流程：先为当前快照的每层 begin，全部返回后 classify，全部分类后才允许产品补丁，
原生检查通过后逐层 resolve。begin 的 instructions 使用真实 `step-04-review.md`：

```sh
cao sdd review begin --layer blind-hunter --instructions _bmad/render/<实际快照>/step-04-review.md --model <当前模型>
cao sdd review classify --layer blind-hunter --report _bmad-output/reviews/blind-triage.json
cao sdd review resolve --layer blind-hunter --report _bmad-output/reviews/blind-resolved.json
```

对渲染出的所有活动层重复操作，不得删掉活动层缩小义务。零发现提交空数组，不编造问题。
classify 报告形状：

```json
{
  "run_id": "<begin 返回的 id>",
  "input_sha256": "<begin 返回的 input_sha256>",
  "reviewer": {"mode": "subagent", "session_id": "<独立会话>", "model": "<当前模型>"},
  "findings_file": "_bmad-output/reviews/blind-raw.json",
  "findings": [{"id": "BH-001", "verdict": "medium", "disposition": "patch", "evidence": "实际核对的引用与结果"}]
}
```

原始 findings_file 是 JSON 数组，每项有唯一非空 id 和原始发现内容；分类必须逐一覆盖，
先裁决再去重。defer 只允许 `scope: outside-intent` 且 `violates_frozen_intent: false`；
违反冻结意图应显式标 `violates_frozen_intent: true` 并走 intent_gap/bad_spec 回环，不能直接延期。
这仍需审查者做语义判断；字段不能代替真实核对。
resolve 报告为 `{"run_id":"...","resolved_ids":["BH-001"]}`，列出该层全部 patch ID；
CAO 保留原始发现、分类和解决记录的指纹，并绑定修复后的代码。review round 不等于 loopback 次数。

安装的 step-03 默认要求子代理时，Pi 先观察完整读取原生步骤，再观察真正实现子会话的产品写入；
主代理不能直接替代可用的实现 handoff。SPEC 必须有 in-progress 和 baseline_commit（HEAD 或 NO_VCS）。
宿主报告无子代理能力时允许原生 fallback，定制 handoff 按实际渲染规则执行。
无法还原历史执行时使用已有 `--allow-unverified --reason` 审计例外，不能制造历史。
renderer 只加载指令，不能作为 workflow execute 的命令；会话运行记录实际 `invoke skill <name>`，
不得添加 `-> step-01..05` 之类伪终端指令。

full-planning 的 PRD/架构文档需完成原生 Finalize，不得用 draft 产物关闭；按安装能力保留 memlog
和 comprehensive 架构 review 文件。SPEC 与 sprint 进展不一致会显示冲突；真实 in-progress/in-review
可反映执行阶段，但不允许把未完成的绑定计划升级为完成，也不会自动改写其他 Story。

Pi 根据宿主 session header 的 parentSession 关联父子会话。子会话不参与主审批，也不生成主交接；
父会话文件不可读时仍保留 child 身份。
