import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import coding_orchestrator as cli
import project_bootstrap
import project_discovery


def git_init(repo: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


class V65BootstrapTests(unittest.TestCase):
    def test_init_creates_project_infrastructure_but_not_fake_work_item(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "README.md").write_text("new project\n", encoding="utf-8")
            result = project_bootstrap.initialize(repo, host="none")
            self.assertEqual("READY_FOR_INTAKE", result["status"])
            self.assertTrue((repo / ".orchestrator/config.yaml").exists())
            self.assertTrue((repo / ".orchestrator/policies/manifest.yaml").exists())
            self.assertTrue((repo / ".orchestrator/enforcement.yaml").exists())
            self.assertTrue((repo / ".orchestrator/session-context.yaml").exists())
            self.assertFalse((repo / ".orchestrator/execution-state.yaml").exists())
            bootstrap = json.loads((repo / ".orchestrator/session/session-bootstrap.json").read_text(encoding="utf-8"))
            self.assertEqual("READY_FOR_INTAKE", bootstrap["status"])

    def test_spring_layered_policy_auto_enables_only_when_structure_is_proven(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "pom.xml").write_text("<dependency>spring-boot-starter-web</dependency><plugin>org.springframework.boot</plugin>", encoding="utf-8")
            for part, name in [("controller","UserController.java"),("service","UserService.java"),("repository","UserRepository.java")]:
                p = repo / "src/main/java/com/acme" / part / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("class X {}\n", encoding="utf-8")
            d = project_discovery.discover(repo)
            self.assertEqual("spring_boot_layered", d["architecture"]["style"])
            result = project_bootstrap.initialize(repo, host="none")
            self.assertTrue(result["policies"]["spring_layered_enabled"])

    def test_spring_boot_alone_does_not_force_classic_layered_policy(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "pom.xml").write_text("<plugin>org.springframework.boot</plugin>", encoding="utf-8")
            p = repo / "src/main/java/com/acme/domain/User.java"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("class User {}\n", encoding="utf-8")
            result = project_bootstrap.initialize(repo, host="none")
            self.assertFalse(result["policies"]["spring_layered_enabled"])
            self.assertIn("SPRING_ARCHITECTURE_NOT_PROVEN", {x["code"] for x in result["warnings"]})

    def test_multiple_sdd_authorities_require_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "openspec").mkdir()
            b = repo / "_bmad"; b.mkdir(); (b / "sprint-status.yaml").write_text("status: active\n", encoding="utf-8")
            result = project_bootstrap.initialize(repo, host="none")
            self.assertEqual("ACTION_REQUIRED", result["status"])
            self.assertIn("SDD_AUTHORITY_REQUIRED", {x["code"] for x in result["unresolved"]})

    def test_explicit_sdd_resolves_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "openspec").mkdir(); (repo / "_bmad").mkdir()
            (repo / "_bmad/sprint-status.yaml").write_text("status: active\n", encoding="utf-8")
            result = project_bootstrap.initialize(repo, host="none", sdd="openspec")
            self.assertEqual("READY_FOR_INTAKE", result["status"])
            self.assertEqual("openspec", result["sdd"]["provider"])

    def test_init_is_idempotent_and_preserves_project_policy_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none")
            manifest = repo / ".orchestrator/policies/manifest.yaml"
            manifest.write_text("version: 99\ncustom: true\n", encoding="utf-8")
            project_bootstrap.initialize(repo, host="none")
            self.assertIn("custom: true", manifest.read_text(encoding="utf-8"))

    def test_doctor_respects_explicit_sdd_resolution_in_project_config(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "openspec").mkdir(); (repo / "_bmad").mkdir()
            (repo / "_bmad/sprint-status.yaml").write_text("status: active\n", encoding="utf-8")
            project_bootstrap.initialize(repo, host="none", sdd="openspec")
            result = cli._doctor(repo)
            check = [x for x in result["checks"] if x["name"] == "sdd_authority"][0]
            self.assertEqual("PASS", check["status"])
            self.assertIn("openspec", check["detail"])

    def test_status_before_first_intake_is_ready_for_intake(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); project_bootstrap.initialize(repo, host="none")
            args = cli.build_parser().parse_args(["--repo", str(repo), "status"]); args.repo = repo
            code, result, _ = cli.cmd_status(args)
            self.assertEqual(0, code); self.assertEqual("READY_FOR_INTAKE", result["status"])

    def test_doctor_after_init_does_not_fail_only_because_no_active_work_exists(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); project_bootstrap.initialize(repo, host="none")
            result = cli._doctor(repo)
            self.assertNotEqual("ERROR", result["status"])
            state = [x for x in result["checks"] if x["name"] == "execution_state"][0]
            self.assertEqual("INFO", state["status"])

    def test_safe_auto_host_uses_current_agent_runtime_not_binary_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            fake = {"pi": "/usr/local/bin/pi", "claude": None, "codex": None}
            selection = {
                "requested": "auto", "host": "codex", "hosts": ["codex"],
                "source": "env:CODEX_THREAD_ID", "confidence": "high",
                "fallback": False, "evidence": ["CODEX_THREAD_ID is set"],
            }
            with mock.patch("shutil.which", side_effect=lambda name: fake.get(name)), \
                 mock.patch("host_runtime.select_hosts", return_value=selection):
                d = project_discovery.discover(repo)
                self.assertIn("pi", d["hosts"]["names"], "binary inventory remains diagnostic")
                result = project_bootstrap.initialize(repo, host="auto")
            self.assertEqual(["codex"], sorted(result["hosts"]["installed"]))
            self.assertTrue((repo / ".codex/hooks.json").exists())
            self.assertFalse((repo / ".pi/extensions/coding-orchestrator.ts").exists())

    def test_safe_auto_host_falls_back_to_claude_code(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            selection = {
                "requested": "auto", "host": "claude-code", "hosts": ["claude-code"],
                "source": "default_fallback", "confidence": "fallback",
                "fallback": True, "evidence": ["no reliable current-agent runtime signal detected"],
            }
            with mock.patch("host_runtime.select_hosts", return_value=selection):
                result = project_bootstrap.initialize(repo, host="auto")
            self.assertIn("claude-code", result["hosts"]["installed"])
            self.assertTrue((repo / ".claude/settings.json").exists())
            self.assertTrue((repo / "CLAUDE.md").exists())
            self.assertIn("HOST_RUNTIME_FALLBACK", {x["code"] for x in result["warnings"]})


    def test_status_before_bootstrap_is_unbootstrapped(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            args = cli.build_parser().parse_args(["--repo", str(repo), "status"]); args.repo = repo
            code, result, _ = cli.cmd_status(args)
            self.assertEqual(0, code)
            self.assertEqual("UNBOOTSTRAPPED", result["status"])
            self.assertEqual("safe_auto_init", result["next_action"])

    def test_intake_self_bootstraps_when_config_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            p = repo / "src/main/java/com/acme/App.java"; p.parent.mkdir(parents=True); p.write_text("class App {}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True); subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            fixture = ROOT / "examples/cbm-detect-changes-fixture.json"
            args = cli.build_parser().parse_args(["--repo", str(repo), "intake", "Add a small behavior change", "--cbm-fixture", str(fixture)])
            args.repo = repo
            code, result, _ = cli.cmd_intake(args)
            self.assertTrue((repo / ".orchestrator/config.yaml").exists())
            self.assertTrue((repo / ".orchestrator/execution-state.yaml").exists())
            self.assertTrue(result["state_created"])
            self.assertIn(code, {0, 1, 3})

    def test_first_intake_creates_real_execution_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            p = repo / "src/main/java/com/acme/App.java"; p.parent.mkdir(parents=True); p.write_text("class App {}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True); subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            project_bootstrap.initialize(repo, host="none")
            fixture = ROOT / "examples/cbm-detect-changes-fixture.json"
            args = cli.build_parser().parse_args(["--repo", str(repo), "intake", "Add a small behavior change", "--cbm-fixture", str(fixture)])
            args.repo = repo
            code, result, _ = cli.cmd_intake(args)
            self.assertTrue((repo / ".orchestrator/execution-state.yaml").exists())
            self.assertTrue(result["state_created"])
            self.assertIn(code, {0, 1, 3})
            import action_guard
            import execution_state_manager as sm
            state = sm._load(repo / ".orchestrator/execution-state.yaml")
            evidence = action_guard.collect_evidence(repo, state)
            required = {item["gate_name"] for item in evidence["verification_plan"]["items"]
                        if item.get("required_by_impact")}
            self.assertTrue(required, "fixture must exercise required semantic-impact gates")
            self.assertTrue(required <= state["quality_gates"].keys())
            for name in required:
                self.assertTrue(state["quality_gates"][name]["required"])
                self.assertEqual("pending", state["quality_gates"][name]["status"])
            self.assertTrue(evidence["repository_fresh"])
            self.assertTrue(evidence["authority_fresh"])
            self.assertTrue(evidence["context_fresh"], evidence["stale_context_sources"])
            if result["status"] == "NEEDS_EVIDENCE":
                decision = action_guard.evaluate(state, "advance", target_phase="implementation", evidence=evidence)
                self.assertIn("DECISION_NOT_CLASSIFIED", decision["reason_codes"])



if __name__ == "__main__":
    unittest.main()
