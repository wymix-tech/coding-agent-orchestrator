# BMAD 稳定版流程兼容修复与验收

日期：2026-10-26。基于 CAO `ff94148ad6d4ec5d522acba86714d9f138d4bace`，修复分支 `fix/bmad-stable-workflow-compatibility`。

本次只处理当前稳定版协议，默认安装固定为 BMAD 6.12.4。官方源码基线为 `bab9082c3e884e0d72c759bcd1a6d90bcc01be84`。没有实现 V7 的 full、built、票据树或新评审选择协议。

## 已修复的行为

| 问题 | 修复后的行为 |
| --- | --- |
| bad_spec / intent_gap 要求先撤销再重推，但评审门禁同时阻止写入 | `cao sdd review loopback` 在全部层分类后归档旧轮次，记录原因、KEEP、基线、文件范围，并进入显式重实现阶段。原生旧 done 状态不能把阶段投影回完成。 |
| 撤销与新实现无法区分 | 只有记录的原始 Git revision + 精确文件 restore，或与原始指纹一致的整文件写入，可在重新批准前恢复。新建文件可按原始变更清单删除；禁止无范围 reset/restore、路径通配和受保护状态。一般重实现仍经过原有前置门禁。 |
| 原生重新规划可能涉及新文件 | `loopback --extend` 追加有原因的精确文件范围，保留原评审历史。只允许建立已记录文件的父目录；受信任的已配置验证命令仍可执行。 |
| 旧批准或旧评审被带入回退后实现 | intent_gap 撤销批准；实现交接、验证和门禁证据失效。完成原生重推后创建新评审轮次。需求本身变更时旧回退记录进入历史，不能继续套用旧文件范围。 |
| stories.yaml 无法绑定或只验证当前 SPEC | 支持原生有序 stories 库存及 `stories/<id>-*.md` 记录；在绑定 SPEC 旁自动发现。全部故事都必须完成，缺失、重复、未知状态、新增、删除、回退均有具体诊断。 |
| Work 跨故事后只保留最后一个故事的评审 | 切换原生 Build route 前检查上一个故事的完成状态、路由和真实评审；分别保留批准、路由、评审与文件指纹。后续故事不能继承前一个故事的评审完成效力。 |
| SPEC done、sprint story review 时没有明确后继动作 | 保留严格 done 门禁，明确交接到安装的 bmad-code-review 和匹配 story key。没有把 review 偷换为 done，也没有在 CAO 再加一轮同样的代码评审。 |
| 完整规划被固定文档类型绑死 | 保留既有 PRD/architecture/epics/sprint 合同；另支持有原生规则依据的 requirements/design/execution 职责映射。执行职责必须对应可检查的 spec、sprint 或 stories。原生 Finalize、审计、评审及 CAO 质量门禁仍适用。 |
| 自定义输出目录与默认目录假设冲突 | 读取稳定版 TOML 的四层合并配置，统一应用于写入准备、产物归属、路由、计划、评审和验收读取。配置根不能逃出仓库、经过符号链接或冒充保护目录/常见源码根。配置变化使路由失效，证据搬迁不能偷偷改变职责或根目录。 |
| 空 _bmad 目录被视为已安装并跳过修复 | 区分 detected、installed、initialized、healthy、compatible；检查真实 Build/runtime 资产和配置。空标记不授权自动选择 provider；已选 provider 的不完整安装明确进入修复。保留原有 legacy workflow 通道。 |

## 一项审查结论的更正

稳定版官方 renderer 在全部评审层 instruction 为空时，确实会成功生成快照，但其输出明确写着：

`No active review layers. HALT with blocking condition ...`

因此本次没有“兼容零层评审并继续实施”，而是把这个原生停止条件报告为 `NATIVE_REVIEW_DISABLED`，要求恢复原生层并重新渲染。前一份审查把它仅描述成 CAO 对合法配置的限制，遗漏了原生停止指令；这里以完整源码为准。

## 运行指引与模块

实际用户流程写在 `references/provider-bmad.md`、`references/bmad-review-dispatch.md`、`references/native-route-contract.md`，不依赖本开发文档。

新增模块分别负责稳定版项目配置/健康检查（`bmad_project.py`）、故事库存（`bmad_stories.py`）、跨故事证据保存（`bmad_units.py`）、受控回退（`native_loopback.py`）。新增 Work schema 字段记录回退及原生单元证据。

Python 3.11+ 使用标准库 TOML 解析器；Python 3.10 通过已声明的条件依赖 tomli 读取相同配置，CI 安装包时不再跳过所需依赖。

## 验证

新增 21 个稳定版兼容性测试，包含官方源码交叉验证、真实 CLI 故事交接、替代规划载体的待办门禁、受控撤销、无 VCS 原始字节恢复、范围扩展、需求版本变化、安装检测及自定义目录边界。测试复用了现有真实 CLI / 主机事件夹具，夹具的独立本地检查进程不冒充大模型评审。

全量 Python 回归按模块分组运行：294、242、242、263 项，共执行 1041 项，1037 项通过、4 项跳过。后续针对最终边界与需求版本变化追加的 76 项集成/完整性检查全部通过；这些补跑包含已有测试，不与全量数量简单相加。环境不具备 Go、Gradle/JDK、Maven/JDK、本地 TypeScript 工具链，对应 4 项真实工具链用例跳过，不记为通过。符号链接用例在替换已有运行时夹具文件后实际执行，不因目标已经存在而误记为平台不支持。

官方 renderer 交叉验证的可重复命令：

```bash
CAO_BMAD_STABLE_SOURCE=/absolute/path/to/BMAD-METHOD-v6.12.4 \
  python -m unittest tests.test_bmad_stable_compatibility -v
```

完整 Python 回归：

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

安装包冒烟在仓库外、独立虚拟环境运行 `tests/installed_package_smoke.py`，24 个 provider / 主机 / profile 夹具组合全部通过。检查了安装后的新模块及相关生命周期模块字节指纹，与最终源码一致；编译检查和 `git diff --check` 同样通过。冒烟验证 wheel 的运行时资源、入口及原有三种 Provider 的流程合同；不代表实际启动了 Claude Code、Codex、Pi 或进行了真实模型会话。

本次没有提供真实 Pi 模型会话 E2E、Windows/macOS 实机结果或缺失工具链的实测结论。自然语言需求满足与代码评审语义质量仍须按真实项目执行验收。
