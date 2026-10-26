"""No-Work boundaries and the legacy shell bridge fallback contract.

Pi failure semantics are exercised through real events in test_pi_adapter.mjs.
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


def provisioned_repo(td: str) -> pathlib.Path:
    """Initialized project with BMAD installed and no Work Item."""
    repo = pathlib.Path(td)
    env = dict(os.environ, **NO_HOST_ENV)
    for args in (["init", "--host", "pi"], ["sdd", "select", "--provider", "bmad"]):
        subprocess.run([sys.executable, str(CAO), *args], cwd=repo, env=env,
                       text=True, capture_output=True, timeout=60)
    (repo / "_bmad").mkdir(exist_ok=True)
    from tests.helpers import provision_bmad
    provision_bmad(repo)
    return repo


def decision(repo: pathlib.Path, raw: dict) -> str:
    env = dict(os.environ, **NO_HOST_ENV)
    p = subprocess.run([sys.executable, str(CAO), "host-event", "--repo", ".", "--host", "pi"],
                       cwd=repo, env=env, text=True, capture_output=True, timeout=60,
                       input=json.dumps(raw))
    return json.loads(p.stdout)["decision"]


class PreToolUseWorkBoundary(unittest.TestCase):
    """Every host payload shape preserves the intake/product boundary."""

    TARGETS = (
        "src/App.java",
        "README.md",
        "pom.xml",
        ".orchestrator/requirements/001-requirement-login-2026-10-10.md",
        "_bmad-output/planning-artifacts/prds/prd-x/prd.md",
    )
    SHAPES = [
        {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": "@", "content": "x"}},
        {"event": "pre_tool", "tool": "write", "input": {"path": "@", "content": "x"}},
        {"event": "pre_tool", "toolName": "write", "params": {"path": "@", "content": "x"}},
        {"event": "pre_tool", "tool_call": {"name": "edit", "arguments": {"filePath": "@", "new_string": "x"}}},
        {"event": "pre_tool", "toolName": "edit", "params": {"file_path": "@", "new_string": "x"}},
        {"event": "pre_tool", "tool_name": "bash", "tool_input": {"command": "echo x > @"}},
        {"event": "pre_tool", "tool_name": "bash", "tool_input": {"command": "rm -rf @"}},
    ]

    def test_product_is_denied_and_intake_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = provisioned_repo(td)
            for shape in self.SHAPES:
                for target in self.TARGETS:
                    raw = json.loads(json.dumps(shape).replace("@", target))
                    expected = "allow" if target.startswith((".orchestrator/requirements/", "_bmad-output/")) else "deny"
                    self.assertEqual(decision(repo, raw), expected, f"incorrect boundary: {raw}")

    def test_cao_commands_are_never_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = provisioned_repo(td)
            for command in ("cao bootstrap --repo . --host pi", "cao init --host pi", "cao next"):
                raw = {"event": "pre_tool", "toolName": "bash", "params": {"command": command}}
                self.assertEqual(decision(repo, raw), "allow", f"blocked: {command}")


class HostAdapterFailsOpen(unittest.TestCase):
    """A broken or missing CAO must never block the Agent."""

    def test_hook_script_exits_zero_when_cao_is_missing(self):
        script = ROOT / "hooks" / "host-event.sh"
        with tempfile.TemporaryDirectory() as td:
            # Copy into a directory with no `cao` launcher beside it, so both
            # resolution paths fail and only the fail-open branch remains.
            copy = pathlib.Path(td) / "host-event.sh"
            copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
            p = subprocess.run(["/bin/sh", str(copy), "--host", "pi"], cwd=td, text=True,
                               capture_output=True, timeout=30)
            self.assertEqual(p.returncode, 0, f"hook failed closed: {p.stderr}")



if __name__ == "__main__":
    unittest.main()
