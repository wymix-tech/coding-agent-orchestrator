# CAO 编排恢复与证据闭环修复提示词

将本文全文交给有仓库访问和执行能力的编码 Agent。本文是修复任务说明，实施时以当前代码核验为准。

## 任务与基线

你负责修复 `wymix-tech/coding-agent-orchestrator` 的编排卡点，完成代码、回归测试与验证说明。审查基线为 main 提交 `695173d14eeccc274015c39673d20df5b2711aa4`。先同步最新 main、阅读适用的 AGENTS.md 和项目开发约定，核对下述问题是否仍存在；不要覆盖后续已合入的修复。

从最新 main 建立统一修复分支 `fix/orchestration-recovery-closure`，按下述阶段形成可审阅提交。用户若另有明确分支要求，以用户要求为准。完成实现及验证后交付分支、差异和验证结果；发布、合并或直接更新远端 main 遵循当前会话授权。

目标：产品失败后能够继续修复；真实原生执行能够绑定、恢复和切换；参数纠正及幂等重试能够正常收敛；外部命令有明确的超时和恢复边界；同一次上下文评估避免重复扫描。

## 已知事实与边界

基线 Python 全量测试运行 1,197 项，1,193 通过、4 跳过；Pi 39 项通过，TypeScript 检查通过。这些是旧基线结果，不能当作修复后的验证结果。五个问题已有临时项目复现，说明原测试缺少跨模块组合覆盖。

CAO 是宿主驱动的控制面。`sdd workflow execute` 登记原生工作流执行；`lifecycle execute` 真正运行受控命令。`start --defer` 不包含后台自动调度。本次不引入新的调度器、第二套功能验收器或新的业务审批流程。

保留需求、scope、provider、run、generation、输入哈希与原始证据绑定。保留独立评审身份、原生任务完成、未知副作用处置、过期结果拒绝、Work 切换日志、受保护状态和历史审计。不允许通过弱化 gate、修改测试预期、伪造报告、删除失败记录或手改状态文件让流程通过。

修复必须兼顾默认 contract 2 与已有 contract 1；不强制重建 Work 或迁移所有历史记录。共享核心策略覆盖 BMAD、OpenSpec、SpecKit；宿主差异通过实际适配器契约验证，不能仅凭一个 fixture 宣称全部线上兼容。

## 阶段 0：建立可失败的复现

先建立下列五条行为回归，在未修改生产代码时证明当前仍受影响的场景确实失败。若某条在新 main 已修复，记录修复位置并保留相应覆盖，不重新实现。

尽量使用真实 CLI、真实临时项目和无网络的确定性测试命令。故障注入只用于构造异常或中断窗口，不 mock 掉待验证的状态判定。测试文件按实际模块边界组织，不将实现细节逐行映射成测试。

## 阶段 1：修复项目检查与产品修复的死循环（P1）

相关模块：`verification_probe.py`、`project_verification.py`、`project_baseline.py`、`cao_host_events.py`、`cao_guidance.py`、`native_execution.py`。

触发条件：项目基线已建立，存在活动 Work；产品改动导致测试失败，此时执行 `verification check --project`。失败覆盖项目准备状态，`project_state()` 返回 `PROJECT_VERIFICATION_CHECK_REQUIRED`，宿主拒绝修复产品的写入。

设计要求：

1. 分开表示“当前标准下已建立的项目验证能力”与“最近一次针对当前产品的执行结果”。两者都保留来源、时间和标准指纹；失败运行仍完整保留。
2. 在标准、项目背景和适用 scope 未改变且已有有效能力认证时，业务断言失败应进入当前 Work 的产品修复路径，不撤销其范围内的实现写入权限。
3. 不能简单忽略所有项目检查失败。首次基线从未建立、脚本缺失、标准变化、项目背景漂移等仍走准备／审阅／reanchor 路径；未知类型不自动视为产品失败或成功。
4. 产品／环境／基线／未知诊断复用现有恢复模型。若需要明确诊断，提供现有命令的可执行入口；不要新增通用审批。基于旧认证允许修复不代表新产品已经通过最终验证。
5. CLI next、resume、conformance 与宿主工具前检查消费相同策略，避免一个提示修复、另一个拒绝修复。
6. 历史兼容：已有 passed 准备记录可作为同指纹认证；已有 failed 记录不能凭空升级成功。仅从可信历史恢复认证，缺乏证据时给出明确准备或修复出口。

验收：准备一个基线测试读取 `feature.valid` 的真实项目，基线通过后启动 contract 2 Work，将产品改坏，执行项目检查失败，再通过真实 host-event 请求修复产品。修复应允许；未修复前 verify/done 不得通过；修复后按原生流程刷新证据并验证关闭。另测首次准备失败、标准改变、环境失败、未知失败、无活动 Work 和 contract 1。

## 阶段 2：统一原生运行的权威状态与恢复（P1）

相关模块：`native_execution.py`、`runtime_store.py`、`runtime_queries.py`、`runtime_finalize.py`、`runtime_commands.py`、`runtime_retention.py`、`recovery_plan.py`、`work_operations.py`。

触发条件：`lifecycle execute` 成功，结果保存在 `sdd.completion_executions`，但 `official_runs()` 未读取；查询把它认作 `result_uncommitted`，resume 失败，switch 的源任务未决检查也受影响。

设计要求：

1. 在现有模块内统一按 run kind 获取权威记录、当前运行、历史运行、generation 和终态。覆盖 verification、旧 acceptance、native_completion，不使用“所有非 verification 都是 acceptance”的分支。
2. 明确区分命令执行结果与完成资格：failed/stale/cancelled 等已知终态不是未知副作用，也不授予成功；abandoned 必须保留显式处置事实。
3. 正常原生执行完成后，即使缺失可重建投影，也不进入 unresolved。runtime rebuild 只能重建投影，不创造权威结果。
4. 仍在运行的执行、真正中断且效果未知的执行不能因映射修复被放行。旧失败和被替代运行不得覆盖当前有效运行，也不得掩盖较新的失败。
5. 审查 native run 的 finalize／abandon／retry 路径。优先复用现有机制：只有完整、可信、身份及输入匹配的收据才允许不重跑地提交；证据不足时明确拒绝并给出适用的处置命令。不要直接把 verification finalize 套给原生执行。
6. 历史上已保存的 completion_executions 应直接被正确识别，不要求重新执行。保留日志及失败历史，保留策略不得误删未决运行。

验收：真实 execute 成功 → bind → runtime show → resume → switch 到另一 Work → 返回原 Work。增加失败终态、取消、孤立 start、执行后提交前中断、generation 改变、投影缺失、旧运行被替代及并发回执的场景。确认未知效果没有被自动重放。

## 阶段 3：稳定原生命令与依赖来源（P1）

相关模块：`native_execution.py`、`lifecycle_contract.py`、`evidence_domains.py`、`native_reports.py`、`evidence_archive.py`。

触发条件：旧报告有 `Command: python3 .orchestrator/scripts/verification/root/check-<hash>.py`。真实执行刷新报告为不带 Command 字段的原始日志后，候选绑定丢失脚本依赖，出现 `NATIVE_COMPLETION_REEXECUTION_REQUIRED`。

设计要求：

1. 执行时固化规范化命令身份、实际命令、依赖清单／指纹、输入、run_id、generation 和报告哈希；复用现有收据结构，避免创建第二套运行状态。
2. execute 的执行前捕获、执行后校验及 bind 的候选计算使用同一套命令与依赖来源。来源以匹配的真实执行收据为准，不要求 stdout 自带 Command。
3. 报告原文保持原样，不添加伪造结果字段，也不通过去掉依赖校验消除差异。
4. 声明依赖、受管 wrapper 及其静态 helper 继续受到变化检测。跨 Work、过期 generation、失败运行、报告篡改、命令不匹配的收据不能被采用。
5. 多报告绑定按报告关联相应运行；明确较新失败如何阻断对应完成资格。不要盲目使用“最后一个成功运行”。
6. 兼容尚未有执行收据的旧绑定：保留已支持且输入未变化的登记修复；实质输入变化后的刷新仍要求当前执行证据。缺失来源时诊断，不补造来源。

验收：使用真实 materialized wrapper，旧报告带 Command，新执行输出不带 Command，execute 和 bind 均成功。再修改 wrapper/helper、产品代码、报告、generation，分别验证过期拒绝。覆盖重复字节输出、多报告、历史失败与其他 provider 的报告适配。

## 阶段 4：修复重试暂停与幂等清理（两个 P2）

相关模块：`completion_repairs.py`、`lifecycle_contract.py`、`native_execution.py`。

### 4A：纠正参数能够重新校验

复现：同一报告两次 `bind --gate made_up`，随后提交无错误 gate 的请求，仍被 `NATIVE_COMPLETION_REPAIR_PAUSED` 拒绝。

- 将请求校验错误与实际报告／来源修复失败区分；错误参数不应污染有效原生证据。
- 如果保留请求级暂停，签名应包含规范化后的完整语义请求，包括 report、gate、evidence 选择及关联输入。顺序或重复参数不应制造虚假“新请求”。
- 有实质纠正的新请求必须进入校验；相同坏输入的重复请求仍应有界暂停。
- 暂停输出说明错误对象、解除条件和下一条可执行命令；不能要求修改报告字节或重跑产品测试来解除参数错误。
- 兼容旧签名记录；仅按可信变化或有效请求重新校验，不全量清除失败历史。

验收：错误 gate 两次后改正确参数成功；同一坏报告重复仍暂停；只重排参数不能绕过暂停；报告实际修正后恢复；证据选择变化正确参与判断。

### 4B：证据幂等不遗漏状态转换

复现：有 product repair，当前输入下新执行成功且报告字节相同，bind 返回 unchanged，但 completion_repair 未清除。

- 区分“证据主体不变”和“修复状态需要推进”。通过收据校验后，即使证据相同，也要归档并清除已解决的产品修复、处理相关失败状态并持久化审计事实。
- 状态清理与绑定提交在同一受保护事务内完成；重试不得重复追加相同历史／事件。
- 无新有效收据、较新运行失败或证据过期时不得清除修复标记。

验收：确定性测试输出下，repair → execute → bind → next 不再返回 NATIVE_PRODUCT_REPAIR_REQUIRED；重复 bind 不重复归档；注入提交中断后可以恢复；没有合法新执行的请求不能解除产品修复。

## 阶段 5：外部命令执行边界（条件性运行风险）

相关模块：`cao_providers.py`、`cao_work_commands.py`、`runtime_execution.py`、`verification_runner.py` 及安装入口。

已知 inspect／transition 默认为空；启用后以及安装流程中的直接 subprocess 调用缺少超时。此项是静态风险，不能在验证记录中写成已有线上事故。

- 复用已有执行运输和进程组清理能力，为安装、inspect、transition 明确可配置超时、取消、日志上限与错误分类。安装可能长期运行，超时配置应区别于短查询，并校验非法值。
- 执行外部命令时不长期持有仓库写锁；采用 prepare／execute／guarded commit。处理无 Work 的安装和项目级运行归属。
- 保持原命令参数、cwd、必要环境和交互契约。对确需交互的安装给出明确入口，不静默等待无终端输入。
- done 的 transition 必须具备操作身份和中断记录。未知副作用不自动重放；已完成且有可信记录的转换不得重复运行。若无法判断，暴露明确恢复步骤。
- 默认未配置 inspect／transition 的行为保持兼容。

验收：正常退出、非零退出、启动失败、超时、取消、超大输出、子进程保留管道、执行中输入变化、transition 已产生效果但提交前中断。执行平台相关清理测试；未测试的平台明确列出。

## 阶段 6：减少同一次上下文检查的重复计算

相关模块：`cao_guidance.py`、`lifecycle_contract.py`、`cao_completion.py`、`evidence_domains.py`、`cao_snapshot.py`、宿主事件入口。

旧最小样例一次 render_context 约 0.42 秒，code_snapshot 调用 9 次、verification_inputs 6 次、project_prerequisite 6 次、capture 3 次。这不是大仓库基准。

- 在单次评估内显式传递只读评估结果，复用快照、前置条件、conformance 和 next 的中间结果；优先小范围改造，不重构整个状态机。
- 不引入跨命令长期缓存。外部命令执行、产品／配置变更、Work 状态转换后必须重新捕获。注意仓库锁不阻止外部编辑，不宣称单次缓存是文件系统事务快照。
- 新旧计算结果必须一致，诊断保持具体。下一步包含 owner、reason、阻塞对象和适用命令，减少“继续工作流”式泛化提示。
- 性能预算以测量确定：至少对小项目及较大文件集比较调用次数、耗时、扫描范围与输出大小；对宿主事件记录 P50/P95，关注 Pi 8 秒桥接超时与状态锁 5 秒等待边界。

验收：调用次数和延迟有实测改善，业务决策不变；同进程两次检查之间修改文件能够发现失效；执行中修改输入仍不能授予完成；多会话锁竞争与桥接超时测试不回退。

## 实施与提交计划

按依赖推进：

1. 核对最新 main 与约定，添加五条失败复现。
2. 修复项目准备与业务修复的状态边界。
3. 建立统一原生运行映射，打通恢复及历史读取。
4. 固化命令和依赖来源，打通 execute → bind。
5. 修复暂停签名及幂等清理。
6. 补齐外部执行超时与 transition 中断处理。
7. 在正确性已稳定后优化单次评估，做集成回归。

每阶段先运行相关回归，确认没有弱化保护，再形成独立提交。不要为每个简单内部辅助函数堆叠重复测试。涉及持久化格式变化时，在同阶段实现旧记录读取、故障恢复和版本兼容测试。

## 最终验收与交付

必须验证完整链路：项目准备 → start → 路径评估 → 原生任务／评审 → bind → verify → done；以及产品失败 → 原生修复 → 新执行 → bind → resume → switch 往返 → verify → done。测试要求既能完成正确流程，又能拒绝失效证据和未知效果。

至少运行：

- 新增缺陷回归及相关项目准备、生命周期、运行恢复、continuity、状态事务和宿主门禁测试。
- `python -m unittest discover -s tests -q`。
- `npm run test:pi`、`npm run check:pi`。
- Python compileall、`git diff --check`。
- 按仓库约定执行发布清单／安装包检查，尤其涉及新增模块、命令或资源时。

全量测试可按独立测试模块分片执行，但应保留 discovery 的 tests 导入路径，汇总准确用例数、跳过、失败与重复收集情况。不能把旧审查中的通过数写成新测试结果。命令失败后必须记录并解决或明确说明环境限制，不把未执行的线上 provider／宿主验收写成通过。

交付内容：

1. 每个问题的触发条件、根因、修复位置及修复前后行为。
2. 提交列表、分支及完整差异，指出持久化格式与历史兼容处理。
3. 测试结果表：执行命令、实际数量、通过／跳过／失败和环境。
4. 恢复操作说明：已有受影响 Work 如何继续；禁止要求用户手工编辑受保护 JSON。
5. 性能前后测量、仍存在的边界与尚未进行的真实宿主验收。

现在开始实施。完成所有已授权的实现与验证工作；遇到真正缺少的业务决策或外部权限时，只提出具体阻塞，并继续不依赖它的工作。
