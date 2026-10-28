# 初始化上下文细化与恢复验证

基于 main `91d3da89e0836197aee01c1b3097e5d828e008b6`。

## 复现与修复

原会话先保存 `Java + SpringBoot + JUnit`，之后在身份访谈中确认 JUnit 5，
但 `junit_version=5` 没有同步 `test_framework=JUnit`。骨架检查通过后，
脚本生成识别 POM 为 JUnit 5，报上下文冲突；修正要求迁移 Work，创建 Work
又要求项目背景和验证基线先就绪，形成初始化死锁。

现在身份问答中的 JUnit 代际会依据原始用户证据细化通用 JUnit 背景。已有答案
在恢复时复用；框架版本使用已确认的版本及来源，较新的确认不会被旧访谈答案覆盖。
Jupiter 别名和 JUnit 5 统一比较，但 JUnit 4 与 JUnit 5 继续视为不同代际。

空项目或 CAO 已建立骨架、且尚无相关 Work 绑定的初始化范围，允许使用真实用户
证据直接修正背景；已有初始化提案也可以不带 `--work` 应用，带修订检查与幂等保护。
已有产品和存在当前/历史 Work 绑定的范围继续使用迁移协议。修正背景不会把旧检查
标成有效，仍需重新规划/检查相关初始化输入和项目验证基线。

Spring Boot 3 的 Maven/Gradle 适配器收集确切 `framework_version`，生成应用入口、
测试框架上下文加载测试和对应命令，不再让模型把普通 Java 库草稿手工改为 Spring Boot。
`junit_version=5` 表示由该框架 BOM 管理 JUnit 5；没有 BOM 管理时要求完整依赖版本，
不生成 Maven 依赖 `<version>5</version>`。其他框架组合及 Spring Boot 其他主要版本
保留显式计划入口，不能擅自更换用户选择以套用模板。

生成的 POM 父版本/Gradle 插件版本必须与确认值一致；框架加载能力不得从计划移除。
`draft --refresh` 仅显式重新生成准备输入，保留原有产品文件覆盖保护。

inspect/show/diff、验证脚本生成及启动失败返回一致的冲突状态、字段和恢复归属。
错误恢复提示指向公开命令、准备输入和阶段指南/schema；明确禁止通过翻阅安装包
实现脚本、测试或开发文档寻找旁路，并要求相同输入的相同错误停止重试。

## 回归覆盖

最终验证结果：Python 95 个模块共 1,152 项测试，1,148 项通过、4 项按环境条件
跳过，0 失败；Pi 适配器 39 项通过，TypeScript 类型检查通过；Python compileall、
git diff --check 通过。最终恢复指引变更后又独立重跑了 12 项复现和 19 项路径边界测试。

新增 `tests/test_bootstrap_context_refinement.py`，覆盖：

1. 真实 CLI/host-event 收集连续用户答案，从通用 JUnit 细化到 JUnit 5。
2. 确认框架版本后生成框架草稿，完成独立项目基线，再创建首个业务 Work。
3. 旧初始化状态可直接修正，不依赖临时 Work。
4. 初始化提案不带 Work 应用、重复应用幂等及旧修订拒绝。
5. 已有产品/已绑定 Work 保持真实迁移门禁。
6. JUnit 代际不作为具体依赖版本；Maven/Gradle 草稿均检查。
7. 未确认或不匹配的框架版本不能应用，匹配注释不能伪装实际 POM 父版本。
8. 已确认框架版本复用、Jupiter 别名一致、新确认优先于旧访谈。
9. 恢复旧代际答案不重复询问、不重新开始 intake。
10. 缺少框架加载能力、无效准备输入返回具体可恢复诊断。

这些测试中的隔离 Maven fixture 执行实际结构断言的 unittest，验证 CAO 命令、
脚本基线、权限和状态衔接；它不执行 Java 编译。实际 Maven/JDK/Gradle 的可选矩阵
仍由 `tests/test_setup_toolchains.py` 提供，缺少工具明确跳过。本次没有把该夹具
宣称为真实 Spring Boot 构建或交互式 Pi/BMAD 端到端结果。
