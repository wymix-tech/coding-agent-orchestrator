"""Phase A / PR3: migration is previewable, backed up, idempotent and resumable.

Interruption is simulated by writing the journal by hand (the state a crashed run leaves),
not by mocking the migration itself.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("requirement_identity", ROOT / "scripts" / "requirement_identity.py")
ri = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(ri)


def _registry_with() -> dict:
    return {
        "schema_version": 1,
        "requirements": {
            "generic:source:docs/a.md": {"provider": "generic", "source_path": "docs/a.md",
                                         "revisions": {"rev-old-a": {"status": "completed", "work_item_id": "W-1"}},
                                         "work_items": ["W-1"]},
            "generic:source:docs/b.md": {"provider": "generic", "source_path": "docs/b.md",
                                         "revisions": {"rev-old-b": {"status": "completed", "work_item_id": "W-2"}},
                                         "work_items": ["W-2"]},
        },
    }


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.registry_path = self.repo / ri.REGISTRY_REL
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(_registry_with(), ensure_ascii=False, indent=2), encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "a.md").write_text("# Spec A\n\nfirst requirement\n", encoding="utf-8")
        (self.repo / "docs" / "b.md").write_text("# Spec B\n\nsecond requirement\n", encoding="utf-8")

    def _registry(self) -> dict:
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _journal(self) -> dict:
        return json.loads((self.repo / ri.JOURNAL_REL).read_text(encoding="utf-8"))

    def test_preview_writes_nothing(self):
        preview = ri.preview_migration(self.repo)
        self.assertEqual(["generic:source:docs/a.md", "generic:source:docs/b.md"], sorted(preview["pending"]))
        self.assertEqual(2, len(preview["planned"]))
        self.assertFalse(preview["backup_exists"])
        self.assertFalse((self.repo / ri.BACKUP_REL).exists())
        self.assertFalse((self.repo / ri.JOURNAL_REL).exists())
        self.assertNotIn("source_revision", self._registry()["requirements"]["generic:source:docs/a.md"])

    def test_dry_run_does_not_apply(self):
        result = ri.migrate(self.repo, dry_run=True)
        self.assertFalse(result["applied"])
        self.assertFalse((self.repo / ri.BACKUP_REL).exists())
        self.assertFalse((self.repo / ri.JOURNAL_REL).exists())

    def test_migrate_records_source_revisions_and_is_idempotent(self):
        first = ri.migrate(self.repo)
        self.assertEqual("MIGRATED", first["status"])
        self.assertTrue((self.repo / ri.BACKUP_REL).exists())
        migrated = {item["requirement_id"]: item["source_revision"] for item in first["migrated"]}
        after = self._registry()
        self.assertEqual(migrated["generic:source:docs/a.md"],
                         after["requirements"]["generic:source:docs/a.md"]["source_revision"])
        self.assertEqual(ri.IDENTITY_VERSION, after["identity_version"])
        self.assertEqual(0, len(first["resumed_skipped"]))

        second = ri.migrate(self.repo)
        self.assertEqual([], second["migrated"], "re-running migration must not re-derive settled revisions")
        self.assertEqual(2, len(second["resumed_skipped"]))
        self.assertEqual(migrated, {item["requirement_id"]: item["source_revision"] for item in first["migrated"]})

    def test_interrupted_run_resumes_from_the_journal(self):
        # Simulate a crash: intent journalled, atomic registry replacement not yet done.
        journal_path = self.repo / ri.JOURNAL_REL
        journal_path.write_text(json.dumps({"status": "in_progress", "identity_version": ri.IDENTITY_VERSION,
                                            "completed": ["generic:source:docs/a.md"]}), encoding="utf-8")
        result = ri.migrate(self.repo)
        self.assertEqual("MIGRATED", result["status"])
        self.assertEqual(2, len(result["migrated"]))
        self.assertEqual("done", self._journal()["status"])
        after = self._registry()
        self.assertTrue(after["requirements"]["generic:source:docs/a.md"]["source_revision"])
        self.assertTrue(after["requirements"]["generic:source:docs/b.md"]["source_revision"])
        self.assertIn("generic:source:docs/a.md", self._journal()["completed"])

    def test_unresolvable_entry_stays_legacy_with_a_command(self):
        (self.repo / "docs" / "b.md").unlink()
        result = ri.migrate(self.repo)
        self.assertEqual("MIGRATED_PARTIAL", result["status"])
        self.assertEqual(["generic:source:docs/b.md"], [item["requirement_id"] for item in result["unresolved"]])
        entry = self._registry()["requirements"]["generic:source:docs/b.md"]
        self.assertTrue(entry["legacy"])
        self.assertEqual(ri.LEGACY_MIGRATION_COMMAND, entry["migration_command"])
        self.assertIsNone(entry.get("source_revision"))
        self.assertEqual("repair_source_members", result["unresolved"][0]["next_action"])

    def test_legacy_entries_are_marked_not_assumed_unchanged(self):
        registry = ri.mark_legacy(ri.load_registry(self.repo))
        for entry in registry["requirements"].values():
            self.assertTrue(entry["legacy"])
            self.assertEqual(ri.LEGACY_MIGRATION_COMMAND, entry["migration_command"])
            self.assertIsNone(entry.get("source_revision"))

    def test_missing_backup_is_created_once_and_preserved(self):
        first_backup = None
        result = ri.migrate(self.repo)
        self.assertTrue(result["applied"])
        first_backup = (self.repo / ri.BACKUP_REL).read_bytes()
        second = ri.migrate(self.repo)
        self.assertEqual(2, len(second["resumed_skipped"]))
        self.assertEqual(first_backup, (self.repo / ri.BACKUP_REL).read_bytes())


if __name__ == "__main__":
    unittest.main()
