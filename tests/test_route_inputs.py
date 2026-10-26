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

    def test_installed_skill_steps_and_references_are_real_rule_sources(self):
        root = (self.repo / self.source).parent
        (root / 'step-03-implement.md').write_text('Native implementation.\n')
        (root / 'step-04-review.md').write_text('#### Blind (`blind-hunter`)\nIndependent review.\n')
        self.draft()
        self.fill()
        self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "4")
        for relative in ("step-02-plan.md", "spec-template.md", "references/routing.md"):
            source = (self.repo / self.skill).parent / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(self.literal + "\n", encoding="utf-8")
            # Both CLI absolute paths and portable declaration paths work.
            rc, out = self.run_cli("sdd", "route", "rule", "--source", source,
                                   "--start-line", "1", "--end-line", "1")
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["rule"]["path"], source.relative_to(self.repo).as_posix())
        self.assertEqual(self.run_cli("sdd", "route", "check")[0], 0)
        rc, out = self.run_cli("sdd", "route", "propose", "--declaration", self.target)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["implementation"]["reason"], "NATIVE_SPEC_APPROVAL_REQUIRED")
        self.assertEqual(self.hook("src/auth.py")["decision"], "deny")

    def test_rule_source_diagnostics_distinguish_missing_and_unowned_sources(self):
        self.draft()
        before = self.target.read_bytes()
        for source, issue in ((".agents/skills/bmad-build/missing.md", "source_missing"),
                              (self.spec, "not_installed_native_rule"),
                              (".agents/rules/login.md", "not_installed_native_rule")):
            rc, out = self.run_cli("sdd", "route", "rule", "--source", source,
                                   "--start-line", "1", "--end-line", "1")
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["diagnostic"]["issue"], issue)
            self.assertIn(".agents/skills/bmad-build", out["diagnostic"]["allowed_roots"])
            self.assertIn("not a generated SPEC", out["diagnostic"]["expected"])
        raw = json.loads(before)
        raw.update(provider_version="fixture", route="direct-build", reason="Fixture")
        raw["rules"] = [{"path": self.spec, "sha256": "0" * 64, "quote": "Fixture"}]
        self.target.write_text(json.dumps(raw))
        rc, out = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["diagnostic"]["field"], "$.rules[0].path")
        self.assertEqual(out["diagnostic"]["issue"], "not_installed_native_rule")

    def test_linked_skill_sources_cannot_be_used_as_rule_evidence(self):
        self.draft()
        linked = (self.repo / self.skill).parent / "step-02-plan.md"
        linked.unlink()  # Replace the installed native fixture file with the alias under test.
        try:
            linked.symlink_to(self.repo / self.spec)
        except OSError:
            self.skipTest("symlinks unavailable")
        rc, out = self.run_cli("sdd", "route", "rule", "--source", linked,
                               "--start-line", "1", "--end-line", "1")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["diagnostic"]["issue"], "not_installed_native_rule")

    def test_malformed_spec_reports_exact_repair_then_cli_creates_input_parents(self):
        spec = self.repo / self.spec
        valid = spec.read_text()
        spec.write_text("# 登录系统\n**规格状态**: ready-for-dev\n**路由**: dispatch\n")
        rc, bound = self.run_cli("sdd", "artifact", "bind", "--artifact", self.spec)
        self.assertEqual(rc, 0, bound)
        self.assertEqual(bound["preparation"]["diagnostic"]["issue"], "frontmatter_missing")
        before = self.read_work(self.wid)
        for command in (("sdd", "route", "prepare"),
                        ("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "3")):
            rc, out = self.run_cli(*command)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "NATIVE_SPEC_PREPARATION_REQUIRED")
            self.assertEqual(out["diagnostic"]["field"], "frontmatter")
            self.assertEqual(out["diagnostic"]["path"], self.spec)
            self.assertIn("body labels do not count", out["next"])
            self.assertNotIn("restart the host BEFORE", out["next"])
            self.assertNotIn("--index <rule-index>", out["next"])
        self.assertFalse(self.target.parent.exists())
        self.assertEqual(self.read_work(self.wid), before)
        mkdir = self.hook(tool_name="bash", tool_input={"command": "mkdir -p .orchestrator/inputs"})
        self.assertEqual(mkdir["decision"], "deny")
        self.assertIn("cao sdd route prepare", mkdir["reason"])
        for key in ("requirement", "path_assessment", "sdd", "execution"):
            self.assertEqual(self.read_work(self.wid)[key], before[key])
        spec.write_text(valid)
        self.run_cli("sdd", "artifact", "bind", "--artifact", self.spec)
        self.draft()
        self.assertTrue(self.target.parent.is_dir())
        self.assertEqual(self.hook(str(self.target))["decision"], "allow")
        self.assertEqual(self.hook(".orchestrator/config.json")["decision"], "deny")
        self.fill()
        self.run_cli("sdd", "route", "rule", "--source", self.source,
                     "--start-line", "3", "--end-line", "3")
        self.assertEqual(self.run_cli("sdd", "route", "check")[0], 0)

    def test_invalid_frontmatter_route_has_a_targeted_diagnostic(self):
        for route in ("", "route: full-planning\n", "route: dispatch\nroute: oneshot\n"):
            (self.repo / self.spec).write_text("---\nstatus: draft\n" + route + "---\n# Intent\n")
            rc, out = self.run_cli("sdd", "route", "prepare")
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["diagnostic"]["field"], "frontmatter.route")
            self.assertEqual(out["diagnostic"]["issue"], "build_route_invalid")

    def test_literal_echo_is_readonly_but_unclosed_quote_is_not_proven_safe(self):
        from scripts.cao_tool_policy import tool_effect
        text = "# Login Rules\n\n- Passwords hashed with bcrypt\n- JWT tokens"
        command = "echo '" + text + "'"
        event = {"tool_name": "bash", "tool_input": {"command": command}}
        self.assertEqual(tool_effect(event, self.repo)["effect"], "read")
        self.assertEqual(self.hook(**event)["decision"], "allow")
        event["tool_input"]["command"] = command[:-1]
        self.assertIn("SHELL_PARSE_UNKNOWN", tool_effect(event, self.repo)["diagnostics"])
        self.assertEqual(self.hook(**event)["decision"], "deny")

    def test_null_omission_replay_has_stable_targeted_diagnostics_without_work_changes(self):
        self.draft()
        raw = self.fill()
        raw["omitted"] = [{"name": "sprint-status", "reason": "Native fixture", "replaced_by": None},
                          {"name": "architecture", "reason": "Native fixture", "replaced_by": None}]
        self.target.write_text(json.dumps(raw))
        self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "3")
        before = self.read_work(self.wid)
        repair_ids = []
        for command in ("check", "propose", "check"):
            rc, out = self.run_cli("sdd", "route", command, "--declaration", self.target)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "NATIVE_ROUTE_REPLACEMENT_REQUIRED")
            self.assertEqual([d["field"] for d in out["diagnostics"]],
                             ["$.omitted[0].replaced_by", "$.omitted[1].replaced_by"])
            self.assertEqual(out["diagnostic"]["actual"], None)
            self.assertEqual(out["diagnostic"]["allowed"], [self.spec])
            self.assertNotIn("restart the host BEFORE", out["next"])
            self.assertIn("$.omitted[0].replaced_by", out["next"])
            self.assertEqual(out["retry_policy"]["unchanged_input"], "repair_instead_of_retry")
            repair_ids.append(out["repair_id"])
        self.assertEqual(len(set(repair_ids)), 1)
        self.assertEqual(self.read_work(self.wid), before)
        self.fill()
        self.assertEqual(self.run_cli("sdd", "route", "check")[0], 0)

    def test_structural_route_errors_name_the_field(self):
        self.draft()
        raw = self.fill()
        self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "3", "--end-line", "3")
        raw = json.loads(self.target.read_text())
        for key, value, field in (("provider", "openspec", "$.provider"),
                                  ("profile", "light", "$.profile"),
                                  ("workflow", "invented", "$.workflow"),
                                  ("route", "oneshot", "$.route"),
                                  ("required_artifacts", [], "$.required_artifacts"),
                                  ("omitted", [], "$.omitted"),
                                  ("provider_version", "", "$.provider_version")):
            changed = copy.deepcopy(raw)
            changed[key] = value
            self.target.write_text(json.dumps(changed))
            rc, out = self.run_cli("sdd", "route", "check")
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["diagnostic"]["field"], field)
            self.assertNotIn("restart the host BEFORE", out["next"])

    def test_invalid_json_is_repairable_and_changed_input_has_a_new_repair_id(self):
        self.draft()
        self.target.write_text("{invalid")
        rc, first = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 2, first)
        self.assertEqual(first["diagnostic"]["field"], "$")
        self.assertEqual(first["diagnostic"]["line"], 1)
        self.assertEqual(first["diagnostic"]["column"], 2)
        self.assertNotIn("restart the host BEFORE", first["next"])
        self.target.write_text("{different")
        rc, second = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 2, second)
        self.assertNotEqual(first["repair_id"], second["repair_id"])

    def test_light_context_does_not_require_workflow_registration_to_shorten_guidance(self):
        work = self.read_work(self.wid)
        work["path_assessment"]["profile"] = "light"
        work["sdd"].pop("workflow", None)
        out = cao.route_preparation_action(self.repo, work, {"reason": "NATIVE_ROUTE_REQUIRED"})
        self.assertIn("Inherit the accepted project background", out["instruction"])
        self.assertNotIn("restart the host BEFORE", out["instruction"])

    def test_not_applicable_requires_real_oneshot_route_evidence(self):
        spec = self.repo / self.spec
        spec.write_text(spec.read_text().replace("route: dispatch", "route: oneshot"))
        source = self.repo / self.source
        source.write_text("If no intent gaps, write only Intent and Implementation Notes. Set `route: 'oneshot'`.\n")
        self.draft()
        raw = self.fill()
        raw["omitted"] = [{"name": "sprint-status", "reason": "Native oneshot does not use sprint tracking",
                           "disposition": "not_applicable", "native_rule": self.source}]
        self.target.write_text(json.dumps(raw))
        self.run_cli("sdd", "route", "rule", "--source", self.source, "--start-line", "1", "--end-line", "1")
        self.assertEqual(self.run_cli("sdd", "route", "check")[0], 0)
        out = self.run_cli("sdd", "route", "propose", "--declaration", self.target)[1]
        self.assertTrue(out["ok"], out)
        self.assertNotIn("replaced_by", out["route"]["omitted"][0])
        raw = json.loads(self.target.read_text())
        raw["omitted"][0]["native_rule"] = ".agents/skills/bmad-build/references/claims-check.md"
        self.target.write_text(json.dumps(raw))
        rc, out = self.run_cli("sdd", "route", "check")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_ROUTE_NOT_APPLICABLE_EVIDENCE_REQUIRED")
        self.assertEqual(out["diagnostic"]["field"], "$.omitted[0].native_rule")
        from scripts.route_omissions import normalize_omissions
        for provider, route, build in (("bmad", "direct-build", "dispatch"), ("bmad", "full-planning", "oneshot"),
                                       ("openspec", "direct-build", "oneshot")):
            with self.assertRaisesRegex(ValueError, "NATIVE_ROUTE_NOT_APPLICABLE_EVIDENCE_REQUIRED"):
                normalize_omissions([{"name": "sprint", "reason": "Fixture", "disposition": "not_applicable", "native_rule": self.source}],
                                    [], [{"path": self.source, "quote": "route: oneshot"}], provider, route, build)

    def test_json_formatter_is_readonly_but_output_files_and_interpreter_code_are_not(self):
        for command in ("cat .orchestrator/inputs/x/route.json | python3 -m json.tool",
                        "python3 -m json.tool --sort-keys route.json"):
            self.assertEqual(self.hook(tool_name="bash", tool_input={"command": command})["decision"], "allow", command)
        for command in ("python3 -m json.tool route.json product.py", "python3 -c 'open(\"product.py\",\"w\").write(\"x\")'",
                        "python3 -m json.tool route.json > product.py"):
            self.assertEqual(self.hook(tool_name="bash", tool_input={"command": command})["decision"], "deny", command)

    def test_dependency_setup_stays_in_project_environment(self):
        folder = self.repo / ".venv"
        executable = folder / "bin/python"
        executable.parent.mkdir(parents=True)
        (folder / "pyvenv.cfg").write_text("home = /fixture\n")
        executable.write_text("fixture interpreter; command is not executed by this test\n")
        for command in ("python3 -m venv .venv", ".venv/bin/python -m pip install pytest",
                        "uv pip install --python .venv/bin/python pytest"):
            self.assertEqual(self.hook(tool_name="bash", tool_input={"command": command})["decision"], "allow", command)
        for command in ("python3 -m pip install pytest --break-system-packages", "pip install fastapi",
                        "npm install -g test", ".venv/bin/python -m pip install --target /tmp/outside pytest",
                        "uv pip install --python .venv/bin/python --python /usr/bin/python pytest"):
            out = self.hook(tool_name="bash", tool_input={"command": command})
            self.assertEqual(out["decision"], "deny", command)
            self.assertIn("VERIFICATION_DEPENDENCY_SCOPE_REQUIRED", out["reason"])
        for command in ("python-evil -m venv .venv", "python3 -m venv /tmp/outside",
                        "uv pip install --python .venv/bin/python -p /usr/bin/python pytest"):
            self.assertEqual(self.hook(tool_name="bash", tool_input={"command": command})["decision"], "deny", command)

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
