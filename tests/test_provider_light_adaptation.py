"""Native oneshot intent and provider-specific light entrypoints."""
from pathlib import Path
from scripts import cao
from tests.repair_helpers import RepairCase

ONESHOT = "---\ntitle: 'Login fix'\nroute: 'oneshot'\nstatus: 'done'\n---\n<frozen-after-approval>\n## Intent\nReject an empty password and keep existing valid logins working.\n</frozen-after-approval>\n## Implementation Notes\nFixed the validator.\n"


class OneShotCompletion(RepairCase):
    def native_spec(self, text=ONESHOT):
        path = self.repo / '_bmad-output/spec-fix.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def bind(self, *args):
        return self.run_cli('sdd', 'spec', 'bind', '--spec', '_bmad-output/spec-fix.md', *args)

    def test_oneshot_closes_without_acceptance_heading_or_extra_artifacts(self):
        wid = self.start(); path = self.native_spec()
        before = path.read_bytes()
        rc, out = self.bind(); self.assertEqual(rc, 0, out)
        self.assertEqual(out['binding']['mode'], 'bmad_oneshot')
        self.assertEqual(out['binding']['criteria_section'], 'Intent')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in path.parent.iterdir()], ['spec-fix.md'])
        rc, out = self.run_cli('verify'); self.assertEqual(rc, 0, out)
        from scripts.dashboard import build_dashboard_overview
        view = build_dashboard_overview(self.repo)['work_items'][0]
        self.assertEqual(view['light_spec']['mode'], 'bmad_oneshot')
        self.assertIn('no separate acceptance checklist', view['light_spec']['note'])
        self.assertEqual(view['status'], 'active')
        rc, out = self.run_cli('done'); self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(wid)['status'], 'completed')
        self.assertNotIn('workflow', self.read_work(wid)['sdd'])

    def test_only_real_oneshot_shape_gets_automatic_intent_binding(self):
        self.start()
        cases = [ONESHOT.replace("route: 'oneshot'", "route: 'dispatch'"),
                 ONESHOT.replace("route: 'oneshot'\n", ''),
                 ONESHOT.replace("route: 'oneshot'", "route: 'oneshot'\nroute: 'dispatch'"),
                 ONESHOT.replace("status: 'done'", "status: 'draft'"),
                 ONESHOT.replace('Reject an empty password and keep existing valid logins working.', '<!-- empty -->'),
                 ONESHOT.replace('## Implementation Notes', '## Intent\nAnother intent\n## Implementation Notes')]
        for text in cases:
            with self.subTest(text=text):
                self.native_spec(text)
                rc, out = self.bind(); self.assertEqual(rc, 2, out)

    def test_oneshot_does_not_apply_to_other_profiles_or_providers(self):
        self.start(profile='standard'); self.native_spec()
        rc, out = self.bind(); self.assertEqual(rc, 2, out)
        self.run_cli('path', 'assess', '--profile', 'light', '--reason', 'bounded fixture')
        self.run_cli('sdd', 'select', '--provider', 'openspec')
        folder = self.repo / 'openspec'; folder.mkdir(exist_ok=True)
        (folder / 'spec.md').write_text(ONESHOT)
        rc, out = self.run_cli('sdd','spec','bind','--spec','openspec/spec.md')
        self.assertEqual(rc, 2, out)

    def test_explicit_criteria_still_win_and_changes_require_rebind(self):
        self.start(); path = self.native_spec()
        criteria = path.parent / 'criteria.md'; criteria.write_text('- Invalid password is rejected.\n')
        rc, out = self.bind('--criteria', '_bmad-output/criteria.md'); self.assertEqual(rc, 0, out)
        self.assertNotIn('mode', out['binding'])
        rc, out = self.bind('--replace'); self.assertEqual(rc, 0, out)
        self.run_cli('verify')
        path.write_text(ONESHOT.replace('empty password', 'empty credentials'))
        rc, out = self.run_cli('done'); self.assertEqual(rc, 2, out)
        self.assertIn('--replace', out['next'])
        self.assertNotIn('--criteria-section', out['next'])
        rc, out = self.bind('--replace'); self.assertEqual(rc, 0, out)
        rc, out = self.run_cli('done'); self.assertEqual(rc, 2, out)
        self.run_cli('verify')
        rc, out = self.run_cli('done'); self.assertEqual(rc, 0, out)

    def test_oneshot_does_not_bypass_failed_verification_or_changed_requirement(self):
        wid = self.start(); self.native_spec(); self.bind()
        (self.repo / 'acceptance-tests/test_acceptance.py').write_text('import unittest\nclass Broken(unittest.TestCase):\n def test_bad(self): self.fail("not fixed")\n')
        rc, out = self.run_cli('verify'); self.assertEqual(rc, 1, out)
        rc, out = self.run_cli('done'); self.assertEqual(rc, 2, out)
        req = self.repo / self.read_work(wid)['requirement']['path']; req.write_text('Changed scope')
        rc, out = self.run_cli('done'); self.assertEqual(rc, 2, out)
        self.assertIn('requirement changed', out['reason'])


class ProviderGuidance(RepairCase):
    def test_light_guidance_preserves_native_preconditions(self):
        self.assertIn('oneshot', cao.SDD_CATALOG['bmad']['path_guidance']['light'])
        openspec = cao.SDD_CATALOG['openspec']['path_guidance']['light']
        self.assertIn('/opsx:propose', openspec)
        self.assertIn('schema', openspec)
        self.assertIn('tasks', openspec)
        speckit = cao.SDD_CATALOG['speckit']['path_guidance']['light']
        self.assertIn('bug-assess', speckit)
        self.assertIn('plan.md', speckit)
        self.assertIn('tasks.md', speckit)
        self.assertIn('installed', speckit)

    def test_cli_setup_is_distinct_from_agent_implementation(self):
        for provider, command in [('openspec', 'openspec'), ('speckit','specify')]:
            with self.subTest(provider=provider):
                guidance = cao.skill_invocation_guidance(self.repo, provider, command)
                self.assertIn('agent', guidance.lower())
                self.assertNotIn('runs its workflows through its own CLI', guidance)
        folder = self.repo / '.agents/skills/openspec-apply'; folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('Follow the active schema')
        self.assertNotIn('renderer', cao.skill_invocation_guidance(self.repo,'openspec','openspec-apply'))

    def test_opsx_skills_are_discovered_and_provider_specific(self):
        folder = self.repo / '.agents/skills/opsx-apply'; folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('Apply change')
        self.assertIn('opsx-apply', [s['name'] for s in cao.provider_native_skills(self.repo,'openspec')])
        self.assertTrue(cao.validate_workflow_entrypoint('openspec','apply','/opsx:apply')['ok'])
        self.assertFalse(cao.validate_workflow_entrypoint('speckit','apply','/opsx:apply')['ok'])

    def test_known_nonexistent_cli_subcommands_are_not_workflow_evidence(self):
        for provider, entrypoint in [('bmad','bmad quick-fix'),('openspec','openspec apply'),('speckit','specify implement')]:
            with self.subTest(provider=provider):
                self.assertFalse(cao.validate_workflow_entrypoint(provider,'build',entrypoint)['ok'])

    def test_known_mistaken_cli_names_are_rejected_through_literal_runners(self):
        for entrypoint in ('sh -c "specify implement"', 'env specify implement',
                           'npx --yes @fission-ai/openspec@latest apply', 'uv run --no-cache bmad quick-fix'):
            provider = 'openspec' if 'openspec' in entrypoint else 'bmad' if 'bmad' in entrypoint else 'speckit'
            self.assertFalse(cao.validate_workflow_entrypoint(provider,'build',entrypoint)['ok'], entrypoint)

    def test_docs_define_oneshot_and_native_light_preconditions(self):
        from tests.repair_helpers import ROOT
        for name in ('README.md', 'SKILL.md', 'templates/AGENTS.md', 'templates/CLAUDE.md'):
            text = (ROOT/name).read_text()
            self.assertIn('oneshot', text, name)
            self.assertIn('Intent', text, name)
            self.assertIn('/opsx:propose', text, name)
            self.assertIn('bug-assess', text, name)
            self.assertIn('plan.md', text, name)
            self.assertIn('tasks.md', text, name)
