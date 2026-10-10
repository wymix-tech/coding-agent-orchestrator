"""Intake deadlock tests.

Reproduces the reported failure: with no work item, a one-sentence requirement
makes the Agent write a requirement/PRD file, the PreToolUse hook denies it
("No active work item"), but `cao start` needs that file to exist — a deadlock
where intake can never begin.
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

spec = __import__("importlib.util").util.spec_from_file_location("cao", CAO)
cao = __import__("importlib.util").util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cao)

HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args, env_extra=None, stdin=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True,
                       env=env, timeout=60, input=stdin)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"raw": p.stdout}
    return p.returncode, out, p.stderr


def provisioned_repo(repo):
    """An initialized project with BMAD detected as installed."""
    run(repo, "init", "--host", "pi", env_extra=NO_HOST_ENV)
    run(repo, "sdd", "select", "--provider", "bmad")
    (repo / "_bmad").mkdir(exist_ok=True)  # BMAD's project marker
    rc, out, _ = run(repo, "bootstrap", env_extra=NO_HOST_ENV)
    assert out.get("mode") == "select", f"bootstrap did not reach selection: {out}"


def write_tool(path: str) -> dict:
    return {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": path, "content": "..."}}


def bash_tool(command: str) -> dict:
    return {"event": "pre_tool", "tool_name": "bash", "tool_input": {"command": command}}


def pre_tool(repo, raw):
    """Run the real hook entry point over stdin, exactly as the host adapter does."""
    return run(repo, "host-event", "--repo", ".", "--host", "pi", env_extra=NO_HOST_ENV, stdin=json.dumps(raw))


class NativeOutputDirExemption(unittest.TestCase):
    """Unit-level checks on the exemption itself."""

    def test_prd_write_is_exempt_even_without_a_work_item(self):
        raw = write_tool("/tmp/demo7/_bmad-output/planning-artifacts/prds/prd-2026-10-10/prd.md")
        self.assertTrue(cao.tool_is_mutating(raw))
        self.assertTrue(cao.write_targets_native_sdd_output(raw))

    def test_product_code_is_not_exempt(self):
        for path in ("/tmp/demo7/src/main/java/App.java", "/tmp/demo7/README.md", "/tmp/demo7/pom.xml"):
            self.assertFalse(cao.write_targets_native_sdd_output(write_tool(path)), path)

    def test_mutating_bash_touching_native_dirs_is_exempt(self):
        self.assertTrue(cao.write_targets_native_sdd_output(bash_tool("mkdir -p _bmad-output/planning-artifacts")))
        self.assertTrue(cao.write_targets_native_sdd_output(bash_tool("echo x > openspec/changes/add-login/spec.md")))

    def test_every_provider_has_an_output_dir(self):
        for provider, meta in cao.SDD_CATALOG.items():
            self.assertTrue(meta.get("output_dirs"), f"{provider} declares no output_dirs")


class IntakeDeadlock(unittest.TestCase):
    def test_requirement_can_be_written_before_any_work_item_exists(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned_repo(repo)
            rc, out, _ = pre_tool(repo, write_tool(str(repo / "_bmad-output/planning-artifacts/prds/prd-x/prd.md")))
            self.assertEqual(rc, 0)
            self.assertEqual(out["decision"], "allow", f"intake write was denied: {out}")

    def test_product_code_is_also_allowed_now_that_the_gate_is_removed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned_repo(repo)
            rc, out, _ = pre_tool(repo, write_tool(str(repo / "src/App.java")))
            self.assertEqual(out["decision"], "allow")

    def test_start_reports_a_missing_explicit_requirement_as_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned_repo(repo)
            rc, out, _ = run(repo, "start", "--requirement", "_bmad-output/planning-artifacts/prds/prd-x/prd.md", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "REQUIREMENT_FILE_MISSING", f"misleading error: {out}")

    def test_full_intake_round_trip_from_a_one_sentence_requirement(self):
        """Write PRD -> start -> path assess, with no work item existing beforehand."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned_repo(repo)

            prd = repo / "_bmad-output/planning-artifacts/prds/prd-login/prd.md"
            prd.parent.mkdir(parents=True, exist_ok=True)
            prd.write_text("# Login\nImplement login.", encoding="utf-8")

            rc, out, _ = run(repo, "start", "--requirement", str(prd), env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0, f"start failed: {out}")
            self.assertEqual(out.get("action"), "ASSESS_PATH")
            self.assertTrue(out["work_item"]["id"])

            rc, out, _ = run(repo, "path", "assess", "--profile", "light", "--reason", "clear small change", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0, f"path assess failed: {out}")


if __name__ == "__main__":
    unittest.main()
