""".orchestrator guard, decision records, and handoff de-duplication.

The guard must be narrow enough to never deadlock intake: CAO writes its own
state through commands (which do not go through this hook), so the only writes
it denies are hand edits to derived state. `requirements/` stays writable
because the Agent authors those documents.
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
        if args and args[0] == "init" and p.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        if args and args[0] == "init" and p.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return p.returncode, {"raw": p.stdout, "stderr": p.stderr}


def provisioned(repo: pathlib.Path) -> str:
    run(repo, "init", "--host", "pi")
    run(repo, "sdd", "select", "--provider", "bmad")
    (repo / "_bmad").mkdir(exist_ok=True)
    from tests.helpers import provision_bmad
    provision_bmad(repo)
    rc, out = run(repo, "requirement", "next-id", "--title", "login")
    path = out["path"]
    (repo / path).write_text("# Fixture\nRecord a bounded local state transition.\n", encoding="utf-8")
    run(repo, "start", "--requirement", path)
    return path


def pre_tool(repo: pathlib.Path, raw: dict) -> dict:
    rc, out = run(repo, "host-event", "--repo", ".", "--host", "pi", stdin=json.dumps(raw))
    return out


class DerivedStateIsProtected(unittest.TestCase):
    PROTECTED = (
        ".orchestrator/work/001-requirement-x.json",
        ".orchestrator/index.json",
        ".orchestrator/config.json",
        ".orchestrator/sessions/session-1.json",
        ".orchestrator/handoffs/w/handoff-1.json",
        ".orchestrator/lifecycle/events.jsonl",
    )

    def test_write_tools_cannot_edit_derived_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            for target in self.PROTECTED:
                out = pre_tool(repo, {"event": "pre_tool", "toolName": "write",
                                      "params": {"path": target, "content": "x"}})
                self.assertEqual(out["decision"], "deny", f"Agent could edit {target}")

    def test_shell_writes_cannot_edit_derived_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            for command in ("rm -rf .orchestrator",
                            "echo '{}' > .orchestrator/index.json",
                            "sed -i s/a/b/ .orchestrator/work/w.json"):
                out = pre_tool(repo, {"event": "pre_tool", "toolName": "bash",
                                      "params": {"command": command}})
                self.assertEqual(out["decision"], "deny", f"shell could mutate state: {command}")

    def test_the_denial_names_the_command_that_owns_it(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            out = pre_tool(repo, {"event": "pre_tool", "toolName": "write",
                                  "params": {"path": ".orchestrator/work/w.json", "content": "x"}})
            self.assertIn("cao checkpoint", out["reason"])
            self.assertIn("cao decision --decision", out["reason"])
            from scripts import cao
            args = cao.build_parser().parse_args(["decision", "--decision", "Observed decision"])
            self.assertEqual(args.decision, "Observed decision")


class GuardDoesNotDeadlockIntake(unittest.TestCase):
    def test_requirement_documents_stay_writable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            req = provisioned(repo)
            for shape in (
                {"toolName": "write", "params": {"path": req, "content": "x"}},
                {"tool_name": "write", "tool_input": {"path": req, "content": "x"}},
                {"toolName": "edit", "params": {"file_path": req, "new_string": "x"}},
            ):
                out = pre_tool(repo, {"event": "pre_tool", **shape})
                self.assertEqual(out["decision"], "allow", f"intake write denied: {shape}")

    def test_reading_state_is_never_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            for command in ("cat .orchestrator/work/w.json", "ls -la .orchestrator", "cao next"):
                out = pre_tool(repo, {"event": "pre_tool", "toolName": "bash",
                                      "params": {"command": command}})
                self.assertEqual(out["decision"], "allow", f"read blocked: {command}")


class DecisionsAreRecorded(unittest.TestCase):
    def test_decision_is_written_and_can_be_read_back(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            rc, out = run(repo, "decision", "--decision", "Use JWT", "--reason", "stateless sessions")
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["decision"]["decision"], "Use JWT")
            self.assertEqual(out["decision"]["reason"], "stateless sessions")
            rc, listed = run(repo, "decisions")
            self.assertEqual(rc, 0, listed)
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["decisions"][0]["decision"], "Use JWT")

    def test_decision_reaches_index_and_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            run(repo, "decision", "--decision", "Use JWT", "--reason", "stateless")
            index = json.loads((repo / ".orchestrator" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["work_items"][0]["decision_count"], 1)
            run(repo, "handoff", "--end-session")
            handoff = sorted((repo / ".orchestrator" / "handoffs").rglob("*.json"))[0]
            record = json.loads(handoff.read_text(encoding="utf-8"))
            self.assertEqual(len(record["decisions"]), 1)


class HandoffsAreDeduplicated(unittest.TestCase):
    def test_identical_snapshots_collapse_into_one_record(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            for _ in range(6):
                run(repo, "handoff")
            records = sorted((repo / ".orchestrator" / "handoffs").rglob("*.json"))
            self.assertEqual(len(records), 1, "identical snapshots should not accumulate")

    def test_changed_state_still_produces_a_new_record(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provisioned(repo)
            run(repo, "handoff")
            run(repo, "handoff", "--summary", "implemented login endpoint")
            run(repo, "handoff", "--summary", "added JWT filter")
            records = sorted((repo / ".orchestrator" / "handoffs").rglob("*.json"))
            self.assertEqual(len(records), 3, "distinct summaries must each be recorded")


if __name__ == "__main__":
    unittest.main()
