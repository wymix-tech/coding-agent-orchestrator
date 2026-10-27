"""Light completion needs bound native intent and real technical checks, no workflow log."""
import json
from tests.repair_helpers import RepairCase


class LightSpecTests(RepairCase):
    def spec(self, text=None, path="_bmad-output/spec.md"):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text or "# Login\nImplement config-file login.\n## 验收标准\n- [ ] Valid credentials create a session.\n", encoding="utf-8")
        return path

    def bind(self, **kwargs):
        args = ["sdd", "spec", "bind", "--spec", kwargs.pop("path", "_bmad-output/spec.md")]
        for key, value in kwargs.items():
            args.append("--" + key.replace("_", "-"))
            if value is not True:
                args.append(value)
        if "--criteria" not in args and "--criteria-section" not in args:
            args += ["--criteria-section", "验收标准"]
        return self.run_cli(*args)

    def test_light_closes_without_workflow_plan_with_real_behavior_acceptance(self):
        wid = self.start()
        self.spec()
        rc, out = self.bind(); self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("verify"); self.assertEqual(rc, 0, out)
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args))
        rc, out = self.run_cli("done"); self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(wid)["status"], "completed")
        self.assertNotIn("workflow", self.read_work(wid)["sdd"])

    def test_missing_spec_and_criteria_reject_completion(self):
        self.start()
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertIn("spec", out["reason"].lower())
        self.assertIn("sdd spec bind", out["next"])

    def test_empty_titles_comments_fences_and_placeholder_criteria_rejected(self):
        self.start()
        for text in ("# Only title\n", "# Spec\nIntent\n## 验收标准\n",
                     "# Spec\nIntent\n## 验收标准\n<!-- fake -->\n",
                     "# Spec\nIntent\n## 验收标准\n```\n- fake\n```\n",
                     "# Spec\nIntent\n## 验收标准\n- TODO\n",
                     "# Spec\nIntent\n## 验收标准\n- {EXPECTED_RESULT}\n",
                     "# Spec\nIntent\n## 验收标准\n-\n",
                     "# Spec\nIntent\n## 验收标准\n- [ ]\n",
                     "# Spec\nIntent\n## 验收标准\n| Criterion | Expected |\n| --- | --- |\n",
                     "# Spec\nIntent\n## 验收标准\n| Criterion | Expected |\n| --- | --- |\n| | |\n"):
            with self.subTest(text=text):
                self.spec(text)
                rc, out = self.bind()
                self.assertEqual(rc, 2, out)

    def test_section_unique_scoped_and_same_file_requires_selector(self):
        self.start(); self.spec()
        rc, out = self.run_cli("sdd", "spec", "bind", "--spec", "_bmad-output/spec.md")
        self.assertEqual(rc, 2, out)
        for text in ("# Spec\nIntent\n## 验收标准\n## Notes\n- not a criterion\n",
                     "# Spec\nIntent\n## 验收标准\n- one\n## 验收标准\n- two\n"):
            self.spec(text)
            rc, out = self.bind(); self.assertEqual(rc, 2, out)

    def test_separate_criteria_document_and_path_ownership(self):
        self.start(); self.spec()
        criteria = self.spec("# Acceptance\n- Login succeeds.\n", "_bmad-output/criteria.md")
        rc, out = self.bind(criteria=criteria); self.assertEqual(rc, 0, out)
        for path in ("missing.md", "src/product.md", "_bmad-output/../src/product.md"):
            self.spec("# Source\nBody.\n", "src/product.md")
            rc, out = self.bind(path=path, replace=True); self.assertEqual(rc, 2, out)
        (self.repo / "_bmad-output/link.md").symlink_to(self.repo / "src/product.md")
        rc, out = self.bind(path="_bmad-output/link.md", replace=True)
        self.assertEqual(rc, 2, out)

    def test_same_binding_is_unchanged_and_changes_require_replace(self):
        wid = self.start(); self.spec()
        rc, out = self.bind(); self.assertEqual(rc, 0, out)
        before = (self.repo / f".orchestrator/work/{wid}.json").read_bytes()
        rc, out = self.bind(); self.assertEqual(rc, 0, out)
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual(before, (self.repo / f".orchestrator/work/{wid}.json").read_bytes())
        self.spec("# Login\nIntent\n## 验收标准\n- Changed behavior.\n")
        rc, out = self.bind(); self.assertEqual(rc, 2, out)
        rc, out = self.bind(replace=True); self.assertEqual(rc, 0, out)

    def test_spec_change_invalidates_verification_until_rebind_and_verify(self):
        self.start(); self.spec(); self.bind(); self.run_cli("verify")
        self.spec("# Login\nChanged intent.\n## 验收标准\n- Session works.\n")
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)
        self.assertIn("replace", out["next"])
        self.bind(replace=True)
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)
        self.run_cli("verify")
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args))
        rc, out = self.run_cli("done"); self.assertEqual(rc, 0, out)

    def test_binding_change_and_product_write_cannot_reuse_technical_success(self):
        self.start(); self.spec(); self.bind(); self.run_cli("verify")
        (self.repo / "product.txt").write_text("changed")
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)
        self.run_cli("verify")
        rc, out = self.run_cli("block", "--reason", "Need scope decision")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)

    def test_requirement_change_clears_spec_binding(self):
        wid = self.start(); self.spec(); self.bind()
        req = self.read_work(wid)["requirement"]["path"]
        (self.repo / req).write_text("Changed requirement")
        self.run_cli("start", "--requirement", req, "--replace")
        self.assertIn("spec_binding", self.read_work(wid)["sdd"])
        rc, out = self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", "explicit requirement revision")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("spec_binding", self.read_work(wid)["sdd"])

    def test_standard_keeps_native_contract(self):
        self.start(profile="standard"); self.spec(); self.bind(); self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertIn("native", out["reason"])


    def test_dashboard_light_acceptance_is_not_applicable(self):
        from scripts.dashboard import build_dashboard_overview
        self.start(); self.spec(); self.bind(); self.run_cli("verify")
        view = build_dashboard_overview(self.repo)["work_items"][0]
        self.assertEqual(view["light_spec"]["state"], "valid")
        self.assertEqual(view["sdd_acceptance"]["state"], "unconfigured")
        self.assertFalse(view["completion"]["ready"])
