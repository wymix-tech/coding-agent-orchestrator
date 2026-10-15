"""Regressions from the 2026-10-15 persistent Pi/BMAD lifecycle test."""
import os
import shlex
import sys

from repair_helpers import RepairCase, ROOT
from scripts import cao


class PiBootstrapRepairs(RepairCase):
    def hook(self, command):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": "pre_tool", "session_id": "bootstrap-pi", "toolName": "bash",
            "input": {"command": command},
        })
        self.assertEqual(rc, 0, out)
        return out

    def test_host_control_commands_do_not_require_work_or_baseline(self):
        launchers = (["cao"], [str(ROOT / "cao")], ["sh", str(ROOT / "cao")],
                     [sys.executable, str(ROOT / "scripts/cao.py")])
        for active in (False, True):
            if active:
                cfg = cao.config(self.repo)
                cfg["verification"]["scripts"] = []
                cao.save_json(self.repo / cao.CONFIG_REL, cfg)
                self.start()
            for launcher in launchers:
                for subcommand in ("install", "status"):
                    command = shlex.join([*launcher, "host", subcommand, "--host", "pi"])
                    with self.subTest(active=active, command=command):
                        self.assertEqual(self.hook(command)["decision"], "allow")

    def test_version_and_digest_probes_are_readonly_before_work(self):
        commands = ("python3.12 --version", "python3.11 -V", shlex.join([sys.executable, "--version"]),
                    "sha256sum _bmad-output/spec.md", "sha256sum --binary -- '-odd name.md'",
                    "shasum -a 256 _bmad-output/spec.md", "shasum --algorithm=512 spec.md")
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command)["decision"], "allow")

    def test_probe_and_host_exemptions_do_not_allow_side_effects(self):
        commands = ("cao host install --host pi > .orchestrator/index.json",
                    "cao host install --host pi && echo code > product.py",
                    "python3.12 --version; touch product.py", "python3.12 -v",
                    "python3.12 -c 'open(\"product.py\", \"w\")'",
                    "python3.12 -c 'open(\"product.py\", \"w\")' scripts/cao.py host install --host pi",
                    "python3.12 /unrelated/scripts/cao.py host install --host pi",
                    "sha256sum spec.md > product.py", "sha256sum spec.md | tee product.py",
                    "sha256sum $(touch product.py)", "shasum -a 256 `touch product.py`",
                    "sha256sum --output=product.py spec.md", "shasum -a 256 --output product.py spec.md")
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.hook(command)["decision"], "deny")

    def test_setup_denial_explains_the_allowed_wrapper_location(self):
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"] = []
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        self.start()
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": "pre_tool", "session_id": "bootstrap-pi", "toolName": "write",
            "input": {"path": "scripts/check.py", "content": "raise SystemExit(1)"},
        })
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("scripts/verification/", out["reason"])
        self.assertIn("scripts/", out["reason"])

    def test_pi_status_recognizes_the_registered_package_in_a_renamed_checkout(self):
        root = self.repo / "renamed-package"
        root.mkdir()
        relative = os.path.relpath(root, self.repo / ".pi")
        for source in (str(root), relative, {"source": relative}):
            with self.subTest(source=source):
                cao.save_json(self.repo / ".pi/settings.json", {"packages": [source]})
                self.assertTrue(cao.host_adapter_status(self.repo, "pi", root)["installed"])
