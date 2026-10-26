"""Bind review tickets to host-observed task prompts and original output bytes.

This is provenance, not semantic certification. Never infer roles from ordering.
"""
import hashlib
import json
import re

from scripts.cao_sessions import save_session

PREFIX = 'CAO_REVIEW_TICKET '


def ticket(run, layer):
    return {'run_id': run['id'], 'layer': layer, 'input_sha256': run['input_sha256']}


def task_marker(run, layer):
    return PREFIX + json.dumps(ticket(run, layer), sort_keys=True, separators=(',', ':'))


def observe(repo, work, session, raw, event):
    if not work or not session:
        return
    if event in {'prompt_submit', 'subagent_start'}:
        prompt = raw.get('prompt')
        if not isinstance(prompt, str) or len(prompt.encode()) > 128 * 1024:
            return
        markers = re.findall(r'(?m)^(?:Task:[ \t]*)?' + PREFIX + r'(\{[^\n]+\})\s*$', prompt)
        if len(markers) != 1:
            return
        try:
            value = json.loads(markers[0])
        except ValueError:
            return
        if not isinstance(value, dict) or not isinstance(value.get('layer'), str):
            return
        reviews = (work.get('sdd') or {}).get('native_reviews') or {}
        run = reviews.get(value.get('layer')) or {}
        if not run or value != ticket(run, value.get('layer')):
            return
        if session['id'] in run.get('existing_session_ids', []):
            return
        previous = session.get('review_ticket')
        if previous and previous != value:
            return  # An existing task cannot be relabeled by a later prompt.
        session['review_ticket'] = value
        save_session(repo, session)
    if event in {'turn_end', 'stop', 'subagent_stop'} and session.get('review_ticket'):
        message = raw.get('last_assistant_message')
        if isinstance(message, str) and message.strip() and len(message.encode()) <= 1024 * 1024:
            session['review_output_sha256'] = hashlib.sha256(message.strip().encode()).hexdigest()
            save_session(repo, session)


def validate_source(repo, run, source):
    if run.get('task_binding_version') != 1:
        return  # Existing historical evidence is not retroactively recertified.
    from scripts.cao_sessions import load_session
    binding = run.get('execution_binding') or {}
    session = load_session(repo, binding.get('session_id'), host=binding.get('host')) or {}
    expected = session.get('review_output_sha256')
    if not expected:
        raise ValueError('NATIVE_REVIEW_OUTPUT_UNOBSERVED: collect the bound reviewer final output through its host adapter')
    actual = hashlib.sha256(source.read_text(encoding='utf-8-sig').strip().encode()).hexdigest()
    if expected != actual:
        raise ValueError('NATIVE_REVIEW_OUTPUT_SOURCE_MISMATCH: preserve the bound reviewer original output unchanged')
