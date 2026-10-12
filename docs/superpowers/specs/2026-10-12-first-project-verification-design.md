# 首次项目技术栈与验证基线引导设计

## 1. 目标与已确认方向

用户选择 **Agent 识别并生成项目脚本，CAO 前置引导并校验配置**，而不是在 CAO 核心维护各技术栈模板。
本设计把“没有配置验证脚本”的发现前移到 bootstrap/启动提示阶段，避免功能开发结束后才临时
声明 `mvn test`，却实际执行零测试。三项实机会话缺陷已另行修复，本功能不混入那些提交。

开工前应建立**已配置的验收基线**，不要求全新项目在创建骨架和第一组测试前已经验证通过。
产品写继续采用提醒模式；内部派生状态保护和 `cao done` 的既有硬约束不变。
本功能改善流程和提示，不承诺强制阻止 Agent 忽略初始化指导。

## 2. 现状证据

- `cmd_bootstrap` 在 SDD 就绪后直接选择需求或恢复 Work，没有验证脚本初始化阶段。
- `verification_scripts(cfg)` 已支持标准脚本对象和历史 command/commands 配置；应复用，不重新猜命令。
- `_initialize_state` 的 force 路径复制 DEFAULT_CONFIG，`cmd_init` 随后可能安装 Host 集成；
  不能让 Agent 用 `init --force` 作为常规脚本配置入口，造成语言/SDD/自定义配置被重置。
- 实际异机 Maven 返回 exit=0 / Tests run:0；核心不理解第三方业务测试语义，项目脚本须承担质量判断。

来源：当前 `3327976`，图谱 generation `2026-10-12T08:36:21Z`，已检查相关覆盖并核对精确函数。
覆盖仅 best-effort，不据“无记录缺口”声称源码或运行场景完备。

## 3. 流程与职责

1. CAO 保留语言、SDD 显式选择/安装授权和 Host 安装提示流程，不在识别技术栈时自动安装任何 Provider。
2. SDD 就绪后，bootstrap 先检查验证配置；缺失、仅有 optional 脚本或脚本声明非法时，返回
   `mode: verification_setup` 和具体原因，而不是默认返回“选择需求后立即开工”。
3. Agent 识别产品工程：阅读构建清单、源码、已有测试及 CI，不以目录是否完全空来判断新项目。
   不把 `.orchestrator` 派生状态、`.agents`、`.pi`、其他 Host 资源、SDD 工具/输出和依赖缓存
   当作产品构建证据；当前需求文档和用户明确选择可作为需求依据，不因存放在 intake 目录而忽略。
4. 既有工程：优先复用项目实际测试/CI 命令和脚本；发现缺失或虚假的验证时，说明证据并提出补充。
   清单足够明确就自行分析；多模块/混合栈按各模块组织脚本，存在重大选择歧义时询问用户。
5. 全新工程：用户已给出技术栈则采用，不重复询问；否则先询问语言/框架与影响构建、测试的选择。
   无询问渠道时明确返回待用户决策，不擅自选默认栈。
6. Agent 在仓库的普通产品路径生成真实验证脚本，或复用已满足约定的现有脚本；CAO 配置通过 CLI 更新，
   禁止 Agent 手改 `.orchestrator/config.json`。可先创建必要骨架/测试配置，不伪造业务测试通过。
7. 配置确认后再次 bootstrap，然后选择/录入需求、start、评估并执行原生工作流。
   bootstrap 不自动创建 Work；既有 Work 不因为新 Session 或该引导而换需求。

CAO 不调用 LLM、不分析 Maven/npm/pytest 报告、不维护技术栈模板；不在 bootstrap/configure 阶段
自动执行项目初始化或验证。用户显式调用 `cao verify` 仍照常运行配置脚本。
Agent 负责读项目、询问选择、生成脚本并解释验证覆盖；用户负责技术栈和有风险变更的决定。

## 4. 配置就绪与验证通过分别定义

配置就绪：沿用历史兼容提取和 normalize_scripts，至少有一个非空、合法、required 的配置脚本。
脚本姓名、required 布尔和 gates 仍按既有规则校验。仅 optional 检查不能替代必需验收。
`gates: ["tests"]` 仍需显式声明，不靠命令名称或 stdout 关键词推断。

已有合法配置不改写、重编号或自动补 capability；可报告 tests 能力是否绑定，提醒 Agent 审阅是否符合
该项目/路径需要。只有一般检查的历史配置不被偷偷当作业务 tests，也不因本功能强制升级历史完成记录。

配置就绪不表示脚本存在性、依赖可用性、测试数量或业务行为已经通过验证。缺工具、环境或真实测试时，
脚本必须失败或给出明确阻塞，而不是 fallback 为 echo/true/空操作成功。

项目生成脚本必须保证零业务测试不能作为 tests 成功，采用项目实际框架支持的非空测试约束/报告检查；
Java/Jupiter 场景应使用支持 JUnit5 的 Surefire 和零测试失败配置。这个约定写入 Agent 指导，
不是在通用 Python 核心中硬编码 Maven 输出解析。文档类项目可只配置有意义的一般检查，不虚构 tests。

## 5. 专用验证配置 CLI

增量新增，不替换现有 init/verify：

```bash
cao verification configure --test-script '<project test wrapper>' --verify-script '<general check>'
cao verification configure --replace --test-script '<reviewed replacement>'
```

两类参数均可重复；至少一个脚本，沿用现有 script-i/test-i 名称规则、required=true，只有 test-script
声明 tests。命令只更新 `verification.scripts` 及信息性 configured_at，不改变其他配置字段和 timeout。

- 无配置/空列表：在仓库事务内读改写，使用现有原子 JSON 写入。
- 提交与当前规范化配置完全相同：返回 unchanged，不改时间或配置文件。
- 存在不同的非空配置：默认拒绝覆盖并展示差异/replace 提示；只有显式 --replace 才替换脚本列表。
  非法但非空的旧配置也不得静默覆盖，须显式 replace。
- 参数非法：返回结构化错误和非零退出，不留下部分配置。
- 不安装 Host、不运行项目初始化/验证/依赖命令，不改项目清单/脚本文件、不启动需求或修改 Work。
  沿用公共仓库定位逻辑的只读 `git rev-parse` 不属于项目执行；不新增外部执行步骤。
- 存在 active Work 且标准变化：返回显式验收复核/reanchor 提示，不自动清门禁或重锚。
  suspended Work 在恢复时仍由既有标准绑定检查发现变化；completed 历史不被重认证。

该命令加入已有独立 CAO 治理调用白名单；仍不豁免混合 Shell 和手改派生状态。

## 6. 运行提示与兼容

- bootstrap 输出新模式及 next/action，保留适用的 Work、Session、Host、语言和 SDD 上下文。
- Session start / prompt 的模型上下文包含相同初始化提醒；不只显示 UI 通知，不额外触发模型 turn。
- 缺配置时产品写仍 allow 并提醒；内部状态 write deny 仍优先处理，不因初始化未完成而放开。
- start 的既有兼容行为不新增硬阻断；若绕过 bootstrap 导致 Work 已绑定空标准，提醒通过专用命令
  配置后显式 reanchor，不自动补造验收锚。完成仍受既有配置来源、门禁和新鲜度要求约束。
- AGENTS/CLAUDE 模板和 SKILL 更新首次阶段顺序；不覆盖用户已有文件，不要求读取 CAO 内部实现。
  已有项目即使保留旧模板，也能从运行时 bootstrap/模型上下文获得提醒。
- 不把“曾经有配置文件”当作已完成初始化；旧项目空脚本也需提醒，重复 bootstrap 幂等。
- 不强制修改语言、已选 SDD、用户定义字段、timeout 或已有效的旧脚本。

## 7. 实现边界与验证

沿用 JSON、现有事务/原子写入、normalize_scripts、验收 hash 和 Host additional_context。
新增聚焦的小模块承载验证 setup 的纯状态/脚本声明构造；CLI 只路由和持锁更新，避免继续堆大块业务逻辑。
Python 核心标准库、Python >=3.10、新代码文件 <500 行；不引入技术栈检测依赖或新的独立状态库。

正式回归必须覆盖：
- 无脚本、optional-only、非法声明、有效 required 脚本、旧字符串/command(s) 的配置识别。
- bootstrap 在 SDD 授权边界后优先引导；已有有效配置及 active Work 正常恢复。
- 配置写入保留语言/SDD/timeout/未知字段；重复不写、不同配置需 replace、失败不部分提交、并发不丢更新。
- 新配置不触发 Host 安装/外部进程，不修改 Work；已有锚变化仍显式 reanchor，历史 completed 不重认证。
- 首次全新工程指导明确询问未给出的栈；已有工程指导明确读产品清单并复用测试，忽略工具资源。
- Pi 模拟桥调用真实 Python 核心，提示进入模型、不额外 turn；状态保护和前次三项回归保持通过。
- 配置了脚本但尚无测试时仍允许骨架/测试编写，verify 失败不伪造 ready/done。

全量 Python、Node 契约、严格 TS、compileall、Shell/diff 再验；环境允许时明确标注安装夹具。
真实宿主/浏览器/跨平台仍按原延期任务补证，不把结构测试当作所有技术栈脚本的业务质量证明。

## 8. 非目标与审批状态

不处理双安装重复扩展，不修另一台机器的登录应用或自动升级其依赖，不合并、推送或发布。
不保证任意模型必遵守指导，不用核心关键词规则证明任意测试脚本语义。
用户已确认本书面设计；已按对应实施计划在当前隔离 worktree 中顺序实施，不使用子代理。
本机 CLI/指令契约和 wheel/editable 安装夹具已验证；真实宿主/模型压力测试/浏览器/跨平台
不据此判通过，详细证据与限制见 `docs/repair-validation.md`。
