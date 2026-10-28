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
