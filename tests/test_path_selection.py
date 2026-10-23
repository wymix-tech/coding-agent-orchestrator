import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from tests.helpers import configure_verification

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo, *args, input_text=None):
    result = subprocess.run(
        [sys.executable, str(CAO), *args], cwd=repo, text=True,
        capture_output=True, input=input_text, timeout=20,
    )
    try:
        payload = json.loads(result.stdout)
    except Exception:
        payload = {"raw": result.stdout}
    if args and args[0] == "init" and result.returncode == 0:
        from tests.helpers import provision_fixture_background
        provision_fixture_background(repo)
    return result.returncode, payload, result.stderr


class PathSelectionTests(unittest.TestCase):
    def init_repo(self, provider="openspec"):
        td = tempfile.TemporaryDirectory()
        repo = pathlib.Path(td.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
        if provider == "openspec":
            (repo / "openspec").mkdir()
            (repo / "openspec/config.yaml").write_text("context: test\n", encoding="utf-8")
        elif provider == "bmad":
            (repo / "_bmad").mkdir()
        elif provider == "speckit":
            (repo / ".specify").mkdir()
        (repo / "requirements").mkdir()
        (repo / "requirements/change.md").write_text("Change the login behavior", encoding="utf-8")
        rc, _, _ = run(repo, "init")
        self.assertEqual(rc, 0)
        configure_verification(repo)
        rc, _, _ = run(repo, "start", "--requirement", "requirements/change.md")
        self.assertEqual(rc, 0)
        return td, repo

    def assess(self, repo, profile, reason="Clear and bounded scope", *risk_flags, questions=()):
        args = ["path", "assess", "--profile", profile, "--reason", reason]
        for flag in risk_flags:
            args.extend(["--risk-flag", flag])
        for question in questions:
            args.extend(["--question", question])
        return run(repo, *args)

    def test_light_path_stores_minimum_assessment_and_provider_guidance(self):
        td, repo = self.init_repo("openspec")
        with td:
            rc, out, _ = self.assess(repo, "light")
            self.assertEqual(rc, 0, out)
            assessment = out["path_assessment"]
            self.assertEqual(assessment["requested_profile"], "light")
            self.assertEqual(assessment["profile"], "light")
            self.assertEqual(assessment["required_gates"], ["targeted_verification"])
            self.assertIn("OpenSpec", assessment["provider_guidance"])
            rc, show, _ = run(repo, "path", "show")
            self.assertEqual(rc, 0)
            self.assertTrue(show["state"]["ready"])

    def test_hard_risk_flag_upgrades_requested_light_to_comprehensive(self):
        td, repo = self.init_repo()
        with td:
            rc, out, _ = self.assess(repo, "light", "Authentication boundary is affected", "security")
            self.assertEqual(rc, 0, out)
            assessment = out["path_assessment"]
            self.assertEqual(assessment["requested_profile"], "light")
            self.assertEqual(assessment["profile"], "comprehensive")
            self.assertIn("security_review", assessment["required_gates"])
            self.assertIn("security_verification", assessment["required_gates"])
            self.assertTrue(assessment["escalation_reasons"])

    def test_contract_and_multimodule_flags_upgrade_to_standard(self):
        td, repo = self.init_repo()
        with td:
            rc, out, _ = self.assess(repo, "light", "Changes API contract across modules", "external-contract", "multi-module")
            self.assertEqual(rc, 0, out)
            assessment = out["path_assessment"]
            self.assertEqual(assessment["profile"], "standard")
            self.assertIn("contract_review", assessment["required_gates"])
            self.assertIn("integration_verification", assessment["required_gates"])

    def test_major_ambiguity_blocks_mutation_until_reassessment(self):
        td, repo = self.init_repo()
        with td:
            rc, out, _ = self.assess(repo, "light", "Expected behavior is unclear", "major-ambiguity", questions=("Should login failure revoke the refresh token?",))
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["path_assessment"]["profile"], "comprehensive")
            self.assertEqual(out["next_action"]["type"], "human")
            event = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": "src.py", "content": "x"}}
            rc, blocked, _ = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(event))
            self.assertEqual(rc, 0)
            self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
            rc, cleared, _ = self.assess(repo, "standard", "User clarified expected behavior")
            self.assertEqual(rc, 0, cleared)
            self.assertTrue(cleared["path_assessment"]["profile"] == "standard")
            rc, allowed, _ = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(event))
            self.assertEqual(rc, 0)
            self.assertNotEqual(allowed.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_open_questions_cannot_silently_leave_the_path_open(self):
        td, repo = self.init_repo()
        with td:
            rc, out, _ = self.assess(
                repo, "light", "Expected behavior is unclear",
                questions=("Should a failed login revoke existing refresh tokens?",),
            )
            self.assertEqual(rc, 0, out)
            self.assertIn("major_ambiguity", out["path_assessment"]["risk_flags"])
            self.assertEqual(out["next_action"]["type"], "human")

    def test_code_mutation_requires_current_path_assessment(self):
        """An accepted baseline alone cannot authorize business implementation."""
        td, repo = self.init_repo()
        with td:
            event = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": "src.py", "content": "x"}}
            rc, out, _ = run(repo, "host-event", "--host", "codex", input_text=json.dumps(event))
            self.assertEqual(rc, 0)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_cao_path_assess_shell_command_is_not_mistaken_for_product_code_mutation(self):
        td, repo = self.init_repo()
        with td:
            event = {
                "event": "pre_tool", "tool_name": "bash",
                "tool_input": {"command": 'cao path assess --profile light --reason "Clear local change"'},
            }
            rc, out, _ = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(event))
            self.assertEqual(rc, 0)
            self.assertNotEqual(out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

            chained = {
                "event": "pre_tool", "tool_name": "bash",
                "tool_input": {"command": 'cao path assess --profile light --reason "Clear local change" && rm -rf src'},
            }
            rc, out, _ = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(chained))
            self.assertEqual(rc, 0)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_assessment_becomes_stale_when_requirement_changes(self):
        td, repo = self.init_repo()
        with td:
            self.assess(repo, "light")
            req = repo / "requirements/change.md"
            req.write_text("Changed requirement", encoding="utf-8")
            rc, out, _ = run(repo, "path", "show")
            self.assertEqual(rc, 0)
            self.assertFalse(out["state"]["ready"])
            self.assertEqual(out["state"]["reason"], "requirement changed since work started")
            rc, out, _ = self.assess(repo, "light", "Reassess new requirement")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "REQUIREMENT_CHANGED")

    def test_manual_profile_downgrade_cannot_bypass_a_hard_risk_minimum(self):
        td, repo = self.init_repo()
        with td:
            rc, out, _ = self.assess(repo, "light", "Authentication boundary", "security")
            self.assertEqual(rc, 0, out)
            work_path = repo / ".orchestrator" / "work" / f"{out['work_item']['id']}.json"
            work = json.loads(work_path.read_text(encoding="utf-8"))
            work["path_assessment"]["profile"] = "light"
            work_path.write_text(json.dumps(work), encoding="utf-8")
            rc, shown, _ = run(repo, "path", "show")
            self.assertEqual(rc, 0, shown)
            self.assertFalse(shown["state"]["ready"])
            self.assertIn("violates a required risk minimum", shown["state"]["reason"])

    def test_done_requires_path_assessment_but_requirement_change_reason_wins(self):
        td, repo = self.init_repo()
        with td:
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 2)
            self.assertEqual(out["reason"], "execution path has not been assessed")
            self.assertIn("cao path assess", out["next"])

    def test_provider_guidance_is_mapped_for_all_builtin_sd_ds(self):
        expected = {"openspec": "OpenSpec", "bmad": "BMAD", "speckit": "Spec Kit"}
        for provider, marker in expected.items():
            td, repo = self.init_repo(provider)
            with td:
                rc, out, _ = self.assess(repo, "standard")
                self.assertEqual(rc, 0, out)
                self.assertEqual(out["path_assessment"]["provider"], provider)
                self.assertIn(marker.lower(), out["path_assessment"]["provider_guidance"].lower())


if __name__ == "__main__":
    unittest.main()
