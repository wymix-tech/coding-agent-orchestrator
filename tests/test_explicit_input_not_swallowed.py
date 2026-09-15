"""Phase A / PR3: explicit new inputs must reach re-analysis, and migrated projects must resume.

Uses the same repository fixtures as the resume regressions; only the external long-running
pipeline call is stubbed, so whether re-analysis was invoked is observable.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import execution_state_manager as sm
import requirement_identity
import start_router
import test_resume_guard as helpers


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


cli = _load("coding_orchestrator", "scripts/coding_orchestrator.py")


class ExplicitInputTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        helpers.git_init(self.repo)
        helpers.bootstrap(self.repo)
        helpers.write_spec(self.repo)
        helpers.start_implementation(self.repo, "Implement Spring Boot JWT login.")

    def _intake(self, *extra: str):
        argv = ["--repo", str(self.repo), "intake", "Continue implementation for approved Spring Boot JWT Login.",
                "--sdd", "generic", "--sdd-ref", str(self.repo / helpers.SPEC), "--actor", "test", *extra]
        args = cli.build_parser().parse_args(argv)
        args.repo = args.repo.resolve()
        with mock.patch.object(cli.semantic_intake_pipeline, "main", return_value=0) as pipeline:
            code, result, text = args.func(args)
        result["_pipeline_calls"] = pipeline.call_count
        return code, result, text

    def _resolutions(self) -> str:
        path = self.repo / "resolutions.json"
        path.write_text(json.dumps([{"fact_id": "ambiguity.goal_explicit", "value": True,
                                     "evidence_strength": "authoritative",
                                     "source_type": "human_decision", "source_ref": "decision-log"}]), encoding="utf-8")
        return str(path)

    def test_new_resolutions_reach_reanalysis(self):
        code, result, text = self._intake("--resolutions", self._resolutions())
        self.assertNotEqual(2, code, text)
        self.assertEqual(1, result["_pipeline_calls"],
                         "a new resolutions file is new input; the source-unchanged shortcut must not swallow it")

    def test_explicit_reanalyze_intent_reaches_reanalysis(self):
        code, result, text = self._intake("--reanalyze")
        self.assertNotEqual(2, code, text)
        self.assertEqual(1, result["_pipeline_calls"])

    def test_new_evidence_ref_reaches_reanalysis(self):
        code, result, text = self._intake("--evidence-ref", "evidence/runs/1/report.json")
        self.assertNotEqual(2, code, text)
        self.assertEqual(1, result["_pipeline_calls"])

    def test_explicit_inputs_change_the_intake_fingerprint(self):
        pipeline = _load("semantic_intake_pipeline", "scripts/semantic_intake_pipeline.py")
        base = pipeline.intake_fingerprint("req", "main", None)
        self.assertNotEqual(base, pipeline.intake_fingerprint("req", "main", None, reanalyze=True))
        self.assertNotEqual(base, pipeline.intake_fingerprint("req", "main", None, explicit_revision="sprint-9"))
        self.assertNotEqual(base, pipeline.intake_fingerprint("req", "main", None, evidence_refs=["evidence/a.json"]))
        self.assertEqual(base, pipeline.intake_fingerprint("req", "main", None))

    def test_plain_resume_still_reuses_the_same_revision(self):
        before = sm._load(self.repo / ".orchestrator" / "execution-state.yaml")
        code, result, text = self._intake()
        self.assertNotEqual(2, code, text)
        self.assertTrue(result["request_rephrased_source_unchanged"], text)
        after = sm._load(self.repo / ".orchestrator" / "execution-state.yaml")
        self.assertEqual(before["work_item"]["requirement_revision"], after["work_item"]["requirement_revision"])
        self.assertEqual("implementation", after["phase"])


class LegacyProjectAfterMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        helpers.git_init(self.repo)
        helpers.bootstrap(self.repo)
        spec = helpers.write_spec(self.repo)
        self.state = helpers.start_implementation(self.repo, "Implement Spring Boot JWT login.")
        registry = requirement_identity.load_registry(self.repo)
        entry = registry.setdefault("requirements", {})[f"generic:source:{helpers.SPEC}"] = {
            "provider": "generic", "source_path": helpers.SPEC,
            "revisions": {str(self.state["work_item"]["requirement_revision"]): {
                "status": "active", "work_item_id": "change-test"}},
            "work_items": ["change-test"],
        }
        requirement_identity.save_registry(self.repo, registry)
        self.entry_before = dict(entry)

    def test_migrated_project_resumes_instead_of_reopening_the_requirement(self):
        # v1 history has no source content revision at all.
        self.assertTrue(requirement_identity.is_legacy_entry(self.entry_before))
        result = requirement_identity.migrate(self.repo)
        self.assertEqual("MIGRATED", result["status"])
        self.assertTrue(requirement_identity.load_registry(self.repo)
                        ["requirements"][f"generic:source:{helpers.SPEC}"]["source_revision"])

        routed = start_router.resolve(self.repo, ensure_bootstrap=False)
        self.assertEqual("READY_FOR_WORK", routed["status"], routed)
        self.assertEqual("RESUME_CURRENT_WORK", routed["route"])
        state = sm._load(self.repo / ".orchestrator" / "execution-state.yaml")
        self.assertEqual("implementation", state["phase"])
        self.assertEqual({"completed": 2, "total": 5}, state["work_item"]["progress"])


if __name__ == "__main__":
    unittest.main()
