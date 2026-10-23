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
            text = (ROOT / name).read_text(encoding="utf-8")
            if len(text.splitlines()) > 100:
                self.assertIn("Contents:", text, name)
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
    def test_contracts_follow_current_operation_and_provider(self):
        work = self.read_work(self.start())
        cases = [
            ("NATIVE_WORKFLOW_SELECTION_REQUIRED", []),
            ("NATIVE_SPEC_PREPARATION_REQUIRED", []),
            ("NATIVE_ROUTE_REQUIRED", ["native_route"]),
            ("NATIVE_ROUTE_RULES_CHANGED", ["native_route"]),
            ("NATIVE_SPEC_APPROVAL_REQUIRED", ["bmad_approval"]),
            ("NATIVE_SPEC_CLARIFICATION_REQUIRED", ["bmad_approval"]),
            ("NATIVE_IMPLEMENTATION_PAUSED", ["bmad_approval"]),
            ("NATIVE_IMPLEMENTATION_HANDOFF_REQUIRED", []),
            ("NATIVE_PLANNING_NOT_FINAL", []),
            ("NATIVE_REVIEW_REQUIRED", ["bmad_oneshot_review"]),
        ]
        for reason, expected in cases:
            with self.subTest(reason=reason):
                nav = cao.agent_guidance(self.repo, work, next_action={"stage": "execution", "reason": reason})
                contracts = [path for path in nav["read_before_action"] if path in {str(ROOT / p) for p in CONTRACT_FILES.values()}]
                self.assertEqual(contracts, [str(ROOT / CONTRACT_FILES[key]) for key in expected])
                self.assertTrue(nav["ready"], nav)
        work["sdd"]["route_decision"] = {"provider": "bmad", "build_route": "dispatch"}
        for reason in ("NATIVE_REVIEW_REQUIRED", "NATIVE_REVIEW_CLASSIFY_REQUIRED", "NATIVE_REVIEW_LOOPBACK_REQUIRED", "NATIVE_REVIEW_TRIAGE_STALE"):
            nav = cao.agent_guidance(self.repo, work, next_action={"stage": "completion", "reason": reason})
            self.assertIn(str(ROOT / CONTRACT_FILES["bmad_dispatch_review"]), nav["read_before_action"])
            self.assertNotIn(str(ROOT / CONTRACT_FILES["bmad_oneshot_review"]), nav["read_before_action"])
            self.assertNotIn(str(ROOT / CONTRACT_FILES["native_route"]), nav["read_before_action"])
        for provider in ("openspec", "speckit"):
            work["sdd"]["provider"] = provider
            nav = cao.agent_guidance(self.repo, work, next_action={"stage": "completion", "reason": "NATIVE_REVIEW_REQUIRED"})
            self.assertFalse(any(Path(p).name.startswith("bmad-") for p in nav["read_before_action"]))

    def test_acceptance_loads_shared_contract_then_only_declared_mode(self):
        work = self.read_work(self.start())
        current = {"stage": "completion", "reason": "SDD acceptance is not configured"}
        nav = cao.agent_guidance(self.repo, work, next_action=current)
        self.assertIn(str(ROOT / CONTRACT_FILES["acceptance"]), nav["read_before_action"])
        self.assertNotIn(str(ROOT / CONTRACT_FILES["acceptance_command"]), nav["read_before_action"])
        self.assertNotIn(str(ROOT / CONTRACT_FILES["acceptance_skill"]), nav["read_before_action"])
        for provider in PROVIDER_FILES:
            work["sdd"]["provider"] = provider
            for mode, other in (("command", "skill"), ("skill", "command")):
                work["sdd"]["acceptance"] = {"declaration": {"executor": {"mode": mode, "entrypoint": "native-acceptance"}}}
                skill = {"name": "native-acceptance", "rel_path": "native/acceptance/SKILL.md"}
                with patch("scripts.cao_guidance.workflow_skill_identity", return_value=skill["name"]), \
                     patch("scripts.cao_guidance.provider_native_skills", return_value=[skill]):
                    nav = cao.agent_guidance(self.repo, work, next_action=current)
                self.assertIn(str(ROOT / CONTRACT_FILES["acceptance_" + mode]), nav["read_before_action"])
                self.assertNotIn(str(ROOT / CONTRACT_FILES["acceptance_" + other]), nav["read_before_action"])
                self.assertEqual(str(self.repo / skill["rel_path"]) in nav["read_before_action"], mode == "skill")

    def test_setup_priority_does_not_load_pending_native_contracts(self):
        work = self.read_work(self.start())
        for mode, stage in (("host_restart_required", "startup"), ("verification_setup", "verification_setup")):
            nav = cao.agent_guidance(self.repo, work, mode, next_action={"stage": "execution", "reason": "NATIVE_ROUTE_REQUIRED"})
            self.assertEqual(nav["stage"], stage)
            self.assertEqual(nav["read_before_action"], [str(ROOT / STAGE_FILES[stage])])

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
            with patch("scripts.cao_guidance.package_root_dir", return_value=root):
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
        from scripts.project_context import make_binding
        work = {"id": "fixture-navigation", "project_binding": make_binding(self.repo), "status": "active", "sdd": {}, "execution": {"phase": "review"}, "path_assessment": {}}
        with patch("scripts.cao_guidance.work_sdd_ready", return_value=(True, None)), \
             patch("scripts.cao_tool_policy.verification_baseline_state", return_value={"ready": True}), \
             patch("scripts.cao_guidance.path_assessment_state", return_value={"ready": True}):
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
