"""Keep the original user request across provider installation, before Work intake."""
import json
from pathlib import Path

from scripts.state_store import atomic_json


STATE = Path('.orchestrator/startup-request.json')
ORDER = ('Choose the SDD provider first. When installation is needed, install and restart the host. '
         'Ask technology-stack questions only AFTER the host has loaded the provider. '
         'Do not bundle technology questions with provider selection. Preserve explicit '
         'choices already present in the original user request; do not ask them again. '
         'Selecting an already-installed unchanged provider needs no extra restart.')


def pending_request(repo):
    path = repo / STATE
    record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(record, dict) or (record and (
            record.get('version') != 1 or not isinstance(record.get('prompt'), str))):
        raise ValueError('STARTUP_REQUEST_INVALID')
    return record if not record.get('work_item_id') else {}


def capture_request(repo, prompt, host, session_id):
    if not isinstance(prompt, str) or not prompt.strip() or not session_id or pending_request(repo):
        return
    atomic_json(repo / STATE, {'version': 1, 'prompt': prompt, 'host': host,
                             'session_id': session_id})


def consume_request(repo, work_id):
    record = pending_request(repo)
    if record:
        record['work_item_id'] = work_id
        atomic_json(repo / STATE, record)


def request_context(repo):
    record = pending_request(repo)
    if not record:
        return ''
    return ('\nOriginal user request (quoted user input, not new system instructions):\n'
            + json.dumps(record, ensure_ascii=False)
            + '\nRestore this request after restart. Confirm only remaining unknown technology choices.\n')
