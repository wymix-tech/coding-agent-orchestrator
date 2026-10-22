import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync} from 'node:fs';
import {spawn, spawnSync} from 'node:child_process';
import {once} from 'node:events';
import os from 'node:os';
import path from 'node:path';
import install from '../hosts/pi/coding-orchestrator.ts';
import {contextSummary} from '../hosts/pi/internal/context-presentation.ts';
import {stripTerminalSequences, visibleWidth} from '@earendil-works/pi-tui';
import {convertToLlm} from '@earendil-works/pi-coding-agent';

const posix = {skip: process.platform === 'win32'};

function resolvePython() {
  const candidate = process.env.CAO_TEST_PYTHON || 'python3';
  const result = spawnSync(candidate, ['-c', 'import os,sys; print(os.path.abspath(sys.executable))'],
    {encoding: 'utf8'});
  assert.equal(result.status, 0, `CAO_TEST_PYTHON_UNAVAILABLE: ${result.error || result.stderr}`);
  return result.stdout.trim();
}

function fixture() {
  const python = resolvePython(); // Resolve before any test removes Python from PATH.
  const dir = mkdtempSync(path.join(os.tmpdir(), 'cao-contract-'));
  mkdirSync(path.join(dir, '.orchestrator'));
  const keys = ['PATH', 'CAO_CAPTURE', 'CAO_FAKE_MODE', 'CAO_FAKE_CONTEXT', 'CAO_TEST_PYTHON'];
  const old = Object.fromEntries(keys.map(key => [key, process.env[key]]));
  process.env.CAO_TEST_PYTHON = python;
  process.env.CAO_CAPTURE = path.join(dir, 'events.jsonl');
  process.env.PATH = dir + path.delimiter + process.env.PATH;
  process.env.CAO_FAKE_MODE = 'allow';
  writeFileSync(path.join(dir, 'fake.py'), `import json,os,sys,time,subprocess
r=json.load(sys.stdin)
with open(os.environ['CAO_CAPTURE'],'a',encoding='utf-8') as f: f.write(json.dumps(r)+'\\n')
mode=os.environ.get('CAO_FAKE_MODE','allow')
if mode=='delay': time.sleep(.15)
if mode=='timeout': time.sleep(10)
if mode=='pipe-child':
 p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
 with open(os.path.join(os.path.dirname(os.environ['CAO_CAPTURE']),'child.pid'),'w') as f: f.write(str(p.pid))
if mode=='exit': print('backend failed',file=sys.stderr); sys.exit(2)
if mode=='long-error': print('X'*3500+'\\nREPAIR_DETAIL_END',file=sys.stderr); sys.exit(2)
if mode=='bad-json': print('not-json'); sys.exit(0)
if mode=='invalid': print(json.dumps({'decision':'unknown'})); sys.exit(0)
reply={'decision':'allow'}
if mode in ('deny','settle-deny'): reply={'decision':'deny','reason':'protected state'}
if mode=='context': reply['additional_context']=os.environ.get('CAO_FAKE_CONTEXT','review scope')
if mode=='deny-context': reply={'decision':'deny','reason':'protected state','additional_context':os.environ.get('CAO_FAKE_CONTEXT','review scope')}
if mode=='bad-fields': reply['additional_context']={'not':'text'}
if mode=='large': reply['additional_context']='x'*300000
print(json.dumps(reply))
`);
  writeFileSync(path.join(dir, 'cao'), '#!/bin/sh\nexec "${CAO_TEST_PYTHON:-python3}" ' +
    JSON.stringify(path.join(dir, 'fake.py')) + '\n', {mode: 0o755});
  const handlers = new Map(), sent = [], notices = [], widgets = new Map(), widgetUpdates = [];
  const commands = new Map(), renderers = new Map();
  install({on: (key, fn) => {handlers.set(key, fn); return () => handlers.delete(key);},
    registerCommand: (key, command) => commands.set(key, command),
    registerMessageRenderer: (key, render) => renderers.set(key, render),
    sendMessage: (...args) => sent.push(args)});
  const context = (id, hasUI = false) => ({cwd: dir, hasUI, mode: hasUI ? 'tui' : 'print',
    sessionManager: {getSessionId: () => id}, ui: {notify: (...args) => notices.push(args),
      setWidget: (key, value) => {
        widgetUpdates.push({key, value});
        if (value === undefined) {
          widgets.delete(key);
        } else {
          widgets.set(key, value);
        }
      }}});
  return {dir, handlers, sent, notices, widgets, widgetUpdates, commands, renderers, context,
    rows: () => readFileSync(process.env.CAO_CAPTURE, 'utf8').trim().split('\n').map(JSON.parse),
    close: () => {
      for (const key of keys) {
        if (old[key] === undefined) delete process.env[key]; else process.env[key] = old[key];
      }
      rmSync(dir, {recursive: true, force: true});
    }};
}

function realCoreFixture() {
  const f = fixture();
  const python = process.env.CAO_TEST_PYTHON || 'python3';
  const core = path.resolve(import.meta.dirname, '../scripts/cao.py');
  const env = {...process.env};
  for (const key of ['CAO_HOST', 'CLAUDE_CODE', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_SESSION_ID',
    'CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CODEX_HOME', 'PI_SESSION_ID', 'PI_CODING_AGENT']) delete env[key];
  function cli(...args) {
    const result = spawnSync(python, [core, ...args, '--repo', f.dir],
      {cwd: f.dir, env, encoding: 'utf8', timeout: 20000});
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return JSON.parse(result.stdout);
  }
  // Only a launcher: every reply comes from the real Python core.
  writeFileSync(path.join(f.dir, 'cao'), '#!/bin/sh\nexec "${CAO_TEST_PYTHON:-python3}" ' +
    JSON.stringify(core) + ' "$@"\n', {mode: 0o755});
  return {...f, python, cli};
}

test('real Pi intake delivers technology clarification before authoring requirements after restart', posix, async () => {
  const f = realCoreFixture();
  try {
    f.cli('init', '--host', 'pi');
    mkdirSync(path.join(f.dir, '_bmad'));
    writeFileSync(path.join(f.dir, '.orchestrator/provider-restarts.json'),
      JSON.stringify([{provider: 'bmad', host: 'pi'}]));
    const ctx = f.context('stack-clarification');
    await f.handlers.get('session_start')({reason: 'startup'}, ctx);
    const result = await f.handlers.get('before_agent_start')({prompt: '开发一个登录系统'}, ctx);
    assert.match(result.message.content, /TECH_STACK_CLARIFICATION_REQUIRED/);
    assert.match(result.message.content, /Offer a concrete recommendation as a proposal and WAIT/);
    assert.match(result.message.content, /not user authorization/);
    assert.equal(f.cli('list').work_items.length, 0);
    const requirement = f.cli('requirement', 'next-id', '--title', 'Login');
    assert.match(readFileSync(path.join(f.dir, requirement.path), 'utf8'), /待确认|pending/);
  } finally { f.close(); }
});

test('real Pi hooks stop after provider installation and acknowledge host resource reload', posix, async () => {
  const f = realCoreFixture();
  try {
    f.cli('init', '--host', 'pi');
    const ctx = f.context('same-conversation');
    await f.handlers.get('session_start')({reason: 'startup'}, ctx);
    const initial = await f.handlers.get('before_agent_start')({prompt: '开发一个登录系统'}, ctx);
    assert.match(initial.message.content, /Ask technology-stack questions only AFTER/);
    assert.doesNotMatch(initial.message.content, /ONE bundled question/);
    mkdirSync(path.join(f.dir, '_bmad'));
    // Installation state fixture; hook replies come from the real Python core.
    writeFileSync(path.join(f.dir, '.orchestrator/provider-restarts.json'),
      JSON.stringify([{provider: 'bmad', host: 'pi'}]));
    const event = {toolName: 'bash', input: {command: 'cao sdd install --provider bmad --host pi --yes'},
      content: [{type: 'text', text: 'installed'}], details: {restart_required: true}};
    const original = structuredClone(event);
    assert.equal(await f.handlers.get('tool_result')(event, ctx), undefined);
    assert.deepEqual(event, original);
    assert.match(f.sent.at(-1)[0].content, /HOST_RESTART_REQUIRED/);
    assert.equal(f.cli('bootstrap', '--host', 'pi').mode, 'host_restart_required');
    assert.equal((await f.handlers.get('tool_call')({toolName: 'write',
      input: {path: '_bmad-output/SPEC.md', content: 'planning'}}, ctx)).block, true);
    assert.equal(await f.handlers.get('agent_before_settle')(settle(true), ctx), undefined);
    await f.handlers.get('session_start')({reason: 'new'}, f.context('different-conversation'));
    assert.equal(f.cli('bootstrap', '--host', 'pi').mode, 'host_restart_required');
    const restarted = f.context('restarted-conversation');
    await f.handlers.get('session_start')({reason: 'reload'}, restarted);
    const resumed = await f.handlers.get('before_agent_start')({prompt: '继续'}, restarted);
    const bootstrap = f.cli('bootstrap', '--host', 'pi');
    assert.equal(bootstrap.mode, 'select');
    assert.equal(bootstrap.pending_request.prompt, '开发一个登录系统');
    assert.match(resumed.message.content, /开发一个登录系统/);
    assert.match(resumed.message.content, /TECH_STACK_CLARIFICATION_REQUIRED/);
  } finally { f.close(); }
});

test('real core blocks Pi business writes without Work and preserves CAO/native intake', posix, async () => {
  const f = realCoreFixture();
  try {
    mkdirSync(path.join(f.dir, '_bmad'));
    f.cli('init', '--host', 'codex', '--sdd', 'bmad');
    assert.equal(f.cli('bootstrap', '--host', 'pi').mode, 'select');
    const ctx = f.context('fresh-pi');
    const denied = await f.handlers.get('tool_call')({toolName: 'write', input: {
      path: 'src/main/java/AuthService.java', content: 'class AuthService {}'}}, ctx);
    assert.equal(denied.block, true);
    assert.match(denied.reason, /start --requirement/);
    for (const path of ['.orchestrator/requirements/login.md', '_bmad-output/spec.md',
      'openspec/changes/login/proposal.md', 'specs/login/spec.md', '.specify/spec.md']) {
      assert.equal(await f.handlers.get('tool_call')({toolName: 'write', input: {path, content: 'scope'}}, ctx), undefined);
    }
    assert.equal(await f.handlers.get('tool_call')({toolName: 'bash', input: {
      command: 'cao requirement next-id --title Login'}}, ctx), undefined);
    const requirement = f.cli('requirement', 'next-id', '--title', 'Login').path;
    writeFileSync(path.join(f.dir, requirement), '# Login\nUse user.yaml and sessions.');
    f.cli('start', '--requirement', requirement);
    assert.equal((await f.handlers.get('tool_call')({toolName: 'write', input: {
      path: 'src/main/java/AuthService.java', content: 'class AuthService {}'}}, ctx)).block, true);
    assert.equal(await f.handlers.get('tool_call')({toolName: 'write', input: {
      path: 'tests/test_setup.py', content: '# verification scaffold'}}, ctx), undefined);
    assert.equal(await f.handlers.get('agent_before_settle')({context: {canContinue: true}}, ctx), undefined);
  } finally {f.close();}
});

test('real Pi hooks permit host installation and literal probes before Work and baseline setup', posix, async () => {
  const f = realCoreFixture();
  try {
    mkdirSync(path.join(f.dir, '_bmad'));
    f.cli('init', '--host', 'codex', '--sdd', 'bmad');
    assert.equal(f.cli('host', 'status', '--host', 'pi').hosts[0].installed, false);
    for (const hasWork of [false, true]) {
      if (hasWork) {
        const requirement = f.cli('requirement', 'next-id', '--title', 'Install regression').path;
        writeFileSync(path.join(f.dir, requirement), '# Install regression\nPython stdlib fixture.');
        f.cli('start', '--requirement', requirement);
      }
      const ctx = f.context(hasWork ? 'empty-baseline-pi' : 'no-work-pi');
      for (const command of ['cao host install --host pi', 'cao host status --host pi',
        'python3.12 --version', 'sha256sum _bmad-output/spec.md', 'shasum -a 256 spec.md']) {
        assert.equal(await f.handlers.get('tool_call')({toolName: 'bash', input: {command}}, ctx), undefined);
      }
      // Execute the actual local Pi package install; no network/model call is needed.
      f.cli('host', 'install', '--host', 'pi');
      assert.equal(f.cli('host', 'status', '--host', 'pi').hosts[0].installed, true);
      for (const command of ['cao host install --host pi > .orchestrator/index.json',
        'sha256sum spec.md > product.py', 'python3.12 --version && touch product.py']) {
        assert.equal((await f.handlers.get('tool_call')({toolName: 'bash', input: {command}}, ctx)).block, true);
      }
      if (hasWork) {
        const denied = await f.handlers.get('tool_call')({toolName: 'write', input: {
          path: 'scripts/check.py', content: 'raise SystemExit(1)'}}, ctx);
        assert.equal(denied.block, true);
        assert.match(denied.reason, /scripts\/verification\//);
        assert.equal(await f.handlers.get('tool_call')({toolName: 'write', input: {
          path: 'scripts/verification/check.py', content: 'raise SystemExit(1)'}}, ctx), undefined);
      }
    }
  } finally {f.close();}
});

test('real Python core handles Pi tool_result with null verification', posix, async () => {
  const f = realCoreFixture();
  try {
    const {cli} = f;
    mkdirSync(path.join(f.dir, '_bmad'));
    writeFileSync(path.join(f.dir, 'requirement.md'), '# Real core fixture');
    cli('init', '--host', 'codex', '--sdd', 'bmad');
    const wid = cli('start', '--requirement', 'requirement.md').work_item.id;
    const workFile = path.join(f.dir, '.orchestrator/work', wid + '.json');
    assert.equal(JSON.parse(readFileSync(workFile, 'utf8')).verification, null);
    const event = {type: 'tool_result', toolName: 'write', input: {path: 'src.py', content: 'x'},
      content: [{type: 'text', text: 'Written'}], details: {bytes: 1}, structuredContent: {ok: true}};
    const original = JSON.parse(JSON.stringify(event));
    writeFileSync(path.join(f.dir, 'src.py'), 'x');
    assert.equal(await f.handlers.get('tool_result')(event, f.context('native-pi', true)), undefined);
    assert.deepEqual(event, original, 'real-core guidance must not change tool result data');
    assert.ok(f.notices.every(([, level]) => level === 'info'), JSON.stringify(f.notices));
    assert.match(f.sent.at(-1)[0].content, /VERIFICATION_BASELINE_REQUIRED/);
    assert.equal(JSON.parse(readFileSync(workFile, 'utf8')).verification, null);
  } finally {f.close();}
});

test('real Pi route preparation follows baseline, assessment, native workflow and SPEC', posix, async () => {
  const f = realCoreFixture();
  try {
    mkdirSync(path.join(f.dir, '_bmad'));
    const skill = '.agents/skills/bmad-build/SKILL.md';
    mkdirSync(path.dirname(path.join(f.dir, skill)), {recursive: true});
    writeFileSync(path.join(f.dir, skill), '---\nname: bmad-build\ndescription: Fixture\n---\nFollow the native renderer.');
    f.cli('init', '--host', 'pi', '--sdd', 'bmad');
    const req = f.cli('requirement', 'next-id', '--title', 'Login replay').path;
    writeFileSync(path.join(f.dir, req), '# Fixture login\nPrepare real native evidence.');
    const started = f.cli('start', '--requirement', req);
    assert.equal(started.next_action.reason, 'VERIFICATION_BASELINE_REQUIRED');
    const ctx = f.context('route-replay-pi');
    async function result(command) {
      const event = {toolName: 'bash', input: {command}, content: [{type: 'text', text: 'CLI result unchanged'}]};
      const before = JSON.parse(JSON.stringify(event));
      await f.handlers.get('tool_result')(event, ctx);
      assert.deepEqual(event, before);
      return f.sent.at(-1)[0].content;
    }
    assert.match(await result('cao start'), /VERIFICATION_BASELINE_REQUIRED/);
    mkdirSync(path.join(f.dir, 'acceptance-tests'));
    writeFileSync(path.join(f.dir, 'acceptance-tests/test_fixture.py'),
      'import unittest\nclass Fixture(unittest.TestCase):\n def test_fixture(self): self.assertEqual(2+2,4)\n');
    f.cli('verification', 'configure', '--test-script', JSON.stringify(f.python) + ' -m unittest discover -s acceptance-tests');
    assert.match(await result('cao verification configure'), /path assess/);
    f.cli('path', 'assess', '--profile', 'standard', '--reason', 'Native fixture preparation');
    const native = await result('cao path assess');
    assert.match(native, /NATIVE_WORKFLOW_SELECTION_REQUIRED/);
    assert.ok(native.includes(path.join(f.dir, skill)));
    assert.doesNotMatch(native, /references\/native-route-contract\.md/);
    f.cli('sdd', 'workflow', 'select', '--name', 'bmad-build', '--entrypoint', 'bmad-build');
    assert.match(await result('cao sdd workflow select'), /NATIVE_SPEC_PREPARATION_REQUIRED/);
    const target = f.cli('next').action.declaration_path;
    assert.equal(await f.handlers.get('tool_call')({toolName: 'write', input: {path: target, content: '{}'}}, ctx), undefined);
    const denied = await f.handlers.get('tool_call')({toolName: 'write', input: {path: 'route-declaration.json', content: '{}'}}, ctx);
    assert.equal(denied.block, true);
    assert.match(denied.reason, /NATIVE_ROUTE_REQUIRED/);
    assert.ok(denied.reason.includes(target));
  } finally {f.close();}
});

test('real Pi tool_result synchronizes a bound BMAD SPEC with a Chinese Work title', posix, async () => {
  const f = realCoreFixture();
  try {
    mkdirSync(path.join(f.dir, '_bmad'));
    f.cli('init', '--host', 'codex', '--sdd', 'bmad');
    const req = f.cli('requirement', 'next-id', '--title', '登录系统开发').path;
    writeFileSync(path.join(f.dir, req), '# 登录系统开发\n用户配置在 users.yml，使用 session。');
    const wid = f.cli('start', '--requirement', req).work_item.id;
    f.cli('path', 'assess', '--profile', 'light', '--reason', 'bounded fixture');
    const spec = '_bmad-output/implementation-artifacts/spec-001-login-system.md';
    mkdirSync(path.dirname(path.join(f.dir, spec)), {recursive: true});
    const body = "---\nstatus: 'ready-for-dev'\n---\n## Intent\nSession login\n## Acceptance\n- Read users.yml\n";
    writeFileSync(path.join(f.dir, spec), body);
    f.cli('sdd', 'spec', 'bind', '--spec', spec, '--criteria-section', 'Acceptance');
    writeFileSync(path.join(f.dir, spec), body.replace('ready-for-dev', 'in-progress'));
    const event = {toolName: 'edit', input: {path: spec, edits: [{oldText: 'ready-for-dev', newText: 'in-progress'}]},
      content: [{type: 'text', text: 'Successfully replaced one block'}], details: {ok: true}};
    const before = JSON.parse(JSON.stringify(event));
    assert.equal(await f.handlers.get('tool_result')(event, f.context('remote-pi')), undefined);
    assert.deepEqual(event, before);
    const work = JSON.parse(readFileSync(path.join(f.dir, '.orchestrator/work', wid + '.json'), 'utf8'));
    assert.equal(work.execution.step, 'implementation');
    assert.equal(work.sdd.native_state.canonical, 'in_progress');
    assert.equal(work.status, 'active');
  } finally {f.close();}
});

test('real core setup reaches Pi model with blocked product writes and preserved Work', posix, async () => {
  const f = realCoreFixture();
  try {
    const {cli} = f;
    mkdirSync(path.join(f.dir, '_bmad'));
    writeFileSync(path.join(f.dir, 'requirement.md'), '# Project baseline fixture');
    cli('init', '--host', 'codex', '--sdd', 'bmad');
    const wid = cli('start', '--requirement', 'requirement.md').work_item.id;
    const workFile = path.join(f.dir, '.orchestrator/work', wid + '.json');
    const originalAnchor = JSON.parse(readFileSync(workFile, 'utf8')).acceptance;
    const ctx = f.context('setup-pi');
    const guidance = await f.handlers.get('before_agent_start')({type: 'before_agent_start', prompt: 'continue'}, ctx);
    assert.match(guidance.message.content, /CAO verification setup/);
    assert.match(guidance.message.content, /tech stack/);
    assert.equal((await f.handlers.get('tool_call')({type: 'tool_call', toolName: 'write',
      input: {path: 'product.py', content: 'pass'}}, ctx)).block, true);
    assert.match(guidance.message.content, /verification configure/);
    for (const [, options] of f.sent) {
      assert.equal(options.triggerTurn, false);
      assert.equal(options.deliverAs, 'steer');
    }
    const denied = await f.handlers.get('tool_call')({type: 'tool_call', toolName: 'write',
      input: {path: '.orchestrator/index.json', content: '{}'}}, ctx);
    assert.equal(denied.block, true);
    mkdirSync(path.join(f.dir, 'acceptance-tests'));
    writeFileSync(path.join(f.dir, 'acceptance-tests/test_project.py'),
      'import unittest\nclass Project(unittest.TestCase):\n    def test_pass(self): self.assertEqual(2+2,4)\n');
    // Host events may update session metadata. First valid setup establishes the initial anchor.
    const before = readFileSync(workFile, 'utf8');
    assert.deepEqual(JSON.parse(before).acceptance, originalAnchor);
    const configured = cli('verification', 'configure', '--test-script',
      JSON.stringify(f.python) + ' -m unittest discover -s acceptance-tests');
    assert.equal(configured.verification_setup.ready, true);
    assert.equal(configured.acceptance_review_required, false);
    assert.equal(configured.initial_baseline_established, true);
    assert.notEqual(JSON.parse(readFileSync(workFile, 'utf8')).acceptance.config_sha256,
      originalAnchor.config_sha256);
    assert.equal(cli('bootstrap').mode, 'resume');
    const after = await f.handlers.get('before_agent_start')({type: 'before_agent_start', prompt: 'continue'}, ctx);
    assert.match(after.message.content, /path assess/);
    assert.equal(f.notices.length, 0, JSON.stringify(f.notices));
    assert.notEqual(JSON.parse(readFileSync(workFile, 'utf8')).acceptance.config_sha256,
      originalAnchor.config_sha256);
  } finally {f.close();}
});

const settle = canContinue => ({type: 'agent_before_settle', context: {canContinue},
  entries: [], continue: false, outcome: 'completed'});
const toolCall = {type: 'tool_call', toolName: 'write', input: {path: 'src.py', content: 'x'}};

test('Pi native session and assistant payloads', posix, async () => {
  const f = fixture();
  try {
    await f.handlers.get('session_start')({type: 'session_start', reason: 'startup'}, f.context('A'));
    await f.handlers.get('session_start')({type: 'session_start', reason: 'new'}, f.context('B'));
    await f.handlers.get('agent_end')({type: 'agent_end', messages: [
      {role: 'assistant', content: [{type: 'text', text: 'old reply'}]},
      {role: 'toolResult', content: [{type: 'text', text: 'ignore tool reply'}]},
      {role: 'assistant', content: [{type: 'thinking', thinking: 'private'},
        {type: 'text', text: '尚未完成'}, {type: 'text', text: '需要确认'}]},
    ]}, f.context('B'));
    assert.ok(f.handlers.has('session_shutdown'));
    await f.handlers.get('session_shutdown')({type: 'session_shutdown', reason: 'quit'}, f.context('B'));
    const rows = f.rows();
    assert.equal(rows[0].session_id, 'A');
    assert.equal(rows[1].session_id, 'B');
    assert.equal(rows[2].event, 'turn_end');
    assert.equal(rows[2].last_assistant_message, '尚未完成\n需要确认');
    assert.equal(rows[3].event, 'session_end');
    assert.equal(rows[3].reason, 'quit');
  } finally {f.close();}
});

test('host parentSession metadata keeps reviewer events separate from primary', posix, async () => {
  const f = fixture();
  try {
    const parent = path.join(f.dir, 'parent.jsonl');
    writeFileSync(parent, JSON.stringify({type: 'session', id: 'primary-native'}) + '\n');
    const ctx = f.context('reviewer-native');
    ctx.sessionManager.getHeader = () => ({parentSession: parent});
    await f.handlers.get('session_start')({type: 'session_start'}, ctx);
    await f.handlers.get('before_agent_start')({prompt: 'review'}, ctx);
    await f.handlers.get('session_shutdown')({reason: 'quit'}, ctx);
    const rows = f.rows();
    assert.equal(rows[0].event, 'subagent_start');
    assert.equal(rows[2].event, 'subagent_stop');
    for (const row of rows) {
      assert.equal(row.kind, 'subagent');
      assert.equal(row.parent_session_id, 'primary-native');
      assert.equal(row.subagent_id, 'reviewer-native');
    }
    ctx.sessionManager.getHeader = () => ({parentSession: path.join(f.dir, 'missing-parent.jsonl')});
    await f.handlers.get('session_start')({type: 'session_start'}, ctx);
    assert.equal(f.rows().at(-1).kind, 'subagent');
  } finally { f.close(); }
});

test('detached runner metadata keeps old headerless child sessions separate', posix, async () => {
  const f = fixture();
  const oldChild = process.env.PI_SUBAGENT_CHILD;
  const oldParent = process.env.PI_SUBAGENT_PARENT_SESSION;
  try {
    process.env.PI_SUBAGENT_CHILD = '1';
    process.env.PI_SUBAGENT_PARENT_SESSION = 'parent-native';
    const ctx = f.context('child-native');
    await f.handlers.get('session_start')({reason: 'startup'}, ctx);
    await f.handlers.get('session_shutdown')({reason: 'quit'}, ctx);
    const rows = f.rows();
    assert.equal(rows[0].event, 'subagent_start');
    assert.equal(rows[1].event, 'subagent_stop');
    for (const row of rows) {
      assert.equal(row.kind, 'subagent');
      assert.equal(row.parent_session_id, 'parent-native');
      assert.equal(row.subagent_id, 'child-native');
    }
  } finally {
    if (oldChild === undefined) delete process.env.PI_SUBAGENT_CHILD;
    else process.env.PI_SUBAGENT_CHILD = oldChild;
    if (oldParent === undefined) delete process.env.PI_SUBAGENT_PARENT_SESSION;
    else process.env.PI_SUBAGENT_PARENT_SESSION = oldParent;
    f.close();
  }
});

test('CLI bridge does not block Pi event loop', posix, async () => {
  const f = fixture();
  let ticks = 0;
  const timer = setInterval(() => ticks++, 10);
  try {
    process.env.CAO_FAKE_MODE = 'delay';
    await f.handlers.get('session_start')({type: 'session_start'}, f.context('A'));
    assert.ok(ticks > 2, 'spawnSync blocked the event loop');
  } finally {clearInterval(timer); f.close();}
});

test('allow guidance reaches model once without triggering another turn', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'context';
    assert.equal(await f.handlers.get('tool_call')(toolCall, f.context('A')), undefined);
    await f.handlers.get('tool_call')(toolCall, f.context('A'));
    assert.equal(f.notices.length, 0, 'no UI must not notify');
    assert.equal(f.sent.length, 1);
    assert.equal(f.sent[0][0].content, 'review scope');
    assert.equal(f.sent[0][0].display, false);
    assert.deepEqual(f.sent[0][1], {triggerTurn: false, deliverAs: 'steer'});
    await f.handlers.get('tool_call')(toolCall, f.context('B'));
    assert.equal(f.sent.length, 2, 'distinct sessions must not share reminder deduplication');
  } finally {f.close();}
});

const resumeContext = [
  '# Coding Agent Orchestrator Resume Context',
  '## Read before acting',
  JSON.stringify({stage: 'completion', read_before_action: ['/project/SKILL.md']}),
  `Work Item: 轻量登录系统${'🐍'.repeat(80)} (work-login)`,
  'Status: active', 'SDD: bmad', 'Step: review-dispatch-patches',
  '## Important Decisions', 'Python + FastAPI; '.repeat(500),
  '## Next Action',
  JSON.stringify({stage: 'completion', reason: 'NATIVE_REVIEW_REQUIRED', instruction: 'Review every active layer.'}),
  '## Verification', '{"status":"pending"}',
].join('\n');

test('long resume guidance is hidden from transcript and updates one width-bounded widget', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'context';
    process.env.CAO_FAKE_CONTEXT = resumeContext;
    const ctx = f.context('compact', true);
    await f.handlers.get('session_start')({reason: 'startup'}, ctx);
    const prompt = await f.handlers.get('before_agent_start')({prompt: '继续'}, ctx);
    await f.handlers.get('tool_call')(toolCall, ctx);
    await f.handlers.get('tool_call')(toolCall, ctx);
    assert.equal(f.notices.length, 0, 'complete context must not also be printed as a notification');
    assert.equal(f.widgets.size, 1, 'updates must reuse the same widget');
    assert.equal(f.widgetUpdates.length, 2, 'identical summaries must not repeatedly rebuild the widget');
    assert.equal(prompt.message.display, false);
    for (const message of [prompt.message, ...f.sent.map(([message]) => message)]) {
      assert.equal(message.display, false);
      assert.equal(message.content, resumeContext);
      const converted = convertToLlm([{role: 'custom', timestamp: 1, ...message}]);
      assert.equal(converted[0].content[0].text, resumeContext, 'Pi must still send the full content to the model');
    }
    const widget = [...f.widgets.values()][0]();
    for (const width of [1, 8, 24, 80, 160]) {
      const lines = widget.render(width);
      assert.ok(lines.length <= 4);
      assert.ok(lines.every(value => visibleWidth(value) <= width), `overflow at width ${width}`);
    }
    const summary = widget.render(160).map(stripTerminalSequences).join('\n');
    assert.match(summary, /轻量登录系统/);
    assert.match(summary, /completion.*bmad.*review-dispatch-patches/);
    assert.match(summary, /Review every active layer/);
    assert.match(summary, /\/cao-context/);
    assert.doesNotMatch(summary, /read_before_action|FastAPI/);
    process.env.CAO_FAKE_CONTEXT = 'HOST_RESTART_REQUIRED: Restart Pi before continuing.';
    await f.handlers.get('tool_result')({toolName: 'read', input: {}, content: []}, ctx);
    assert.equal(f.widgets.size, 1);
    assert.match([...f.widgets.values()][0]().render(100).join('\n'), /HOST_RESTART_REQUIRED/);
    await f.handlers.get('session_shutdown')({reason: 'quit'}, ctx);
    assert.equal(f.widgets.size, 0);
  } finally { f.close(); }
});

test('legacy visible CAO entries collapse without modifying their full content', posix, () => {
  const f = fixture();
  try {
    const message = {content: resumeContext, customType: 'coding-agent-orchestrator', display: true};
    const render = f.renderers.get(message.customType);
    const collapsed = render(message, {expanded: false}).render(50);
    assert.ok(collapsed.length <= 4);
    assert.ok(collapsed.every(value => visibleWidth(value) <= 50));
    assert.match(render(message, {expanded: true}).render(100).join('\n'), /Important Decisions/);
    assert.equal(message.content, resumeContext);
    assert.ok(contextSummary('## Next Action\n{"stage":4,"reason":[]}').length <= 4);
    assert.doesNotThrow(() => contextSummary('## Next Action\nnot-json'));
  } finally { f.close(); }
});

test('context viewer scrolls within the viewport, closes, and isolates sessions without model messages', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'context';
    process.env.CAO_FAKE_CONTEXT = Array.from({length: 100}, (_, i) => `Line ${i}: 全文详情`).join('\n');
    const ctx = f.context('viewer', true);
    await f.handlers.get('session_start')({reason: 'startup'}, ctx);
    const sentBefore = f.sent.length;
    let component, closed = false;
    const tui = {terminal: {rows: 12}, requestRender() {}};
    ctx.ui.custom = async factory => {component = factory(tui, {}, {}, () => {closed = true;});};
    await f.commands.get('cao-context').handler('', ctx);
    assert.match(component.render(40).join('\n'), /Line 0:/);
    component.handleInput('\x1b[6~'); // Page Down
    assert.match(component.render(40).join('\n'), /Line 8:/);
    component.handleInput('\x1b[F'); // End
    assert.match(component.render(40).join('\n'), /Line 99:/);
    tui.terminal.rows = 8;
    const narrow = component.render(10);
    assert.ok(narrow.length <= 8);
    assert.ok(narrow.every(value => visibleWidth(value) <= 10));
    component.handleInput('\x1b');
    assert.equal(closed, true);
    assert.equal(f.sent.length, sentBefore);
    const other = f.context('other', true);
    other.ui.custom = ctx.ui.custom;
    await f.handlers.get('session_start')({reason: 'resume'}, other);
    process.env.CAO_FAKE_MODE = 'allow';
    await f.handlers.get('session_start')({reason: 'resume'}, other);
    await f.commands.get('cao-context').handler('', other);
    assert.match(component.render(100).join('\n'), /No CAO context recorded/);
    assert.doesNotMatch(component.render(100).join('\n'), /Line 0:/);
  } finally { f.close(); }
});

test('RPC and print modes keep model guidance without terminal widgets', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'context';
    for (const mode of ['rpc', 'print', 'json']) {
      const ctx = f.context(mode, mode === 'rpc');
      ctx.mode = mode;
      await f.handlers.get('session_start')({reason: 'startup'}, ctx);
      assert.equal(f.sent.at(-1)[0].content, 'review scope');
      assert.equal(f.sent.at(-1)[0].display, false);
      await f.commands.get('cao-context').handler('', ctx);
    }
    assert.equal(f.widgetUpdates.length, 0);
    assert.equal(f.notices.length, 1, 'only explicit RPC viewer request produces a short notice');
  } finally { f.close(); }
});

test('denied guidance and settle correction stay complete while the widget flags the block', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'deny-context';
    process.env.CAO_FAKE_CONTEXT = resumeContext;
    const ctx = f.context('blocked', true);
    const denied = await f.handlers.get('tool_call')(toolCall, ctx);
    assert.deepEqual(denied, {block: true, reason: 'protected state'});
    assert.match([...f.widgets.values()][0]().render(100).join('\n'), /Blocked/);
    assert.match([...f.widgets.values()][0]().render(100).join('\n'), /protected state/);
    const correction = await f.handlers.get('agent_before_settle')(settle(true), ctx);
    assert.equal(correction.continue, true);
    assert.equal(correction.entries[0].display, false);
    assert.equal(correction.entries[0].content, 'protected state');
    assert.ok(f.sent.every(([message]) => !message.display && message.content === resumeContext));
    assert.equal(f.notices.length, 0);
  } finally { f.close(); }
});

test('widget failures do not drop guidance or change native gate results', posix, async () => {
  const f = fixture();
  try {
    const ctx = f.context('ui-failure', true);
    ctx.ui.setWidget = () => {throw Error('renderer unavailable');};
    process.env.CAO_FAKE_MODE = 'context';
    await f.handlers.get('session_start')({reason: 'startup'}, ctx);
    assert.equal(f.sent.at(-1)[0].content, 'review scope');
    assert.equal(await f.handlers.get('tool_call')(toolCall, ctx), undefined);
    process.env.CAO_FAKE_MODE = 'deny-context';
    assert.deepEqual(await f.handlers.get('tool_call')(toolCall, ctx), {block: true, reason: 'protected state'});
    await f.handlers.get('session_shutdown')({reason: 'quit'}, ctx);
  } finally { f.close(); }
});

test('long backend diagnostics produce a short warning and keep repair details in the viewer', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'long-error';
    const ctx = f.context('diagnostic', true);
    const denied = await f.handlers.get('tool_call')(toolCall, ctx);
    assert.equal(denied.block, true);
    assert.match(denied.reason, /REPAIR_DETAIL_END/);
    assert.equal(f.notices.length, 1);
    assert.ok(f.notices[0][0].length < 128);
    let component;
    ctx.ui.custom = async factory => {
      component = factory({terminal: {rows: 20}, requestRender() {}}, {}, {}, () => {});
    };
    await f.commands.get('cao-context').handler('', ctx);
    component.render(80);
    component.handleInput('\x1b[F');
    assert.match(component.render(80).join('\n'), /do not bypass the gate/);
    assert.match(f.sent.at(-1)[0].content, /REPAIR_DETAIL_END/);
  } finally { f.close(); }
});

test('tool deny and bridge failures block writes and diagnose once', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'deny';
    assert.deepEqual(await f.handlers.get('tool_call')(toolCall, f.context('A')), {block: true, reason: 'protected state'});
    for (const mode of ['bad-json', 'invalid', 'bad-fields', 'exit']) {
      process.env.CAO_FAKE_MODE = mode;
      assert.equal((await f.handlers.get('tool_call')(toolCall, f.context(mode, true))).block, true);
      await f.handlers.get('tool_call')(toolCall, f.context(mode, true));
    }
    assert.equal(f.notices.filter(([, level]) => level === 'warning').length, 4);
    assert.equal(f.notices.filter(([, level]) => level === 'info').length, 0);
    assert.equal(f.sent.length, 4, 'repeated faults must not duplicate recovery guidance');
  } finally {f.close();}
});

test('prompt context and tool result data follow native result contracts', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'context';
    const prompt = await f.handlers.get('before_agent_start')({type: 'before_agent_start', prompt: 'work'}, f.context('A'));
    assert.equal(prompt.message.content, 'review scope');
    const content = [{type: 'text', text: 'original'}], structuredContent = {answer: 42};
    const result = await f.handlers.get('tool_result')({type: 'tool_result', toolName: 'read',
      input: {}, content, structuredContent, details: {}}, f.context('A'));
    assert.equal(result, undefined, 'tool_result guidance must not replace or drop tool data');
    assert.deepEqual(content, [{type: 'text', text: 'original'}]);
    assert.deepEqual(structuredContent, {answer: 42});
  } finally {f.close();}
});

test('settle correction continues once, respects capability, resets for next user turn', posix, async () => {
  const f = fixture();
  try {
    assert.ok(f.handlers.has('agent_before_settle'));
    process.env.CAO_FAKE_MODE = 'settle-deny';
    const first = await f.handlers.get('agent_before_settle')(settle(true), f.context('A'));
    assert.equal(first.continue, true);
    assert.equal(first.entries[0].type, 'custom_message');
    assert.equal(first.entries[0].content, 'protected state');
    assert.equal(await f.handlers.get('agent_before_settle')(settle(true), f.context('A')), undefined);
    assert.equal(await f.handlers.get('agent_before_settle')(settle(false), f.context('B')), undefined);
    await f.handlers.get('before_agent_start')({type: 'before_agent_start', prompt: 'next'}, f.context('A'));
    assert.equal((await f.handlers.get('agent_before_settle')(settle(true), f.context('A'))).continue, true);
  } finally {f.close();}
});

test('oversized bridge output is rejected instead of injected into context', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'large';
    assert.equal(await f.handlers.get('before_agent_start')({type: 'before_agent_start', prompt: 'work'}, f.context('A', true)), undefined);
    assert.equal(f.notices.length, 1);
    assert.match(f.notices[0][0], /CAO_OUTPUT_LIMIT/);
  } finally {f.close();}
});

test('missing console CLI uses checkout fallback', posix, async () => {
  const f = fixture();
  try {
    rmSync(path.join(f.dir, 'cao'));
    writeFileSync(path.join(f.dir, 'sh'), '#!/bin/sh\nexec "${CAO_TEST_PYTHON:-python3}" ' +
      JSON.stringify(path.join(f.dir, 'fake.py')) + '\n', {mode: 0o755});
    process.env.PATH = f.dir;
    await f.handlers.get('session_start')({type: 'session_start'}, f.context('A', true));
    assert.equal(f.notices.length, 0, JSON.stringify(f.notices));
    assert.equal(f.rows()[0].session_id, 'A');
  } finally {f.close();}
});

test('missing bridge diagnoses on stderr without requiring a UI', posix, async () => {
  const f = fixture();
  const write = process.stderr.write;
  const diagnostics = [];
  process.stderr.write = chunk => {diagnostics.push(String(chunk)); return true;};
  try {
    rmSync(path.join(f.dir, 'cao'));
    process.env.PATH = f.dir;
    assert.equal((await f.handlers.get('tool_call')(toolCall, f.context('A'))).block, true);
    await f.handlers.get('tool_call')(toolCall, f.context('A'));
    assert.equal(f.notices.length, 0);
    assert.equal(diagnostics.length, 1);
    assert.match(diagnostics[0], /CAO/);
  } finally {process.stderr.write = write; f.close();}
});

test('inherited output pipes cannot postpone bridge timeout', {...posix, timeout: 15000}, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'pipe-child';
    const start = Date.now();
    assert.equal((await f.handlers.get('tool_call')(toolCall, f.context('A', true))).block, true);
    assert.ok(Date.now() - start < 10000);
    assert.match(f.notices[0][0], /CAO_TIMEOUT/);
    const pid = Number(readFileSync(path.join(f.dir, 'child.pid'), 'utf8'));
    try {
      process.kill(pid, 0);
      // Linux may retain a killed orphan as a zombie until init reaps it.
      assert.match(readFileSync(`/proc/${pid}/stat`, 'utf8'), /\) Z /);
    } catch (error) {
      assert.ok(['ESRCH', 'ENOENT'].includes(error.code), String(error));
    }
  } finally {f.close();}
});

test('bridge timeout is bounded and fail-closed', {...posix, timeout: 12000}, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'timeout';
    const start = Date.now();
    assert.equal((await f.handlers.get('tool_call')(toolCall, f.context('A', true))).block, true);
    assert.ok(Date.now() - start < 10000);
    assert.match(f.notices[0][0], /CAO_TIMEOUT/);
  } finally {f.close();}
});


test('fallback accepts an interpreter path containing spaces', posix, async () => {
  const python = resolvePython();
  const dir = mkdtempSync(path.join(os.tmpdir(), 'cao python '));
  const previous = process.env.CAO_TEST_PYTHON;
  try {
    const alias = path.join(dir, 'python interpreter');
    symlinkSync(python, alias);
    process.env.CAO_TEST_PYTHON = alias;
    const f = fixture();
    try {
      rmSync(path.join(f.dir, 'cao'));
      writeFileSync(path.join(f.dir, 'sh'), '#!/bin/sh\nexec "$CAO_TEST_PYTHON" ' +
        JSON.stringify(path.join(f.dir, 'fake.py')) + '\n', {mode: 0o755});
      process.env.PATH = f.dir;
      await f.handlers.get('session_start')({type: 'session_start'}, f.context('spaces', true));
      assert.equal(f.notices.length, 0, JSON.stringify(f.notices));
      assert.equal(f.rows()[0].session_id, 'spaces');
    } finally { f.close(); }
  } finally {
    if (previous === undefined) delete process.env.CAO_TEST_PYTHON;
    else process.env.CAO_TEST_PYTHON = previous;
    rmSync(dir, {recursive: true, force: true});
  }
});

test('an unavailable interpreter diagnoses the actual fixture failure', posix, () => {
  const previous = process.env.CAO_TEST_PYTHON;
  try {
    process.env.CAO_TEST_PYTHON = '/nonexistent/cao-fixture-python';
    assert.throws(resolvePython, /CAO_TEST_PYTHON_UNAVAILABLE/);
  } finally {
    if (previous === undefined) delete process.env.CAO_TEST_PYTHON;
    else process.env.CAO_TEST_PYTHON = previous;
  }
});


test('offline gate allows literal recovery only and blocks disguised shell effects', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'bad-json';
    const ctx = f.context('offline');
    for (const command of ['pwd', 'cao status', 'cao host status --host pi', 'cao host install --host pi']) {
      assert.equal(await f.handlers.get('tool_call')({toolName: 'bash', input: {command}}, ctx), undefined);
    }
    for (const command of ['cao done', 'cao init --force', 'cao status > product.py',
      'cao host install --host pi && touch product.py', 'cat README.md | sh',
      'cao status; touch product.py', 'cao status\nrm file', 'cao status $(touch file)',
      'cao status `touch file`', 'cat <(touch file)', 'python -c "print(1)"']) {
      assert.equal((await f.handlers.get('tool_call')({toolName: 'bash', input: {command}}, ctx)).block, true, command);
    }
    assert.equal((await f.handlers.get('tool_call')({toolName: 'bash',
      input: {command: 'cao status', cwd: '/other'}}, ctx)).block, true);
    for (const toolName of ['read', 'ls', 'grep', 'find']) {
      assert.equal(await f.handlers.get('tool_call')({toolName, input: {path: 'README.md'}}, ctx), undefined);
    }
    for (const toolName of ['edit', 'write', 'apply_patch', 'customUnknown']) {
      assert.equal((await f.handlers.get('tool_call')({toolName, input: {}}, ctx)).block, true);
    }
    assert.match(f.sent.at(-1)[0].content, /GATE_UNAVAILABLE/);
  } finally { f.close(); }
});

test('disabled projects are distinct from missing configuration and cannot downgrade after activation', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'exit';
    rmSync(path.join(f.dir, '.orchestrator'), {recursive: true});
    const ctx = f.context('enrollment');
    assert.equal(await f.handlers.get('tool_call')(toolCall, ctx), undefined);
    mkdirSync(path.join(f.dir, '.orchestrator'));
    assert.equal((await f.handlers.get('tool_call')(toolCall, ctx)).block, true);
    rmSync(path.join(f.dir, '.orchestrator'), {recursive: true});
    assert.equal((await f.handlers.get('tool_call')(toolCall, ctx)).block, true);
  } finally { f.close(); }
});

test('notification failures preserve tool results while oversized pre-tool responses block writes', posix, async () => {
  const f = fixture();
  try {
    const ctx = f.context('notifications');
    process.env.CAO_FAKE_MODE = 'large';
    assert.equal((await f.handlers.get('tool_call')(toolCall, ctx)).block, true);
    const event = {toolName: 'write', input: {path: 'x'}, content: [{type: 'text', text: 'original'}]};
    const original = structuredClone(event);
    assert.equal(await f.handlers.get('tool_result')(event, ctx), undefined);
    assert.deepEqual(event, original);
  } finally { f.close(); }
});


test('nested project directories retain offline gate enforcement', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'bad-json';
    const nested = path.join(f.dir, 'src', 'nested');
    mkdirSync(nested, {recursive: true});
    const ctx = {...f.context('nested'), cwd: nested};
    assert.equal((await f.handlers.get('tool_call')(toolCall, ctx)).block, true);
  } finally { f.close(); }
});

test('state lock contention returns a core diagnostic before the bridge deadline', {...posix, timeout: 12000}, async () => {
  const f = realCoreFixture();
  let owner;
  try {
    mkdirSync(path.join(f.dir, '_bmad'));
    f.cli('init', '--host', 'codex', '--sdd', 'bmad');
    owner = spawn(f.python, ['-u', '-c',
      "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(30)",
      path.join(f.dir, '.orchestrator/state.lock')], {stdio: ['ignore', 'pipe', 'pipe']});
    await once(owner.stdout, 'data');
    const started = Date.now();
    const reply = await f.handlers.get('tool_call')(toolCall, f.context('lock'));
    assert.equal(reply.block, true);
    assert.match(reply.reason, /STATE_LOCK_TIMEOUT/);
    assert.ok(Date.now() - started < 8000);
  } finally {
    if (owner) {
      owner.kill('SIGKILL');
      await once(owner, 'close');
    }
    f.close();
  }
});
