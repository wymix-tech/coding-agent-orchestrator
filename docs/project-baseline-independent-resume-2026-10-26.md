# 项目基线恢复路径补修

基于已合并 PR #27 的 main `68d508c`。

## 上次遗漏

PR #27 前移了新建 Work 的准备条件，但没有完整覆盖已有 Work：

- `project_prerequisite` 在存在 Work 时跳过 `project_state`；缺失声明还会返回 `legacy_baseline_missing/invalid` 的 ready 状态。
- `resume --reactivate` 没有项目准备前置检查，直接恢复状态和注册 Work 会话。
- `verification_setup_context` 与宿主恢复上下文仍包含需求、SDD、决策、进度；模型容易把当前 Work 作为全局验证脚本的编写依据。
- 旧的准备记录没有证明使用了项目独立的编写上下文，升级后会继续复用。

## 当前行为

创建和恢复 Work 都要先完成项目准备。缺失/无效基线不再因为已经存在 Work 而绕过；`bootstrap`、`next`、宿主提示统一进入 `owner: project` 的准备阶段。`resume --reactivate` 在检查通过前不恢复业务状态、不进行原生业务状态协调、不注册新 Work 会话。

基线编写上下文只包含确认过的项目技术、布局、身份值、验证声明与待恢复 Work 的 ID/status。需求、验收条目、Work 决策、交接摘要和 SDD 业务上下文不作为脚本编写输入。已有标准的 Work 绑定/批准单独标为 adoption，不能据此重新编写任务专用脚本。SDD 继续负责业务行为测试和当前需求验收。

准备策略升级为 v2，`context.preparation_policy` 返回 2。旧记录需先按项目技术、清单和构建/测试/CI 入口复核，并实际运行项目级检查。原 Work、已做进度和脚本文件均不被自动清空或覆盖。

```bash
cao bootstrap
# 按返回的项目准备动作复核/修复全局脚本与配置。
cao verification check --project --scope root
cao bootstrap
cao resume --work <id> --reactivate
```

多模块应使用实际作用域。项目技术迁移可额外用 `--work <id>` 选择挂起的迁移所有者作为上下文权限来源，结果仍归项目所有，Work 需求不成为验证基线的编写依据。

脚本继续存放在 `.orchestrator/scripts/verification/`。已有项目根指引不会被包升级覆盖；需要时通过 `cao instructions update --host <host> --apply` 更新 CAO 托管区块，再按命令提示重载宿主，保留自定义内容。

## 验证

- 全量按 88 个文件执行 1061 项测试，4 项跳过；最后一个失败是交付迁移测试仍缺少真实项目准备步骤。
- 补齐该步骤后，最终定向执行 74 项测试全部通过，包含该文件全部 40 项、项目基线 17 项、setup 12 项、验证指南 5 项。按各测试最后一次结果汇总为 1057 通过、4 跳过，无未解决失败；没有声称全量单次调用从未失败。
- 新回归验证：已有 Work 基线缺失时上下文不包含业务验收标记，next 同样不泄露 SDD 业务字段；挂起 Work 不能提前恢复或注册会话；声明存在但未检查时仍不能恢复；旧准备策略必须复核。
- Python compileall、git diff --check、TypeScript 检查通过。
- Pi 适配测试仍为 28/37 通过，9 个失败名称与 PR #27 记录完全相同，没有新增失败名称。

逐文件与复测记录见 `project-baseline-resume-validation-2026-10-26.json`。外部 provider 的真实模型端到端验收不在上述测试声明内。
