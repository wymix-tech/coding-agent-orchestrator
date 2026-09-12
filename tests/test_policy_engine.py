import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ecc_rules_adapter
import policy_engine
import verification_planner


class PolicyEngineTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name)
        (self.repo / "pom.xml").write_text(
            '<project><dependency><artifactId>spring-boot-starter-web</artifactId></dependency></project>',
            encoding="utf-8",
        )
        pol = self.repo / ".orchestrator" / "policies"
        pol.mkdir(parents=True)
        # Copy starter policies from the skill package.
        for name in ("manifest.yaml", "common-engineering.yaml", "spring-boot-layered.yaml"):
            (pol / name).write_text((ROOT / "policies" / name).read_text(encoding="utf-8"), encoding="utf-8")
        self.manifest = pol / "manifest.yaml"

    def tearDown(self):
        self.td.cleanup()

    def impact(self, changed):
        return {
            "changes": {"changed_files": changed, "changed_symbols": []},
            "impact": {"affected_files": changed},
            "boundaries": {"cross_service": False, "async_boundary": False},
            "contracts": {"public_contract_signal": False},
        }

    def test_routes_spring_web_layer_rules(self):
        f = "src/main/java/com/acme/web/UserController.java"
        plan = policy_engine.route(self.repo, self.impact([f]), self.manifest, "implementation")
        ids = {r["id"] for r in plan["applicable_rules"]}
        self.assertIn("ARCH-SPRING-LAYER-001", ids)
        self.assertIn("ARCH-SPRING-RESP-001", ids)
        self.assertIn("ENG-TEST-001", ids)
        self.assertNotIn("ARCH-SPRING-LAYER-003", ids)

    def test_web_to_dao_import_is_blocking_violation(self):
        p = self.repo / "src/main/java/com/acme/web/UserController.java"
        p.parent.mkdir(parents=True)
        p.write_text(
            "package com.acme.web;\nimport com.acme.dao.UserDao;\nclass UserController { UserDao dao; }\n",
            encoding="utf-8",
        )
        plan = policy_engine.route(self.repo, self.impact([p.relative_to(self.repo).as_posix()]), self.manifest, "implementation")
        ev = policy_engine.evaluate(self.repo, plan, self.manifest)
        self.assertEqual(ev["status"], "FAILED")
        flat = [v for r in ev["blocking_violations"] for v in r["violations"]]
        self.assertTrue(any(v["to_layer"] == "dao" for v in flat))

    def test_web_to_service_is_allowed(self):
        p = self.repo / "src/main/java/com/acme/web/UserController.java"
        p.parent.mkdir(parents=True)
        p.write_text(
            "package com.acme.web;\nimport com.acme.service.UserService;\nclass UserController { UserService svc; }\n",
            encoding="utf-8",
        )
        plan = policy_engine.route(self.repo, self.impact([p.relative_to(self.repo).as_posix()]), self.manifest, "implementation")
        ev = policy_engine.evaluate(self.repo, plan, self.manifest)
        self.assertEqual(ev["status"], "PASSED")

    def test_policy_context_is_compact_and_reference_based(self):
        f = "src/main/java/com/acme/web/UserController.java"
        plan = policy_engine.route(self.repo, self.impact([f]), self.manifest, "implementation")
        text = policy_engine.render_context(plan)
        self.assertIn("ARCH-SPRING-LAYER-001", text)
        self.assertNotIn("full rule corpus", text.lower())

    def test_state_gates_include_required_architecture_gate(self):
        f = "src/main/java/com/acme/web/UserController.java"
        p = self.repo / f
        p.parent.mkdir(parents=True)
        p.write_text("package com.acme.web; class UserController {}", encoding="utf-8")
        plan = policy_engine.route(self.repo, self.impact([f]), self.manifest, "implementation")
        ev = policy_engine.evaluate(self.repo, plan, self.manifest)
        gates = policy_engine.build_state_gates(plan, ev)
        self.assertTrue(any(g["required"] and g["gate"] == "architecture-layers" for g in gates))
        self.assertTrue(any(g["engine"] == "v6-policy-check" and g["status"] == "passed" for g in gates))

    def test_verification_plan_carries_policy_gates(self):
        f = "src/main/java/com/acme/web/UserController.java"
        plan = policy_engine.route(self.repo, self.impact([f]), self.manifest, "implementation")
        vp = verification_planner.build_plan({}, self.impact([f]), {"flow_profile": "STANDARD"}, plan)
        self.assertGreater(len(vp["policy_gates"]), 0)
        self.assertEqual(vp["policy_snapshot_id"], plan["policy_snapshot_id"])

    def test_illegal_policy_downgrade_is_rejected(self):
        pol = self.repo / ".orchestrator/policies"
        (pol / "base.yaml").write_text(
            "id: base\nrules:\n  - id: X\n    level: MUST\n    title: X\n",
            encoding="utf-8",
        )
        (pol / "override.yaml").write_text(
            "id: override\nrules:\n  - id: X\n    level: SHOULD\n    title: weaker\n",
            encoding="utf-8",
        )
        (pol / "m.yaml").write_text(
            "packs:\n  - {id: base, path: base.yaml, precedence: 100}\n  - {id: override, path: override.yaml, precedence: 200}\n",
            encoding="utf-8",
        )
        _, rules, conflicts = policy_engine.load_policy(self.repo, pol / "m.yaml")
        self.assertEqual(rules[0]["level"], "MUST")
        self.assertEqual(conflicts[0]["type"], "ILLEGAL_DOWNGRADE")


class EccRulesAdapterTests(unittest.TestCase):
    def test_ecc_rules_are_non_authoritative_and_path_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "common").mkdir()
            (root / "java").mkdir()
            (root / "common/testing.md").write_text("# Testing\n", encoding="utf-8")
            (root / "java/patterns.md").write_text(
                "---\npaths: **/*.java\ndescription: Java patterns\n---\n# Java\n",
                encoding="utf-8",
            )
            cat = ecc_rules_adapter.discover(root)
            selected = ecc_rules_adapter.relevant_rules(cat, ["src/A.java"], ["common", "java"])
            self.assertEqual(len(selected), 2)
            self.assertTrue(all(r["authority"] == "external_guidance" for r in selected))
            self.assertTrue(all(r["blocking"] is False for r in selected))


if __name__ == "__main__":
    unittest.main()
