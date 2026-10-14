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

```bash
cao sdd route propose --declaration route.json
cao sdd route show
cao sdd approval request
cao sdd approval show
cao sdd sync
```

## 决策与审批证据

接受的声明写入 Work 的 `sdd.route_history`，并以 `route_decision` 引用当前版本。
记录前序决策 ID、之前路线、需求/评估版本、档位/门禁、原生规则、产物及省略职责。
首次选择没有虚构的前序路线。每次接受新的路线清除旧审批、验证和手工门禁证据，
并使旧 workflow 完成记录失效。历史原样保留，重新运行才可以再次登记完成。

dispatch 在所有 Open Questions 解决后才进入审批；仍有开放问题时，CAO 保留先澄清的下一步，并拒绝审批请求。请求绑定当前需求、路线决策和 SPEC 的 frozen intent 指纹。
用户通过宿主输入 `批准并继续` 或 `批准并停止`（对应英文同样支持）；Pi 的
ask_user_question 结构化实际返回也可观察。CAO 保存原始回答、摘要、宿主、会话和时间。
`继续`、技术选项回答和状态字段修改均不成立。批准并停止保持暂停，用户明确
`继续实现` 后才解除。宿主事件是信任边界；本机制不能防御人为伪造宿主事件。
宿主未观察到审批时须保留 pending，不能通过 CLI 的理由字段补造批准记录。

## 状态与阻塞

原生 `in-progress` 映射到实现，`in-review/done` 映射到审查；done 不关闭 CAO。
多 Story/task 范围依照已有的需求级计划聚合。原生来源有状态但未批准时，记录真实
观察和实现阶段，同时显示阻塞原因，禁止业务写入、正式 verify 和 done。
构建清单、测试脚手架、需求和原生产物保留写入通路，避免初始化死锁。

修改需求、规则、路线或 frozen intent 必须重新评估/批准；单纯状态及任务勾选不使
批准意图失效。源文件消失或关联改变失败关闭，不自动采用另一份 SPEC。
已有 Work 可以补建实际绑定和当前路线证据，保留已有代码/进度；历史审批无法还原时
要求当前复核，不制造历史批准，不重复 intake。

新 Work 以空验证配置开始时，首次有效配置直接建立基线，不执行 reopen。
已有基线修改继续要求显式审核/重锚定；安装了 BMAD Build 时，须由宿主观察到用户
`批准验证基线`（approve verification baseline），不能仅靠 Agent 填写理由通过。active reanchor 保留业务阶段及开始时间，
清除旧验证证据并重算原生下一步。
