# 第三轮评审修复：evidence 纠正后的暂停恢复

基线 610d549；生产修复 86c1e0a。日期：2026-10-28。

## 触发与根因

先附加有效 evidence，再改变原报告，通过同一 evidence 连续绑定两次，进入暂停；恢复正确的原文后，同一请求仍暂停。select 在校验报告哈希时提前抛出 IMPLEMENTATION_EVIDENCE_STALE，尚未向 args.report 返回路径，失败签名只包含空 reports。报告纠正不会改变该签名。

新增真实 CLI 回归在修改生产代码前确实失败：恢复原文后返回 NATIVE_COMPLETION_REPAIR_PAUSED。修复在 completion_repairs.signature 中通过现有 implementation_evidence.records 读取受保护且校验完整性的选择记录，观察对应路径当前哈希、missing 或 unavailable 状态，并将选择记录纳入签名。此观察仅用于决定是否重新校验，未代替 select 的 generation、输入、原始哈希、需求和后续失败检查。

旧暂停记录没有选择指纹，首次读取时签名不匹配，进入完整重验；相同坏状态按新签名达到阈值后继续暂停，不清除历史。选择记录损坏时只记录不可用诊断，绑定仍须通过原有完整性校验。

## 验证

- 新回归覆盖过期→两次失败→暂停→相同请求不追加事件→恢复原文→同一请求成功，成功后清理绑定失败标记。
- 额外真实 CLI 检查：报告删除后恢复、旧空 reports 暂停记录均可恢复；旧记录使用受保护写入接口构造兼容性夹具。
- 完整回归结果见同名 JSON；六个互不重叠测试模块分片，不累计定向重复用例。
- Pi、TypeScript、compileall、diff、发布清单和新 wheel 独立安装后的离线 smoke 均执行本轮验证。

已有 Work 更新后重试原来的 `cao lifecycle bind --evidence <id>` 即可。原文恢复使签名失效并重新校验，无需编辑受保护 JSON、重建 Work 或重跑产品测试。若文件仍与登记哈希不同，必须修复来源或登记真正的新证据，不能靠暂停恢复绕过过期校验。

本轮不改变性能评估策略。保留此前宿主事件 P95 波动、真实模型/宿主组合和 macOS/Windows 清理未验收的边界。

最终结果：Python 1223 项，1219 通过、4 跳过、0 失败；核心 26 项包含于全量。Pi 39 项、TypeScript、compileall、diff、195 文件发布清单、新 wheel 独立 venv 安装后 24 个离线场景全部通过。
