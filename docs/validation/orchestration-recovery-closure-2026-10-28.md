# CAO 编排恢复与证据闭环修复验收

本报告记录首轮实现及 1,214 项测试。后续评审发现旧收据可解除新产品修复要求；修复、反向回归和最新结果见 [编排恢复评审补齐](orchestration-review-followup-2026-10-28.md)。不得将本轮通过数量解释为覆盖全部保护条件。

日期：2026-10-28。基线：main `695173d14eeccc274015c39673d20df5b2711aa4`。
分支：`fix/orchestration-recovery-closure`。本文以代码和离线真实 CLI 验证为依据；不等同于线上模型组合验收。

## 修复内容

| 问题 | 根因 | 实现与修复后行为 |
|---|---|---|
| 项目检查失败后无法修复产品 | 最新项目执行结果覆盖了已有验证能力认证 | `verification_probe.py` 分开保留 certification 与最新结果。相同标准、有效历史认证及活动 Work 下，明确断言失败允许当前 Work 修复；首次准备、标准漂移、环境与未知失败保持阻塞。 |
| 不透明测试失败没有诊断出口 | 日志不足以自动区分产品、环境和基线 | 复用 `lifecycle repair`，将诊断绑定到当前项目检查 run_id。只有有效认证且标准一致时接受产品修复诊断；unknown/environment/baseline 不自动获得产品写入资格。contract 1/2 均可使用项目诊断。 |
| 原生成功执行阻塞 resume/switch | 运行查询未读取 completion_executions | `runtime_store.py`、`runtime_queries.py` 将 native_completion 与 verification、sdd_acceptance 分别映射。成功、失败、取消、过期和 abandoned 是已知终态，不自动授予成功。缺失投影不再成为未决执行。 |
| execute 后日志不含 Command 导致 bind 指纹漂移 | 依赖仅从报告文本重建 | 执行前从实际已绑定命令捕获依赖，保存到现有执行行及依赖声明。刷新原始日志后仍用相同依赖集合检测 wrapper/helper 变化，不改写 stdout 为伪造报告。 |
| 纠正参数仍暂停 | 参数错误混入报告修复，暂停请求身份不完整 | gate/report 请求错误直接返回可执行的参数纠正提示。报告修复签名包含规范化 report/gate/evidence 请求；排序和重复参数不能绕过暂停。旧签名先重新校验，再按新身份记录。 |
| 同字节报告绑定不清理产品修复 | unchanged 分支提前返回 | 收据、generation、输入及依赖校验通过后，在同一受保护提交内清理并归档修复，即使证据主体不变。重复绑定不重复归档。最新失败不能靠旧成功解除修复。 |
| 外部命令可能无限等待 | 安装、inspect、transition 直接使用无超时 subprocess | `provider_execution.py` 复用运行运输、进程组清理、取消和有界日志。安装默认 900 秒，inspect/transition 默认 300 秒，均使用现有 timeout 校验。外部执行期间不持有仓库写锁。 |
| transition 中断后重复外部效果 | 缺少持久操作身份及恢复事实 | 先保存 transition_runs 操作，再执行，再提交结果。成功记录可复用；非零结果和中断保留未知状态。即使删除/更改配置，未知操作仍阻止继续；resume/switch 也消费这一未决状态。 |
| 上下文重复扫描 | next、conformance、completion、evidence 各自求值 | 仅在单次只读评估内显式传递结果，复用 technical inputs、native prerequisites 和 completion。没有跨命令缓存；两次调用之间的产品修改仍使证据失效。 |

## 持久化及历史兼容

- 未更改 lifecycle contract 编号，不要求重建 Work，也不要求批量迁移。
- 项目检查增加 certification、results、at。现有 passed 记录可以作为相同标准的认证；只有历史 project probe 中确实 passed 且标准匹配的事实才能恢复认证，failed 不会被升级成功。
- 已有 completion_executions 直接进入统一运行查询，无须重跑来修复查询状态。投影缺失可用 runtime rebuild 重建；投影不成为权威结果。
- 原生执行行增加依赖及显式 retry 处置事实。报告、输入、run_id、generation 仍须匹配；旧登记方式仅在原有兼容边界内有效。
- transition_runs 为新增可选历史，旧 Work 无记录时按原行为执行。操作开始、结果、诊断证据与人工声明保留在受保护 Work 历史中。
- 原生 finalize 不套用 verification 的 admission 格式。已存在权威终态可读；中断原生执行缺少可安全重建的完整 admission 时明确拒绝 finalize，要求检查效果、显式 abandon，再重新执行。

## 已受影响 Work 的恢复操作

1. 项目准备已有认证但产品测试失败：先读项目检查原始结果。明确断言失败会允许活动 Work 修复；不透明失败先执行 `cao lifecycle repair --kind product --reason "已检查的具体根因"`。首次准备或标准变化仍须完成原有准备、审阅和 reanchor。
2. 产品修复完成后：执行已绑定的真实原生命令，例如 `cao lifecycle execute --command "<原生命令>" --report <provider-原始日志>`，再 `cao lifecycle bind --report <原始日志>`，最后 `cao verify`、`cao done`。原始日志无需自行包含 Command。
3. 成功原生执行后 resume/switch 被旧查询卡住：更新代码后重新查询 `cao runtime show --run <id>`，再 resume 或 switch。若只有投影缺失，可执行 `cao runtime rebuild`；不要编辑 Work JSON。
4. 真实中断、效果未知的原生执行：等待/检查实际进程和产物，保存检查文件，执行 `cao runtime abandon --run <id> --evidence <检查文件> --reason "具体检查结论"`。这不认证成功；下一次执行使用新 generation。显式 `--retry-of` 保留声明和旧运行的处置事实。
5. 外部命令诊断：用 `cao runtime runs --work _unassigned` 找到运行，再 show/logs。取消使用 `cao runtime cancel --work _unassigned --run <id> --reason "原因"`，等待执行器收据确认取消。
6. transition 结果未知：检查外部 provider 实际状态，保存检查证据，然后 `cao sdd transition resolve --work <work-id> --operation <operation-id> --outcome applied|not-applied --evidence <检查文件> --reason "检查结论"`。applied 不再重放；not-applied 才允许新的显式尝试。这是检查声明，不伪装成命令执行成功。
7. 参数写错：修正 gate/report/evidence 参数后重新 bind。真正报告失败重复达到暂停阈值时，应修复报告或来源；无需为了参数错误改动产品或伪造日志。

## 回归及环境

执行环境：Linux、Python 3.12.14；本地 provider 和宿主事件 fixture，无网络模型调用。

| 检查 | 实际结果 |
|---|---|
| Python 全量 | discovery 1,214 项；六个分片执行 1,214 项，1,210 通过、4 跳过、0 失败 |
| 新增恢复及修复边界定向回归 | 24 项通过；最终全量另覆盖全部 17 条新增恢复场景 |
| `npm run test:pi` | 39/39 通过 |
| `npm run check:pi` | 通过 |
| Python compileall / git diff --check | 通过 |
| `python tools/release/build_release.py --check` | 通过，195 个运行时文件 |
| wheel 构建、独立 venv 安装及 installed_package_smoke.py | 通过，24 个离线安装后场景 |

全量分片保留 discovery 的 tests 导入路径，每个测试模块只分配一次，六片用例之和与 discovery 的 1,214 相等。命令为 `PYTHONPATH=.:tests python -m unittest <分片模块列表> -q`，不是把重复执行的定向测试叠加为全量数量。结果及安装场景明细保存在 [orchestration-recovery-results-2026-10-28.json](orchestration-recovery-results-2026-10-28.json)。

远端实现提交：`b6b226a`（五个缺陷）、`1d564eb`（受控外部命令）、`1aa90d7`（恢复兼容及评估复用）、`2db51a1`（未知效果及取消）、`101b2c1`（不透明失败诊断）。此前的 `9de6768` 保存五个失败复现，`7274b87` 保存实施提示词。最终报告提交另外补充了真实产品修复至关闭的完整回归。

第一轮全量 1,210 项中发现一处安装测试模拟边界过时：测试仍拦截 subprocess.run，而实现改为受控 runner。已将模拟移到 `cao_providers.run_install_command`，保留失败安装不产生 restart 边界的原断言。一次定向命令包含不存在的 test_verification_probe 模块，已纠正为实际项目准备测试；该导入错误不计为通过。

新增 `test_orchestration_recovery_closure.py` 使用临时项目及真实 CLI，覆盖五个原缺陷、暂停参数规范化、投影缺失、环境/未知失败、显式原生 abandon、项目命令取消、transition 中断处置、跨调用失效，以及 execute → bind → resume → switch 往返 → verify → done。故障注入仅用于中断提交窗口；没有 mock 掉所验证的状态判定。

## 性能测量

可复现工具：`python tools/benchmark_context.py /absolute/checkout 500 label`。
数据：[orchestration-context-benchmark-2026-10-28.json](orchestration-context-benchmark-2026-10-28.json)。
每组 12 次，P95 使用 nearest-rank；500 个额外 Python 文件合计 455,890 字节。测量时共享容器还在运行回归分片，不能据此作生产 SLA 承诺。

| 场景 | 基线 P50 / P95 | 修复后 P50 / P95 |
|---|---:|---:|
| 小样例 render_context | 0.172 / 0.213 秒 | 0.070 / 0.101 秒 |
| 500 文件 render_context | 0.592 / 0.732 秒 | 0.114 / 0.141 秒 |
| 小样例 host pre_tool | 0.295 / 0.371 秒 | 0.242 / 0.276 秒 |
| 500 文件 host pre_tool | 0.460 / 0.479 秒 | 0.394 / 0.522 秒 |

一次 render_context 的 code_snapshot 从 9 次降至 1 次，capture 从 3 次降至 1 次，project_prerequisite 从 6 次降至 4 次。host pre_tool P95 在较大样例中未改善，后续须单独分析进程启动、文件系统和锁等待，不能宣称所有宿主入口都已提速。

## 边界

- 尚未进行真实 BMAD/OpenSpec/SpecKit × Claude Code/Codex/Pi 模型组合验收；离线适配器及 provider fixture 通过不能代替真实安装和模型行为。
- 本次进程清理和取消在 Linux 验证，macOS/Windows 需由 CI 对应平台测试确认。
- 安装命令沿用原有非交互参数；需要交互的第三方安装应在宿主终端明确完成，不能让 CAO 静默等待交互。
- 单次评估复用不是文件系统事务快照。外部编辑不受仓库状态锁控制，实际执行提交前仍须重新校验输入。
- 未引入后台调度器、第二套验收器或通用审批流程；`start --defer` 仍只是入队。
