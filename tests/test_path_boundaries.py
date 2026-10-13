import importlib.util
import tempfile
from pathlib import Path

from repair_helpers import RepairCase
from scripts import cao


class BoundaryTests(RepairCase):
    def setUp(self):
        super().setUp()
        self.wid = self.start()

    def pre_tool(self, tool, payload):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": "pre_tool", "session_id": "guard", "tool_name": tool, "tool_input": payload})
        self.assertEqual(rc, 0, out)
        return out

    def test_requirement_traversal_cannot_edit_config(self):
        out = self.pre_tool("write", {"path": ".orchestrator/requirements/../config.json", "content": "{}"})
        self.assertEqual(out["decision"], "deny", out)

    def test_state_symlink_aliases_cannot_bypass_guard(self):
        alias = self.repo / "config-alias.json"
        alias.symlink_to(self.repo / ".orchestrator/config.json")
        out = self.pre_tool("write", {"path": "config-alias.json", "content": "{}"})
        self.assertEqual(out["decision"], "deny", out)
        requirement_alias = self.repo / ".orchestrator/requirements/alias.json"
        requirement_alias.symlink_to(self.repo / ".orchestrator/config.json")
        out = self.pre_tool("write", {"path": str(requirement_alias), "content": "{}"})
        self.assertEqual(out["decision"], "deny", out)

    def test_protected_link_itself_stays_protected_when_target_is_outside(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.repo / ".orchestrator/work/escape.json"
            link.symlink_to(Path(outside) / "external.json")
            out = self.pre_tool("delete", {"path": str(link)})
            self.assertEqual(out["decision"], "deny", out)

    def test_nonexistent_parent_traversal_is_protected(self):
        out = self.pre_tool("write", {"path": ".orchestrator/requirements/new/../../work/fake.json", "content": "{}"})
        self.assertEqual(out["decision"], "deny", out)

    def test_patch_all_files_and_move_destinations_are_checked(self):
        patches = [
            "*** Begin Patch\n*** Update File: src/main.py\n+x\n*** Add File: .orchestrator/requirements/../index.json\n+{}\n*** End Patch",
            "*** Begin Patch\n*** Update File: src/main.py\n*** Move to: .orchestrator/work/fake.json\n+x\n*** End Patch",
            "*** Begin Patch\n*** Delete File: .orchestrator/config.json\n*** End Patch",
        ]
        for patch in patches:
            for payload in (patch, {"patch": patch}, {"params": {"patch": patch}}):
                with self.subTest(payload=payload):
                    self.assertEqual(self.pre_tool("apply_patch", payload)["decision"], "deny")

    def test_structured_targets_are_not_silently_truncated(self):
        files = [{"path": f"src/{n}.py"} for n in range(30)] + [{"path": ".orchestrator/index.json"}]
        self.assertEqual(self.pre_tool("edit", {"files": files, "new_string": "x"})["decision"], "deny")
        nested = {"path": ".orchestrator/config.json", "content": "{}"}
        for _ in range(8):
            nested = {"params": nested}
        self.assertEqual(self.pre_tool("write", nested)["decision"], "deny")

    def test_shell_redirect_and_compound_mutation_are_protected(self):
        for command in (
            "cat /dev/null > .orchestrator/config.json",
            "cat /dev/null>.orchestrator/config.json",
            "ls; rm .orchestrator/index.json",
            "sed -n '1p' /dev/null; echo '{}' > .orchestrator/config.json",
            "cao status && rm .orchestrator/work/fake.json",
            "ls\nrm .orchestrator/index.json",
            "ls;\nrm .orchestrator/index.json",
            "cd .orchestrator; rm config.json",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.pre_tool("bash", {"command": command})["decision"], "deny")

    def test_string_shell_payload_is_checked(self):
        out = self.pre_tool("bash", "cat /dev/null > .orchestrator/config.json")
        self.assertEqual(out["decision"], "deny", out)

    def test_readonly_and_governance_commands_remain_allowed(self):
        for command in (
            "cat .orchestrator/config.json",
            "ls; git status",
            "cd .orchestrator; cat config.json",
            "cao checkpoint --summary 'Do not edit .orchestrator/config.json'",
            "cao checkpoint --summary '.orchestrator/config.json' --next '(review) then verify'",
            f"python3.11 {cao.__file__} path assess --profile light --reason 'small change'",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.pre_tool("bash", {"command": command})["decision"], "allow")

    def test_requirement_document_and_product_writes_are_allowed(self):
        for path in (".orchestrator/requirements/new/doc.md", "docs/my.orchestrator/config.json"):
            with self.subTest(path=path):
                self.assertEqual(self.pre_tool("write", {"path": path, "content": "text"})["decision"], "allow")

    def test_shell_native_output_is_not_confused_with_echo_data_or_read_input(self):
        out = self.pre_tool("bash", {"command": "echo text > _bmad-output/story.md"})
        self.assertEqual(out["decision"], "allow")
        self.assertNotIn("additional_context", out)
        out = self.pre_tool("bash", {"command": "cat /dev/null > _bmad-output/story.md"})
        self.assertEqual(out["decision"], "allow")
        self.assertNotIn("additional_context", out)

    def test_done_lists_concrete_pending_gates(self):
        self.run_cli("path", "assess", "--profile", "standard", "--reason", "review needed")
        self.execute_workflow()
        (self.repo / "_bmad-output/story.md").write_text("# story\n", encoding="utf-8")
        self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md", "--no-plan")
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("pending_gates"), ["scope_review", "implementation_review", "sdd_acceptance"])
        self.assertIn("cao gate satisfy", out["next"])

    def test_mixed_product_and_native_targets_keep_product_reminder(self):
        out = self.pre_tool("edit", {"files": [{"path": "_bmad-output/story.md"}, {"path": "src/main.py"}], "new_string": "x"})
        self.assertEqual(out["decision"], "allow")
        self.assertIn("sdd spec bind", out.get("additional_context", ""))

    def test_dynamic_shell_is_not_claimed_to_be_isolated(self):
        out = self.pre_tool("bash", {"command": "printf x > \"$TARGET\""})
        self.assertEqual(out["decision"], "allow")
        self.assertIn("dynamic", out.get("additional_context", "").lower())
        out = self.pre_tool("bash", {"command": "sh -c 'echo x'"})
        self.assertEqual(out["decision"], "allow")
        self.assertIn("dynamic", out.get("additional_context", "").lower())

    def execute_workflow(self):
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        self.run_cli("sdd", "workflow", "execute", "--command", "bmad-build")
        (self.repo / "_bmad-output").mkdir(exist_ok=True)

    def test_provider_traversal_is_rejected_even_with_exception_flag(self):
        self.execute_workflow()
        (self.repo / "src.py").write_text("x = 1\n", encoding="utf-8")
        for flags in ([], ["--allow-unverified", "--reason", "explicit exception"]):
            with self.subTest(flags=flags):
                rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/../src.py", "--no-plan", *flags)
                self.assertEqual(rc, 2, out)
                self.assertEqual(out.get("error"), "NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS")

    def test_provider_symlink_cannot_make_product_artifact_native(self):
        self.execute_workflow()
        target = self.repo / "src.py"
        target.write_text("x = 1\n", encoding="utf-8")
        (self.repo / "_bmad-output/alias.py").symlink_to(target)
        rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/alias.py", "--no-plan")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS")

    def test_absolute_owned_artifact_is_allowed_and_canonicalized(self):
        self.execute_workflow()
        target = self.repo / "_bmad-output/story.md"
        target.write_text("# story\n", encoding="utf-8")
        rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", target, "--no-plan")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["workflow"]["artifacts"], ["_bmad-output/story.md"])

    def test_done_rechecks_provider_membership_after_symlink_retarget(self):
        self.run_cli("path", "assess", "--profile", "standard", "--reason", "ownership check")
        self.execute_workflow()
        target = self.repo / "_bmad-output/story.md"
        target.write_text("# same bytes\n", encoding="utf-8")
        self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md", "--no-plan")
        target.unlink()
        product = self.repo / "source.md"
        product.write_text("# same bytes\n", encoding="utf-8")
        target.symlink_to(product)
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertIn("outside provider", out["reason"])

    def test_path_core_and_patch_parser(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.path_guard"), "path_guard is missing")
        from scripts import path_guard
        self.assertEqual(path_guard.repo_file(self.repo, "src/../new/file.txt"), self.repo.resolve() / "new/file.txt")
        self.assertFalse(path_guard.inside(self.repo / "../outside", self.repo))
        with self.assertRaisesRegex(ValueError, "PATH_OUTSIDE_REPOSITORY"):
            path_guard.repo_file(self.repo, "../outside")
        text = "*** Add File: a.py\n*** Update File: b.py\n*** Move to: c.py\n*** Delete File: d.py\n"
        self.assertEqual(path_guard.patch_targets(text), ["a.py", "b.py", "c.py", "d.py"])
