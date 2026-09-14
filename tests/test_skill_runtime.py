"""The Skill must locate its own runtime without a hardcoded install directory name."""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


skill_runtime = load_module("skill_runtime", ROOT / "scripts" / "skill_runtime.py")
cli = load_module("coding_orchestrator_runtime", ROOT / "scripts" / "coding_orchestrator.py")
activation = load_module("project_activation_runtime", ROOT / "scripts" / "project_activation.py")
snapshot = load_module("repository_snapshot_runtime", ROOT / "scripts" / "repository_snapshot.py")


def install_skill(base: pathlib.Path, dir_name: str, parents: bool = False) -> pathlib.Path:
    """Copy the real Skill package under an arbitrary directory name.

    `parents=False` installs project-level at `<base>/.agents/skills/<dir_name>`;
    `parents=True` treats `base` as the skills directory itself, for user-level installs
    such as `~/.codebuddy/skills/<dir_name>`.
    """
    dest = (base / dir_name) if parents else (base / ".agents" / "skills" / dir_name)
    dest.mkdir(parents=True)
    for item in ("scripts", "references", "policies", "examples", "hosts", "SKILL.md",
                 "MANIFEST.json", "coding-orchestrator", "coding-orchestrator.cmd"):
        src = ROOT / item
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dest / item)
    (dest / "coding-orchestrator").chmod(0o755)
    return dest


def git_init(repo: pathlib.Path) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    (repo / "README.md").write_text("# project\n", encoding="utf-8")  # a repo needs something to commit
    for args in (["git", "init", "-q", "."],
                 ["git", "add", "-A"],
                 ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
        proc = subprocess.run(args, cwd=repo, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env)
        assert proc.returncode == 0, f"{args} failed rc={proc.returncode}\nOUT:{proc.stdout}\nERR:{proc.stderr}"


class SkillRuntimeUnitTests(unittest.TestCase):
    def test_runtime_resolves_from_its_own_location(self):
        self.assertEqual(ROOT, skill_runtime.skill_root())
        self.assertEqual(ROOT / "SKILL.md", skill_runtime.skill_file())
        self.assertEqual(ROOT / "coding-orchestrator", skill_runtime.cli_path())
        self.assertTrue(skill_runtime.python_entry().is_file())

    def test_ref_is_repo_relative_inside_repo_and_absolute_outside(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            inside = repo / "scripts" / "x.py"
            inside.parent.mkdir(parents=True)
            inside.write_text("", encoding="utf-8")
            self.assertEqual("scripts/x.py", skill_runtime.ref(repo, inside))
            self.assertEqual(str(skill_runtime.skill_file()), skill_runtime.ref(repo, skill_runtime.skill_file()))

    def test_command_is_runnable_from_repository_root(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            command = skill_runtime.command(repo, "start")
            self.assertIn("--repo . start", command)
            self.assertTrue(command.endswith("--repo . start"))


class WhereCommandTests(unittest.TestCase):
    def invoke(self, skill_dir, repo, *args, json_mode=False):
        payload = ["--json"] if json_mode else []
        proc = subprocess.run(
            [str(skill_dir / "coding-orchestrator"), *payload, "--repo", str(repo), *args],
            cwd=repo, capture_output=True, text=True, check=False, env=ENV,
        )
        return proc

    def test_where_reports_actual_install_directory(self):
        for dir_name in ("orchestrating-sdd-coding", "coding-agent-orchestrator", "some-other-name"):
            with self.subTest(dir_name=dir_name):
                with tempfile.TemporaryDirectory() as td:
                    repo = pathlib.Path(td)
                    skill = install_skill(repo, dir_name)
                    proc = self.invoke(skill, repo, "where", json_mode=True)
                    self.assertEqual(0, proc.returncode, proc.stderr)
                    result = json.loads(proc.stdout)
                    self.assertEqual(dir_name, result["skill_dir_name"])
                    self.assertEqual(f".agents/skills/{dir_name}/SKILL.md", result["skill_ref"])
                    self.assertEqual(f".agents/skills/{dir_name}/coding-orchestrator --repo . start",
                                     result["commands"]["start"])

    def test_reported_command_actually_runs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            skill = install_skill(repo, "orchestrating-sdd-coding")
            proc = self.invoke(skill, repo, "where", json_mode=True)
            command = json.loads(proc.stdout)["commands"]["discover"]
            prefix = command.split(" --repo ")[0]
            ran = subprocess.run([str(repo / prefix), "--repo", ".", "discover"],
                                 cwd=repo, capture_output=True, text=True, check=False)
            self.assertEqual(0, ran.returncode, ran.stderr)
            self.assertIn("Discovery:", ran.stdout)

    def test_user_level_install_reports_absolute_paths(self):
        """Outside the repo there is no relative path, so report absolute and say so."""
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "home"
            repo = pathlib.Path(td) / "project"
            repo.mkdir()
            skill = install_skill(home / ".codebuddy" / "skills", "orchestrating-sdd-coding", parents=True)
            runtime = load_module("rt_user_level", skill / "scripts" / "skill_runtime.py")
            self.assertFalse(runtime.is_in_repo(repo))
            proc = subprocess.run([str(skill / "coding-orchestrator"), "--json", "--repo", str(repo), "where"],
                                  cwd=repo, capture_output=True, text=True, check=False, env=ENV)
            self.assertEqual(0, proc.returncode, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertFalse(result["in_repo"])
            self.assertTrue(pathlib.Path(result["cli_ref"]).is_absolute())
            self.assertTrue(pathlib.Path(result["skill_ref"]).is_absolute())
            text = subprocess.run([str(skill / "coding-orchestrator"), "--repo", str(repo), "where"],
                                  cwd=repo, capture_output=True, text=True, check=False, env=ENV).stdout
            self.assertIn("outside this repository", text)

    def test_command_with_spaces_in_path_is_paste_runnable(self):
        """User home directories contain spaces (Windows `C:\\Users\\Jane Doe\\...`)."""
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "home dir"
            repo = pathlib.Path(td) / "project"
            repo.mkdir()
            skill = install_skill(home / ".codebuddy" / "skills", "orchestrating-sdd-coding", parents=True)
            runtime = load_module("rt_spaces", skill / "scripts" / "skill_runtime.py")
            command = runtime.command(repo, "discover")
            self.assertIn("'", command, "path with spaces must be quoted")
            ran = subprocess.run(command, cwd=repo, shell=True, capture_output=True, text=True, check=False, env=ENV)
            self.assertEqual(0, ran.returncode, ran.stderr)
            self.assertIn("Discovery:", ran.stdout)

    def test_command_without_spaces_is_not_quoted(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            self.assertNotIn("'", skill_runtime.command(repo, "start"))

    def test_where_never_prints_the_wrong_directory_name(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            skill = install_skill(repo, "orchestrating-sdd-coding")
            proc = self.invoke(skill, repo, "where")
            self.assertNotIn("coding-agent-orchestrator/", proc.stdout)


class ActivationAndSnapshotTests(unittest.TestCase):
    def test_activation_block_uses_installed_directory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            skill = install_skill(repo, "orchestrating-sdd-coding")
            git_init(repo)
            proc = subprocess.run(
                [str(skill / "coding-orchestrator"), "--repo", ".", "init", "--host", "none"],
                cwd=repo, capture_output=True, text=True, check=False, env=ENV,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(".agents/skills/orchestrating-sdd-coding/SKILL.md", agents)
            self.assertIn(".agents/skills/orchestrating-sdd-coding/coding-orchestrator --repo . start", agents)

    def test_user_level_install_keeps_absolute_paths_out_of_agents_md(self):
        """AGENTS.md is committed; a user-level install must not leak `/home/<user>/...` into it."""
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "home"
            repo = pathlib.Path(td) / "project"
            repo.mkdir()
            git_init(repo)
            skill = install_skill(home / ".codebuddy" / "skills", "orchestrating-sdd-coding", parents=True)
            proc = subprocess.run(
                [str(skill / "coding-orchestrator"), "--repo", ".", "init", "--host", "none"],
                cwd=repo, capture_output=True, text=True, check=False, env=ENV,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertNotIn(str(home), agents, "absolute machine-specific path leaked into AGENTS.md")
            self.assertNotIn("orchestrating-sdd-coding", agents)
            self.assertIn("Coding Agent Orchestrator Skill", agents)
            self.assertIn("user level", agents)

    def test_project_level_install_still_records_a_relative_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            skill = install_skill(repo, "orchestrating-sdd-coding")
            git_init(repo)
            proc = subprocess.run(
                [str(skill / "coding-orchestrator"), "--repo", ".", "init", "--host", "none"],
                cwd=repo, capture_output=True, text=True, check=False, env=ENV,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(".agents/skills/orchestrating-sdd-coding/SKILL.md", agents)
            self.assertNotIn("user level", agents)

    def test_incomplete_install_explains_the_missing_asset(self):
        """An install without `hosts/` must say what is missing, not just dump a path."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            skill = install_skill(repo, "orchestrating-sdd-coding")
            shutil.rmtree(skill / "hosts")
            runtime = load_module("rt_no_hosts", skill / "scripts" / "install_host_adapter.py")
            with self.assertRaises(FileNotFoundError) as caught:
                runtime.load_template(skill / "hosts" / "claude-code" / "hooks.template.json", repo)
            message = str(caught.exception)
            self.assertIn("incomplete", message)
            self.assertIn("--host none", message)

    def test_hooks_carry_the_project_path_instead_of_guessing_from_cwd(self):
        """`init --repo <path> --host X` must bake <path> into the adapter so the kernel never guesses."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            skill = install_skill(root, "orchestrating-sdd-coding")
            repo = root / "my project"
            repo.mkdir()
            git_init(repo)
            proc = subprocess.run(
                [str(skill / "coding-orchestrator"), "--repo", str(repo), "init", "--host", "claude-code"],
                cwd=repo, capture_output=True, text=True, check=False, env=ENV,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
            commands = [
                h["command"]
                for group in settings["hooks"].values()
                for entry in group
                for h in entry["hooks"]
            ]
            self.assertTrue(commands)
            for command in commands:
                # No unresolved placeholder, and the project path is passed explicitly.
                self.assertNotIn("__REPO__", command)
                self.assertNotIn("__KERNEL__", command)
                self.assertIn('--repo "%s"' % repo.resolve(), command)

    def test_pi_extension_carries_the_project_path(self):
        """The Pi extension is generated per project, so it must know which project it belongs to."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            skill = install_skill(root, "orchestrating-sdd-coding")
            repo = root / "my project"
            repo.mkdir()
            git_init(repo)
            proc = subprocess.run(
                [str(skill / "coding-orchestrator"), "--repo", str(repo), "init", "--host", "pi"],
                cwd=repo, capture_output=True, text=True, check=False, env=ENV,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            extension = (repo / ".pi" / "extensions" / "coding-orchestrator.ts").read_text(encoding="utf-8")
            self.assertNotIn("__REPO__", extension)
            self.assertIn('"--host", "pi", "--repo", REPO', extension)
            self.assertIn('"%s"' % repo.resolve(), extension)

    def test_activation_fallback_scans_rather_than_assumes_a_name(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            target = repo / ".agents" / "skills" / "whatever" / "coding-orchestrator"
            target.parent.mkdir(parents=True)
            (target.parent / "scripts").mkdir()
            (target.parent / "scripts" / "coding_orchestrator.py").write_text("", encoding="utf-8")
            target.write_text("", encoding="utf-8")
            self.assertEqual(".agents/skills/whatever/coding-orchestrator", activation.cli_ref(repo))

    def test_snapshot_ignores_the_skill_wherever_it_is_installed(self):
        prefixes = snapshot.IGNORED_PREFIXES
        self.assertIn(".agents/skills/coding-agent-orchestrator/", prefixes)
        # In this checkout the package is not under .agents/skills, so no self prefix is derived.
        self.assertTrue(all(p.startswith(".agents/skills/") for p in prefixes))

    def test_installed_skill_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            skill = install_skill(repo, "orchestrating-sdd-coding")
            git_init(repo)
            loaded = load_module("snapshot_in_installed", skill / "scripts" / "repository_snapshot.py")
            self.assertIn(".agents/skills/orchestrating-sdd-coding/", loaded.IGNORED_PREFIXES)


if __name__ == "__main__":
    unittest.main()
