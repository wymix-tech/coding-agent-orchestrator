import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import project_architecture as arch


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.context = {'scopes': {s: {'paths': ['.'] if s == 'root' else [s], 'technology': {}} for s in ('root', 'backend', 'frontend')}}
        self.loader = patch.object(arch, 'load_context', return_value=self.context)
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def text(self, scope='root', template='spring-boot-modular-ddd'):
        text = arch.catalog(self.repo)[template]['text']
        root = '' if scope == 'root' else scope + '/'
        return text.replace('{{scope}}', scope).replace('{{source_root}}', root+'src').replace('{{test_root}}', root+'tests')

    def adopt(self, scope='root'):
        text = self.text(scope)
        p = self.repo / arch.scope_file(scope)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        arch.atomic_json(self.repo / arch.receipt_path(scope), {'sha256': arch.sha(text)})
        return p

    def test_catalog_and_custom_duplicate(self):
        self.assertEqual(14, len(arch.catalog(self.repo)))
        p = self.repo / arch.DIRECTORY / 'custom' / 'duplicate.md'
        p.parent.mkdir(parents=True)
        p.write_text(self.text())
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_ID'):
            arch.catalog(self.repo)

    def test_content_stale_even_without_version_change(self):
        p = self.adopt()
        self.assertTrue(arch.state(self.repo, ['root'], required=True)['ready'])
        p.write_text(p.read_text()+'\nChanged boundary\n')
        self.assertEqual('ARCHITECTURE_RULE_STALE', arch.state(self.repo, ['root'])['reason'])

    def test_scope_and_binding_isolation(self):
        self.adopt('backend')
        self.adopt('frontend')
        expected = arch.snapshot(self.repo, ['backend'])
        (self.repo / arch.scope_file('frontend')).write_text(self.text('frontend')+'\nChanged\n')
        work = {'project_binding': {'scopes': ['backend'], 'architecture': expected}}
        self.assertTrue(arch.state(self.repo, work=work)['ready'])
        self.adopt()
        self.assertEqual('WORK_ARCHITECTURE_BINDING_STALE', arch.state(self.repo, work=work)['reason'])

    def test_missing_and_path_safety(self):
        self.assertEqual('ARCHITECTURE_REQUIRED', arch.state(self.repo, ['backend'], required=True)['reason'])
        with self.assertRaises(ValueError):
            arch.scope_file('..')
        with self.assertRaises(ValueError):
            arch.parse(self.text().replace('"src"', '"../outside"'))
        (self.repo / 'link').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            arch.safe(self.repo, Path('link/file'))

    def test_real_java_dependency_fail_and_pass(self):
        self.adopt()
        path = self.repo / 'src/order/web/OrderController.java'
        path.parent.mkdir(parents=True)
        path.write_text('package app.order.web; import app.order.infrastructure.Repository; class OrderController {}')
        self.assertFalse(arch.check(self.repo, ['root'])['ready'])
        path.write_text('package app.order.web; import app.order.application.Service; // import app.order.infrastructure.Repository;\nclass OrderController {}')
        result = arch.check(self.repo, ['root'])
        self.assertTrue(result['ready'])
        self.assertTrue(result['semantic_review_required'])
        self.assertIn('unsupported', [r['status'] for r in result['results']])

    def test_draft_preserves_input_and_scope(self):
        result = arch.draft(self.repo, 'backend', 'generic-custom', {'source_root':'backend/src', 'test_root':'backend/tests'})
        self.assertTrue((self.repo / result['path']).is_file())
        with self.assertRaisesRegex(ValueError, 'DRAFT_EXISTS'):
            arch.draft(self.repo, 'backend', 'generic-custom', {'source_root':'backend/src', 'test_root':'backend/tests'})
        with self.assertRaisesRegex(ValueError, 'SCOPE_PATH'):
            arch.validate_instance(self.repo, 'backend', self.text('backend').replace('backend/src','frontend/src'))

    def test_apply_requires_real_evidence_and_compare_and_swap(self):
        arch.draft(self.repo, 'root', 'generic-custom', {'source_root':'src', 'test_root':'tests'})
        with self.assertRaises(ValueError):
            arch.accept(self.repo, 'root', 'fake', arch.sha(''))
        with patch('scripts.project_sources.user_evidence', return_value={}):
            result = arch.accept(self.repo, 'root', 'evidence', arch.sha(''))
            self.assertEqual('accepted', result['status'])
            with self.assertRaisesRegex(ValueError, 'REVISION_CONFLICT'):
                arch.accept(self.repo, 'root', 'evidence', arch.sha(''))

    def test_input_grants_exclude_accepted_state(self):
        self.assertTrue(arch.writable_input(self.repo, '.orchestrator/project/architecture-inputs/root.md'))
        self.assertFalse(arch.writable_input(self.repo, str(arch.scope_file('root'))))

if __name__ == '__main__':
    unittest.main()

class ArchitectureIntegrationTests(ArchitectureTests):
    def test_yaml_custom_rule(self):
        import yaml
        text = self.text()
        front, body = text[4:].split('\n---\n',1)
        result = arch.parse('---\n'+yaml.safe_dump(json.loads(front))+'---\n'+body)
        self.assertEqual('spring-boot-modular-ddd', result['rule_id'])

    def test_new_project_policy_blocks_missing_architecture(self):
        with patch('scripts.cao_runtime.config', return_value={'architecture_policy':1}):
            self.assertEqual('ARCHITECTURE_REQUIRED', arch.state(self.repo,['root'])['reason'])
            self.adopt()
            self.assertTrue(arch.state(self.repo,['root'])['ready'])

    def test_old_work_can_resume_and_new_scope_rules_inherit(self):
        work={'project_binding':{'scopes':['backend']}}
        self.assertTrue(arch.state(self.repo,work=work)['ready'])
        self.adopt()
        self.assertEqual(1,len(arch.snapshot(self.repo,['backend'])))

    def test_accept_revalidates_edited_input(self):
        arch.draft(self.repo,'backend','generic-custom',{'source_root':'backend/src','test_root':'backend/tests'})
        path=self.repo/arch.ROOT/'architecture-inputs/backend.md'
        path.write_text(path.read_text().replace('backend/src','frontend/src'))
        with patch('scripts.project_sources.user_evidence',return_value={}):
            with self.assertRaisesRegex(ValueError,'SCOPE_PATH'):
                arch.accept(self.repo,'backend','evidence',arch.sha(''))

    def test_packaged_templates(self):
        from tools.release.build_release import included_files
        files={p.name for p in included_files()}
        self.assertIn('spring-boot-modular-ddd.md',files)

from tests.repair_helpers import RepairCase

class ArchitectureCliFlow(RepairCase):
    def test_first_work_adopts_real_cli_rule_and_retains_binding(self):
        from scripts.project_sources import observe_prompt
        from scripts import cao
        config_path=self.repo/'.orchestrator/config.json'
        cfg=json.loads(config_path.read_text())
        cfg['architecture_policy']=1
        config_path.write_text(json.dumps(cfg))
        rc,out=self.run_cli('project','architecture','show')
        self.assertEqual((2,'ARCHITECTURE_REQUIRED'),(rc,out['reason']))
        ref=observe_prompt(self.repo,'Use generic-custom architecture for this project.','pi','architecture-session')
        rc,out=self.run_cli('project','architecture','draft','--template','generic-custom',
                            '--variable','source_root=acceptance-tests','--variable','test_root=acceptance-tests')
        self.assertEqual(0,rc,out)
        rc,out=self.run_cli('project','architecture','diff')
        self.assertEqual(0,rc,out)
        rc,out=self.run_cli('project','architecture','apply','--evidence',ref,'--expected-sha256',out['expected_sha256'])
        self.assertEqual(0,rc,out)
        wid=self.start(profile='light')
        work=cao.load_work(self.repo,wid)
        self.assertEqual('generic-custom',work['project_binding']['architecture'][0]['rule_id'])
        from scripts.project_context import binding_state
        self.assertTrue(binding_state(self.repo,work)['ready'])
        path=self.repo/arch.scope_file('root')
        path.write_text(path.read_text()+'\nChanged contract\n')
        self.assertEqual('ARCHITECTURE_RULE_STALE',binding_state(self.repo,work)['reason'])

class LayoutRegression(ArchitectureTests):
    def test_flat_business_class_fails_but_boot_entry_passes(self):
        self.adopt()
        root=self.repo/'src'
        root.mkdir()
        (root/'OrderController.java').write_text('package app; class OrderController {}')
        result=arch.check(self.repo,['root'])
        self.assertFalse(result['ready'])
        self.assertTrue(any(r['rule_id']=='ARCH-STRUCT-001' and r['status']=='failed' for r in result['results']))
        (root/'OrderController.java').unlink()
        (root/'OrderApplication.java').write_text('package app; class OrderApplication {}')
        self.assertTrue(arch.check(self.repo,['root'])['ready'])
