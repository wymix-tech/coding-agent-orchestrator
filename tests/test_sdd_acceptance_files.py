"""Bounded native sources and exact Work-owned acceptance report paths."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.sdd_acceptance_policy import normalize_declaration
try:
    from scripts import sdd_acceptance_files as files
except ImportError:
    files = None


class AcceptanceFilesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(files, "SDD acceptance file boundaries are missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.dirs = ["_bmad-output/"]
        self.wid = "login-work"
        self.spec = self.repo / "_bmad-output/spec.md"
        self.spec.parent.mkdir()
        self.spec.write_text("# Login SPEC\nImplement login.\n## Acceptance\n- AC-1: correct credentials succeed.\n", encoding="utf-8")
        self.declaration = normalize_declaration({
            "spec": "_bmad-output/spec.md", "criteria": [
                {"id": "AC-1", "source": "_bmad-output/spec.md", "kind": "requirement", "description": "Login succeeds"}],
            "executor": {"mode": "command", "entrypoint": "python check.py"},
        })
        self.output = "_bmad-output/cao-acceptance/login-work/report.json"

    def sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def outputs(self, values=None, previous=None):
        d = {**self.declaration, "outputs": values if values is not None else [self.output]}
        return files.output_paths(self.repo, d, self.dirs, self.wid, previous or {})

    def test_spec_and_standard_in_one_file_are_sufficient(self):
        hashes = files.source_hashes(self.repo, self.declaration, self.dirs)
        self.assertEqual(hashes, {"_bmad-output/spec.md": self.sha(self.spec)})
        self.assertFalse((self.spec.parent / "tasks.md").exists())

    def test_title_frontmatter_or_empty_sources_rejected(self):
        for content in ("", " \n", "# Heading\n## Acceptance\n", "---\nstatus: ready\n---\n# Title\n"):
            self.spec.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_SOURCE_INVALID"):
                files.source_hashes(self.repo, self.declaration, self.dirs)

    def test_source_hashes_detect_body_changes(self):
        before = files.source_hashes(self.repo, self.declaration, self.dirs)
        self.spec.write_text("# Revised SPEC\nRequire logout too.\n", encoding="utf-8")
        self.assertNotEqual(before, files.source_hashes(self.repo, self.declaration, self.dirs))

    def test_missing_directory_non_provider_or_external_sources_rejected(self):
        (self.repo / "product.md").write_text("Product document", encoding="utf-8")
        for path in ("_bmad-output/missing.md", "_bmad-output", "product.md", "../outside.md"):
            d = {**self.declaration, "sources": [path]}
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_SOURCE_INVALID"):
                files.source_hashes(self.repo, d, self.dirs)

    def test_provider_source_alias_to_product_is_rejected(self):
        target = self.repo / "product.md"
        target.write_text("Product source", encoding="utf-8")
        self.spec.unlink()
        self.spec.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_SOURCE_INVALID"):
            files.source_hashes(self.repo, self.declaration, self.dirs)

    def test_new_output_is_exact_relative_work_scoped_path(self):
        self.assertEqual(self.outputs(), {self.output})
        self.assertFalse((self.repo / self.output).exists(), "validation must not create files")
        self.assertEqual(self.outputs(values=[]), set())

    def test_output_cannot_be_input_other_work_directory_or_product(self):
        for path in ("_bmad-output/spec.md", "_bmad-output/cao-acceptance/other/report.json",
                     "_bmad-output/cao-acceptance/login-work", "src/report.json",
                     "_bmad-output/cao-acceptance/login-work/source.py", "../report.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_OUTPUT_INVALID"):
                self.outputs([path])

    def test_output_path_must_not_alias_an_input(self):
        path = self.repo / self.output
        path.parent.mkdir(parents=True)
        path.symlink_to(self.spec)
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_OUTPUT_INVALID"):
            self.outputs(previous={self.output: self.sha(self.spec)})

    def test_output_parent_cannot_alias_another_work(self):
        other = self.repo / "_bmad-output/cao-acceptance/other"
        other.mkdir(parents=True)
        (other.parent / self.wid).symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_OUTPUT_INVALID"):
            self.outputs()

    def test_existing_output_needs_matching_previous_ownership_bytes(self):
        path = self.repo / self.output
        path.parent.mkdir(parents=True)
        path.write_text('{"review":"previous"}', encoding="utf-8")
        for previous in ({}, {self.output: "wrong"}):
            with self.subTest(previous=previous), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_OUTPUT_INVALID"):
                self.outputs(previous=previous)
        self.assertEqual(self.outputs(previous={self.output: self.sha(path)}), {self.output})

    def test_existing_directory_cannot_be_report(self):
        (self.repo / self.output).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_OUTPUT_INVALID"):
            self.outputs()

    def test_evidence_and_registered_output_hashes_change_with_bytes(self):
        evidence = self.repo / "test_acceptance.py"
        evidence.write_text("assert 2+2 == 4\n", encoding="utf-8")
        report = self.repo / self.output
        report.parent.mkdir(parents=True)
        report.write_text('{"accepted":true}', encoding="utf-8")
        results = [{"id": "AC-1", "status": "passed", "evidence": [{"path": evidence.name, "reason": "Executed assertion"}]}]
        hashes = files.report_hashes(self.repo, results, [self.output])
        self.assertEqual(hashes, {evidence.name: self.sha(evidence), self.output: self.sha(report)})
        evidence.write_text("assert 2+2 == 5\n", encoding="utf-8")
        self.assertNotEqual(hashes, files.report_hashes(self.repo, results, [self.output]))

    def test_evidence_hashes_include_large_files_without_report_size_limit(self):
        evidence = self.repo / "acceptance-evidence.bin"
        evidence.write_bytes(b"a" * (1024 * 1024) + b"z")
        results = [{"evidence": [{"path": evidence.name, "reason": "Complete evidence bytes"}]}]
        try:
            hashes = files.report_hashes(self.repo, results, [])
        except ValueError as exc:
            self.fail(f"Report parser limit must not truncate or reject evidence hashes: {exc}")
        self.assertEqual(hashes, {evidence.name: self.sha(evidence)})

    def test_missing_external_or_directory_evidence_and_outputs_rejected(self):
        for value in ("missing", "_bmad-output", "../outside"):
            results = [{"evidence": [{"path": value, "reason": "checked"}]}]
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_EVIDENCE_INVALID"):
                files.report_hashes(self.repo, results, [])
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_EVIDENCE_INVALID"):
            files.report_hashes(self.repo, [], [self.output])

    def test_json_read_is_utf8_bounded_object_and_not_partial(self):
        report = self.repo / "result.json"
        report.write_text(json.dumps({"summary": "验收"}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(files.read_json(report), {"summary": "验收"})
        for content in ("[]", "false", "{broken", ""):
            report.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RESULT_INVALID"):
                files.read_json(report)
        report.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RESULT_INVALID"):
            files.read_json(report)
        report.write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_REPORT_TOO_LARGE"):
            files.read_json(report)


if __name__ == "__main__":
    unittest.main()
