"""Acceptance-criteria integrity: the implementer must not define what counts as done.

demo11 escape path: verify fails (no scripts) -> Agent runs `cao init
--verify-script "<compile>" --force` -> verify passes -> `cao done` closes a
requirement that was never implemented. These tests replay that exact sequence.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"

HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args, stdin=None):
    env = dict(os.environ, **NO_HOST_ENV)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True,
                       capture_output=True, env=env, timeout=60, input=stdin)
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"raw": p.stdout, "stderr": p.stderr}


def active_work(repo: pathlib.Path) -> str:
    run(repo, "init", "--host", "pi")
    run(repo, "sdd", "select", "--provider", "bmad")
    (repo / "_bmad").mkdir(exist_ok=True)
    rc, out = run(repo, "requirement", "next-id", "--title", "login")
    req = repo / out["path"]
    req.write_text("# login\n\nJWT auth, RBAC, session management.\n", encoding="utf-8")
    rc, out = run(repo, "start", "--requirement", out["path"])
    return out["work_item"]["id"]


def standard_path(repo: pathlib.Path, wid: str) -> None:
    """Walk the Work Item to the point where verify/done become the only blockers."""
    rc, out = run(repo, "path", "assess", "--profile", "standard", "--reason", "auth system", "--work", wid)
    assert rc == 0, out
    rc, out = run(repo, "sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build", "--work", wid)
    assert rc == 0, out
    # The artifact must live inside the provider's own output directory and must
    # exist before `complete` records it.
    artifact = repo / "_bmad-output" / "implementation_artifacts" / "story.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# story\n", encoding="utf-8")
    rc, out = run(repo, "sdd", "workflow", "execute", "--command", "bmad-build", "--exit-code", "0", "--work", wid)
    assert rc == 0, out
    rc, out = run(repo, "sdd", "workflow", "complete", "--artifact", "_bmad-output/implementation_artifacts/story.md", "--work", wid)
    assert rc == 0, out


def config_verification(repo: pathlib.Path, command: str, *, after_start: bool = False) -> None:
    cfg = json.loads((repo / ".orchestrator" / "config.json").read_text(encoding="utf-8"))
    cfg.setdefault("verification", {})["scripts"] = [{"name": "script-1", "command": command, "required": True}]
    # By default the standard predates the Work Item, so tests can exercise the
    # coverage rule (B) in isolation; `after_start=True` replays the demo11 move.
    index = json.loads((repo / ".orchestrator" / "index.json").read_text(encoding="utf-8"))
    work_file = next((repo / ".orchestrator" / "work").glob("*.json"), None)
    started = json.loads(work_file.read_text(encoding="utf-8"))["started_at"] if work_file else 0
    cfg["verification"]["configured_at"] = started + (3600 if after_start else -100)
    (repo / ".orchestrator" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


class StandardIsFixedBeforeStart(unittest.TestCase):
    def test_verify_refuses_a_standard_configured_after_start(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            config_verification(repo, "true", after_start=True)  # the demo11 move
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "VERIFICATION_CONFIGURED_AFTER_START")

    def test_standard_that_predates_the_work_is_still_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "pi", "--verify-script", "true")
            run(repo, "sdd", "select", "--provider", "bmad")
            (repo / "_bmad").mkdir(exist_ok=True)
            rc, out = run(repo, "requirement", "next-id", "--title", "login")
            req = repo / out["path"]
            req.write_text("# login\n", encoding="utf-8")
            rc, out = run(repo, "start", "--requirement", out["path"])
            wid = out["work_item"]["id"]
            # light path: no `tests` gate, so a build-only standard is allowed.
            rc, out = run(repo, "path", "assess", "--profile", "light", "--reason", "docs change", "--work", wid)
            rc, out = run(repo, "verify", "--work", wid)
            self.assertNotEqual(out.get("error"), "VERIFICATION_CONFIGURED_AFTER_START", out)


class StandardMustCoverRequiredGates(unittest.TestCase):
    def test_verify_refuses_build_only_standard_when_tests_gate_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)  # standard profile -> `tests` gate required
            config_verification(repo, "mvn clean compile")  # compiles, never tests
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "VERIFICATION_DOES_NOT_RUN_TESTS")

    def test_a_real_test_command_satisfies_the_tests_gate_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            config_verification(repo, "python3 -m unittest --help")
            rc, out = run(repo, "verify", "--work", wid)
            self.assertNotEqual(out.get("error"), "VERIFICATION_DOES_NOT_RUN_TESTS", out)


class DoneCannotUseACliChosenStandard(unittest.TestCase):
    def test_done_rejects_a_verification_run_with_cli_override(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = active_work(repo)
            standard_path(repo, wid)
            config_verification(repo, "python3 -m unittest --help")
            # sanity check with an ad-hoc command is allowed, but must not count for done
            rc, out = run(repo, "verify", "--work", wid, "--command", "true")
            self.assertNotEqual(out.get("error"), "VERIFICATION_CONFIGURED_AFTER_START")
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "COMPLETION_BLOCKED")


class Reopen(unittest.TestCase):
    def provisioned_completed(self, repo: pathlib.Path) -> str:
        wid = active_work(repo)
        standard_path(repo, wid)
        config_verification(repo, "python3 -m unittest --help")
        run(repo, "verify", "--work", wid)
        for gate in ("scope_review", "tests", "implementation_review"):
            run(repo, "gate", "satisfy", "--gate", gate, "--evidence", "reviewed", "--work", wid)
        rc, out = run(repo, "done", "--work", wid)
        self.assertEqual(rc, 0, out)
        return wid

    def test_reopen_returns_a_completed_item_to_active_and_clears_self_earned_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = self.provisioned_completed(repo)
            rc, out = run(repo, "reopen", "--work", wid, "--reason", "only story 1.1 was implemented")
            self.assertEqual(rc, 0, out)
            work = json.loads((repo / ".orchestrator" / "work" / f"{wid}.json").read_text(encoding="utf-8"))
            self.assertEqual(work["status"], "active")
            self.assertIsNone(work.get("completed_at"))
            self.assertEqual(work.get("gate_status"), {}, "self-satisfied gates must be re-earned")
            self.assertEqual(work.get("verification"), {}, "stale verification must not carry over")
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
            config_verification(repo, "python3 -m unittest --help")
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual(rc, 0, out)
            for gate in ("scope_review", "tests", "implementation_review"):
                run(repo, "gate", "satisfy", "--gate", gate, "--evidence", "re-reviewed", "--work", wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
