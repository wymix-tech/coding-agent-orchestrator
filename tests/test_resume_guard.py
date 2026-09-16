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
sys.path.insert(0, str(ROOT / "tests"))

from governance_fixture import attach_fixture_analysis

import yaml

import action_guard
import coding_orchestrator as cli
import fact_resolver
import execution_state_manager as sm
import project_bootstrap
import repository_snapshot
import requirement_identity
import tool_actions
import evidence_factory

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
    req_rev = requirement_identity.requirement_content_revision(repo, SPEC)["source_revision"]
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


def resolution_document(facts: dict, repo: pathlib.Path | None = None,
                        work_item: dict | None = None) -> dict:
    """Produce bounded, authoritative fixture answers only for unresolved facts."""
    work_item = work_item or {}
    work_item_id = str(work_item.get("id") or "") or None
    revision = work_item.get("requirement_revision")
    values = {
        "ambiguity.goal_explicit": True,
        "ambiguity.acceptance_criteria_explicit": True,
        "ambiguity.boundaries_explicit": True,
        "novelty.exact_repo_precedent": True,
        "complexity.architecture_decision_required": True,
        "complexity.concurrency_or_transaction": True,
    }
    resolutions = []
    for path in (facts.get("extraction") or {}).get("resolution_queue") or []:
        value = 1 if path in fact_resolver.INT_PATHS else values.get(path, False)
        entry = {
            "path": path, "value": value, "source_type": "fixture", "source": "resume guard test",
            "evidence": "controlled acceptance fixture", "strength": "authoritative",
        }
        # The evidence is checked against the work item it is submitted for, so the fixture
        # says which one that is instead of letting a default name decide it.
        if work_item_id:
            entry["work_item_id"] = work_item_id
        if revision:
            entry["requirement_revision"] = revision
        # A negative fact is only worth the search that found nothing, never its own title.
        if value is False and repo is not None:
            entry["negative_proof"] = evidence_factory.negative_proof_entry(repo, path)
            # "No conflicting requirement" is not the absence of a word. A semantic section
            # needs a source produced for this exact predicate, so the fixture carries one
            # instead of asking a search to decide it.
            if path.split(".")[0] in {"risk", "ambiguity", "novelty"}:
                entry["evidence_record"] = evidence_factory.fact_evidence(
                    repo, path, work_item_id=work_item_id or "W-1", requirement_revision=revision)
        resolutions.append(entry)
    return {"source": "resume guard test", "resolutions": resolutions}


class ResumeMustNotReviseRequirement(unittest.TestCase):
    def test_real_intake_rephrased_resume_reuses_current_analysis_and_verification(self):
        """No semantic pipeline mock: a rephrase must keep the exact approved evidence."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "app.py").write_text("def login(): return True\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            spec = repo / "requirements.md"
            spec.write_text("# Login\n\nImplement login.\n\n## Acceptance Criteria\nUsers can log in.\n", encoding="utf-8")
            project_bootstrap.initialize(repo, host="none", activation=False)
            # Who may approve is project configuration, published before the analysis that has
            # to stay valid: writing it later would legitimately invalidate that analysis.
            evidence_factory.publish_evidence_policy(repo)
            fixture = repo / "cbm-fixture.json"
            fixture.write_text(json.dumps({
                "project": repo.name, "changed_files": [{"path": "app.py"}],
                "changed_symbols": [{"qualified_name": "app.login", "label": "Function", "file_path": "app.py",
                                     "module": "app", "service": "app", "project": repo.name}],
                "impacted_symbols": [], "edges": [], "risk_classification": "LOW",
            }), encoding="utf-8")

            # First real intake produces a fact scaffold; the second attaches real classified analysis.
            args = cli.build_parser().parse_args(["--repo", str(repo), "intake", "Initial login feature",
                                                   "--sdd", "generic", "--sdd-ref", str(spec),
                                                   "--cbm-fixture", str(fixture)])
            args.repo = repo
            self.assertEqual(1, args.func(args)[0])
            state_path = repo / ".orchestrator/execution-state.yaml"
            first_state = sm._load(state_path)
            facts = json.loads((repo / first_state["analysis"]["work_facts_ref"]).read_text(encoding="utf-8"))
            # Kept outside the analyzed content: a resolution set is an input, not project source.
            resolutions = pathlib.Path(tempfile.mkdtemp()) / "resolutions.json"
            resolutions.write_text(json.dumps(
                resolution_document(facts, repo, work_item=first_state.get("work_item") or {})),
                encoding="utf-8")
            args = cli.build_parser().parse_args(["--repo", str(repo), "intake", "--request-file", str(spec),
                                                   "--sdd", "generic", "--sdd-ref", str(spec),
                                                   "--resolutions", str(resolutions), "--cbm-fixture", str(fixture)])
            args.repo = repo
            code, result, text = args.func(args)
            self.assertEqual(0, code, text)
            self.assertEqual("CLASSIFIED", result["status"])

            state = sm._load(state_path)
            for key in ("behavior_change", "acceptance_criteria_present", "sdd_ready"):
                evidence_factory.establish_readiness(state_path, key, repo=repo)
            state = sm._load(state_path)
            state = sm.transition(state_path, "implementation", "in_progress", "test", "start", state["revision"])
            state = sm.record_gate(state_path, "unit", True, "passed", "test",
                                   evidence_ref=evidence_factory.result_document(repo, "unit-results.json", target="gate:unit"),
                                   expected_revision=state["revision"])
            state = sm.record_verification(state_path, "passed", "test", state["execution_snapshot_id"],
                                           evidence_factory.result_document(repo, "final-verification.json", target="verification"),
                                           state["revision"])
            before = sm._load(state_path)

            args = cli.build_parser().parse_args(["--repo", str(repo), "intake", "Continue approved login implementation.",
                                                   "--sdd", "generic", "--sdd-ref", str(spec), "--cbm-fixture", str(fixture)])
            args.repo = repo
            code, result, text = args.func(args)
            self.assertEqual(0, code, text)
            self.assertEqual("RESUMED", result["status"])
            self.assertTrue(result["analysis_reused"])
            after = sm._load(state_path)
            self.assertEqual(before["analysis"]["evidence_snapshot_id"], after["analysis"]["evidence_snapshot_id"])
            self.assertEqual(before["verification"], after["verification"])
            self.assertEqual(before["quality_gates"], after["quality_gates"])
            self.assertEqual(before["work_item"]["progress"], after["work_item"]["progress"])

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

    def test_rephrased_request_refreshes_stale_analysis_without_revision_reset(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            before = start_implementation(repo, "Implement Spring Boot JWT login.")
            # This fixture intentionally has no attached current analysis. A request rephrase
            # must therefore refresh intake, while preserving the requirement and in-flight work.
            code, result, text = run_intake(repo, "Continue implementation for approved Spring Boot JWT Login.")
            self.assertNotEqual(2, code, text)
            self.assertTrue(result["request_rephrased_source_unchanged"])
            self.assertEqual(1, result["_pipeline_calls"])
            self.assertIn("Analysis was refreshed", text)
            after = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertEqual(before["work_item"]["requirement_revision"], after["work_item"]["requirement_revision"])
            self.assertEqual(before["work_item"]["progress"], after["work_item"]["progress"])

    def test_changed_source_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            start_implementation(repo, "Implement Spring Boot JWT login.")
            write_spec(repo, "# Spec\n\nLogin with JWT and refresh tokens.\n")

            code, result, text = run_intake(repo, "Implement Spring Boot JWT login.")
            self.assertEqual(2, code)
            self.assertEqual("REQUIREMENT_REVISION_CHANGED", result["error"])
            self.assertTrue(result["source_changed"])
            state_before_confirmation = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertEqual("implementation", state_before_confirmation["phase"])
            self.assertEqual({"completed": 2, "total": 5}, state_before_confirmation["work_item"]["progress"])

            code, result, text = run_intake(repo, "Implement the revised login spec.")
            self.assertEqual(2, code)
            self.assertEqual("REQUIREMENT_REVISION_CHANGED", result["error"])
            self.assertTrue(result["resets_in_flight_work"])
            self.assertEqual("resume_current_work", result["next_action"])
            self.assertIn("--repo", text)
            self.assertIn(" start`", text)

            code, result, text = run_intake(repo, "Implement the revised login spec.", "--revise-current")
            self.assertEqual(2, code)
            self.assertEqual("REVISION_RESET_REQUIRES_CONFIRMATION", result["error"])
            self.assertIn("--confirm-reset", text)
            unchanged = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            self.assertEqual("implementation", unchanged["phase"])

            # An unbound confirmation is not a confirmation: it must name the revision and the
            # state it discards, otherwise it silently authorizes a future state too.
            code, result, text = run_intake(repo, "Implement the revised login spec.",
                                            "--revise-current", "--confirm-reset")
            self.assertEqual(2, code)
            self.assertEqual("REVISION_CONFIRMATION_REQUIRED", result["error"])
            self.assertEqual("implementation", sm._load(repo / ".orchestrator" / "execution-state.yaml")["phase"])

            state_before_confirmation = sm._load(repo / ".orchestrator" / "execution-state.yaml")
            code, result, text = run_intake(
                repo, "Implement the revised login spec.",
                "--revise-current", "--confirm-reset",
                "--confirm-revision", str(state_before_confirmation["work_item"]["requirement_revision"]),
                # Both revisions of the move, and the state it would discard.
                "--confirm-incoming-revision", requirement_identity.source_revision_id(repo, SPEC),
                "--confirm-state-revision", str(state_before_confirmation["revision"]),
                "--confirm-phase", state_before_confirmation["phase"],
                "--confirm-status", state_before_confirmation["status"])
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
            state_path = repo / ".orchestrator" / "execution-state.yaml"
            code, result, text = self._run(repo, ["readiness", "--key", "sdd_ready", "--value", "false",
                                                  "--evidence-ref", SPEC])
            self.assertEqual(0, code, text)
            state = sm._load(state_path)
            self.assertFalse(state["readiness"]["sdd_ready"])

            # The CLI may only be handed evidence that actually verifies.
            sdd = evidence_factory.establish_readiness(state_path, "sdd_ready", repo=repo)["evidence_id"]
            code, result, text = self._run(repo, ["readiness", "--key", "sdd_ready", "--value", "true",
                                                  "--evidence-ref", sdd])
            self.assertEqual(0, code, text)
            self.assertTrue(sm._load(state_path)["readiness"]["sdd_ready"])

            progress = evidence_factory.establish_progress(state_path, repo=repo, completed=3, total=5)["evidence_id"]
            code, result, text = self._run(repo, ["progress", "--completed", "3", "--total", "5",
                                                  "--evidence-ref", progress])
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
            state = {"phase": "discovery", "status": "in_progress", "blocked": False, "blockers": [],
                     "readiness": {"sdd_ready": False}, "work_item": {"progress": {"completed": 0, "total": 0}},
                     "analysis": {}, "enforcement": {}, "review": {}, "verification": {}, "quality_gates": {}, "cursor": {}}
            pure = action_guard.evaluate(state, "advance", target_phase="implementation",
                                         evidence={"decision_status": "CLASSIFIED"})
            rendered = action_guard.render_recovery(repo, pure)
            for item in rendered:
                described = tool_actions.describe({"tool_name": "Bash", "tool_input": {"command": item["command"]}}, repo)
                self.assertEqual("prepare", described["action"], item)

    def test_native_transition_returns_recovery_and_native_sync_is_the_legal_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); bootstrap(repo); write_spec(repo)
            start_implementation(repo, "Implement Spring Boot JWT login.")
            state_path = repo / ".orchestrator" / "execution-state.yaml"
            state = sm._load(state_path)
            state["authority"]["mode"] = "native"
            state["phase"] = "discovery"
            state["status"] = "in_progress"
            state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
            native_state = repo / "_bmad" / "sprint-status.yaml"
            native_state.parent.mkdir()
            # A realistic native status source: native authority is parsed from this content,
            # not restated by whoever calls native-sync.
            native_state.write_text(
                "development_status:\n"
                f"  {state['work_item']['id']}: in-progress\n", encoding="utf-8")
            # The native advance is a repository change: refresh the analysis before asking
            # governance to follow it.
            attach_fixture_analysis(sm, state_path)

            code, result, text = self._run(repo, ["transition", "--phase", "implementation", "--status", "in_progress",
                                                  "--reason", "native workflow completed", "--evidence-ref", SPEC])
            self.assertEqual(1, code)
            self.assertEqual("NATIVE_AUTHORITY_REQUIRED", result["error"])
            self.assertTrue(result["recovery"])
            self.assertIn("native-sync", text)

            digest = repository_snapshot.digest_path(native_state)
            code, result, text = self._run(repo, ["native-sync", "--phase", "implementation", "--status", "in_progress",
                                                  "--native-state-ref", "_bmad/sprint-status.yaml", "--native-revision", digest])
            self.assertEqual(0, code, text)
            self.assertEqual("NATIVE_STATE_SYNCED", result["status"])
            synced = sm._load(state_path)
            self.assertEqual(digest, synced["authority"]["last_native_sync"]["native_revision"])
            # Native observation and governance are two records: the native fact is always kept,
            # and it only moves governance once governance is actually authorized.
            observation = synced["authority"]["native_observation"]
            self.assertEqual("implementation", observation["phase"])
            self.assertEqual("in_progress", observation["status_detail"])
            divergence = synced["authority"].get("native_divergence")
            if divergence:
                self.assertEqual("implementation/in_progress", divergence["native"])
                self.assertTrue(divergence["reason_codes"])
            else:
                self.assertEqual("implementation", synced["phase"])


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
        self.assertEqual(action_guard.recovery_actions(result["reasons"]), result["recovery"])


if __name__ == "__main__":
    unittest.main()
