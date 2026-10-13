"""No-Work business writes are denied without deadlocking CAO or native intake."""
from scripts import cao
from tests.repair_helpers import RepairCase


class NoWorkGuardTests(RepairCase):
    def event(self, tool, value, host="pi"):
        rc, out = self.run_cli("host-event", "--host", host, payload={
            "event": "pre_tool", "session_id": "fresh-project",
            "toolName": tool, "input": value,
        })
        self.assertEqual(rc, 0, out)
        return out

    def test_business_writes_block_including_the_remote_login_sequence(self):
        paths = ["pom.xml", "src/main/java/AuthService.java", "src/main/resources/user.yaml",
                 "src/test/java/AuthServiceTest.java", "README.md"]
        for path in paths:
            for tool in ("write", "edit"):
                with self.subTest(path=path, tool=tool):
                    out = self.event(tool, {"path": path, "content": "business implementation"})
                    self.assertEqual(out["decision"], "deny", out)
                    self.assertIn("requirement next-id", out["reason"])
                    self.assertIn("start --requirement", out["reason"])

    def test_native_and_requirement_writes_stay_allowed(self):
        for directory in cao.intake_writable_dirs():
            with self.subTest(directory=directory):
                out = self.event("write", {"path": directory + "spec.md", "content": "scope"})
                self.assertEqual(out["decision"], "allow", out)
                self.assertNotIn("additional_context", out)
                out = self.event("bash", {"command": f"mkdir -p {directory}new"})
                self.assertEqual(out["decision"], "allow", out)

    def test_shell_native_destinations_are_not_confused_with_read_sources(self):
        for command in (
            "echo text > _bmad-output/spec.md", "cat README.md > openspec/spec.md",
            "cp README.md _bmad-output/spec.md", "cp .orchestrator/config.json specs/config-copy.json",
            "printf text | tee .specify/spec.md", "sed -i 's/old/new/' _bmad-output/spec.md",
            "sed -n '1,10p' .orchestrator/config.json > _bmad-output/config-copy.md",
            "cat <<'EOF' > _bmad-output/spec.md\nLogin spec\nEOF\n",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.event("bash", {"command": command})["decision"], "allow")

    def test_mixed_writes_and_native_escape_are_denied(self):
        patch = "*** Begin Patch\n*** Add File: _bmad-output/spec.md\n+scope\n*** Add File: src.py\n+code\n*** End Patch"
        self.assertEqual(self.event("apply_patch", {"patch": patch})["decision"], "deny")
        self.assertEqual(self.event("move", {"path": "_bmad-output/spec.md", "destination": "src.py"})["decision"], "deny")
        for command in ("mkdir -p src/main/java", "cao status && echo code > src.py",
                        "echo scope > _bmad-output/spec.md; echo code > src.py"):
            self.assertEqual(self.event("bash", {"command": command})["decision"], "deny")
        self.assertEqual(self.event("bash", {"command": "cat <<'EOF' > _bmad-output/spec.md\nLogin spec\nEOF\necho code > src.py"})["decision"], "deny")
        (self.repo / "src.py").write_text("code")
        (self.repo / "_bmad-output").mkdir()
        (self.repo / "_bmad-output/alias.md").symlink_to(self.repo / "src.py")
        for path in ("_bmad-output/../src.py", "_bmad-output/alias.md"):
            self.assertEqual(self.event("write", {"path": path, "content": "code"})["decision"], "deny")

    def test_native_patches_remain_allowed_in_all_host_payload_forms(self):
        patch = "*** Begin Patch\n*** Add File: _bmad-output/spec.md\n+scope\n*** End Patch"
        for payload in (patch, {"patch": patch}, {"command": patch}, {"input": patch}):
            self.assertEqual(self.event("apply_patch", payload)["decision"], "allow", payload)

    def test_cao_commands_are_exempt_but_hand_edits_are_not(self):
        launcher = str(cao.package_root_dir() / "cao")
        for command in (
            "cao bootstrap --host pi", "cao requirement next-id --title 'Login'",
            "cao start --requirement .orchestrator/requirements/login.md",
            "cao sdd install --provider bmad --host pi --yes",
            "cao verification configure --test-script './tests.sh'",
            f"{launcher} bootstrap --host pi", f"sh {launcher} status",
            f"python3 {cao.__file__} start --requirement .orchestrator/requirements/login.md",
        ):
            self.assertEqual(self.event("bash", {"command": command})["decision"], "allow", command)
        out = self.event("write", {"path": ".orchestrator/config.json", "content": "{}"})
        self.assertEqual(out["decision"], "deny")
        self.assertIn("CAO-managed", out["reason"])

    def test_active_work_allows_setup_and_oneshot_implementation(self):
        self.start()
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"] = []
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        out = self.event("write", {"path": "src.py", "content": "code"})
        self.assertEqual(out["decision"], "allow")
        self.assertIn("verification setup", out["additional_context"].lower())
        self.assertEqual(self.event("bash", {"command": "mkdir -p src"})["decision"], "allow")

    def test_native_metadata_tools_stay_allowed_without_granting_business_writes(self):
        script = self.repo / "_bmad/scripts/render_skill.py"
        script.parent.mkdir()
        script.write_text("print('native instructions')")
        renderer = f"uv run --no-cache {script} --project-root {self.repo} --skill {self.repo}/.agents/skills/bmad-build"
        self.assertEqual(self.event("bash", {"command": renderer})["decision"], "allow")
        for command in (renderer + " > src.py", renderer + "; echo code > src.py", renderer + " --output src.py"):
            self.assertEqual(self.event("bash", {"command": command})["decision"], "deny")
        (self.repo / "openspec").mkdir()
        for command in ("openspec new change add-login", "openspec status --change add-login", "openspec instructions proposal --change add-login"):
            self.assertEqual(self.event("bash", {"command": command})["decision"], "allow")
        for folder, suffix, runner in (("bash", ".sh", "sh"), ("powershell", ".ps1", "pwsh -File")):
            helper = self.repo / ".specify/scripts" / folder / ("create-new-feature" + suffix)
            helper.parent.mkdir(parents=True)
            helper.write_text("# Installed native metadata helper fixture")
            command = f"{runner} {helper} --short-name login 'Session login'"
            self.assertEqual(self.event("bash", {"command": command})["decision"], "allow")
            self.assertEqual(self.event("bash", {"command": command + "; echo code > src.py"})["decision"], "deny")

    def test_shell_tool_aliases_preserve_the_same_boundary(self):
        for tool in cao.SHELL_TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual(self.event(tool, {"cmd": "echo code > src.py"})["decision"], "deny")
                self.assertEqual(self.event(tool, {"cmd": "echo scope > specs/spec.md"})["decision"], "allow")
                self.assertEqual(self.event(tool, {"cmd": "cao status"})["decision"], "allow")

    def test_native_directory_symlink_cannot_exempt_product_tree(self):
        (self.repo / "src").mkdir()
        (self.repo / "_bmad-output").symlink_to(self.repo / "src", target_is_directory=True)
        out = self.event("write", {"path": "_bmad-output/AuthService.java", "content": "code"})
        self.assertEqual(out["decision"], "deny")

    def test_native_heredoc_cannot_hide_expanded_business_or_state_writes(self):
        for path in ("src.py", ".orchestrator/config.json"):
            command = f"cat <<EOF > _bmad-output/spec.md\n$(echo code > {path})\nEOF\n"
            self.assertEqual(self.event("bash", {"command": command})["decision"], "deny")
        literal = "cat <<'EOF' > _bmad-output/spec.md\nExample: $(echo code > src.py)\nEOF\n"
        self.assertEqual(self.event("bash", {"command": literal})["decision"], "allow")

    def test_empty_verification_does_not_deadlock_numbered_intake_and_start(self):
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"] = []
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        rc, out = self.run_cli("requirement", "next-id", "--title", "Login")
        self.assertEqual(rc, 0, out)
        path = out["path"]
        self.assertEqual(self.event("write", {"path": path, "content": "Login using sessions"})["decision"], "allow")
        (self.repo / path).write_text("# Login\nLogin using sessions\n")
        rc, out = self.run_cli("start", "--requirement", path)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["work_item"]["status"], "active")
        self.assertFalse(out["verification_setup"]["ready"])
        rc, out = self.run_cli("bootstrap", "--host", "pi")
        self.assertEqual(out["mode"], "verification_setup")

    def test_no_work_bootstrap_and_context_prioritize_intake(self):
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"] = []
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        rc, out = self.run_cli("bootstrap", "--host", "pi")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["mode"], "select")
        self.assertFalse(out["verification_setup"]["ready"])
        self.assertIn("requirement next-id", out["next"]["instruction"])
        text = cao.render_context(self.repo, None)
        self.assertIn("start --requirement", text)
        self.assertIn("before", text.lower())

    def test_suspended_work_requires_resume_instead_of_new_intake(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        cao.register_session(self.repo, work, "fresh-project", "pi", event="session_start")
        work["status"] = "suspended"
        cao.save_work(self.repo, work)
        out = self.event("write", {"path": "src.py", "content": "code"})
        self.assertEqual(out["decision"], "deny")
        self.assertIn(f"resume --work {wid}", out["reason"])

    def test_claude_and_codex_receive_native_deny(self):
        for host in ("claude-code", "codex"):
            out = self.event("write", {"path": "src.py", "content": "code"}, host=host)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", out)

    def test_readonly_tools_and_stop_are_not_blocked(self):
        self.assertEqual(self.event("read", {"path": "src.py"})["decision"], "allow")
        self.assertEqual(self.event("bash", {"command": "ls; git status"})["decision"], "allow")
        self.assertEqual(self.event("bash", {"command": "which java && java -version"})["decision"], "allow")
        for event in ("turn_end", "before_settle"):
            rc, out = self.run_cli("host-event", "--host", "pi", payload={
                "event": event, "session_id": "fresh-project", "last_assistant_message": "Waiting for user"})
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["decision"], "allow")
