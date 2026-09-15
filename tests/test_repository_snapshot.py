"""Real Git regressions for content freshness across metadata-only commits."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import action_guard
import cbm_provider
import enforcement_kernel as kernel
import execution_state_manager as state_manager
import fact_extractor
import repository_snapshot as snapshots
import semantic_intake_pipeline as pipeline
from governance_fixture import refresh_context
from test_enforcement_kernel import EnforcementFixture


def git(repo, *args):
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo, text=True, stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"git {args!r} failed:\n{exc.stdout}\n{exc.stderr}") from exc


class ContentSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Snapshot Test")
        git(self.repo, "config", "user.email", "snapshot@example.com")
        self.source = self.repo / "src" / "app.py"
        self.source.parent.mkdir()
        self.source.write_text("value = 1\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "initial")
        git(self.repo, "branch", "comparison-base")

    def fingerprint(self):
        return snapshots.fingerprint(self.repo)

    def test_empty_and_message_only_commits_preserve_content_identity(self):
        expected = self.fingerprint()
        head = git(self.repo, "rev-parse", "HEAD")
        for args in (
            ("commit", "--allow-empty", "-qm", "empty"),
            ("commit", "--amend", "--allow-empty", "-qm", "different message"),
        ):
            with self.subTest(args=args):
                git(self.repo, *args)
                new_head = git(self.repo, "rev-parse", "HEAD")
                self.assertNotEqual(head, new_head)
                self.assertEqual(expected, self.fingerprint())
                head = new_head

    def test_staging_and_committing_existing_and_new_files_preserve_identity(self):
        previous = self.fingerprint()
        self.source.write_text("value = 2\n", encoding="utf-8")
        (self.source.parent / "new.py").write_text("new = True\n", encoding="utf-8")
        changed = self.fingerprint()
        self.assertNotEqual(previous, changed)
        git(self.repo, "add", ".")
        self.assertEqual(changed, self.fingerprint())
        git(self.repo, "commit", "-qm", "material change already analyzed")
        self.assertEqual(changed, self.fingerprint())

    def test_committing_deletion_preserves_already_deleted_content_identity(self):
        previous = self.fingerprint()
        self.source.unlink()
        deleted = self.fingerprint()
        self.assertNotEqual(previous, deleted)
        git(self.repo, "add", "-u")
        self.assertEqual(deleted, self.fingerprint())
        git(self.repo, "commit", "-qm", "delete")
        self.assertEqual(deleted, self.fingerprint())

    def test_committing_rename_preserves_already_renamed_content_identity(self):
        previous = self.fingerprint()
        self.source.rename(self.source.with_name("renamed.py"))
        renamed = self.fingerprint()
        self.assertNotEqual(previous, renamed)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "rename")
        self.assertEqual(renamed, self.fingerprint())

    def test_initial_commit_preserves_unborn_content_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            before_git = snapshots.fingerprint(repo)
            git(repo, "init", "-q")
            git(repo, "config", "user.name", "Snapshot Test")
            git(repo, "config", "user.email", "snapshot@example.com")
            self.assertEqual(before_git, snapshots.fingerprint(repo))
            git(repo, "add", ".")
            self.assertEqual(before_git, snapshots.fingerprint(repo))
            git(repo, "commit", "-qm", "initial")
            self.assertEqual(before_git, snapshots.fingerprint(repo))

    def test_committing_generated_intake_files_does_not_change_content_identity(self):
        before = self.fingerprint()
        generated = self.repo / ".orchestrator/intake/fact-resolutions.json"
        generated.parent.mkdir(parents=True)
        generated.write_text('{"resolutions": []}\n', encoding="utf-8")
        git(self.repo, "add", ".orchestrator/intake")
        git(self.repo, "commit", "-qm", "record intake")
        self.assertEqual(before, self.fingerprint())

    @unittest.skipUnless(os.name == "posix", "requires POSIX executable modes")
    def test_executable_mode_changes_still_invalidate_content_identity(self):
        before = self.fingerprint()
        self.source.chmod(self.source.stat().st_mode | 0o111)
        changed = self.fingerprint()
        self.assertNotEqual(before, changed)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "make executable")
        self.assertEqual(changed, self.fingerprint())

    @unittest.skipUnless(os.name == "posix", "requires POSIX symbolic links")
    def test_symlink_retarget_still_invalidates_content_identity(self):
        link = self.repo / "entry"
        link.symlink_to("src/app.py")
        before = self.fingerprint()
        link.unlink()
        link.symlink_to("src/missing.py")
        changed = self.fingerprint()
        self.assertNotEqual(before, changed)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "link")
        self.assertEqual(changed, self.fingerprint())

    def analyses(self, request="Update app", base_ref="comparison-base", raw=None):
        facts = fact_extractor.extract(self.repo, request=request, base_ref=base_ref)
        impact = cbm_provider.normalize_detect_changes(
            raw or {"changed_files": ["src/app.py"], "changed_symbols": [], "impacted_symbols": []},
            repo=self.repo, project="snapshot-test", base_ref=base_ref,
        )
        return facts, impact, pipeline.combined_snapshot(facts, impact)

    def test_metadata_commits_preserve_analysis_ids_and_update_head_trace(self):
        first = self.analyses()
        for args in (
            ("commit", "--allow-empty", "-qm", "empty"),
            ("commit", "--amend", "--allow-empty", "-qm", "message only"),
        ):
            with self.subTest(args=args):
                old_head = git(self.repo, "rev-parse", "HEAD")
                git(self.repo, *args)
                current = self.analyses()
                self.assertEqual(first[2], current[2])
                self.assertNotEqual(old_head, current[0]["extraction"]["observations"]["git_head"])
                self.assertNotEqual(old_head, current[1]["snapshot"]["git_head"])

    def test_analysis_of_fixed_diff_scope_survives_staging_and_commit(self):
        self.source.write_text("value = 2\n", encoding="utf-8")
        first = self.analyses()
        git(self.repo, "add", ".")
        self.assertEqual(first[2], self.analyses()[2])
        git(self.repo, "commit", "-qm", "feature")
        self.assertEqual(first[2], self.analyses()[2])

    def test_request_and_provider_evidence_changes_still_change_analysis(self):
        first = self.analyses()
        self.assertNotEqual(first[2], self.analyses(request="Different requirement")[2])
        changed = {
            "changed_files": ["src/app.py"], "changed_symbols": [],
            "impacted_symbols": [{"name": "consumer", "file_path": "src/consumer.py"}],
        }
        self.assertNotEqual(first[2], self.analyses(raw=changed)[2])

    def test_content_change_outside_provider_change_list_invalidates_analysis(self):
        other = self.repo / "src" / "dependency.py"
        other.write_text("dependency = 1\n", encoding="utf-8")
        first = self.analyses()
        other.write_text("dependency = 2\n", encoding="utf-8")
        current = self.analyses()
        self.assertNotEqual(first[0]["extraction"]["snapshot_id"], current[0]["extraction"]["snapshot_id"])
        self.assertNotEqual(first[1]["snapshot"]["id"], current[1]["snapshot"]["id"])

    def test_moving_comparison_base_changes_analysis_without_content_change(self):
        self.source.write_text("value = 2\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "feature")
        before_content = self.fingerprint()
        before = self.analyses()
        git(self.repo, "update-ref", "refs/heads/comparison-base", "HEAD")
        current = self.analyses()
        self.assertEqual(before_content, self.fingerprint())
        self.assertNotEqual(before[0]["extraction"]["snapshot_id"], current[0]["extraction"]["snapshot_id"])
        self.assertNotEqual(before[1]["snapshot"]["id"], current[1]["snapshot"]["id"])

    def test_missing_explicit_base_is_unresolved(self):
        self.assertIsNone(snapshots.comparison_basis(self.repo, None))
        self.assertEqual("resolved", snapshots.comparison_basis(self.repo, "comparison-base")["status"])
        git(self.repo, "branch", "-D", "comparison-base")
        self.assertEqual("unresolved", snapshots.comparison_basis(self.repo, "comparison-base")["status"])

    def test_fresh_uncommitted_diff_collection_can_change_scope_after_commit(self):
        # Without --base-ref a NEW extraction describes outstanding changes.
        # Consuming already analyzed content must remain valid across the commit.
        self.source.write_text("value = 2\n", encoding="utf-8")
        before = self.analyses(base_ref=None)
        before_content = self.fingerprint()
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "feature")
        after = self.analyses(base_ref=None)
        self.assertEqual(before_content, self.fingerprint())
        self.assertNotEqual(before[0]["extraction"]["snapshot_id"], after[0]["extraction"]["snapshot_id"])
        self.assertEqual([], after[0]["extraction"]["observations"]["changed_files"])


class CommitAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.fx = EnforcementFixture()
        self.addCleanup(self.fx.close)
        self.repo = self.fx.repo
        self.path = self.fx.state_path
        self.source = self.repo / "src/main/java/com/acme/App.java"

    def state(self):
        return state_manager._load(self.path)

    def attach(self):
        state_manager.attach_analysis(
            self.path, "test", "A2",
            str(self.fx.intake / "semantic-impact.json"),
            str(self.fx.intake / "work-facts.semantic-draft.json"),
            str(self.fx.intake / "decision.json"),
            str(self.fx.intake / "verification-plan.json"),
            context_manifest_ref=str(self.fx.intake / "context-manifest.json"),
        )
        refresh_context(self.repo, self.path)

    def ready(self):
        for key in ("implementation_tasks_complete", "acceptance_satisfied"):
            state_manager.set_readiness(self.path, key, True, "test", "evidence")
        state_manager.record_gate(self.path, "unit", True, "passed", "test", evidence_ref="unit-test-evidence")
        state_manager.record_review(self.path, "passed", "reviewer", evidence_ref="review-evidence")
        state_manager.record_verification(
            self.path, "passed", "verifier", self.state()["execution_snapshot_id"], "verification-evidence",
        )
        self.assert_can_close()

    def assert_can_close(self):
        result = action_guard.authorize(self.repo, self.state(), "close")
        self.assertTrue(result["allowed"], result)

    def tool(self, event, command):
        return kernel.handle(self.repo, "claude-code", event, {
            "tool_name": "Bash", "tool_input": {"command": command},
        })

    def test_full_gates_remain_fresh_after_add_commit_empty_commit_and_amend(self):
        self.source.write_text("class App { int feature; }\n", encoding="utf-8")
        self.attach()
        self.ready()
        before = copy.deepcopy(self.state())
        for args in (
            ("add", "src"),
            ("commit", "-qm", "feature"),
            ("commit", "--amend", "-qm", "feature-message"),
            ("commit", "--allow-empty", "-qm", "metadata"),
            ("commit", "--amend", "--allow-empty", "-qm", "new-message"),
        ):
            command = "git " + " ".join(args)
            with self.subTest(command=command):
                self.assertEqual("allow", self.tool("pre_tool", command)["decision"])
                git(self.repo, *args)
                self.tool("post_tool", command)
                current = self.state()
                self.assertFalse(current["enforcement"]["dirty"])
                self.assertTrue(current["verification"]["fresh"])
                for key in ("analysis", "quality_gates", "review", "verification", "execution_snapshot_id"):
                    self.assertEqual(before[key], current[key], key)
                self.assert_can_close()
        # A later event must not reinterpret the new HEAD as an external code edit.
        self.assertEqual("allow", self.tool("pre_tool", "git status")["decision"])
        self.assert_can_close()

    def test_external_metadata_commit_without_post_hook_preserves_authorization(self):
        self.ready()
        git(self.repo, "commit", "--allow-empty", "-qm", "external-metadata")
        self.assert_can_close()
        self.tool("pre_tool", "git status")
        self.assertFalse(self.state()["enforcement"]["dirty"])
        self.assert_can_close()

    @unittest.skipUnless(os.name == "posix", "executes a POSIX Git pre-commit hook")
    def test_real_git_hook_that_changes_code_invalidates_verification(self):
        self.ready()
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            "printf 'class App { int from_hook; }\\n' > src/main/java/com/acme/App.java\n"
            "git add src/main/java/com/acme/App.java\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        self.assertEqual("allow", self.tool("pre_tool", "git commit --allow-empty -qm hook")["decision"])
        git(self.repo, "commit", "--allow-empty", "-qm", "hook")
        self.tool("post_tool", "git commit --allow-empty -qm hook")
        self.assertTrue(self.state()["enforcement"]["dirty"])
        self.assertFalse(self.state()["verification"]["fresh"])
        result = action_guard.authorize(self.repo, self.state(), "close")
        self.assertFalse(result["allowed"])
        self.assertIn("REPOSITORY_CHANGED", result["reason_codes"])

    def test_authoritative_input_edit_still_invalidates_after_commit(self):
        self.ready()
        (self.fx.intake / "request-context.md").write_text("Changed acceptance criteria", encoding="utf-8")
        git(self.repo, "commit", "--allow-empty", "-qm", "external")
        self.tool("post_tool", "git commit --allow-empty -qm external")
        self.assertTrue(self.state()["enforcement"]["dirty"])
        result = action_guard.authorize(self.repo, self.state(), "close")
        self.assertIn("AUTHORITY_EVIDENCE_STALE", result["reason_codes"])

    def test_moved_or_deleted_comparison_base_blocks_existing_analysis(self):
        git(self.repo, "branch", "comparison-base")
        self.source.write_text("class App { int feature; }\n", encoding="utf-8")
        git(self.repo, "add", "src")
        git(self.repo, "commit", "-qm", "feature")
        facts = fact_extractor.extract(self.repo, request="Implement feature", base_ref="comparison-base")
        (self.fx.intake / "work-facts.semantic-draft.json").write_text(json.dumps(facts), encoding="utf-8")
        self.attach()
        self.ready()
        for args in (
            ("update-ref", "refs/heads/comparison-base", "HEAD"),
            ("branch", "-D", "comparison-base"),
        ):
            with self.subTest(args=args):
                git(self.repo, *args)
                evidence = action_guard.collect_evidence(self.repo, self.state())
                self.assertTrue(evidence["repository_fresh"])
                self.assertFalse(evidence["comparison_fresh"])
                result = action_guard.authorize(self.repo, self.state(), "close")
                self.assertIn("COMPARISON_BASE_CHANGED", result["reason_codes"])

    def test_metadata_commit_does_not_clear_an_existing_dirty_state(self):
        self.ready()
        state_manager.mark_enforcement_dirty(
            self.path, "mutation", [], "test", snapshots.fingerprint(self.repo), "existing unresolved mutation",
        )
        git(self.repo, "commit", "--allow-empty", "-qm", "metadata")
        self.tool("post_tool", "git commit --allow-empty -qm metadata")
        self.assertTrue(self.state()["enforcement"]["dirty"])
        self.assertFalse(self.state()["verification"]["fresh"])
        result = action_guard.authorize(self.repo, self.state(), "close")
        self.assertIn("ANALYSIS_DIRTY", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
