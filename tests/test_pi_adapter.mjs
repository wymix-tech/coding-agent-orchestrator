import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import install from '../hosts/pi/coding-orchestrator.ts';

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
  const keys = ['PATH', 'CAO_CAPTURE', 'CAO_FAKE_MODE', 'CAO_TEST_PYTHON'];
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
if mode=='pipe-child': subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])
if mode=='exit': print('backend failed',file=sys.stderr); sys.exit(2)
if mode=='bad-json': print('not-json'); sys.exit(0)
if mode=='invalid': print(json.dumps({'decision':'unknown'})); sys.exit(0)
reply={'decision':'allow'}
if mode in ('deny','settle-deny'): reply={'decision':'deny','reason':'protected state'}
if mode=='context': reply['additional_context']='review scope'
if mode=='bad-fields': reply['additional_context']={'not':'text'}
if mode=='large': reply['additional_context']='x'*300000
print(json.dumps(reply))
`);
  writeFileSync(path.join(dir, 'cao'), '#!/bin/sh\nexec "${CAO_TEST_PYTHON:-python3}" ' +
    JSON.stringify(path.join(dir, 'fake.py')) + '\n', {mode: 0o755});
  const handlers = new Map(), sent = [], notices = [];
  install({on: (key, fn) => {handlers.set(key, fn); return () => handlers.delete(key);},
    sendMessage: (...args) => sent.push(args)});
  const context = (id, hasUI = false) => ({cwd: dir, hasUI, mode: hasUI ? 'tui' : 'print',
    sessionManager: {getSessionId: () => id}, ui: {notify: (...args) => notices.push(args)}});
  return {dir, handlers, sent, notices, context,
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
    assert.equal(f.notices.length, 0, JSON.stringify(f.notices));
    assert.equal(JSON.parse(readFileSync(workFile, 'utf8')).verification, null);
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
    assert.deepEqual(f.sent[0][1], {triggerTurn: false, deliverAs: 'steer'});
    await f.handlers.get('tool_call')(toolCall, f.context('B'));
    assert.equal(f.sent.length, 2, 'distinct sessions must not share reminder deduplication');
  } finally {f.close();}
});

test('tool deny blocks while bridge failures fail open and diagnose once', posix, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'deny';
    assert.deepEqual(await f.handlers.get('tool_call')(toolCall, f.context('A')), {block: true, reason: 'protected state'});
    for (const mode of ['bad-json', 'invalid', 'bad-fields', 'exit']) {
      process.env.CAO_FAKE_MODE = mode;
      assert.equal(await f.handlers.get('tool_call')(toolCall, f.context(mode, true)), undefined);
      await f.handlers.get('tool_call')(toolCall, f.context(mode, true));
    }
    assert.equal(f.notices.length, 4);
    assert.ok(f.notices.every(([, level]) => level === 'warning'));
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
    assert.equal(await f.handlers.get('tool_call')(toolCall, f.context('A')), undefined);
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
    assert.equal(await f.handlers.get('tool_call')(toolCall, f.context('A', true)), undefined);
    assert.ok(Date.now() - start < 8000);
    assert.match(f.notices[0][0], /CAO_TIMEOUT/);
  } finally {f.close();}
});

test('bridge timeout is bounded and fail-open', {...posix, timeout: 12000}, async () => {
  const f = fixture();
  try {
    process.env.CAO_FAKE_MODE = 'timeout';
    const start = Date.now();
    assert.equal(await f.handlers.get('tool_call')(toolCall, f.context('A', true)), undefined);
    assert.ok(Date.now() - start < 8000);
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
