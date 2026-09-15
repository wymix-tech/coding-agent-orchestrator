import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

import evidence_factory

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod

extractor = load_module("fact_extractor", ROOT / "scripts" / "fact_extractor.py")
resolver = load_module("fact_resolver", ROOT / "scripts" / "fact_resolver.py")
decision = load_module("decision_engine_v4", ROOT / "scripts" / "decision_engine.py")


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class RepoFixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        sh(self.root, "git", "init", "-q")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "Test")
        (self.root / "package.json").write_text('{"name":"fixture"}\n')
        (self.root / "src").mkdir()
        (self.root / "src" / "base.js").write_text("export const x = 1;\n")
        sh(self.root, "git", "add", ".")
        sh(self.root, "git", "commit", "-qm", "base")

    def close(self):
        self.tmp.cleanup()


class FactExtractorTests(unittest.TestCase):
    def setUp(self):
        self.fx = RepoFixture()

    def tearDown(self):
        self.fx.close()

    def test_absence_of_signal_never_becomes_false(self):
        draft = extractor.extract(self.fx.root, request="change a local helper")
        self.assertIsNone(draft["risk"]["authn_authz"])
        self.assertIsNone(draft["risk"]["payment_or_financial"])
        self.assertIsNone(draft["scope"]["external_consumers"])

    def test_changed_proto_sets_public_contract_and_compatibility(self):
        p = self.fx.root / "api"
        p.mkdir()
        f = p / "service.proto"
        f.write_text('syntax = "proto3";\nmessage A {}\n')
        draft = extractor.extract(self.fx.root)
        self.assertTrue(draft["scope"]["public_contract_change"])
        self.assertTrue(draft["verification"]["compatibility_or_migration"])
        self.assertEqual(1, draft["scope"]["files_estimate"])
        self.assertEqual("observed", draft["provenance"]["scope.public_contract_change"][0]["strength"])

    def test_auth_path_is_hint_not_final_fact(self):
        d = self.fx.root / "src" / "auth"
        d.mkdir()
        (d / "token_validator.js").write_text("export function valid(x){return !!x}\n")
        draft = extractor.extract(self.fx.root)
        self.assertIsNone(draft["risk"]["authn_authz"])
        hints = [x for x in draft["extraction"]["suggestions"] if x["fact"] == "risk.authn_authz"]
        self.assertTrue(hints)
        self.assertEqual("heuristic", hints[0]["strength"])

    def test_destructive_migration_is_observed_high_risk(self):
        d = self.fx.root / "db" / "migration"
        d.mkdir(parents=True)
        f = d / "V2__drop.sql"
        f.write_text("DROP TABLE old_sessions;\n")
        draft = extractor.extract(self.fx.root)
        self.assertTrue(draft["risk"]["persistent_data_change"])
        self.assertTrue(draft["risk"]["destructive_migration"])
        self.assertTrue(draft["risk"]["irreversible_or_hard_to_recover"])

    def test_snapshot_id_changes_when_file_content_changes(self):
        f = self.fx.root / "src" / "base.js"
        f.write_text("export const x = 2;\n")
        first = extractor.extract(self.fx.root)["extraction"]["snapshot_id"]
        f.write_text("export const x = 3;\n")
        second = extractor.extract(self.fx.root)["extraction"]["snapshot_id"]
        self.assertNotEqual(first, second)

    def test_resolver_cannot_override_mechanically_proven_fact_same_snapshot(self):
        p = self.fx.root / "api"
        p.mkdir()
        (p / "service.proto").write_text('syntax = "proto3";\n')
        draft = extractor.extract(self.fx.root)
        self.assertTrue(draft["scope"]["public_contract_change"])
        doc = {"resolutions": [{
            "path": "scope.public_contract_change", "value": False,
            "source_type": "agent", "source": "manual", "evidence": "claim",
            "strength": "authoritative"
        }]}
        with self.assertRaises(ValueError):
            resolver.apply_resolutions(draft, doc)

    def test_sdd_markers_and_gate_signals_are_observations(self):
        (self.fx.root / "openspec").mkdir()
        (self.fx.root / "_bmad").mkdir()
        wf = self.fx.root / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("run: semgrep --config auto\n")
        draft = extractor.extract(self.fx.root)
        sdd = draft["extraction"]["observations"]["sdd"]
        self.assertTrue(sdd["multiple_sdd_markers"])
        self.assertIn("openspec", sdd["detected"])
        self.assertIn("bmad", sdd["detected"])
        gates = draft["extraction"]["observations"]["quality_gate_signals"]
        self.assertTrue(any(g["gate"] == "semgrep" for g in gates))

    def test_resolution_rejects_heuristic_final_evidence(self):
        draft = extractor.extract(self.fx.root)
        doc = {"resolutions": [{
            "path": "risk.authn_authz", "value": True, "source_type": "path_hint",
            "source": "src/auth", "evidence": "name match", "strength": "heuristic"
        }]}
        with self.assertRaises(ValueError):
            resolver.apply_resolutions(draft, doc)


    def test_resolution_rejects_wrong_fact_type(self):
        draft = extractor.extract(self.fx.root)
        doc = {"resolutions": [{
            "path": "risk.authn_authz", "value": "false", "source_type": "inspection",
            "source": "repo", "evidence": "bad type", "strength": "observed"
        }]}
        with self.assertRaises(ValueError):
            resolver.apply_resolutions(draft, doc)

    def test_false_resolution_requires_negative_proof(self):
        draft = extractor.extract(self.fx.root)
        doc = {"resolutions": [{
            "path": "scope.external_consumers", "value": False, "source_type": "inspection",
            "source": "repo", "evidence": "none found", "strength": "observed"
        }]}
        with self.assertRaises(ValueError):
            resolver.apply_resolutions(draft, doc)


class StrictEvidenceTests(unittest.TestCase):
    def complete_facts(self):
        facts = json.loads((ROOT / "examples" / "work-facts-trivial.json").read_text())
        facts["provenance"] = {}
        for path in decision.REQUIRED_PATHS:
            value = decision.get_path(facts, path)
            entry = {
                "value": value,
                "source_type": "fixture",
                "source": "bounded test fixture",
                "evidence": "explicit fixture declaration",
                "strength": "authoritative",
            }
            if value is False:
                # A false is only carried by the search that found nothing, never by the
                # fixture describing itself as authoritative.
                entry["negative_proof"] = {"ref": f"examples/negative-proof/{path}.json",
                                           "search": f"no occurrence of {path} in the analyzed content",
                                           "matches": 0}
            facts["provenance"][path] = [entry]
        return facts

    def test_strict_mode_accepts_complete_provenance(self):
        result = decision.classify(self.complete_facts(), strict_evidence=True)
        self.assertEqual("CLASSIFIED", result["status"])
        self.assertEqual("TRIVIAL", result["flow_profile"])


    def test_decision_engine_rejects_wrong_fact_type(self):
        facts = self.complete_facts()
        facts["risk"]["authn_authz"] = "false"
        result = decision.classify(facts, strict_evidence=True)
        self.assertEqual("INVALID_FACTS", result["status"])
        self.assertIn("risk.authn_authz", result["type_errors"])

    def test_end_to_end_resolution_can_reach_classified(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            sh(repo, "git", "init", "-q")
            sh(repo, "git", "config", "user.email", "test@example.com")
            sh(repo, "git", "config", "user.name", "Test")
            (repo / "README.md").write_text("fixture")
            sh(repo, "git", "add", ".")
            sh(repo, "git", "commit", "-qm", "base")
            draft = extractor.extract(repo)
            trivial = json.loads((ROOT / "examples" / "work-facts-trivial.json").read_text())
            resolutions = []
            for path in decision.REQUIRED_PATHS:
                if decision.get_path(draft, path) is not None:
                    continue
                value = decision.get_path(trivial, path)
                entry = {
                    "path": path,
                    "value": value,
                    "source_type": "fixture",
                    "source": "bounded authoritative fixture",
                    "evidence": "explicit test declaration",
                    "strength": "authoritative"
                }
                if value is False:
                    entry["negative_proof"] = evidence_factory.negative_proof_entry(repo, path)
                resolutions.append(entry)
            resolved = resolver.apply_resolutions(draft, {"resolutions": resolutions})
            result = decision.classify(resolved, strict_evidence=True)
            self.assertEqual("CLASSIFIED", result["status"])
            self.assertEqual("TRIVIAL", result["flow_profile"])

    def test_strict_mode_rejects_missing_provenance(self):
        facts = self.complete_facts()
        del facts["provenance"]["risk.authn_authz"]
        result = decision.classify(facts, strict_evidence=True)
        self.assertEqual("NEEDS_EVIDENCE", result["status"])
        self.assertIn("risk.authn_authz", result["missing_evidence"])

    def test_strict_mode_rejects_conflicting_accepted_evidence(self):
        facts = self.complete_facts()
        facts["provenance"]["risk.authn_authz"].append({
            "value": True,
            "source_type": "conflict_fixture",
            "source": "other authority",
            "evidence": "conflicting accepted claim",
            "strength": "authoritative"
        })
        result = decision.classify(facts, strict_evidence=True)
        self.assertEqual("NEEDS_EVIDENCE", result["status"])
        self.assertIn("risk.authn_authz", result["conflicting_evidence"])

    def test_strict_mode_rejects_heuristic_only(self):
        facts = self.complete_facts()
        facts["provenance"]["risk.authn_authz"] = [{
            "value": False,
            "source_type": "path_hint",
            "source": "repo",
            "evidence": "no auth-looking path",
            "strength": "heuristic"
        }]
        result = decision.classify(facts, strict_evidence=True)
        self.assertEqual("NEEDS_EVIDENCE", result["status"])
        self.assertIn("risk.authn_authz", result["weak_evidence"])


if __name__ == "__main__":
    unittest.main()
