"""Fixes for the two intake dead ends: no evidence scaffold, and silent retry loops."""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


fact_resolver = load_module("fact_resolver_scaffold", ROOT / "scripts" / "fact_resolver.py")
fact_extractor = load_module("fact_extractor_scaffold", ROOT / "scripts" / "fact_extractor.py")
pipeline = load_module("semantic_intake_pipeline", ROOT / "scripts" / "semantic_intake_pipeline.py")


def make_repo():
    tmp = tempfile.TemporaryDirectory()
    root = pathlib.Path(tmp.name)
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    run = lambda *a: subprocess.run(a, cwd=root, check=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env={**env, "PATH": "/usr/bin:/bin"})
    run("git", "init", "-q", ".")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
    return tmp, root


class ResolutionTemplateTests(unittest.TestCase):
    def test_template_has_one_typed_entry_per_queued_fact(self):
        with tempfile.TemporaryDirectory() as td:
            draft = fact_extractor.extract(pathlib.Path(td), request="新增用户查询 REST API")
            queue = draft["extraction"]["resolution_queue"]
            template = fact_resolver.build_resolution_template(draft)
            self.assertEqual(len(queue), template["unresolved_count"])
            self.assertEqual([i["path"] for i in template["resolutions"]], queue)
            for item in template["resolutions"]:
                for field in fact_resolver.REQUIRED_FIELDS:
                    self.assertIn(field, item, item["path"])
                self.assertIn(item["value_type"], {"boolean", "non_negative_integer"})

    def test_template_distinguishes_counts_from_booleans(self):
        with tempfile.TemporaryDirectory() as td:
            draft = fact_extractor.extract(pathlib.Path(td), request="x")
            template = fact_resolver.build_resolution_template(draft)
            by_path = {i["path"]: i for i in template["resolutions"]}
            self.assertEqual("non_negative_integer", by_path["scope.files_estimate"]["value_type"])
            self.assertEqual("boolean", by_path["risk.authn_authz"]["value_type"])

    def test_template_states_the_rules_that_reject_resolutions(self):
        with tempfile.TemporaryDirectory() as td:
            draft = fact_extractor.extract(pathlib.Path(td), request="x")
            rules = fact_resolver.build_resolution_template(draft)["rules"]
            self.assertEqual(sorted(["authoritative", "derived", "observed"]), rules["strength_allowed"])
            self.assertTrue(rules["heuristic_is_hint_only"])
            self.assertTrue(rules["false_requires_negative_proof_unless_authoritative"])

    def test_heuristic_hints_are_attached_but_marked_hint_only(self):
        with tempfile.TemporaryDirectory() as td:
            draft = fact_extractor.extract(pathlib.Path(td), request="优化性能")
            template = fact_resolver.build_resolution_template(draft)
            hinted = [i for i in template["resolutions"] if "hint" in i]
            self.assertTrue(hinted, "a hint should be attached to the fact it suggests")
            for item in hinted:
                self.assertEqual("heuristic", item["hint"]["strength"])
                self.assertIn("cannot finalize", item["hint"]["note"])
                self.assertNotEqual("heuristic", item["strength"])

    def test_filled_template_is_accepted_by_the_resolver(self):
        with tempfile.TemporaryDirectory() as td:
            draft = fact_extractor.extract(pathlib.Path(td), request="新增用户查询 REST API")
            template = fact_resolver.build_resolution_template(draft)
            filled = []
            for item in template["resolutions"]:
                value = 0 if item["value_type"] == "non_negative_integer" else True
                filled.append({**{k: v for k, v in item.items() if k != "hint"},
                               "value": value, "source_type": "code_inspection",
                               "source": "src/app.py", "evidence": "checked", "strength": "observed"})
            if any(i["value"] is False for i in filled):
                for i in filled:
                    if i["value"] is False:
                        i["negative_proof"] = "bounded inspection"
            resolved = fact_resolver.apply_resolutions(draft, {"resolutions": filled})
            self.assertEqual([], resolved["extraction"]["resolution_queue"])
            self.assertEqual("COMPLETE", fact_resolver.build_resolution_template(resolved)["status"])


class IntakeLoopDetectionTests(unittest.TestCase):
    def run_intake(self, root, request, resolutions=None):
        outdir = root / ".orchestrator" / "intake"
        argv = ["--repo", str(root), "--request", request, "--output-dir", str(outdir),
                "--skip-context", "--skip-policy", "--allow-cbm-unavailable"]
        if resolutions:
            argv += ["--resolutions", str(resolutions)]
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pipeline.main(argv)
        return code, json.loads(buf.getvalue().strip())

    def test_first_run_never_warns(self):
        tmp, root = make_repo()
        with tmp:
            _, summary = self.run_intake(root, "新增用户查询 REST API")
            self.assertEqual([], summary["warnings"])
            self.assertTrue(pathlib.Path(summary["artifacts"]["resolutions_template"]).exists())

    def test_identical_rerun_warns_and_counts_up(self):
        tmp, root = make_repo()
        with tmp:
            request = "新增用户查询 REST API"
            self.run_intake(root, request)
            _, second = self.run_intake(root, request)
            codes = [w["code"] for w in second["warnings"]]
            self.assertIn("IDENTICAL_INPUT_NO_NEW_EVIDENCE", codes)
            self.assertEqual(2, second["warnings"][0]["repeat_count"])
            self.assertEqual("supply_fact_resolutions", second["warnings"][0]["next_action"])
            _, third = self.run_intake(root, request)
            self.assertEqual(3, third["warnings"][0]["repeat_count"])

    def test_new_evidence_clears_the_warning(self):
        tmp, root = make_repo()
        with tmp:
            request = "新增用户查询 REST API"
            self.run_intake(root, request)
            self.run_intake(root, request)
            resolutions = root / "fact-resolutions.json"
            resolutions.write_text(json.dumps({"resolutions": [{
                "path": "risk.authn_authz", "value": True, "source_type": "user_requirement",
                "source": "request", "evidence": "JWT 无状态方案", "strength": "authoritative"}]}),
                encoding="utf-8")
            _, summary = self.run_intake(root, request, resolutions)
            self.assertEqual([], summary["warnings"])

    def test_same_resolutions_file_advises_strengthening_not_supplying(self):
        tmp, root = make_repo()
        with tmp:
            request = "新增用户查询 REST API"
            resolutions = root / "fact-resolutions.json"
            resolutions.write_text(json.dumps({"resolutions": [{
                "path": "risk.authn_authz", "value": True, "source_type": "user_requirement",
                "source": "request", "evidence": "JWT", "strength": "authoritative"}]}),
                encoding="utf-8")
            self.run_intake(root, request, resolutions)
            _, summary = self.run_intake(root, request, resolutions)
            self.assertTrue(summary["warnings"])
            self.assertEqual("strengthen_resolution_evidence", summary["warnings"][0]["next_action"])

    def test_warning_points_at_the_scaffold(self):
        tmp, root = make_repo()
        with tmp:
            request = "新增用户查询 REST API"
            self.run_intake(root, request)
            _, summary = self.run_intake(root, request)
            self.assertTrue(summary["warnings"][0]["resolutions_template"].endswith("fact-resolutions.template.json"))


if __name__ == "__main__":
    unittest.main()
