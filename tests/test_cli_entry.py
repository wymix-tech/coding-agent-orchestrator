import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliEntryTests(unittest.TestCase):
    def test_project_declares_cao_console_script(self):
        project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(
            project_text,
            r'(?ms)^\[project\.scripts\]\s+cao\s*=\s*\"scripts\.cao:main\"',
        )

    def test_project_instruction_templates_are_packaged_as_data_files(self):
        project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('[tool.setuptools.data-files]', project_text)
        self.assertTrue((ROOT / "templates" / "AGENTS.md").is_file())
        self.assertTrue((ROOT / "templates" / "CLAUDE.md").is_file())
        self.assertFalse((ROOT / "AGENTS.md").exists())
        self.assertFalse((ROOT / "CLAUDE.md").exists())
        self.assertFalse((ROOT / ".agents").exists())

    def test_core_is_under_scripts_directory(self):
        self.assertTrue((ROOT / "scripts" / "cao.py").is_file())
        self.assertFalse((ROOT / "cao.py").exists())
        self.assertTrue((ROOT / "scripts" / "__init__.py").is_file())

    def test_source_launcher_prints_help(self):
        result = subprocess.run(
            [str(ROOT / "cao"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Coding Agent Orchestrator", result.stdout)

    def test_hooks_use_shared_runner(self):
        claude = (ROOT / "hosts/claude-code/settings.fragment.json").read_text()
        codex = (ROOT / "hosts/codex/hooks.json").read_text()
        pi = (ROOT / "hosts/pi/coding-orchestrator.ts").read_text()
        self.assertIn("hooks/host-event.sh", claude)
        self.assertIn("hooks/host-event.sh", codex)
        self.assertIn('spawnSync("cao", args', pi)
        self.assertIn('spawnSync("sh", [CAO_FALLBACK, ...args]', pi)


if __name__ == "__main__":
    unittest.main()
