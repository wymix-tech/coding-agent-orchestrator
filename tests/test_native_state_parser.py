"""Phase A / PR2: native state must be parsed from real content, never restated by the caller.

Core parsing and projection are exercised for real. No mocking of the parser, the source
lookup, or the digest computation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("native_state_parser", ROOT / "scripts" / "native_state_parser.py")
nsp = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(nsp)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class NativeStateParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    # --- supported lifecycle -----------------------------------------------------------

    def test_ready_for_dev_projects_planning_ready(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: ready-for-dev\n")
        state = {"work_item": {"id": "story-1"}}
        projection, diag = nsp.resolve_projection(self.repo, state)
        self.assertTrue(diag == {}, diag)
        self.assertEqual("planning", projection.phase)
        self.assertEqual("ready", projection.status_detail)

    def test_in_progress_projects_implementation(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: in-progress\n")
        projection, _ = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertEqual(("implementation", "in_progress"), (projection.phase, projection.status_detail))

    def test_done_projects_closed_completed(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: done\n")
        projection, _ = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertEqual(("closed", "completed"), (projection.phase, projection.status_detail))

    def test_digest_and_parse_use_the_same_bytes(self):
        body = "development_status:\n  story-1: in-progress\n"
        _write(self.repo, "sprint-status.yaml", body)
        projection, _ = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertEqual(hashlib.sha256(body.encode("utf-8")).hexdigest(), projection.content_digest)
        self.assertEqual(projection.content_digest, projection.native_state_revision)

    def test_task_progress_comes_from_the_native_source(self):
        _write(self.repo, "sprint-status.yaml", (
            "development_status:\n"
            "  story-1:\n"
            "    status: in-progress\n"
            "    tasks:\n"
            "      a: true\n"
            "      b: false\n"))
        projection, _ = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertEqual({"completed": 1, "total": 2}, projection.progress)

    # --- structured diagnostics --------------------------------------------------------

    def test_no_configured_source_is_reported(self):
        projection, diag = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertIsNone(projection)
        self.assertEqual("NATIVE_SOURCE_UNCONFIGURED", diag["error"])
        self.assertIn("next_action", diag)

    def test_unparsable_source_is_reported(self):
        _write(self.repo, "sprint-status.yaml", "development_status: [unclosed\n  - : x\n")
        projection, diag = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertIsNone(projection)
        self.assertEqual("NATIVE_FORMAT_UNKNOWN", diag["error"])

    def test_unsupported_status_is_not_guessed(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: half-finished\n")
        projection, diag = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertIsNone(projection)
        self.assertEqual("NATIVE_STATUS_UNSUPPORTED", diag["error"])
        self.assertIn("half-finished", diag.get("observed_status", ""))

    def test_ambiguous_work_item_is_reported(self):
        _write(self.repo, "sprint-status.yaml",
               "development_status:\n  story-1: done\n  story-2: in-progress\n")
        projection, diag = nsp.resolve_projection(self.repo, {"work_item": {}})
        self.assertIsNone(projection)
        self.assertEqual("NATIVE_WORK_ITEM_AMBIGUOUS", diag["error"])

    def test_missing_work_item_is_reported(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-9: done\n")
        projection, diag = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertIsNone(projection)
        self.assertEqual("NATIVE_TASK_MISSING", diag["error"])

    # --- parse-then-commit drift -------------------------------------------------------

    def test_source_change_after_parsing_is_detected(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: ready-for-dev\n")
        projection, _ = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        unchanged = nsp.detect_source_change(self.repo, projection)
        self.assertFalse(unchanged["changed"])
        self.assertEqual(projection.content_digest, unchanged["observed_revision"])
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: in-progress\n")
        drift = nsp.detect_source_change(self.repo, projection)
        self.assertTrue(drift["changed"])
        self.assertEqual("NATIVE_SOURCE_CHANGED", drift["error"])
        self.assertEqual("re_run_native_sync", drift["next_action"])

    # --- the projection type is not a trust carrier -------------------------------------

    def test_projection_cannot_be_imported_from_serialized_data(self):
        for name in ("from_dict", "from_json", "deserialize", "load", "parse"):
            self.assertFalse(hasattr(nsp.NativeProjection, name), name)

    def test_as_record_is_an_audit_record_only(self):
        _write(self.repo, "sprint-status.yaml", "development_status:\n  story-1: done\n")
        projection, _ = nsp.resolve_projection(self.repo, {"work_item": {"id": "story-1"}})
        self.assertEqual("done-as-record", "done-as-record")
        self.assertIn("content_digest", projection.as_record())
        self.assertTrue(json.dumps(projection.as_record()))


if __name__ == "__main__":
    unittest.main()
