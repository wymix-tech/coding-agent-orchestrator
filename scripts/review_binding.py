"""Require a real independent host session to acknowledge a review's frozen input."""
from scripts.native_obligations import digest


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
    if (not identity or not session or session.get('status') != 'active'
            or session.get('work_item_id') != inputs['work_id']
            or session.get('id') in run.get('existing_session_ids', [])
            or identity[1] in run.get('primary_session_ids', [])
            or model != run.get('model')):
        raise ValueError('NATIVE_REVIEW_INDEPENDENT_SESSION_REQUIRED')
    reviewer = {'mode': 'subagent' if session.get('kind') == 'subagent' else 'external-session',
                'session_id': identity[1], 'model': model}
    previous = run.get('execution_binding')
    if previous and previous['reviewer'] != reviewer:
        raise ValueError('NATIVE_REVIEW_ALREADY_ATTACHED')
    run['execution_binding'] = {'reviewer': reviewer, 'input_sha256': input_sha256,
                                'session_id': session['id'], 'host': identity[0]}
    return run
