"""Regressions grounded in the 2026-10-13 remote Pi transcript."""
import json
from scripts import cao
from tests.repair_helpers import RepairCase


class LogContracts(RepairCase):
    def test_uv_no_cache_native_renderer_is_recognized(self):
        command = "uv run --no-cache /tmp/demo/_bmad/scripts/render_skill.py --project-root /tmp/demo --skill /tmp/demo/.agents/skills/bmad-build"
        self.assertTrue(cao.validate_workflow_entrypoint("bmad", "bmad-build", command)["ok"])
        self.assertFalse(cao.validate_workflow_entrypoint("bmad", "bmad-build", "uv run --unknown echo bmad")["ok"])

    def test_nonpass_reason_prioritizes_stderr(self):
        self.assertEqual(cao.verification_reason("Compiling Java source...\n", "javac: not found\n", "error"), "javac: not found")
        self.assertEqual(cao.verification_reason("Tests passed\n", "warning\n", "passed"), "Tests passed")

    def test_execute_exception_is_audited_and_new_operation_is_distinct(self):
        self.start()
        self.run_cli("sdd", "workflow", "select", "--name", "custom", "--entrypoint", "bmad-custom")
        for _ in range(2):
            rc, out = self.run_cli("sdd", "workflow", "execute", "--command", "custom-runner", "--allow-unverified", "--reason", "Explicit custom integration")
            self.assertEqual(rc, 0, out)
        events = [json.loads(line) for line in (self.repo / ".orchestrator/lifecycle/events.jsonl").read_text().splitlines()]
        exceptions = [e for e in events if e["event"] == "workflow.unverified_exception"]
        self.assertEqual(len(exceptions), 2)
        self.assertNotEqual(exceptions[0]["idempotency_key"], exceptions[1]["idempotency_key"])
        self.assertTrue(all(e["source"] == "cao" for e in exceptions))
        self.assertTrue(all(e["operation"] == "execute" for e in exceptions))

    def test_unknown_skill_workflow_rejected_with_real_candidates(self):
        self.start()
        skill = self.repo / ".agents/skills/bmad-build"
        skill.mkdir(parents=True); (skill / "SKILL.md").write_text("Build instructions")
        rc, out = self.run_cli("sdd", "workflow", "select", "--name", "quick-fix", "--entrypoint", "bmad quick-fix")
        self.assertEqual(rc, 2, out)
        self.assertIn("bmad-build", json.dumps(out))

    def test_different_skill_requires_reselection_even_with_exception(self):
        self.start()
        for name in ("bmad-build", "bmad-review"):
            folder = self.repo / ".agents/skills" / name
            folder.mkdir(parents=True); (folder / "SKILL.md").write_text(name)
        rc, out = self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "invoke skill bmad-build")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("sdd", "workflow", "execute", "--command", "invoke skill bmad-review", "--allow-unverified", "--reason", "Different skill")
        self.assertEqual(rc, 2, out)
        self.assertIn("select", out["next"])

    def test_active_reanchor_does_not_claim_completed(self):
        self.start()
        rc, out = self.run_cli("reopen", "--reanchor", "--reason", "Reviewed standard")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("marked completed", out["work_item"]["next_action"]["instruction"])

    def test_exit_is_readonly_but_redirected_exit_is_not(self):
        self.assertTrue(cao.readonly_shell("exit 0"))
        self.assertFalse(cao.readonly_shell("exit 0 > .orchestrator/config.json"))

    def test_uninstalled_explicit_skill_cannot_be_selected(self):
        self.start()
        rc, out = self.run_cli("sdd", "workflow", "select", "--name", "missing", "--entrypoint", "invoke skill bmad-missing")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_SKILL_REQUIRED")
        self.assertEqual(out["skills"], [])

    def test_unverified_old_workflow_cannot_claim_a_different_installed_skill(self):
        self.start()
        folder = self.repo / ".agents/skills/bmad-build"
        folder.mkdir(parents=True); (folder / "SKILL.md").write_text("Build instructions")
        rc, out = self.run_cli("sdd", "workflow", "select", "--name", "quick-fix", "--entrypoint", "bmad quick-fix", "--allow-unverified", "--reason", "Legacy entry")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("sdd", "workflow", "execute", "--command", "invoke skill bmad-build")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_WORKFLOW_IDENTITY_CHANGED")
