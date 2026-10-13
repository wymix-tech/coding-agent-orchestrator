# CAO light 路径与 Pi 会话修复方案

日期：2026-10-13  
源码基线：`wymix-tech/coding-agent-orchestrator`，`fix/cao-integrity-repair`，`8b144f646d1365d50206db0814b1e3b359de53d5`  
证据：`2026-10-13T00-59-23-709Z_01a132c5-74bc-7605-870b-6a6ac1e7ea64.jsonl`，242 条记录  
状态：用户已授权当前会话顺序实施；未推送。日志中的 L 为 JSONL 行号。

## 1. 本轮决策与范围

用户已接受：light 在 SDD 侧仅检查最简 SPEC 和明确验收标准；项目配置的必需验证仍须通过且新鲜。取消 light 对原生 workflow select/execute/complete、计划绑定或 no-plan 声明、独立 SDD 收尾验收的硬要求。

standard/comprehensive 保留原生流程、关联计划、对应评审门禁，并继续完成已规划的独立 SDD 收尾验收。CAO 不解释自然语言验收语义，不调用 LLM 判定代码是否满足需求，不新增统一覆盖率门槛。

本轮决策取代下列旧文档中“所有 profile 强制工作流登记和双验收”的部分；其事务、路径、证据和执行安全设计保留：

- `docs/superpowers/specs/2026-10-12-sdd-final-acceptance-design.md`
- `docs/superpowers/plans/2026-10-12-sdd-final-acceptance.md`
- README、DESIGN、SKILL、两份宿主模板中的对应叙述。

当前开始实施本方案。实施第一步同步这些文档，防止另一个 Agent 继续执行旧的 all-profile 计划。已完成的 `sdd_acceptance_policy.py`、`sdd_acceptance_files.py` 保留，供 standard/comprehensive 使用，不为 light 强制要求 executor、run 或逐项验收报告。

不改异机登录应用；不自动安装依赖、推送、合并、发布或委派子代理。实施时使用本机实际 checkout 路径，不照搬旧计划的 macOS worktree 路径。

## 2. 完成契约

| 条件 | light | standard | comprehensive |
|---|---|---|---|
| Work 为 active、无 blocker | 必须 | 必须 | 必须 |
| 当前需求与有效路径评估 | 必须 | 必须 | 必须 |
| SPEC 与明确验收标准 | 必须，允许同一文件 | 由原生规范及验收声明绑定 | 由原生规范及验收声明绑定 |
| required 项目验证通过且新鲜 | 必须 | 必须 | 必须 |
| 原生 select/execute/complete 登记 | 可选记录，不是完成门禁 | 必须 | 必须 |
| 关联计划完成、无计划声明 | 不要求，不为关闭补造计划 | 按当前计划契约 | 按当前计划契约 |
| profile/risk 对应评审门禁 | 保留有效评估中的适用门禁 | 必须 | 必须 |
| 独立 SDD 收尾验收 | 不强制 | 必须，待接入 | 必须，待接入 |

light 的关闭公式：

`active AND no_blocker AND requirement_current AND assessment_valid AND light_spec_valid AND configured_verification_fresh AND applicable_gates_satisfied`

有效风险评估仍由现有规则升级 profile；声明 security 等硬风险不能维持 light。缺失 workflow、历史 workflow 失败、unresolved plan 本身不阻断 light。需要保留的用户义务应体现在当前需求、项目检查或 blocker 中，不能依赖旧流程记录隐含承担。

`done` 只检查并关闭，不在后台运行 Skill、安装工具、修改 SPEC、自动重锚或增加计划。保留当前关闭前后两次检查；若配置的原生 transition 改动文档或代码，第二次检查必须拒绝过期验证，要求重验。

### 2.1 SPEC 与验收标准的最低检查

- 必须绑定到当前 Work 与 Provider；只读取明确引用，不扫描任意同名文件作为依据。
- SPEC 位于当前 Provider 的真实原生输出目录，存在、可读、为普通文件且有正文；仅标题、空白、frontmatter 不满足。
- 标准可与 SPEC 同文档，也可为另一个 Provider 原生文档。不强制 PRD、架构、Epic、Story 或 tasks。
- 标准所在区域至少一个非空条目。带标准的未勾选 checklist 可接受，因为 light 只检查标准存在，不要求填写人工 passed。
- 不从“status: done”“实现说明”或任意正文推断标准存在；不把产品源文件、普通运行日志、报告声明当标准。
- 拒绝路径越界、Provider 目录逃逸、目录或缺失文件、读失败；沿用现有真实路径检查。
- 此检查只证明标准存在，不能证明标准充分、覆盖全部用户意图或实现正确。

### 2.2 最小引用接口（已实现）

已增加一个单次引用命令，不引入 light acceptance configure/begin/submit 流程：

```bash
cao sdd spec bind --spec <provider-spec.md> \
  [--criteria <provider-criteria.md>] [--criteria-section <exact-heading>] \
  [--work <id>] [--replace] [--repo <path>]
```

确定参数语义：

1. `--criteria` 缺省为 SPEC。与 SPEC 为同一路径时，必须提供 `--criteria-section`，避免把 SPEC 任意正文误认成标准。
2. `--criteria` 为独立文档时，省略 section 表示其全文为标准区域；提供 section 则仅校验该区域。
3. section 为 Markdown 标题文本，按标题层级界定区域；必须唯一，缺失或重复返回明确错误，不猜语言或标题。
4. 校验标准区域至少一项列表、checklist、非空表格数据行或非空普通段落。代码块、HTML 注释、标题及模板占位符不充当条目；纯机械规则不判定语义质量。
5. 相同绑定及文件字节不变返回 unchanged；更换已有路径、区域或文档字节需 `--replace`。首次合法绑定不要求用户额外确认。
6. 在现有 Work 的 `sdd.spec_binding` 保存 version、requirement_sha256、provider、spec/criteria 引用与 section；不新增状态库、评分、事实图或验收 run。继续沿用 artifacts 引用机制展示，新增字段只承担两个角色与区域的明确关联。
7. 绑定操作使用现有事务、revision 校验和原子存储，保留决策/会话/交接/进度。绑定修改纳入技术输入摘要，使旧 verification stale；不重新创建 Work，不静默接受变动的需求或技术标准。
8. SPEC 变动后返回具体重绑命令；`artifact refresh` 不能偷偷更新 spec_binding。需求变化或 Provider 更换时清除此绑定。重锚技术标准不自动重绑 SPEC。

示例最简文档：

```markdown
# 配置文件用户登录

## 目标与边界
按已确认的语言和测试框架实现登录，以会话维护登录状态，用户来自配置文件。

## 验收标准
- 正确凭据登录成功，可用会话访问已确认的受保护功能。
- 错误凭据登录失败，符合已确认的错误响应方式。
- 用户数据从配置文件读取。
- 使用用户选择的测试框架执行项目验证。
```

```bash
cao sdd spec bind --spec _bmad-output/implementation-artifacts/spec-login.md \
  --criteria-section "验收标准"
cao verify
cao done
```

示例不新增 Web/CLI 形态、超时策略等用户尚未确认的要求。现有 BMAD oneshot 允许简化文档但删除 Tasks & Acceptance；Agent 应把明确标准保留在原生 SPEC 中或附上原生标准文档，不能假定模板天然满足 CAO 的标准检查。

### 2.3 验证新鲜度与兼容

技术 required 检查不因 light 而减少。继续绑定需求、代码、规则、verification 配置及 assessment revision。轻量绑定摘要纳入 rules_snapshot，确保绑定路径、criteria section 等保存在 .orchestrator 的变更也会使技术证据过期；SPEC/标准文件仍留在现有代码快照中，不排除整个 Provider 目录。

只在已配置 spec_binding 时扩展相关输入计算，减少无关接口变动。对于准备关闭的 light，缺绑定明确失败，不把无绑定旧 verification 当作新契约通过。

已 completed 的任务仅保持历史记录，不自动重开、不按新规则重新认证。旧 active light 只需显式绑定 SPEC/标准并重验，不强制补造 workflow 或计划。有效 light 评估下的旧计划/流程记录继续保留和展示，不参与 light 关闭硬判定。

从 standard/comprehensive 改为 light 必须走显式 path assess 和理由，不能修改 profile 字段直接降级；新 revision 使旧验证失效。保留需求、blocker、决策和历史。不自动删计划，也不将缩减业务范围误当简单重评估；实际范围缩减仍需用户确认并更新需求。

## 3. 日志问题与责任边界

| 编号 | 日志/源码证据 | 修复方向 |
|---|---|---|
| R01 | L222 未绑定计划阻止 light done，L227 事后补全勾选计划 | light 独立完成分支，不要求计划或 no-plan |
| R02 | L33–43 接受并尝试不存在的 bmad quick-fix | 使用安装资产发现与 CLI/Skill 类型指导；不凭 Provider 字符串证明工作流存在 |
| R03 | L143 合法 uv 渲染成功，L204 登记被拒；本机复现 | 支持 uv run --no-cache，同时保持对占位/动态入口的拒绝边界 |
| R04 | L136 用户换为 bmad-build，L206 仍记录 quick-fix | 换流程重新 select；有可解析 Skill 身份时检查所选与执行一致 |
| R05 | execute --allow-unverified 未进入 lifecycle；本机复现 | 统一 select/execute/complete 例外审计，持久事件队列与幂等 |
| R06 | L19 JUnit5 被 L112–114 自写 main 替代 | 指导保留用户选择；缺依赖 blocked/请求授权，不自动降低框架要求 |
| R07 | L164 要求独立 reviewer，L185 主 Agent 自写 findings | 原生流程与新 standard/comprehensive 验收指导不得用自审文件冒充独立审查 |
| R08 | L209 才配置验证，L215–218 自动安装 JDK | 骨架/包装器配置在功能开发前；环境检查和安装授权由项目/Agent处理 |
| R09 | L214 reason 显示编译提示，stderr 才是真正错误 | failed/blocked/error 优先有效 stderr，passed 优先 stdout |
| R10 | L212 active 重锚被描述为“错误完成后重开” | 区分 active reanchor 与 completed reopen 的措辞和下一步 |
| R11 | L220 verify 提示直接 done，随后流程/计划阻断 | 验证提交后复查统一 completion，并提供当前可执行下一步 |
| R12 | L241 关闭后 exit 0 触发“请 start 新任务”提醒 | 完成后纯退出/展示命令不引导新建需求；保留真实产品写提醒 |
| R13 | 简体中文配置生效但 SPEC/问题仍英文 | 模板和入口指导重申偏好；不以自然语言检测新增完成硬门禁 |
| R14 | 登录交付形态未澄清，安全风险未声明 | Agent 在评估前确认用户可感知选择、明确风险；保留现有风险映射，不引入关键词语义打分 |

R06/R07/R08/R13/R14 有模型指令遵循因素，不能仅靠增加 JSON 字段保证解决。CAO 核心仍不解析 JUnit/Maven 报告、不自动证明 Skill 或 reviewer 真正运行、不自动把“登录”关键词升级为最高 profile。

四项 main 测试在异机实际运行通过，不能写成“零测试”或“伪造全部测试”。它们没有满足用户选定的 JUnit5 要求，且覆盖范围有限。异机缺 JDK 是环境事实；错误路径和替换标准是 Agent 偏差；合法入口误拒、审计遗漏和 light 门禁偏重是核心问题。

## 4. 分阶段实施任务

按 P0→P1→P2→P3→P4→P5→P6→P7 顺序执行，不派发子代理。每步先用相关失败场景建立回归，再最小实现、定向测试和适用全量检查，独立暂存与提交。

### P0：统一修订设计与计划

- [ ] 更新旧双验收设计与实施计划，删除 light 强制独立 SDD 验收和 workflow 完成门禁；保留 standard/comprehensive 的运行状态、CLI、精确报告边界及门禁任务。
- [ ] 更新 README、DESIGN、SKILL、AGENTS/CLAUDE 模板，统一完成矩阵、简体中文示例和最简操作顺序。
- [ ] 标注本轮仅设计审批，不把旧文档测试数字当作新策略验证。
- [ ] 更新 ROADMAP 中已不一致的完成契约范围。

### P1：light SPEC 绑定与完成策略

建议文件：新增 `scripts/light_spec_policy.py`、`tests/test_light_spec_completion.py`；窄改 `scripts/cao.py`、`protocol/work-item.schema.json`。新模块 <500 行，复用 path_guard、现有正文读取/文件 hash 与事务。

拟定接口：`validate_binding(repo, work, binding) -> dict`、`binding_digest(binding) -> str`、`light_spec_state(repo, work) -> dict`。返回 ready/reason/error 和具体下一步，不另建框架生命周期。

- [ ] 新增 spec bind 路由与绑定结构；支持同文档 section 和独立标准文档。
- [ ] completion_status 按当前有效 profile 分支。light 跳过 workflow_ready、provider_pending_plan_items 和独立 acceptance 硬检查；保留公共条件与技术新鲜度。
- [ ] 不通过旧 assessment.required_gates 强制套入未来 sdd_acceptance；重评估与所有默认门禁保持一致。
- [ ] spec_binding 摘要进入技术输入；需求变化/Provider 变化失效，绑定替换不丢无关更新。
- [ ] done 第二次检查仍可捕获 transition 改动，失败时保持 active。

必须验证：无 workflow、无 tasks、无 no-plan 的 light 可关闭；缺/空/只有标题的 SPEC 拒绝；缺标准、空区域、重复标题、只代码块/注释拒绝；同文件非空未勾选标准可接受；文件越界/链接逃逸/读失败拒绝；技术 failed/error/stale 不能关闭；文件/绑定/需求/评估变化使证据失效；standard/comprehensive 不因 light 分支绕过其门禁。

### P2：真实入口发现、解析与流程替换

文件：`scripts/cao.py`、`tests/test_native_skill_guidance.py`、`tests/test_workflow_execution_integrity.py`、`tests/test_pi_session_repairs.py`。

- [ ] workflow_command_identities 为 uv run 增加明确支持的 `--no-cache` 无值选项。保留值选项位置解析，未知/动态表达式走明确例外，不扫描参数里的 bmad 字符串当命令身份。
- [ ] 已安装 Skill 模式输出具体 SKILL.md 路径，指导完整读取并跟随其真实渲染入口；不猜 skill/scripts 下的脚本。
- [ ] select 若声明 Agent Skill，须存在与 Provider 对应的安装资产；不存在返回实际发现的候选与可执行选择命令。已识别为 Skill-only 的 BMAD 路径不要指导不存在的 quick-fix Shell 子命令。
- [ ] 不维护跨版本猜测的全部 CLI 子命令列表。真实 CLI/workflow 能力无法确认时标明未验证；用户明确的自定义入口继续通过有理由的例外记录。
- [ ] 不将 render_skill 的退出 0 表述为整条 BMAD 构建流程成功。render 是加载前置，execute 是 Agent 的流程执行登记，仍不属于核心实际运行证明。
- [ ] 用户改选后必须重新 select bmad-build，清旧完成证据。解析得出不同 Skill 身份时拒绝串用；无法机械证明时给精确诊断，不能只比字符串原样相等。
- [ ] light 保留原生 Skill 指导，但不强制 CAO workflow 登记；standard/comprehensive 完整登记路径仍需上述修复。

回归：日志中的两条 uv 命令、invoke skill bmad-build、Skill 不存在、换流程、合法 Shell/runner 前置、占位/echo/mkdir、Provider 字符串仅出现在输出参数、动态命令、跨 Provider 入口。

### P3：统一例外审计

文件：`scripts/cao.py`、`tests/test_audit_recovery.py`、`tests/test_workflow_execution_integrity.py`。

- [ ] select/execute/complete 新接受的例外都入现有 durable pending_events，Work 与事件绑定当前 requirement/provider/workflow。
- [ ] execute 的入口/登记例外与 complete 的非零执行例外分别记录；旧理由不能替代本次失败理由。
- [ ] 一次操作故障恢复只追加一次事件；不同操作可以记录同一理由的新事件。
- [ ] 审计字段区分 Agent 声明与真实命令执行结果，不宣称已核验 Skill 运行。
- [ ] 历史 unverified 没有 audit event 时不伪造过去事件；按需追加明确的历史缺失诊断。

回归必须完整覆盖 execute --allow-unverified →普通 complete 的日志路径、保存中断恢复、两次接受例外、旧 Provider/需求不能借用例外、空理由拒绝。

### P4：验证引导、诊断和下一步

文件：`scripts/verification_setup.py`、`scripts/cao.py`、两份模板与 SKILL；测试 `test_verification_guidance.py`、`test_verification_runner.py`、`test_host_event_gate.py`、`test_pi_adapter.mjs`。

- [ ] 明确分离用户需求约束与 CAO 工程指导，不把“测试必须最初失败”等指导擅自写成用户验收条款。
- [ ] 骨架、验证包装器与配置可先建立，不要求空工程立即通过；业务实现开始前配置有效基线。
- [ ] 对用户指定的 Java/JUnit5，指导真正的 Jupiter 测试及兼容 runner；工具缺失、零测试需包装器明确 fail/block，不能用 main 或 echo 悄悄替代。
- [ ] 环境 preflight 由项目包装器检查 command -v 等；缺工具先说明并获取所需安装授权，不自动 apt install。CAO 不新增内置 Java 自动安装器。
- [ ] 避免把同一脚本同时配置成 general 与 tests；如果同一脚本覆盖两者，只登记一次 tests 能力即可，不新增多余去重状态机。
- [ ] verification_reason 对非通过优先有效 stderr；保留完整有界 tails、原退出码和状态。127 可保留 error，包装器自行将缺工具映射 blocked，不由核心解析报错文本猜类型。
- [ ] verify 成功后复查统一 completion，返回真实阻塞或 done 动作；active reanchor 与 completed reopen 使用不同提示。
- [ ] 完成后 exit/纯展示不要求 start 新需求；真实新的产品写仍提醒。不得把含重定向/复合写入的 echo 统一列为 readonly。
- [ ] 提醒仍通过现有追加上下文/steer，不触发额外 turn、不改变原工具结果。

### P5：继续完成 standard/comprehensive 独立 SDD 验收

基于旧计划 Task 3—6 修订实施，不另起第二套运行策略。已完成的纯策略/文件边界继续复用。

- [ ] 实现 runtime、配置/运行/begin/submit CLI、冻结输入、显式重锚、长运行锁外执行、提交重读和并发保护。
- [ ] 两种 profile 都独立要求当前验收通过；不能由 workflow completed、勾选计划、manual gate 或 allow-unverified 替代。
- [ ] 命令模式由 CAO 真实运行，Skill 模式按当前 run 接收逐项结果；后者仍是提交证据，不证明模型诚实执行了独立审查。
- [ ] 逐项结果完整覆盖声明，证据/原生输出按真实字节 hash；新失败不能复用旧成功。
- [ ] 精确输出排除边界同时应用于技术与 SDD 输入，拒绝输入冒充报告，不能排除整个原生目录。
- [ ] 技术标准和验收来源变化仍需显式审核；light 不因没有 acceptance declaration 而 blocked。
- [ ] 原生工作流指定的独立 reviewer 或人工回传路径必须遵循，不能把主 Agent 自写 findings 冒充 reviewer 输出。完成报告准确区分真实执行、Agent 声明和未验证事项。

### P6：展示、协议、迁移和安装一致性

文件：Dashboard Python/HTML、work-item schema、README/DESIGN/SKILL、模板、installed_package_smoke.py。

- [ ] light 显示“规范/标准有效”“项目验证”；独立 SDD 验收显示“不适用”，不误显示 pending/failed。
- [ ] standard/comprehensive 显示技术与独立 SDD 两种状态；只有 Work completed 才显示已关闭。
- [ ] optional workflow/plan 历史继续显示，不成为 light 未完成标志。
- [ ] schema 支持 nullable/未绑定历史、spec_binding 与既有 acceptance 记录；不把旧证据迁移成新通过结果。
- [ ] 新模板不会覆盖已有 AGENTS/CLAUDE 文件；已安装旧模板需要给出明确差异/人工更新说明，不能假装 bootstrap 已自动替换内容。
- [ ] wheel/editable 包包含新模块，checkout 外执行 light 最短路径及两种完整路径；Provider/Host 夹具必须明确标为模拟。

### P7：收尾验证与可交付证据

- [ ] 新增日志驱动的集成场景：用户选框架→有效 light SPEC/标准→真实项目测试→无工作流/计划登记关闭；另有框架不可用不静默替换的指导/包装器回归。
- [ ] 覆盖现有两次 done 复查、并发更新、审计恢复、单 active、保护路径、Session/工具结果保留。
- [ ] 执行适用 Python 全量、Node 适配器、严格 TS、compileall、Shell 语法与 diff 检查。
- [ ] 临时安装验证与文档声明一致；真实 Pi、浏览器、不同平台未运行的保持未验证，不借用模拟结果签字。
- [ ] 使用异机新会话复测；应记录 CAO SHA、Pi/Node/Python/Provider 版本、项目验证包装器和实际收集测试数。旧日志无 SHA，不能声称同版本复现。

## 5. 综合验收矩阵

| 场景 | 预期 |
|---|---|
| light，无 workflow/plan/acceptance run，但规范绑定和技术验证有效 | done 成功 |
| light，未声明 no-plan | 不为此阻断 |
| light，SPEC 为标题占位或标准区域缺失 | done 失败，返回准确修复命令 |
| light，明确标准为未勾选 checklist | 可满足结构要求，仍需技术验证 |
| light，项目 required 脚本失败、missing tools 或 verification stale | done 失败 |
| light，SPEC/标准/引用区域/需求/代码/评估变化 | 原技术证据不能复用 |
| light，有历史 unresolved/failed workflow | 保留展示，不作为完成硬门禁 |
| light，有实际 blocker 或有效风险要求更高 profile | 不允许轻量关闭 |
| standard/comprehensive，缺流程、关联计划未完成或独立验收缺失 | done 失败 |
| 验收命令非零、逐项缺失、旧 run、输入变化 | 不能认证通过 |
| uv run --no-cache 的真实原生脚本 | 正常识别，不因参数误用例外 |
| 入口只有 bmad 字符串却无原生调用身份 | 不伪认证 |
| quick-fix 未安装却按 Skill 选择 | 返回可用 Skill 与正确路径 |
| workflow 改为 bmad-build | 旧选择/完成记录不混用 |
| execute 接受例外再普通 complete | 审计事件存在，恢复幂等 |
| stdout 只有编译提示，stderr 报 javac 缺失 | 摘要呈现 stderr 错误 |
| active 重锚 | 不声称过去已完成 |
| 修改/绑定/验收期间并发 decision | 决策保留，旧输入不能关闭 |
| 历史 completed | 保持历史，不自动重开/重新认证 |
| 完成后 exit，或纯展示结果 | 不误提示新建任务；真实写入仍提醒 |

## 6. 实施检查命令

以下是已有入口，不构成本轮已运行结果。新文件测试名以最终实现为准。

```bash
python3 -m unittest discover -s tests -v
npm run test:pi
npm run check:pi
python3 -m compileall -q scripts tests
sh -n hooks/host-event.sh
git diff --check
```

运行前确认 Python >=3.10、Node >=22.18.0 及已有依赖；缺依赖先报告，不自动全局安装。新专项先定向验证，再运行上述完整检查。失败必须说明原因并修正，不能调整断言为泛失败来掩盖原问题。

## 7. 当前证据和完成边界

已完成分析：242 条日志读取；当前分支关键代码/设计/计划阅读；31 项现有 SDD policy/files 测试通过；临时夹具复现 uv --no-cache 误拒、execute 例外漏审计、无明确标准仍能通过当前完成流程、错误摘要 stdout 优先问题。

这些证据说明旧问题及修复目标，不证明本方案已实现。四项异机手写 main 测试的通过结果不等于 JUnit5 验收；本机夹具不等于真实 Pi/BMAD 工作流验收。后续实施应分别提交实现、正式回归与实际验证报告，不自动推送或发布。

## 2026-10-13 实施进度

P0—P6 已实现并完成对应定向回归；P7 已完成：533项全量静态复验、最后50项兼容回归、Pi的13项测试和类型检查通过，wheel/editable各18个安装组合通过。新增模块是 `light_spec_policy.py`、`sdd_acceptance_runtime.py` 和 `sdd_acceptance_commands.py`。标准/完整路径实际采用 command 或已安装Skill执行；所有完成/门禁/Dashboard结果复用同一核心状态。历史completed不重开，重开后的独立run被清除；显式降为light保留旧流程/验收历史，但不让其缺失来源成为light额外门禁。

本轮是同一checkout的集成修复，使用一个本地集成提交交付，不把历史计划的各阶段提交名误报为已创建提交。未推送、合并或发布。最终数字与环境记录见 `docs/repair-validation.md`。
