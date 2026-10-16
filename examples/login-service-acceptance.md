# 登录服务实验的验收脚本修复

这是 2026-10-15 用户提供的 Java 登录服务实验专用示例，不是 CAO 对其他项目默认采用的技术栈。

将 `login-service-acceptance.sh` 复制到该服务的 `scripts/verification/acceptance.sh`；
核对示例 declaration 的 SPEC、来源、criteria 和实际测试后，再登记及重锚原生验收。
不要把新的报告冒充原实验历史：保留原始会话，使用当前 Work 的新验收运行。

脚本使用临时配置文件；原 `config/users.yaml` 只用于读取示例哈希，退出时不再回写。
删除前证明改密后的当前密码有效，删除后再用同一个当前密码探测 401。
AC-4/AC-5 还关联构建后的具体 Surefire 会话失效用例；证据缺失/失败/跳过/过期则 blocked。
哈希生成结果需真实登录成功，启动失败需匹配配置缺失原因；日志检查同时包含失败密码和完整哈希。
脱敏运行日志和会话测试结果快照必须预先声明在
`_bmad-output/cao-acceptance/<当前 Work ID>/` 的 outputs 中。
示例 declaration 使用本次实验的 Work ID；换 Work 时先替换该 ID，不能复用其他 Work 的输出。
只保存测试用例身份和成败，移除 XML 系统属性、环境信息及控制台原文。

该例仍依赖项目自己的接口与测试语义，不能替其他项目自动判断所有验收标准。
