"""Exercise installed navigation, stage transitions and safe instruction migration."""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from scripts import cao
from scripts.instruction_updates import legacy_template, update_instructions
from scripts.skill_guidance import guidance, STAGE_FILES, PROVIDER_FILES, CONTRACT_FILES
from tests.repair_helpers import RepairCase, ROOT


class ResourceContracts(unittest.TestCase):
    def test_every_shipped_guide_is_directly_discoverable_and_bounded(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(skill.splitlines()), 180)
        self.assertLess(len(skill.encode()), 10000)
        links = re.findall(r"\]\(([^)]+)\)", skill)
        for name in {*STAGE_FILES.values(), *PROVIDER_FILES.values(), *CONTRACT_FILES.values()}:
            self.assertIn(name, links)
        for name in links:
            self.assertTrue((ROOT / name).is_file(), name)
        for name in {*STAGE_FILES.values(), *PROVIDER_FILES.values()}:
            path = ROOT / name
            text = path.read_text(encoding="utf-8")
            for section in ("## Enter when", "## Execute", "## Exit when", "## If blocked"):
                self.assertIn(section, text, path.name)
        self.assertIn("```mermaid", skill)

    def test_missing_resource_is_reported_without_fallback_to_other_installation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "SKILL.md").write_text("Installed skill")
            out = guidance(root, "my-cao", "execution", "bmad")
            self.assertFalse(out["ready"])
            self.assertIn("references/provider-bmad.md", out["missing_resources"])
            self.assertTrue(all(Path(path).is_absolute() for path in out["read_before_action"]))


class RuntimeNavigation(RepairCase):
    def test_native_contract_uses_references_and_does_not_fall_back_to_developer_docs(self):
        wid = self.start()
        work = self.read_work(wid)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "SKILL.md").write_text("Installed Skill")
            for name in (STAGE_FILES["execution"], PROVIDER_FILES["bmad"], "docs/native-route-contract.md"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fixture resource")
            current = {"stage": "execution", "reason": "NATIVE_ROUTE_REQUIRED"}
            with patch.object(cao, "package_root_dir", return_value=root):
                out = cao.agent_guidance(self.repo, work, next_action=current)
                self.assertFalse(out["ready"])
                self.assertEqual(out["missing_resources"], [CONTRACT_FILES["native_route"]])
                self.assertIn(str(root / CONTRACT_FILES["native_route"]), out["read_before_action"])
                self.assertNotIn(str(root / "docs/native-route-contract.md"), out["read_before_action"])
                (root / CONTRACT_FILES["native_route"]).write_text("Runtime contract")
                self.assertTrue(cao.agent_guidance(self.repo, work, next_action=current)["ready"])

    def assert_guides(self, out, stage, expected):
        guide = out["guidance"]
        self.assertTrue(guide["ready"], guide)
        self.assertEqual(guide["stage"], stage)
        self.assertEqual([Path(p).name for p in guide["read_before_action"]], expected)
        self.assertTrue(Path(guide["skill"]).is_file())
        for path in guide["read_before_action"]:
            self.assertTrue(Path(path).is_absolute())
            self.assertTrue(Path(path).is_file())

    def test_bootstrap_and_next_share_setup_priority_then_route_current_provider(self):
        # An actual baseline change must route next to setup before assessment.
        wid = self.start()
        self.run_cli("verification", "configure", "--test-script", self.test_command + " -q", "--replace")
        for command in ("bootstrap", "next"):
            rc, out = self.run_cli(command)
            self.assertEqual(rc, 0, out)
            self.assert_guides(out, "verification_setup", ["verification-setup.md"])
            self.assertIn("verification", out.get("next", out.get("action"))["instruction"].lower())
        rc, out = self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", "Reviewed baseline")
        self.assertEqual(rc, 0, out)
        for command in ("bootstrap", "next"):
            rc, out = self.run_cli(command)
            self.assertEqual(rc, 0, out)
            self.assert_guides(out, "execution", ["execution.md", "provider-bmad.md"])

    def test_startup_intake_scope_recovery_and_handoff(self):
        (self.repo / "_bmad").rmdir()
        rc, out = self.run_cli("bootstrap")
        self.assertEqual(rc, 0, out)
        self.assert_guides(out, "startup", ["startup.md"])
        rc, next_out = self.run_cli("next")
        self.assert_guides(next_out, "startup", ["startup.md"])
        self.assertIn("bootstrap", next_out["action"]["instruction"])
        (self.repo / "_bmad").mkdir()
        rc, out = self.run_cli("bootstrap")
        self.assert_guides(out, "intake", ["work-intake.md"])
        wid = self.start()
        req = self.repo / self.read_work(wid)["requirement"]["path"]
        req.write_text("# Changed requirement\n")
        for command in ("bootstrap", "next"):
            rc, out = self.run_cli(command)
            self.assert_guides(out, "recovery", ["recovery.md"])
        rc, out = self.run_cli("context")
        self.assertEqual(rc, 0, out)
        self.assertIn("references/recovery.md", out["raw"])

    def test_current_provider_only_for_all_quality_profiles(self):
        work = {"status": "active", "sdd": {}, "execution": {"phase": "review"}, "path_assessment": {}}
        with patch.object(cao, "work_sdd_ready", return_value=(True, None)), \
             patch.object(cao, "verification_baseline_state", return_value={"ready": True}), \
             patch.object(cao, "path_assessment_state", return_value={"ready": True}):
            for provider in PROVIDER_FILES:
                for profile in ("light", "standard", "comprehensive"):
                    work["sdd"]["provider"] = provider
                    work["path_assessment"]["profile"] = profile
                    out = {"guidance": cao.agent_guidance(self.repo, work)}
                    self.assert_guides(out, "completion", ["completion.md", f"provider-{provider}.md"])
            work["status"] = "completed"
            self.assert_guides({"guidance": cao.agent_guidance(self.repo, work)}, "completed", [])

    def test_upgrade_is_advertised_but_only_explicit_apply_changes_project_rules(self):
        target = self.repo / "AGENTS.md"
        old = target.read_text().replace("Use CAO first", "Use legacy CAO first") + "\n# My custom rules\n"
        target.write_text(old)
        rc, boot = self.run_cli("bootstrap", "--host", "codex")
        self.assertEqual(rc, 0, boot)
        self.assertEqual(boot["project_instructions"]["upgrade_available"], ["AGENTS.md"])
        self.assertEqual(target.read_text(), old)
        rc, preview = self.run_cli("instructions", "update", "--host", "codex")
        self.assertEqual(rc, 0, preview)
        self.assertEqual(target.read_text(), old)
        self.assertEqual(preview["files"][0]["status"], "preview")
        rc, applied = self.run_cli("instructions", "update", "--host", "codex", "--apply")
        self.assertEqual(rc, 0, applied)
        self.assertEqual(Path(applied["files"][0]["backup"]).read_text(), old)
        self.assertTrue(target.read_text().endswith("# My custom rules\n"))
        self.assertNotIn("Use legacy CAO first", target.read_text())


class InstructionMigration(unittest.TestCase):
    def test_preview_preserves_custom_tail_and_apply_backs_up_exact_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            old = b"# User rules\r\n<!-- CAO:BEGIN v2 -->\nold CAO\n<!-- CAO:END -->\r\n# Keep this\r\n"
            target = repo / "AGENTS.md"
            target.write_bytes(old)
            template = (ROOT / "templates/AGENTS.md").read_text()
            preview = update_instructions(repo, {"AGENTS.md": template})
            self.assertEqual(preview["files"][0]["status"], "preview")
            self.assertEqual(target.read_bytes(), old)
            result = update_instructions(repo, {"AGENTS.md": template}, True)
            item = result["files"][0]
            self.assertEqual(Path(item["backup"]).read_bytes(), old)
            self.assertTrue(target.read_bytes().startswith(b"# User rules\r\n"))
            self.assertTrue(target.read_bytes().endswith(b"\r\n# Keep this\r\n"))
            self.assertEqual(update_instructions(repo, {"AGENTS.md": template}, True)["files"][0]["status"], "unchanged")

    def test_unmarked_custom_file_and_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            path = repo / "AGENTS.md"
            path.write_text("# My project\n")
            self.assertEqual(update_instructions(repo, {"AGENTS.md": "new"}, True)["files"][0]["status"], "preserved_custom")
            self.assertEqual(path.read_text(), "# My project\n")
            try:
                (repo / "CLAUDE.md").symlink_to(path)
            except OSError:
                return
            self.assertEqual(update_instructions(repo, {"CLAUDE.md": "new"}, True)["files"][0]["status"], "preserved_symlink")
            self.assertEqual(path.read_text(), "# My project\n")

    def test_legacy_normalization_keeps_custom_edits_out(self):
        sample = "# Old CAO\n\n```\n{{CAO_COMMAND}}\n```\nUse {{CAO_COMMAND}} bootstrap --host <host>\n"
        digest = hashlib.sha256(sample.encode()).hexdigest()
        with patch("scripts.instruction_updates.LEGACY_HASHES", {"AGENTS.md": digest}):
            rendered = sample.replace("{{CAO_COMMAND}}", "/tmp/installed/cao").replace("<host>", "pi")
            self.assertTrue(legacy_template(rendered, "AGENTS.md"))
            self.assertFalse(legacy_template(rendered + "Custom rule\n", "AGENTS.md"))

    def test_nonfile_or_unreadable_instructions_do_not_break_bootstrap_preview(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "AGENTS.md").mkdir()
            out = update_instructions(repo, {"AGENTS.md": "new"})
            self.assertEqual(out["files"][0]["status"], "preserved_nonfile")
            (repo / "CLAUDE.md").write_text("Custom rules")
            with patch.object(Path, "read_bytes", side_effect=PermissionError):
                out = update_instructions(repo, {"CLAUDE.md": "new"})
            self.assertEqual(out["files"][0]["status"], "preserved_unreadable")


if __name__ == "__main__":
    unittest.main()
