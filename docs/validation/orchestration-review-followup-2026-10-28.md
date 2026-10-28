# 编排恢复评审补齐

评审基线 `ba09779`；实现 `282dc71`。原验收报告的通过数量仅对应旧用例集合，本文件补充反向保护及验收映射。

## 修复及兼容

产品 repair 记录新增 execution_boundary，保存分类时已经存在的全部原生 run_id。receipt_matches 只采用边界之外、当前 generation、输入及依赖匹配的 passed 收据，每个报告必须分别匹配。不使用秒级时间戳比较，避免同秒执行被误拒绝。重复 bind 仍只归档一次。

历史 product repair 缺少 execution_boundary 时，bind 保守拒绝。下一次 lifecycle execute 在受保护事务中先捕获现有运行集合，再登记新运行；无需手工改状态、重建 Work 或删除失败历史。新运行失败、过期、被替换或没有覆盖所有报告时，仍不能清理 repair。

多报告测试另外发现：写入此前未绑定的报告路径会改变产品指纹，导致真实成功执行被误判输入变化。现在执行前记录 native_execution_reports，技术输入扫描将这些经过 provider 路径及非可执行报告格式校验的文件视为输出。报告仍独立校验原文哈希，实际产品修改仍使执行 stale。

## 验收映射

| 提示词义务 | 验证入口及证据 | 范围 |
|---|---|---|
| 五个原缺陷 | test_orchestration_recovery_closure 的前五个原始回归 | 真实临时项目、CLI |
| 项目失败、未知诊断、首次准备及标准漂移 | test_failed_project_product_check_allows_repair、test_opaque_project_failure_requires_explicit_product_diagnosis、test_project_verification_bootstrap | 当前 Work 修复权限与最终验收分离 |
| runtime 终态、未知效果、投影 | test_native_success_is_resolved_and_resumable、test_missing_projection_does_not_make_native_success_unresolved、test_runtime_recovery、test_continuity_operations | 已知终态不重新变未决，投影不授予资格 |
| 依赖和失效 | test_wrapper_execution_keeps_dependencies_without_command_in_log、test_native_lifecycle_contract、test_native_commit_rejects_inputs_changed_during_execution、test_static_shell_helper_change_invalidates_native_receipt | wrapper 原日志、产品及报告变化、执行期间外部修改 |
| 参数暂停 | test_corrected_gate_request_can_bind、test_duplicate_report_argument_cannot_bypass_pause | 纠正参数可重试、重复参数不绕过暂停 |
| 新收据边界 | test_identical_binding_finishes_product_repair_once | 先断言旧收据被拒绝，再执行新命令，绑定后只归档一次 |
| 历史 repair | test_legacy_repair_requires_new_execution_without_timestamp_guessing | 缺边界旧记录拒绝旧收据，新 execute 恢复 |
| generation | test_new_execution_in_old_generation_cannot_resolve_repair | 新执行也不能跨 generation 解除标记 |
| 多报告 | test_multiple_reports_require_post_repair_receipt_for_each | 只有一个新报告收据时拒绝；两份齐全后通过 |
| 绑定提交中断 | test_binding_commit_failure_keeps_repair_recoverable | 注入持久化失败，标记保留，可重试成功 |
| 外部执行异常 | test_verification_runner、test_external_command_timeout_has_receipt_and_bounded_output、test_project_owned_provider_command_can_be_cancelled | 运输层真实进程的超时、大输出、保留管道、进程清理；provider 层超时和取消 |
| 外部执行不占写锁 | test_state_transactions.test_start_inspect_runs_without_lock_and_returns_fresh_state、test_done_rechecks_after_unlocked_transition | 并发修改后重新判断 |
| transition 效果未知 | test_transition_commit_interruption_requires_inspection_without_replay、test_unknown_transition_blocks_recovery_until_inspected | 效果已发生但提交中断，检查后处置，无自动重放 |
| 评估缓存失效 | test_context_evaluation_is_not_cached_across_calls | 同进程两次评估之间修改文件，决策更新 |
| 状态并发和宿主桥接 | test_state_transactions、test_pi_adapter.mjs | 状态写入冲突、桥接超时等既有契约回归 |
| 跨 provider/宿主 | installed_package_smoke.py | 24 个离线 fixture 场景，不代表真实模型验收 |
| 性能 | tools/benchmark_context.py 与原 benchmark JSON | 小样本与 500 文件集合、调用次数、输出大小、P50/P95；不宣称扫描等于事务快照 |

运输层异常用例不能替代每个第三方安装器的交互测试；以上映射明确区分层次。需要交互的安装，应在项目目录的宿主终端运行安装结果中 command 字段所列的实际命令，完成后重新运行 cao sdd install 以检测安装状态。CAO 受控安装入口继续使用既有非交互参数与有界等待。

## 本轮结果

- 修复前反向测试确实失败：没有新执行时 bind 返回 0，违反保留修复标记的断言。
- 最终 discovery 1,220 项；六个不重复模块分片共执行 1,220 项：1,216 通过、4 跳过、0 失败。包含最后新增 helper 测试的第 0 片以复验结果替换，未重复累加首轮和定向测试。
- 恢复核心共 23 条测试；包含新收据边界、历史兼容、多报告、generation、提交中断、执行期间输入变化及静态 helper 失效。
- Pi 39/39、TypeScript、compileall、diff 检查通过。
- 运行时发布清单 195 个文件；新版 wheel 独立安装后 24 个离线场景通过。
- 结构化结果见 [orchestration-review-results-2026-10-28.json](orchestration-review-results-2026-10-28.json)。

本轮没有修改性能求值策略，原性能测量继续作为对应实现的样本证据；未把定向测试通过数与全量相加。

## 保留的环境边界

真实模型/provider/宿主组合、macOS/Windows 清理需要对应环境执行。已有明确的离线验证与平台边界，不将未执行项目计为通过。较大文件集 host-event P95 尚未稳定改善，保留原测量结果。对全部可能的第三方命令、并发时序与日志格式不作穷尽覆盖声明。
