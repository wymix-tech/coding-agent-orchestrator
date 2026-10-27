import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from repair_helpers import HOST_KEYS

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"

spec = __import__("importlib.util").util.spec_from_file_location("cao", CAO)
cao = __import__("importlib.util").util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cao)


def run(repo, *args):
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, timeout=20)
    try:
        out = json.loads(p.stdout)
    except Exception:
        out = {"raw": p.stdout}
    if args and args[0] == "init" and p.returncode == 0:
        from tests.helpers import provision_fixture_background
        provision_fixture_background(repo)
    return p.returncode, out, p.stderr


class RepairTests(unittest.TestCase):
    def test_bmad_project_command_is_non_interactive_and_scoped(self):
        args = argparse.Namespace(host="claude-code", tools=None)
        cmd = cao.build_bmad_project_command(args, pathlib.Path("/tmp/demo"))
        self.assertEqual(cmd, [
            "npx", "bmad-method@6.12.4", "install",
            "--directory", "/tmp/demo",
            "--modules", "bmm",
            "--tools", "claude-code",
            "--yes",
        ])

    def test_bmad_requires_host_or_tools(self):
        args = argparse.Namespace(host=None, tools=None)
        with mock.patch.dict(os.environ, {key: "" for key in HOST_KEYS}):
            with self.assertRaises(ValueError):
                cao.build_bmad_project_command(args, pathlib.Path("/tmp/demo"))


    def test_bmad_catalog_command_template_contains_required_non_interactive_flags(self):
        template = cao.SDD_CATALOG["bmad"]["install"]["project_command"]
        self.assertIn("--directory", template)
        self.assertIn("--modules", template)
        self.assertIn("--tools", template)
        self.assertIn("--yes", template)
        self.assertIn("{repo}", template)
        self.assertIn("{tool}", template)

    def test_python311_satisfies_bmad_when_generic_python3_alias_is_missing(self):
        def exists(name):
            return name in {"python3.11", "uv", "node"}

        def output(argv, timeout=3.0):
            if argv[0] == "python3.11":
                return 0, "Python 3.11.9", ""
            if argv[0] == "node":
                return 0, "v20.12.0", ""
            return 127, "", ""

        with mock.patch("scripts.cao_providers.command_exists", side_effect=exists), mock.patch("scripts.cao_providers.command_output", side_effect=output):
            check = cao.sdd_prerequisite_check("bmad")
        self.assertTrue(check["ok"], check)
        py = next(item for item in check["checks"] if item["name"] == "python")
        self.assertTrue(py["ok"], py)
        self.assertEqual(py["selected"], "python3.11")

    def test_completion_guidance_for_changed_requirement_reanchors_instead_of_verify(self):
        work = {
            "id": "work-1",
            "requirement": {"path": "SPEC.md", "sha256": "old"},
        }
        instruction = cao.completion_next_instruction(pathlib.Path("/tmp/repo"), work, {
            "ready": False,
            "reason": "requirement changed since work started",
        })
        self.assertIn("cao amend --work work-1", instruction)
        self.assertIn("--expected-token", instruction)
        self.assertNotEqual(instruction, "Run `python scripts/cao.py verify`.")

    def test_replace_uses_reanchor_language(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "openspec").mkdir()
            (repo / "openspec/config.yaml").write_text("context: test\n", encoding="utf-8")
            (repo / "requirements").mkdir()
            (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            (repo / "requirements/b.md").write_text("B", encoding="utf-8")
            run(repo, "init")
            run(repo, "start", "--requirement", "requirements/a.md")
            rc, _, _ = run(repo, "start", "--requirement", "requirements/b.md", "--replace")
            self.assertEqual(rc, 0)
            a_id = cao.stable_work_id(repo, repo / "requirements/a.md")
            handoff_file = next((repo / ".orchestrator/handoffs" / a_id).glob("*.json"))
            handoff = json.loads(handoff_file.read_text(encoding="utf-8"))
            self.assertIn("re-anchored", handoff["reason"])
            old_work = json.loads((repo / ".orchestrator/work" / f"{a_id}.json").read_text(encoding="utf-8"))
            self.assertTrue(any("re-anchored" in x for x in old_work["resume"]["pending"]))

    def test_existing_old_verification_default_is_treated_as_unconfigured(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / ".orchestrator").mkdir()
            (repo / ".orchestrator/config.json").write_text(json.dumps({
                "verification": {"command": "python -m unittest discover -s tests -p 'test_*.py'"}
            }), encoding="utf-8")
            self.assertEqual(cao.config(repo)["verification"]["scripts"], [])

    def test_verification_scripts_support_multiple_configured_entries(self):
        cfg = {
            "verification": {
                "scripts": [
                    {"name": "unit", "command": "python -c \"print(1)\"", "required": True},
                    {"name": "coverage", "command": "python -c \"print(2)\"", "required": True},
                    {"name": "lint", "command": "python -c \"raise SystemExit(1)\"", "required": False},
                ]
            }
        }
        commands = cao.verification_scripts(cfg)
        self.assertEqual([x["name"] for x in commands], ["unit", "coverage", "lint"])
        self.assertTrue(all(x["required"] for x in commands[:2]))
        self.assertFalse(commands[2]["required"])

    def test_legacy_single_verification_command_is_migrated(self):
        cfg = {"verification": {"command": "mvn -q test"}}
        commands = cao.verification_scripts(cfg)
        self.assertEqual(commands, [{"name": "default", "command": "mvn -q test", "required": True, "gates": []}])

    def test_legacy_command_list_is_migrated_to_scripts(self):
        cfg = {
            "verification": {
                "commands": [
                    {"name": "unit", "command": "./verify-unit.sh", "required": True},
                    {"name": "coverage", "command": "./verify-coverage.sh", "required": True},
                ]
            }
        }
        commands = cao.verification_scripts(cfg)
        self.assertEqual([x["name"] for x in commands], ["unit", "coverage"])

    def test_verification_exit_code_protocol_is_explicit(self):
        self.assertEqual(cao.verification_status_for_exit_code(0), "passed")
        self.assertEqual(cao.verification_status_for_exit_code(1), "failed")
        self.assertEqual(cao.verification_status_for_exit_code(2), "blocked")
        self.assertEqual(cao.verification_status_for_exit_code(3), "error")

    def test_verification_reason_uses_last_non_empty_output_line(self):
        self.assertEqual(cao.verification_reason("first\n reason here \n", "", "failed"), "reason here")
        self.assertEqual(cao.verification_reason("", "blocked by Redis\n", "blocked"), "blocked by Redis")


if __name__ == "__main__":
    unittest.main()
