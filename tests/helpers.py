"""Real, pre-start verification standards for CAO integration tests."""
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time


def configure_verification(repo, command=None):
    """Provision a genuine unittest, or an explicitly general verification script."""
    repo = pathlib.Path(repo)
    gates = []
    if command is None:
        tests = repo / "acceptance-tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_acceptance.py").write_text(
            "import unittest\nclass Acceptance(unittest.TestCase):\n"
            "    def test_ok(self): self.assertEqual(1 + 1, 2)\n", encoding="utf-8")
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "acceptance-tests", "-v"]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        gates = ["tests"]
    path = repo / ".orchestrator/config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("verification", {})["scripts"] = [
        {"name": "script-1", "command": command, "required": True, "gates": gates}
    ]
    cfg["verification"]["configured_at"] = int(time.time())
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def verify_via_config(repo, run):
    """Execute the existing standard without altering it or fabricating its age."""
    return run(repo, "verify")


def bind_light_spec(repo, run, path, wid=None):
    """Bind an explicit local-fixture criterion without native workflow registration."""
    target = pathlib.Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# Fixture SPEC\nBounded local fixture.\n## Acceptance\n- The fixture check passes.\n', encoding='utf-8')
    args = ['sdd', 'spec', 'bind', '--spec', path, '--criteria-section', 'Acceptance']
    if wid:
        args += ['--work', wid]
    result = run(repo, *args)
    assert result[0] == 0, result


def complete_acceptance(repo, run, wid=None, prepare_only=False):
    """Real CLI final acceptance for local fixtures, not a real provider/reviewer test.

    Preserves previously observed manual reviews across the initial explicit anchor.
    The executor runs the fixture unittest and reports its actual result.
    """
    repo = pathlib.Path(repo)
    if wid is None:
        wid = json.loads((repo / '.orchestrator/index.json').read_text())['active_work']
    work = json.loads((repo / f'.orchestrator/work/{wid}.json').read_text())
    provider = work['sdd']['provider']
    directory = {'bmad': '_bmad-output', 'openspec': 'openspec', 'speckit': 'specs'}[provider]
    def checked(*args):
        result = run(repo, *args, '--work', wid)
        assert result[0] == 0, result
        return result[1]
    if not work['sdd'].get('acceptance'):
        binding = work['sdd'].get('spec_binding') or {}
        spec = (binding.get('spec') or {}).get('path') or f'{directory}/fixture-acceptance.md'
        (repo / directory).mkdir(exist_ok=True)
        if not binding:
            (repo / spec).write_text('# Local fixture acceptance\n- The fixture unittest passes.\n', encoding='utf-8')
        script = 'fixture-final-acceptance.py'
        (repo / script).write_text('''import json, os, subprocess, sys
r = json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])
p = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests', '-v'], stdout=sys.stderr, stderr=sys.stderr)
print(json.dumps({'run_id': r['run_id'], 'input_sha256': r['input_sha256'], 'provider': r['provider'], 'items': [{'id': 'AC-FIXTURE', 'check': {'criterion_id': 'AC-FIXTURE', 'run_id': r['run_id'], 'selector': 'Acceptance.test_ok', 'assertion': 'fixture unittest succeeds', 'observed': 'actual fixture unittest execution'}, 'status': 'passed' if p.returncode == 0 else 'failed', 'evidence': [{'path': 'acceptance-tests/test_acceptance.py', 'reason': 'Actual fixture unittest execution'}]}]}))
sys.exit(p.returncode)
''', encoding='utf-8')
        argv = [sys.executable, script]
        command = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
        declaration = {'spec': spec, 'criteria': [{'id': 'AC-FIXTURE', 'source': spec, 'kind': 'requirement', 'description': 'Fixture unittest passes'}], 'executor': {'mode': 'command', 'entrypoint': command}}
        from scripts.acceptance_trace import criteria
        declaration['criteria'][0]['covers'] = [item['id'] for item in criteria(repo, work)]
        (repo / 'fixture-acceptance.json').write_text(json.dumps(declaration), encoding='utf-8')
        checked('sdd', 'acceptance', 'configure', '--declaration', 'fixture-acceptance.json')
        checked('reopen', '--reanchor', '--reason', 'Reviewed local fixture acceptance')
        for gate, record in work.get('gate_status', {}).items():
            if record.get('satisfied'):
                checked('gate', 'satisfy', '--gate', gate, '--evidence', record['evidence'])
    if prepare_only and work['sdd'].get('acceptance'):
        from scripts.acceptance_trace import criteria, coverage
        if coverage(repo, work)['missing']:
            declaration = work['sdd']['acceptance']['declaration']
            declaration['criteria'][0]['covers'] = [item['id'] for item in criteria(repo, work)]
            (repo / 'fixture-acceptance.json').write_text(json.dumps(declaration))
            checked('sdd', 'acceptance', 'configure', '--declaration', 'fixture-acceptance.json', '--replace')
            checked('reopen', '--reanchor', '--reason', 'Reviewed changed local fixture source mapping')
    if prepare_only:
        return
    checked('verify')
    checked('sdd', 'acceptance', 'run')


def bind_fixture_reviewer(repo, ticket, layer, model, reviewer_cli=None):
    """Exercise the real attach CLI in a newly registered independent process session."""
    import uuid
    session_id = 'fixture-reviewer-' + uuid.uuid4().hex
    env = {**os.environ, 'CAO_HOST': 'pi', 'PI_SESSION_ID': session_id, 'PI_MODEL': model}
    launcher = ([str(reviewer_cli)] if reviewer_cli else
                [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts/cao.py')])
    def invoke(*args, payload=None):
        result = subprocess.run([*launcher, *args, '--repo', str(repo)],
                                input=json.dumps(payload) if payload else None,
                                text=True, capture_output=True, env=env, timeout=30)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)
    invoke('host-event', '--host', 'pi', payload={'event': 'subagent_start',
           'session_id': 'fixture-parent', 'subagent_id': session_id,
           'prompt': __import__('scripts.review_observation', fromlist=['task_marker']).task_marker(ticket, layer)})
    out = invoke('sdd', 'review', 'attach', '--layer', layer, '--run', ticket['id'],
                 '--input-sha256', ticket['input_sha256'], '--model', model)
    return out['review']['execution_binding']['reviewer']


def fixture_native_review(repo, run, wid=None, reviewer_cli=None):
    """Recorded, independently executed local review fixture; not a real model review."""
    import tempfile
    repo = pathlib.Path(repo)
    source = repo / '_bmad/render/cao-fixture/step-oneshot.md'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('### Review\n#### Blind Hunter (`blind-hunter`)\nRun an independent fixture inspection.\n### Classify\nRecord each finding.\n### Commit\nCommit when Git is available.\n')
    args = ['sdd','review','begin','--restart','--layer','blind-hunter','--model','fixture-model','--instructions',source.relative_to(repo).as_posix()]
    if wid: args += ['--work',wid]
    rc,out = run(repo,*args);assert rc==0,out
    ticket=out['review'];directory=repo/'_bmad-output/review-fixture';directory.mkdir(parents=True,exist_ok=True)
    reviewer = bind_fixture_reviewer(repo, ticket, 'blind-hunter', 'fixture-model', reviewer_cli)
    with tempfile.TemporaryDirectory() as d:
        script=pathlib.Path(d)/'reviewer.py'
        script.write_text("from pathlib import Path\nimport sys\np=Path(sys.argv[1])\nassert p.is_dir()\nprint('- F1: Independently inspect the local fixture source and verification setup.')\n")
        findings=subprocess.check_output([sys.executable,str(script),str(repo)],text=True)
    raw=directory/'findings.md';raw.write_text(findings)
    publish_fixture_review(repo, reviewer, findings)
    report=directory/'report.json'
    report.write_text(json.dumps({'run_id':ticket['id'],'input_sha256':ticket['input_sha256'],
        'reviewer':reviewer,
        'findings_file':raw.relative_to(repo).as_posix(),'findings':[{'id':'finding-1','verdict':'false','disposition':'reject',
        'evidence':'A separate local Python process inspected the fixture directory; this fixture does not assert expert semantic review.'}]}))
    args=['sdd','review','submit','--layer','blind-hunter','--report',report.relative_to(repo).as_posix()]
    if wid:args+=['--work',wid]
    rc,out=run(repo,*args);assert rc==0,out
    return report


def provision_fixture_background(repo):
    """Explicit background for legacy workflow fixtures, scoped to their test harness.

    These fixtures test pre-existing workflow contracts; new project setup tests
    use the unmodified CLI directly and do not call this helper.
    """
    from scripts.project_context import commit_context, load_context
    from scripts.project_sources import observe_prompt, user_evidence
    from scripts.project_verification import verification_binding
    repo = pathlib.Path(repo)
    if load_context(repo):
        return
    (repo / 'acceptance-tests').mkdir(exist_ok=True)
    (repo / 'acceptance-tests/pyproject.toml').write_text('[project]\nname = \"existing-fixture\"\nversion = \"0.1.0\"\n')
    ref = observe_prompt(repo, 'Fixture uses Python, unittest and setuptools.', 'fixture', 'fixture-setup')
    values = {'language': 'Python', 'build_tool': 'setuptools', 'test_framework': 'unittest'}
    context = {'schema_version': 1, 'scopes': {'root': {'paths': ['acceptance-tests'],
               'technology': {key: {'value': value, 'status': 'accepted'} for key, value in values.items()}}},
               'evidence': [user_evidence(repo, ref, values)], 'active_changes': []}
    commit_context(repo, context, None)
    path = repo / '.orchestrator/config.json'
    cfg = json.loads(path.read_text())
    cfg.setdefault('verification', {})['context_binding'] = verification_binding(repo)
    cfg['verification']['plan'] = {'version': 1, 'scripts': [{'name': 'planned-fixture',
        'command': 'python -m unittest discover -s acceptance-tests', 'required': True, 'gates': ['tests']}]}
    path.write_text(json.dumps(cfg, ensure_ascii=False))


def publish_fixture_review(repo, reviewer, text):
    """Publish a local fixture reviewer's actual output via the real host event API."""
    env = {**os.environ, 'CAO_HOST': 'pi', 'PI_SESSION_ID': reviewer['session_id'], 'PI_MODEL': reviewer['model']}
    payload = {'event': 'turn_end', 'kind': 'subagent', 'subagent_id': reviewer['session_id'],
               'last_assistant_message': text}
    result = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts/cao.py'),
        'host-event', '--host', 'pi', '--repo', str(repo)], input=json.dumps(payload),
        text=True, capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
