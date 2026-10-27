# CAO / SDD 生命周期修复验收记录

日期：2026-10-27。基线 main：`c9b98e2`。分支：`fix/sdd-cao-lifecycle-boundary`。

## 已实现

- 新 Work 默认生命周期契约 2；已有无版本/契约 1 的 Work 保留旧协议，可通过 `cao lifecycle migrate --work <id>` 迁移。
- SDD 规划、实现、测试、代码评审、修复、原生验收和必需收尾属于原生执行。`in_review` 不推动 CAO review。
- CAO review 的资格由原生流程、全部关联单元、实际规划/评审/批准和必要收尾共同决定；单个 SPEC done 或 workflow complete 不自动授予完整完成资格。
- `cao lifecycle bind --report <native-report>` 绑定原生完成/测试验收结果，保留原生格式、指纹、Work/需求/范围/产品输入与既有执行来源。风险专属原生检查用重复 `--gate` 声明报告覆盖。
- CAO conformance 前置检查与本轮项目验证分开，没有“先通过未来验证才能评审证据”的循环依赖。
- 正式 `cao verify` 先检查原生完成与证据链，然后只执行已接受项目基线；契约 2 无额外逐条功能 executor/JSON 映射配置要求。
- 产品/需求/范围变化撤销相应完成证据；绑定修复和仅基线传输配置变化不自动重启全部原生评审。基线变更仍保留原来的审批/reanchor 义务。
- 精确准备的基线文件仍经现有注册与路径保护；恢复控制命令可通过 Host 门禁，未注册状态文件不能任意改写。
- 迁移保留身份、旧失败、原始绑定和历史；运行中的验证/验收禁止并发迁移，并使用 generation 防止旧结果取得新授权。
- 其他 Work 已认领的原始结果不能直接复用。JSON 语法/结构和显式失败状态受检查；报告绑定不等于重新进行语义功能评审。
- 已完成 Work 冻结完成时的结果；迁移不能静默修改历史。知识收集保持非阻塞。
- 技能、完成/恢复指南、双语 README、CLI 输出及 dashboard 已对齐新旧契约。

## 本轮补齐

- 三个 provider 共用原始 Markdown/平面 YAML/JSON/运行日志适配器，记录解析观察与适配器版本；明确失败、零检查、身份冲突、错误范围和未知文本均不能视为通过。不会改写原报告。
- 分离产品、原生来源/范围、原始报告、原生执行/评审依赖、策略、项目基线依赖域；失效诊断列出具体路径、受影响及仍有效证据。仅报告变化不会使未变的项目基线失效。
- 管理目录内共享原生依赖可显式声明；跟踪静态 shell source，动态依赖需声明。仅基线传输变化与真实原生输入变化分别处理。
- 两次相同输入绑定失败后暂停，不再修改状态；原始报告或输入修正后可继续，不重新 intake 或启动全部原生评审。
- 迁移新增只读 preflight/check、完整原始 Work 快照、迁移/回滚中断恢复；回滚拒绝覆盖迁移后的进展或来源变化，保留审计并推进 generation。
- 契约 2 持久化保存最后授权状态；拒绝不支持的旧 writer、契约降级及损坏状态。恢复隔离原始异常字节，保留授权成果。该机制是当前引擎的授权状态校验，并非操作系统写隔离，不能阻止旧二进制物理改写文件。迁移前应停止旧 writer。
- 修复 Pi 测试夹具与安装矩阵中未跟随 PR #28 更新的初始化顺序，保留项目基线前置要求。

## 验证结果

全量发现 90 个模块、1095 项测试。首次全量仅指南目录检查失败，补齐长指南 Contents 后该模块 14 项全部通过；其余 89 个模块通过。重复定向执行不计入总数。

| 检查 | 结果 | 范围说明 |
| --- | --- | --- |
| `make check` / `git diff --check` | 通过 | 编译与格式检查 |
| 全量 Python 回归及失败修正复跑 | 90 模块、1095 项通过 | 全量约 162 秒；指南模块修正后定向复跑 14/14 通过 |
| `npm run test:pi` | 37/37 通过 | Pi adapter 的真实 CLI/模拟 Host 事件与 UI 合约 |
| `npm run check:pi` | 通过 | TypeScript 类型检查 |
| wheel 构建、隔离环境安装 | 通过 | 安装入口与资源离开源码执行 |
| 完整安装矩阵 | 24/24 通过 | 旧协议场景及新增三个 Host 的契约 2 本地闭环 |

此前记录的 Pi 9 项失败和旧安装矩阵初始化断言已修复；本记录以本轮最终结果为准。

## A01—A26 自动化覆盖索引

以下是覆盖位置，不把 provider fixture、模拟 Host 或本地 CLI 等同真实模型/官方 provider 对话验收。目录均相对 `tests/`，测试文件名省略 `test_` 前缀。

| 项目 | 覆盖位置与检查 |
| --- | --- |
| A01 | `native_lifecycle_contract.py`：BMAD in_review 不能取得 CAO review/verify 资格 |
| A02 | `review_round_integrity.py`、`review_convergence.py`：修复、复核与实际输入刷新 |
| A03 | `native_lifecycle_contract.py`、`task_provider_verify.py`：全部关联单元聚合 |
| A04 | `native_lifecycle_contract.py`、`lifecycle_integrity.py`：原生完成后 conformance/verify/done |
| A05 | `native_route_sync.py`、`native_sync_binding.py`、`lifecycle_integrity.py`：绑定不证明执行；未授权手改被拒绝 |
| A06 | `lifecycle_integrity.py`：三个 provider 原格式适配；不转换报告 |
| A07 | `native_lifecycle_contract.py`、`lifecycle_integrity.py`：缺失/空白/篡改/跨 Work/版本身份失败 |
| A08 | `review_convergence.py`、`lifecycle_integrity.py`：绑定变化与未变原生/基线证据分离 |
| A09 | `native_lifecycle_contract.py`、安装契约 2 场景：无独立 executor 闭环 |
| A10 | `native_lifecycle_contract.py`、`lifecycle_repair_boundaries.py`：拒绝提前 verify；原生 build/test 非只读 |
| A11 | `native_lifecycle_contract.py`：首次正式验证前可完成 conformance |
| A12 | `verification_runner.py`、`review_convergence.py`、`lifecycle_integrity.py`：日志与 JSON 协议分开 |
| A13 | `verification_runner.py`、`verification_preflight.py`、`lifecycle_completion_matrix.py`：失败/缺工具/超时与零测试边界 |
| A14 | `native_lifecycle_contract.py`、`lifecycle_integrity.py`、`snapshot_integrity.py`：产品改动及精确路径失效 |
| A15 | `native_lifecycle_contract.py`、`lifecycle_integrity.py`：基线传输与共享原生依赖分别失效 |
| A16 | `lifecycle_completion_matrix.py`、`native_route_sync.py`：语义稳定的状态变化保留有效证据 |
| A17 | `task_provider_verify.py`、`plan_scope.py`：新增/删除/重开任务不缩减范围 |
| A18 | `lifecycle_completion_matrix.py`、`path_boundaries.py`：精确准备、越界与软链接保护 |
| A19 | `native_lifecycle_contract.py`、`lifecycle_integrity.py`：迁移保留旧失败；不产生新通过证据 |
| A20 | `lifecycle_integrity.py`：完整回滚、迁移前后中断、历史不变、重复操作幂等 |
| A21 | `native_lifecycle_contract.py`、`verification_bindings.py`、`lifecycle_completion_matrix.py`：运行 owner 与 generation |
| A22 | `bmad_stable_compatibility.py`、`native_route_sync.py`、安装矩阵：dispatch/oneshot/full planning 原生义务 |
| A23 | `task_provider_verify.py`、`openspec_lifecycle_repairs.py`、`lifecycle_integrity.py`：split 范围、归档、checklist 与两 provider CLI 完成 |
| A24 | `path_selection.py`、`native_route_sync.py`、安装矩阵：深度不同、现有职责约束保留 |
| A25 | `project_verification_bootstrap.py`、`project_setup_lifecycle.py`、Pi/安装矩阵：项目准备先于 Work |
| A26 | `lifecycle_integrity.py`、`review_convergence.py`：重复失败暂停、输出稳定、修正后恢复 |

## 真实模型会话：仍未完成

Pi 1.1.0 实际模型连通探测返回 `No API key found for the selected model`。
当前运行环境未配置选定模型的登录/密钥，因此未完成真实 Pi + 实际模型 + 官方安装 provider
的端到端对话，也不能据此声称 P7 全部完成。没有虚构 reviewer、语义验收或人工批准。
本地 CLI 调用、真实本地测试执行、安装包验证与模型对话属于不同验收层级。

## 使用与恢复

新 Work 完成原生全部工作后：

```bash
cao lifecycle bind --report <original-native-report>
cao conformance check
cao verify
cao done
```

多个报告重复 `--report`；风险义务按真实原生结果追加 `--gate`。旧 active/suspended Work：

```bash
cao lifecycle preflight --work <existing-id>
cao lifecycle migrate --work <existing-id> --check
cao lifecycle migrate --work <existing-id>
```

迁移不产生通过证据；跟随返回动作完成原生范围、绑定或恢复基线。Completed Work
需显式 reopen 才可验证当前输入。不要让不理解契约 2 的旧版程序继续写入迁移后的 Work。

回滚、保护、隔离恢复及依赖声明的操作见 `references/native-lifecycle.md`。本记录覆盖实现与本地验证；真实模型会话仍待具备模型登录的环境验收。
