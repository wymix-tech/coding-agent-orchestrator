"""Replay literal quote failures and protect the new CAO input boundary."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest

from scripts import cao, route_inputs
from scripts.native_route import normalize_rules, RouteValidationError
from tests.repair_helpers import RepairCase


class RouteInputs(RepairCase):
    def setUp(self):
        super().setUp()
        self.wid = self.start(profile="standard")
        self.skill = ".agents/skills/bmad-build/SKILL.md"
        path = self.repo / self.skill
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: bmad-build\ndescription: Native fixture\n---\nFollow the installed plan.\n", encoding="utf-8")
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        self.source = "_bmad/render/current/step-02-plan.md"
        source = self.repo / self.source
        source.parent.mkdir(parents=True)
        self.literal = "Otherwise write the full spec. Set `route: 'dispatch'` and continue."
        source.write_bytes(("\ufeff# Plan\r\n\r\n" + self.literal + "\r\n保留实际规则。\r\n").encode("utf-8"))
        self.spec = "_bmad-output/spec-fixture.md"
        spec = self.repo / self.spec
        spec.parent.mkdir()
        spec.write_text("---\nroute: dispatch\nstatus: draft\n---\n<frozen-after-approval>\n## Intent\nActual fixture intent.\n</frozen-after-approval>\n", encoding="utf-8")
        self.run_cli("sdd", "artifact", "bind", "--artifact", self.spec)
        self.target = route_inputs.input_path(self.repo, self.read_work(self.wid))

    def draft(self):
        rc, out = self.run_cli("sdd", "route", "prepare")
        self.assertEqual(rc, 0, out)
        return out

    def fill(self):
        raw = json.loads(self.target.read_text())
        raw.update(provider_version="fixture-version", route="direct-build", reason="Actual installed dispatch rule.",
                   omitted=[{"name": "standalone planning", "reason": "Native SPEC holds this bounded intent.", "replaced_by": self.spec}])
        self.target.write_text(json.dumps(raw), encoding="utf-8")
        return raw

    def hook(self, path=None, **kwargs):
        payload = {"event": "pre_tool", "session_id": "route-input-fixture", "tool_name": "write", "tool_input": {"path": path, "content": "{}"}}
        payload.update(kwargs)
        return self.run_cli("host-event", "--host", "pi", payload=payload)[1]

    def test_prepare_rule_check_and_propose_preserve_native_approval(self):
        work_file = self.repo / f".orchestrator/work/{self.wid}.json"
        before = work_file.read_bytes()
        out = self.draft()
        self.assertEqual(out["declaration_path"], f".orchestrator/inputs/{self.wid}/route.json")
        self.assertEqual(out["declaration"]["route"], "")
        self.assertEqual(out["declaration"]["provider_version"], "")
        self.fill()
        saved = self.target.read_bytes()
        self.assertEqual(self.draft()["status"], "existing_draft_preserved")
        self.assertEqual(self.target.read_bytes(), saved)
        rc, out = self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "4")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["rule"]["quote"], self.literal + "\n保留实际规则。")
        self.assertEqual(out["rule"]["sha256"], hashlib.sha256((self.repo / self.source).read_bytes()).hexdigest())
        self.assertEqual(work_file.read_bytes(), before)
        rc, checked = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 0, checked)
        self.assertTrue(checked["decision"]["approval_required"])
        self.assertEqual(work_file.read_bytes(), before)
        rc, proposed = self.run_cli("sdd", "route", "propose", "--declaration", str(self.target))
        self.assertEqual(rc, 0, proposed)
        self.assertEqual(proposed["implementation"]["reason"], "NATIVE_SPEC_APPROVAL_REQUIRED")
        work = self.read_work(self.wid)
        self.assertNotIn("spec_approval", work["sdd"])
        self.assertEqual(work["sdd"]["native_ref"], self.spec)
        self.assertNotIn(str(self.target.relative_to(self.repo)), [a["path"] for a in work["sdd"]["artifacts"]])

    def test_all_quote_errors_are_named_and_repaired_without_weakening_checks(self):
        self.draft()
        raw = self.fill()
        sha = hashlib.sha256((self.repo / self.source).read_bytes()).hexdigest()
        raw["rules"] = [
            {"path": self.source, "sha256": sha, "quote": self.literal.replace("`", "")},
            {"path": self.source, "sha256": sha, "quote": "Otherwise ... continue."},
        ]
        self.target.write_text(json.dumps(raw), encoding="utf-8")
        before = self.read_work(self.wid)
        rc, out = self.run_cli("sdd", "route", "propose", "--declaration", str(self.target))
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_ROUTE_RULE_EVIDENCE_MISMATCH")
        self.assertEqual([d["field"] for d in out["diagnostics"]], ["$.rules[0].quote", "$.rules[1].quote"])
        self.assertTrue(all(d["sha256_matches"] for d in out["diagnostics"]))
        self.assertIn("Do not resubmit unchanged", out["next"])
        self.assertEqual(self.read_work(self.wid), before)
        self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "3", "--index", "0")
        rc, out = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 2, out)
        self.assertEqual([d["field"] for d in out["diagnostics"]], ["$.rules[1].quote"])
        self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "3", "--index", "1")
        rc, out = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(self.wid), before)

    def test_hash_and_quote_diagnostics_are_independent(self):
        rule = {"path": self.source, "sha256": "0" * 64, "quote": self.literal.replace("`", "")}
        with self.assertRaises(RouteValidationError) as caught:
            normalize_rules(self.repo, "bmad", [rule], [])
        errors = caught.exception.diagnostics
        self.assertEqual([d["field"] for d in errors], ["$.rules[0].sha256", "$.rules[0].quote"])
        self.assertEqual(errors[0]["expected"], hashlib.sha256((self.repo / self.source).read_bytes()).hexdigest())
        self.assertFalse(errors[1]["sha256_matches"])

    def test_legacy_copy_preserves_source_and_existing_draft(self):
        self.draft()
        raw = self.fill()
        legacy = self.repo / "_bmad-output/cao/legacy/route.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(raw))
        self.target.unlink()
        before = legacy.read_bytes()
        rc, out = self.run_cli("sdd", "route", "prepare", "--from", str(legacy))
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["declaration"], raw)
        self.assertEqual(legacy.read_bytes(), before)
        legacy.write_text("{}")
        self.assertEqual(self.run_cli("sdd", "route", "prepare", "--from", str(legacy))[1]["status"], "existing_draft_preserved")
        self.assertEqual(json.loads(self.target.read_text()), raw)

    def test_rule_replaces_a_legacy_absolute_source_in_place(self):
        self.draft()
        raw = self.fill()
        raw["rules"] = [{"path": str(self.repo / self.source), "sha256": "0" * 64, "quote": "Rewritten summary"}]
        self.target.write_text(json.dumps(raw))
        rc, out = self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "3")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["rule_index"], 0)
        self.assertEqual(len(json.loads(self.target.read_text())["rules"]), 1)
        self.assertEqual(self.run_cli("sdd", "route", "check")[0], 0)

    def test_parent_input_symlink_cannot_write_into_native_outputs(self):
        directory = self.repo / ".orchestrator/inputs"
        try:
            directory.symlink_to(self.repo / "_bmad-output", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.hook(str(self.target))["decision"], "deny")
        rc, out = self.run_cli("sdd", "route", "prepare")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_ROUTE_INPUT_SYMLINK")
        self.assertFalse((self.repo / "_bmad-output" / self.wid).exists())

    def test_invalid_evidence_range_and_foreign_source_do_not_change_draft(self):
        self.draft()
        before = self.target.read_bytes()
        for first, last in ((0, 1), (3, 2), (1, 99), (2, 2)):
            rc, out = self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", str(first), "--end-line", str(last))
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "NATIVE_ROUTE_RULE_LINE_RANGE_INVALID")
        rc, out = self.run_cli("sdd", "route", "rule", "--source", f".orchestrator/work/{self.wid}.json", "--start-line", "1", "--end-line", "1")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_ROUTE_RULE_SOURCE_INVALID")
        self.assertEqual(self.target.read_bytes(), before)

    def test_only_current_input_is_writable_and_it_is_not_a_native_artifact(self):
        self.draft()
        self.assertEqual(self.hook(str(self.target))["decision"], "allow")
        for path in (".orchestrator/inputs/other/route.json", f".orchestrator/inputs/{self.wid}/product.py",
                     ".orchestrator/config.json", f".orchestrator/work/{self.wid}.json", "src/auth.py"):
            self.assertEqual(self.hook(path)["decision"], "deny", path)
        patch = f"*** Begin Patch\n*** Update File: {self.target.relative_to(self.repo)}\n*** Move to: .orchestrator/config.json\n@@\n-old\n+new\n*** End Patch"
        self.assertEqual(self.hook(tool_name="apply_patch", tool_input={"patch": patch})["decision"], "deny")
        self.run_cli("block", "--reason", "Fixture paused", "--suspend")
        self.assertEqual(self.hook(str(self.target))["decision"], "deny")

    def test_input_links_cannot_overwrite_managed_state(self):
        self.draft()
        managed = self.repo / ".orchestrator/config.json"
        before = managed.read_bytes()
        self.target.unlink()
        try:
            self.target.symlink_to(managed)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.hook(str(self.target))["decision"], "deny")
        rc, out = self.run_cli("sdd", "route", "prepare")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_ROUTE_INPUT_SYMLINK")
        self.target.unlink()
        os.link(managed, self.target)
        self.assertEqual(self.hook(str(self.target))["decision"], "deny")
        self.assertEqual(self.run_cli("sdd", "route", "prepare")[0], 2)
        self.assertEqual(managed.read_bytes(), before)

    def test_wrapper_chmod_is_allowed_but_mixed_product_and_unsafe_modes_are_not(self):
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"] = []
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        script = self.repo / "scripts/verification/test.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nexit 2\n")
        for mode in ("+x", "u+x", "755"):
            command = f"chmod {mode} scripts/verification/test.sh"
            self.assertEqual(self.hook(tool_name="bash", tool_input={"command": command})["decision"], "allow")
            self.assertEqual(self.hook(tool_name="bash", tool_input=command)["decision"], "allow")
        for command in ("chmod -R +x scripts/verification", "chmod 777 scripts/verification/test.sh",
                        "chmod +x .orchestrator/config.json", "mkdir -p scripts/verification src",
                        "chmod +x scripts/verification/test.sh && touch product.py"):
            self.assertEqual(self.hook(tool_name="bash", tool_input={"command": command})["decision"], "deny", command)
        for command in ("chmod -w .orchestrator/config.json", "chmod -R -w .orchestrator/work", "chmod -- -w .orchestrator/config.json"):
            self.assertTrue(cao.write_targets_protected_state(self.repo, {"tool_name": "bash", "tool_input": {"command": command}}), command)


class InputLocations(unittest.TestCase):
    def test_provider_does_not_determine_cao_input_location(self):
        for provider in ("bmad", "openspec", "speckit"):
            self.assertEqual(route_inputs.declaration_path({"id": "work-1", "sdd": {"provider": provider}}),
                             ".orchestrator/inputs/work-1/route.json")
