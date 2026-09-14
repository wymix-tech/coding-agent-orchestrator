# 可直接交给另一个大模型的完整修复提示词

把以下正文与基线压缩包一起交给模型即可；正文不依赖本轮聊天记录。

```text
你是一名负责修复现有 Python 开发流程协调器的工程师。请直接修改附件中的完整源码，完成下面列出的稳定性修复、测试和交付。不要只返回建议。

【基线与交付范围】
使用 coding-agent-orchestrator-v6.5-unified-authorization.zip。
SHA-256：1c5e6413f6496787bb3bbef239076af361c3321eb8d310903e8033a2b315e938。
顶层目录 coding-agent-orchestrator，115 个文件，上一轮回归 172 项通过。
如果我同时提供了明确更新的版本，先检查差异并保留其修复；不要用旧包覆盖新改动。
本任务交付修改后的完整源码 ZIP，包含源码、文档、Schema、模板、示例和测试。我没有要求安装为个人技能，也没有要求修改真实项目或发布服务。压缩包交付本身已授权。

【必须保留的设计】
1. scripts/action_guard.py 是“允许执行/推进/关闭”的唯一判定器；collect_evidence 读取事实，evaluate 做纯函数判断，authorize 组合二者。
2. CLI、状态迁移、宿主 Hook、start/resume 消费这一个判定结果；不得在适配器重新写一套许可条件。
3. 保留 repository_snapshot.py 的共享材料分类和 tool_actions.py 的输入归一化。新增生成目录必须接入同一分类，不能污染业务代码快照。
4. 保留缺少状态时允许需求准备、生产代码修改受限、活动 blocker 过滤、NEEDS_EVIDENCE/部分分析拒绝推进、实时输入校验和受记录的连续编辑窗口。
5. 保留门禁/评审/最终验证的执行及内容快照绑定。不得用旧通过状态、缺失计划项、required=false、重试耗尽或 finish_role 绕过关闭。
6. 保留原生 SDD 与项目 Policy 的权威，不凭空决定架构、质量阈值或低风险。记录证据不等于实际执行了验证。

【工作方法】
先阅读 SKILL.md、MANIFEST.json、references/action-authorization.md 以及当前任务涉及的代码；参考资料按需加载。核对附件和现有测试基线。
下列旧缺陷在原包中已复现，相关代码在本次交付包中仍保留；修复前为当前版本建立失败场景。每项按“复现 → 最小修复 → 反例/成功路径回归 → 文档”推进。
保留现有 172 项测试的覆盖意图，不通过删除测试或减弱断言掩盖回归。若协议迁移需调整旧断言，说明其理由。
按 T1–T3、T4–T6、T7、最终 T8 的顺序实施；T8 的回归随各项持续补充。一个外部环境不可用不能阻止其余本地修复，最后明确未验证项。

【T1 / P1：状态与历史的事务提交】
重点：scripts/execution_state_manager.py 的 _commit、_dump、历史写入、初始化与读取恢复。
当前版本的 revision 检查、覆盖状态、追加历史不是原子提交，两个进程可都成功却丢更新，同名 .tmp 也会竞争。
建立跨进程协调和可恢复提交。可用锁加事务日志或适合现有架构的方案；不要把线程锁或单个 os.replace 当成多文件事务。锁内不要运行 CBM 或测试等长操作。
验收：两个进程从同一 revision 更新不同字段时，一方明确冲突或重载后串行成功，无丢失更新、重复 revision 边或重复事件；在状态/历史写入各阶段注入崩溃，恢复后是完整旧版或完整新版；锁超时和持有者退出行为明确。保留公共存储接口或提供迁移。

【T2 / P1：工作项身份、需求修订和产物隔离】
重点：scripts/coding_orchestrator.py 的 _ensure_state、cmd_intake、归档，以及 semantic_intake_pipeline.py。
当前存在状态就复用，新的 request/work_id 却覆盖固定 intake 产物，旧验收可能属于另一需求。
区分重新分析、需求修订和创建/切换工作项；建立稳定 requirement/work-item 身份及 revision。产物按工作项/修订隔离，在暂存区处理并通过一致提交切换引用。
验收：A 活动时显式 intake B 必须明确冲突或进入明确切换操作，拒绝时不能先改 A；同一修订重跑幂等；需求或验收范围变更使相关验收、任务完成和证据失效；中途失败、并发 intake 不混用产物；A 关闭后新增 B 不自动撤销 A 的历史完成或复用 A。区分历史完成事实与当前关闭准备，不能借此放松当前实时证据检查。

【T3 / P1：Policy 语义快照与来源】
重点：scripts/policy_engine.py 的 route/load_policy、context_plane.py 与 action_guard.py 的来源绑定。
当前 policy_snapshot_id 仍主要 hash 规则 ID、上下文和路径；新增 authority_hashes 只缓解了部分失效问题。
快照覆盖有效规则内容、约束、level、scope、required、命令、override/优先级合并结果及相关来源内容。规范化必须确定，不能丢掉有意义的顺序，绝对目录位置不能充当规则内容身份。
验收：同 ID 改规则、命令、阈值或 required 会改变快照；追踪 manifest 实际引用的 pack/override，包括默认目录外的来源；删除或不可读会失效；Context/分析/验证计划/门禁使用一致版本；外部 ECC 指导不会因被索引自动变成项目 MUST。

【T4 / P2：SDD 显式选择持久化】
重点：scripts/project_bootstrap.py、bootstrap_guard.py。
已有 config 时 init --sdd 目前可能只报告选择成功而保留旧 unresolved。
对显式选择做字段级合并，只清除对应歧义，保留项目自定义 Policy/host/其他配置，不要求 --force。
验收：OpenSpec/BMAD 并存 → 初次 init 有歧义 → init --sdd openspec → 后续 start 不再被同一歧义阻塞；输出与落盘一致，重复调用幂等；已有任务的原生归属冲突必须明确处理。

【T5 / P2：历史需求去重】
重点：scripts/start_router.py、requirement_discovery.py 与归档/身份记录。
当前只比较最后一个 completed work ID 与候选正文的默认 hash。
建立基于稳定 requirement ID、revision、native 状态和历史处理记录的去重。
验收：A 关闭归档、B 关闭、再次 start 不重开 A；自定义 work ID、native ID、进程重启仍有效；同身份新修订有明确处理；不同身份的相同正文不误合并。正文 hash 只能辅助标识修订，旧数据依据不足时保守迁移，不猜测完成或自动重启。

【T6 / P2：BMAD 工作项发现】
重点：scripts/state_provider_detector.py、requirement_discovery.py。
需求发现目前主要扫描 _bmad/.bmad/bmad 安装目录，与能发现输出目录 sprint-status 的 detector 不一致。
使用配置和已识别 native 状态定位真实工作项、身份和状态，排除模板、示例及安装资源。
验收：发现 _bmad-output/implementation-artifacts 中的 sprint-status 与 story，也支持明确配置的输出位置；安装模板不是候选；完成项被过滤；多候选和未知格式返回有来源的选择/定位结果，不用关键词猜一个任务。明确记录支持的格式版本。

【T7 / P2：验证义务与执行结果的生命周期】
已有离线流程表明：合成影响数据误报跨项目边界后，即使纠正数据，旧必要门禁也可能永久 pending。当前拒绝关闭是正确的，需要补充有依据的变更路径。
将 obligation 的稳定 ID、来源、适用工作项/修订和状态，与运行结果/证据分离。
计划变化不能无痕删除义务；真正不再适用时，通过项目规则认可且包含原因、依据、来源与审计记录的变更处理。区分 passed、被替代和获准不适用/豁免，不能伪装为测试通过。
验收：合法变更可恢复工作流；直接改 required=false、漏掉计划项或缺少有效处置依据仍阻塞。生效义务统一由 action_guard 判断，旧任务义务不能污染新任务。

【T8：跨模块、CBM 与宿主验收】
1. 实跑空仓库到下一需求的完整链路：init → 需求准备 → intake → 解决证据缺口 → 实现 → 修改/重分析 → review → 必要 gate → 最终 verification → close → 新需求 → start。既验证成功关闭，也验证拒绝路径。
2. 对同一状态与证据，比较 check、pre_tool、transition、verify、Stop、start/resume 的结果；覆盖 blocker 解决、外部编辑、角色交接与恢复。路由成功不等于代码写入许可。
3. CBM 当前在任意非零退出后尝试 cli --raw，并可能只保留后一次错误。先依据目标安装版本的 help/能力和官方文档检查协议，区分格式不支持与业务调用失败，避免重复副作用或掩盖原始错误；补超时、非零退出、stdout/stderr、JSON、index_repository/detect_changes 的契约测试。不要凭猜测再加命令格式。
4. 使用真实宿主捕获的事件补契约 fixture，包括 cmd/command、原始 patch、失败工具调用、角色与停止行为。明确 CBM/宿主/OS 版本。离线 fixture 与真实集成结果分别报告，没有环境的项目标为未验证并附复现命令。
5. 成功路径的测试必须真实执行，结果与工作项/修订/快照对应；禁止伪造证据、删除门禁或使用错配项目身份的 fixture 拼出通过。

【验证命令与迁移】
在包根目录至少运行：
python3 -m unittest discover -s tests -v
python3 scripts/context_footprint_check.py --repo . --json
python3 ./coding-orchestrator --repo <temporary-project> --json check --action mutate_code
python3 ./coding-orchestrator --repo <temporary-project> --json check --action advance --phase review
python3 ./coding-orchestrator --repo <temporary-project> --json check --action close
说明各命令针对的状态和预期退出码，不能将拒绝用例的非零退出当作测试失败。
SKILL.md 保持延迟加载，并满足 110 行、7500 字节、1000 词的现有预算。
新状态字段、义务 ID、来源绑定或目录布局必须说明旧项目迁移方式。需要新证据时明确提示，不能静默补造通过状态；历史诊断与失败证据保留。

【最终交付】
- T1–T8 逐项列出：已修复/已验证/受阻/未验证，附对应复现和实际测试结果。
- 汇总根因、关键文件、行为变化、迁移步骤及已知限制；更新 README、README-zh、Schema、CHANGELOG、MANIFEST，测试数量使用实测值。
- 交付包含全部修订文件的完整 ZIP，提供文件名、SHA-256 与打包完整性检查结果；不只交 diff、报告或几个修改文件。
- 对不能运行的外部集成如实说明，不把这一限制扩展为停止其余已授权的代码修复和压缩包交付。
```
