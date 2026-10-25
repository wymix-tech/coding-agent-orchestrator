"""Exercise real archives and the shell boundary used by the release workflow."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_builder", ROOT / "tools/release/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleasePackagingTests(unittest.TestCase):
    def test_archives_support_tags_and_preserve_runtime(self):
        for tag in ("v0.1.0", "release/v0.1.0", "v$(touch injected)", "版本/一", "../escape", "v" * 200):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as directory:
                archives = builder.build(Path(directory), tag)
                with tarfile.open(archives[0]) as archive:
                    tar_paths = {p.name for p in archive.getmembers() if p.isfile()}
                    launcher = next(p for p in archive.getmembers() if p.name.endswith("/cao"))
                    self.assertTrue(launcher.mode & 0o111)
                with zipfile.ZipFile(archives[1]) as archive:
                    self.assertEqual(tar_paths, set(archive.namelist()))
                relative = {p.split("/", 1)[1] for p in tar_paths}
                self.assertTrue({"SKILL.md", "README.md", "scripts/cao.py", "scripts/dashboard.html",
                                 "templates/pi-reviewer.md", "hosts/pi/coding-orchestrator.ts"} <= relative)
                self.assertFalse(any(p.startswith(("tools/", "tests/", "docs/")) for p in relative))
                self.assertNotIn("scripts/build_release.py", relative)
                self.assertNotIn("release-manifest.txt", relative)
                for path in archives:
                    self.assertEqual(Path(directory), path.parent)
                    self.assertTrue(path.is_file())

    def test_normalization_does_not_collide_with_plain_tag(self):
        self.assertEqual("v0.1.0", builder.archive_version("v0.1.0"))
        self.assertNotEqual(builder.archive_version("release/v1"), builder.archive_version("release-v1"))

    @unittest.skipIf(os.name == "nt", "GitHub release job uses bash on Ubuntu")
    def test_workflow_passes_tag_as_literal_data(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        commands = [line.split("run: ", 1)[1] for line in workflow.splitlines() if "run: " in line]
        self.assertTrue(all("${{" not in command for command in commands))
        command = next(c for c in commands if "--version=" in c)
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, RELEASE_TAG="v$(touch injected)")
            command = command.replace("python tools/release/build_release.py", 'python "' + str(ROOT / 'tools/release/build_release.py') + '"')
            result = subprocess.run(["bash", "-c", command], cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((Path(directory) / "injected").exists())
            self.assertEqual(2, len(list((Path(directory) / "dist").iterdir())))
