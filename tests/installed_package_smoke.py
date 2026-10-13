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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cao", required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    env = dict(os.environ)
    for key in ("PYTHONPATH", "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT"):
        env.pop(key, None)
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    records = []
    with tempfile.TemporaryDirectory(prefix="cao-installed-smoke-") as td:
        base = Path(td)
        modules = ("import json; from scripts import cao, state_store, completion_policy, path_guard, verification_runner, verification_setup, windows_supervisor, dashboard, light_spec_policy, sdd_acceptance_runtime, sdd_acceptance_commands; "
                   "root=cao.package_root_dir(); "
                   "assert all((root/p).is_file() for p in ['hosts/pi/coding-orchestrator.ts','hooks/host-event.sh','hosts/claude-code/settings.fragment.json','hosts/codex/hooks.json','SKILL.md']); "
                   "assert dashboard.dashboard_html_path().is_file(); print(json.dumps({'runner':verification_runner.__file__,'invocation':cao.cao_invocation()}))")
        imported = subprocess.run([args.python, "-c", modules], cwd=base, env=env,
                                  text=True, capture_output=True, timeout=10)
        assert imported.returncode == 0, imported.stderr
        installation = json.loads(imported.stdout)
        for provider, marker, artifact in (("bmad", "_bmad", "_bmad-output/review.md"),
                                           ("openspec", "openspec", "openspec/changes/review.md"),
                                           ("speckit", ".specify", "specs/review.md")):
            for host in ("claude-code", "codex", "pi"):
                repo = base / f"{provider}-{host}"
                repo.mkdir()
                (repo / marker).mkdir()
                def cli(*argv, payload=None):
                    result = subprocess.run([args.cao, *argv, "--repo", str(repo)], cwd=repo, env=env,
                                            text=True, capture_output=True, timeout=30,
                                            input=json.dumps(payload) if payload is not None else None)
                    if result.returncode:
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
                (tests / "test_acceptance.py").write_text("import unittest\nclass Acceptance(unittest.TestCase):\n    def test_pass(self): self.assertEqual(2+2,4)\n", encoding="utf-8")
                cli("init", "--host", "codex", "--sdd", provider)
                assert cli("bootstrap")["mode"] == "select"
                setup_context = event("prompt_submit")
                context = setup_context.get("additional_context") or setup_context.get("hookSpecificOutput", {}).get("additionalContext", "")
                assert "start --requirement" in context, setup_context
                assert decision(event("pre_tool", tool_name="write", tool_input={"path": "product.py", "content": "pass"})) == "deny"
                for path in (".orchestrator/requirements/intake.md", "_bmad-output/spec.md", "openspec/proposal.md", "specs/spec.md", ".specify/spec.md"):
                    assert decision(event("pre_tool", tool_name="write", tool_input={"path": path, "content": "scope"})) == "allow"
                # Test the command advertised to the Agent, including wheel `sh` fallback;
                # an explicit console outside PATH is not the discovered invocation.
                readonly = event("pre_tool", tool_name="bash", tool_input={"command": installation["invocation"]["command"] + " status"})
                assert decision(readonly) == "allow", readonly
                assert not readonly.get("additional_context") and not readonly.get("hookSpecificOutput", {}).get("additionalContext"), readonly
                allocated = cli("requirement", "next-id", "--title", "Installed fixture")
                (repo / allocated["path"]).write_text("# Local fixture acceptance\n", encoding="utf-8")
                wid = cli("start", "--requirement", allocated["path"])["work_item"]["id"]
                assert cli("bootstrap")["mode"] == "verification_setup"
                configured = cli("verification", "configure", "--test-script",
                                 shell([args.python, "-m", "unittest", "discover", "-s", "acceptance-tests"]))
                assert configured["verification_setup"]["ready"], configured
                cli("reopen", "--work", wid, "--reanchor", "--reason", "Reviewed initial verification baseline")
                event("session_start")
                reminder = event("pre_tool", tool_name="write", tool_input={"path": "product.py", "content": "pass"})
                assert decision(reminder) == "allow", reminder
                assert reminder.get("additional_context") or reminder.get("hookSpecificOutput", {}).get("additionalContext"), reminder
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
                cli("verify")
                cli("done")
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
                    "print(json.dumps({'run_id':r['run_id'],'input_sha256':r['input_sha256'],'provider':r['provider'],'items':[{'id':'AC-1','status':'passed' if rc==0 else 'failed','evidence':[{'path':'acceptance-tests/test_acceptance.py','reason':'Actual fixture unittest execution'}]}]}))\nsys.exit(rc)\n", encoding="utf-8")
                declaration = {"spec": artifact, "criteria": [{"id": "AC-1", "source": artifact, "kind": "requirement", "description": "Fixture unittest passes"}],
                    "executor": {"mode": "command", "entrypoint": shell([args.python, executor.name])}}
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
                    oneshot.write_text("---\nroute: 'oneshot'\nstatus: 'done'\n---\n## Intent\nThe explicit fixture test passes.\n## Implementation Notes\nFixture execution.\n", encoding="utf-8")
                    before = oneshot.read_bytes()
                    bound = cli("sdd", "spec", "bind", "--spec", "_bmad-output/spec-oneshot.md")
                    assert bound["binding"]["mode"] == "bmad_oneshot", bound
                    cli("verify")
                    cli("done")
                    assert oneshot.read_bytes() == before
                    records.append({"provider_fixture": provider, "simulated_host": host, "profile": "light-oneshot", "status": "completed", "work": native_id})
        print(json.dumps({"installed_runner": installation["runner"], "resolved_invocation": installation["invocation"],
                          "fixture_cases": records}, indent=2))


if __name__ == "__main__":
    main()
