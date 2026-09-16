# Changelog

## Phase A follow-up — Shared Receipt Checks and Authenticated Sources

- Receipt paths and evidence IDs now share target, claim type, work item, requirement revision, and before/after code checks. Refreshing claim dependencies cannot refresh an old execution or reuse a unit run for another gate, review, or final verification.
- Approval imports verify externally issued Ed25519 events against project-configured public keys and exact scope/value. The runtime holds no signing key; unsigned local channel files no longer establish authority. Added the `cryptography` dependency.
- Semantic observations require a policy-selected command with bound program digests and inputs, including the active requirement source. Arbitrary constant-output commands cannot establish facts by supplying `--fact-path`.
- Progress counts come from captured execution output. Editing wrapper fields cannot change the observed count. Authentic failures remain verified failures; an agent claim cannot gain trust by attaching a failed report. A signed general approval cannot authenticate a separately attached stale observation.
- Evidence execution resolves the current requirement before running, and preserves command-internal `--` arguments. Source-backed reset confirmations retain the source reference and provider when replayed.
- Code receipts exclude host integration files and the selected native runtime state source; native observations retain their separate validation and governance checks.
- Added real consumer/CLI counterexamples and positive signed-approval, observer, progress, and reset-replay tests. See [evidence source setup and upgrade steps](references/evidence-sources.md).
- Validation: **470 tests / OK** (16 new boundary regressions); `context_footprint_check` **PASS** (103 lines / 7400 bytes / 918 words). Local verification; live host approval issuer integrations remain project-specific.

## Phase A follow-up — Evidence Is Bound to Real Objects, and Nothing Is Pre-Verified

- **依赖指向真实存在的对象**：`evidence_provenance` 的每条强制依赖（`path_revision` / `tree_revision` / `object_revision` / 报告 / 审批 / 原生状态）都在校验时读回仓库里的真实对象并比对摘要，调用方只能追加依赖、不能替换或留空；记录与校验结果分离存放（`checks/` 下的最新校验可重放），`resolve_dependency` 对无法定位的对象返回结构化诊断而不是"跳过即通过"。
- **测试先造真材料再走真入口**：新增 `tests/evidence_factory.py`。readiness / gate / verification / 原生绑定一律通过真实入口建立（真实存在的报告、真实写入的审批文件、真实 digest 的源码文件、真实执行的 `set_progress`），不再把已 `verified` 的对象直接交给 `sm.set_readiness`；此前绕过入口的测试改为先建材料后断言。
- **readiness 使用时复核**：`action_guard.unsupported_readiness()` 报告"为真但此刻没有已核验证据支撑"的 key（`READINESS_WITHOUT_EVIDENCE`，或 per-key 来源规则不允许该证据时的具体错误码），`evaluate()` 在受保护操作上以 `READINESS_UNSUPPORTED` 拒绝；未核验不等于无效——未核验记录进入 `evidence_unverified`，由消费方判断该条判断是否必需。
- **身份作用域缺失即失败**：复核时身份只从 `work_item` 读取；缺少 `work_item.id` / `requirement_revision` 不再被当作"未绑定即跳过"。
- **证据 CLI**：新增 `coding-orchestrator evidence show|verify|index`（查看单条记录、按当前仓库重新校验、从存储记录重建索引），补齐此前只有库内 API 的可操作重建路径。
- **报告必须是跑出来的**：新增 `coding-orchestrator evidence run --target … --work-item … -- <command>` 与 `ep.run_execution()`。它真跑命令并把 argv、退出码、target、工作项、需求版本与前后代码版本写进按内容寻址的执行收据（`executions/`）；`result_document_binding()` 只认这份收据：手写/改字段的"形似报告"、指不到存储的收据、target 或工作项不符、未记录需求或代码版本、代码已漂移，分别以 `EVIDENCE_EXECUTION_NOT_RECORDED` / `EVIDENCE_EXECUTION_IDENTITY_MISMATCH` / `EVIDENCE_RESULT_TARGET_MISMATCH` / `EVIDENCE_RESULT_SCOPE_MISMATCH` / `EVIDENCE_EXECUTION_REVISION_MISSING` / `EVIDENCE_EXECUTION_CODE_MISMATCH` 拒绝。
- **审批必须走受保护入口**：新增 `coding-orchestrator evidence approve --channel … --channel-ref …` 与 `ep.record_approval()`。审批写进项目声明的唯一存储（哈希链）并引用宿主侧通道产物，复核时重读该产物：复制到别处的副本、断链的哈希、缺失的通道产物分别为 `EVIDENCE_APPROVAL_SOURCE_UNTRUSTED` / `EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED`。
- **代码快照是推导出的依赖**：`required_dependencies()` 新增 `code_snapshot`（对象 `.`，由目录树摘要得出，`.git` / `.orchestrator` / `node_modules` / `__pycache__` 与文档后缀不算代码），编辑需求文档不再把代码结果判为漂移；漂移不再只报第一个对象，`drifted_dependencies` 记录全部。
- **语义 false 由观测承载**：`fact_resolver` 不再接受"某次失败"或"搜索没找到"当作语义结论，只接受为该谓词跑过并报告该值的观察者或针对它的审批；`decision_engine.validate_provenance()` 判据同步为"可信来源观测到该谓词为 false"，失败的运行不再被当作方向性证明。
- **原生 id 绑定**：新增 `coding-orchestrator native bind --native-id [--work-item]` 与 `sm.bind_native_work_item()`：原生侧用自己的 id 寻址时，只把原生键记到当前工作项上，治理身份与审计轨迹不变，无需编辑 `execution-state.yaml` 或重跑 intake。
- **混合内容边界按标题树判定**：`requirement_identity` 把 Markdown 解析为带层级与父链的标题树，标题按全名精确匹配（"Status"不再吞掉"Status API"），小节继承父级归属（`## Tasks` 下的 `### Implementation` 勾选不会算作需求变更），首个标题前的正文视为需求内容，未知标题一律保留为需求内容而不静默删除。
- **破坏性修订确认绑定完整集合**：`--confirm-reset` 现需 `--confirm-revision` / `--confirm-incoming-revision` / `--confirm-state-revision`（含观察到的执行状态版本），确认提示由 `requirement_identity.confirmation_command()` 渲染出可直接执行的完整命令。
- **需求内容版本独立可取**：`context_plane.requirement_content_revision()` 对单文件或目录返回"仅需求内容"的版本，供上下文/证据绑定复用同一口径。
- **治理边界独立成测**：新增 `tests/test_governed_transition_boundaries.py`——原生推进到 review 但 `implementation_tasks_complete` 未成立时不推进治理（记 `native_divergence` 且保留原生事实），原生 done 但终验未通过时不生成 `completion_record`，verification 的 `finish_role` 仍要求已记录结论、release 仍需新鲜通过的终验（既有边界不是新的白名单）。
- 回归：`discover` 实跑 **454 tests / OK**，`context_footprint_check` **PASS**（SKILL.md 103 行 / 7400 bytes / 918 words）。

## Phase A — Evidence Is Verifiable, Native Projection Is Real, Identity Migrates

- **T1 可核验证据**：新增 `scripts/evidence_provenance.py`。`kind` × `validation_status` × `outcome` 三维分离，可信来源的真实失败报告记为 `verified + failed`，仅来源/格式/工作项/绑定不符才为 `invalid`；强制依赖由验证器按结论类型 + Policy 推导（`required_dependencies()`），调用方只能追加且每项绑定对象与版本；`collect_verification_inputs`（IO）/`verify`（纯函数，时间显式传入）/`persist_verification` 三层分离；证据记录不可变、按内容寻址，索引可重建，孤立证据允许存在。`set_readiness` 按 key 独立来源规则，`record_gate`/`record_verification` 绑定命令、退出码、报告与快照；`action_guard.collect_evidence` 增加使用时复核并映射到实际依赖它的那条判断。
- **T2 原生状态真实性**：新增 `scripts/native_state_parser.py`。单次读取 → 同字节摘要与解析 → `NativeProjection` 或结构化诊断（`NATIVE_SOURCE_UNCONFIGURED` / `NATIVE_TASK_MISSING` / `NATIVE_WORK_ITEM_AMBIGUOUS` / `NATIVE_FORMAT_UNKNOWN` / `NATIVE_STATUS_UNSUPPORTED` / `NATIVE_SOURCE_CHANGED`）。前端 `native-sync`、底层 `sync-native`、`transition`、`progress` 共用同一校验；`--native-confirmed` 退化为兼容开关，不再是信任来源。`sync_native` 先把原生事实写入 `authority.native_observation`，治理 `phase/status/completion_record` 必须经 `action_guard.authorize(native_projection=...)` 才推进；不一致时输出 `native_divergence` 诊断，不丢弃原生事实。该一致性为 best-effort，不声明为安全边界。
- **T3 需求版本与迁移**：`requirement_identity` 升级 `identity_version=2` 四元组（`requirement_id` / `source_revision` / `request_revision` / `native_state_revision`）。`candidate_identity()` 不再把请求措辞摘要当作来源 revision；目录/多文件按成员清单、排序、路径规范化的确定性摘要；同文件混合内容按结构化边界拆分（任务勾选、开发记录、变更日志不属于需求内容），未知格式返回 `MIXED_CONTENT_*` 诊断而不静默删除内容，完整原文摘要与需求内容版本分开保留。`intake` 的新 resolutions / base-ref / 证据引用 / `--reanalyze` 不再被"源未变"快捷恢复吞掉；破坏性修订确认必须绑定 `--confirm-revision` / `--confirm-phase` / `--confirm-status`，状态或版本移动后重新确认。迁移支持 preview → backup → 原子替换 → 幂等重入 → 中断恢复，缺 `source_revision` 的旧记录标记 `legacy` 并给出迁移命令。
- 测试聚焦新增：`tests/test_evidence_provenance.py`、`tests/test_native_state_parser.py`、`tests/test_native_entrypoint_equivalence.py`、`tests/test_requirement_identity_v2.py`、`tests/test_requirement_revision_confirm.py`、`tests/test_migration_transactions.py`、`tests/test_explicit_input_not_swallowed.py`；`discover` 实跑 **382 tests / OK**，`context_footprint_check` **PASS**。

## 6.5 follow-up — Resume Recovery Paths Are Freshness-Aware

- A rephrased intake now bypasses semantic analysis only when repository, authority, comparison, context, semantic, policy, and enforcement inputs are current. If any input is stale, it refreshes analysis under the same requirement revision and preserves in-flight phase and progress instead of falsely reporting that evidence was reused; a new binding correctly makes prior verification stale.
- Source-content comparison is independent of request wording. A source change now reaches the revision-confirmation path before any registry baseline is recorded; an unhashable legacy source requires an explicit baseline rather than being assumed unchanged.
- Recovery actions remain pure in `action_guard.evaluate()`, then `authorize()` renders actual platform-aware front-controller commands with an absolute repository path. This prevents a returned command from being reclassified as production mutation because the launcher is missing from PATH or the host uses another working directory.
- Native-authority transition denials now return `NATIVE_AUTHORITY_REQUIRED` with recovery. Added `native-sync`, which verifies an in-repository native state reference and its supplied SHA-256 digest before recording the native lifecycle projection; it has no `--native-confirmed` bypass.
- Updated the runtime guidance, session contract, authorization reference, manifest, and resume regression coverage. The focused suite now covers real classified intake/resume evidence reuse, stale same-revision refresh, actual rendered recovery classification, source-change confirmation, and native sync.

## 6.5 revision — Resume Must Not Revise, and Denials Must Be Executable

- Fixed a session-ending deadlock found in a real run. An agent resumed an implementation-stage work item with "继续", re-ran `intake`, was told `Requirement content changed`, used `--revise-current`, and silently lost the implementation phase, readiness flags, quality gates, and task progress. The resulting `advance_native_sdd_to_ready` then had no legal CLI path, so the agent could only hand five manual commands back to the operator.
- `requirement_identity.source_revision_id()` hashes the requirement source document, and `record()`/`last_source_revision()` keep that content revision per requirement. A source-stable rephrase keeps the existing requirement revision; later follow-up ensures evidence is reused only when every analyzed input remains current.
- `intake --revise-current` now refuses to discard in-flight work without `--confirm-reset` (`REVISION_RESET_REQUIRES_CONFIRMATION`), and the plain `REQUIREMENT_REVISION_CHANGED` message names what the reset would discard plus `coding-orchestrator start` as the resume alternative. `_resets_in_flight_work()` treats implementation or later, or any completed task, as in-flight.
- Added the missing recovery surface to the front controller: `coding-orchestrator readiness`, `coding-orchestrator progress`, and `coding-orchestrator transition`. All three are classified as `prepare`, so they are the supported way to change execution state; direct `execution_state_manager.py` edits stay denied. A denied `transition` now reports `TRANSITION_DENIED` with the authorization and recovery commands instead of raising.
- `action_guard.RECOVERY_COMMANDS` maps `advance_native_sdd_to_ready`, `define_acceptance_criteria`, `transition_to_implementation`, and `reconfigure_governance_explicitly` to runnable commands. `evaluate()` returns them as `recovery`, `check` prints them, and host `pre_tool` denials append them to the reason so a blocked agent can act immediately.
- `SKILL.md` and the recovery references document the resume and CLI recovery rules; `MANIFEST.json` lists the new subcommands.
- Added `tests/test_resume_guard.py` (**6 tests**) covering a rephrased resume request, source-change confirmation, the CLI readiness/progress/transition path, `prepare` classification of recovery commands, and recovery output on `SDD_NOT_READY`.

## 6.5 revision — Content Snapshots Independent of Git Commits

- Remove HEAD identity from material fingerprints and keep deleted paths absent before and after their deletion is committed.
- Retain Git HEAD as trace metadata in Work Facts/CBM output; bind analysis IDs to material content and explicit diff bases.
- Check base and merge-base trees separately, rejecting changed or missing comparison inputs with `COMPARISON_BASE_CHANGED`.
- Preserve current gate/review/verification evidence after no-op code-mutation events, including staging, commits, and message-only amendments; actual hook edits and stale authority inputs still invalidate it.
- Add 21 real Git regression tests and a GitHub Actions workflow for focused/full tests and context footprint validation.
- Existing active work needs one refresh when migrating from the previous HEAD-bound fingerprint.

## 6.5 revision — Runtime/Governance Review Corrections

- Normalize literal governance targets across direct tools and shell commands, including relative aliases, native separators, symlinks, tool working directories, and parent-directory removal/moves.
- Protect configured and currently analyzed policy manifests/packs outside the default policy directory while retaining ordinary SDD preparation.
- Share packaged front-controller identity with tool classification, keeping the Windows `.cmd` recovery entry available before intake creates state.
- Reconcile stale host adapters by replacing managed hooks, preserving unrelated user hooks/settings, and avoiding duplicate registration on reinstall.
- Serialize host arguments separately from JSON/TS strings; preserve special characters in project/runtime paths and honor an explicit kernel `--repo` inside a parent Git tree.
- Create evidence scaffolds exclusively at new paths, preserving previously filled files on repeated fixed-directory intake and through file aliases.
- Isolate retry-detection tests with a CBM fixture and check the pipeline status before assertions; add review regression coverage for the six reported defects.

## 6.5 revision — Deployment-Independent Runtime Location

- Fixed agents repeatedly failing to locate the Skill's scripts. The install directory name is a deployment choice, but documentation and fallback paths hardcoded `.agents/skills/coding-agent-orchestrator/`, so any other name forced guessing and retries.
- Added `scripts/skill_runtime.py` as the single source of truth for this Skill's own location, resolved from `__file__` instead of an assumed directory name.
- Added `coding-orchestrator where`, which prints the resolved skill root, the front controller path, and ready-to-run commands; `init` now reports the runtime too.
- `project_activation.py` no longer assumes a directory name. When this package is not inside the repository, it scans `.agents/skills/*/` for a directory that actually looks like this Skill.
- `repository_snapshot.py` derives its own ignore prefix from the install location, so a Skill installed under any name stays out of material repository fingerprints.
- Handled user-level installs. `where` reports absolute paths and states that the Skill is outside the repository; printed commands are shell-quoted so paths containing spaces (`C:\Users\Jane Doe\...`) run when pasted; Windows prefers `coding-orchestrator.cmd` over the POSIX launcher.
- User-level installs no longer write a machine-specific absolute path into `AGENTS.md`. The activation block names the Skill instead, because that file is committed and shared.
- An incomplete install that ships without `hosts/` now fails with an actionable message naming the missing asset instead of a bare `FileNotFoundError`.
- Generated host adapters now carry the project path. `init --repo <project> --host X` bakes `--repo <project>` into `.claude/settings.json`, `.codex/hooks.json`, and the Pi extension, so the enforcement kernel resolves the project from the explicit argument instead of inferring it from the caller's cwd with `git rev-parse`. A hook triggered outside the repository, or in a tree that is not a Git repository, now targets the right project.
- Added `tests/test_skill_runtime.py` (**18 tests**) covering project-level and user-level installs under several directory names, paths with spaces, the no-absolute-path rule, and the `--repo` propagation into Claude Code and Pi adapters. The suite now discovers **249 tests**.

## 6.5 revision — Governance Artifacts Are Not Agent-Writable

- Added the `mutate_governance` action class, **denied by default**. Authority config (`.orchestrator/config.yaml`), enforcement config, execution state, policy sources, and requirement identity are what *grant* authority, so an agent must not be able to widen its own authority by editing them.
- `tool_actions.py` classifies file writes/edits and patch targets through `governance_class()`, and shell commands through `GOVERNANCE_TOKENS`; matched paths are reported as `governance_paths` on the described action.
- Legitimate changes go through the orchestrator CLI (classified as `prepare`) or an explicit operator action with `--allow-governance-mutation`.
- `references/action-authorization.md` documents the class and the SDD artifact widening of `prepare`; `tests/test_action_guard.py` covers it.

## 6.5 revision — Intake Evidence Scaffold and Retry Loop Detection

- Fixed intake reporting dozens of unresolved Work Facts without saying how to answer them. `build_resolution_template()` emits a fillable scaffold (`--resolutions-template`, also reported as `resolutions_template`) with one entry per queued fact; `value_type()` pre-types integer facts as `non_negative_integer` and the rest as `boolean`, and heuristic hints are attached as inspection guidance that still cannot finalize a fact.
- Fixed silent retry loops. Collectors are deterministic, so identical inputs reproduce the same draft. `intake_fingerprint()` hashes the request, base ref, and resolutions file, and `record_intake()` keeps it per requirement revision in `.orchestrator/runtime/intake-history.json`. A repeated fingerprint reproducing the same non-classified status now warns with `IDENTICAL_INPUT_NO_NEW_EVIDENCE`, `repeat_count`, and a `next_action` of `supply_fact_resolutions`, or `strengthen_resolution_evidence` when a resolutions file was already supplied.
- Intake history is diagnostic only; a write failure never fails intake.
- `references/fact-extractor.md` documents both fixes; `tests/test_evidence_scaffold.py` covers them.

## 6.5 revision — Real CBM CLI Contract and Empty Greenfield Bootstrap

- Verified the CLI/tool schemas against upstream CBM source commit `339b3f4097aa6ede22fc382ab7fd320d93c498b8` (2026-09-13).
- Corrected `detect_changes`: CBM accepts `scope=files|impact`; Orchestrator scopes are no longer passed through.
- Corrected `check_index_coverage`: every call now includes the concrete evidence `paths` required by CBM.
- Corrected the index smoke command: current `index_repository` does not declare `--format`; JSON formatting is used on tools that advertise it.
- Added empty-greenfield handling. BMAD/Orchestrator/Agent installation metadata is excluded from product-code detection, so an empty project does not invoke CBM, require a Git `HEAD`, or open a false provider incident.
- For product files before the first commit, indexing still runs and all product files are conservatively treated as new until CBM `detect_changes` has a valid Git baseline.
- Added regressions for real scope mapping, exact-path coverage, and a BMAD-only empty project.

## 6.5 revision — CBM Provider Resilience and Retry Circuit

- Modern CBM schema CLI builds now use explicit `--format json`, preventing compact/tree stdout from being misclassified as a provider outage.
- CBM binary discovery now honors `ORCHESTRATOR_CBM_BINARY` / `CBM_BINARY` and common installer paths (`~/.local/bin`, Homebrew, `/usr/local/bin`) when an Agent process inherits a reduced PATH.
- Added durable `.orchestrator/providers/codebase-memory-mcp.json` provider incidents. The first operational failure remains fail-closed; identical automatic retries are circuit-broken and route to `repair_cbm_provider` instead of repeatedly requesting unrelated Work Fact evidence.
- Added `coding-orchestrator provider status/reset codebase-memory-mcp`; a changed CBM binary/version automatically receives one fresh attempt.
- Provider incident runtime files are excluded from material repository fingerprints, so recording an outage cannot make semantic evidence stale by itself.
- Added provider-incident schema and five resilience regression tests. Affected CBM/Start/CLI/Action/Session/Enforcement regression groups: **116 tests PASS**. The suite now discovers **216 tests**.
- The current repair container did not complete a new one-process standard discover reliably: repeated attempts stalled and one diagnostic run terminated in the local Python/PyYAML stack. The previous persisted baseline remains 211/211 full-suite PASS; this revision records the current targeted validation explicitly rather than claiming 216/216.

## 6.5 revision — Stability and Lifecycle Repair (T1–T8)

- T1: made Execution State + append-only history commits cross-process coordinated and recoverable with a transaction journal; stale revisions conflict explicitly, interrupted commits recover idempotently, stale dead-owner locks are reclaimed, and live-lock timeout is deterministic.
- T2: separated stable requirement/work-item identity from requirement revision; isolated semantic intake into immutable per-work/per-revision run directories; stale concurrent analysis cannot overwrite newer canonical state, and explicit requirement revision invalidates derived acceptance/task/review/verification evidence.
- T3: changed Policy snapshot identity from mostly rule IDs/paths to effective semantic rule content plus referenced policy source hashes, including packs outside the default directory; external guidance remains non-authoritative unless explicitly promoted.
- T4: made explicit `init --sdd ...` resolution persist by field-level merge while preserving unrelated project configuration; active native work ownership conflicts remain explicit instead of silently rewriting authority.
- T5: added stable requirement history/registry semantics so completed A/B work does not reopen after restart; content hashes identify revisions, not requirement identity.
- T6: aligned BMAD Requirement Discovery with detected/configured native sprint state and `_bmad-output` artifacts; templates/install resources are excluded, completed items are filtered, and unknown formats or missing story artifacts produce sourced action-required diagnostics.
- T7: separated Verification Obligations from execution results; required obligations cannot disappear through plan shrinkage or `required=false`, and `superseded` / `not_applicable` / `waived` require auditable reason, authority, and evidence rather than masquerading as passed tests.
- T8: added cross-module authorization, real CLI rejection-path, CBM timeout/JSON/runtime contract, lifecycle, concurrency, and end-to-end close/next-requirement regression coverage. Historical governed completion is now distinct from current close readiness; native SDD closed status alone is not Governance Done.
- Standard regression command `python3 -m unittest discover -s tests -v`: **211 tests pass**. Context Footprint: **100 lines / 7490 bytes / 942 words, PASS**.
- Live CBM/Pi/Codex/Claude Code integration was not executed in the repair environment because those binaries were unavailable; offline provider/host contract fixtures remain covered and `REPAIR_REPORT.md` records reproduction commands and limitations.

## 6.5 revision — Current-Agent Host Auto Selection

- Changed Safe Auto host selection from repository-marker inference to **current Agent runtime detection**.
- Added `scripts/host_runtime.py`: explicit `--host` wins; otherwise nearest recognized process/runtime evidence is used, with `CODEX_THREAD_ID` and `PI_CODING_AGENT` as positive runtime signals.
- Added deterministic Claude Code fallback when the current Agent cannot be identified reliably.
- Added lightweight Host Reconcile for already-bootstrapped projects: switching to Pi/Codex/Claude installs only the missing current-host adapter without rerunning project bootstrap or recreating the active work item.
- Kept `.claude/.codex/.pi`, packaged Skill files, and Orchestrator-managed activation blocks out of material code fingerprints so host integration changes do not invalidate semantic analysis; user-owned AGENTS/CLAUDE content still participates.
- Added current-host/adapter diagnostics to `discover` and `doctor`.
- Added runtime-host selection and host-switch regression coverage.
- Full regression suite: 185 tests pass.

## 6.5 revision — CBM CLI Compatibility

- Hardened the CBM adapter across current/intermediate/legacy CLI generations.
- Prefer `codebase-memory-mcp cli <tool>` with JSON on stdin; fall back only on proven invocation-shape incompatibilities.
- Probe `--raw` support before using it, so real indexing failures are never overwritten by a secondary `unknown tool: --raw` error.
- Added CBM CLI protocol information to provider health and a non-mutating `list_projects` smoke probe to `coding-orchestrator doctor`.
- Added compatibility regression coverage for modern stdin JSON, legacy inline JSON, conditional raw fallback, and preservation of real runtime failures.
- Full merged regression suite: 177 tests pass.

## 6.5 revision — Shared Action Authorization

- Centralized execution, phase advance, release, and close eligibility in `action_guard.py`; state/CLI/host/start adapters share stable reasons and recovery actions.
- Added read-only `check --action` and live material/authority input checks independent of host hook delivery.
- Kept requirement preparation and tracked implementation edit windows usable while denying stale/unclassified advancement.
- Bound gate/review/final-verification evidence to execution and content snapshots; synchronized required impact-plan gates and prohibited result-based gate downgrades.
- Resolved blockers no longer stop resume; optional review findings remain blocking; exhausted Stop retries remain denied.
- Normalized raw patches, moves, shell payloads, and `cmd`/`command`; separated role outcome reporting from global completion.
- Added migration guidance and removed obsolete host permission override settings from starters.
- Added 32 regression tests; 172 tests pass. Earlier release counts below are historical.

## 6.5 — Project Bootstrap & Unified CLI

- Refactored `SKILL.md` to be version-neutral: capability names now replace historical V3/V4/V5/V6.x labels; release history remains in this changelog and `MANIFEST.json`.
- Added `coding-orchestrator` / `coding-orchestrator.cmd` and `scripts/coding_orchestrator.py` as the stable human/agent front controller.
- Added `init`, `discover`, `doctor`, `status`, `intake`, `resume`, `verify`, and `host install` commands.
- Added conservative repository discovery for technology, SDD/state authority, host markers, CBM availability, and architecture evidence.
- Added safe, idempotent project bootstrap that creates governance infrastructure without inventing an active work item.
- Added first-intake creation of the canonical work item; the Decision Engine remains responsible for the real Flow classification.
- Added fail-safe handling for OpenSpec/BMAD authority ambiguity and CI non-interactive failure semantics.
- Added evidence-gated Spring layered-policy auto-enablement; Spring Boot identity alone no longer implies `web -> service -> dao`.
- Added Safe Auto host installation; later V6.5 revisions refined selection to current-Agent runtime detection with Claude Code fallback.
- Added a project Activation Layer: idempotent managed `AGENTS.md` routing, optional `CLAUDE.md` routing, and Pi session-start activation so vague prompts such as `开始`, `继续`, `start`, `continue`, and `resume` can reliably enter the Orchestrator workflow.
- Added a self-bootstrap `Bootstrap Guard`: once the Skill or an installed host lifecycle adapter is activated, missing `.orchestrator/config.yaml` triggers Safe Auto `init` exactly once, then the same user turn continues without requiring a restart.
- Added deterministic `start` intent routing: `开始` / `继续` / `start` / `continue` / `resume` now resolve project state first, resume active work, surface blockers, discover requirements, auto-intake exactly one high-confidence candidate, ask when none exists, and stop for selection when several exist.
- Added conservative Requirement Discovery for selected SDD artifacts and generic requirement/spec/PRD sources; Skill/host/runtime directories and ordinary install READMEs are excluded, and dedicated requirement documents outrank README fallback.
- Split first-project lifecycle state into `UNBOOTSTRAPPED -> READY_FOR_INTAKE -> READY_FOR_WORK`; initialization no longer conflates a valid project with a missing active work item.
- Invalid existing config and unresolved SDD authority remain `ACTION_REQUIRED`; self-bootstrap never overwrites or guesses.
- Activation stubs preserve existing project instructions and remain routing-only; `.orchestrator/`, SDD, Policy, State, and Evidence remain authoritative.
- Renamed documentation so `README.md` is the default English README and `README-zh.md` is the Simplified Chinese README.
- Added bootstrap report, project config generation, Doctor health checks, closed-work archival, and unified resume/verification readiness views.
- Added V6.5 bootstrap/CLI tests.
- Refactored `SKILL.md` into a compact runtime router with an explicit Lazy Reference Loading Contract; detailed CLI/examples remain in on-demand references.
- Added `scripts/context_footprint_check.py` and regression guards for Skill size, version-neutrality, and lazy-reference semantics.
- Updated README guidance to avoid sequential eager-loading of the reference corpus.
- Full regression suite is 140 tests.

## 6.4 — Session Bootstrap & Handoff Context

- Added `scripts/session_context.py` with canonical JSON Session Bootstrap and Task Handoff artifacts plus compact Markdown prompt projections.
- Added YAML session-context configuration and explicit format separation: JSON for machine truth/projections, YAML for human config, Markdown for model injection; XML is not used by default.
- Added `fresh_project`, `session_resume`, and `agent_handoff` bootstrap modes.
- Added explicit handoff freshness binding to work item, execution snapshot, analysis snapshot, and Context snapshot.
- Added latest-handoff persistence without treating casual assistant completion claims as authoritative task completion.
- Updated Claude Code/Codex SessionStart integration and Pi pending-bootstrap injection so the full bootstrap is injected once; subsequent prompt events are delta-only.
- Excluded generated `.orchestrator/session/` artifacts from material working-tree fingerprints to prevent self-induced staleness.
- Added Session Bootstrap / Task Handoff JSON Schemas, examples, documentation, tests, and pressure scenarios.
- Added 13 V6.4 session-context/enforcement tests; full regression suite is 100 tests.

## 6.3 — Host Enforcement Adapters

- Added one host-neutral `scripts/enforcement_kernel.py` instead of duplicating governance logic in each coding agent.
- Added Claude Code and Codex hook templates plus a Pi TypeScript extension adapter.
- Added `scripts/install_host_adapter.py` to merge/install repository-local adapters explicitly.
- Added runtime guards for missing/unclassified state, illegal code mutation phase, reviewer/verifier write separation, stale Context Manifest, and completion claims.
- Added dirty-window semantics: material mutation invalidates semantic/context/final-verification freshness immediately but does not force heavyweight re-analysis after every edit; review/verification/close remain blocked until re-analysis.
- Added V5 execution-state enforcement metadata and transition guards for stale semantic/policy evidence.
- Added external working-tree change detection at the next lifecycle event.
- Added host capability matrix documenting hard/soft enforcement differences and Codex/Pi caveats.
- Added 11 runtime enforcement tests; full regression suite is 87 tests.

## 6.2 — Context Manifest / Role-aware Context Packs

- Added V6 Context Plane to unify SDD/request, Work Facts, Decision, Execution State, Semantic Impact, Engineering Policy, Verification, and Evidence references.
- Added `context-manifest.json` with source hashes, authority metadata, snapshot binding, blockers, and freshness warnings.
- Added role/stage Context Packs for planner, implementer, reviewer, verifier, debugger, and resume workflows.
- Added context budgets that defer low-relevance sources without dropping mandatory authority context.
- Added manifest freshness validation and stale-source detection.
- Added context refs to V5 analysis state and integrated pack generation into the semantic intake pipeline.
- Added Context Manifest/Pack JSON Schemas, examples, documentation, tests, and pressure scenarios.

## 6.1 — Engineering Policy Layer + ECC Rule Source

- Added project-owned Engineering Policy as a first-class V6 context and governance layer.
- Added progressive policy loading: manifest -> planning summaries -> exact implementation rules -> review/verification evidence.
- Added `MUST` / `SHOULD` / `PREFER` levels, precedence, and illegal-downgrade conflict detection.
- Added Spring Boot layered starter policy (`web/controller -> service -> dao/repository`) and lightweight Java import enforcement.
- Added Policy Gate projection into Verification Plan and V5 Execution State.
- Added optional ECC `rules/` adapter as non-authoritative external guidance; no ECC rule bodies are redistributed.
- Added policy bootstrap, compact policy context packs, third-party attribution, and policy tests.

## 6.0 — Semantic Impact & CBM Provider

- Added `CodeIntelligenceProvider` SPI with one implementation: `codebase-memory-mcp`.
- Added CBM health/capabilities/index/`detect_changes` collection and stable semantic-impact normalization.
- Added provider-risk quarantine: CBM risk labels are retained but never authoritative for Work Facts or Flow.
- Added semantic blast-radius mapping into scope/complexity/verification Work Facts with auditable supersession.
- Added evidence-linked Verification Planner.
- Added end-to-end `semantic_intake_pipeline.py`.
- Added Execution State `analysis` attachments for semantic-impact, Work Facts, decision, and verification-plan references.
- Added CBM/provider pressure tests and offline fixtures.

## 5.0 — Execution State Manager

- Added Canonical Execution State with `phase × status` and blocker modifiers.
- Added native/hybrid/orchestrator authority modes for BMAD, OpenSpec, and generic SDD.
- Added transition guards, optimistic revisions, append-only history, resume, native sync, gates, review, and snapshot-bound verification.
- Added native-completion vs governance-completion separation.

## 4.0 — Evidence-backed Fact Extractor

- Added conservative mechanical Work Fact collection, semantic resolver, strict evidence lint, snapshots, and conflict protection.

## 3.0 — Deterministic Decision Engine

- Replaced subjective Work Profile scoring with executable facts, scoring, combination rules, and risk/policy overrides.

## 2.0 — Adaptive Flow

- Added six-dimensional Work Profile, TRIVIAL/FAST/STANDARD/DEEP flow profiles, and deterministic reassessment/escalation concepts.

## 1.0

- Initial SDD adapter + Superpowers + quality-gate orchestration model.
