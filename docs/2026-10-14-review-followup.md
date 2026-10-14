# 2026-10-14 分支评审修复

审查基线：`fix/cao-integrity-repair` 的 `242f978`。

| 问题 | 修复后的行为 |
| --- | --- |
| 跨任务审批串用 | SPEC 和验证基线请求绑定 Work、主会话；结构化提问结果还必须匹配该审批的原生 tool-call ID。迟到结果不重新绑定会话到当前活动任务。 |
| standard/comprehensive oneshot 漏审查及提交 | 原生义务优先从当前路线的 primary SPEC 识别，保留 legacy spec_binding 支持；所有档位的 oneshot 均要求独立审查，Git 可用时要求本地提交。 |
| Direct Build 未完成任务可关闭 | 当前路线的所有 SPEC Tasks & Acceptance 聚合到 verify/done 门禁，`--no-plan` 不忽略这些任务。已观察任务保留，删除或替换不能静默缩小范围。 |
| 无关历史 sprint 阻塞 | 已接受的独立 Direct Build 使用自己的 SPEC；只有显式绑定或路线声明的 sprint 进入当前需求的聚合检查。 |
| 切换失败挂起当前任务 | 先校验目标状态并准备需求、验证配置等证据；目标不允许启动或基线无效时保留当前任务、活动指针与交接历史。 |
| 原生恢复后保留自动 blocker | 健康原生状态清除 native-sdd-state，保留人工 blocker，并产生幂等的解除事件。 |

SPEC 任务读取放在 `scripts/native_progress.py`，主 CLI 负责串接状态与持久化。
同步更新工作项协议、路线契约与 Agent 指引。移除无仓库调用入口的四个旧函数、
未使用导入和局部变量，修正 heredoc 队列的元组类型及重复判断。

## 回归验证

新增 `tests/test_review_followup.py` 覆盖真实 CLI 和模拟宿主事件，包括：

- 跨任务迟到提问结果、旧会话输入、同一会话旧提问复用，以及迟到结果先于新请求到达。
- 验证基线审批归属、新会话先接收请求上下文。
- standard/comprehensive oneshot 义务，以及审查、验收、Git 提交后正常关闭。
- 未完成任务、删除/替换/新增任务、无关 sprint 和显式 sprint 关联。
- 原生阻塞恢复、人工 blocker 保留和解除事件不重复。
- 已完成目标及无效目标基线拒绝后当前任务和交接历史保持不变。

完整门禁使用 `python -m unittest discover -s tests -v`、Pi adapter 测试、
Python 编译与 diff 检查，并从 wheel 安装后在 checkout 之外执行 provider/host 冒烟场景。
这些本地 fixture 不替代真实宿主与真实模型审查的端到端验收。

历史审批缺少会话和提问绑定时重新请求，不恢复或伪造历史批准。
原生任务范围真的改变时提交新的路线/需求证据，保留旧决策历史。
