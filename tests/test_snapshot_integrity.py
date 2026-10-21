"""Pruned snapshots preserve ordinary hashes and fail visibly on unreadable inputs."""
import hashlib
import os
import pathlib
import time
from unittest import mock

from repair_helpers import RepairCase
from scripts import cao


class SnapshotTests(RepairCase):
    def test_versioned_snapshot_tracks_mutations(self):
        (self.repo / "src").mkdir()
        file = self.repo / "src/app.py"
        file.write_text("x = 1\n", encoding="utf-8")
        excluded = {"acceptance-tests/test_acceptance.py"}
        initial = cao.code_snapshot(self.repo, excluded)
        self.assertTrue(initial.startswith("v4:"))
        file.write_text("x = 2\n", encoding="utf-8")
        changed = cao.code_snapshot(self.repo, excluded)
        self.assertNotEqual(changed, initial)
        file.unlink()
        self.assertNotEqual(cao.code_snapshot(self.repo, excluded), changed)
        file.write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(cao.code_snapshot(self.repo, excluded), initial)

    def test_walk_prunes_ignored_subtrees(self):
        with mock.patch("scripts.cao_snapshot.os.walk", wraps=cao.os.walk) as walk:
            cao.code_snapshot(self.repo)
        self.assertTrue(walk.called)

    def test_large_ignored_tree_is_not_visited_or_hashed(self):
        before = cao.code_snapshot(self.repo)
        for index in range(100):
            folder = self.repo / "node_modules" / f"package-{index}" / "deep"
            folder.mkdir(parents=True)
            for number in range(20):
                (folder / f"file-{number}.js").write_text("ignored\n", encoding="utf-8")
        calls = []
        original = os.scandir
        def scan(path):
            calls.append(str(path))
            return original(path)
        started = time.monotonic()
        with mock.patch("scripts.cao_snapshot.os.scandir", side_effect=scan):
            current = cao.code_snapshot(self.repo)
        self.assertEqual(current, before)
        self.assertFalse(any("node_modules" in path for path in calls), calls[:10])
        print(f"SNAPSHOT_BENCHMARK ignored_files=2000 visited_dirs={len(calls)} seconds={time.monotonic()-started:.6f}")

    def test_product_read_error_is_not_skipped(self):
        file = self.repo / "src.py"
        file.write_text("source\n", encoding="utf-8")
        original = pathlib.Path.open
        def read(path, *args, **kwargs):
            if path.resolve() == file.resolve():
                raise PermissionError("unreadable source")
            return original(path, *args, **kwargs)
        with mock.patch.object(pathlib.Path, "open", read), self.assertRaisesRegex(RuntimeError, "SNAPSHOT_READ_FAILED.*src.py"):
            cao.code_snapshot(self.repo)

    def test_unreadable_subtree_is_not_silently_skipped(self):
        folder = self.repo / "restricted"
        folder.mkdir()
        original = os.scandir
        def scan(path):
            if pathlib.Path(path) == folder: raise PermissionError(13, "denied", str(folder))
            return original(path)
        with mock.patch("scripts.cao_snapshot.os.scandir", side_effect=scan), self.assertRaisesRegex(RuntimeError, "SNAPSHOT_READ_FAILED.*restricted"):
            cao.code_snapshot(self.repo)

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            self.skipTest(f"symlink capability unavailable: {exc}")

    def test_external_file_link_cannot_certify_snapshot(self):
        target = self.repo.parent / (self.repo.name + "-external.py")
        target.write_text("outside\n", encoding="utf-8")
        self.addCleanup(target.unlink)
        self.symlink(self.repo / "source-link.py", target)
        with self.assertRaisesRegex(RuntimeError, "SNAPSHOT_EXTERNAL_SYMLINK.*source-link.py"):
            cao.code_snapshot(self.repo)

    def test_internal_file_and_directory_links_are_hashed(self):
        file = self.repo / "src.py"
        file.write_text("first\n", encoding="utf-8")
        self.symlink(self.repo / "link.py", file)
        first = cao.code_snapshot(self.repo)
        file.write_text("second\n", encoding="utf-8")
        self.assertNotEqual(first, cao.code_snapshot(self.repo))
        hidden = self.repo / "node_modules"
        hidden.mkdir()
        (hidden / "ignored.py").write_text("ignored\n", encoding="utf-8")
        before = cao.code_snapshot(self.repo)
        self.symlink(self.repo / "linked-dir", hidden, directory=True)
        linked = cao.code_snapshot(self.repo)
        self.assertNotEqual(linked, before)
        (hidden / "ignored.py").write_text("changed linked business input\n")
        self.assertNotEqual(cao.code_snapshot(self.repo), linked)

    def test_rules_read_error_and_external_link_report_explicit_diagnostics(self):
        rules = self.repo / "AGENTS.md"
        rules.write_text("rules\n", encoding="utf-8")
        original = pathlib.Path.read_bytes
        def read(path):
            if path.resolve() == rules.resolve(): raise PermissionError("unreadable rules")
            return original(path)
        with mock.patch.object(pathlib.Path, "read_bytes", read), self.assertRaisesRegex(RuntimeError, "SNAPSHOT_READ_FAILED.*AGENTS.md"):
            cao.rules_snapshot(self.repo, {"rules": {"files": ["AGENTS.md"]}})
        outside = self.repo.parent / (self.repo.name + "-rules.md")
        outside.write_text("external rules\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        rules.unlink()
        self.symlink(rules, outside)
        with self.assertRaisesRegex(RuntimeError, "SNAPSHOT_EXTERNAL_SYMLINK.*AGENTS.md"):
            cao.rules_snapshot(self.repo, {"rules": {"files": ["AGENTS.md"]}})
