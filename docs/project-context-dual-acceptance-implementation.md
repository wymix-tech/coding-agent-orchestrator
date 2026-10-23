# 项目背景与双层验收实施记录

日期：2026-10-23  
原始基线：`main@5eab01b4301da5d08b71083ff52e26bf912637ec`  
实施分支：`feat/project-context-dual-acceptance`  
依据：CAO-Project-Context-and-Dual-Acceptance-Plan-2026-10-23，以及对应 M0—M8 实施计划。

本文是开发与验证记录。运行 CAO 的 Agent 使用 SKILL.md 和 references，不需要读取本文。

## 实施结果

技术栈现在属于项目背景，在没有 Work 时也能保存。Provider 安装和宿主重启仍优先；随后先恢复项目背景与验证声明，再创建或恢复 Work。空项目只需先保存验证计划，实际产品文件和测试脚手架仍由活动 Work 创建。

每个新 Work 绑定适用项目范围及公共约束。技术质量和当前需求/原生 SDD 分别计算证据有效性；两部分均通过且新鲜，原有批准、评审、原生任务范围和 Commit 等义务也满足后，done 才关闭 Work。

| 批次 | 实现 |
| --- | --- |
| M0 | 核对 main 基线；注册的项目启动器可被另一安装识别，仍保留副作用检查；独立修复提交 `cdf6095` |
| M1 | 版本化项目背景、根/模块范围、来源追踪、有界识别、不可变版本、提交指针、并发版本检查与恢复 |
| M2 | plan-only、真实入口配置、context_binding、已知工具不匹配检查、原有 review/reanchor；计划不作为执行结果 |
| M3 | bootstrap/next/宿主统一前置条件；start 继承、旧任务补绑定；原始请求仅在 Work 创建成功后消费 |
| M4 | 新技术输入与任务输入版本；按模块背景和公共约束判断；保留旧输入解释与原始运行，不重写旧 run |
| M5 | 只读双层聚合、完整阻塞义务、来源覆盖、light 行为验收、真实运行复用、统一关闭条件 |
| M6 | 旧 Work 身份/阶段保持；显式项目变更、迁移 owner、范围隔离；两层验收后稳定目标背景 |
| M7 | Pi、三 Provider、阶段资源、模板与 wheel 集成；运行上下文展示实际项目背景，而非只有指纹 |
| M8 | Python/Pi 回归、包安装场景、三 Provider 真实安装与真实 Pi RPC 启动；具体结果见验证节 |

## 主要实现落点

| 模块 | 职责 |
| --- | --- |
| project_context / project_sources | 项目模型、范围/语义指纹、提交与来源；宿主用户输入和绑定问答的来源验证 |
| project_inspection / project_commands | 有界清单识别、差异、无 Work 命令、历史、补绑定与受控迁移 |
| project_verification | 背景与验证声明的对应、统一前置谓词、已知工具冲突检查 |
| acceptance_inputs / acceptance_trace | 技术/任务输入分离；从原需求、原生规范与完整关联范围派生条目索引 |
| acceptance_summary | 两部分状态、新鲜度、来源覆盖、原有生命周期义务与恢复动作；不保存独立 passed 开关 |
| test_discovery / verification_runner | 从原始输出流提取常见测试运行器的数量记录，独立于日志尾部长度；识别零测试，保留真实进程退出码 |
| 现有 bootstrap / guidance / CLI / host / completion / acceptance 模块 | 接入统一顺序与谓词；保留原生流程要求、配置审查、运行身份与提交时输入检查 |

项目 context 的当前提交指针和完整不可变 revision 组成提交依据：先写 revision，再原子替换当前 context。中断产生的未引用 revision 不会被自动采纳；审计沿当前版本的祖先链恢复，不能把任意缓存文件当作已接受版本。

项目与 Work 的验收结果仍保存在原有 verification、sdd.acceptance 和运行记录内。聚合视图只读取这些结果；未执行、未覆盖、缺依赖、零测试、过期或运行错误不会转为通过。

## 可用命令

```bash
cao project inspect
cao project context show
cao project context diff
cao project decisions
cao project context set --help
cao project bind --work <id>
cao project bind --work <id> --refresh
cao project context change propose --help
cao project context change apply --change <change-id> --work <owner-id>
cao verification configure --plan-only --test-script '<planned command>'
cao start --requirement <path> --scope <module>
cao acceptance criteria --work <id>
cao acceptance configure --executor-command '<actual command>'
cao acceptance configure --executor-skill '<native skill>'
cao acceptance status --work <id>
cao acceptance reuse --script <verification-result-name>
```

project context set 接受宿主真实观察到的用户证据 ID、已建立的产品清单路径，或范围明确的用户委托。Agent 自写的 source 标签、reason、SPEC/POM 不构成授权；不能覆盖实质技术选择来跳过显式变更。

验收配置可从已绑定原生 SPEC/Intent 自动派生，不要求再手写重复声明。原生源未绑定时可显式传 --spec。高级调用仍保留现有 --declaration、run、begin/submit 协议；--output 使用原有受保护的 Work 范围输出规则。light 的正式原生验收阶段可以不适用，其实际需求验收仍须通过。

同一真实技术运行只有在其精确命令匹配已接受的任务执行入口、输出含完整条目结果、运行新鲜且证据合法时，才可复用为任务证据。复用保存原技术 run 的引用，不伪造第二次命令执行；V1 不复用其他 Work 的通过结果。

## 验证

验证环境：Linux、Python 3.12.14、Node 24.19.0、Pi 1.1.0。Windows/macOS 和其他 Python 版本需要由现有 CI 矩阵补充，不能将本次本地结果解释为已在所有平台执行。

最终验证通过：完整 Python 回归 873 项通过（498.098 秒），随后在专项回归中新增的纯文档范围场景通过；当前 874 项 Python 用例均有执行证据。项目背景专项最后重跑 40 项通过，Pi 适配器 36 项通过，仓库外 wheel 21 场景通过。

| 检查 | 结果与边界 |
| --- | --- |
| 干净 main 基线 | 828 项 Python 测试通过；在独立基线工作树执行 |
| 项目背景与验收专项 | 40 项真实 CLI 回归通过；包括并发、事务中断、来源、连续任务、light、复用和实际 ZIP 交付迁移 |
| 测试数量记录 | 4 项回归；含跨数据块、多个模块、真实执行和日志尾部截断 |
| 跨安装 | 2 项专门回归；注册启动器可达，重定向/复合命令仍受控 |
| Pi TypeScript | 仓库 check:pi 通过 |
| Pi 适配器 | 仓库 test:pi 36 项通过；真实 Python 桥接和离线恢复限制保持原契约 |
| wheel 安装 | 在仓库外独立环境执行；3 Provider × 3 宿主的 light/standard 共 18 场景，加 BMAD oneshot 3 场景，共 21 场景；原生内容为明确标注的本地夹具 |
| 真实第三方安装 | OpenSpec、BMAD、Spec Kit 均真实安装到独立临时项目；OpenSpec 实际 CLI 版本为 1.14.3 |
| 真实 Pi 启动 | 三个项目分别启动实际 Pi RPC 进程；get_state 正常、退出码 0、标准错误为空，均留下 CAO Session |
| 真实重启边界 | 三 Provider 安装后先返回 host_restart_required；真实 Pi 启动后返回 project_context_setup / PROJECT_CONTEXT_REQUIRED |
| 产品识别 | 三个真实安装项目均判定为空产品，Work 数为 0；不会把宿主/Provider 设施识别成产品栈 |
| 静态与技能校验 | compileall、diff 检查、SKILL frontmatter 校验及资源可发现性通过 |

真实 Pi 检查只请求 RPC 状态，没有发送模型开发提示词，没有执行在线模型开发/评审回合。因此本次验证证明安装、宿主生命周期和 CAO 路由可运行；不声称已验证在线模型在完整开发会话中的遵循能力。功能开发与双层验收路径由真实 CLI、真实命令和明确的本地原生夹具验证。

## 原方案验收标准的对应证据

| 原标准 | 对应检查 |
| --- | --- |
| AC01—AC03 | 无 Work 保存与计划、缺失背景阻断、完整/部分来源字段覆盖、Java 不隐式授权其他选择 |
| AC04—AC05 | 工具目录排除、真实三 Provider 空产品检查、产品 POM 来源检查及已有入口保留 |
| AC06—AC07 | 第二 Work 继承、请求保留、绑定问答、原有 restart 测试与真实 Pi 启动 |
| AC08—AC10 | plan-only 可 start 不可 verify、无 Work 写保护、已知工具不匹配拒绝及基线恢复 |
| AC11—AC12 | 幂等、补证据不改语义、大小写、模块指纹隔离、根级公共约束 |
| AC13—AC15 | 伪造/不覆盖/无绑定问答拒绝，旧 decision 不提升；补绑定保留阶段和旧 run |
| AC16—AC17 | owner 与范围隔离、实际 ZIP 构建与两层验收后稳定、并发 expected revision、损坏与中断恢复 |
| AC18—AC21 | 跨安装回归、现有命令副作用/离线限制、Pi 适配器和仓库外 wheel 场景 |
| AC22—AC25 | 运行身份与输入变化、技术/需求交叉不满足、代码或证据变化均阻止关闭 |
| AC26—AC28 | 标准继承不继承通过、单次真实运行复用、light 原生阶段不适用但行为证据必需 |
| AC29—AC30 | 原有 BMAD/OpenSpec/Spec Kit 完整原生范围回归、条目精确覆盖与部分覆盖拒绝 |
| AC31—AC33 | 需求单独变化可保留技术运行、风险/检查集合绑定、既有全量标准与审查路径保留 |
| AC34—AC35 | 配置/计划不替代执行、空操作测试命令、真实零测试输出及截断后数量记录、运行错误 |
| AC36—AC38 | 两层通过仍需原生批准/评审/Commit/无阻塞；报告哈希与提交检查；不适用维度由范围声明，产品任务两层不能整层跳过 |

这些是结构与行为契约的对应证据，不表示静态文本解析能够自动判断所有自然语言需求的语义正确性。实际断言、原生评审和明确来源仍为必要输入；自定义验证包装脚本应自行强制其完整的测试发现、环境和质量策略，不能以通用日志解析取代项目标准。

## 历史兼容与恢复

- 已完成历史保持原格式解释，不因新字段而重新认证或重开任务。
- 活动/暂停旧 Work 先补项目背景和 project_binding；不改 ID、重做 intake 或退回 discovery，不把旧结果改成已覆盖新背景。
- 原生规范改变与技术检查改变分别判断；Git commit、证据说明与时间戳不是技术语义变更。
- OpenSpec 已核验的归档迁移保留来源条目身份；真实内容或范围改变仍要求对应证据刷新。
- 技术迁移稳定标记不会额外改变技术语义指纹；回滚背景不等于回滚产品代码，不自动 reset 工作区。
- 发现损坏状态、并发冲突、工具缺失或真实失败时保留现有数据并给出恢复动作；离线桥接不增加任意写入能力。

## 交付边界

本次在实施分支形成可评审的源码、测试、协议与运行指引。未自动推送、建 PR、合并或发布。验证结果随源码提交保存。

机器可读结果见 [project-context-dual-acceptance-validation.json](project-context-dual-acceptance-validation.json)。
