import importlib.util
import json
from scripts import cao
from repair_helpers import RepairCase


class EvidenceTests(RepairCase):
    def policy(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.completion_policy"), "completion_policy is missing")
        from scripts import completion_policy
        return completion_policy

    def workflow(self, wid):
        for args in [
            ("select", "--name", "bmad-build", "--entrypoint", "bmad-build"),
            ("execute", "--command", "bmad-build", "--exit-code", "0"),
        ]:
            rc, out = self.run_cli("sdd", "workflow", *args, "--work", wid)
            self.assertEqual(rc, 0, out)
        artifact = self.repo / "_bmad-output/story.md"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text("# story\n", encoding="utf-8")
        rc, out = self.run_cli("sdd", "workflow", "complete", "--work", wid,
                               "--artifact", "_bmad-output/story.md", "--no-plan")
        self.assertEqual(rc, 0, out)

    def change_standard(self):
        path = self.repo / ".orchestrator/config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        # Retaining configured_at deliberately models an attempted timestamp bypass.
        cfg["verification"]["scripts"][0]["command"] += " --buffer"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        rc, out = self.run_cli('verification', 'check', '--project')
        self.assertEqual(0, rc, out)

    def test_anchor_exists_before_verify(self):
        work = self.read_work(self.start())
        anchor = work.get("acceptance") or {}
        self.assertEqual(anchor.get("version"), 2)
        self.assertEqual(anchor.get("config_sha256"), cao.verification_snapshot(cao.config(self.repo)))

    def test_changed_requirement_drops_review_and_workflow_execution(self):
        wid = self.start(profile="standard")
        self.workflow(wid)
        self.run_cli("gate", "satisfy", "--work", wid, "--gate", "scope_review", "--evidence", "old")
        self.run_cli("decision", "--decision", "keep history")
        req = self.repo / self.read_work(wid)["requirement"]["path"]
        req.write_text("# New scope\n", encoding="utf-8")
        rc, out = self.run_cli("start", "--requirement", req.relative_to(self.repo), "--replace")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "REQUIREMENT_CHANGED_REANCHOR_REQUIRED")
        rc, out = self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", "explicit changed requirement")
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        self.assertEqual(work.get("gate_status"), {})
        self.assertIsNone(work.get("path_assessment"))
        self.assertFalse((work["sdd"].get("workflow") or {}).get("execution"))
        self.assertEqual(work["sdd"]["workflow"]["status"], "selected")
        self.assertEqual(work["decisions"][0]["decision"], "keep history")
        self.assertTrue(work["resume"]["previous_sdd_artifacts"])

    def test_provider_change_clears_assessment_workflow_and_gates(self):
        wid = self.start(profile="standard")
        self.workflow(wid)
        self.run_cli("gate", "satisfy", "--gate", "scope_review", "--evidence", "old provider")
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec/config.yaml").write_text("context: local\n", encoding="utf-8")
        self.run_cli("sdd", "select", "--provider", "openspec")
        rc, out = self.run_cli("start", "--requirement", self.read_work(wid)["requirement"]["path"], "--replace")
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        self.assertIsNone(work.get("path_assessment"))
        self.assertEqual(work.get("gate_status"), {})
        self.assertFalse(work["sdd"].get("workflow"))
        self.assertEqual(work["sdd"]["provider"], "openspec")

    def test_same_requirement_preserves_evidence_and_anchor(self):
        wid = self.start(profile="standard")
        self.workflow(wid)
        self.run_cli("gate", "satisfy", "--gate", "scope_review", "--evidence", "reviewed")
        before = self.read_work(wid)
        rc, out = self.run_cli("start", "--requirement", before["requirement"]["path"], "--replace")
        self.assertEqual(rc, 0, out)
        after = self.read_work(wid)
        for field in ("gate_status", "path_assessment", "acceptance"):
            self.assertEqual(after.get(field), before.get(field))
        self.assertEqual(after["sdd"]["workflow"], before["sdd"]["workflow"])

    def test_manual_gate_binds_requirement_and_assessment_revision(self):
        wid = self.start(profile="standard")
        rc, out = self.run_cli("gate", "satisfy", "--gate", "scope_review", "--evidence", "reviewed")
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        evidence = work["gate_status"]["scope_review"]
        self.assertEqual(evidence.get("requirement_sha256"), work["requirement"]["sha256"])
        self.assertIsNotNone(work["path_assessment"].get("revision"))
        self.assertEqual(evidence.get("assessment_revision"), work["path_assessment"]["revision"])

    def test_legacy_assessment_cannot_certify_a_new_manual_gate(self):
        wid = self.start(profile="standard")
        work = self.read_work(wid)
        work["path_assessment"].pop("revision", None)
        original = json.dumps(work).encode("utf-8")
        cao.work_path(self.repo, wid).write_bytes(original)
        rc, out = self.run_cli("gate", "satisfy", "--gate", "scope_review", "--evidence", "old assessment")
        self.assertEqual(rc, 2, out)
        self.assertIn("path assess", out["next"])
        self.assertEqual(cao.work_path(self.repo, wid).read_bytes(), original)

    def test_reassessment_invalidates_old_manual_gate(self):
        wid = self.start(profile="standard")
        self.run_cli("gate", "satisfy", "--gate", "scope_review", "--evidence", "reviewed")
        before = self.read_work(wid)["path_assessment"].get("revision", 0)
        self.run_cli("path", "assess", "--profile", "standard", "--reason", "changed risk")
        self.assertEqual(self.read_work(wid)["path_assessment"].get("revision"), before + 1)
        rc, out = self.run_cli("gate", "show")
        self.assertEqual(rc, 0, out)
        self.assertIn("scope_review", out["gates"]["pending"])

    def test_changed_standard_same_timestamp_requires_explicit_anchor(self):
        self.start()
        self.change_standard()
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_STANDARD_CHANGED")
        self.assertIn("reanchor", out["next"])

    def test_reanchor_changed_requirement_invalidates_workflow(self):
        wid = self.start(profile="standard")
        self.workflow(wid)
        req = self.repo / self.read_work(wid)["requirement"]["path"]
        req.write_text("# Changed requirement\n", encoding="utf-8")
        rc, out = self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", "new scope")
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        self.assertEqual(work["requirement"]["sha256"], cao.sha256_file(req))
        self.assertIsNone(work.get("path_assessment"))
        self.assertFalse((work["sdd"].get("workflow") or {}).get("execution"))

    def test_reanchor_standard_only_preserves_native_workflow(self):
        wid = self.start()
        self.workflow(wid)
        before = self.read_work(wid)["sdd"]["workflow"]
        self.change_standard()
        rc, out = self.run_cli("reopen", "--reanchor", "--reason", "new standard")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(wid)["sdd"]["workflow"], before)
        self.assertEqual(self.read_work(wid).get("acceptance", {}).get("config_sha256"),
                         cao.verification_snapshot(cao.config(self.repo)))
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["verification"].get("version"), 2)

    def test_legacy_active_does_not_gain_implicit_anchor_on_start(self):
        wid = self.start()
        work = self.read_work(wid)
        work.pop("acceptance", None)
        cao.work_path(self.repo, wid).write_text(json.dumps(work), encoding="utf-8")
        rc, out = self.run_cli("start", "--requirement", work["requirement"]["path"], "--replace")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(self.read_work(wid).get("acceptance"))
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_STANDARD_CHANGED")
        self.assertIn("reanchor", out["next"])

    def test_evidence_upgrade_backs_up_integrity_v2_record_once(self):
        wid = self.start()
        work = self.read_work(wid)
        work.pop("acceptance", None)
        original = (json.dumps(work, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        cao.work_path(self.repo, wid).write_bytes(original)
        backup = self.repo / f".orchestrator/backups/integrity-v2/work/{wid}.json"
        for _ in range(2):
            rc, out = self.run_cli("reopen", "--reanchor", "--reason", "upgrade evidence")
            self.assertEqual(rc, 0, out)
            self.assertTrue(backup.is_file(), "evidence upgrade must back up old bytes")
            self.assertEqual(backup.read_bytes(), original)

    def test_old_completed_is_not_recertified_when_read(self):
        wid = self.start()
        work = self.read_work(wid)
        work.pop("acceptance", None)
        work["status"] = "completed"
        original = json.dumps(work).encode("utf-8")
        cao.work_path(self.repo, wid).write_bytes(original)
        rc, out = self.run_cli("status", "--work", wid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(cao.work_path(self.repo, wid).read_bytes(), original)
        self.assertTrue(out["work"]["work_item"].get("legacy_evidence"))
        self.assertEqual(out["work"]["work_item"]["status"], "completed")

    def test_policy_hash_and_missing_evidence_are_explicit(self):
        policy = self.policy()
        a = [{"name": "a", "required": True, "command": "test"}]
        b = [{"command": "test", "name": "a", "required": True}]
        self.assertEqual(policy.acceptance_hash(a), policy.acceptance_hash(b))
        self.assertFalse(policy.evidence_matches({}, {}))
        self.assertFalse(policy.evidence_matches({"path_assessment": {"revision": 1}}, {}))
