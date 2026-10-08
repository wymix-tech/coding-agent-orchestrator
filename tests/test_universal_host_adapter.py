import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import host_runtime
import install_host_adapter


class UniversalHostAdapterTests(unittest.TestCase):
    def test_auto_selects_current_host(self):
        result = host_runtime.select_hosts(
            "auto",
            env={"PI_CODING_AGENT": "true"},
            process_chain=[],
        )
        self.assertEqual(["pi"], result["hosts"])
        self.assertEqual("env:PI_CODING_AGENT", result["source"])

    def test_all_remains_explicit_multi_host_provisioning(self):
        result = host_runtime.select_hosts("all", env={}, process_chain=[])
        self.assertEqual(["claude-code", "codex", "pi"], result["hosts"])
        self.assertEqual("explicit", result["source"])

    def test_none_disables_adapter_provisioning(self):
        result = host_runtime.select_hosts("none", env={}, process_chain=[])
        self.assertEqual([], result["hosts"])

    def test_installer_accepts_auto_without_touching_files_in_dry_run(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            # Dry-run should not create host files.
            import subprocess
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "install_host_adapter.py"),
                 "--repo", str(repo), "--host", "auto"],
                text=True, capture_output=True, check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual("auto", payload["host_selection"]["requested"])
            self.assertFalse((repo / ".claude").exists())
            self.assertFalse((repo / ".codex").exists())
            self.assertFalse((repo / ".pi").exists())


if __name__ == "__main__":
    unittest.main()
