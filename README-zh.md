# Coding Agent Orchestrator

[English](README.md)

> Auto Bootstrap + Unified CLI + Adaptive SDD + Evidence + Semantic Impact + Engineering Policy + Context Plane + Runtime Enforcement

**Current version: V6.5**

## 统一执行、推进与关闭判定

所有许可判断统一由 `scripts/action_guard.py` 负责。宿主 Hook、状态转换、CLI 验证以及
start/resume 视图消费相同结果和原因码。可用以下只读命令检查当前许可：

```bash
python3 ./coding-orchestrator --repo /path/to/project --json check --action mutate_code
python3 ./coding-orchestrator --repo /path/to/project --json check --action advance --phase review
python3 ./coding-orchestrator --repo /path/to/project --json check --action close
```

退出码 `0` 表示允许，`1` 表示拒绝；结果包含 `allowed`、`reason_codes`、`reasons` 和
`next_action`。实际执行边界会重新检查。`verify` 只判断关闭条件，不运行测试或关闭任务。

共享规则检查未解决阻塞、已分类且完整有效的证据、SDD 就绪、阶段与角色权限、必要门禁与
评审，以及最终验证。门禁、评审和验证同时绑定执行快照与实际输入内容；即使分析编号未变，
需求或规则内容变化也会使旧结果失效。遗漏计划中的必要门禁、耗尽 Stop 重试次数都不能放行。

旧项目若缺少内容绑定，需要针对同一活动需求重新 intake、补齐事实证据、刷新上下文，并重新
记录适用的门禁、评审与最终验证结果。动作契约、连续编辑窗口、原生 SDD 权限和迁移细节见
[Action Authorization](references/action-authorization.md)。

### V6.5 稳定性与生命周期加固

当前 V6.5 还补齐了并发与长期工作项的控制面边界：

- Execution State / History 提交使用跨进程锁与可恢复事务日志；过期 revision 明确冲突，不再静默丢更新。
- Requirement Identity 与内容 Revision 分离。Intake 产物按 `.orchestrator/work-items/<work>/revisions/<revision>/runs/<run>/` 隔离；长时间分析若基于旧 revision，不能覆盖更新后的 canonical state。
- “历史已完成”与“当前是否允许 close”分离。后续新增需求或仓库变化不会把已经治理关闭的 A 重新拉回 active；但 native SDD 单独报告 `done` 仍不等于 Governance Done。
- Policy Snapshot 绑定实际生效规则语义和所有项目级引用 pack，包括默认目录之外的 pack。
- BMAD Discovery 以配置/检测到的 native sprint state 为入口；格式未知或 story artifact 缺失时返回带来源的 ACTION_REQUIRED，不从安装模板猜任务。
- Required Verification Obligation 在计划缩减时不会无痕消失，只能通过带 reason、authority、evidence 的 `superseded` / `not_applicable` / `waived` 处置；这些状态不会伪装成测试 passed。
- CBM CLI 调用有有界超时（`ORCHESTRATOR_CBM_TIMEOUT_SECONDS`，默认 120 秒）；超时、运行时失败和 JSON 契约失败不会再被兼容 fallback 掩盖。

迁移：旧活动 Work Item 若缺少稳定 Requirement Identity，不自动猜测，需要显式迁移或关闭。旧的 Orchestrator-owned `closed/completed` 保留历史完成事实；只有 native closed、没有 Governance completion record 的状态仍不算 Done。新生成的 work-item/registry 产物属于控制面，不污染业务代码快照。 详细修复状态、迁移说明、验证命令和外部集成限制见 [REPAIR_REPORT.md](REPAIR_REPORT.md)。

Coding Agent Orchestrator 是一个面向 AI Coding Agent 的工程控制面。它不替代 OpenSpec、BMAD、Superpowers、CI 或代码图谱工具，而是把它们组织成一条可审计、可恢复、可动态调整、可在运行时强制执行的开发链路。

它解决的核心问题不是“Agent 会不会写代码”，而是：

- 当前需求应该走多重的开发流程？
- 这个判断依据是什么？
- 项目现在开发到哪里？
- 本次修改会影响哪些模块、服务、契约和测试？
- 当前实现允许遵守哪些架构与编码约束？
- 当前 Agent 到底应该看到哪些上下文？
- Agent 是否真的被这些约束限制，而不是只在 Prompt 中被提醒？
- 最终什么时候才允许宣布 DONE？

---

## 1. 项目定位

Coding Agent Orchestrator 将 AI 软件开发拆分为多个职责明确的控制层：

```text
Project Bootstrap / Unified CLI
        │
        ▼
Requirement / SDD
        │
        ▼
Evidence-backed Work Facts
        │
        ▼
Deterministic Decision Engine
        │
        ├── TRIVIAL
        ├── FAST
        ├── STANDARD
        └── DEEP
        │
        ▼
CBM Semantic Impact
        │
        ▼
Engineering Policy
        │
        ▼
Context Plane
        │
        ▼
Execution State Manager
        │
        ▼
Session Bootstrap / Handoff
        │
        ▼
Host Enforcement Kernel
        │
   ┌────┼────┐
   ▼    ▼    ▼
Claude Codex Pi
Code
        │
        ▼
Superpowers / Coding Execution
        │
        ▼
Quality + Policy + Verification Gates
        │
        ▼
       DONE
```

各层职责保持严格边界：

| 层 | 负责回答的问题 | 权威来源 |
|---|---|---|
| SDD | 要做什么，为什么做 | OpenSpec / BMAD / Generic SDD |
| Work Facts | 当前有哪些可证明事实 | Repository / Git / SDD / CI / Evidence |
| Decision Engine | 应该走多重流程 | Deterministic rules |
| Semantic Impact | 修改会影响哪里 | CBM structural evidence |
| Engineering Policy | 允许怎么实现 | Project-owned policy |
| Context Plane | 当前角色应该看什么 | Manifest + role/stage projection |
| Execution State | 做到哪里，下一步是什么 | Native / Hybrid / Orchestrator state |
| Session Context | 冷启动/恢复/交接时先看到什么 | State + Context Plane 的 JSON projection |
| Host Enforcement | 当前动作是否允许 | Shared Enforcement Kernel |
| Superpowers | Agent 应该怎样工作 | Execution discipline |
| Quality Gates | 是否满足完成条件 | Tests / Static analysis / Review / Verification |

一个重要原则是：**任何单一工具都不能越权成为全局权威。**

例如 CBM 可以证明存在跨服务调用，但不能决定 Flow 是 STANDARD 还是 DEEP；ECC 可以提供工程规则参考，但不能自动成为项目级 MUST；Claude Code / Codex / Pi 的 Hook 可以做实时阻断，但最终 DONE 仍由 Execution State + CI/Gates 决定。

---

## 2. 版本演进

Coding Agent Orchestrator 从一个 SDD 编排 Skill 演进为完整的 Coding Governance Plane：

| 版本 | 核心能力 |
|---|---|
| V1 | OpenSpec / BMAD / Generic SDD Adapter + Superpowers + Quality Gates |
| V2 | Adaptive Flow，按需求特征选择 TRIVIAL / FAST / STANDARD / DEEP |
| V3 | Deterministic Decision Engine，消除“凭感觉打分” |
| V4 | Evidence-backed Fact Extractor，事实必须附带证据 |
| V5 | Execution State Manager，提供阶段、任务、阻塞、Gate、Resume 和历史 |
| V6.0 | CBM Semantic Impact，分析 changed symbol 与 blast radius |
| V6.1 | Engineering Policy Layer，项目级架构与开发约束 |
| V6.2 | Context Manifest / Context Pack，统一项目上下文平面 |
| V6.3 | Claude Code / Codex Hooks + Pi Extension Runtime Enforcement |
| V6.4 | Session Bootstrap / Task Handoff，上下文冷启动、恢复和角色交接 |
| **V6.5** | **Project Bootstrap + Unified CLI，一次初始化、自动发现、统一入口与 Doctor** |

V6.5 不再增加新的治理规则，而是把 V3~V6.4 已有能力收口成真正可用的项目入口：`init / discover / doctor / status / start / intake / resume / check / verify / host install`。全新项目只需要一次安全初始化，后续由宿主 Hook/Extension 和 Context Plane 自动恢复。

V6.4 在 V6.3 Runtime Enforcement 基础上继续解决“新会话/新 Agent 怎么快速恢复当前工程现实”。V6.3 的核心变化是从：

```text
“请 Agent 遵守规则”
```

升级到：

```text
Prompt-guided
    ↓
Runtime-enforced
    ↓
State-guarded
    ↓
CI-verified
```

---

## 3. 核心设计原则

### 3.1 SDD 负责 What / Why

Orchestrator 不创建第四套 SDD。已有 OpenSpec 或 BMAD 时，继续使用其原生需求、规格、设计和任务体系。

```text
OpenSpec / BMAD / Generic
          │
          ▼
Orchestrator Adapter
          │
          ▼
统一执行模型
```

### 3.2 Decision Engine 负责 How Much Process

不是每个需求都走完整重流程。

Decision Engine 根据可验证事实计算六个维度：

```text
Ambiguity
Complexity
Scope
Risk
Novelty
Verification Difficulty
```

并确定：

```text
TRIVIAL
FAST
STANDARD
DEEP
```

Agent 不允许直接修改分数或手工选择更轻的 Flow。

### 3.3 Unknown != False

没有发现证据，不代表事实为假。

例如：

```text
没有发现 external consumer
```

不能自动推出：

```text
external_consumers = false
```

需要负面证明或权威证据。

### 3.4 CBM 只提供结构证据

V6 当前唯一 Code Intelligence Provider 是 **Codebase Memory MCP，简称 CBM**。

CBM 用来提供：

```text
changed symbols
call relationships
imports
cross-service edges
HTTP / async / data-flow relationships
blast radius
```

但 CBM 的 risk label 不具有治理权威：

```text
CBM: HIGH RISK
```

不会直接变成：

```text
Flow = DEEP
```

结构事实必须先进入 Work Facts，再由 Decision Engine 分类。

### 3.5 Project Policy > External Guidance

项目自己的 `.orchestrator/policies/` 是工程约束权威。

ECC 等外部规则源只能作为 guidance，除非显式晋升为项目 Policy。

### 3.6 State over Chat History

工程事实不应依赖某一次 Agent 会话。

新的 Agent 应该可以只依赖仓库状态恢复：

```text
SDD
Decision
Execution State
Semantic Impact
Engineering Policy
Context Manifest
Evidence
```

而不是重新阅读整段历史对话。

### 3.7 DONE 必须由系统证明

```text
DONE != 代码写完
DONE != 单元测试绿了
DONE != Agent 说“完成了”
```

DONE 至少意味着：

```text
SDD / Native Work Item Complete
+
Acceptance Criteria Satisfied
+
Required Review Passed
+
Required Quality Gates Passed
+
Required Policy Gates Passed
+
No Blocking Finding
+
Fresh Verification
+
Verification Snapshot == Current Execution Snapshot
```

---

## 4. 目录结构

```text
coding-agent-orchestrator/
├── README.md              # English（默认）
├── README-zh.md           # 中文
├── coding-orchestrator
├── coding-orchestrator.cmd
├── SKILL.md
├── CHANGELOG.md
├── MANIFEST.json
├── THIRD_PARTY_NOTICES.md
│
├── scripts/
│   ├── coding_orchestrator.py
│   ├── project_discovery.py
│   ├── project_bootstrap.py
│   ├── project_activation.py
│   ├── fact_extractor.py
│   ├── fact_resolver.py
│   ├── decision_engine.py
│   ├── code_intelligence_provider.py
│   ├── cbm_provider.py
│   ├── impact_mapper.py
│   ├── verification_planner.py
│   ├── semantic_intake_pipeline.py
│   ├── execution_state_manager.py
│   ├── state_provider_detector.py
│   ├── policy_bootstrap.py
│   ├── policy_engine.py
│   ├── ecc_rules_adapter.py
│   ├── context_plane.py
│   ├── session_context.py
│   ├── enforcement_kernel.py
│   └── install_host_adapter.py
│
├── policies/
│   ├── manifest.yaml
│   ├── common-engineering.yaml
│   └── spring-boot-layered.yaml
│
├── hosts/
│   ├── claude-code/
│   │   └── hooks.template.json
│   ├── codex/
│   │   └── hooks.template.json
│   └── pi/
│       └── coding-orchestrator.template.ts
│
├── references/
│   ├── project-bootstrap.md
│   ├── decision-engine.md
│   ├── semantic-impact-engine.md
│   ├── engineering-policy-layer.md
│   ├── context-plane.md
│   ├── session-context.md
│   ├── execution-state-manager.md
│   ├── host-enforcement.md
│   ├── host-capabilities.yaml
│   └── ...
│
├── examples/
│   ├── orchestrator-config.yaml
│   ├── enforcement.yaml
│   ├── session-context.yaml
│   ├── session-bootstrap-example.json
│   ├── task-handoff-example.json
│   ├── work-facts-*.json
│   ├── semantic-impact-cbm-example.json
│   ├── context-manifest-example.json
│   ├── context-pack-implementer-example.json
│   └── execution-state-*.yaml
│
├── evals/
│   └── pressure-scenarios.md
│
└── tests/
    ├── test_decision_engine.py
    ├── test_fact_extractor.py
    ├── test_execution_state_manager.py
    ├── test_semantic_impact.py
    ├── test_policy_engine.py
    ├── test_context_plane.py
    ├── test_session_context.py
    ├── test_enforcement_kernel.py
    ├── test_project_activation.py
    └── test_v65_bootstrap_cli.py
```

---

## 5. 运行要求

### 必需

- Python 3
- Git repository
- 一个明确的项目工作目录

项目 Python 实现主要使用标准库；由于状态、Policy 和项目配置使用 YAML，需安装 `PyYAML`。

### V6 Semantic Impact 推荐

安装并可调用 `codebase-memory-mcp` 对应的 CBM CLI/binary。

V6 默认策略是：**CBM 不可用时 fail closed**，因为 Provider 不可用不能被解释为“低影响”。

如果只做离线测试，可以使用包内 fixture：

```bash
--cbm-fixture examples/cbm-detect-changes-fixture.json
```

### Spring Boot 项目推荐

对于架构分层约束，建议项目自身增加 ArchUnit 作为最终 REQUIRED Gate。

内置 V6 policy checker 负责快速反馈，ArchUnit 负责更强的仓库级证明。

### Host Enforcement 可选宿主

V6.4 保留 V6.3 的宿主适配，并新增 Session Bootstrap/Handoff 注入：

- Claude Code hooks
- Codex hooks
- Pi extension

Host Hook 永远不是最终唯一安全边界。V5 State Guard、CI 和 Merge/Branch Protection 应继续保留。

---

## 6. 快速开始

### 6.0 推荐方式：首次激活自动自举

推荐把 Skill 安装在项目内：

```text
project/.agents/skills/<skill-dir>/
```

目录名 `<skill-dir>` 由你决定 —— `coding-agent-orchestrator`、`orchestrating-sdd-coding` 或其他名字都可以。**不要硬编码它**：同一个 Skill 包在不同项目里可能安装在不同的目录名下。想知道它实际在哪里，直接问 Skill 自己：

```bash
<skill-dir>/coding-orchestrator --repo . where
```

正常情况下，现在不再需要记住一个额外的“首次初始化步骤”。Skill 第一次被选中后，**Bootstrap Guard** 会先检查 `.orchestrator/config.yaml`；如果不存在，就自动执行一次包内 Safe Auto `init`，然后继续当前这次用户请求，不需要退出或再发一次消息。

仍然可以在**项目根目录**手工初始化：

```bash
./.agents/skills/<skill-dir>/coding-orchestrator --repo . init
```

Windows：

```bat
.agents\skills\coding-agent-orchestrator\coding-orchestrator.cmd --repo . init
```

如果当前工作目录本身就是 Skill 目录，也仍然可以使用短命令：

```bash
./coding-orchestrator init
```

`init` 会保守地自动完成：

```text
Repository Discovery
  -> technology / build / framework
  -> SDD authority detection
  -> 当前 Agent Host detection
  -> CBM availability detection
  -> architecture evidence
  -> safe Engineering Policy bootstrap
  -> enforcement/session config
  -> Project Activation Stub（AGENTS.md；适用时 CLAUDE.md）
  -> 当前 Agent Host adapter install（无法识别时兜底 Claude Code）
  -> fresh-project Session Bootstrap
  -> READY_FOR_INTAKE
```

**它不会创建一个假的 active task。** 第一个真实 `execution-state.yaml` 只会在第一次 `intake` 时创建。

安全自动化原则：

- 能客观证明的内容自动配置。
- 多个 SDD Authority 同时存在时返回 `ACTION_REQUIRED`，不擅自选择。
- 仅检测到 Spring Boot 不等于自动启用经典 `web -> service -> dao`；只有仓库结构能证明该架构时才自动启用。
- Safe Auto 按**当前正在运行的 Agent Host**选择 Adapter，而不是根据仓库里残留的 `.pi/.codex/.claude` 目录或 PATH 中有哪些二进制来猜。Pi 优先使用运行时信号/父进程识别，Codex 使用运行时信号/父进程识别；无法可靠识别时确定性兜底安装 Claude Code。
- 项目已经 bootstrap 后，如果以后换到另一个受支持 Agent，Bootstrap Guard 只补装当前 Host Adapter，不重跑项目初始化，也不会重建当前 Work Item。
- CBM 未安装不会阻止项目 bootstrap，但正常 Semantic Intake 会 fail closed。
- 已存在的项目 Policy/配置默认保留；不要让 bootstrap 静默覆盖团队规则。

#### 冷启动激活

把 Skill 放到 `.agents/skills/coding-agent-orchestrator/` 后，如果第一次输入 `开始`、`继续`、`start`、`continue`、`resume` 时 Host 成功选中了这个 Skill，Bootstrap Guard 会自动执行一次 Safe Auto `init`、建立 Activation Layer，并在**同一轮**继续处理当前请求。之后 `AGENTS.md` / Host Integration 会让这类模糊恢复提示可靠得多。

仍然存在一个无法从 Skill 内部消除的首次边界：如果 Host 在 Activation Layer 尚不存在时，对极短的 `开始` 根本**没有选择这个 Skill**，那么尚未加载的 Skill 自然无法执行自举。这种情况下只需要第一次显式提到 `coding-agent-orchestrator`，或者执行上面的手工 `init`。

`init` 会幂等地创建或更新 `AGENTS.md` 中的受控区块。默认 `--host auto` 会只安装**当前 Agent**对应的一份 Adapter：Pi 安装 Pi extension，Codex 安装 Codex hooks；无法可靠识别当前 Agent 时兜底安装 Claude Code hooks，并维护 `CLAUDE.md`。受控区块之外的项目原有指令不会被覆盖。

Activation Stub 只负责回答 **“先加载哪个工作流”**，绝不成为项目事实源。Skill 被激活后，必须从 `.orchestrator/`、SDD Authority、Policy 与 Evidence 恢复真实状态。

#### 单独一句“开始”到底表示什么

`开始`、`继续`、`接着做`、`start`、`continue`、`resume` 这类短提示现在被定义为**控制意图**，不是需求本身。Activation Layer 会把它路由到：

```bash
./.agents/skills/coding-agent-orchestrator/coding-orchestrator --repo . start
```

然后根据权威项目状态确定唯一合法的下一步：

```text
UNBOOTSTRAPPED -> Safe Auto init
已有 active work -> Resume 当前工作
当前工作 blocked -> 先暴露/解决 blocker
没有 active work + 0 个可执行需求 -> 请求用户提供第一个需求
没有 active work + 1 个高置信度需求 -> 自动 Intake
没有 active work + 多个需求 -> ACTION_REQUIRED，让用户选择
```

因此单独一句“开始”永远不会被解释成“随便选技术栈并创建生产代码”。Generic Requirement Discovery 会排除 `.agents/`、`.orchestrator/`、Host 配置、构建产物以及普通安装型 README。

初始化后的推荐项目结构：

```text
project/
├── AGENTS.md                         # 自动创建/合并的激活入口
├── CLAUDE.md                         # 仅 Claude Code 适用时存在
├── .agents/
│   └── skills/
│       └── coding-agent-orchestrator/
│           ├── SKILL.md
│           ├── references/
│           ├── scripts/
│           └── ...
├── .orchestrator/                    # 项目运行态事实源
├── .claude/ .codex/ .pi/             # 仅宿主集成层
└── src/
```

初始化后，在项目根目录：

```bash
ORCH=./.agents/skills/coding-agent-orchestrator/coding-orchestrator
$ORCH --repo . doctor
$ORCH --repo . status
$ORCH --repo . start
$ORCH --repo . intake "新增用户查询 REST API"
$ORCH --repo . resume
$ORCH --repo . verify
```

正常切换 Agent 时，Bootstrap Guard 会自动补装当前 Host。若需要预安装或覆盖自动识别，仍可显式安装：

```bash
$ORCH --repo . host install pi
$ORCH --repo . host install claude-code
$ORCH --repo . host install codex
```

机器/CI 使用：

```bash
$ORCH --repo . --json discover
$ORCH --repo . --json doctor
$ORCH --repo . init --ci
```

`--ci` 遇到无法自动解决的 Authority 冲突时返回非零退出码。

> 下面 6.1~6.6 保留的是内部/高级手工路径，适合调试 Engine 或自定义集成；普通项目优先使用 V6.5 Unified CLI。


以下示例假设 Orchestrator 包位于项目中可访问的位置。

### 6.1 安装项目级 Engineering Policy

```bash
python scripts/policy_bootstrap.py --repo .
```

默认不会覆盖已有文件。

需要强制覆盖 starter policy 时：

```bash
python scripts/policy_bootstrap.py --repo . --force
```

会建立或补齐：

```text
.orchestrator/
└── policies/
    ├── manifest.yaml
    ├── common-engineering.yaml
    └── spring-boot-layered.yaml
```

这些 starter 文件应该根据项目实际架构修改，不建议长期原样使用。

### 6.2 检测 Execution State Provider

```bash
python scripts/state_provider_detector.py .
```

典型策略：

| 项目 | 推荐模式 |
|---|---|
| BMAD + sprint-status | `native` |
| OpenSpec | `hybrid` |
| 无原生执行状态 | `orchestrator` |

### 6.3 初始化执行状态

例如 OpenSpec 项目：

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  init \
  --work-id CHG-42 \
  --title "Login reporting" \
  --flow STANDARD \
  --provider openspec \
  --authority-mode hybrid
```

查看 Resume 摘要：

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  resume
```

### 6.4 运行完整 Semantic Intake

V6.5 下该命令作为内部/高级入口：

```bash
python scripts/semantic_intake_pipeline.py \
  --repo . \
  --request-file request.md \
  --resolutions .orchestrator/fact-resolutions.json \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --state .orchestrator/execution-state.yaml \
  --sync-state \
  --sdd-provider openspec \
  --sdd-ref openspec/changes/login-reporting \
  --context-role implementer \
  --context-stage implementation
```

这条流水线会依次完成：

```text
Fact Extraction
      ↓
CBM Semantic Impact
      ↓
Policy Routing
      ↓
Impact → Work Facts
      ↓
Semantic Resolution
      ↓
Decision Classification
      ↓
Verification Planning
      ↓
Execution State Sync
      ↓
Context Manifest
      ↓
Role/Stage Context Pack
```

### 6.5 安装 Host Enforcement Adapter

先 dry-run：

```bash
python scripts/install_host_adapter.py \
  --repo . \
  --host all
```

确认后安装：

```bash
python scripts/install_host_adapter.py \
  --repo . \
  --host all \
  --apply
```

也可以单独安装：

```bash
python scripts/install_host_adapter.py --repo . --host claude-code --apply
python scripts/install_host_adapter.py --repo . --host codex --apply
python scripts/install_host_adapter.py --repo . --host pi --apply
```

安装器会创建或合并宿主配置，并在需要时创建备份，同时创建 `.orchestrator/session-context.yaml`。

### 6.6 生成冷启动 / Resume Context

```bash
python scripts/session_context.py --repo . bootstrap \
  --role implementer \
  --stdout markdown
```

受控任务/角色交接时生成 Handoff：

```bash
python scripts/session_context.py --repo . handoff \
  --from-role implementer \
  --to-role reviewer \
  --completed-task-id T4.2 \
  --summary "Implemented async report delivery" \
  --next-action "review_task:T4.2"
```

格式约定：**JSON 是 canonical artifact，YAML 是配置，Markdown 是实际注入 Agent 的提示词投影。默认不使用 XML。**

---

## 7. Adaptive Flow

Orchestrator 不要求所有任务使用同样重的流程。

### TRIVIAL

适合：

```text
文案修改
简单配置
明确的局部非风险调整
```

典型流程：

```text
Impact Check
  ↓
Implement
  ↓
Targeted Verify
  ↓
Done
```

### FAST

适合需求明确、影响有限的功能或 Bug Fix。

```text
Mini Spec / AC
  ↓
Tasks
  ↓
Implementation
  ↓
Targeted Tests
  ↓
Verification
```

### STANDARD

适合正常 Feature、多文件/多模块、API 或数据模型变化。

```text
Spec
  ↓
Design
  ↓
Tasks
  ↓
Implementation
  ↓
Review
  ↓
Quality Gates
  ↓
Verification
```

### DEEP

适合高风险、高复杂度、跨服务、认证授权、破坏性迁移等任务。

```text
Discovery
  ↓
Architecture / Alternatives
  ↓
Risk Analysis
  ↓
Detailed Spec
  ↓
Incremental Implementation
  ↓
Adversarial Review
  ↓
Security / Compatibility / Migration Checks
  ↓
Fresh Verification
```

### 动态升降级

开发过程中一旦发现事实变化，必须重新分析：

```text
FAST
 ↓
发现跨服务依赖
 ↓
重新 Work Facts + CBM Impact
 ↓
STANDARD
```

反之，如果调查确认原本复杂的问题其实是局部 bug，也允许降级，但不能取消已经成立的安全与质量义务。

---

## 8. Deterministic Decision Engine

V3 之后不允许 Agent 自由打分。

运行：

```bash
python scripts/decision_engine.py \
  .orchestrator/intake/work-facts.resolved.json \
  --strict-evidence \
  --pretty
```

Decision Engine 使用结构化 Work Facts、组合规则和 Override Rules。

例如：

```text
代码只有 2 行修改
但属于 authentication path
```

不会因为“代码量小”就选择 TRIVIAL。

Risk Override 可以规定：

```text
authentication / authorization
→ minimum STANDARD

critical risk
→ force DEEP
```

模糊需求也不会直接等同于 DEEP。高 Ambiguity 首先产生 implementation blocker：

```text
Clarify
  ↓
补齐 Acceptance Criteria
  ↓
重新生成 Work Facts
  ↓
重新分类
```

---

## 9. Evidence-backed Work Facts

Fact Extractor 采用保守策略：

```text
Mechanical Evidence
→ 可以直接形成 Fact

Heuristic
→ 只能形成 Hint

Unknown
→ 保持 Unknown
```

例如路径包含：

```text
src/auth/token_validator
```

只能提示：

```text
可能涉及 authentication
```

不能自动得到：

```json
{"risk": {"authn_authz": true}}
```

Agent Resolver 必须补充代码检查或权威证据。

同时所有 Fact 都有严格类型约束，避免：

```json
{"authn_authz": "false"}
```

被 Python 当成 truthy 值。

---

## 10. CBM Semantic Impact

V6 当前只接入一个 Code Intelligence Provider：**Codebase Memory MCP**。

Provider 抽象保留在：

```text
scripts/code_intelligence_provider.py
```

当前实现：

```text
scripts/cbm_provider.py
```

查看 Provider 能力：

```bash
python scripts/cbm_provider.py capabilities
```

检查健康状态：

```bash
python scripts/cbm_provider.py health
```

健康信息会包含检测到的 CBM CLI 协议；`coding-orchestrator doctor` 还会执行一次无副作用的
`list_projects` smoke probe。对现代 schema CLI，Adapter 会显式使用 `cli <tool> --... --format json`，
避免把 CBM 的 compact/tree 人类可读输出误判成 JSON 契约失败；旧版本才按协议检测结果使用 stdin/
inline JSON 兼容路径。Agent 的 PATH 不完整时还会检查 `~/.local/bin`、Homebrew 等常见安装位置。
真正的 indexing 失败不会被兼容 fallback 的二次错误覆盖。当前 CBM 可用下面的命令直接验证索引：

```bash
codebase-memory-mcp cli index_repository --repo-path /absolute/path/to/repo
codebase-memory-mcp cli list_projects --format json --detail stats
```

当前 CBM 的 `detect_changes --scope` 只接受 `files|impact`，不能传入 Orchestrator 自己的
`all|branch`；`check_index_coverage` 还必须提供具体 `paths` 或 `scopes`。如果项目只有
BMAD/Orchestrator 安装元数据、尚无业务源码，则按空白 greenfield 处理：不调用 CBM，也不会把
尚无 Git `HEAD` 误报成 provider 故障。产生业务源码后才建立索引；首个提交前保守地把业务文件
全部视为新增文件。

CBM 运行失败现在会写入持久化 provider incident，不会再形成“证据不足 → 重跑 semantic intake →
同一个 provider 再失败”的循环。可使用：

```bash
coding-orchestrator --repo . provider status codebase-memory-mcp
coding-orchestrator --repo . provider reset codebase-memory-mcp
```

第一次失败仍然 fail closed；之后自动调用直接返回 `PROVIDER_BLOCKED`，`next_action=repair_cbm_provider`。
如果 CBM binary/version 发生变化，则允许自动进行一次新的尝试。

CBM 主要帮助 Orchestrator 发现：

```text
Git Change
   ↓
Changed Symbols
   ↓
Call / Import / HTTP / Async / Data Relationships
   ↓
Affected Modules / Services / Projects
   ↓
Blast Radius
```

### Provider Risk Quarantine

CBM 自己的风险分类只保存为 provider opinion：

```yaml
provider_opinion:
  risk_labels:
    - HIGH
  authoritative_for_flow: false
```

Flow 仍由 Decision Engine 决定。

### 分页完整性

如果 CBM blast radius 结果存在 continuation cursor 或 partial result，Orchestrator 默认不会把第一页的数量当作完整上界。

```text
partial semantic impact
→ NEEDS_EVIDENCE
```

除非显式使用：

```bash
--allow-partial-impact
```

该参数是逃生口，不是推荐默认行为。

---

## 11. Engineering Policy

Engineering Policy 负责表达：

> 项目允许怎么实现。

它和 SDD 的职责不同：

```text
SDD
→ What / Why

Engineering Policy
→ What is allowed
```

### Policy Manifest

默认入口：

```text
.orchestrator/policies/manifest.yaml
```

规则支持三级：

```text
MUST
SHOULD
PREFER
```

推荐语义：

| Level | 行为 |
|---|---|
| MUST | 可映射为 blocking gate |
| SHOULD | Review / warning |
| PREFER | Advisory only |

### Spring Boot Layered Policy

Starter Policy 包含：

```text
web/controller -> service -> dao/repository
```

并禁止：

```text
web -> dao
service -> web
dao -> service/web
```

例如：

```java
@RestController
class UserController {
    UserDao dao;
}
```

会触发：

```text
ARCH-SPRING-LAYER-001
web -> dao forbidden
```

### Policy Routing

根据 Semantic Impact 选择当前真正适用的 Policy：

```bash
python scripts/policy_engine.py route \
  --repo . \
  --impact .orchestrator/intake/semantic-impact.json \
  --manifest .orchestrator/policies/manifest.yaml \
  --stage implementation \
  --output .orchestrator/intake/policy-plan.json
```

执行轻量检查：

```bash
python scripts/policy_engine.py evaluate \
  --repo . \
  --plan .orchestrator/intake/policy-plan.json \
  --manifest .orchestrator/policies/manifest.yaml \
  --output .orchestrator/intake/policy-evaluation.json
```

### ECC Integration

ECC `rules/` 可以作为外部工程规则来源。

默认定位：

```text
ECC rules
→ external guidance
→ non-blocking
```

只有显式转换为 `.orchestrator/policies/*.yaml` 的项目规则，才可以成为 blocking MUST。

这防止外部规则更新后悄悄改变项目 Gate。

---

## 12. Context Plane

V6.2 将上下文从 Chat History 中剥离出来，建立真正的 Project Context Plane。

### Context Manifest

`context-manifest.json` 回答：

```text
当前权威 Requirement 在哪里？
Decision 在哪里？
Execution State 在哪里？
Semantic Impact 在哪里？
Policy 在哪里？
Verification 在哪里？
每份数据对应什么 hash/snapshot？
```

它是索引，不是新的 Source of Truth。

### Context Pack

Context Pack 回答：

> 当前这个角色、当前这个阶段到底应该看什么？

支持角色：

```text
planner
implementer
reviewer
verifier
debugger
resume
```

支持阶段：

```text
bootstrap
planning
implementation
review
verification
resume
```

例如 Implementer Context Pack 更关注：

```text
Requirement
Current Task
Execution State
Semantic Impact
Exact Engineering Policies
Relevant Verification Requirements
```

Reviewer 则更关注：

```text
Requirement / AC
Diff / Semantic Impact
Policy Violations
Review Requirements
Verification Plan
```

Verifier 更关注：

```text
Acceptance Criteria
Execution Snapshot
Quality Gates
Policy Gates
Review Result
Fresh Verification
```

### 构建 Context Plane

```bash
python scripts/context_plane.py build \
  --repo . \
  --state .orchestrator/execution-state.yaml \
  --sdd-provider openspec \
  --sdd-ref openspec/changes/login-reporting \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --role implementer \
  --stage implementation
```

验证是否 stale：

```bash
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json
```

Context Pack 绑定：

```text
source hashes
execution revision
analysis snapshot
policy snapshot
```

任意重要来源改变后，旧 Pack 不再被视为当前事实。

### Context Budget

Context Plane 不会把完整 CBM Graph、完整执行历史、全部 ECC Rule Corpus 塞进 Prompt。

原则：

```text
Graph is storage, not prompt.
```

Mandatory Context 永不因为预算不足被静默删除，低相关内容进入 lazy retrieval。

---

## 12.5 V6.4 Session Bootstrap / Handoff Context

V6.4 解决的是：新会话、新 Agent、compact/resume 之后，不再依赖旧聊天历史重新拼项目状态。

```text
SDD / Decision / State / Semantic Impact / Policy / Evidence
                         │
                         ▼
                  Session Bootstrap
                         │
              JSON canonical artifact
                         │
                         ▼
               compact Markdown prompt
                         │
                         ▼
                       Agent
```

三类启动模式：

```text
fresh_project  → 尚未初始化 State，只允许 discovery/intake
session_resume → 已有 State，从当前 task/cursor/next action 恢复
agent_handoff  → 有 fresh handoff，额外携带上一任务摘要、风险和交接动作
```

默认文件：

```text
.orchestrator/session/
├── session-bootstrap.json
├── session-bootstrap.md
├── latest-handoff.json
├── latest-handoff.md
└── handoff-<id>.json/.md
```

Handoff 不能从“看起来完成了”之类自然语言自动制造完成事实。只有显式交接操作才能写入 `completed_task`。Handoff 会绑定 Work Item、Execution/Analysis snapshot 以及 Requirement/Decision/Policy/Evidence Authority Fingerprint；这些事实发生变化后旧 Handoff 会变 stale。

完整规范见 `references/session-context.md`。

---

## 13. Execution State Manager

V5 提供 Durable Execution State，解决：

```text
现在做到哪里？
谁在做？
是否被阻塞？
下一步是什么？
哪些 Gate 通过了？
新的 Agent 如何恢复？
```

### Canonical State

使用：

```text
Phase × Status + Blocked Modifier
```

Phase：

```text
discovery
specification
design
planning
implementation
review
verification
release
closed
```

Status：

```text
pending
ready
in_progress
completed
failed
cancelled
```

Blocked 单独存在：

```yaml
phase: implementation
status: in_progress
blocked: true
```

这样不会因为 blocked 而丢失“到底阻塞在哪个阶段”的信息。

### Authority Modes

#### native

适合 BMAD 等已有执行状态权威的系统。

Native Tool 管：

```text
phase / status / progress
```

Orchestrator 补充：

```text
blockers
assignments
gates
evidence
verification
context refs
```

#### hybrid

适合 OpenSpec。

OpenSpec 管：

```text
proposal / spec / design / tasks readiness
```

Orchestrator 管：

```text
implementation / review / verification runtime state
```

#### orchestrator

没有原生状态机制时，由：

```text
.orchestrator/execution-state.yaml
```

作为执行状态权威。

### Optimistic Revision

Execution State 使用 revision 防止多 Agent last-write-wins。

```text
Agent A read revision 12
Agent B read revision 12

A update -> revision 13
B update expected 12 -> conflict
```

并发环境中每个 State mutation 都应携带 expected revision。

### Append-only History

默认：

```text
.orchestrator/execution-history.jsonl
```

记录：

```text
STATE_INITIALIZED
TRANSITION
BLOCKED
UNBLOCKED
QUALITY_GATE_RECORDED
REVIEW_RECORDED
VERIFICATION_RECORDED
...
```

不要重写历史记录。

---

## 14. Host Enforcement（V6.3 Runtime + V6.4 Session Context）

V6.3 增加统一 Runtime Enforcement Kernel，V6.4 在相同生命周期上增加 Session Bootstrap/Handoff：

```text
Host Event
  ↓
Enforcement Kernel
  ↓
Decision / State / Context / Policy
  ↓
allow | deny | context | stale
```

三个 Host Adapter 都只是事件翻译器，不复制治理规则。

### Claude Code

模板：

```text
hosts/claude-code/hooks.template.json
```

主要事件：

```text
SessionStart
UserPromptSubmit
PreToolUse
PostToolUse
SubagentStart
SubagentStop
Stop
```

### Codex

模板：

```text
hosts/codex/hooks.template.json
```

主要事件与 Claude Code 类似。

项目级 Hook 只是 Runtime Guardrail，最终安全边界仍是 V5 + CI。

### Pi

模板：

```text
hosts/pi/coding-orchestrator.template.ts
```

主要事件：

```text
session_start
before_agent_start
tool_call
tool_result
agent_end
```

Pi 的 `tool_call` 可以提供 mutation 前阻断，`agent_end` 更适合作为 best-effort completion continuation。最终 DONE 依然由 State / CI 决定。

---

## 15. Runtime Enforcement 规则

### Session Start

执行：

```text
detect project
  ↓
load State / Context Manifest / current role Pack
  ↓
validate latest Handoff
  ↓
build Session Bootstrap JSON
  ↓
render compact Markdown
  ↓
inject once
```

后续 `UserPromptSubmit` 默认只注入 delta context，不重复注入完整 Bootstrap。

不会把所有历史、CBM 图或 Policy 全量塞进模型。

### Pre Tool

先归一化工具输入，再调用共享 Action Guard。`read` 与需求/治理文档的 `prepare` 操作仍可用；
`mutate_code` 要求分析有效、SDD 就绪、没有活动阻塞、处于 implementation 阶段且角色可写。
已记录编辑窗口的例外只针对代码新鲜度，不能豁免已变化的需求或规则。准确契约与诊断命令见
[Action Authorization](references/action-authorization.md)；适配器不得另写一套判断。

### Post Tool

发生 material mutation 后：

```text
semantic_fresh = false
context_fresh = false
verification.fresh = false
```

并可执行轻量 Policy feedback。

### Stop

普通中间回复不会被拦截。

只有 Agent 明确声称：

```text
完成
Done
Finished
可以合并
```

或状态已经进入 verification / release / closed 语境时，才检查 Completion Contract。

---

## 16. Dirty Window

V6.3 不会在每次改一行代码后立即跑全套重型分析。

修改后进入 Dirty Window：

```text
Implementation
   ↓
edit
   ↓
edit
   ↓
edit
```

这些连续 Implementation mutation 可以继续进行。

但是第一次 material mutation 会立即使以下证据失效：

```text
Semantic Impact
Context freshness
Policy proof
Final Verification
```

当准备进入：

```text
review
verification
closed
```

时，State Guard 会要求重新完成：

```text
Semantic Intake
CBM refresh
Policy routing/evaluation
Decision reassessment
Context refresh
Verification
```

这个设计避免出现两种极端：

```text
极端 A：每改一行都跑 PIT/CBM/全量测试
极端 B：改完一堆代码继续拿旧证据宣布 DONE
```

---

## 17. Enforcement Configuration

Starter：

```text
examples/enforcement.yaml
```

示例：

```yaml
version: 1
enabled: true
post_mutation_policy_feedback: true
completion_claim_only: true
max_stop_blocks_per_session: 3

```

安装 Host Adapter 后，项目可维护自己的：

```text
.orchestrator/enforcement.yaml
```

许可条件由共享 Action Guard 定义。旧的 `require_*`、阶段/路径覆盖及 `mode`
配置不再降低许可条件。`enabled: false` 仅关闭宿主 Hook，状态与 CLI 检查仍然有效。
重试上限只限制自动续跑次数，不降低关闭要求。

---

## 18. Spring Boot 推荐落地

一个典型 Spring Boot 项目可以采用：

```text
web/controller
    ↓
service
    ↓
dao/repository
```

建议三层执行：

### Context Layer

Agent 修改 Controller 前，Context Pack 注入：

```text
ARCH-SPRING-LAYER-001
web may depend on service, not dao
```

### Runtime Layer

写入代码后，V6 Policy Check 立即识别直接跨层依赖。

### CI Layer

ArchUnit 执行真正的仓库级 Architecture Gate。

推荐关系：

```text
Prompt / Context
→ 防忘

V6 Hook / Policy Check
→ 快速反馈

ArchUnit / CI
→ 最终证明
```

---

## 19. Verification Planner

Verification Planner 根据 Semantic Impact + Engineering Policy + Project Quality Policy 决定需要哪些验证。

典型推导：

```text
changed executable symbol
→ targeted unit/component checks

runtime boundary
→ integration checks

public contract
→ contract / compatibility checks

async boundary
→ retry / timing / failure checks

security Work Fact
→ security regression checks

data migration
→ migration / rollback checks
```

已有 REQUIRED Gate 不能被 Planner 降级。

例如项目规定：

```text
Semgrep REQUIRED
PIT mutation >= 80% REQUIRED
```

即使 Flow 是 FAST，也不能擅自取消这些 Gate。

---

## 20. 推荐项目配置

参考：

```text
examples/orchestrator-config.yaml
```

它涵盖：

```text
Evidence
CBM
Engineering Policy
Context Plane
Decision Engine
SDD
Execution State
Execution Discipline
Quality Gates
```

建议复制到项目自己的配置体系后再修改实际命令和阈值，不要把示例中的：

```text
<project-test-command>
<project-archunit-test-command>
```

留在真实项目中。

---

## 21. 推荐日常开发工作流

### 新 Feature

```text
1. SDD 创建/定位 Change 或 Story
2. 初始化或恢复 Execution State
3. 运行 Semantic Intake
4. Decision Engine 确定 Flow
5. Policy Router 选择工程约束
6. Context Plane 生成 Planner/Implementer Pack
7. Transition → implementation
8. Agent 开发，Host Hook 实时约束
9. Dirty Window 内完成一个 traceable slice
10. 重新 Semantic Intake
11. Transition → review
12. Review / Policy / Quality Gates
13. Transition → verification
14. Fresh Verification
15. Transition → closed
```

### 明确 Bug Fix

```text
Bug
 ↓
Work Facts
 ↓
FAST / STANDARD
 ↓
Systematic Debugging
 ↓
Targeted Fix
 ↓
Relevant Tests
 ↓
Semantic Reanalysis
 ↓
Verification
```

未知失败不应靠 speculative patch 逐个尝试，应进入 root-cause debugging。

### Resume

新的 Agent 接手时：

```bash
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  resume
```

然后生成：

```text
role = resume
stage = resume
```

的 Context Pack，而不是让新 Agent 重新扫整个聊天历史。

---

## 22. 推荐 `.orchestrator/` 项目布局

真实项目中建议维护：

```text
.orchestrator/
├── policies/
│   ├── manifest.yaml
│   ├── common-engineering.yaml
│   └── spring-boot-layered.yaml
│
├── intake/
│   ├── work-facts.draft.json
│   ├── work-facts.resolved.json
│   ├── semantic-impact.json
│   ├── policy-plan.json
│   ├── policy-evaluation.json
│   ├── verification-plan.json
│   ├── decision.json
│   ├── context-manifest.json
│   └── context-pack-*.json
│
├── evidence/
│   └── ...
│
├── execution-state.yaml
├── execution-history.jsonl
└── enforcement.yaml
```

具体输出名称可能随 Pipeline 参数变化，但建议保持上述职责划分。

---

## 23. Source of Truth 规则

为了避免“双权威”，请遵守以下优先级。

### Requirement Authority

```text
Existing SDD
> Project explicit configuration
> Generic fallback
```

### Execution State Authority

```text
Native SDD State
> Project-defined State
> Orchestrator-owned State
```

### Engineering Policy Authority

```text
Project Policy
> Framework / Language local pack
> External guidance such as ECC
```

### Semantic Structure Authority

```text
CBM structural evidence
```

但 CBM 不能覆盖 Requirement、Policy 或 Decision Authority。

### Completion Authority

```text
V5 Transition Guards
+
Required CI/Gates
+
Fresh Verification
```

Host Agent 自己不是 Completion Authority。

---

## 24. Host Capability 差异

三种宿主能力不完全一致，详见：

```text
references/host-capabilities.yaml
```

设计时应遵守：

```text
Host 能力不足
≠
降低治理要求
```

而应该：

```text
Host Guard weaker
→ State / CI Guard stronger
```

因此项目不能因为使用某个 Host 没有硬 Stop Block，就取消最终 Completion Gate。

---

## 25. 常见问题

### Q1：没有 CBM 能不能使用？

V3/V4/V5/Policy/Context 的部分能力仍然存在，但 V6 Semantic Impact 默认 fail closed。

对于正式代码变更，不建议把“CBM 不可用”解释为“低风险”。

测试时可使用 fixture。

### Q2：所有需求都需要 OpenSpec/BMAD 吗？

不需要。

TRIVIAL/FAST 可以走 Generic 或轻量 SDD，但仍应遵守项目 Engineering Policy 和必要 Verification。

### Q3：为什么不把所有规则都写进 Prompt？

因为规则越多，Context Pollution 越严重。

本项目采用：

```text
Manifest
→ Relevant Summary
→ Exact Applicable Rules
→ Machine Enforcement
```

而不是一次加载全部规范。

### Q4：为什么 External ECC Rule 不能直接 BLOCK？

因为外部规则更新不应该隐式改变项目治理行为。

必须显式晋升为 Project Policy 后，才能成为 MUST / Gate。

### Q5：为什么代码改完后 Semantic Impact 会 stale？

因为旧 CBM 图、旧 Context Pack、旧 Verification 针对的是旧 Snapshot。

新代码必须重新证明其 Impact 和 Verification。

### Q6：为什么 Hook 不直接每次跑全量测试？

同步 Hook 应保持廉价，否则开发体验会崩坏。

重型 Proof 应在 slice boundary、phase transition 或 final gate 执行。

### Q7：Agent 能不能自己把状态改成 DONE？

不能绕过 Transition Guard。

即使 Native SDD 报 `done`，如果 REQUIRED Gate 或 Fresh Verification 不满足：

```text
Native Done != Governance Done
```

### Q8：是否已经支持 Multi-Agent Scheduler？

V6.5 已经为多 Agent 准备了 revision、assignment、role context、reviewer/verifier write separation 和 Subagent hook，但当前版本不包含完整 V7 Scheduler。

---

## 26. 测试

运行完整测试：

```bash
python3 -m unittest discover \
  -s tests \
  -p 'test_*.py'
```

V6.5 当前包：

```text
177 tests
```

覆盖范围包括：

```text
Decision Engine
Evidence Fact Extraction
Execution State
CBM Semantic Impact
Engineering Policy
Context Plane
Session Bootstrap / Handoff
Host Enforcement
Project Activation
```

压力场景参考：

```text
evals/pressure-scenarios.md
```

---

## 27. 故障排查

### Code mutation 被拒绝

检查：

```text
1. execution-state.yaml 是否存在
2. Decision 是否已 CLASSIFIED
3. 当前 phase 是否 implementation
4. Context Manifest 是否 stale
5. 当前角色是否允许修改 production code
```

### 无法进入 Review

常见原因：

```text
implementation tasks incomplete
active blocker
semantic evidence stale
policy evidence stale
```

代码修改后通常需要重新运行 Semantic Intake。

### 无法 CLOSED

检查：

```text
Acceptance Criteria
Required Review
Blocking Findings
Required Quality Gates
Required Policy Gates
Final Verification
Verification Freshness
Execution Snapshot Match
```

### Context Pack stale

运行：

```bash
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json
```

然后重新 build Context Plane。

### Policy 冲突

项目 Policy 不允许通过高优先级配置把已有 MUST 偷偷降级为 SHOULD/PREFER。

遇到 `ILLEGAL_DOWNGRADE` 时应解决 Policy Authority，而不是跳过检查。

---

## 28. 安全与治理边界

V6.4 采用 defense-in-depth，而不是假设任意一个 Hook 永远不可绕过。

推荐完整链路：

```text
Layer 1  Context / Prompt
         提前告诉 Agent 正确约束

Layer 2  Host Hook / Extension
         实时阻断明显非法动作

Layer 3  Execution State Guard
         阻止非法 phase transition

Layer 4  CI / Git / Quality Gate
         仓库级机器证明

Layer 5  Merge / Branch Protection
         最终交付边界
```

不要把 V6.4 Host Adapter 当成唯一安全机制。

---

## 29. 扩展新的 SDD

实现：

```text
references/adapter-contract.md
```

要求保持：

```text
SDD owns What / Why
Orchestrator owns execution governance
```

不要把已有 SDD artifact 全量复制到 Orchestrator。

---

## 30. 扩展新的 Code Intelligence Provider

当前只有 CBM。

如果未来接入其他 Provider，应实现：

```text
scripts/code_intelligence_provider.py
```

并输出 Canonical Semantic Impact。

Decision Engine 不应直接依赖 Provider 私有数据结构。

```text
Provider
  ↓
Adapter
  ↓
Canonical Evidence
  ↓
Work Facts
```

---

## 31. 扩展新的 Host

增加一个 Host Adapter 时：

1. 阅读 `references/host-enforcement.md`。
2. 在 `references/host-capabilities.yaml` 描述能力。
3. 将宿主事件转换为 Canonical Enforcement Event。
4. 调用 `scripts/enforcement_kernel.py`。
5. 不要在 Host Adapter 内复制 Decision / Policy / State 规则。
6. 对宿主无法硬阻断的能力，依赖 V5/CI 最终 Gate。

正确关系：

```text
New Host
   ↓
Thin Adapter
   ↓
Shared Enforcement Kernel
```

而不是：

```text
New Host
   ↓
重新实现一套 Orchestrator
```

---

## 32. Superpowers 的位置

Superpowers 不负责决定需求属于 FAST 还是 DEEP，它负责约束 Agent 怎样完成工作。

典型路由：

```text
Ambiguity
→ brainstorming

Planning required
→ writing-plans

Behavior change
→ test-driven-development

Unknown failure
→ systematic-debugging

Independent tasks
→ parallel/subagent disciplines

Implementation complete
→ requesting-code-review

Before DONE
→ verification-before-completion
```

因此：

```text
SDD
→ What / Why

Decision Engine
→ How much process

Engineering Policy
→ What is allowed

Superpowers
→ How to work

State + Gates
→ Can it be done
```

---

## 33. 当前 V6.5 的边界

V6.5 已经完成 Coding Governance 的主体闭环、Session Context 收口以及项目级 Auto Bootstrap/Unified CLI，但它不是：

- Jira/Linear 替代品
- CI/CD 替代品
- CodeQL/SonarQube 替代品
- 完整 Multi-Agent Scheduler
- 组织级 Governance Dashboard
- 完整代码知识图谱实现

它更像这些系统之间的**控制面与协议层**。

后续可能演进方向包括：

```text
Real Project Validation / Calibration
Golden Cases
Spring-specific semantic enrichment
Multi-Agent Scheduling
Organization-level Governance Control Plane
```

但在继续增加功能前，建议先在真实项目中验证误拦截、漏拦截、CBM 准确率、Context Pack 质量和 Verification Recall。

---

## 34. 推荐真实项目验证指标

在 Spring Boot 项目中建议准备 Golden Cases，覆盖：

```text
local change
REST API change
service / dao change
cross-module change
Feign cross-service call
Kafka producer/consumer
Spring Security / token validation
DB migration
public DTO / SDK contract
bug fix
large refactor
```

重点观察：

```text
Impact Recall
Impact Precision
Flow Accuracy
Verification Recall
Policy Violation Recall
False Blocking Rate
Context Pack Relevance
Context Token Cost
```

其中建议优先保证：

```text
Verification Recall
Policy MUST Recall
High-risk Impact Recall
```

宁可多跑一个检查，也不要漏掉关键生产风险。

---

## 35. 核心命令速查

```bash
# 检测状态 Provider
python scripts/state_provider_detector.py .

# 安装 starter policy
python scripts/policy_bootstrap.py --repo .

# 决策分类
python scripts/decision_engine.py work-facts.json --strict-evidence --pretty

# 完整 V6 semantic intake
python scripts/semantic_intake_pipeline.py \
  --repo . \
  --request-file request.md \
  --policy-manifest .orchestrator/policies/manifest.yaml \
  --context-role implementer \
  --context-stage implementation

# Resume
python scripts/execution_state_manager.py \
  --state .orchestrator/execution-state.yaml \
  resume

# Context freshness
python scripts/context_plane.py validate \
  --repo . \
  --manifest .orchestrator/intake/context-manifest.json

# Session bootstrap
python scripts/session_context.py --repo . bootstrap --role implementer --stdout markdown

# Task/role handoff
python scripts/session_context.py --repo . handoff --from-role implementer --to-role reviewer --summary "Current task completed"

# Host adapter dry-run
python scripts/install_host_adapter.py --repo . --host all

# Host adapter install
python scripts/install_host_adapter.py --repo . --host all --apply

# Tests
python3 -m unittest discover -s tests -p 'test_*.py'
```

---

## 36. 核心理念

Coding Agent Orchestrator 的目标不是让 AI Coding 变成更重的流程，而是让流程强度与真实风险匹配。

```text
简单需求
→ 简单流程

复杂需求
→ 深度规划

模糊需求
→ 先澄清

高风险需求
→ 强验证

代码变化
→ 旧证据失效

项目规则
→ 可机器执行

上下文
→ 按角色/阶段精确投影

Agent 声称完成
→ 必须有 Fresh Evidence
```

最终希望达到的是：

> **让 Agent 在正确的时间看到正确的上下文，在正确的流程深度下工作，并且只有在有足够证据时才能宣布完成。**

---

## 37. 按需阅读与上下文预算

`SKILL.md` 是运行时路由器，不是文档目录的预加载清单。**不要按顺序一次性读取全部 references**；应根据当前阶段只读取 1～2 份最相关文档。

典型路由：

```text
新项目初始化      → references/project-bootstrap.md
流程/SDD 选择     → orchestration-model.md / adapter-contract.md
事实与分类        → fact-extractor.md / decision-engine.md
代码影响          → semantic-impact-engine.md
工程规则          → engineering-policy-layer.md
执行状态          → execution-state-manager.md
角色上下文        → context-plane.md
冷启动/交接       → session-context.md
宿主 Hook         → host-enforcement.md
Review/Verify     → quality-gates.md
```

可以运行上下文体积检查：

```bash
python scripts/context_footprint_check.py --repo .
```

默认门限用于防止 `SKILL.md` 重新膨胀；完整 reference 语料仍然可以保持丰富，因为它们是 lazy retrieval 的知识库，而不是常驻 Prompt。

需要针对具体 SDD 时再阅读：

```text
references/adapters-openspec.md
references/adapters-bmad.md
references/adapters-generic-sdd.md
```

需要理解 ECC Integration：

```text
references/ecc-rules-integration.md
```

需要理解 Superpowers Routing：

```text
references/superpowers-policy.md
```

---

## License / Third-party

本包包含对第三方工具和规则源的集成设计。第三方说明见：

```text
THIRD_PARTY_NOTICES.md
```

项目没有把外部规则源自动提升为项目治理权威。
