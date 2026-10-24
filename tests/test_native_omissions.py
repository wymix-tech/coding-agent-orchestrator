"""Regressions from demo3: independent review, early baseline, commit and owned smoke."""
import json
import http.client
import os
import shlex
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from tests.repair_helpers import RepairCase
from tests.helpers import fixture_native_review
from scripts import cao
from scripts.smoke_runner import run_smoke
from tests.test_provider_light_adaptation import ONESHOT


class NativeOmissionTests(RepairCase):
    def setup_oneshot(self):
        self.wid=self.start()
        self.spec=self.repo/'_bmad-output/spec-fix.md';self.spec.parent.mkdir(exist_ok=True)
        self.spec.write_text(ONESHOT)
        (self.repo/'product.py').write_text('value = 1\n')
        rc,out=self.run_cli('sdd','spec','bind','--spec',self.spec.relative_to(self.repo));self.assertEqual(rc,0,out)

    def review(self):
        return fixture_native_review(self.repo,lambda repo,*a:self.run_cli(*a),self.wid)

    def event(self,path,tool='write',**extra):
        return self.run_cli('host-event','--host','pi',payload={'event':'pre_tool','session_id':'primary-demo3','tool_name':tool,'tool_input':{'path':path,'content':'fixture',**extra}})[1]

    def missing_baseline(self):
        cfg=cao.config(self.repo);cfg['verification']['scripts']=[];cao.save_json(self.repo/cao.CONFIG_REL,cfg)
        self.wid=self.start()

    def test_business_before_baseline_denied_in_all_hosts(self):
        self.missing_baseline()
        for host in ('pi','codex','claude-code'):
            rc,out=self.run_cli('host-event','--host',host,payload={'event':'pre_tool','session_id':'baseline-'+host,'tool_name':'write','tool_input':{'path':'main.py','content':'business'}})
            self.assertEqual(rc,0,out)
            decision=out.get('decision') or (out.get('hookSpecificOutput') or {}).get('permissionDecision')
            self.assertEqual(decision,'deny',out)

    def test_setup_and_native_outputs_remain_writable(self):
        self.missing_baseline()
        for path in ('tests/test_auth.py','scripts/verification/check.sh','pyproject.toml','requirements.txt','_bmad-output/spec.md','.orchestrator/requirements/new.md'):
            self.assertEqual(self.event(path)['decision'],'allow',path)
        self.assertEqual(self.event('.orchestrator/index.json')['decision'],'deny')

    def test_mixed_scaffolding_and_business_patch_is_denied(self):
        self.missing_baseline()
        patch='*** Begin Patch\n*** Add File: tests/test_auth.py\n+pass\n*** Add File: main.py\n+business\n*** End Patch'
        rc,out=self.run_cli('host-event','--host','pi',payload={'event':'pre_tool','session_id':'mixed','tool_name':'apply_patch','tool_input':{'patch':patch}})
        self.assertEqual(out['decision'],'deny',out)

    def test_initial_baseline_needs_no_reopen_or_passing_tests(self):
        self.missing_baseline()
        rc,out=self.run_cli('verification','configure','--work',self.wid,'--test-script',self.test_command)
        self.assertEqual(rc,0,out)
        self.assertFalse(out['acceptance_review_required'])
        self.assertTrue(out['initial_baseline_established'])
        self.assertEqual(self.run_cli('bootstrap')[1]['mode'],'resume')
        self.assertEqual(self.event('main.py')['decision'],'allow')
        self.assertFalse(self.read_work(self.wid)['verification'])

    def test_setup_symlink_to_business_is_not_exempt(self):
        self.missing_baseline();(self.repo/'src').mkdir();(self.repo/'tests').symlink_to(self.repo/'src',target_is_directory=True)
        self.assertEqual(self.event('tests/main.py')['decision'],'deny')

    def test_done_spec_and_manual_gate_do_not_replace_review(self):
        self.setup_oneshot()
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *a: self.run_cli(*a), prepare_only=True)
        self.run_cli('gate','satisfy','--gate','implementation_review','--evidence','Self reviewed')
        self.assertEqual(self.run_cli('verify')[1]['error'],'NATIVE_REVIEW_REQUIRED')
        self.assertEqual(self.run_cli('done')[1]['reason'],'NATIVE_REVIEW_REQUIRED')
        out = self.run_cli('next')[1]
        self.assertIn('review submit', out['action']['instruction'])
        self.assertNotIn('review resolve', out['action']['instruction'])

    def test_fresh_independent_report_allows_verify_and_non_git_done(self):
        self.setup_oneshot();before=self.spec.read_bytes()
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args), prepare_only=True)
        self.review()
        self.assertEqual(self.spec.read_bytes(),before)
        self.assertEqual(self.run_cli('verify')[0],0)
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args))
        self.assertEqual(self.run_cli('done')[0],0)

    def test_code_or_evidence_changes_invalidate_review(self):
        self.setup_oneshot();report=self.review()
        (self.repo/'product.py').write_text('value = 2\n')
        self.assertEqual(self.run_cli('verify')[1]['error'],'NATIVE_REVIEW_STALE')
        self.review();report.write_text(report.read_text()+'\n')
        self.assertEqual(self.run_cli('verify')[1]['error'],'NATIVE_REVIEW_STALE')

    def test_changed_native_instructions_invalidates_review(self):
        self.setup_oneshot();self.review()
        (self.repo/'_bmad/render/cao-fixture/step-oneshot.md').write_text('Changed active review policy')
        self.assertEqual(self.run_cli('verify')[1]['error'],'NATIVE_REVIEW_STALE')

    def test_other_active_layer_cannot_be_silently_dropped(self):
        self.setup_oneshot();self.review()
        source=self.repo/'_bmad/render/cao-fixture/step-oneshot.md'
        source.write_text(source.read_text()+'\n#### Other Reviewer (`other-reviewer`)\nIndependent review.\n')
        self.run_cli('sdd','review','begin','--restart','--layer','blind-hunter','--model','fixture-model','--instructions',source.relative_to(self.repo))
        self.assertEqual(self.run_cli('verify')[1]['error'],'NATIVE_REVIEW_REQUIRED')

    def test_model_mismatch_or_stale_ticket_or_empty_triage_rejected(self):
        self.setup_oneshot();report=self.review()
        rc,ticket=self.run_cli('sdd','review','begin','--layer','blind-hunter','--model','fixture-model','--instructions','_bmad/render/cao-fixture/step-oneshot.md')
        original=json.loads(report.read_text())
        for mutation in ('model','empty','run'):
            data=dict(original);data['run_id']=ticket['review']['id'];data['input_sha256']=ticket['review']['input_sha256']
            data['reviewer']=dict(original['reviewer'])
            if mutation=='model':data['reviewer']['model']='different-model'
            elif mutation=='empty':data['findings']=[]
            else:data['run_id']='old-run'
            report.write_text(json.dumps(data))
            self.assertEqual(self.run_cli('sdd','review','submit','--layer','blind-hunter','--report',report.relative_to(self.repo))[0],2)

    @unittest.skipIf(os.name=='nt','Local POSIX git fixture')
    def test_git_commit_required_after_fresh_verify_and_does_not_stale_it(self):
        self.setup_oneshot()
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args), prepare_only=True)
        self.review();self.assertEqual(self.run_cli('verify')[0],0)
        subprocess.run(['git','init','-q'],cwd=self.repo,check=True)
        (self.repo/'.git/info/exclude').write_text('__pycache__/\n*.py[cod]\n')
        self.assertEqual(self.run_cli('done')[1]['reason'],'NATIVE_SPEC_UNTRACKED')
        subprocess.run(['git','add','-A'],cwd=self.repo,check=True)
        subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','feat: fixture'],cwd=self.repo,check=True)
        from tests.helpers import complete_acceptance
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args))
        self.assertEqual(self.run_cli('done')[0],0)


class OwnedSmokeTests(RepairCase):
    def command(self):
        return shlex.join([sys.executable,'-m','http.server'])+' {port} --bind 127.0.0.1'

    def test_fresh_port_and_cleanup(self):
        result=run_smoke(self.repo,self.command(),timeout=5)
        self.assertTrue(result['ok'],result)
        # A managed loopback proxy may retain its TCP listener after the owned
        # process dies; it must not return an HTTP service response.
        connection=http.client.HTTPConnection('127.0.0.1',result['port'],timeout=.2)
        try:
            with self.assertRaises((OSError,http.client.HTTPException)):
                connection.request('GET','/');connection.getresponse()
        finally:connection.close()

    def test_old_service_not_reused_and_bad_new_process_fails(self):
        with socket.socket() as old:
            old.bind(('127.0.0.1',0));old.listen()
            result=run_smoke(self.repo,shlex.join([sys.executable,'-c','raise SystemExit(1)'])+' {port}',timeout=2)
            self.assertFalse(result['ok']);self.assertNotEqual(result['port'],old.getsockname()[1])

    def test_missing_dynamic_port_and_http_mismatch_fail(self):
        with self.assertRaisesRegex(ValueError,'SMOKE_DYNAMIC_PORT'):run_smoke(self.repo,'server --port 8000')
        result=run_smoke(self.repo,self.command(),path='/missing',timeout=5)
        self.assertFalse(result['ok']);self.assertEqual(result['status'],404)
