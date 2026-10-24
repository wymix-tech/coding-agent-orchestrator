# Demo 会话评审衔接与收敛修复

基线：main `6a49e9a669f17f9d125bf08b5346bc1bc96d8f34`。
证据来源：2026-10-23 demo 主会话及 34 个子会话。范围是 CAO/Provider/Host 的过程与产物适配；业务代码审查继续由 SDD Provider 负责。

## 完整方案与实现对应

| 问题 | 解决方式 | 验收 |
| --- | --- | --- |
| 子代理没有 shell，attach 失败 | 主会话 `attach --reviewer-session`，从宿主观察记录读取子会话、父子关系、模型、read 能力，校验独立性和票据创建顺序；子代理不需执行命令 | 只读审查员可绑定；主会话冒充、未知身份、错误模型、跨层复用被拒绝 |
| 原生 Markdown/JSON 需要人工转换 | `review draft --source` 自动填机械元数据和 ID；原始文件不变；只有判断留给协调者 | heading findings 保持整块；无 ID JSON 自动编号；草稿不能直接通过分类 |
| 验收把元数据当需求 | 过滤编号、日期、状态、负责人、标签和 XML 标记；保留业务描述与边界约束 | 元数据不在必须覆盖集合，未知 covers 可见并阻止验收 |
| 4 个发现被拆为 31 项 | 优先按最浅问题标题组织报告；其下列表和代码块属于该问题；无标题时沿用列表解析 | 4 个标题得到 4 个 finding，列表和 fenced 示例不额外计数 |
| 验收映射导致全部评审失效 | 配置时记录原始声明路径；已配置且内容匹配的声明，仅 covers 映射不参与代码审查指纹；其他内容、文件执行权限、executor、标准等仍绑定 | covers 更新保持审查有效；改标准、改执行器、未配置的文件变更会使指纹改变；任务验收仍重新锚定并执行 |
| 审查复现被禁止修改的门禁拦截 | `review check --script` 仅供当前已绑定审查员，或其经过父子关系验证的协调者，运行已配置命令于临时项目副本；生成目录不进入原工作树 | 测试可以失败并作为发现线索；不产生技术/任务验收门禁通过；不扩大直接产品写权限 |
| target 导致验证自变化 | 根范围已确认 Maven 时（包括 pom.xml 尚未生成的空项目），在首个实际验证基线配置中登记 target/；现有基线和显式范围配置不自动修改 | 构建输出被排除，Git 跟踪文件仍优先纳入快照 |
| 诊断笼统导致修改报告试错 | finding 映射返回缺失/额外 ID，处理规则返回具体数组位置、原始 ID、verdict/disposition；身份错误返回期望的实际身份 | 修复指定字段，不需猜测整个报告；不放松原生分类规则 |
| 改报告导致重试计数重置 | 故障按 Work、ticket、输入、动作、layer、诊断字段聚合；报告全文只用于识别完全相同的请求 | cosmetic 改动不重置次数；重复失败后相同请求直接返回缓存诊断；修正前提后允许重新验证 |
| 代理名、二进制、模型参数不兼容 | preflight 接受具备 read 的真实定义；检查顶层/runner.command 的可执行文件；external-cli 必须显式采用独立 session 适配且不默认覆盖模型；宿主提交实际 active tools 与当前模型 | 不因没有 bash 排除只读 reviewer；缺失 backend 在票据前报告；不默认注入模型覆盖参数 |
| worker 被套用实现验收合同 | 预检拒绝 acceptanceRole: writer；提供 templates/pi-reviewer.md 专用定义；每份派发包明确 reviewer 合同 | 使用 worker 的工具能力时，也必须按 reviewer 合同启动 |
| 输出截断吞错误、缺少统一查询 | `review status` 汇总票据/身份/验收覆盖；全局 `--compact-json` 输出精简 JSON（保留原 checkpoint/handoff 的 --summary 文本参数）；`classify-batch` 原子分类所有层，避免 shell 循环误拦截 | 不依赖 grep/head 推断状态，不通过重新执行命令获得摘要 |

## 执行顺序

1. 在实现前按已确认技术栈建立验证入口和生成目录范围。
2. 在启动审查前配置任务验收声明。`review prepare` 对项目绑定的 Work 检查来源覆盖，失败时不创建任何票据。
3. 检查实际 agent/backend 工具、模型参数能力和扩展；优先一次 `prepare` 建立所有层。
4. 每层新建独立审查会话。使用 reviewer 任务合同，不能套用 writer 的实现/新增测试要求。可将 templates/pi-reviewer.md 安装为 .pi/agents/cao-native-reviewer.md，再指定该定义。
5. 有 shell 的子会话自行 attach；只读子会话先通知主会话，主会话使用宿主观察到的 child ID 执行 delegated attach，成功后通知子会话继续。
6. 保存未修改的原生 Markdown/JSON，使用 draft 生成分类草稿。主代理核查真实问题、填写语义判断，全部层分类后再修改产品代码。
7. 完成补丁和测试，再 resolve 所有层；复用原生评审作为 CAO 符合性证据。
8. 正式技术与任务验收分别执行。仅补 covers 时重做任务验收，改变代码/标准时按真实变化重新评估；不通过修改 CAO 管理状态绕过门禁。

## 边界与兼容

- `begin`、子会话自行 `attach`、现有完整 JSON 报告均保留；已显式提交的错误身份或票据值不会自动覆盖。
- `prepare` 的验收覆盖预检针对具有 project_binding 的 Work，未绑定的历史 Work 不强制迁移；手动 begin 保留为低层接口，推荐使用 prepare。
- 临时副本是工作目录隔离，并非操作系统沙箱。只运行已配置的可信验证命令；绝对路径、外部服务、用户缓存仍受进程原有权限约束。副本沿用快照边界排除缓存/已声明生成输出，并保留 Git 跟踪输入；依赖不足应报告阻塞，不能宣称测试通过。
- 外部 agent backend 的可执行文件和参数并不都由 CAO 管理。CAO 可检查声明的 command，宿主必须在真正 launch 前验证具体 backend、有效工具、模型覆盖支持；CAO 不伪造这些检查通过。
- reviewer 合同由专用 agent 定义、派发包和引用协议提供；预检拒绝显式 writer 合同。第三方 runner 仍须识别 reviewer 角色，本仓库不修改第三方 runner 本身。
- 语义是否合理仍由主代理/Provider 判断；CAO 的结构校验不宣称能够自动证明 covers 的语义正确或 finding 属实。
- 更正 mapping/parser 会改变旧编号。旧 triage 不能按新编号静默接受，需用原始报告重新生成和核查分类；不需为格式问题重跑代码审查。

## 新命令示例

```bash
# 查看票据和验收预检；不改变状态
cao sdd review status --compact-json

# 主会话替已观察到的只读子会话绑定（其他参数取自 prepare 返回的票据）
cao sdd review attach --layer edge-case-hunter --run <run-id> \
  --input-sha256 <sha256> --model <model> --reviewer-session <child-native-id>

# 保留原始报告，生成待人工/主代理判断的结构化草稿
cao sdd review draft --layer edge-case-hunter \
  --source _bmad-output/implementation-artifacts/edge-case-hunter-findings.md

# manifest 是 {"blind-hunter":"_bmad-output/.../report.json", ...}，必须列全活动层
cao sdd review classify-batch --report _bmad-output/review-manifest.json --compact-json

# 主会话替只读审查员执行已配置测试；结果只是审查实验，不满足正式验收
cao sdd review check --layer edge-case-hunter --script test-1 \
  --reviewer-session <child-native-id>
```

## 验证记录

- `python -m unittest discover -s tests -v`：930 项通过。
- 新增 `tests/test_review_convergence.py`：19 项会话回归，覆盖原生报告分组、无 ID JSON、只读会话绑定、验收映射保留已通过评审、原子批量分类、重试恢复、临时副本测试及锁释放、空项目 Maven 输出范围。
- `npm run test:pi`：37 项通过，包含实际启用工具/模型的宿主观察。
- `npm run check:pi`、`make check`、`git diff --check`：通过。
- 测试使用自动化单元/集成环境与真实 Python CLI，不包含在用户 Mac 上调用真实模型重放整个会话。安装更新后需重启 Pi，加载新的模型/工具观察逻辑；使用专用 reviewer 定义而非 writer。
