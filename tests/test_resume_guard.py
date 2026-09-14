"""Resume must not look like a requirement change, and a blocked state must be recoverable.

These regressions come from a real session: an agent resumed an implementation-stage work
item with "继续", re-ran intake, was told the requirement changed, used --revise-current,
and silently lost the implementation phase, readiness, gates, and progress. The resulting
`advance_native_sdd_to_ready` had no legal CLI path, so the agent deadlocked.
"""
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

import yaml

import action_guard
import coding_orchestrator as cli
import execution_state_manager as sm
import project_bootstrap
import requirement_identity
import tool_actions

SPEC = "docs/spec.md"


def git_init(repo: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def bootstrap(repo: pathlib.Path) -> None:
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    project_bootstrap.initialize(repo, host="none", sdd="generic")


def write_spec(repo: pathlib.Path, body: str = "# Spec\n\nLogin with JWT.\n") -> pathlib.Path:
    spec = repo / SPEC
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body, encoding="utf-8")
    return spec


def start_implementation(repo: pathlib.Path, request: str, title: str = "Spring Boot JWT Login") -> dict:
    """Create an ACTIVE work item that is already past planning."""
    state_path = repo / ".orchestrator" / "execution-state.yaml"
    req_id = requirement_identity.stable_requirement_id(provider="generic", source_path=SPEC)
    req_rev = requirement_identity.revision_id(request)
    state = sm.create_state(
        "change-test", title, "DEEP", provider="generic", authority_mode="orchestrator",
        requirement_id=req_id, requirement_revision=req_rev, requirement_source_ref=SPEC,
    )
    sm.initialize(state_path, state, "test")
    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    doc["phase"] = "implementation"
    doc["status"] = "in_progress"
    doc["readiness"] = {"behavior_change": True, "sdd_ready": True,
                        "acceptance_criteria_present": True,
                        "implementation_tasks_complete": False, "acceptance_satisfied": False}
    doc["work_item"]["progress"] = {"completed": 2, "total": 5}
    state_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    requirement_identity.record(
        repo, requirement_id=req_id, revision_id=req_rev, work_item_id="change-test",
        provider="generic", source_path=SPEC, status="active",
        source_revision=requirement_identity.source_revision_id(repo, SPEC),
    )
    return doc


def run_intake(repo: pathlib.Path, request: str, *extra: str) -> tuple[int, dict, str]:
    argv = ["--repo", str(repo), "intake", request, "--sdd", "generic",
            "--sdd-ref", str(repo / SPEC), "--actor", "test", *extra]
    args = cli.build_parser().parse_args(argv)
    args.repo = args.repo.resolve()
    with mock.patch.object(cli.semantic_intake_pipeline, "main", return_value=0) as pipeline:
        code, result, text = args.func(args)
    result["_pipeline_calls"] = pipeline.call_count
    return code, result, text


class ResumeMustNotReviseRequirement(unittest.TestCase):
    def test_rephrased_request_keeps_revision_and_implementation_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            before = start_implementation(repo, "Implement Spring Boot JWT login.")
            code, result, text = run_intake(repo, "Continue implementation for approved Spring Boot JWT Login.")
            self.assertNotEqual(2, code, text)
            self.assertTrue(result["request_rephrased_source_unchanged"], text)
            after = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertEqual(before["work_item"]["requirement_revision"], after["work_item"]["requirement_revision"])
            self.assertEqual("implementation", after["phase"])
            self.assertTrue(after["readiness"]["sdd_ready"])
            self.assertEqual({"completed": 2, "total": 5}, after["work_item"]["progress"])

    def test_changed_source_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            start_implementation(repo, "Implement Spring Boot JWT login.")
            write_spec(repo, "# Spec\n\nLogin with JWT and refresh tokens.\n")

            code, result, text = run_intake(repo, "Implement the revised login spec.")
            self.assertEqual(2, code)
            self.assertEqual("REQUIREMENT_REVISION_CHANGED", result["error"])
            self.assertTrue(result["resets_in_flight_work"])
            self.assertEqual("resume_current_work", result["next_action"])
            self.assertIn("coding-orchestrator start", text)

            code, result, text = run_intake(repo, "Implement the revised login spec.", "--revise-current")
            self.assertEqual(2, code)
            self.assertEqual("REVISION_RESET_REQUIRES_CONFIRMATION", result["error"])
            self.assertIn("--confirm-reset", text)
            unchanged = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertEqual("implementation", unchanged["phase"])

            code, result, text = run_intake(repo, "Implement the revised login spec.",
                                            "--revise-current", "--confirm-reset")
            self.assertNotEqual(2, code, text)
            reset = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertEqual("discovery", reset["phase"])
            self.assertFalse(reset["readiness"]["sdd_ready"])
            self.assertEqual({"completed": 0, "total": 0}, reset["work_item"]["progress"])


class CliRecoveryPath(unittest.TestCase):
    def _run(self, repo: pathlib.Path, argv: list[str]) -> tuple[int, dict, str]:
        args = cli.build_parser().parse_args(["--repo", str(repo), *argv])
        args.repo = args.repo.resolve()
        return args.func(args)

    def test_readiness_and_progress_update_through_the_cli(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            start_implementation(repo, "Implement Spring Boot JWT login.")
            code, result, text = self._run(repo, ["readiness", "--key", "sdd_ready", "--value", "false",
                                                  "--evidence-ref", SPEC])
            self.assertEqual(0, code, text)
            state = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertFalse(state["readiness"]["sdd_ready"])

            code, result, text = self._run(repo, ["readiness", "--key", "sdd_ready", "--value", "true",
                                                  "--evidence-ref", SPEC])
            self.assertEqual(0, code, text)
            self.assertTrue(sm._load(repo / ".orchestrator" / "execution-state.yaml")["readiness"]["sdd_ready"])

            code, result, text = self._run(repo, ["progress", "--completed", "3", "--total", "5",
                                                  "--evidence-ref", SPEC])
            self.assertEqual(0, code, text)
            self.assertEqual({"completed": 3, "total": 5},
                             sm._load(repo / ".orchestrator" / "execution-state.yaml")["work_item"]["progress"])

    def test_transition_denial_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            start_implementation(repo, "Implement Spring Boot JWT login.")
            doc = yaml.safe_load((repo / ".orchestrator" / "execution-state.yaml").read_text(encoding="utf-8"))
            doc["readiness"]["sdd_ready"] = False
            doc["phase"] = "discovery"
            (repo / ".orchestrator" / "execution-state.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
            code, result, text = self._run(repo, ["transition", "--phase", "implementation",
                                                  "--status", "in_progress", "--reason", "plan is approved",
                                                  "--evidence-ref", SPEC])
            self.assertEqual(1, code)
            self.assertEqual("TRANSITION_DENIED", result["error"])
            self.assertIn("SDD_NOT_READY", (result["authorization"] or {}).get("reason_codes") or [])
            self.assertTrue(result["recovery"])
            self.assertIn("Recovery:", text)

    def test_recovery_commands_are_classified_as_preparation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            for command in action_guard.RECOVERY_COMMANDS["advance_native_sdd_to_ready"]:
                described = tool_actions.describe(
                    {"tool_name": "Bash", "tool_input": {"command": f"{ROOT / 'coding-orchestrator'} {command.split(' ', 1)[1]}"}},
                    repo)
                self.assertEqual("prepare", described["action"], command)


class DenialsNameTheWayForward(unittest.TestCase):
    def test_sdd_not_ready_carries_recovery_commands(self):
        state = {
            "phase": "discovery", "status": "in_progress", "blocked": False, "blockers": [],
            "flow_profile": "DEEP", "authority": {"mode": "orchestrator"},
            "readiness": {"sdd_ready": False}, "work_item": {"progress": {"completed": 0, "total": 0}},
            "analysis": {}, "enforcement": {}, "review": {"required": False}, "verification": {},
            "quality_gates": {}, "cursor": {},
        }
        result = action_guard.evaluate(state, "advance", target_phase="implementation",
                                       evidence={"decision_status": "CLASSIFIED"})
        self.assertIn("SDD_NOT_READY", result["reason_codes"])
        self.assertEqual("advance_native_sdd_to_ready", result["next_action"])
        self.assertEqual(action_guard.RECOVERY_COMMANDS["advance_native_sdd_to_ready"], result["recovery"])


if __name__ == "__main__":
    unittest.main()
