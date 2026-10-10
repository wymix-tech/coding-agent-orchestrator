"""Provider-neutral user-language policy tests."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo: pathlib.Path, *args: str) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(CAO), *args], cwd=repo,
        text=True, capture_output=True, timeout=20,
    )
    try:
        payload = json.loads(result.stdout)
    except Exception:
        payload = {"raw": result.stdout, "stderr": result.stderr}
    return result.returncode, payload


class UserLanguageTests(unittest.TestCase):
    def init_repo(self, language: str | None = None):
        temp = tempfile.TemporaryDirectory()
        repo = pathlib.Path(temp.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
        args = ["init"] + (["--language", language] if language else [])
        code, output = run(repo, *args)
        self.assertEqual(code, 0, output)
        return temp, repo

    def test_init_sets_project_language_and_show_reports_it(self):
        temp, repo = self.init_repo("en-US")
        with temp:
            code, payload = run(repo, "language", "show")
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload["language"]["user"], "en-US")
            self.assertEqual(payload["language"]["communication"], "follow-user")
            self.assertEqual(payload["language"]["sdd_artifacts"], "follow-user")

    def test_openspec_context_uses_user_language_and_is_idempotent(self):
        temp, repo = self.init_repo()
        with temp:
            openspec = repo / "openspec"
            openspec.mkdir()
            cfg = openspec / "config.yaml"
            cfg.write_text("schema: spec-driven\ncontext: |\n  Project uses Spring Boot.\nrules:\n  tasks:\n    - End tasks with a test\n", encoding="utf-8")
            code, payload = run(repo, "language", "set", "--language", "zh-CN")
            self.assertEqual(code, 0, payload)
            contents = cfg.read_text(encoding="utf-8")
            self.assertIn("Project uses Spring Boot.", contents)
            self.assertIn("所有 OpenSpec 原生产物", contents)
            self.assertIn("简体中文", contents)
            self.assertIn("End tasks with a test", contents)
            first = contents
            code, payload = run(repo, "language", "apply", "--provider", "openspec")
            self.assertEqual(code, 0, payload)
            self.assertEqual(cfg.read_text(encoding="utf-8"), first)

    def test_openspec_inline_context_is_preserved_when_converted_to_block(self):
        temp, repo = self.init_repo()
        with temp:
            (repo / "openspec").mkdir()
            cfg = repo / "openspec" / "config.yaml"
            cfg.write_text('schema: spec-driven\ncontext: "Current project context"\n', encoding="utf-8")
            code, payload = run(repo, "language", "set", "--language", "ja-JP")
            self.assertEqual(code, 0, payload)
            contents = cfg.read_text(encoding="utf-8")
            self.assertIn("Current project context", contents)
            self.assertIn("Japanese", contents)
            self.assertIn("context: |", contents)

    def test_bmad_modern_config_updates_language_keys_without_losing_other_settings(self):
        temp, repo = self.init_repo("zh-CN")
        with temp:
            bmad = repo / "_bmad"
            bmad.mkdir()
            cfg = bmad / "config.toml"
            cfg.write_text('[core]\nproject_name = "demo"\ncommunication_language = "English"\n\n[modules.bmm]\nuser_skill_level = "advanced"\n', encoding="utf-8")
            (bmad / "config.user.toml").write_text('[core]\nuser_name = "Dev"\n', encoding="utf-8")
            code, payload = run(repo, "language", "apply", "--provider", "bmad")
            self.assertEqual(code, 0, payload)
            base = cfg.read_text(encoding="utf-8")
            user = (bmad / "config.user.toml").read_text(encoding="utf-8")
            custom_user = (bmad / "custom" / "config.user.toml").read_text(encoding="utf-8")
            # Team/base config remains untouched; CAO writes only a high-priority user override.
            self.assertIn('communication_language = "English"', base)
            self.assertIn('project_name = "demo"', base)
            self.assertIn('user_skill_level = "advanced"', base)
            self.assertIn('user_name = "Dev"', user)
            self.assertIn('communication_language = "Simplified Chinese"', custom_user)
            self.assertIn('document_output_language = "Simplified Chinese"', custom_user)

    def test_bmad_legacy_yaml_config_uses_user_language(self):
        temp, repo = self.init_repo()
        with temp:
            bmad_config = repo / "_bmad" / "bmm" / "config.yaml"
            bmad_config.parent.mkdir(parents=True)
            bmad_config.write_text("project_name: demo\ncommunication_language: English\ndocument_output_language: English\n", encoding="utf-8")
            code, payload = run(repo, "language", "set", "--language", "ja-JP")
            self.assertEqual(code, 0, payload)
            text = bmad_config.read_text(encoding="utf-8")
            self.assertIn('communication_language: "Japanese"', text)
            self.assertIn('document_output_language: "Japanese"', text)

    def test_speckit_constitution_managed_section_preserves_existing_principles(self):
        temp, repo = self.init_repo()
        with temp:
            memory = repo / ".specify" / "memory"
            memory.mkdir(parents=True)
            constitution = memory / "constitution.md"
            constitution.write_text("# Project Constitution\n\n## Quality\nKeep code simple.\n", encoding="utf-8")
            code, payload = run(repo, "language", "set", "--language", "zh-CN")
            self.assertEqual(code, 0, payload)
            contents = constitution.read_text(encoding="utf-8")
            self.assertIn("Keep code simple.", contents)
            self.assertIn("Spec Kit 的所有规格", contents)
            self.assertIn("简体中文", contents)
            first = contents
            code, payload = run(repo, "language", "apply", "--provider", "speckit")
            self.assertEqual(code, 0, payload)
            self.assertEqual(constitution.read_text(encoding="utf-8"), first)

    def test_language_set_applies_to_all_installed_sdds(self):
        temp, repo = self.init_repo()
        with temp:
            (repo / "openspec").mkdir()
            (repo / "openspec" / "config.yaml").write_text("schema: spec-driven\n", encoding="utf-8")
            (repo / "_bmad").mkdir()
            (repo / ".specify").mkdir()
            code, payload = run(repo, "language", "set", "--language", "ja-JP")
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload["language"]["language"], "ja-JP")
            self.assertEqual({x["provider"] for x in payload["language"]["results"]}, {"bmad", "openspec", "speckit"})
            self.assertIn("Japanese", (repo / "openspec" / "config.yaml").read_text(encoding="utf-8"))
            self.assertIn("Japanese", (repo / "_bmad" / "custom" / "config.user.toml").read_text(encoding="utf-8"))
            self.assertIn("Japanese", (repo / ".specify" / "memory" / "constitution.md").read_text(encoding="utf-8"))

    def test_bootstrap_reapplies_preference_to_selected_sdd(self):
        temp, repo = self.init_repo("zh-CN")
        with temp:
            (repo / "openspec").mkdir()
            cfg = repo / "openspec" / "config.yaml"
            cfg.write_text("schema: spec-driven\ncontext: |\n  User prefers English.\n", encoding="utf-8")
            code, payload = run(repo, "bootstrap")
            self.assertEqual(code, 0, payload)
            self.assertIn("简体中文", cfg.read_text(encoding="utf-8"))
            self.assertIn("language_configuration", payload)

    def test_sdd_select_applies_configured_language_to_spec_kit(self):
        temp, repo = self.init_repo("zh-CN")
        with temp:
            (repo / ".specify" / "memory").mkdir(parents=True)
            constitution = repo / ".specify" / "memory" / "constitution.md"
            constitution.write_text("# Existing Constitution\n", encoding="utf-8")
            code, payload = run(repo, "sdd", "select", "--provider", "speckit")
            self.assertEqual(code, 0, payload)
            self.assertIn("简体中文", constitution.read_text(encoding="utf-8"))
            self.assertEqual(payload["language_configuration"]["provider"], "speckit")

    def test_bootstrap_persists_default_language_on_existing_config(self):
        temp, repo = self.init_repo()
        with temp:
            cfg_path = repo / ".orchestrator" / "config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.pop("language", None)
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            code, payload = run(repo, "bootstrap")
            self.assertEqual(code, 0, payload)
            persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["language"]["user"], "zh-CN")


if __name__ == "__main__":
    unittest.main()
