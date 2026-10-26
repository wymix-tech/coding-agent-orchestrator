"""CAO-owned numbered requirement documents.

A one-sentence requirement has to become a real file before `cao start` can
create a Work Item. CAO owns the location and numbering convention
(`.orchestrator/requirements/001-requirement-<slug>-<date>.md`) and allocates
the path; the Agent writes the content so requirement text is never squeezed
into a CLI argument.
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
    if args and args[0] == "init" and p.returncode == 0:
        from tests.helpers import provision_fixture_background
        provision_fixture_background(repo)
    return p.returncode, out, p.stderr


class NamingConvention(unittest.TestCase):
    def test_numbered_dated_filename(self):
        name = cao.requirement_filename(1, "user-login", "2026-10-10")
        self.assertEqual(name, "001-requirement-user-login-2026-10-10.md")

    def test_chinese_titles_keep_their_characters(self):
        self.assertEqual(cao.requirement_slug("用户登录功能"), "用户登录功能")
        name = cao.requirement_filename(1, cao.requirement_slug("用户登录功能"), "2026-10-10")
        self.assertEqual(name, "001-requirement-用户登录功能-2026-10-10.md")

    def test_unsafe_characters_collapse_to_hyphens(self):
        self.assertEqual(cao.requirement_slug("Add login: v2 (urgent)!"), "Add-login-v2-urgent")

    def test_empty_title_still_produces_a_valid_name(self):
        name = cao.requirement_filename(7, "", "2026-10-10")
        self.assertEqual(name, "007-requirement-2026-10-10.md")


class NumberAllocation(unittest.TestCase):
    def test_starts_at_one(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(cao.next_requirement_number(pathlib.Path(td)), 1)

    def test_increments_past_existing_files(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / cao.REQUIREMENT_DIR).mkdir(parents=True)
            for n in ("001", "002", "005"):
                (repo / cao.REQUIREMENT_DIR / f"{n}-requirement-x-2026-10-10.md").write_text("x", encoding="utf-8")
            self.assertEqual(cao.next_requirement_number(repo), 6)

    def test_ignores_files_that_are_not_numbered(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / cao.REQUIREMENT_DIR).mkdir(parents=True)
            (repo / cao.REQUIREMENT_DIR / "notes.md").write_text("x", encoding="utf-8")
            self.assertEqual(cao.next_requirement_number(repo), 1)


class RequirementCommands(unittest.TestCase):
    def _repo(self, td):
        repo = pathlib.Path(td)
        run(repo, "init", "--host", "pi", env_extra=NO_HOST_ENV)
        return repo

    def test_next_id_creates_the_file_so_the_agent_only_edits(self):
        """A gated Agent cannot `write` a new file, so CAO must create it."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._repo(td)
            rc, out, _ = run(repo, "requirement", "next-id", "--title", "用户登录", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual(out["number"], "001")
            self.assertTrue(out["path"].startswith(".orchestrator/requirements/001-requirement-"))
            self.assertTrue(out["created"])
            self.assertTrue((repo / out["path"]).is_file(), "next-id must create the file")

    def test_created_file_has_a_skeleton_with_the_project_language(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._repo(td)
            rc, out, _ = run(repo, "requirement", "next-id", "--title", "用户登录", env_extra=NO_HOST_ENV)
            body = (repo / out["path"]).read_text(encoding="utf-8")
            self.assertIn("用户登录", body)
            self.assertIn("001", body)
            self.assertIn("需求描述", body, "default language is zh-CN")

    def test_next_id_allocates_distinct_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._repo(td)
            rc, first, _ = run(repo, "requirement", "next-id", "--title", "登录", env_extra=NO_HOST_ENV)
            rc, second, _ = run(repo, "requirement", "next-id", "--title", "订单", env_extra=NO_HOST_ENV)
            self.assertEqual(second["number"], "002")
            self.assertNotEqual(first["path"], second["path"])

    def test_requirement_files_discovers_the_directory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._repo(td)
            path = cao.new_requirement_path(repo, "登录")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 登录\n", encoding="utf-8")
            found = cao.requirement_files(repo, cao.config(repo))
            self.assertIn(path.resolve(), found)


class RequirementWriteIsNotBlocked(unittest.TestCase):
    """The intake gate must not deadlock writing CAO's own requirement docs."""

    def _provisioned(self, td):
        repo = pathlib.Path(td)
        run(repo, "init", "--host", "pi", env_extra=NO_HOST_ENV)
        run(repo, "sdd", "select", "--provider", "bmad")
        (repo / "_bmad").mkdir(exist_ok=True)
        from tests.helpers import provision_bmad
        provision_bmad(repo)
        return repo

    def test_requirement_document_is_writable_without_a_work_item(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._provisioned(td)
            target = str(repo / ".orchestrator/requirements/001-requirement-登录-2026-10-10.md")
            raw = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": target, "content": "# 登录"}}
            rc, out, _ = run(repo, "host-event", "--repo", ".", "--host", "pi", env_extra=NO_HOST_ENV, stdin=json.dumps(raw))
            self.assertEqual(out["decision"], "allow", f"requirement write denied: {out}")

    def test_exemption_survives_whatever_nesting_the_host_uses(self):
        """Hosts disagree on where the path lives; first-level reads deadlocked Pi."""
        target = ".orchestrator/requirements/001-requirement-登录-2026-10-10.md"
        shapes = [
            {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": target}},
            {"event": "pre_tool", "tool": "write", "input": {"path": target}},
            {"event": "pre_tool", "toolName": "write", "params": {"path": target}},
            {"event": "pre_tool", "tool_call": {"name": "write", "arguments": {"filePath": target}}},
            {"event": "pre_tool", "toolName": "edit", "params": {"file_path": target, "new_string": "x"}},
        ]
        for raw in shapes:
            self.assertTrue(cao.write_targets_native_sdd_output(pathlib.Path("."), raw), f"not exempt: {raw}")

    def test_other_orchestrator_state_is_still_protected(self):
        """Only the requirements directory is exempt; CAO state is not."""
        raw = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": ".orchestrator/work/x.json", "content": "{}"}}
        self.assertFalse(cao.write_targets_native_sdd_output(pathlib.Path("."), raw))
        raw = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": ".orchestrator/config.json", "content": "{}"}}
        self.assertFalse(cao.write_targets_native_sdd_output(pathlib.Path("."), raw))

    def test_round_trip_from_one_sentence_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._provisioned(td)
            rc, out, _ = run(repo, "requirement", "next-id", "--title", "用户登录", env_extra=NO_HOST_ENV)
            path = repo / out["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 用户登录\n基于配置文件实现登录。\n", encoding="utf-8")

            rc, out, _ = run(repo, "start", "--requirement", out["path"], env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0, f"start failed: {out}")
            self.assertEqual(out.get("action"), "VERIFICATION_SETUP")
            self.assertEqual(out["next_action"]["reason"], "VERIFICATION_BASELINE_REQUIRED")

            rc, out, _ = run(repo, "path", "assess", "--profile", "light", "--reason", "clear small change", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0, f"path assess failed: {out}")


if __name__ == "__main__":
    unittest.main()
