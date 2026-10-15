# CAO Host and Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline; use superpowers:subagent-driven-development only after explicit operator authorization. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 R08/R10/R11/R14/R15，落实提醒模式、真实会话生命周期、有限验证执行和发布验证。

**Architecture:** 主机适配器只翻译正式事件，CAO 保留唯一完成判定。执行停止与 Session 退出分离，Dashboard 共享 B 的证据视图；验证进程和快照性能优化不削弱新鲜度。

**Tech Stack:** Python >=3.10 标准库、Pi 1.1.0 API、TypeScript、Node 22.18+ 原生 TS 测试、unittest。

## Global Constraints

- Python >=3.10；Python 核心仅使用标准库，不新增第三方运行时依赖。
- UTF-8 文档；保留项目语言偏好与 Provider 原生文件所有权。
- 新增代码文件不超过 500 行；不为满足行数限制一次性重写现有大型文件。
- 产品代码写入只提醒；内部状态保护与 `cao done` 硬约束；不新增强制模式配置。
- B1—B5 通过后实施 C；工具/类型检查开发依赖不成为 Python 核心运行时依赖。
- 不把模拟事件、单机结果、未执行在线安装声明为真实主机或跨平台验证。

---

## 依赖与文件结构

设计：`docs/superpowers/specs/2026-10-11-cao-integrity-repair-design.md`。A 提供 repo_transaction 与 RepairCase，B 提供 verification_inputs/verification_fresh/gate_state/workflow_ready。新增 `scripts/verification_runner.py`；Pi 仍在 `hosts/pi/coding-orchestrator.ts`，无需独立治理引擎。

Pi 参考：本机安装包 `docs/extensions.md`、`docs/tui.md`、`docs/packages.md`、`dist/core/extensions/types.d.ts` 与 `examples/extensions/notify.ts`，位于 `/Users/wymix/.pi/agent/install/releases/1.1.0/node_modules/@earendil-works/pi-coding-agent/`。实施时读完整相关文档，按正式类型核对 agent_end、agent_before_settle、session_shutdown，不能依靠候选字段猜测。支持声明先固定已测试的 1.1.0；其他宿主版本须提供可验证能力清单。

每任务定向+全量 Python 测试、compileall、git diff --check；C1 起额外运行 Node 契约和 TS 类型检查。`PYTHON=python3.11` 为本机验证命令，CI 使用矩阵解释器。

## C1：Pi API、异步调用与生命周期载荷

**Files:** Modify `hosts/pi/coding-orchestrator.ts:1-102`, `package.json`; Create `tests/test_pi_adapter.mjs`, `tsconfig.pi.json`; Generate `package-lock.json` during implementation。

**Interfaces:** `callCao(event: string,payload: object,cwd: string) -> Promise<CaoReply>`；CaoReply 包含 decision/additional_context/reason/diagnostic。Python C2 消费 session_start/prompt_submit/pre_tool/post_tool/turn_end/before_settle/session_end。

- [ ] **1. 写正式载荷测试。** `tests/test_pi_adapter.mjs` 的完整主测试（POSIX 假可执行文件，Windows 不宣称通过此测试）：

```javascript
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import os from 'node:os'; import path from 'node:path';
import install from '../hosts/pi/coding-orchestrator.ts';
test('Pi native session and assistant payloads', {skip:process.platform==='win32'}, async () => {
  const dir=mkdtempSync(path.join(os.tmpdir(),'cao-contract-'));
  const oldPath=process.env.PATH, oldCapture=process.env.CAO_CAPTURE;
  try {
    process.env.CAO_CAPTURE=path.join(dir,'events.jsonl');
    process.env.PATH=dir+path.delimiter+oldPath;
    writeFileSync(path.join(dir,'fake.py'),
      'import json,os,sys\nr=json.load(sys.stdin)\n'+
      'with open(os.environ["CAO_CAPTURE"],"a") as f: f.write(json.dumps(r)+"\\n")\n'+
      'print(json.dumps({"decision":"allow"}))\n');
    writeFileSync(path.join(dir,'cao'), '#!/bin/sh\nexec "${CAO_TEST_PYTHON:-python3}" '+
      JSON.stringify(path.join(dir,'fake.py'))+'\n',{mode:0o755});
    const handlers=new Map();
    install({on:(key,fn)=>handlers.set(key,fn),sendMessage(){}});
    const context=id=>({cwd:dir,hasUI:false,sessionManager:{getSessionId:()=>id},ui:{notify(){}}});
    await handlers.get('session_start')({type:'session_start',reason:'startup'},context('A'));
    await handlers.get('session_start')({type:'session_start',reason:'new'},context('B'));
    await handlers.get('agent_end')({type:'agent_end',messages:[{role:'assistant',
      content:[{type:'text',text:'尚未完成'}]}]},context('B'));
    assert.ok(handlers.has('session_shutdown'));
    await handlers.get('session_shutdown')({type:'session_shutdown',reason:'quit'},context('B'));
    const rows=readFileSync(process.env.CAO_CAPTURE,'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(rows[0].session_id,'A'); assert.equal(rows[1].session_id,'B');
    assert.equal(rows[2].event,'turn_end'); assert.equal(rows[2].last_assistant_message,'尚未完成');
    assert.equal(rows[3].event,'session_end');
  } finally {
    process.env.PATH=oldPath;
    if(oldCapture===undefined) delete process.env.CAO_CAPTURE; else process.env.CAO_CAPTURE=oldCapture;
    rmSync(dir,{recursive:true,force:true});
  }
});
```

运行 `CAO_TEST_PYTHON="$PYTHON" node --test tests/test_pi_adapter.mjs`。旧适配器缺正确 ID/消息/退出 handler，必须失败。

- [ ] **2. 替换调用桥与 ID/消息提取。** 使用正式导入，删除候选字段猜测和 spawnSync：

```typescript
import type {AgentEndEvent,ExtensionAPI,ExtensionContext} from '@earendil-works/pi-coding-agent';
import {spawn} from 'node:child_process';
import path from 'node:path'; import {fileURLToPath} from 'node:url';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
type CaoReply={decision?:'allow'|'deny';additional_context?:string;reason?:string;diagnostic?:string};
async function invoke(cmd:string,args:string[],input:string,cwd:string) {
  return new Promise<{code:number|null;out:string;error?:string;missing?:boolean}>(resolve=>{
    const child=spawn(cmd,args,{cwd,stdio:['pipe','pipe','pipe']});
    let out='',err='',failure='';
    const timer=setTimeout(()=>{failure='CAO_TIMEOUT';child.kill('SIGKILL');},5000);
    child.stdout.on('data',chunk=>{out+=chunk.toString();
      if(Buffer.byteLength(out)>262144){failure='CAO_OUTPUT_LIMIT';out='';child.kill('SIGKILL');}});
    child.stderr.on('data',chunk=>{err=(err+chunk.toString()).slice(-4000);});
    child.on('error',error=>{clearTimeout(timer);resolve({code:null,out:'',error:error.message,
      missing:(error as NodeJS.ErrnoException).code==='ENOENT'});});
    child.on('close',code=>{clearTimeout(timer);resolve({code,out,error:failure||(code!==0?err:undefined)});});
    child.stdin.on('error',()=>{}); child.stdin.end(input);
  });
}
async function callCao(event:string,payload:object,cwd:string):Promise<CaoReply> {
  const args=['host-event','--repo',cwd,'--host','pi'];
  const input=JSON.stringify({...payload,event});
  let result=await invoke('cao',args,input,cwd);
  if(result.missing) result=await invoke('sh',[path.join(ROOT,'cao'),...args],input,cwd);
  if(result.error||result.code!==0) return {diagnostic:result.error||'CAO_UNAVAILABLE'};
  try {
    const value=JSON.parse(result.out);
    if(!value||typeof value!=='object'||Array.isArray(value)||
       !['allow','deny'].includes(value.decision)) throw Error('CAO_PROTOCOL_INVALID');
    return value as CaoReply;
  } catch { return {diagnostic:'CAO_PROTOCOL_INVALID'}; }
}
function lastAssistantMessage(event:AgentEndEvent):string {
  for(const message of [...event.messages].reverse()) {
    if(message.role==='assistant') return message.content.filter(c=>c.type==='text').map(c=>c.text).join('\n');
  }
  return '';
}
function nativeSessionId(ctx:ExtensionContext):string {return ctx.sessionManager.getSessionId();}
```

每个原 handler `await callCao`，session_id 使用 ctx。before_agent_start 返回已有自定义上下文；tool_call 仅对明确内部状态 deny 返回 block；allow 的提醒通过去重自定义 steer 消息进入模型上下文（triggerTurn false），UI notify 仅作展示；tool_result 维持结果内容。diagnostic 每个会话每种原因只提示一次（hasUI 时 warn，否则 stderr），会话切换清理，不因桥失效拒绝所有工具。

- [ ] **3. 注册正确生命周期。** 工厂内部添加：

```typescript
const continued=new Set<string>();
pi.on('agent_end',async(event,ctx)=>{
  await callCao('turn_end',{session_id:nativeSessionId(ctx),last_assistant_message:lastAssistantMessage(event)},ctx.cwd);
});
pi.on('session_shutdown',async(event,ctx)=>{
  const id=nativeSessionId(ctx);
  await callCao('session_end',{session_id:id,reason:event.reason},ctx.cwd);
  continued.delete(id);
});
pi.on('agent_before_settle',async(event,ctx)=>{
  const id=nativeSessionId(ctx);
  const result=await callCao('before_settle',{session_id:id},ctx.cwd);
  if(result.decision==='deny'&&event.context.canContinue&&!continued.has(id)) {
    continued.add(id);
    return {entries:[{type:'custom_message' as const,customType:'coding-agent-orchestrator',
      content:result.reason||'CAO requires correction',display:true}],continue:true};
  }
});
```

before_agent_start 清除此轮 id 的 continued；普通未完成返回提醒，不触发无限继续。续执行分支仅传递明确纠正响应，不基于自然语言完成关键词触发。
- [ ] **4. 类型与测试。** tsconfig 使用 target ES2022/module ESNext/moduleResolution Bundler/strict/noEmit/skipLibCheck/types node，include hosts/pi/**/*.ts。开发依赖固定 Pi 1.1.0、typescript 5.9.3、@types/node 22；peer 范围声明已测试最低 API 1.1.0，不承诺未知未来主版本。`npm install --ignore-scripts` 后运行 `npx --no-install tsc --noEmit -p tsconfig.pi.json`。补假 CLI 超时/坏 JSON/ENOENT/非零、两次 before_settle 只继续一次、无 UI 和工具 deny/allow 测试；回归通过。
- [ ] **5. 提交。** `git add hosts/pi/coding-orchestrator.ts package.json package-lock.json tsconfig.pi.json tests/test_pi_adapter.mjs && git commit -m "fix: align Pi adapter with native session and boundary contracts"`。

## C2：执行停止、退出、提醒与原生输出

**Files:** Modify `scripts/cao.py:4581-4726`, `hosts/claude-code/settings.fragment.json`, `hosts/codex/hooks.json`, `hooks/host-event.sh`; Create `tests/test_host_lifecycle_contract.py`; Modify `tests/test_host_event_gate.py`, `tests/test_sessions.py`, `tests/test_host_output.py`。

**Interfaces:** host_event 区分 turn_end/stop、session_end；before_settle 默认 allow+context。native_hook_output 不丢 additionalContext；宿主明确 SessionEnd 才结束 Session。

- [ ] **1. 写生命周期回归。**

```python
from repair_helpers import RepairCase
from scripts import cao
class HostLifecycleTests(RepairCase):
    def test_negated_stop_keeps_session_active(self):
        self.start()
        self.run_cli('host-event','--host','pi',payload={'event':'session_start','session_id':'A'})
        rc,out=self.run_cli('host-event','--host','pi',payload={'event':'stop',
            'session_id':'A','last_assistant_message':'尚未完成，需要用户确认'})
        self.assertEqual(out['decision'],'allow',out)
        self.assertEqual(cao.load_session(self.repo,'A',host='pi')['status'],'active')
    def test_allow_keeps_additional_context(self):
        out=cao.native_hook_output('claude-code','pre_tool',
            {'decision':'allow','additional_context':'assess scope'})
        self.assertEqual(out['hookSpecificOutput']['additionalContext'],'assess scope')
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_host_lifecycle_contract.py -v`；旧 stop 拒绝/关闭，context 丢失。

- [ ] **2. 修改事件规范化和分支。** SessionEnd/session_end 不再映射 stop，agent_end 映射 turn_end；stop/turn_end 保存必要 Handoff 及提醒但不 end_session，不扫描自然语言关键词。只有 session_end/subagent_stop 调用 end_session。before_settle 只报告当前权威 completion_status；普通未完成不 deny，不自动继续。无真实 ID 返回 allow+诊断，不创建 `host-default`。

替换 pre_tool allow 覆盖分支：

```python
specific={'hookEventName':hook_name}
if context: specific['additionalContext']=context
if event=='pre_tool':
    specific['permissionDecision']='deny' if result.get('decision')=='deny' else 'allow'
    if result.get('decision')=='deny': specific['permissionDecisionReason']=result.get('reason') or 'Blocked by CAO'
    return {'hookSpecificOutput':specific}
```

- [ ] **3. 提醒和兼容。** 没有 Work Item、评估缺失/过期、重大歧义和工作流未执行的产品修改返回 allow+具体下一步；保护状态由 B5 deny。Claude 的 SessionEnd 增加独立 hook；Codex 只注册其实际支持的退出事件，若目标版本无该能力在 host status 明示，不虚构已结束。shell bridge 非零失败在 stderr 报原因并 exit 0（不锁死工具），CLI直接调用仍保留真实错误码；安全性依赖适配器可用的边界写入文档。
- [ ] **4. 测试。** 正反例覆盖读取/需求录入/治理命令通行、无评估产品写提醒、保护状态拒绝、停止不结束、退出才结束、不同 Host 相同原生 ID 不碰撞、子代理不结束主会话。旧 stop 即 ended 和中文完成即 block 的测试改为明确 SessionEnd 与 `cao done` 硬门禁，不删除后者。测试真实宿主输出字段及安装片段幂等性；全量通过。
- [ ] **5. 提交。** `git add scripts/cao.py hooks/host-event.sh hosts/claude-code/settings.fragment.json hosts/codex/hooks.json tests/test_host_lifecycle_contract.py tests/test_host_event_gate.py tests/test_sessions.py tests/test_host_output.py && git commit -m "fix: separate turn stops from session shutdown and preserve reminders"`。

## C3：Dashboard 权威状态和旧版/例外展示

**Files:** Modify `scripts/dashboard.py:41-65,415-443,446-616`, `scripts/dashboard.html`; Create `tests/test_dashboard_completion_state.py`; Modify `tests/test_dashboard.py`。

**Interfaces:** 工作视图新增 completion/gates/legacy_evidence/workflow_exception；B 的判定无 HTTP/CLI 依赖，产物读取显式接收 repo。

- [ ] **1. 写提前完成回归。**

```python
import unittest
from scripts.dashboard import infer_execution_phase
class PhaseTests(unittest.TestCase):
    def test_workflow_completion_is_not_work_completion(self):
        view=infer_execution_phase({'status':'active','execution':{
            'step':'native-sdd-workflow-completed'},'sdd':{'workflow':{'status':'completed'}}},[])
        self.assertNotEqual(view['id'],'done')
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_dashboard_completion_state.py -v`；旧函数显示 done。

- [ ] **2. 替换模糊完成条件。**

```python
if work.get('status') == 'completed':
    return {'id':'done','source':'cao-state'}
```

删除 `done|complete|closed` 任意 step 匹配；其余阶段使用明确映射，包括 selected/executed/completed 的原生工作流步骤到 planning/implementation/review。build_dashboard_overview 使用 completion_status，显示过期/阻塞理由及 gate_state，不因 passed 直接给绿灯。旧 completed 没有 v2 evidence 显示 legacy_evidence，不自动重开。显式失败例外有原因标签，不能伪装正常执行成功。
- [ ] **3. UI 测试。** 追加 active+workflow completed、completed+legacy、passed+代码变化、failed exception、scope unresolved、产物修改取消确认的 API/UI用例；保留 Origin/Host/loopback/路径防护断言。修改 HTML 仅新增必要标签，不做美术重设计。
- [ ] **4. 验证。** 两个 dashboard 测试模块及 Python 全量、Node、TS 检查通过。
- [ ] **5. 提交。** `git add scripts/dashboard.py scripts/dashboard.html tests/test_dashboard_completion_state.py tests/test_dashboard.py && git commit -m "fix: project authoritative completion and evidence states in dashboard"`。

## C4：验证子进程超时、回收和有限输出

**Files:** Create `scripts/verification_runner.py`, `scripts/windows_supervisor.py`, `tests/test_verification_runner.py`; Modify `scripts/cao.py:4071-4175`, `examples/config.json`。

**Interfaces:** `run_script(command: str,repo: Path,timeout: float=300.0,max_bytes: int=4000) -> dict` 返回 exit_code/stdout_tail/stderr_tail/timed_out；由原 reason/status 协议规范化。配置 verification.timeout_seconds 纳入 B acceptance_hash。

- [ ] **1. 写超时与大输出测试。**

```python
import pathlib, shlex, sys, tempfile, unittest
import os, subprocess
from scripts.verification_runner import run_script
class RunnerTests(unittest.TestCase):
    def test_timeout_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            argv=[sys.executable,'-c','import time; time.sleep(30)']
            cmd=subprocess.list2cmdline(argv) if os.name=='nt' else shlex.join(argv)
            result=run_script(cmd,pathlib.Path(td),timeout=.2)
            self.assertTrue(result['timed_out']); self.assertEqual(result['exit_code'],124)
    def test_large_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            argv=[sys.executable,'-c','print("x"*1000000)']
            cmd=subprocess.list2cmdline(argv) if os.name=='nt' else shlex.join(argv)
            r=run_script(cmd,pathlib.Path(td),max_bytes=4000)
            self.assertEqual(r['exit_code'],0); self.assertLessEqual(len(r['stdout_tail']),4000)
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_verification_runner.py -v`，预期模块缺失。

- [ ] **2. 实现完整执行器。**

```python
import math, os, signal, subprocess, sys, threading
from pathlib import Path
def run_script(command: str, repo: Path, timeout=300.0, max_bytes=4000):
    if not math.isfinite(timeout) or timeout <= 0 or max_bytes < 1:
        raise ValueError('VERIFICATION_LIMIT_INVALID')
    if os.name=='nt':
        launch=[sys.executable,str(Path(__file__).with_name('windows_supervisor.py')),command]
        kwargs={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP, 'shell':False}
    else:
        launch=command; kwargs={'start_new_session':True, 'shell':True}
    try:
        child=subprocess.Popen(launch,cwd=repo,stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,**kwargs)
    except OSError as exc:
        return {'exit_code':127,'stdout_tail':'','stderr_tail':str(exc),'timed_out':False}
    tails=[bytearray(),bytearray()]
    def read(stream,tail):
        try:
            while True:
                chunk=stream.read(4096)
                if not chunk: break
                tail.extend(chunk)
                if len(tail)>max_bytes: del tail[:-max_bytes]
        finally: stream.close()
    def kill_group():
        if os.name=='nt':
            if child.poll() is None: child.kill()
        else:
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
    readers=[threading.Thread(target=read,args=(stream,tail),daemon=True)
             for stream,tail in zip((child.stdout,child.stderr),tails)]
    for thread in readers: thread.start()
    timed_out=False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True; kill_group(); child.wait(timeout=5)
    finally:
        for thread in readers: thread.join(timeout=1)
        if any(thread.is_alive() for thread in readers):
            timed_out=True; kill_group()
            for thread in readers: thread.join(timeout=5)
    if any(thread.is_alive() for thread in readers):
        raise RuntimeError('VERIFICATION_PIPE_CLEANUP_FAILED')
    return {'exit_code':124 if timed_out else child.returncode,
            'stdout_tail':tails[0].decode('utf-8',errors='replace'),
            'stderr_tail':tails[1].decode('utf-8',errors='replace'),'timed_out':timed_out}
```

Windows 使用专用 supervisor 自身先加入 Kill-on-close Job Object，再启动验证命令，避免仅 taskkill 已退出父 PID 无法回收孙进程。`scripts/windows_supervisor.py` 的完整平台代码：

```python
import ctypes, subprocess, sys
from ctypes import wintypes as W
class Basic(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
              ('flags',W.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),
              ('active',W.DWORD),('affinity',ctypes.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
class Counters(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in
              ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
class Extended(ctypes.Structure):
    _fields_=[('basic',Basic),('io',Counters),('process_mem',ctypes.c_size_t),
              ('job_mem',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
def run_windows_command(command):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,W.LPCWSTR]
    kernel.CreateJobObjectW.restype=W.HANDLE
    kernel.SetInformationJobObject.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
    kernel.SetInformationJobObject.restype=W.BOOL
    kernel.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
    kernel.AssignProcessToJobObject.restype=W.BOOL
    kernel.GetCurrentProcess.restype=W.HANDLE
    job=kernel.CreateJobObjectW(None,None)
    limits=Extended(); limits.basic.flags=0x2000
    if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return subprocess.call(command,shell=True)
if __name__=='__main__':
    try: code=run_windows_command(sys.argv[1])
    except OSError as exc:
        print('VERIFICATION_JOB_FAILED: '+str(exc),file=sys.stderr); code=125
    sys.exit(code)
```

Job 句柄在 supervisor 退出时由系统关闭，整个成员树回收；不能提前 CloseHandle 杀死尚未输出退出码的 supervisor。Windows 嵌套 CI Job 不兼容时报告失败，不降级为不可靠回收。

在 B2 锁外循环调用 run_script，不 capture_output 全量缓冲。timeout/负返回码/Job 初始化失败均 error。验收 hash 同时扩展：`acceptance_hash(scripts, timeout_seconds=300.0)` 编码 `{scripts,timeout_seconds}`；`anchor_acceptance(work,scripts,timeout_seconds=300.0)` 接收同参数；`verification_snapshot(cfg)` 调用该 hash，start/reanchor 传配置 timeout。改动两侧所有调用者和测试，不能只绑定一侧。
- [ ] **3. 异常路径专项。** 清理错误必须执行最终 kill/wait 并返回 error 诊断，reader 失败不能静默返回成功；补充含孙进程、主命令先退出、超时后 decision 可取得锁、大 stderr/中文输出及 0/1/2/3 协议用例。Windows 测试命令使用 subprocess.list2cmdline；POSIX 用 shlex.join。
- [ ] **4. 验证。** 进程消失以 PID/句柄和 Event 确认，不能只断言父命令返回。runner 定向、verify 新鲜度与全量回归通过，300 秒默认不改变正常脚本结果。
- [ ] **5. 提交。** `git add scripts/verification_runner.py scripts/windows_supervisor.py scripts/completion_policy.py scripts/cao.py examples/config.json tests/test_verification_runner.py && git commit -m "fix: bound verification execution time and output memory"`。

## C5：快照提前剪枝与读取错误

**Files:** Modify `scripts/cao.py:1500-1535`; Create `tests/test_snapshot_integrity.py`。

**Interfaces:** 保持 `code_snapshot(repo, excluded=None) -> str` 和 rules_snapshot 签名与哈希排序；不引入易过期缓存。

- [ ] **1. 写忽略目录不读取回归。**

```python
from repair_helpers import RepairCase
from scripts import cao
from unittest import mock
class SnapshotTests(RepairCase):
    def test_walk_prunes_ignored_subtrees(self):
        with mock.patch('scripts.cao.os.walk', wraps=cao.os.walk) as walk:
            cao.code_snapshot(self.repo)
        self.assertTrue(walk.called)
```

运行 `"$PYTHON" -m unittest discover -s tests -p test_snapshot_integrity.py -v`，旧 rglob 不调用剪枝遍历。补真正检查被剪枝目录无递归访问的 spy，不能以这一接口断言替代性能验收。

- [ ] **2. 替换遍历，保留排序和哈希。**

```python
files=[]
for base, dirs, names in os.walk(repo, topdown=True, followlinks=False):
    dirs[:]=sorted(d for d in dirs if d not in SNAPSHOT_IGNORE_DIRS)
    files.extend(Path(base)/name for name in names)
for p in sorted(files):
    rp=p.relative_to(repo).as_posix()
    if rp in (excluded or set()) or not p.is_file(): continue
    if not p.resolve().is_relative_to(repo.resolve()): raise RuntimeError('SNAPSHOT_EXTERNAL_SYMLINK: '+rp)
    try: data=p.read_bytes()
    except OSError as exc: raise RuntimeError('SNAPSHOT_READ_FAILED: '+rp) from exc
    h.update(rp.encode()); h.update(b'\0'); h.update(hashlib.sha256(data).digest()); h.update(b'\0')
```

将此块放入原 code_snapshot 的 h 初始化与 return 之间；不添加缓存、不读取被忽略目录。rules_snapshot 读取错误同样报告而非跳过。目录符号链接不递归，文件链接在仓库内才参与，行为写入文档。
- [ ] **3. 验证。** 比较正常夹具新旧哈希相同；增删改文件改变快照；ignored 子树增删不改变且不扫描；权限错误/外部链接阻止新鲜度认证而不是误通过。做大 ignored 树计数基准，记录访问数和耗时，不用固定毫秒断言制造 CI 波动。
- [ ] **4. 定向及全量回归。** snapshot、verify、done、dashboard 均通过。
- [ ] **5. 提交。** `git add scripts/cao.py tests/test_snapshot_integrity.py && git commit -m "perf: prune ignored snapshot trees without weakening freshness"`。

## C6：文档、协议、CI、包和真实冒烟

**Files:** Modify `README.md`, `DESIGN.md`, `SKILL.md`, `examples/config.json`, `protocol/host-event.schema.json`, `protocol/work-item.schema.json`, `.github/workflows/tests.yml`; Create `docs/repair-validation.md`。

**Interfaces:** 外部文档准确描述提醒模式、硬完成门禁、测试声明、计划绑定、迁移与宿主能力；不新增第三套规则。

- [ ] **1. 新增契约验收测试。** `tests/test_host_lifecycle_contract.py` 加解析 JSON schema 枚举/字段与实际事件对照；`tests/test_cli_entry.py` 对安装后 help/test-script/plan 参数和新增 scripts 模块做 smoke。旧字段保留，标注退出事件与 turn_end/before_settle 区别。
- [ ] **2. 修改 CI。** 保留 Ubuntu Python 3.10—3.13 全套；追加独立 jobs：

```yaml
  state-platforms:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: python -m unittest discover -s tests -p test_state_store.py -v
      - run: python -m unittest discover -s tests -p test_verification_runner.py -v
  pi-contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - run: npm ci --ignore-scripts
      - run: npx --no-install tsc --noEmit -p tsconfig.pi.json
      - run: node --test tests/test_pi_adapter.mjs
```

Node 22 CI 必须 >=22.18；发布 metadata 只声明真正测试的 API 范围。Windows runner 不能跳过进程回收测试；Unix-only断言分别标注。
- [ ] **3. 完成包与文档变更。** README/DESIGN/SKILL 用明确文字替换“未评估时 Hook 拒绝产品写”，说明只能提醒；保留 state guard/done 硬约束及不可用诊断。更新 init --test-script、complete --plan/--plan-item/--no-plan、旧任务 reanchor、备份路径、legacy 证据、超时及动态 Shell 边界。示例实际命令必须在夹具跑通，不放 --help 充当测试。包元数据确保新模块与 TS/Hook 资源可安装，不能只测试 checkout。
- [ ] **4. 实际验证并记录。** Linux/macOS/Windows 专项 CI，Python 矩阵、Node、TS、editable 和 wheel 安装分别记录结果。三类宿主各跑“启动→需求录入→未评估产品写提醒→内部状态写拒绝→两轮执行仍 active→切换/退出 ended→完整原生流程与 verify/done”。BMAD/OpenSpec/Spec Kit 使用真实或明确标注的本地夹具；在线安装另外取得用户授权并记录版本，不执行则写未验证。
- [ ] **5. 提交和交付。** `git add README.md DESIGN.md SKILL.md examples/config.json protocol .github/workflows/tests.yml tests/test_cli_entry.py tests/test_host_lifecycle_contract.py docs/repair-validation.md && git commit -m "docs: align reminder policy and publish verified repair contracts"`。只暂存本任务改动；阻塞或失败留任务未完成，不把计划中的验收写成已经通过。

## C 出口与整体完成定义

- [ ] R01—R15 都有正式回归及成功对照；新增源文件均 <=500 行。
- [ ] 状态恢复、需求绑定、计划作用域、路径、Host、UI 与验证资源限制全部按真实结果验收。
- [ ] 手动宿主/在线安装缺失时明确列出，不宣称全平台集成完成；阻塞发布的能力须真实补验证。
- [ ] 报告修改文件、通过的命令与次数、兼容步骤和残余动态 Shell/语义验收边界；之后才讨论合并/发布，不自动合并或推送。
