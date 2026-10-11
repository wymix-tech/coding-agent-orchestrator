"""Native SDD workflow contract, execution gate, and Session fallback tests.

These cover the failure modes observed in a real session where an Agent recorded
`echo '...'` as a workflow entrypoint and its own Java source files as native SDD
outputs, and closed a `light` Work Item without any native planning/implementation
step.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import configure_verification, verify_via_config

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo, *args, payload=None, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(CAO), *args],
        cwd=repo,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        env=env,
        timeout=20,
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        output = {"raw": result.stdout}
    return result.returncode, output, result.stderr


def init_repo(repo, provider="bmad"):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
    if provider == "bmad":
        (repo / "_bmad").mkdir()
    elif provider == "openspec":
        (repo / "openspec").mkdir()
        (repo / "openspec" / "config.yaml").write_text("context: test\n", encoding="utf-8")
    (repo / "requirements").mkdir()
    (repo / "requirements" / "login.md").write_text("Implement login", encoding="utf-8")
    run(repo, "init")
    configure_verification(repo)
    run(repo, "sdd", "select", "--provider", provider)
    run(repo, "start", "--requirement", "requirements/login.md")


class WorkflowContractTests(unittest.TestCase):
    def test_placeholder_entrypoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature needing review")
            rc, out, _ = run(repo, "sdd", "workflow", "select", "--name", "implement", "--entrypoint", "echo 'Implementing ABAC permission management'")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "NATIVE_WORKFLOW_ENTRYPOINT_INVALID")

    def test_entrypoint_unrelated_to_provider_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature needing review")
            rc, out, _ = run(repo, "sdd", "workflow", "select", "--name", "implement", "--entrypoint", "openspec new change")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "NATIVE_WORKFLOW_ENTRYPOINT_INVALID")
            self.assertEqual(out["provider"], "bmad")

    def test_product_source_files_cannot_masquerade_as_sdd_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature needing review")
            run(repo, "sdd", "workflow", "select", "--name", "implement", "--entrypoint", "bmad workflow implement")
            run(repo, "sdd", "workflow", "execute", "--command", "bmad workflow implement")
            (repo / "src").mkdir()
            (repo / "src" / "App.java").write_text("class App {}", encoding="utf-8")
            rc, out, _ = run(repo, "sdd", "workflow", "complete", "--artifact", "src/App.java")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS")
            self.assertIn("src/App.java", out["offending"])

    def test_completion_requires_recorded_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature needing review")
            run(repo, "sdd", "workflow", "select", "--name", "implement", "--entrypoint", "bmad workflow implement")
            (repo / "_bmad-output").mkdir()
            (repo / "_bmad-output" / "spec.md").write_text("# spec", encoding="utf-8")
            rc, out, _ = run(repo, "sdd", "workflow", "complete", "--artifact", "_bmad-output/spec.md")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "NATIVE_WORKFLOW_EXECUTION_REQUIRED")
            rc, out, _ = run(repo, "sdd", "workflow", "execute", "--command", "bmad workflow implement", "--exit-code", "0")
            self.assertEqual(rc, 0)
            self.assertEqual(out["workflow"]["execution"]["command"], "bmad workflow implement")
            rc, out, _ = run(repo, "sdd", "workflow", "complete", "--artifact", "_bmad-output/spec.md")
            self.assertEqual(rc, 0)

    def test_allow_unverified_records_an_auditable_trace(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "comprehensive", "--reason", "High risk change")
            rc, out, _ = run(repo, "sdd", "workflow", "select", "--name", "legacy", "--entrypoint", "echo 'no native command'", "--allow-unverified", "--reason", "Provider exposes no CLI entrypoint")
            self.assertEqual(rc, 0)
            self.assertTrue(out["workflow"]["unverified"]["allowed"])
            (repo / "src").mkdir()
            (repo / "src" / "App.java").write_text("class App {}", encoding="utf-8")
            rc, out, _ = run(repo, "sdd", "workflow", "complete", "--artifact", "src/App.java", "--allow-unverified", "--reason", "Provider emits no file artifacts")
            self.assertEqual(rc, 0)
            rc, events, _ = run(repo, "lifecycle", "events")
            self.assertEqual(rc, 0)
            names = [event["event"] for event in events["events"]]
            self.assertIn("workflow.unverified_exception", names)

    def test_allow_unverified_requires_a_reason(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature needing review")
            rc, out, _ = run(repo, "sdd", "workflow", "select", "--name", "legacy", "--entrypoint", "echo 'x'", "--allow-unverified")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "UNVERIFIED_REASON_REQUIRED")


class LightPathContractTests(unittest.TestCase):
    def test_light_is_no_longer_exempt_from_the_native_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change")
            verify_via_config(repo, run)
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 2)
            self.assertEqual(out["reason"], "native SDD workflow is not recorded as completed")

    def test_light_completes_after_the_native_workflow_and_gates(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change")
            run(repo, "sdd", "workflow", "select", "--name", "quick-fix", "--entrypoint", "bmad quick-fix")
            run(repo, "sdd", "workflow", "execute", "--command", "bmad quick-fix")
            (repo / "_bmad-output").mkdir()
            (repo / "_bmad-output" / "fix.md").write_text("# fix", encoding="utf-8")
            run(repo, "sdd", "workflow", "complete", "--artifact", "_bmad-output/fix.md")
            verify_via_config(repo, run)
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 0, out)


class GateTests(unittest.TestCase):
    def test_gate_satisfy_rejects_unknown_gates_and_missing_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature needing review")
            rc, out, _ = run(repo, "gate", "satisfy", "--gate", "not_a_gate", "--evidence", "x")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "GATE_NOT_REQUIRED")
            rc, out, _ = run(repo, "gate", "satisfy", "--gate", "scope_review")
            self.assertEqual(rc, 2)
            rc, out, _ = run(repo, "gate", "satisfy", "--gate", "scope_review", "--evidence", "Reviewed the requirement scope")
            self.assertEqual(rc, 0)
            rc, out, _ = run(repo, "gate", "show")
            self.assertEqual(rc, 0)
            self.assertIn("scope_review", out["gates"]["satisfied"])
            self.assertIn("implementation_review", out["gates"]["pending"])

    def test_verification_gates_are_derived_not_manually_marked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change")
            rc, out, _ = run(repo, "gate", "satisfy", "--gate", "targeted_verification", "--evidence", "ran tests")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "GATE_IS_AUTO_DERIVED")


class SessionFallbackTests(unittest.TestCase):
    def sessions_in(self, repo):
        folder = repo / ".orchestrator" / "sessions"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []

    def test_bootstrap_and_start_attach_a_session_without_a_host_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
            (repo / "_bmad").mkdir()
            (repo / "requirements").mkdir()
            (repo / "requirements" / "login.md").write_text("Implement login", encoding="utf-8")
            env = {"CAO_HOST": "pi", "PI_SESSION_ID": "pi-session-1"}
            rc, out, _ = run(repo, "bootstrap", env_extra=env)
            self.assertEqual(rc, 0)
            self.assertIsNotNone(out.get("session"))
            self.assertEqual(out["session"]["host_session_id"], "pi-session-1")
            rc, out, _ = run(repo, "start", "--requirement", "requirements/login.md", env_extra=env)
            self.assertEqual(rc, 0)
            sessions = [s for s in self.sessions_in(repo) if s["host_session_id"] == "pi-session-1"]
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["host"], "pi")
            self.assertEqual(sessions[0]["work_item_id"], out["work_item"]["id"])

    def test_no_session_is_invented_when_the_host_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
            (repo / "_bmad").mkdir()
            (repo / "requirements").mkdir()
            (repo / "requirements" / "login.md").write_text("Implement login", encoding="utf-8")
            env = {"CAO_HOST": "", "PI_SESSION_ID": "", "CLAUDE_SESSION_ID": "", "CLAUDE_CODE_SESSION_ID": "", "CODEX_THREAD_ID": "", "PI_CODING_AGENT": "", "CLAUDE_CODE": "", "CODEX_SANDBOX": "", "CODEX_HOME": ""}
            run(repo, "bootstrap", env_extra=env)
            run(repo, "start", "--requirement", "requirements/login.md", env_extra=env)
            self.assertEqual(self.sessions_in(repo), [])


if __name__ == "__main__":
    unittest.main()
