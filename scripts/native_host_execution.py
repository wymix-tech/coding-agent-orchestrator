"""Purpose-built Pi bash receipts; generic runtime observations remain diagnostic."""
import hashlib
import time
from scripts.runtime_host import call_key


def observe(repo, work, session, raw, host, event, decision):
    from scripts.cao_work_store import save_work, queue_event
    from scripts.native_execution import validate_command, material_inputs
    from scripts.lifecycle_contract import inputs
    from scripts.path_guard import provider_artifact
    from scripts.cao_provider_workflows import provider_output_dirs
    if (not work or work.get('status') != 'active' or work.get('lifecycle_contract') != 2 or host != 'pi'
            or not session or session.get('review_ticket') or event not in {'pre_tool', 'post_tool'}):
        return
    tool = raw.get('tool_name') or raw.get('toolName') or raw.get('name')
    if tool != 'bash' or decision == 'deny':
        return
    key = call_key(raw, host, session['host_session_id'])
    if not key:
        return
    payload = raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {}
    command = payload.get('command') if isinstance(payload, dict) else None
    rows = work['sdd'].get('native_host_executions', [])
    row = next((r for r in rows if r['run_id'] == key), None)
    if event == 'pre_tool':
        if row or not isinstance(command, str):
            return
        try:
            validate_command(work, command, repo)
        except ValueError:
            return
        directory = provider_output_dirs(work['sdd']['provider'], repo)[0]
        output = directory.rstrip('/') + '/cao-native-execution/' + key + '.log'
        path = provider_artifact(repo, output, provider_output_dirs(work['sdd']['provider'], repo))
        reports = work['sdd'].setdefault('native_execution_reports', [])
        if output not in reports:
            reports.append(output)
        row = {'run_id': key, 'source': 'pi_bash_structured_result_v1', 'status': 'running',
               'host': host, 'session_id': session['host_session_id'], 'command': command,
               'generation': work.get('execution_generation', 0), 'at': time.time(),
               'inputs': inputs(repo, work), 'domains': material_inputs(repo, work), 'output': output}
        work['sdd'].setdefault('native_host_executions', []).append(row)
        save_work(repo, work)
        return
    if not row or row['status'] != 'running':
        return
    result = raw.get('structuredContent') or raw.get('structured_content')
    if not isinstance(result, dict) or type(result.get('exit_code')) is not int or not isinstance(result.get('output'), str) or result.get('truncated') is not False:
        row.update(status='unavailable', reason='NATIVE_HOST_RESULT_INCOMPLETE: complete structured output and exit_code are required; use lifecycle execute if unavailable')
    elif (command != row['command'] or work.get('execution_generation', 0) != row['generation']
          or inputs(repo, work) != row['inputs'] or material_inputs(repo, work) != row['domains']):
        row.update(status='stale', reason='NATIVE_EXECUTION_INPUT_CHANGED')
    elif len(result['output'].encode()) > 1024 * 1024:
        row.update(status='unavailable', reason='NATIVE_HOST_RESULT_TOO_LARGE')
    else:
        path = provider_artifact(repo, row['output'], provider_output_dirs(work['sdd']['provider'], repo))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            row.update(status='unavailable', reason='NATIVE_HOST_OUTPUT_ALREADY_EXISTS')
        else:
            path.write_text(result['output'], encoding='utf-8')
            path.chmod(0o600)
            row.update(exit_code=result['exit_code'], status='failed')
            row['reports'] = [{'path': row['output'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
            if result['exit_code'] == 0 and not raw.get('isError', raw.get('is_error', False)):
                try:
                    from scripts.native_reports import read
                    row['observation'] = read(repo, work, path, execution=row)
                    row['status'] = 'passed'
                except (ValueError, OSError) as exc:
                    row['reason'] = str(exc)
    queue_event(work, {'event': 'sdd.native_host.executed', 'work_item_id': work['id'],
                       'run_id': row['run_id'], 'status': row['status']})
    save_work(repo, work)
