"""Real CLI and acceptance fixtures shared by the integrity repair tests."""

import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOST_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)


class RepairCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name)
        tests = self.repo / "acceptance-tests"
        tests.mkdir()
        (tests / "test_acceptance.py").write_text(
            "import unittest\nclass Acceptance(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n", encoding="utf-8",
        )
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "acceptance-tests", "-v"]
        self.test_command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        rc, out = self.run_cli("init", "--host", "codex", "--sdd", "bmad",
                               "--test-script", self.test_command)
        self.assertEqual(rc, 0, out)
        (self.repo / "_bmad").mkdir()

    def run_cli(self, *args, payload=None) -> tuple[int, dict]:
        env = dict(os.environ)
        for key in HOST_KEYS:
            env.pop(key, None)
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/cao.py"), *map(str, args), "--repo", str(self.repo)],
            text=True, capture_output=True, env=env, timeout=30,
            input=json.dumps(payload) if payload is not None else None,
        )
        try:
            out = json.loads(result.stdout)
        except json.JSONDecodeError:
            out = {"raw": result.stdout, "stderr": result.stderr}
        return result.returncode, out

    def start(self, title="a", profile="light") -> str:
        req = self.repo / f".orchestrator/requirements/{title}.md"
        req.parent.mkdir(exist_ok=True)
        req.write_text(f"# {title}\nOriginal scope\n", encoding="utf-8")
        rc, out = self.run_cli("start", "--requirement", req.relative_to(self.repo))
        self.assertEqual(rc, 0, out)
        wid = out["work_item"]["id"]
        rc, out = self.run_cli("path", "assess", "--work", wid,
                               "--profile", profile, "--reason", "bounded repair")
        self.assertEqual(rc, 0, out)
        return wid

    def read_work(self, wid) -> dict:
        return json.loads((self.repo / f".orchestrator/work/{wid}.json").read_text(encoding="utf-8"))
