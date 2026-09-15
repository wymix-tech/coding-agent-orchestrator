"""Phase A / PR3: identity v2 quadruple, mixed-content boundaries and file-set revisions.

Real files are read and really hashed. Nothing here mocks the content layer.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("requirement_identity", ROOT / "scripts" / "requirement_identity.py")
ri = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(ri)

STORY = """# Story

As a user I want password reset by email.

## Acceptance Criteria

1. A reset link is emailed within one minute.

## Tasks / Subtasks

- [ ] {task}

## Dev Agent Record

worked on tasks.
"""


class MixedContentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel: str, body: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def _revision(self, rel: str, **kwargs):
        return ri.requirement_content_revision(self.repo, rel, **kwargs)

    def test_task_tick_box_does_not_change_the_requirement_revision(self):
        self._write("docs/story.md", STORY.format(task="[ ]"))
        before = self._revision("docs/story.md")
        self._write("docs/story.md", STORY.format(task="[x]"))
        after = self._revision("docs/story.md")
        self.assertEqual(before["source_revision"], after["source_revision"])
        # The raw file did change, and that fact is kept separately.
        self.assertNotEqual(before["raw_digest"], after["raw_digest"])
        self.assertIn("Tasks / Subtasks", " ".join(after["runtime_sections"]))

    def test_subsection_of_a_task_section_inherits_runtime_ownership(self):
        """Tick-boxes live under `### Implementation` inside `## Tasks`, not at the top level."""
        body = (STORY.format(task="[ ]")
                .replace("## Tasks / Subtasks\n\n- [ ] [ ]",
                         "## Tasks\n\n### Implementation\n\n- [ ] write the endpoint"))
        self._write("docs/story.md", body)
        before = self._revision("docs/story.md")
        self.assertIn("Tasks > Implementation", " ".join(before["runtime_sections"]))
        self._write("docs/story.md", body.replace("- [ ] write the endpoint", "- [x] write the endpoint"))
        after = self._revision("docs/story.md")
        self.assertEqual(before["source_revision"], after["source_revision"])
        self.assertNotEqual(before["raw_digest"], after["raw_digest"])

    def test_status_api_section_is_not_the_runtime_status_section(self):
        """A section whose title merely contains "Status" is requirement content, exactly."""
        base = STORY.format(task="[ ]")
        with_api = base.replace("## Dev Agent Record",
                                "## Status API\n\nReturn 200 when the token is valid.\n\n## Dev Agent Record")
        self._write("docs/story.md", base)
        before = self._revision("docs/story.md")["source_revision"]
        self._write("docs/story.md", with_api)
        first = self._revision("docs/story.md")
        self.assertNotEqual(before, first["source_revision"])
        self.assertIn("Status API", " ".join(first["requirement_sections"]))
        self._write("docs/story.md", with_api.replace("Return 200", "Return 500"))
        self.assertNotEqual(first["source_revision"],
                            self._revision("docs/story.md")["source_revision"])

    def test_prose_before_the_first_heading_is_requirement_content(self):
        body = ("As a user I want auditability.\n\n## Acceptance Criteria\n\nEvery change is logged.\n"
                "\n## Tasks\n\n- [ ] log rotation\n")
        self._write("docs/story.md", body)
        result = self._revision("docs/story.md")
        self.assertTrue(result["source_revision"])
        self.assertTrue(result["requirement_sections"])
        self.assertIn("preamble", " ".join(result["requirement_sections"]).lower())
        self._write("docs/story.md", body.replace("auditability.", "auditability today."))
        self.assertNotEqual(result["source_revision"],
                            self._revision("docs/story.md")["source_revision"])

    def test_acceptance_criteria_edit_changes_the_requirement_revision(self):
        self._write("docs/story.md", STORY.format(task="[ ]"))
        before = self._revision("docs/story.md")["source_revision"]
        self._write("docs/story.md", STORY.format(task="[ ]").replace("within one minute", "within ten seconds"))
        self.assertNotEqual(before, self._revision("docs/story.md")["source_revision"])

    def test_unmapped_runtime_only_source_is_diagnosed_not_guessed(self):
        self._write("docs/story.md", "## Status\n\ndone\n\n## Change Log\n\nnothing\n")
        result = self._revision("docs/story.md")
        self.assertIsNone(result["source_revision"])
        self.assertEqual("MIXED_CONTENT_NO_REQUIREMENT_REGION", result["error"])
        self.assertEqual("configure_content_boundary", result["next_action"])

    def test_explicit_boundary_resolves_an_unmapped_source(self):
        self._write("docs/story.md", "## Status\n\ndone\n\n## Need1\n\nreal requirement\n")
        result = self._revision("docs/story.md", boundary={"runtime_sections": ["Status"]})
        self.assertTrue(result["source_revision"])
        self.assertIn("Status", " ".join(result["runtime_sections"]))

    def test_unknown_section_is_kept_and_reported(self):
        """An unknown heading is never dropped: it stays content and is reported as unmapped."""
        body = ("## Mystery Section\n\nsurprise\n\n## Acceptance Criteria\n\nmust hold\n"
                "\n## Tasks\n\n- [ ] do it\n")
        self._write("docs/story.md", body)
        result = self._revision("docs/story.md")
        self.assertTrue(result["source_revision"])
        self.assertEqual("SOURCE_SECTION_UNMAPPED", result["diagnostics"][0]["code"])
        headings = " ".join(str(entry.get("heading")) for entry in result["diagnostics"][0]["headings"])
        self.assertIn("Mystery Section", headings)
        # Keeping it means its text is part of the requirement content: edit it, revision moves.
        self._write("docs/story.md", body.replace("surprise", "surprise two"))
        self.assertNotEqual(result["source_revision"], self._revision("docs/story.md")["source_revision"])

    def test_plain_document_keeps_whole_file_semantics(self):
        self._write("docs/spec.md", "# Spec\n\nLogin with JWT.\n")
        result = self._revision("docs/spec.md")
        self.assertEqual("whole_file", result["boundary"])
        self.assertEqual(ri.source_revision_id(self.repo, "docs/spec.md"), result["source_revision"])


class FileSetRevisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel: str, body: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_order_does_not_matter_but_membership_does(self):
        self._write("reqs/a.md", "A\n")
        self._write("reqs/b.md", "B\n")
        forward = ri.file_set_revision(self.repo, ["reqs/a.md", "reqs/b.md"])
        reverse = ri.file_set_revision(self.repo, ["reqs/b.md", "reqs/a.md"])
        self.assertEqual(forward["file_set_revision"], reverse["file_set_revision"])
        self.assertEqual(["reqs/a.md", "reqs/b.md"], forward["members"])
        grew = ri.file_set_revision(self.repo, ["reqs/a.md", "reqs/b.md", "reqs/c.md"])
        self.assertNotEqual(forward["file_set_revision"], grew["file_set_revision"])

    def test_generated_and_missing_members_are_reported(self):
        self._write("reqs/a.md", "A\n")
        self._write("node_modules/x.md", "X\n")
        result = ri.requirement_content_revision(self.repo, None, members=["reqs/a.md", "node_modules/x.md", "gone.md"])
        self.assertIsNone(result["source_revision"])
        self.assertEqual("SOURCE_UNREADABLE", result["error"])
        self.assertIn("gone.md", result["missing_members"])
        self.assertNotIn("node_modules/x.md", result["members"])

    def test_directory_members_form_a_content_revision(self):
        self._write("reqs/a.md", "Requirement A.\n")
        self._write("reqs/b.md", "Requirement B.\n")
        result = ri.requirement_content_revision(self.repo, None, members=["reqs/a.md", "reqs/b.md"])
        self.assertTrue(result["source_revision"])
        self._write("reqs/b.md", "Requirement B2.\n")
        self.assertNotEqual(result["source_revision"],
                            ri.requirement_content_revision(self.repo, None, members=["reqs/a.md", "reqs/b.md"])["source_revision"])


class IdentityQuadrupleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, rel: str, body: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_request_wording_is_tracked_but_never_becomes_the_source_revision(self):
        self._write("docs/spec.md", "# Spec\n\nLogin with JWT.\n")
        one = ri.candidate_identity({"path": "docs/spec.md", "request_text": "please implement login"},
                                    "generic", repo=self.repo)
        two = ri.candidate_identity({"path": "docs/spec.md", "request_text": "继续"}, "generic", repo=self.repo)
        self.assertEqual(one["requirement_id"], two["requirement_id"])
        self.assertEqual(one["revision_id"], two["revision_id"])
        self.assertEqual(one["source_revision"], two["source_revision"])
        self.assertNotEqual(one["request_revision"], two["request_revision"])
        self.assertEqual(ri.IDENTITY_VERSION, one["identity_version"])

    def test_source_less_intake_uses_the_request_revision_explicitly(self):
        ident = ri.candidate_identity({"request_text": "add a health endpoint"}, "generic", repo=self.repo)
        self.assertIsNone(ident["source_revision"])
        self.assertEqual(ri.revision_id("add a health endpoint"), ident["request_revision"])
        self.assertEqual(ident["request_revision"], ident["revision_id"])
        # A direct free-text request is not an error; it simply has no source revision.
        self.assertIsNone(ident["source_diagnostic"])

    def test_explicit_revision_is_a_label_and_does_not_hide_content_change(self):
        self._write("docs/spec.md", "# Spec\n\none\n")
        before = ri.candidate_identity({"path": "docs/spec.md", "request_text": "r"}, "generic", repo=self.repo,
                                       explicit_revision="sprint-42")
        self._write("docs/spec.md", "# Spec\n\ntwo\n")
        after = ri.candidate_identity({"path": "docs/spec.md", "request_text": "r"}, "generic", repo=self.repo,
                                      explicit_revision="sprint-42")
        self.assertEqual("sprint-42", after["explicit_revision"])
        self.assertNotEqual(before["source_revision"], after["source_revision"],
                            "an external label must not mask a content change")

    def test_completed_requirement_does_not_reopen_on_rephrasing(self):
        self._write("docs/spec.md", "# Spec\n\nLogin with JWT.\n")
        ident = ri.candidate_identity({"path": "docs/spec.md", "request_text": "implement login"},
                                      "generic", repo=self.repo)
        ri.record(self.repo, requirement_id=ident["requirement_id"], revision_id=ident["revision_id"],
                  work_item_id="W-1", provider="generic", source_path="docs/spec.md", status="completed",
                  source_revision=ident["source_revision"], request_revision=ident["request_revision"])
        rephrased = ri.candidate_identity({"path": "docs/spec.md", "request_text": "继续 implement login"},
                                          "generic", repo=self.repo)
        self.assertTrue(ri.processed(self.repo, rephrased["requirement_id"], rephrased["revision_id"]))

    def test_different_prose_never_merges_two_requirements(self):
        self._write("docs/a.md", "# A\n\nfirst\n")
        self._write("docs/b.md", "# B\n\nsecond\n")
        a = ri.candidate_identity({"path": "docs/a.md", "request_text": "x"}, "generic", repo=self.repo)
        b = ri.candidate_identity({"path": "docs/b.md", "request_text": "x"}, "generic", repo=self.repo)
        self.assertNotEqual(a["requirement_id"], b["requirement_id"])


if __name__ == "__main__":
    unittest.main()
