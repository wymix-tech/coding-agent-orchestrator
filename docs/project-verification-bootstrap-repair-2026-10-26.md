# 项目初始化与全局验证基线修复验收记录

日期：2026-10-26。基于 main `285fd24`，分支 `fix/project-verification-bootstrap`。

## 根因与修复

现有空项目 setup 已支持无 Work 初始化，但已有项目的声明状态允许 plan-only 进入 Work，`verification check` 又要求 active Work。主技能、模板和文档保留旧顺序，导致 Agent 在首个 Work 后才建立全局脚本。`.orchestrator/` 的整体状态保护及快照排除，也不能直接用于新的脚本输入目录。

现在，新 Work 必须先有真实必需检查并完成项目准备；空项目复用 setup plan/apply/check，已有项目复用实际入口并运行 verification check。已有 Work 不回退阶段，通过原基线门禁补齐缺失配置。初始化结果独立于 Work，不填充正式验证或 SDD 需求验收门禁。

CAO 管理的验证脚本统一使用 `.orchestrator/scripts/verification/`。项目原生构建文件、测试源码及用户自有入口保留原位。新增精确路径准备命令，host 仅放行已授权的脚本文件及受限 chmod，其他派生状态仍受保护。脚本、辅助文件、缺失状态和执行位纳入独立基线指纹，Python 缓存不参与指纹。项目准备按作用域保存，根级公共标准向模块继承。

新增命令示例：

```bash
cao verification prepare --scope root --path .orchestrator/scripts/verification/root/check.py
# 通过文件写入工具维护上面返回的确切脚本。
cao verification configure --test-script "python3 .orchestrator/scripts/verification/root/check.py"
cao verification check --scope root
cao bootstrap
```

在已有 active Work 下，`cao verification check --project --scope root` 保存项目准备证据；默认 check 保留已有 Work 的诊断探测语义。两者都不能代替正式 verify 或 SDD 验收。

`cao verification migrate` 迁移有 setup 所有权证据的旧脚本，保留原文件，更新已知命令与作用域工作目录，检查冲突并记录可重试映射。目标不一致则停止，未知所有权文件不移动。迁移后按适用规则审查/reanchor 并重新检查，不能沿用旧通过结果。

Git 忽略例外只放行脚本输入子树，并保留用户已声明的 `.orchestrator/` 保留规则。所有阶段指引、主流程图、宿主模板及中英文 README 已统一。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| Python：88 个测试文件，共 1057 项 | 1053 通过，4 跳过，0 失败 |
| 新增项目验证回归 | 13 项通过 |
| 现有项目 setup 回归 | 12 项通过 |
| Python compileall / git diff --check | 通过 |
| npm run check:pi | 通过 |
| npm run test:pi | 28 通过，9 失败 |
| 未修改 main 285fd24 的 Pi 对照 | 同样 28 通过、9 失败，失败测试名称完全相同 |

全量 Python 按测试文件分成四个独立子进程并行执行。最后的脚本路径与 Git 忽略规则调整后，重跑新增回归及相关 setup 检查。逐文件计数、跳过原因与 Pi 失败名称见同目录 JSON。

新增回归实际运行 CLI、初始化检查与 host-event，覆盖 plan-only 拦截、无 Work 精确写入、第二个 Work 不继承通过结果、历史 Work 连续性、辅助文件指纹及缓存、零测试、作用域隔离、迁移冲突/中断/重试和含空格的模块工作目录。历史生命周期测试夹具的准备状态有明确的测试模拟标记；不将这些夹具宣称为真实外部 provider 验收。

## 验证边界

Pi 的九个失败在未修改 main 的独立 worktree 中复现，主要来自 BMAD 安装夹具不满足当前安装健康检查，本次未扩展为该夹具修复。没有声称完成真实模型驱动的 BMAD、OpenSpec、Spec Kit 全流程端到端验证。

历史脚本仅在所有权明确时自动迁移；未知用户脚本继续原位使用。原文件有意保留以支持回滚，迁移后不应再将其视为新生成脚本的默认位置。
