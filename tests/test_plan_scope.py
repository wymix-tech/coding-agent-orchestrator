from repair_helpers import RepairCase
from scripts import cao


class PlanScopeTests(RepairCase):
    def write_plan(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def bind(self, work, paths=None, selections=None, no_plan=False):
        self.assertTrue(callable(getattr(cao, "bind_plan_scope", None)), "bind_plan_scope is missing")
        return cao.bind_plan_scope(self.repo, work, paths or [], selections or {}, no_plan)

    def openspec(self):
        work = self.read_work(self.start())
        work["sdd"] = {"provider": "openspec", "native_ref": "openspec/changes/a",
                       "workflow": {"artifacts": ["openspec/changes/a/proposal.md"]}}
        self.write_plan("openspec/changes/a/proposal.md", "# proposal\n")
        return work

    def test_unrelated_tasks_do_not_block(self):
        work = self.openspec()
        self.write_plan("openspec/changes/a/tasks.md", "- [x] A\n")
        self.write_plan("openspec/changes/b/tasks.md", "- [ ] B\n")
        work["sdd"]["plan_scope"] = self.bind(work)
        self.assertEqual(cao.provider_pending_plan_items(self.repo, work), [])

    def test_shared_plan_discovered_beside_artifact_still_requires_ids(self):
        work = self.read_work(self.start())
        work["sdd"]["workflow"] = {"artifacts": ["_bmad-output/story.md"]}
        self.write_plan("_bmad-output/story.md", "# story\n")
        self.write_plan("_bmad-output/sprint-status.yaml", "development_status:\n  1-1-story: backlog\n")
        self.assertTrue(callable(getattr(cao, "bind_plan_scope", None)), "bind_plan_scope is missing")
        with self.assertRaisesRegex(ValueError, "SHARED_PLAN_ITEM_BINDING_REQUIRED"):
            self.bind(work)

    def test_multiple_artifact_anchors_preserve_all_discovered_plans(self):
        work = self.openspec()
        work["sdd"]["workflow"]["artifacts"].append("openspec/changes/b/proposal.md")
        self.write_plan("openspec/changes/b/proposal.md", "# proposal\n")
        paths = ["openspec/changes/a/tasks.md", "openspec/changes/b/tasks.md"]
        for path in paths:
            self.write_plan(path, "- [ ] related\n")
        work["sdd"]["plan_scope"] = self.bind(work)
        self.assertEqual({plan["path"] for plan in work["sdd"]["plan_scope"]["plans"]}, set(paths))
        self.assertEqual(len(cao.provider_pending_plan_items(self.repo, work)), 2)

    def test_multiple_explicit_changes_are_all_checked(self):
        work = self.openspec()
        paths = ["openspec/changes/a/tasks.md", "openspec/changes/b/tasks.md"]
        for path in paths:
            self.write_plan(path, "- [ ] related item\n")
        work["sdd"]["plan_scope"] = self.bind(work, paths)
        pending = cao.provider_pending_plan_items(self.repo, work)
        self.assertEqual(len(pending), 2)
        self.assertTrue(all(any(path in item for item in pending) for path in paths))

    def test_implicit_repeat_adds_new_specific_artifact_plan(self):
        work = self.openspec()
        a, b = "openspec/changes/a/tasks.md", "openspec/changes/b/tasks.md"
        self.write_plan(a, "- [x] original\n")
        work["sdd"]["plan_scope"] = self.bind(work, [a])
        work["sdd"]["workflow"]["artifacts"] = ["openspec/changes/b/proposal.md"]
        self.write_plan("openspec/changes/b/proposal.md", "# new change\n")
        self.write_plan(b, "- [ ] new related\n")
        work["sdd"]["plan_scope"] = self.bind(work)
        self.assertEqual({plan["path"] for plan in work["sdd"]["plan_scope"]["plans"]}, {a, b})
        self.assertEqual(len(cao.provider_pending_plan_items(self.repo, work)), 1)

    def test_scope_is_not_silently_narrowed_on_implicit_repeat(self):
        work = self.openspec()
        paths = ["openspec/changes/a/tasks.md", "openspec/changes/b/tasks.md"]
        for path in paths:
            self.write_plan(path, "- [x] related\n")
        work["sdd"]["plan_scope"] = self.bind(work, paths)
        repeated = self.bind(work)
        self.assertEqual({plan["path"] for plan in repeated["plans"]}, set(paths))

    def test_shared_sprint_checks_all_thirteen_selected_stories_only(self):
        work = self.read_work(self.start())
        path = "_bmad-output/implementation-artifacts/sprint-status.yaml"
        ids = [f"1-{n}-story" for n in range(1, 14)]
        text = "development_status:\n" + "".join(f"  {story}: backlog\n" for story in ids)
        text += "  9-1-unrelated: backlog\n  epic-1: backlog\n  epic-1-retrospective: optional\n"
        target = self.write_plan(path, text)
        work["sdd"]["plan_scope"] = self.bind(work, [path], {"./" + path: ids})
        pending = cao.provider_pending_plan_items(self.repo, work)
        self.assertEqual(len(pending), 13)
        self.assertFalse(any("unrelated" in item for item in pending))
        target.write_text(text.replace(": backlog", ": done", 13), encoding="utf-8")
        self.assertEqual(cao.provider_pending_plan_items(self.repo, work), [])

    def test_shared_sprint_requires_real_story_ids(self):
        work = self.read_work(self.start())
        path = "_bmad-output/implementation-artifacts/sprint-status.yaml"
        self.write_plan(path, "development_status:\n  epic-1: done\n  1-1-story: done\n")
        for ids in ([], ["missing"], ["epic-1"]):
            with self.subTest(ids=ids):
                self.assertTrue(callable(getattr(cao, "bind_plan_scope", None)), "bind_plan_scope is missing")
                with self.assertRaisesRegex(ValueError, "SHARED_PLAN_ITEM_BINDING_REQUIRED|PLAN_ITEM_INVALID"):
                    self.bind(work, [path], {path: ids})

    def test_public_root_is_unresolved_not_a_project_wide_guess(self):
        work = self.openspec()
        work["sdd"]["native_ref"] = "openspec/changes"
        work["sdd"]["workflow"]["artifacts"] = []
        self.write_plan("openspec/changes/tasks.md", "- [ ] whole project\n")
        self.assertEqual(self.bind(work)["mode"], "unresolved")

    def test_no_plan_is_explicit_and_cannot_remove_existing_scope(self):
        work = self.openspec()
        self.assertEqual(self.bind(work)["mode"], "unresolved")
        work["sdd"]["plan_scope"] = self.bind(work, no_plan=True)
        self.assertEqual(cao.provider_pending_plan_items(self.repo, work), [])
        path = "openspec/changes/a/tasks.md"
        self.write_plan(path, "- [x] current\n")
        work["sdd"]["plan_scope"] = self.bind(work, [path])
        with self.assertRaisesRegex(ValueError, "PLAN_SCOPE_REMOVAL_REQUIRES_REANCHOR"):
            self.bind(work, no_plan=True)

    def test_bound_plan_deletion_and_missing_story_are_errors(self):
        work = self.read_work(self.start())
        path = "_bmad-output/sprint-status.yaml"
        target = self.write_plan(path, "development_status:\n  1-1-story: done\n")
        work["sdd"]["plan_scope"] = self.bind(work, [path], {path: ["1-1-story"]})
        target.write_text("development_status:\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PLAN_ITEM_MISSING"):
            cao.provider_pending_plan_items(self.repo, work)
        target.unlink()
        with self.assertRaisesRegex(ValueError, "PLAN_SOURCE_MISSING"):
            cao.provider_pending_plan_items(self.repo, work)

    def test_native_reference_does_not_recursively_discover_unbound_plans(self):
        work = self.openspec()
        self.write_plan("openspec/changes/a/other/tasks.md", "- [ ] unknown scope\n")
        self.assertEqual(self.bind(work)["mode"], "unresolved")

    def test_plan_outside_current_provider_is_rejected(self):
        work = self.openspec()
        self.write_plan("_bmad-output/tasks.md", "- [x] wrong provider\n")
        self.assertTrue(callable(getattr(cao, "bind_plan_scope", None)), "bind_plan_scope is missing")
        with self.assertRaisesRegex(ValueError, "PLAN_SOURCE_INVALID"):
            self.bind(work, ["_bmad-output/tasks.md"])

    def test_complete_none_and_unresolved_have_distinct_done_results(self):
        wid = self.start()
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        self.run_cli("sdd", "workflow", "execute", "--command", "bmad-build")
        self.write_plan("_bmad-output/story.md", "# story\n")
        rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md")
        self.assertEqual(rc, 0, out)
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertIn("PLAN_SCOPE_UNRESOLVED", out["reason"])
        self.assertIn("--plan", out["next"])
        rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md", "--no-plan")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(wid)["sdd"]["plan_scope"]["mode"], "none")
        events = cao.list_lifecycle_events(self.repo, wid, 100)
        self.assertTrue(any(event["event"] == "workflow.no_plan_declared" for event in events))
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 0, out)

    def test_cli_merges_normalized_plan_item_paths(self):
        self.start()
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        self.run_cli("sdd", "workflow", "execute", "--command", "bmad-build")
        self.write_plan("_bmad-output/story.md", "# story\n")
        path = "_bmad-output/sprint-status.yaml"
        self.write_plan(path, "development_status:\n  1-1-story: done\n  1-2-story: done\n")
        rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md",
                               "--plan", path, "--plan-item", "./" + path + "#1-1-story",
                               "--plan-item", path + "#1-2-story")
        self.assertEqual(rc, 0, out)
        scope = self.read_work(out["work_item"]["id"])["sdd"]["plan_scope"]
        self.assertEqual(scope["plans"], [{"path": path, "item_ids": ["1-1-story", "1-2-story"]}])
