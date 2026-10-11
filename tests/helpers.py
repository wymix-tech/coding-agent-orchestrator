"""Real, pre-start verification standards for CAO integration tests."""
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time


def configure_verification(repo, command=None):
    """Provision a genuine unittest, or an explicitly general verification script."""
    repo = pathlib.Path(repo)
    gates = []
    if command is None:
        tests = repo / "acceptance-tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_acceptance.py").write_text(
            "import unittest\nclass Acceptance(unittest.TestCase):\n"
            "    def test_ok(self): self.assertEqual(1 + 1, 2)\n", encoding="utf-8")
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "acceptance-tests", "-v"]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        gates = ["tests"]
    path = repo / ".orchestrator/config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("verification", {})["scripts"] = [
        {"name": "script-1", "command": command, "required": True, "gates": gates}
    ]
    cfg["verification"]["configured_at"] = int(time.time())
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def verify_via_config(repo, run):
    """Execute the existing standard without altering it or fabricating its age."""
    return run(repo, "verify")
