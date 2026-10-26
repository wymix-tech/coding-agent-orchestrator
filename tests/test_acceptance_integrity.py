"""Accepted standards are content-bound, not made valid by editing timestamps."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import configure_verification, complete_acceptance

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts/cao.py"
HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args, stdin=None):
    result = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True,
                            capture_output=True, env=dict(os.environ, **NO_HOST_ENV),
                            timeout=60, input=stdin)
    try:
        if args and args[0] == "init" and result.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return result.returncode, json.loads(result.stdout)
    except json.JSONDecodeError:
        if args and args[0] == "init" and result.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return result.returncode, {"raw": result.stdout, "stderr": result.stderr}


def active_work(repo, command=None):
    run(repo, "init", "--host", "pi")
    configure_verification(repo, command)
    run(repo, "sdd", "select", "--provider", "bmad")
    (repo / "_bmad").mkdir(exist_ok=True)
    from tests.helpers import provision_bmad
    provision_bmad(repo)
    rc, out = run(repo, "requirement", "next-id", "--title", "login")
    req = repo / out["path"]
    # This suite exercises baseline/reanchor integrity, not authentication risk.
    req.write_text("# Fixture\n\nGenerate a bounded local status report.\n", encoding="utf-8")
    rc, out = run(repo, "start", "--requirement", out["path"])
    assert rc == 0, out
    return out["work_item"]["id"]


def standard_path(repo, wid):
    rc, out = run(repo, "path", "assess", "--profile", "standard", "--reason", "auth system", "--work", wid)
    assert rc == 0, out
    run(repo, "sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build", "--work", wid)
    artifact = repo / "_bmad-output/implementation_artifacts/story.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# story\n", encoding="utf-8")
    run(repo, "sdd", "workflow", "execute", "--command", "bmad-build", "--exit-code", "0", "--work", wid)
    rc, out = run(repo, "sdd", "workflow", "complete", "--artifact",
                  "_bmad-output/implementation_artifacts/story.md", "--work", wid, "--no-plan")
    assert rc == 0, out


def change_test_standard(repo):
    # A real test command with a changed option; no forged configured_at.
    path = repo / ".orchestrator/config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["verification"]["scripts"][0]["command"] += " --buffer"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    rc, out = run(repo, 'verification', 'check', '--project')
    assert rc == 0, out


def read_work(repo, wid):
    return json.loads((repo / f".orchestrator/work/{wid}.json").read_text(encoding="utf-8"))


class StandardIsFixedBeforeStart(unittest.TestCase):
    def test_verify_refuses_a_standard_configured_after_start(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            configure_verification(repo, "true")  # the demo11 late-standard move
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "VERIFICATION_STANDARD_CHANGED")

    def test_standard_that_predates_the_work_is_still_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            run(repo, "path", "assess", "--profile", "light", "--reason", "docs change", "--work", wid)
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 0, out)
            self.assertIn("Ran 1 test", out["verification"]["results"][0]["stderr_tail"])


class StandardMustCoverRequiredGates(unittest.TestCase):
    def test_verify_refuses_build_only_standard_when_tests_gate_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo, "python -m compileall acceptance-tests")
            standard_path(repo, wid)
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "VERIFICATION_GATE_UNBOUND")

    def test_a_real_test_command_satisfies_the_tests_gate_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 0, out)
            self.assertIn("Ran 1 test", out["verification"]["results"][0]["stderr_tail"])


class DoneCannotUseACliChosenStandard(unittest.TestCase):
    def test_done_rejects_a_verification_run_with_cli_override(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            rc, out = run(repo, "verify", "--work", wid, "--command", "true")
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["verification"]["source"], "cli")
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "COMPLETION_BLOCKED")


class Reopen(unittest.TestCase):
    def provisioned_completed(self, repo):
        wid = active_work(repo)
        standard_path(repo, wid)
        rc, out = run(repo, "verify", "--work", wid)
        self.assertEqual(rc, 0, out)
        for gate in ("scope_review", "implementation_review"):
            run(repo, "gate", "satisfy", "--gate", gate, "--evidence", "observed and reviewed", "--work", wid)
        complete_acceptance(repo, run, wid)
        rc, out = run(repo, "done", "--work", wid)
        self.assertEqual(rc, 0, out)
        return wid

    def test_reopen_returns_a_completed_item_to_active_and_clears_self_earned_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = self.provisioned_completed(repo)
            rc, out = run(repo, "reopen", "--work", wid, "--reason", "only story 1.1 was implemented")
            self.assertEqual(rc, 0, out)
            work = read_work(repo, wid)
            self.assertEqual(work["status"], "active")
            self.assertIsNone(work.get("completed_at"))
            self.assertEqual(work.get("gate_status"), {})
            self.assertEqual(work.get("verification"), {})
            self.assertIn("reopened", work["execution"]["step"])

    def test_reopen_refuses_work_that_is_not_completed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            rc, out = run(repo, "reopen", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "WORK_ITEM_NOT_COMPLETED")

    def test_reopened_item_can_be_completed_again_with_real_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = self.provisioned_completed(repo)
            run(repo, "reopen", "--work", wid, "--reason", "incomplete")
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 0, out)
            for gate in ("scope_review", "implementation_review"):
                run(repo, "gate", "satisfy", "--gate", gate, "--evidence", "re-reviewed", "--work", wid)
            complete_acceptance(repo, run, wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)


class Reanchor(unittest.TestCase):
    """Explicit acceptance of changed standards does not depend on advancing clocks."""
    def verification_stuck_work(self, repo):
        wid = active_work(repo)
        standard_path(repo, wid)
        change_test_standard(repo)
        rc, out = run(repo, "verify", "--work", wid)
        self.assertEqual(out["error"], "VERIFICATION_STANDARD_CHANGED", out)
        return wid

    def test_reanchor_lets_a_stuck_active_item_verify_again(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = self.verification_stuck_work(repo)
            before = read_work(repo, wid)["acceptance"]["config_sha256"]
            rc, out = run(repo, "reopen", "--work", wid, "--reason", "changed standard", "--reanchor")
            self.assertEqual(rc, 0, out)
            self.assertTrue(out["reanchored"])
            self.assertNotEqual(read_work(repo, wid)["acceptance"]["config_sha256"], before)
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["verification"]["status"], "passed")

    def test_reanchor_clears_evidence_earned_under_the_invalid_standard(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            for gate in ("scope_review", "implementation_review"):
                run(repo, "gate", "satisfy", "--gate", gate, "--evidence", "reviewed", "--work", wid)
            change_test_standard(repo)
            rc, out = run(repo, "reopen", "--work", wid, "--reason", "re-anchoring", "--reanchor")
            self.assertEqual(rc, 0, out)
            self.assertEqual(read_work(repo, wid).get("gate_status"), {})
            self.assertEqual(read_work(repo, wid).get("verification"), {})

    def test_reanchor_still_does_not_exempt_a_standard_that_never_runs_tests(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            configure_verification(repo, "python3 -c 'pass'")
            rc, out = run(repo, "reopen", "--work", wid, "--reason", "new standard", "--reanchor")
            self.assertEqual(rc, 0, out)
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "VERIFICATION_GATE_UNBOUND")

    def test_reanchor_records_itself_in_the_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = self.verification_stuck_work(repo)
            run(repo, "reopen", "--work", wid, "--reason", "re-anchored", "--reanchor")
            events = [json.loads(line) for line in (repo / ".orchestrator/lifecycle/events.jsonl").read_text(encoding="utf-8").splitlines()]
            reopens = [event for event in events if event.get("event") == "verification.baseline.reanchored"]
            self.assertTrue(reopens)
            self.assertTrue(reopens[-1]["reanchored"])

    def test_reopen_without_reanchor_does_not_move_the_clock(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = Reopen().provisioned_completed(repo)
            before = read_work(repo, wid)["started_at"]
            rc, out = run(repo, "reopen", "--work", wid, "--reason", "incomplete")
            self.assertEqual(rc, 0, out)
            self.assertFalse(out["reanchored"])
            self.assertEqual(read_work(repo, wid)["started_at"], before)


if __name__ == "__main__":
    unittest.main()
