"""Behavioral regressions from the 6fec51a runtime/governance review."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import enforcement_kernel as kernel
import fact_resolver
import install_host_adapter as installer
import intake_pipeline
import policy_engine
import project_bootstrap
import semantic_intake_pipeline
import skill_runtime
import tool_actions
from test_enforcement_kernel import EnforcementFixture


class GovernanceTargetsTests(unittest.TestCase):
    def test_shell_aliases_and_parent_operations_cannot_disable_enforcement(self):
        fx = EnforcementFixture()
        self.addCleanup(fx.close)
        ordinary = {"tool_name": "Write", "tool_input": {"file_path": "src/main/java/com/acme/App.java"}}
        self.assertEqual("allow", kernel.handle(fx.repo, "pi", "pre_tool", ordinary)["decision"])
        commands = [
            "printf 'enabled: false\\n' > .orchestrator/./enforcement.yaml",
            "printf x > .orchestrator/runtime/../enforcement.yaml",
            "rm -rf .orchestrator", "mv .orchestrator previous-state",
            "rm -rf .orchestrator/*", "rm -rf .",
            "cd .orchestrator && printf x > enforcement.yaml",
            "python3 -c \"open('.orchestrator/./enforcement.yaml','w').write('enabled: false')\"",
            r"powershell -Command Set-Content .orchestrator\enforcement.yaml value",
        ]
        before = (fx.repo / ".orchestrator/enforcement.yaml").read_bytes()
        for command in commands:
            with self.subTest(command=command):
                result = kernel.handle(fx.repo, "pi", "pre_tool", {"tool_name": "Bash", "tool_input": {"command": command}})
                self.assertEqual("deny", result["decision"], result)
                self.assertEqual(["GOVERNANCE_CONFIG_PROTECTED"], result["metadata"]["authorization"]["reason_codes"])
        self.assertEqual(before, (fx.repo / ".orchestrator/enforcement.yaml").read_bytes())

    def test_shell_paths_use_the_tool_working_directory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            raw = {"tool_name": "exec_command", "tool_input": {
                "workdir": str(repo / ".orchestrator"), "cmd": "printf x > ./enforcement.yaml"}}
            result = kernel.handle(repo, "codex", "pre_tool", raw)
            self.assertEqual(["GOVERNANCE_CONFIG_PROTECTED"], result["metadata"]["authorization"]["reason_codes"])
            result = kernel.handle(repo, "pi", "pre_tool", {"tool_name": "Write", "cwd": str(repo / ".orchestrator"),
                                                            "tool_input": {"path": "enforcement.yaml"}})
            self.assertEqual(["GOVERNANCE_CONFIG_PROTECTED"], result["metadata"]["authorization"]["reason_codes"])

    def test_read_only_and_recovery_commands_still_work(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for command in ("cat .orchestrator/./config.yaml", "git diff -- .orchestrator/policies",
                            f'python3 "{ROOT / "coding-orchestrator"}" --repo "{repo}" start'):
                result = kernel.handle(repo, "pi", "pre_tool", {"tool_name": "Bash", "tool_input": {"command": command}})
                self.assertEqual("allow", result["decision"], result)
            self.assertEqual("mutate_code", tool_actions.shell_action("rm .orchestrator/runtime/cache.json", repo))

    def test_external_policy_pack_is_protected_in_every_write_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            manifest = repo / ".orchestrator/policies/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            source = repo / "docs/policy.yaml"
            source.parent.mkdir()
            source.write_text("id: project\nrules:\n  - id: required\n    level: MUST\n", encoding="utf-8")
            manifest.write_text("packs:\n  - path: ../../docs/policy.yaml\n", encoding="utf-8")
            self.assertEqual("MUST", policy_engine.load_policy(repo, manifest)[1][0]["level"])
            payloads = [
                {"tool_name": "Write", "tool_input": {"file_path": "docs/policy.yaml"}},
                {"tool_name": "Edit", "tool_input": {"file_path": str(source)}},
                {"tool_name": "apply_patch", "tool_input": "*** Delete File: docs/policy.yaml\n"},
                {"tool_name": "apply_patch", "tool_input": "*** Update File: docs/spec.md\n*** Move to: docs/policy.yaml\n"},
                {"tool_name": "Bash", "tool_input": {"command": "printf x > docs/./policy.yaml"}},
                {"tool_name": "Bash", "tool_input": {"command": "rm -rf docs"}},
            ]
            for raw in payloads:
                with self.subTest(raw=raw):
                    result = kernel.handle(repo, "pi", "pre_tool", raw)
                    self.assertEqual(["GOVERNANCE_CONFIG_PROTECTED"], result["metadata"]["authorization"]["reason_codes"])
            self.assertEqual("prepare", tool_actions.describe(
                {"tool_name": "Write", "tool_input": {"path": "docs/spec.md"}}, repo)["action"])

    def test_configured_and_analyzed_manifests_contribute_policy_sources(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".orchestrator").mkdir()
            (repo / "docs").mkdir()
            (repo / ".orchestrator/config.yaml").write_text(
                "orchestrator:\n  engineering_policy:\n    manifest: docs/policy-manifest.yaml\n", encoding="utf-8")
            (repo / "docs/policy-manifest.yaml").write_text("packs:\n  - path: custom-policy.yaml\n", encoding="utf-8")
            for target in ("docs/policy-manifest.yaml", "docs/custom-policy.yaml"):
                self.assertIsNotNone(tool_actions.governance_class(repo, target))
            # The currently bound plan also protects a custom manifest and its old
            # source if that source has since been removed from the live manifest.
            (repo / ".orchestrator/execution-state.yaml").write_text(
                "analysis:\n  policy_plan_ref: .orchestrator/plan.json\n", encoding="utf-8")
            (repo / ".orchestrator/plan.json").write_text(json.dumps({
                "manifest": "docs/other-manifest.yaml", "policy_sources": [{"ref": "old-policy.yaml"}]}), encoding="utf-8")
            self.assertIsNotNone(tool_actions.governance_class(repo, "docs/other-manifest.yaml"))
            self.assertIsNotNone(tool_actions.governance_class(repo, "docs/old-policy.yaml"))

    def test_symlink_does_not_remove_governance_identity(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".orchestrator").mkdir()
            (repo / "docs").mkdir()
            target = repo / "docs/settings.yaml"
            target.write_text("enabled: true\n", encoding="utf-8")
            (repo / ".orchestrator/enforcement.yaml").symlink_to(target)
            (repo / "docs/nested").mkdir()
            (repo / "shortcut").symlink_to(repo / "docs/nested", target_is_directory=True)
            for path in ("docs/settings.yaml", ".orchestrator/enforcement.yaml", "shortcut/../settings.yaml"):
                self.assertIsNotNone(tool_actions.governance_class(repo, path))


class WindowsRecoveryTests(unittest.TestCase):
    def test_packaged_cmd_can_run_recovery_before_state_exists(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            command = f'"{skill_runtime.windows_cli_path()}" --repo "{repo}" intake "First requirement"'
            result = kernel.handle(repo, "codex", "pre_tool", {"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual("allow", result["decision"])
            self.assertEqual("prepare", result["metadata"]["authorization"]["action"])
            for bad in (f'"{repo / "coding-orchestrator.cmd"}" start', command + " && python3 generate.py"):
                result = kernel.handle(repo, "codex", "pre_tool", {"tool_name": "Bash", "tool_input": {"command": bad}})
                self.assertEqual("deny", result["decision"])

    def test_windows_tokenization_preserves_native_quoted_paths(self):
        command = r'"C:\Users\Jane Doe\skills\coding-orchestrator.cmd" --repo "D:\project files" start'
        self.assertEqual([r"C:\Users\Jane Doe\skills\coding-orchestrator.cmd", "--repo", r"D:\project files", "start"],
                         tool_actions.shell_words(command, windows=True))


class HostUpgradeTests(unittest.TestCase):
    def test_auto_migration_replaces_old_hooks_preserves_user_hooks_and_is_idempotent(self):
        for host, relative in (("claude-code", ".claude/settings.json"), ("codex", ".codex/hooks.json")):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                desired = installer.load_template(ROOT / "hosts" / host / "hooks.template.json", repo)
                old = copy.deepcopy(desired)
                legacy = 'python3 "/old installation/scripts/enforcement_kernel.py" host --host ' + host
                for groups in old["hooks"].values():
                    for group in groups:
                        for hook in group["hooks"]:
                            hook["command"] = legacy
                user_hook = {"type": "command", "command": "echo user-hook", "timeout": 7}
                old["hooks"]["PreToolUse"][0]["hooks"].append(user_hook)
                old["userSetting"] = {"preserve": True}
                target = repo / relative
                target.parent.mkdir(parents=True)
                target.write_text(json.dumps(old), encoding="utf-8")
                self.assertFalse(installer.is_installed(repo, host))
                result = project_bootstrap.ensure_selected_host(repo, host, activation=False)
                self.assertTrue(result["performed"])
                migrated = json.loads(target.read_text())
                hooks = [hook for group in migrated["hooks"]["PreToolUse"] for hook in group["hooks"]]
                self.assertIn(user_hook, hooks)
                self.assertEqual(2, len(hooks))
                self.assertEqual(old["userSetting"], migrated["userSetting"])
                self.assertNotIn(legacy, target.read_text())
                self.assertTrue(installer.is_installed(repo, host))
                before = target.read_bytes()
                self.assertFalse(project_bootstrap.ensure_selected_host(repo, host, activation=False)["performed"])
                installer.merge_hooks(target, desired, True)
                self.assertEqual(before, target.read_bytes())

    def test_pi_migration_replaces_stale_runtime_and_repo(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            target = repo / ".pi/extensions/coding-orchestrator.ts"
            target.parent.mkdir(parents=True)
            target.write_text('const KERNEL = "/old/enforcement_kernel.py"; // "--host", "pi"\n', encoding="utf-8")
            self.assertFalse(installer.is_installed(repo, "pi"))
            self.assertTrue(project_bootstrap.ensure_selected_host(repo, "pi", activation=False)["performed"])
            self.assertTrue(installer.is_installed(repo, "pi"))
            self.assertFalse(project_bootstrap.ensure_selected_host(repo, "pi", activation=False)["performed"])

    def test_path_literals_survive_json_shell_and_ts_serialization(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / 'project "quoted" $UNKNOWN `literal` %PATH% 汉字'
            repo.mkdir()
            fake_kernel = base / 'runtime "quoted" $UNKNOWN.py'
            fake_kernel.write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n', encoding="utf-8")
            for windows in (False, True):
                with self.subTest(windows=windows), mock.patch.object(installer, "KERNEL", fake_kernel):
                    fragment = installer.load_template(ROOT / "hosts/claude-code/hooks.template.json", repo)
                    command = fragment["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
                    if windows:
                        command = installer._shell_command(shlex.split(command), windows=True)
                    completed = subprocess.run(command, shell=True, cwd=base, capture_output=True, text=True, check=True)
                    self.assertEqual(["host", "--host", "claude-code", "--repo", str(repo)], json.loads(completed.stdout))
            with mock.patch.object(installer, "KERNEL", fake_kernel):
                text = installer.render_pi(repo)
            for name, expected in (("KERNEL", str(fake_kernel)), ("REPO", str(repo))):
                literal = re.search(rf"const {name} = (.*);", text).group(1)
                self.assertEqual(expected, json.loads(literal))

    def test_explicit_repo_is_not_replaced_by_parent_git_root(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            subprocess.run(["git", "init", "-q", str(parent)], check=True)
            repo = parent / "nested-project"
            repo.mkdir()
            subprocess.run([sys.executable, str(ROOT / "scripts/enforcement_kernel.py"), "host", "--host", "pi",
                            "--repo", str(repo), "--event", "pre_tool"], cwd=parent, input=json.dumps({
                                "tool_name": "Read", "cwd": td}), capture_output=True, text=True, check=True)
            self.assertTrue((repo / ".orchestrator/runtime/enforcement-state.json").is_file())
            self.assertFalse((parent / ".orchestrator").exists())


class ScaffoldPreservationTests(unittest.TestCase):
    def test_both_pipelines_preserve_filled_input_during_repeated_intake(self):
        for pipeline in (intake_pipeline, semantic_intake_pipeline):
            with self.subTest(pipeline=pipeline.__name__), tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                outdir = repo / ".orchestrator/intake"
                argv = ["--repo", td, "--request", "Add user lookup", "--output-dir", str(outdir)]
                if pipeline is semantic_intake_pipeline:
                    argv += ["--skip-context", "--skip-policy", "--cbm-fixture", str(ROOT / "examples/cbm-detect-changes-fixture.json")]
                def run(extra=()):
                    stream = io.StringIO()
                    with contextlib.redirect_stdout(stream):
                        pipeline.main([*argv, *extra])
                    return json.loads(stream.getvalue())
                first = run()
                source = Path(first["artifacts"]["resolutions_template"])
                filled = {"resolutions": [{"path": "risk.authn_authz", "value": True,
                          "source_type": "user_requirement", "source": "request", "evidence": "JWT", "strength": "authoritative"}]}
                source.write_text(json.dumps(filled), encoding="utf-8")
                before = source.read_bytes()
                for _ in range(2):
                    result = run(["--resolutions", str(source)])
                    self.assertEqual(before, source.read_bytes())
                    remaining = Path(result["artifacts"]["resolutions_template"])
                    self.assertNotEqual(source, remaining)
                    self.assertNotIn("risk.authn_authz", [x["path"] for x in json.loads(remaining.read_text())["resolutions"]])

    def test_template_writer_does_not_follow_existing_symlinks_or_hardlinks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "filled.json"
            source.write_text('{"resolutions": []}', encoding="utf-8")
            for kind in ("symlink", "hardlink"):
                outdir = root / kind
                outdir.mkdir()
                target = outdir / "fact-resolutions.template.json"
                if kind == "symlink":
                    target.symlink_to(source)
                else:
                    os.link(source, target)
                result = fact_resolver.write_resolution_template(outdir, {"extraction": {"resolution_queue": []}})
                self.assertNotEqual(target, result)
                self.assertEqual('{"resolutions": []}', source.read_text())


if __name__ == "__main__":
    unittest.main()
