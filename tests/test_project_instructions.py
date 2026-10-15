"""Project instruction template provisioning tests.

`AGENTS.md` / `CLAUDE.md` are read when a session starts, so like the host hooks
they are provisioned by `cao init` rather than discovered later by `cao bootstrap`.
`cao bootstrap` keeps an idempotent re-check because it never overwrites.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"

HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}

# The clauses that encode the hardened native-workflow contract. If a template
# edit drops one of these, the instructions injected into projects go stale and
# Agents fall back to the pre-hardening workflow.
CONTRACT_COMMANDS = (
    "path assess --profile",
    "sdd workflow select",
    "sdd workflow execute",
    "sdd workflow complete",
    "gate satisfy",
)


# Which instruction file each host reads, mirroring cao.INSTRUCTION_TEMPLATES_BY_HOST.
# `cao init` installs only the one that applies, so tests must ask for the file a
# host actually receives instead of assuming both always land.
INSTRUCTION_FILES_BY_HOST = {
    "claude-code": ("CLAUDE.md",),
    "codex": ("AGENTS.md",),
    "pi": ("AGENTS.md",),
}
ALL_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def instruction_files(host: str | None) -> tuple[str, ...]:
    return INSTRUCTION_FILES_BY_HOST.get(host or "", ALL_INSTRUCTION_FILES)


def rendered_instructions(host: str | None = "claude-code", env_extra=None) -> dict[str, str]:
    """Render the templates a given host would receive: {filename: text}."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td)
        args = ["init"] + ([] if host is None else ["--host", host])
        run(repo, *args, env_extra=env_extra)
        return {name: (repo / name).read_text(encoding="utf-8") for name in instruction_files(host)}


def every_template_text(env_extra=None) -> dict[str, str]:
    """Both templates, each rendered for the host that actually reads it."""
    texts: dict[str, str] = {}
    texts.update(rendered_instructions("claude-code", env_extra))
    texts.update(rendered_instructions("pi", env_extra))
    return texts


def parsed_cao_command(text: str) -> str | None:
    """Read the CAO invocation out of the rendered `## CAO command` block."""
    match = __import__("re").search(r"```\n(.+?)\n```", text, __import__("re").S)
    return match.group(1).strip() if match else None


def run(repo, *args, env_extra=None):
    env = __import__("os").environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, env=env, timeout=60)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"raw": p.stdout}
    return p.returncode, out, p.stderr


class InitInstallsTemplates(unittest.TestCase):
    def test_init_installs_only_the_file_the_host_reads(self):
        """A project must not carry instructions for a host it never runs."""
        cases = {
            "claude-code": ("CLAUDE.md", ("AGENTS.md",)),
            "codex": ("AGENTS.md", ("CLAUDE.md",)),
            "pi": ("AGENTS.md", ("CLAUDE.md",)),
        }
        for host, (installed, absent) in cases.items():
            with tempfile.TemporaryDirectory() as td:
                repo = pathlib.Path(td)
                rc, out, _ = run(repo, "init", "--host", host)
                self.assertEqual(rc, 0, host)
                self.assertEqual(out["project_instructions"]["installed"], [installed], host)
                self.assertTrue((repo / installed).is_file(), host)
                for name in absent:
                    self.assertFalse((repo / name).exists(), f"{host} project should not carry {name}")

    def test_unknown_host_gets_every_template_as_a_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", env_extra=NO_HOST_ENV)
            for name in ALL_INSTRUCTION_FILES:
                self.assertTrue((repo / name).is_file(), f"fallback install omitted {name}")

    def test_known_host_is_rendered_into_the_instructions(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "codex")
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("bootstrap --repo . --host codex", agents)
            self.assertNotIn("--host <host>", agents)

    def test_claude_code_receives_only_the_claude_specific_file(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "claude-code")
            claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("bootstrap --repo . --host claude-code", claude)
            self.assertNotIn("--host <host>", claude)

    def test_unknown_host_keeps_the_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", env_extra=NO_HOST_ENV)
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("--host <host>", agents)

    def test_existing_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "AGENTS.md").write_text("# project's own rules\n", encoding="utf-8")
            rc, out, _ = run(repo, "init", "--host", "codex")
            self.assertEqual(rc, 0)
            self.assertEqual(out["project_instructions"]["skipped_existing"], ["AGENTS.md"])
            self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), "# project's own rules\n")


class TemplateContentStaysCurrent(unittest.TestCase):
    def test_templates_encode_the_native_workflow_contract(self):
        for name, text in every_template_text().items():
            for command in CONTRACT_COMMANDS:
                self.assertIn(command, text, f"{name} is missing `{command}`")

    def test_templates_state_light_minimal_completion_contract(self):
        for name, text in every_template_text().items():
            self.assertIn("sdd spec bind", text, name)
            self.assertIn("are optional", text, name)


    def test_templates_forbid_placeholder_entrypoints_and_fake_artifacts(self):
        for name, text in every_template_text().items():
            self.assertIn("placeholder entrypoint", text, name)
            self.assertIn("product source files", text, name)


    def test_templates_teach_the_numbered_requirement_convention(self):
        for name, text in every_template_text().items():
            self.assertIn("requirement next-id", text, f"{name} omits requirement numbering")
            self.assertIn(".orchestrator/requirements/", text, f"{name} omits the requirements directory")


    def test_templates_list_every_supported_host(self):
        agents = rendered_instructions("pi")["AGENTS.md"]
        for host in ("claude-code", "codex", "pi"):
            self.assertIn(host, agents)


class CaoPathIsNeverGuessed(unittest.TestCase):
    def test_no_placeholder_survives_rendering(self):
        for name, text in every_template_text().items():
            self.assertNotIn("{{CAO_COMMAND}}", text, f"{name} kept an unrendered placeholder")


    def test_templates_draw_the_derived_state_boundary(self):
        """An Agent must know `.orchestrator` is CAO-derived before it hand-edits state."""
        for name, text in every_template_text().items():
            self.assertIn("derived state", text, name)
            self.assertIn("never edit those files by hand", text, name)
            self.assertIn("except `requirements/`", text, name)
            # The verbatim file paths are what let an Agent recognise them.
            for token in ("config.json", "index.json", "work/", "sessions/", "handoffs/", "lifecycle/"):
                self.assertIn(token, text, f"{name} omits {token}")


    def test_the_hardcoded_guessed_path_is_gone(self):
        for name, text in every_template_text().items():
            self.assertNotIn(".agents/skills/coding-agent-orchestrator/scripts/cao.py", text, name)


    def test_every_command_uses_the_rendered_prefix(self):
        """A half-rendered file is worse than none: step 1 would work and the rest would fail."""
        for name, text in every_template_text().items():
            prefix = parsed_cao_command(text)
            self.assertIsNotNone(prefix, f"{name} has no CAO command block")
            self.assertTrue(prefix)
            if prefix != "cao":
                # No bare `cao ...` command may remain once the prefix is resolved.
                self.assertNotIn("`cao ", text, f"{name} still contains an unprefixed command")
            for command in CONTRACT_COMMANDS:
                self.assertIn(f"{prefix} {command}", text, f"{name}: `{command}` is not prefixed")


    def test_the_rendered_command_actually_runs(self):
        import shlex
        prefix = parsed_cao_command(rendered_instructions("pi")["AGENTS.md"])
        p = subprocess.run([*shlex.split(prefix), "--help"], cwd=str(ROOT), text=True, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0, f"rendered CAO command is not runnable: {prefix}")

    def test_the_implementation_is_never_exposed(self):
        """Agents must call the skill launcher, never `scripts/cao.py`."""
        for name, text in every_template_text().items():
            self.assertNotIn("cao.py", text, f"{name} points the Agent at the CAO implementation")


    def test_without_a_console_script_the_skill_launcher_is_used(self):
        import shutil
        if shutil.which("cao"):
            self.skipTest("`cao` is installed as a console script in this environment")
        prefix = parsed_cao_command(rendered_instructions("pi")["AGENTS.md"])
        self.assertFalse(prefix.endswith("cao.py"))
        self.assertTrue(prefix == "cao" or prefix.endswith("/cao") or prefix.startswith("sh "),
                        f"unexpected invocation: {prefix}")


class BootstrapBackfill(unittest.TestCase):
    def test_bootstrap_restores_a_deleted_instruction_file(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "codex")
            (repo / "AGENTS.md").unlink()
            run(repo, "bootstrap", "--repo", ".", "--host", "codex")
            self.assertTrue((repo / "AGENTS.md").is_file())


if __name__ == "__main__":
    unittest.main()
