import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo: pathlib.Path, *args: str):
    proc = subprocess.run(
        [sys.executable, str(CAO), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=20,
    )
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = {"raw": proc.stdout}
    if args and args[0] == "init" and proc.returncode == 0:
        from tests.helpers import provision_fixture_background
        provision_fixture_background(repo)
    return proc.returncode, payload, proc.stderr


class HandoffHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tempdir.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True, timeout=20)
        (self.repo / "openspec" / "changes" / "add-login").mkdir(parents=True)
        (self.repo / "openspec" / "config.yaml").write_text("schema: spec-driven\n", encoding="utf-8")
        (self.repo / "requirements").mkdir()
        (self.repo / "requirements" / "login.md").write_text("Implement login", encoding="utf-8")
        self.assertEqual(run(self.repo, "init")[0], 0)
        self.assertEqual(run(self.repo, "start", "--requirement", "requirements/login.md")[0], 0)
        self.work_id = run(self.repo, "status")[1]["work"]["work_item"]["id"]

    def tearDown(self):
        self.tempdir.cleanup()

    def _create(self, summary: str):
        rc, out, err = run(
            self.repo,
            "handoff",
            "--work", self.work_id,
            "--reason", "transfer",
            "--summary", summary,
        )
        self.assertEqual(rc, 0, msg=err or out)
        return out["handoff"]

    def test_multiple_handoffs_are_retained_and_listed_newest_first(self):
        first = self._create("First handoff summary")
        second = self._create("Second handoff summary")

        history_dir = self.repo / ".orchestrator" / "handoffs" / self.work_id
        archived = list(history_dir.glob("*.json"))
        self.assertEqual(len(archived), 2)
        self.assertNotEqual(first["id"], second["id"])

        rc, out, err = run(self.repo, "handoff", "list", "--work", self.work_id)
        self.assertEqual(rc, 0, msg=err or out)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["handoffs"][0]["id"], second["id"])
        self.assertEqual(out["handoffs"][1]["id"], first["id"])

    def test_show_returns_full_handoff_record(self):
        created = self._create("Handoff visible by ID")
        rc, out, err = run(self.repo, "handoff", "show", "--id", created["id"])
        self.assertEqual(rc, 0, msg=err or out)
        self.assertEqual(out["handoff"]["id"], created["id"])
        self.assertEqual(out["handoff"]["summary"], "Handoff visible by ID")
        self.assertIn("next_action", out["handoff"])

    def test_resume_uses_latest_handoff_record(self):
        self._create("Old state")
        latest = self._create("Latest state")
        rc, out, err = run(self.repo, "resume", "--work", self.work_id, "--host", "codex", "--session", "codex-session")
        self.assertEqual(rc, 0, msg=err or out)
        self.assertIn("Latest state", out.get("raw", ""))
        self.assertEqual(latest["summary"], "Latest state")
        history_dir = self.repo / ".orchestrator" / "handoffs" / self.work_id
        self.assertEqual(len(list(history_dir.glob("*.json"))), 2)
        self.assertFalse((self.repo / ".orchestrator" / "handoffs" / f"{self.work_id}.json").exists())

    def test_handoff_list_can_filter_and_limit(self):
        self._create("Login handoff one")
        self._create("Login handoff two")
        (self.repo / "requirements" / "search.md").write_text("Implement search", encoding="utf-8")
        self.assertEqual(run(self.repo, "start", "--requirement", "requirements/search.md", "--replace")[0], 0)
        other_work = run(self.repo, "status")[1]["work"]["work_item"]["id"]
        run(self.repo, "handoff", "--work", other_work, "--summary", "Search handoff")

        rc, out, err = run(self.repo, "handoff", "list", "--work", self.work_id, "--limit", "1")
        self.assertEqual(rc, 0, msg=err or out)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["handoffs"][0]["work_item"], self.work_id)

    def test_unknown_handoff_returns_actionable_error(self):
        rc, out, _ = run(self.repo, "handoff", "show", "--id", "handoff-does-not-exist")
        self.assertEqual(rc, 2)
        self.assertEqual(out["error"], "HANDOFF_NOT_FOUND")

    def test_flat_handoff_files_at_history_root_are_not_loaded(self):
        # The history-directory layout is the only supported storage format.
        root = self.repo / ".orchestrator" / "handoffs"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.work_id}.json"
        path.write_text(json.dumps({
            "version": 1,
            "id": "handoff-old-flat-format",
            "work_item": self.work_id,
            "reason": "old format",
            "created_at": 100,
            "summary": "must not be loaded",
        }), encoding="utf-8")

        rc, out, err = run(self.repo, "handoff", "list", "--work", self.work_id)
        self.assertEqual(rc, 0, msg=err or out)
        self.assertEqual(out["count"], 0)
        rc, shown, _ = run(self.repo, "handoff", "show", "--id", "handoff-old-flat-format")
        self.assertEqual(rc, 2)
        self.assertEqual(shown["error"], "HANDOFF_NOT_FOUND")

    def test_invalid_limit_is_rejected(self):
        rc, out, _ = run(self.repo, "handoff", "list", "--limit", "0")
        self.assertEqual(rc, 2)
        self.assertEqual(out["error"], "INVALID_LIMIT")


if __name__ == "__main__":
    unittest.main()
