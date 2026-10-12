"""Host reminders, native output and real session shutdown contracts."""
import json
import os
import subprocess

from repair_helpers import RepairCase, ROOT
from scripts import cao


class HostLifecycleTests(RepairCase):
    def test_host_schema_covers_canonical_lifecycle_and_exit_reason(self):
        schema = json.loads((ROOT / "protocol/host-event.schema.json").read_text(encoding="utf-8"))
        expected = {"session_start", "prompt_submit", "pre_tool", "post_tool", "subagent_start",
                    "subagent_stop", "stop", "turn_end", "before_settle", "session_end", "file_changed"}
        self.assertEqual(set(schema["properties"]["event"]["enum"]), expected)
        self.assertEqual(schema["properties"]["reason"]["type"], "string")
        self.assertIn("turn", schema["properties"]["event"]["description"])

    def test_work_schema_preserves_optional_legacy_and_verification_evidence(self):
        schema = json.loads((ROOT / "protocol/work-item.schema.json").read_text(encoding="utf-8"))
        self.assertNotIn("acceptance", schema["required"])
        verification = schema["properties"]["verification"]
        self.assertIn("null", verification["type"])
        result = verification["properties"]["results"]["items"]["properties"]
        self.assertEqual(result["timed_out"]["type"], "boolean")
        self.assertEqual(result["gates"]["items"]["enum"], ["tests"])
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work["path_assessment"] = None
        cao.save_work(self.repo, work)
        rc, out = self.run_cli("verify", "--script", self.test_command)
        self.assertEqual(rc, 0, out)
        self.assertIsNone(out["verification"]["assessment_revision"])
        self.assertIn("null", verification["properties"]["assessment_revision"]["type"])
        self.assertEqual(self.run_cli("done")[0], 2)

    def event(self, event, host="pi", session="A", **fields):
        raw = {"event": event, **fields}
        if session is not None:
            raw["session_id"] = session
        rc, out = self.run_cli("host-event", "--host", host, payload=raw)
        self.assertEqual(rc, 0, out)
        return out

    def test_negated_stop_keeps_session_active(self):
        self.start()
        self.event("session_start")
        out = self.event("stop", last_assistant_message="尚未完成，需要用户确认")
        self.assertEqual(out["decision"], "allow", out)
        self.assertEqual(cao.load_session(self.repo, "A", host="pi")["status"], "active")
        self.assertIn("cao", out["additional_context"])

    def test_completion_words_cannot_block_stop_or_bypass_done(self):
        self.start()
        for message in ("Done, ready to merge", "已完成", "not completed", ""):
            with self.subTest(message=message):
                self.assertEqual(self.event("stop", last_assistant_message=message)["decision"], "allow")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertEqual(cao.load_session(self.repo, "A", host="pi")["status"], "active")

    def test_two_turns_stay_active_exit_ends_with_reason(self):
        self.start()
        self.event("session_start")
        for event in ("turn_end", "prompt_submit", "agent_end", "Stop"):
            self.event(event, last_assistant_message="done")
            self.assertEqual(cao.load_session(self.repo, "A", host="pi")["status"], "active")
        self.event("session_end", reason="new")
        session = cao.load_session(self.repo, "A", host="pi")
        self.assertEqual(session["status"], "ended")
        self.assertEqual(session["last_event"], "session_end")
        self.assertEqual(session["end_reason"], "new")
        self.assertIsNotNone(session["last_handoff_id"])

    def test_native_session_end_not_stop_for_each_host(self):
        for host in ("pi", "claude-code", "codex"):
            with self.subTest(host=host):
                self.event("session_start", host=host)
                self.event("SessionEnd", host=host, reason="quit")
                self.assertEqual(cao.load_session(self.repo, "A", host=host)["last_event"], "session_end")

    def test_before_settle_unfinished_only_reminds(self):
        self.start()
        out = self.event("before_settle")
        self.assertEqual(out["decision"], "allow")
        self.assertIn("cao", out["additional_context"])
        self.assertEqual(cao.load_session(self.repo, "A", host="pi")["status"], "active")

    def test_missing_id_has_diagnostic_without_default_session(self):
        for event in ("session_start", "prompt_submit", "pre_tool", "post_tool", "turn_end", "session_end", "before_settle"):
            with self.subTest(event=event):
                out = self.event(event, session=None)
                self.assertEqual(out["decision"], "allow")
                self.assertIn("SESSION_ID", out["diagnostic"])
        self.assertEqual(cao.list_sessions(self.repo), [])

    def test_missing_child_id_cannot_end_primary(self):
        self.event("session_start")
        out = self.event("subagent_stop")
        self.assertEqual(out["decision"], "allow")
        self.assertIn("SESSION_ID", out["diagnostic"])
        self.assertEqual(cao.load_session(self.repo, "A", host="pi")["status"], "active")

    def test_child_id_collision_cannot_reclassify_or_end_primary(self):
        self.event("session_start")
        out = self.event("subagent_stop", agent_id="A")
        self.assertIn("SESSION_ID", out["diagnostic"])
        session = cao.load_session(self.repo, "A", host="pi")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["kind"], "primary")

    def test_requirement_change_reminds_without_blocking_product_write(self):
        wid = self.start()
        requirement = self.repo / self.read_work(wid)["requirement"]["path"]
        requirement.write_text("Changed scope", encoding="utf-8")
        out = self.event("pre_tool", toolName="write", input={"path": "src.py", "content": "x"})
        self.assertEqual(out["decision"], "allow")
        self.assertIn("requirement changed", out["additional_context"])
        self.assertIn("--replace", out["additional_context"])
        self.assertIn("cao path assess", out["additional_context"])

    def test_allow_keeps_additional_context(self):
        for host in ("claude-code", "codex"):
            with self.subTest(host=host):
                out = cao.native_hook_output(host, "pre_tool", {"decision": "allow", "additional_context": "assess scope"})
                self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "assess scope")
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_product_without_work_reminds_but_intake_and_reads_pass(self):
        out = self.event("pre_tool", toolName="write", input={"path": "src.py", "content": "x"})
        self.assertEqual(out["decision"], "allow")
        self.assertIn("cao start", out["additional_context"])
        for name, payload in (("read", {"path": "src.py"}), ("write", {"path": ".orchestrator/requirements/new.md", "content": "x"}),
                              ("bash", {"command": "cao next"})):
            out = self.event("pre_tool", toolName=name, input=payload)
            self.assertEqual(out["decision"], "allow")
            self.assertNotIn("additional_context", out)

    def test_missing_assessment_and_ambiguity_remind_with_next_step(self):
        wid = self.start()
        for assessment in (None, {"risk_flags": ["major_ambiguity"], "open_questions": ["scope?"]}):
            work = cao.load_work(self.repo, wid)
            if assessment is None:
                work["path_assessment"] = None
            else:
                rc, out = self.run_cli("path", "assess", "--work", wid, "--profile", "comprehensive", "--risk-flag", "major_ambiguity", "--reason", "scope?")
                self.assertEqual(rc, 0, out)
                work = cao.load_work(self.repo, wid)
            cao.save_work(self.repo, work)
            out = self.event("pre_tool", toolName="write", input={"path": "src.py", "content": "x"})
            self.assertEqual(out["decision"], "allow")
            self.assertIn("path", out["additional_context"])

    def test_internal_state_deny_includes_native_reason_even_without_id(self):
        for host in ("pi", "claude-code", "codex"):
            out = self.event("pre_tool", host=host, session=None, toolName="write", input={"path": ".orchestrator/index.json", "content": "{}"})
            if host == "pi":
                self.assertEqual(out["decision"], "deny")
                self.assertIn("CAO-managed", out["reason"])
            else:
                specific = out["hookSpecificOutput"]
                self.assertEqual(specific["permissionDecision"], "deny")
                self.assertIn("CAO-managed", specific["permissionDecisionReason"])

    def test_claude_exit_hook_installs_idempotently_codex_reports_limitation(self):
        first = cao.install_host_adapter(self.repo, "claude-code")
        self.assertEqual(first["status"], "installed", first)
        self.assertEqual(cao.install_host_adapter(self.repo, "claude-code")["status"], "already_present")
        fragment = json.loads((self.repo / ".claude/settings.local.json").read_text(encoding="utf-8"))
        self.assertIn("SessionEnd", fragment["hooks"])
        status = cao.host_adapter_status(self.repo, "codex", ROOT)
        self.assertFalse(status["session_end_supported"])
        self.assertIn("SessionEnd", " ".join(status["limitations"]))

    def test_shell_bridge_reports_failure_without_leaking_stdout_or_blocking(self):
        if os.name == "nt":
            self.skipTest("POSIX shell bridge fixture")
        fake = self.repo / "cao"
        fake.write_text('#!/bin/sh\nprintf \'{"decision":"block"}\\n\'\nprintf "failure detail\\n" >&2\nexit 7\n', encoding="utf-8")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=str(self.repo) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["sh", str(ROOT / "hooks/host-event.sh"), "--host", "claude-code"],
                                cwd=self.repo, env=env, text=True, input="{}", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("failure detail", result.stderr)
        self.assertIn("7", result.stderr)
