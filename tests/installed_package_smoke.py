"""Outside-checkout CLI smoke with explicitly local SDD fixtures and simulated host events.

This does not launch a real Claude Code/Codex/Pi host or install a real SDD runtime.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.helpers import fixture_native_review, fixture_native_review_source, complete_acceptance, provision_bmad


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cao", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--native-only", action="store_true", help="Run installed contract-2 native lifecycle scenarios without the legacy executor matrix")
    args = parser.parse_args()
    env = dict(os.environ)
    for key in ("PYTHONPATH", "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT"):
        env.pop(key, None)
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    records = []
    with tempfile.TemporaryDirectory(prefix="cao-installed-smoke-") as td:
        base = Path(td)
        modules = ("import json, importlib, pkgutil; import scripts; "
                   "[importlib.import_module(item.name) for item in pkgutil.iter_modules(scripts.__path__, 'scripts.') if item.name.startswith('scripts.cao_')]; "
                   "from scripts import cao, state_store, completion_policy, path_guard, verification_runner, verification_setup, windows_supervisor, dashboard, light_spec_policy, sdd_acceptance_runtime, sdd_acceptance_commands, native_route, native_progress, route_inputs, route_input_commands, conformance, project_knowledge, knowledge_commands, review_dispatch, review_diagnostics, spec_intent, project_setup, setup_identity, setup_rules, native_contracts, native_metadata, preparation_result, review_changes, review_observation, route_repair, verification_probe; "
                   "root=cao.package_root_dir(); "
                   "from scripts.skill_guidance import STAGE_FILES, PROVIDER_FILES, CONTRACT_FILES; "
                   "assert all((root/p).is_file() for p in {*STAGE_FILES.values(), *PROVIDER_FILES.values(), *CONTRACT_FILES.values()}); "
                   "assert all((root/p).is_file() for p in ['references/runtime-records.md','protocol/runtime-event.schema.json','protocol/runtime-run.schema.json','hosts/pi/coding-orchestrator.ts','hosts/pi/internal/failure-policy.ts','hosts/pi/internal/context-presentation.ts','hooks/host-event.sh','hosts/claude-code/settings.fragment.json','hosts/codex/hooks.json','SKILL.md']); "
                   "assert not (root/'docs').exists(), 'Runtime installation must not contain developer docs'; "
                   "assert cao.overall_verification_status([{'required':False,'status':'failed'}]) == 'blocked'; "
                   "assert dashboard.dashboard_html_path().is_file(); print(json.dumps({'runner':verification_runner.__file__,'invocation':cao.cao_invocation()}))")
        imported = subprocess.run([args.python, "-c", modules], cwd=base, env=env,
                                  text=True, capture_output=True, timeout=10)
        assert imported.returncode == 0, imported.stderr
        installation = json.loads(imported.stdout)
        for host in ('claude-code', 'codex', 'pi'):
            repo = base / ('empty-' + host)
            (repo / 'openspec').mkdir(parents=True)
            (repo / 'openspec/config.yaml').write_text('context: installed fixture\n')
            def setup_cli(*argv, payload=None):
                result = subprocess.run([args.cao, *argv, '--repo', str(repo)], cwd=repo, env=env,
                                        text=True, capture_output=True, timeout=60,
                                        input=json.dumps(payload) if payload is not None else None)
                assert result.returncode == 0, (argv, result.stdout, result.stderr)
                return json.loads(result.stdout)
            setup_cli('init')
            setup_cli('host-event', '--host', host, payload={'event': 'prompt_submit', 'session_id': 'installed-setup',
                'prompt': 'Initialize sample-dist with import sample_import. Use Python setuptools unittest.'})
            inspection = setup_cli('project', 'inspect')
            evidence = inspection['user_evidence'][-1]['id']
            setup_cli('project', 'context', 'set', '--language', 'Python', '--build-tool', 'setuptools',
                      '--test-framework', 'unittest', '--source', 'user-specified', '--evidence', evidence)
            setup_cli('project', 'setup', 'draft', '--rule', 'python-unittest', '--identity',
                      '{"name":"sample-dist","import_name":"sample_import"}', '--evidence', evidence)
            setup_cli('project', 'setup', 'plan')
            setup_cli('project', 'setup', 'apply')
            assert setup_cli('project', 'setup', 'check')['status'] == 'passed'
            assert setup_cli('project', 'setup', 'status')['ready']
            assert not list((repo / '.orchestrator/work').glob('*.json'))
            (repo / 'openspec/feature.md').write_text('# Feature\n## Acceptance\n- The new operation works.\n')
            work = setup_cli('start', '--requirement', 'openspec/feature.md')['work_item']
            assert work['status'] == 'active'
            assert setup_cli('verification', 'check', '--work', work['id'])['status'] == 'passed'
            setup_cli('path', 'assess', '--profile', 'light', '--reason', 'Installed native evidence fixture')
            setup_cli('sdd', 'spec', 'bind', '--spec', 'openspec/feature.md', '--criteria-section', 'Acceptance')
            report = repo / 'openspec/native-completion.md'
            # Actual local fixture tests, not a real model/provider execution.
            check = subprocess.run([args.python, '-m', 'unittest', 'discover', '-s', 'tests'],
                                   cwd=repo, env=env, capture_output=True, text=True, timeout=30)
            assert check.returncode == 0, check.stderr
            report.write_text('# Native fixture result\n\n' + check.stdout + check.stderr)
            setup_cli('lifecycle', 'bind', '--report', 'openspec/native-completion.md')
            setup_cli('conformance', 'check')
            setup_cli('verify')
            assert setup_cli('done')['status'] == 'completed'
            records.append({'provider_fixture': 'openspec', 'simulated_host': host,
                            'profile': 'empty-project-native-lifecycle', 'status': 'completed-with-native-report', 'work': work['id']})
        if args.native_only:
            print(json.dumps({"ok": True, "scope": "native-lifecycle", "records": records}, ensure_ascii=False, indent=2))
            return
        for provider, marker, artifact in (("bmad", "_bmad", "_bmad-output/review.md"),
                                           ("openspec", "openspec", "openspec/changes/review.md"),
                                           ("speckit", ".specify", "specs/review.md")):
            for host in ("claude-code", "codex", "pi"):
                repo = base / f"{provider}-{host}"
                repo.mkdir()
                (repo / marker).mkdir()
                if provider == 'bmad':
                    provision_bmad(repo)
                def cli(*argv, payload=None, expected=0):
                    result = subprocess.run([args.cao, *argv, "--repo", str(repo)], cwd=repo, env=env,
                                            text=True, capture_output=True, timeout=30,
                                            input=json.dumps(payload) if payload is not None else None)
                    if result.returncode != expected:
                        raise AssertionError((argv, result.returncode, result.stdout, result.stderr))
                    return json.loads(result.stdout)
                def event(name, **fields):
                    return cli("host-event", "--host", host, payload={"event": name, "session_id": "smoke", **fields})
                def decision(out):
                    return out.get("decision") or out["hookSpecificOutput"]["permissionDecision"]
                def shell(argv):
                    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
                tests = repo / "acceptance-tests"
                tests.mkdir()
                # This matrix exercises an existing product. Empty-project
                # initialization has its own public-CLI load/test regression.
                (repo / 'pyproject.toml').write_text('[project]\nname = "installed-fixture"\nversion = "0.1.0"\n')
                (tests / "test_acceptance.py").write_text("import unittest\nclass Acceptance(unittest.TestCase):\n    def test_pass(self): self.assertEqual(2+2,4)\n", encoding="utf-8")
                cli("init", "--host", "codex", "--sdd", provider)
                # The retained executor matrix explicitly exercises contract 1.
                config_path = repo / '.orchestrator/config.json'
                config = json.loads(config_path.read_text())
                config['lifecycle_contract'] = 1
                config_path.write_text(json.dumps(config))
                assert cli('bootstrap')['mode'] == 'project_context_setup'
                event('prompt_submit', prompt='Fixture uses Python, setuptools and unittest.')
                evidence = cli('project', 'inspect')['user_evidence'][0]['id']
                cli('project', 'context', 'set', '--language', 'Python', '--build-tool', 'setuptools',
                    '--test-framework', 'unittest', '--source', 'user-specified', '--evidence', evidence)
                cli('verification', 'configure', '--plan-only', '--test-script',
                    shell([args.python, '-m', 'unittest', 'discover', '-s', 'acceptance-tests']))
                bootstrap = cli("bootstrap")
                assert bootstrap["mode"] == "select"
                assert bootstrap["guidance"]["ready"]
                assert Path(bootstrap["guidance"]["skill"]).is_file()
                assert all(Path(p).is_file() for p in bootstrap["guidance"]["read_before_action"])
                assert cli("next")["guidance"]["read_before_action"] == bootstrap["guidance"]["read_before_action"]
                assert cli("instructions", "update", "--host", "codex")["files"][0]["status"] == "unchanged"
                setup_context = event("prompt_submit")
                context = setup_context.get("additional_context") or setup_context.get("hookSpecificOutput", {}).get("additionalContext", "")
                assert "start --requirement" in context, setup_context
                assert decision(event("pre_tool", tool_name="write", tool_input={"path": "product.py", "content": "pass"})) == "deny"
                native_cwd = repo / artifact.split('/')[0]; native_cwd.mkdir(exist_ok=True)
                assert decision(event("pre_tool", tool_name="exec_command", tool_input={"cmd": "./unknown.sh", "workdir": str(native_cwd)})) == "deny"
                assert decision(event("pre_tool", tool_name="exec_command", tool_input={"cmd": "touch $DESTINATION", "workdir": str(native_cwd)})) == "deny"
                for path in (".orchestrator/requirements/intake.md", "_bmad-output/spec.md", "openspec/proposal.md", "specs/spec.md", ".specify/spec.md"):
                    assert decision(event("pre_tool", tool_name="write", tool_input={"path": path, "content": "scope"})) == "allow"
                # Test the command advertised to the Agent, including wheel `sh` fallback;
                # an explicit console outside PATH is not the discovered invocation.
                readonly = event("pre_tool", tool_name="bash", tool_input={"command": installation["invocation"]["command"] + " status"})
                assert decision(readonly) == "allow", readonly
                assert not readonly.get("additional_context") and not readonly.get("hookSpecificOutput", {}).get("additionalContext"), readonly
                allocated = cli("requirement", "next-id", "--title", "Installed fixture")
                (repo / allocated["path"]).write_text("# Local fixture acceptance\nExpected fixture output exists.\n", encoding="utf-8")
                wid = cli("start", "--requirement", allocated["path"])["work_item"]["id"]
                assert cli("bootstrap")["mode"] == "verification_setup"
                configured = cli("verification", "configure", "--test-script",
                                 shell([args.python, "-m", "unittest", "discover", "-s", "acceptance-tests"]))
                assert configured["verification_setup"]["ready"], configured
                assert configured["initial_baseline_established"], configured
                assert not configured["acceptance_review_required"], configured
                event("session_start")
                reminder = event("pre_tool", tool_name="write", tool_input={"path": "product.py", "content": "pass"})
                assert decision(reminder) == "deny", reminder
                assert reminder.get("reason") or reminder.get("hookSpecificOutput", {}).get("permissionDecisionReason"), reminder
                assert decision(event("pre_tool", tool_name="write", tool_input={"path": ".orchestrator/index.json", "content": "{}"})) == "deny"
                for _ in range(2):
                    event("turn_end")
                    session = next(iter((repo / ".orchestrator/sessions").glob("*.json")))
                    assert json.loads(session.read_text(encoding="utf-8"))["status"] == "active"
                    event("prompt_submit")
                event("session_end", reason="fixture-exit")
                assert json.loads(session.read_text(encoding="utf-8"))["status"] == "ended"
                cli("path", "assess", "--profile", "light", "--reason", "bounded local fixture")
                native = repo / f"{provider}-fixture.py"
                native.write_text(f"from pathlib import Path\np=Path({artifact!r})\np.parent.mkdir(parents=True,exist_ok=True)\np.write_text('Local fixture output\\n## Acceptance\\n- Expected fixture output exists.\\n',encoding='utf-8')\n", encoding="utf-8")
                command = shell([args.python, native.name])
                cli("sdd", "workflow", "select", "--name", "local-fixture", "--entrypoint", command)
                subprocess.run([args.python, str(native)], cwd=repo, env=env, timeout=10, check=True)
                cli("sdd", "workflow", "execute", "--command", command, "--exit-code", "0")
                cli("sdd", "workflow", "complete", "--artifact", artifact, "--no-plan")
                cli("sdd", "spec", "bind", "--spec", artifact, "--criteria-section", "Acceptance")
                assert decision(event("pre_tool", tool_name="exec_command", tool_input={"cmd": "sed -n 'w .orchestrator/config.json' input.txt"})) == "deny"
                subdir = repo / "sub"; subdir.mkdir()
                assert decision(event("pre_tool", tool_name="exec_command", tool_input={"cmd": "printf x > ../.orchestrator/config.json", "workdir": str(subdir)})) == "deny"
                complete_acceptance(repo, lambda _repo, *argv: (0, cli(*argv)), wid)
                verified = cli("verify")
                assert verified["verification"]["snapshot_format"] == 4
                assert verified["verification"]["run_id"]
                runtime_run = verified["verification"]["run_id"]
                assert cli("runtime", "show", "--run", runtime_run)["admission_status"] == "passed"
                assert cli("runtime", "events")["items"]
                assert cli("runtime", "recover", "--check")["check_only"]
                assert cli("runtime", "logs", "--run", runtime_run, "--command", "command-0001", "--stream", "stderr")["metadata"]
                cli("runtime", "rebuild")
                assert cli("runtime", "prune", "--dry-run")["dry_run"]
                cli("done")
                before_completed = (repo / f".orchestrator/work/{wid}.json").read_bytes()
                denied = subprocess.run([args.cao, "verify", "--work", wid, "--repo", str(repo)], cwd=repo, env=env, capture_output=True, text=True, timeout=30)
                assert denied.returncode == 2 and json.loads(denied.stdout)["error"] == "VERIFICATION_WORK_NOT_ACTIVE"
                assert (repo / f".orchestrator/work/{wid}.json").read_bytes() == before_completed
                work = json.loads((repo / f".orchestrator/work/{wid}.json").read_text(encoding="utf-8"))
                assert work["status"] == "completed"
                records.append({"provider_fixture": provider, "simulated_host": host, "status": work["status"], "profile": "light"})
                # Full profile uses the installed acceptance CLI and a real fixture executor.
                req = cli("requirement", "next-id", "--title", "Full profile installed fixture")
                (repo / req["path"]).write_text("# Full fixture\nVerify the explicit fixture behavior.\n", encoding="utf-8")
                full_id = cli("start", "--requirement", req["path"])["work_item"]["id"]
                cli("path", "assess", "--profile", "standard", "--reason", "full fixture acceptance")
                cli("sdd", "workflow", "select", "--name", "local-fixture", "--entrypoint", command)
                subprocess.run([args.python, str(native)], cwd=repo, env=env, timeout=10, check=True)
                cli("sdd", "workflow", "execute", "--command", command)
                cli("sdd", "workflow", "complete", "--artifact", artifact, "--no-plan")
                executor = repo / "fixture-final.py"
                executor.write_text("import json,os,subprocess,sys\nr=json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])\n"
                    "rc=subprocess.run([sys.executable,'-m','unittest','discover','-s','acceptance-tests','-v'],stdout=sys.stderr,stderr=sys.stderr).returncode\n"
                    "print(json.dumps({'run_id':r['run_id'],'input_sha256':r['input_sha256'],'provider':r['provider'],'items':[{'id':'AC-1','check':{'criterion_id':'AC-1','run_id':r['run_id'],'selector':'test_acceptance.Acceptance.test_pass','assertion':'2+2 equals 4','observed':'unittest exit '+str(rc)},'status':'passed' if rc==0 else 'failed','evidence':[{'path':'acceptance-tests/test_acceptance.py','reason':'Actual fixture unittest execution'}]}]}))\nsys.exit(rc)\n", encoding="utf-8")
                declaration = {"spec": artifact, "criteria": [{"id": "AC-1", "source": artifact, "kind": "requirement", "description": "Fixture unittest passes"}],
                    "executor": {"mode": "command", "entrypoint": shell([args.python, executor.name])}}
                declaration["criteria"][0]["covers"] = [item["id"] for item in cli("acceptance", "criteria")["criteria"]]
                (repo / "fixture-declaration.json").write_text(json.dumps(declaration), encoding="utf-8")
                cli("sdd", "acceptance", "configure", "--declaration", "fixture-declaration.json")
                cli("reopen", "--reanchor", "--reason", "Reviewed native fixture acceptance")
                for gate in ("scope_review", "implementation_review"):
                    cli("gate", "satisfy", "--gate", gate, "--evidence", "Explicit local fixture review")
                cli("verify")
                cli("sdd", "acceptance", "run")
                assert "sdd_acceptance" in cli("gate", "show")["gates"]["satisfied"]
                cli("done")
                records.append({"provider_fixture": provider, "simulated_host": host, "profile": "standard", "status": "completed", "work": full_id})
                if provider == "bmad":
                    req = cli("requirement", "next-id", "--title", "Installed oneshot fixture")
                    (repo / req["path"]).write_text("# Fixture intent\nThe existing fixture check passes.\n", encoding="utf-8")
                    native_id = cli("start", "--requirement", req["path"])["work_item"]["id"]
                    cli("path", "assess", "--profile", "light", "--reason", "bounded native oneshot")
                    oneshot = repo / "_bmad-output/spec-oneshot.md"
                    oneshot.write_text("---\nroute: 'oneshot'\nstatus: 'done'\n---\n## Intent\n<frozen-after-approval>\nThe explicit fixture test passes.\n</frozen-after-approval>\n## Implementation Notes\nFixture execution.\n", encoding="utf-8")
                    before = oneshot.read_bytes()
                    bound = cli("sdd", "spec", "bind", "--spec", "_bmad-output/spec-oneshot.md")
                    assert bound["binding"]["mode"] == "bmad_oneshot", bound
                    # Installed route input generation must work without the
                    # checkout, while native review/Commit obligations remain.
                    skill = repo / '.agents/skills/bmad-build/SKILL.md'
                    skill.parent.mkdir(parents=True, exist_ok=True)
                    skill.write_text('---\nname: bmad-build\ndescription: Explicit local fixture\n---\nUse the native oneshot route for this fixture.\n', encoding='utf-8')
                    provision_bmad(repo)
                    rendered_review = fixture_native_review_source(repo)
                    native_rule = rendered_review.parent / 'rule.md'
                    native_rule.write_text('Use the native oneshot route for this fixture.\n')
                    (rendered_review.parent / 'step-03-implement.md').write_text('Native implementation fixture.\n')
                    cli('sdd', 'artifact', 'bind', '--artifact', '_bmad-output/spec-oneshot.md')
                    prepared = cli('sdd', 'route', 'prepare')
                    route_path = prepared['declaration_path']
                    assert route_path == f'.orchestrator/inputs/{native_id}/route.json'
                    draft = prepared['declaration']
                    draft.update(provider_version='local-fixture', route='direct-build', reason='Installed native fixture rule.',
                                 omitted=[{'name':'separate planning', 'reason':'Bounded explicit fixture intent.', 'replaced_by':'_bmad-output/spec-oneshot.md'}])
                    (repo / route_path).write_text(json.dumps(draft), encoding='utf-8')
                    cli('sdd', 'route', 'rule', '--source', native_rule.relative_to(repo).as_posix(), '--start-line', '1', '--end-line', '1')
                    assert cli('sdd', 'route', 'check')['status'] == 'validated'
                    cli('sdd', 'route', 'propose', '--declaration', route_path)
                    sprint = repo / '_bmad-output/sprint-status.yml'
                    sprint.write_text('development_status:\n  epic-1: done\n  1-1-existing: done\n')
                    cli('sdd', 'plan', 'bind', '--plan-item', '_bmad-output/sprint-status.yml#epic-1')
                    sprint.write_text(sprint.read_text()+'  1-2-added: backlog\n')
                    assert cli('verify', expected=2)['error'] == 'BMAD_PLAN_NOT_DONE'
                    sprint.write_text('development_status:\n  epic-1: done\n  1-1-existing: done\n')
                    assert cli('verify', expected=2)['error'] == 'PLAN_ITEM_MISSING'
                    sprint.write_text(sprint.read_text()+'  1-2-added: done\n')
                    def git(*argv):
                        subprocess.run(['git', '-C', str(repo), *argv], check=True, capture_output=True, env=env)
                    git('init', '-q')
                    excludes = repo / '.git/info/exclude'
                    excludes.write_text('.orchestrator/\n_bmad-output/\n__pycache__/\n*.py[cod]\n')
                    complete_acceptance(repo, lambda _repo, *argv: (0, cli(*argv)), native_id, prepare_only=True)
                    fixture_native_review(repo, lambda _repo, *argv: (0, cli(*argv)), native_id, reviewer_cli=args.cao)
                    complete_acceptance(repo, lambda _repo, *argv: (0, cli(*argv)), native_id)
                    cli("verify")
                    git('add', '-A'); git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Installed business fixture')
                    ignored_result = cli('done', expected=2)
                    assert ignored_result['reason'] == 'NATIVE_SPEC_IGNORED', ignored_result
                    excludes.write_text('.orchestrator/\n__pycache__/\n*.py[cod]\n')
                    git('add', '--', '_bmad-output/spec-oneshot.md')
                    head_result = cli('done', expected=2)
                    assert head_result['reason'] == 'NATIVE_SPEC_NOT_IN_HEAD', head_result
                    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Installed SPEC fixture')
                    cli("done")
                    assert oneshot.read_bytes() == before
                    records.append({"provider_fixture": provider, "simulated_host": host, "profile": "light-oneshot", "status": "completed", "work": native_id})
        print(json.dumps({"installed_runner": installation["runner"], "resolved_invocation": installation["invocation"],
                          "fixture_cases": records}, indent=2))


if __name__ == "__main__":
    main()
