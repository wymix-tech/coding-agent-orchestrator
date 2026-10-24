"""Require a real independent host session to acknowledge a review's frozen input."""
from scripts.native_obligations import digest
from scripts.review_diagnostics import invalid


def runtime_model_name(model, runtime_model=None, provider=None):
    """Normalize only the current provider's qualified spelling, never other models."""
    model = str(model or '').strip()
    runtime_model = str(runtime_model or '').strip()
    provider = str(provider or '').strip()
    if runtime_model and provider and model == provider + '/' + runtime_model:
        return runtime_model
    return model


def attach_reviewer(run, inputs, identity, session, model, run_id, input_sha256):
    if not isinstance(run.get('existing_session_ids'), list):
        raise ValueError('NATIVE_REVIEW_RESTART_REQUIRED')
    if (run.get('status') != 'running' or run.get('id') != run_id
            or run.get('input_sha256') != input_sha256 or digest(inputs) != input_sha256
            or run.get('inputs') != inputs):
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    legacy = 'NATIVE_REVIEW_INDEPENDENT_SESSION_REQUIRED'
    if not identity or not session:
        invalid('NATIVE_REVIEW_SESSION_UNOBSERVED', 'session', 'a host-observed independent session with its CAO adapter', legacy=legacy)
    if session.get('status') != 'active':
        invalid('NATIVE_REVIEW_SESSION_INACTIVE', 'session.status', 'active', session.get('status'), legacy=legacy)
    if session.get('work_item_id') != inputs['work_id']:
        invalid('NATIVE_REVIEW_WORK_MISMATCH', 'session.work_item_id', inputs['work_id'], session.get('work_item_id'), legacy=legacy)
    if identity[1] in run.get('primary_session_ids', []):
        invalid('NATIVE_REVIEW_PRIMARY_SESSION', 'session.id', 'a new independent reviewer, not the coordinator', identity[1], legacy=legacy)
    if session.get('id') in run.get('existing_session_ids', []):
        invalid('NATIVE_REVIEW_SESSION_PREDATES_TICKET', 'session.id', 'launch a NEW session AFTER coordinator creates tickets; do not restart tickets in the reviewer', session.get('id'), legacy=legacy)
    if model != run.get('model'):
        invalid('NATIVE_REVIEW_MODEL_MISMATCH', 'reviewer.model', run.get('model'), model, legacy=legacy)
    reviewer = {'mode': 'subagent' if session.get('kind') == 'subagent' else 'external-session',
                'session_id': identity[1], 'model': model}
    previous = run.get('execution_binding')
    if previous and previous['reviewer'] != reviewer:
        raise ValueError('NATIVE_REVIEW_ALREADY_ATTACHED')
    run['execution_binding'] = {'reviewer': reviewer, 'input_sha256': input_sha256,
                                'session_id': session['id'], 'host': identity[0]}
    return run


def delegated_reviewer(repo, run, inputs, coordinator_identity, child_ref, model, run_id, input_sha256):
    """A coordinator may bind an observed child; it never becomes the reviewer."""
    from scripts.cao_sessions import load_session
    parent = load_session(repo, coordinator_identity[1], host=coordinator_identity[0]) if coordinator_identity else None
    if not parent or parent.get('kind') != 'primary' or parent.get('status') != 'active':
        invalid('NATIVE_REVIEW_COORDINATOR_REQUIRED', 'session', 'active observed primary coordinator')
    child = load_session(repo, child_ref, host=coordinator_identity[0])
    if not child or child.get('kind') != 'subagent' or child.get('parent_session_id') not in {parent['id'], parent['host_session_id']}:
        invalid('NATIVE_REVIEW_CHILD_REQUIRED', 'reviewer_session', 'a host-observed child of this coordinator', child_ref)
    if 'read' not in (child.get('observed_tools') or []):
        invalid('NATIVE_REVIEW_TOOLS_UNOBSERVED', 'reviewer.tools', 'observed read capability', child.get('observed_tools'))
    if child.get('observed_model') != model:
        invalid('NATIVE_REVIEW_MODEL_UNOBSERVED', 'reviewer.model', model, child.get('observed_model'))
    identity = (child['host'], child['host_session_id'])
    attached = attach_reviewer(run, inputs, identity, child, model, run_id, input_sha256)
    attached['execution_binding']['delegated_by'] = parent['host_session_id']
    return attached, child
