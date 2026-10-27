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

## 验证结果

| 检查 | 结果 | 范围说明 |
| --- | --- | --- |
| `make check` / `git diff --check` | 通过 | Python 编译及变更格式 |
| 全量 Python 回归，按文件隔离运行 | 89 个模块、1077 项通过，0 失败 | 使用与 unittest discover 相同的 `test_*.py` 文件，6 个并行测试进程；约 152 秒 |
| 新契约定向回归 | 16 项通过 | 包含全量执行之后补充的 JSON 报告校验；与上行存在重叠，不能相加为总测试数 |
| Dashboard 回归 | 27 项通过 | 修正后续 UI 投影及原生评审提示 |
| 技能资源约束 | 通过 | 主技能小于 10000 bytes、引用可发现、阶段指南具备必需章节 |
| `npm run check:pi` | 通过 | TypeScript 类型检查 |
| wheel 构建、隔离环境安装 | 通过 | 离开源码 checkout 使用安装入口与资源 |
| 安装后的新契约闭环 `installed_package_smoke.py --native-only` | 3 个场景通过 | 模拟 Claude Code/Codex/Pi Host 事件；实际项目初始化、本地测试、原生报告绑定、conformance、verify、done |

16 项定向回归涵盖：默认契约、无第二执行器闭环、报告缺失/空白/越界/篡改、产品变化、幂等绑定、旧失败迁移、历史不可变、多单元未完成、原生 reviewer preflight、运行与迁移竞争、风险检查归属、基线变更、BMAD in-review、Spec Kit 共享策略、跨 Work 结果复用和无效/失败 JSON。部分项目在同一测试方法中以多种输入验证。

## 已识别但未计为通过的检查

### Node Pi 适配器旧夹具

`npm run test:pi`：37 项中 28 项通过，9 项失败。在独立的原 main
`c9b98e2` worktree 上使用相同锁文件依赖复跑，也是 28 通过、9 失败；
失败测试名称集合完全相同，主要为不完整 BMAD marker 夹具和旧初始化顺序断言。
本次未通过降低生产安装/初始化门禁来消除这些既有失败。

### 旧完整安装验收矩阵

默认 `installed_package_smoke.py` 的旧矩阵仍在 `bootstrap.mode == select`
断言处停止，其期望未跟随项目准备前置流程更新。本次增加了独立的
`--native-only` 范围，验证新契约在实际安装环境中的完整本地闭环；
不能将该结果表述为旧矩阵全部通过。

### 真实模型会话

未执行真实 Pi + 实际模型 + 官方安装 provider 的端到端对话。模拟 Host、
测试 provider fixture、本地 CLI 与真实模型工作流属于不同验证层级。
本次没有虚构独立 reviewer、原生语义验收或人工批准。

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
cao lifecycle migrate --work <existing-id>
```

迁移不产生通过证据；跟随返回动作完成原生范围、绑定或恢复基线。Completed Work
需显式 reopen 才可验证当前输入。不要让不理解契约 2 的旧版程序继续写入迁移后的 Work。

本记录覆盖实现和本地验证；真实模型会话及上述既有夹具债务仍需单独验收。
