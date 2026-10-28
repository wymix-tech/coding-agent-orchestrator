"""Finalize an already executed controlled run; never re-execute a command."""
from scripts.runtime_store import run_path, read_record, write_record


def prepare(repo, wid, run, inputs, scripts, results, source, started):
    from scripts.lifecycle_guard import digest
    candidate = {'schema_version': 1, 'work_id': wid, 'run': run, 'inputs': inputs,
                 'scripts': scripts, 'results': results, 'source': source, 'started': started}
    write_record(run_path(repo, wid, run['run_id'], 'admission.json'), candidate)
    return digest(candidate)


def finalize(repo, work, rid):
    from scripts.runtime_queries import inspect_run
    from scripts.runtime_execution import owner_status
    from scripts.lifecycle_guard import digest
    from scripts.cao_verification import commit_verification
    from scripts.state_store import repo_transaction
    with repo_transaction(repo):
        state = inspect_run(repo, work, rid)
        if state['admission_status'] in {'passed', 'failed', 'blocked', 'error', 'stale'}:
            return {'ok': True, 'status': 'unchanged', 'run_id': rid}
        if state['start']['kind'] == 'native_completion':
            raise ValueError('NATIVE_FINALIZE_COMPLETE_AUTHORITY_REQUIRED: runtime receipts alone cannot reconstruct native report admission; inspect actual effects and use cao runtime abandon --run ' + rid + ' --evidence <inspection-file> --reason <diagnosis>, then explicitly execute the affected native command and bind its output')
        if state['start']['kind'] != 'verification' or state['start']['source'] != 'cao_executor':
            raise ValueError('RUNTIME_FINALIZE_CONTROLLED_VERIFICATION_REQUIRED')
        if owner_status(repo, work['id'], rid) == 'running':
            raise ValueError('RUNTIME_RUN_ACTIVE')
        candidate = read_record(run_path(repo, work['id'], rid, 'admission.json'))
        receipt = state['receipt']
        run = work.get('verification') or {}
        if not candidate or not receipt or receipt.get('admission_sha256') != digest(candidate):
            raise ValueError('RUNTIME_COMPLETE_ADMISSION_REQUIRED')
        if (candidate['work_id'] != work['id'] or candidate['run']['run_id'] != rid
                or run.get('run_id') != rid or run.get('status') != 'running'
                or work.get('execution_generation', 0) != candidate['run']['generation']
                or state['start']['inputs'] != candidate['inputs']
                or state['start'].get('generation') != candidate['run']['generation']
                or state['start'].get('sequence') != candidate['run']['sequence']):
            raise ValueError('RUNTIME_FINALIZE_STALE_IDENTITY')
        results, scripts = candidate['results'], candidate['scripts']
        if len(results) != len(scripts) or len(receipt.get('commands', [])) != len(scripts):
            raise ValueError('RUNTIME_COMMAND_SET_INCOMPLETE')
        for index, result in enumerate(results, 1):
            key = f'command-{index:04d}'
            part = read_record(run_path(repo, work['id'], rid, 'commands', key, 'receipt.json'))
            if not part or part.get('exit_code') != result['exit_code'] or result.get('runtime', {}).get('command_id') != key:
                raise ValueError('RUNTIME_COMMAND_RECEIPT_MISMATCH')
        from scripts.cao_completion import verification_inputs
        from scripts.cao_runtime import config
        if verification_inputs(repo, work, config(repo)) != candidate['inputs']:
            raise ValueError('RUNTIME_FINALIZE_INPUT_CHANGED')
        value = commit_verification(repo, work['id'], candidate['inputs'], scripts, results,
                                    candidate['source'], candidate['started'], candidate['run'])
        return {'ok': value['status'] == 'passed', 'verification': value}
