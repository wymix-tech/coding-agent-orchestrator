"""Work selection precedes setup; setup preserves active Work and its evidence."""
from repair_helpers import RepairCase
from scripts import cao


class VerificationPreflightTests(RepairCase):
    def scripts(self, value):
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"] = value
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)

    def event(self, name, **fields):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": name, "session_id": "native-pi", **fields,
        })
        self.assertEqual(rc, 0, out)
        return out

    def test_new_work_selection_precedes_missing_verification(self):
        self.scripts([])
        before = (self.repo / cao.CONFIG_REL).read_bytes()
        for _ in range(2):
            rc, out = self.run_cli("bootstrap", "--host", "pi")
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["mode"], "select")
            self.assertFalse(out["verification_setup"]["ready"])
            self.assertIsNone(out["work_item"])
            self.assertIn("requirement next-id", out["next"]["instruction"])
            self.assertIn("start --requirement", out["next"]["instruction"])
        self.assertEqual((self.repo / cao.CONFIG_REL).read_bytes(), before)
        self.assertEqual(list((self.repo / cao.WORK_DIR).glob("*.json")), [])
        self.assertEqual(cao.load_session(self.repo, "native-pi", host="pi"), None)

    def test_optional_only_and_invalid_declarations_need_setup(self):
        for value in ([{"name": "optional", "command": "check", "required": False}],
                      [{"name": "invalid", "command": "", "required": True}]):
            with self.subTest(scripts=value):
                self.scripts(value)
                rc, out = self.run_cli("bootstrap")
                self.assertEqual(rc, 0, out)
                self.assertEqual(out["mode"], "verification_setup" if value[0].get("name") == "invalid" else "select")
                self.assertFalse(out["verification_setup"]["ready"])

    def test_valid_configuration_keeps_work_selection(self):
        rc, out = self.run_cli("bootstrap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["mode"], "select")
        self.assertIn("verification_setup", out)
        self.assertTrue(out["verification_setup"]["ready"])
        self.assertTrue(out["verification_setup"]["tests_bound"])

    def test_sdd_authorization_still_precedes_setup(self):
        self.scripts([])
        shutil.rmtree(self.repo / "_bmad")
        rc, out = self.run_cli("bootstrap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["mode"], "sdd_select")
        self.assertEqual(out["selection_policy"], "explicit_user_choice_only")

    def test_active_work_continuity_survives_missing_or_invalid_configuration(self):
        wid = self.start()
        self.run_cli("decision", "--decision", "Use sessions", "--reason", "user scope")
        self.run_cli("checkpoint", "--step", "test-login", "--summary", "Keep current implementation")
        before = (self.repo / cao.WORK_DIR / (wid + ".json")).read_bytes()
        for value in ([], [{"name": "invalid", "command": ""}]):
            with self.subTest(scripts=value):
                self.scripts(value)
                rc, out = self.run_cli("bootstrap", "--host", "pi")
                self.assertEqual(rc, 0, out)
                self.assertEqual(out["mode"], "verification_setup")
                self.assertEqual(out["work_item"]["id"], wid)
                self.assertEqual(out["context"]["execution"]["step"], "test-login")
                self.assertEqual(out["context"]["decisions"][-1]["decision"], "Use sessions")
                self.assertEqual((self.repo / cao.WORK_DIR / (wid + ".json")).read_bytes(), before)

    def test_start_and_prompt_guidance_are_model_visible(self):
        self.start()
        self.scripts([])
        for name in ("session_start", "prompt_submit"):
            with self.subTest(event=name):
                out = self.event(name)
                self.assertEqual(out["decision"], "allow")
                text = out["additional_context"]
                self.assertIn("verification setup", text.lower())
                self.assertIn("existing project", text.lower())
                self.assertIn("new project", text.lower())
                self.assertIn(".agents", text)
                self.assertIn("already specified", text)
                self.assertIn("verification configure", text)
        self.assertEqual(cao.load_session(self.repo, "native-pi", host="pi")["status"], "active")

    def test_active_invalid_config_context_preserves_decisions_without_crashing(self):
        wid = self.start()
        self.run_cli("decision", "--decision", "Keep chosen stack", "--reason", "confirmed")
        self.scripts([{"name": "bad", "command": ""}])
        out = self.event("prompt_submit")
        self.assertEqual(out["decision"], "allow")
        self.assertIn(wid, out["additional_context"])
        self.assertIn("Keep chosen stack", out["additional_context"])
        self.assertIn("verification setup", out["additional_context"].lower())

    def test_missing_configuration_blocks_product_writes_but_allows_setup(self):
        self.start()
        self.scripts([])
        out = self.event("pre_tool", toolName="write", input={"path": "src.py", "content": "x"})
        self.assertEqual(out["decision"], "deny")
        self.assertIn("verification setup", out["reason"].lower())
        for name, args in (("read", {"path": "src.py"}),
                           ("write", {"path": ".orchestrator/requirements/new.md", "content": "scope"}),
                           ("bash", {"command": "cao verification configure --test-script 'project wrapper'"})):
            with self.subTest(tool=name):
                out = self.event("pre_tool", toolName=name, input=args)
                self.assertEqual(out["decision"], "allow")
                self.assertNotIn("additional_context", out)

    def test_internal_state_protection_precedes_invalid_setup(self):
        self.scripts([{"name": "bad", "command": ""}])
        out = self.event("pre_tool", toolName="write", input={"path": ".orchestrator/config.json", "content": "{}"})
        self.assertEqual(out["decision"], "deny")
        self.assertIn("CAO-managed", out["reason"])

    def test_start_without_baseline_establishes_initial_anchor_on_first_configure(self):
        self.scripts([])
        (self.repo / ".orchestrator/requirements").mkdir(exist_ok=True)
        (self.repo / ".orchestrator/requirements/scope.md").write_text("New product scope", encoding="utf-8")
        rc, out = self.run_cli("start", "--requirement", ".orchestrator/requirements/scope.md")
        self.assertEqual(rc, 0, out)
        self.assertIn("verification setup", out.get("warning", "").lower())
        wid = out["work_item"]["id"]
        before = self.read_work(wid)["acceptance"]
        rc, out = self.run_cli("verification", "configure", "--test-script", self.test_command)
        self.assertEqual(rc, 0, out)
        self.assertFalse(out["acceptance_review_required"])
        self.assertTrue(out["initial_baseline_established"])
        self.assertNotEqual(self.read_work(wid)["acceptance"], before)
        self.assertEqual(self.run_cli("done")[0], 2)

    def test_existing_valid_active_work_still_resumes(self):
        wid = self.start()
        rc, out = self.run_cli("bootstrap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["mode"], "resume")
        self.assertEqual(out["work_item"]["id"], wid)
        self.assertIn("verification_setup", out)
        self.assertTrue(out["verification_setup"]["ready"])

    def test_legacy_general_check_is_not_promoted_to_tests(self):
        self.scripts(["project general check"])
        rc, out = self.run_cli("bootstrap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["mode"], "select")
        self.assertIn("verification_setup", out)
        self.assertTrue(out["verification_setup"]["ready"])
        self.assertFalse(out["verification_setup"]["tests_bound"])
