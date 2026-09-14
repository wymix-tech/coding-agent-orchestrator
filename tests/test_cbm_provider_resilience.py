import contextlib
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cbm_provider
import provider_incident
import semantic_intake_pipeline
import start_router


class CbmProviderResilienceTests(unittest.TestCase):
    def _proc(self, rc, stdout="", stderr=""):
        import subprocess
        return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)

    def test_modern_schema_cli_requests_explicit_json_output(self):
        p = cbm_provider.CBMProvider("fake-cbm")
        p._binary_path = mock.Mock(return_value="/fake/cbm")
        p._tool_protocol = mock.Mock(return_value={"supports_format": True})
        p._run = mock.Mock(return_value=self._proc(0, '{"status":"indexed","project":"demo"}'))
        out = p._run_tool("index_repository", {"repo_path": "/tmp/demo"})
        self.assertEqual("indexed", out["status"])
        self.assertEqual(
            ["/fake/cbm", "cli", "index_repository", "--repo-path", "/tmp/demo", "--format", "json"],
            p._run.call_args.args[0],
        )
        self.assertIsNone(p._run.call_args.kwargs.get("input_text"))

    def test_collect_maps_orchestrator_scope_to_real_cbm_schema_and_checks_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess = __import__("subprocess")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
            (repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
            provider = cbm_provider.CBMProvider("fake-cbm")
            provider.health = mock.Mock(return_value={"available": True})
            provider._version = mock.Mock(return_value="0.10.8")
            provider._run_tool = mock.Mock(side_effect=[
                {"status": "indexed", "project": repo.name},
                {"changed_files": ["app.py"], "changed_symbols": [], "impacted_symbols": []},
                {"status": "complete"},
            ])
            out = provider.collect_impact(repo, scope="all", base_branch="HEAD", depth=3)
            detect_args = provider._run_tool.call_args_list[1].args[1]
            self.assertEqual("impact", detect_args["scope"])
            self.assertNotEqual("all", detect_args["scope"])
            coverage_args = provider._run_tool.call_args_list[2].args[1]
            self.assertEqual(["app.py"], coverage_args["paths"])
            self.assertTrue(out["collection"]["detect_changes_invoked"])

    def test_empty_bmad_only_project_does_not_invoke_cbm_or_open_incident(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "_bmad/core").mkdir(parents=True)
            (repo / "_bmad/core/workflow.yaml").write_text("name: bmad\n", encoding="utf-8")
            with mock.patch.object(cbm_provider.CBMProvider, "health", side_effect=AssertionError("CBM must not be probed")), \
                 mock.patch.object(cbm_provider.CBMProvider, "collect_impact", side_effect=AssertionError("CBM must not run")):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = semantic_intake_pipeline.main(["--repo", str(repo), "--request", "Build the first feature"])
            result = json.loads(buf.getvalue())
            self.assertNotEqual("PROVIDER_UNAVAILABLE", result["status"])
            impact_path = repo / ".orchestrator/intake/semantic-impact.json"
            impact = json.loads(impact_path.read_text(encoding="utf-8"))
            self.assertFalse(impact["collection"]["cbm_invoked"])
            self.assertEqual("empty_greenfield_project", impact["collection"]["reason"])
            self.assertFalse(provider_incident.inspect(repo).get("status") == "open")

    def test_common_local_bin_is_found_when_agent_path_is_minimal(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            binary = home / ".local" / "bin" / "codebase-memory-mcp"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False), \
                 mock.patch.object(cbm_provider.Path, "home", return_value=home), \
                 mock.patch.object(cbm_provider.shutil, "which", return_value=None):
                p = cbm_provider.CBMProvider()
                self.assertEqual(str(binary.resolve()), p._binary_path())

    def test_open_incident_blocks_automatic_repeat_until_reset(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / ".orchestrator").mkdir()
            (repo / "app.py").write_text("print('product code')\n", encoding="utf-8")
            health = {"available": True, "binary": "/fake/cbm", "version": "0.10.8"}
            diagnostics = {"provider": "codebase-memory-mcp", "binary": "/fake/cbm", "version": "0.10.8"}
            with mock.patch.object(cbm_provider.CBMProvider, "health", return_value=health), \
                 mock.patch.object(cbm_provider.CBMProvider, "diagnostics", return_value=diagnostics), \
                 mock.patch.object(cbm_provider.CBMProvider, "collect_impact", side_effect=cbm_provider.ProviderError("backend unavailable")) as collect:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc1 = semantic_intake_pipeline.main(["--repo", str(repo), "--request", "Build feature A"])
                self.assertEqual(2, rc1)
                self.assertEqual(1, collect.call_count)
                first = json.loads(buf.getvalue())
                self.assertEqual("PROVIDER_UNAVAILABLE", first["status"])
                self.assertEqual("repair_cbm_provider", first["next_action"])

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc2 = semantic_intake_pipeline.main(["--repo", str(repo), "--request", "Build feature A"])
                self.assertEqual(3, rc2)
                self.assertEqual(1, collect.call_count, "second automatic intake must not call CBM again")
                second = json.loads(buf.getvalue())
                self.assertEqual("PROVIDER_BLOCKED", second["status"])
                self.assertEqual("explicit_only", second["retry"])

                provider_incident.reset(repo)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc3 = semantic_intake_pipeline.main(["--repo", str(repo), "--request", "Build feature A"])
                self.assertEqual(2, rc3)
                self.assertEqual(2, collect.call_count, "reset permits exactly one fresh attempt")

    def test_binary_version_change_allows_fresh_attempt_without_manual_reset(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            provider_incident.record_failure(
                repo, error_class="OPERATION_FAILED", error="old failure",
                binary="/fake/cbm", version="0.10.7",
            )
            old = provider_incident.inspect(repo)
            self.assertTrue(provider_incident.is_open(repo, provider_identity=old["provider_identity"]))
            new_identity = provider_incident.identity("/fake/cbm", "0.10.8")
            self.assertFalse(provider_incident.is_open(repo, provider_identity=new_identity))

    def test_start_surfaces_provider_blocker_instead_of_semantic_retry(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            # A minimal initialized project + mocked active state route is sufficient to pin the
            # provider circuit semantics without re-testing the state manager here.
            (repo / ".orchestrator").mkdir()
            provider_incident.record_failure(
                repo, error_class="OUTPUT_PROTOCOL_ERROR", error="compact output was not JSON",
                binary="/fake/cbm", version="0.10.8",
            )
            state = {
                "work_item": {"id": "W1", "title": "Feature", "requirement_id": "r1", "requirement_revision": "v1"},
            }
            (repo / ".orchestrator" / "execution-state.yaml").write_text("placeholder", encoding="utf-8")
            with mock.patch.object(start_router.bootstrap_guard, "ensure", return_value={"status": "BOOTSTRAPPED"}), \
                 mock.patch.object(start_router.cbm_provider.CBMProvider, "health", return_value={"binary":"/fake/cbm","version":"0.10.8","available":True}), \
                 mock.patch.object(start_router.sm, "_load", return_value=state), \
                 mock.patch.object(start_router.sm, "completion_status", return_value={"done": False}), \
                 mock.patch.object(start_router.sm, "resume_summary", return_value={"work_item": state["work_item"], "next_action": "run_semantic_intake"}):
                out = start_router.resolve(repo)
            self.assertEqual("SURFACE_PROVIDER_BLOCKER", out["route"])
            self.assertEqual("repair_cbm_provider", out["next_action"])


if __name__ == "__main__":
    unittest.main()
