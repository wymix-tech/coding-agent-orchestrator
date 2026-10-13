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


def complete_acceptance(repo, run, wid=None):
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
        spec = f'{directory}/fixture-acceptance.md'
        (repo / directory).mkdir(exist_ok=True)
        (repo / spec).write_text('# Local fixture acceptance\n- The fixture unittest passes.\n', encoding='utf-8')
        script = 'fixture-final-acceptance.py'
        (repo / script).write_text('''import json, os, subprocess, sys
r = json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])
p = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests', '-v'], stdout=sys.stderr, stderr=sys.stderr)
print(json.dumps({'run_id': r['run_id'], 'input_sha256': r['input_sha256'], 'provider': r['provider'], 'items': [{'id': 'AC-FIXTURE', 'status': 'passed' if p.returncode == 0 else 'failed', 'evidence': [{'path': 'acceptance-tests/test_acceptance.py', 'reason': 'Actual fixture unittest execution'}]}]}))
sys.exit(p.returncode)
''', encoding='utf-8')
        argv = [sys.executable, script]
        command = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
        declaration = {'spec': spec, 'criteria': [{'id': 'AC-FIXTURE', 'source': spec, 'kind': 'requirement', 'description': 'Fixture unittest passes'}], 'executor': {'mode': 'command', 'entrypoint': command}}
        (repo / 'fixture-acceptance.json').write_text(json.dumps(declaration), encoding='utf-8')
        checked('sdd', 'acceptance', 'configure', '--declaration', 'fixture-acceptance.json')
        checked('reopen', '--reanchor', '--reason', 'Reviewed local fixture acceptance')
        for gate, record in work.get('gate_status', {}).items():
            if record.get('satisfied'):
                checked('gate', 'satisfy', '--gate', gate, '--evidence', record['evidence'])
    checked('verify')
    checked('sdd', 'acceptance', 'run')
