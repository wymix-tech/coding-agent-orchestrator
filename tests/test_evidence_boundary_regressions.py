"""Counterexamples from the a5d6086 review, exercised through real consumers."""
import json
import pathlib
import sys
import tempfile
import unittest
import subprocess
import shlex

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import evidence_provenance as ep
import evidence_factory as ef
import execution_state_manager as sm


class EvidenceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name)
        ef.write_source(self.repo)
        ef.write_requirement(self.repo)

    def claim(self, report, claim_type='verification', revision='R'):
        return ep.build_record(kind='mechanical_observation', claim_type=claim_type,
            work_item_id='W', requirement_revision=revision, outcome='passed', report=report,
            depends_on=ef.required_dependencies(self.repo, kind='mechanical_observation',
                                                claim_type=claim_type))

    def test_unit_receipt_cannot_be_relabelled_verification(self):
        report = ef.write_report(self.repo, target='gate:unit', work_item_id='W', requirement_revision='R')
        result = ep.revalidate(self.repo, self.claim(report), work_item_id='W', requirement_revision='R')
        self.assertNotEqual('verified', result['validation_status'])

    def test_refreshing_dependencies_cannot_refresh_old_execution(self):
        report = ef.write_report(self.repo, target='verification', work_item_id='W', requirement_revision='R')
        ef.write_source(self.repo, content='raise RuntimeError("changed")\n')
        result = ep.revalidate(self.repo, self.claim(report), work_item_id='W', requirement_revision='R')
        self.assertNotEqual('verified', result['validation_status'])

    def test_unsigned_channel_is_not_an_approval(self):
        ef.publish_evidence_policy(self.repo)
        ref = self.repo / 'caller.json'
        ref.write_text(json.dumps({'event':'approval','approver':'reviewer-1','decision':'approved'}))
        result = ep.verify_approval_channel(self.repo, {'approver':'reviewer-1','subject':'W',
            'requirement_revision':'R','decision':'approved',
            'channel':{'type':'host_approval_event','ref':str(ref)}})
        self.assertFalse(result['verified'])

    def test_failed_report_does_not_authenticate_an_agent_claim(self):
        record = ep.build_record(kind='agent_claim', claim_type='readiness',
            work_item_id='W', outcome='passed', report={'path':'invented.json'},
            depends_on=[ef.requirement_dependency(self.repo)])
        result = ep.verify(record, inputs={'report':{'available':True,'status':'failed',
                                                   'exit_code':1}})
        self.assertEqual('unverified', result['validation_status'])
        self.assertEqual('EVIDENCE_UNVERIFIED', result['reason_code'])

    def test_constant_command_is_not_a_semantic_observer(self):
        result = ep.run_execution(self.repo, [sys.executable, '-c', 'print(\'{"value":false}\')'],
            target='fact:risk.security_sensitive', fact_path='risk.security_sensitive',
            work_item_id='W', requirement_revision='R')
        if result.get('available'):
            report = {'path':result['path']}
            verified = ep.revalidate(self.repo, self.claim(report, 'fact_observation'),
                                     work_item_id='W', requirement_revision='R')
            self.assertFalse(ep.fact_observation(verified, 'risk.security_sensitive', False)['observed'])

    def test_report_wrapper_cannot_change_progress(self):
        sp = self.repo / '.orchestrator/execution-state.yaml'
        sm.initialize(sp, sm.create_state('W','Example','STANDARD',requirement_revision='R'), 'test')
        rec = ef.progress_record(self.repo, completed=1,total=2,work_item_id='W',requirement_revision='R')
        path = self.repo / rec['report']['path']
        doc = json.loads(path.read_text())
        doc['tests'] = {'completed':999,'total':999}
        path.write_text(json.dumps(doc))
        rec['report']['digest'] = ep.path_revision(self.repo, rec['report']['path'])
        rec['extra'] = {'completed':999,'total':999}
        with self.assertRaises(sm.StateError):
            sm.set_progress(sp,999,999,'test','claim',evidence_record=rec)

    def test_real_progress_and_signed_approval_are_consumable(self):
        sp = self.repo / '.orchestrator/execution-state.yaml'
        sm.initialize(sp, sm.create_state('W','Example','STANDARD',requirement_revision='R'), 'test')
        ef.establish_progress(sp,1,2,repo=self.repo)
        self.assertEqual({'completed':1,'total':2}, sm._load(sp)['work_item']['progress'])
        ef.establish_readiness(sp,'sdd_ready',repo=self.repo)
        self.assertTrue(sm._load(sp)['readiness']['sdd_ready'])

    def test_receipt_file_and_id_both_reject_other_gate(self):
        report = ef.write_report(self.repo, target='gate:unit', work_item_id='W', requirement_revision='R')
        sp = self.repo / '.orchestrator/execution-state.yaml'
        sm.initialize(sp, sm.create_state('W','Example','STANDARD',requirement_revision='R'), 'test')
        rec = self.claim(report, 'gate_result')
        eid = ep.persist_raw_record(self.repo, rec)['evidence_id']
        for ref in (report['path'], eid):
            with self.subTest(ref=ref):
                with self.assertRaises(sm.StateError):
                    sm.record_gate(sp,'integration',True,'passed','test',evidence_ref=ref)
                sm.record_gate(sp,'unit',True,'passed','test',evidence_ref=ref)

    def test_code_changed_during_execution_is_not_coverage_of_final_code(self):
        run = ep.run_execution(self.repo, [sys.executable,'-c',
            'from pathlib import Path;Path("src/app.py").write_text("broken = True")'],
            target='verification',work_item_id='W',requirement_revision='R')
        result = ep.result_document_binding(self.repo,run['path'],target='verification',
                                           work_item_id='W',requirement_revision='R')
        self.assertEqual('EVIDENCE_EXECUTION_CODE_MISMATCH',result['error'])

    def test_published_observer_succeeds_and_input_drift_invalidates(self):
        from trusted_evidence_fixture import observer
        path = 'risk.security_sensitive'
        argv = observer(self.repo,path,False,[ef.DEFAULT_REQUIREMENT])
        run = ep.run_execution(self.repo,argv,target='fact:'+path, fact_path=path,
                               work_item_id='W',requirement_revision='R')
        self.assertTrue(run['available'])
        rec = self.claim({'path':run['path']},'fact_observation')
        result = ep.revalidate(self.repo,rec,work_item_id='W',requirement_revision='R')
        self.assertTrue(ep.fact_observation(result,path,False)['observed'])
        rule = ep.load_policy(self.repo)['evidence']['observers'][path]
        (self.repo / rule['inputs'][-1]).write_text('true')
        result = ep.revalidate(self.repo,rec,work_item_id='W',requirement_revision='R')
        self.assertFalse(ep.fact_observation(result,path,False)['observed'])

    def test_signed_event_cannot_be_retargeted_or_change_its_value(self):
        from trusted_evidence_fixture import sign_event
        event = sign_event(self.repo, {'id':'evt-1','event':'approval','approver':'reviewer-1',
            'subject':'W','work_item_id':'W','requirement_revision':'R','decision':'approved',
            'channel':'host_approval_event','fact_path':'risk.security_sensitive','value':True})
        ref = self.repo / 'signed.json'
        ref.write_text(json.dumps(event))
        entry = {**event,'channel':{'type':'host_approval_event','ref':str(ref)}}
        self.assertTrue(ep.verify_approval_channel(self.repo,entry)['verified'])
        for patch in ({'subject':'OTHER'}, {'requirement_revision':'NEXT'}, {'value':False}):
            self.assertFalse(ep.verify_approval_channel(self.repo,{**entry,**patch})['verified'])
        event['value'] = False
        ref.write_text(json.dumps(event))
        self.assertFalse(ep.verify_approval_channel(self.repo,{**entry,'value':False})['verified'])

    def test_removed_issuer_invalidates_existing_signed_approval(self):
        from trusted_evidence_fixture import policy
        ef.publish_evidence_policy(self.repo)
        source = ef.write_approval(self.repo,subject='W',requirement_revision='R')
        rec = ep.build_record(kind='human_approval',claim_type='readiness',work_item_id='W',
            requirement_revision='R',outcome='approved',producer='agent',
            depends_on=[ef.requirement_dependency(self.repo)],
            approval={'source':source,'subject':'W','revision':'appr-1'})
        self.assertEqual('verified',ep.revalidate(self.repo,rec)['validation_status'])
        policy(self.repo,lambda evidence:evidence.update({'approval_keys':{}}))
        self.assertEqual('unverified',ep.revalidate(self.repo,rec)['validation_status'])

    def test_general_approval_cannot_authenticate_an_attached_stale_observation(self):
        ef.publish_evidence_policy(self.repo)
        report = ef.write_report(self.repo, target='fact:risk.security_sensitive',
            fact_path='risk.security_sensitive', value=False, work_item_id='W', requirement_revision='R')
        ef.write_source(self.repo, content='security_sensitive = True\n')
        source = ef.write_approval(self.repo, subject='W', requirement_revision='R')
        rec = ep.build_record(kind='human_approval', claim_type='readiness', work_item_id='W',
            requirement_revision='R', outcome='approved', producer='agent', report=report,
            depends_on=[ef.requirement_dependency(self.repo)],
            approval={'source':source, 'subject':'W', 'revision':'appr-1'})
        result = ep.revalidate(self.repo, rec, work_item_id='W', requirement_revision='R')
        self.assertEqual('verified', result['validation_status'])
        self.assertFalse(ep.fact_observation(result, 'risk.security_sensitive', False)['observed'])

    def test_cli_uses_current_requirement_and_preserves_command_separator(self):
        sp = self.repo / '.orchestrator/execution-state.yaml'
        sm.initialize(sp,sm.create_state('W','Example','STANDARD',requirement_revision='R'),'test')
        cmd = [sys.executable,str(ROOT/'scripts/coding_orchestrator.py'),'--repo',str(self.repo),
               '--json','evidence','run','--target','gate:unit','--work-item','W','--',
               sys.executable,'-c','import sys;assert sys.argv[1:] == ["--", "tail"]','--','tail']
        run = subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        self.assertEqual(0,run.returncode,run.stderr)
        payload = json.loads(run.stdout)
        self.assertEqual('R',payload['requirement_revision'])
        sm.record_gate(sp,'unit',True,'passed','test',evidence_ref=payload['path'])

    def test_missing_scope_is_rejected_before_running_command(self):
        run = ep.run_execution(self.repo,[sys.executable,'-c',
            'from pathlib import Path;Path("side-effect").touch()'],target='gate:unit',work_item_id='W')
        self.assertFalse(run['available'])
        self.assertFalse((self.repo/'side-effect').exists())

    def test_source_confirmation_keeps_source_identity_when_replayed(self):
        import project_bootstrap as pb
        import requirement_identity as ri
        subprocess.run(['git','init','-q'],cwd=self.repo,check=True)
        pb.initialize(self.repo,host='none',sdd='generic')
        source = self.repo / ef.DEFAULT_REQUIREMENT
        source.write_text('# Story\n\n## Requirement\nReturn 1\n\n## Tasks\n- [ ] implement\n')
        old = ri.requirement_content_revision(self.repo,str(source))['source_revision']
        sp = self.repo / '.orchestrator/execution-state.yaml'
        sm.initialize(sp,sm.create_state('W','Example','TRIVIAL',requirement_id='REQ',
            requirement_revision=old,requirement_source_ref=ef.DEFAULT_REQUIREMENT),'test')
        ri.record(self.repo,requirement_id='REQ',revision_id=old,work_item_id='W',provider='generic',
                  source_path=ef.DEFAULT_REQUIREMENT,status='active',source_revision=old)
        ef.establish_progress(sp,1,2,repo=self.repo)
        source.write_text(source.read_text().replace('Return 1','Return 2'))
        cmd = [sys.executable,str(ROOT/'scripts/coding_orchestrator.py'),'--repo',str(self.repo),
               'intake','update request','--sdd','generic','--sdd-ref',str(source),
               '--requirement-id','REQ','--revise-current']
        first = subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        self.assertEqual(2,first.returncode,first.stderr)
        prefix = 'python3 '+str(ROOT/'scripts/coding_orchestrator.py')
        command = first.stdout[first.stdout.rfind(prefix):].strip()
        self.assertIn('--sdd-ref',command)
        subprocess.run(shlex.split(command),capture_output=True,text=True,timeout=30,cwd=ROOT)
        after = sm._load(sp)
        self.assertEqual(ri.requirement_content_revision(self.repo,str(source))['source_revision'],
                         after['work_item']['requirement_revision'])
        self.assertEqual({'completed':0,'total':0},after['work_item']['progress'])

if __name__ == '__main__':
    unittest.main()
