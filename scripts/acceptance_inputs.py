"""V1 project-bound technical inputs and separately bound task requirements."""
import os
from scripts.project_context import binding_state, digest, load_context, fingerprint

TECHNICAL_KEYS = ('snapshot_format', 'technical_input_version', 'code_snapshot', 'rules_snapshot',
                  'verification_config_sha256', 'project_context_sha256', 'technical_policy_sha256',
                  'environment_sha256', 'maximum_evidence_age_seconds')


def technical_inputs(repo, work, cfg, *, manifest=None):
    from scripts.cao_completion import sdd_acceptance_outputs
    from scripts.cao_snapshot import SNAPSHOT_FORMAT, code_snapshot, rules_snapshot, verification_snapshot
    state = binding_state(repo, work)
    if not state['ready']:
        raise ValueError(state['reason'] + ': ' + state['next'])
    declaration = ((work.get('sdd') or {}).get('acceptance') or {}).get('declaration') or {}
    excluded = {work['requirement']['path'], *declaration.get('sources', [])}
    binding = (work.get('sdd') or {}).get('spec_binding') or {}
    for key in ('spec', 'criteria'):
        if binding.get(key):
            excluded.add(binding[key]['path'])
    excluded |= {path for path in ((work.get('sdd') or {}).get('workflow') or {}).get('artifacts', []) if path.endswith(('.md', '.yaml', '.yml'))}
    excluded |= sdd_acceptance_outputs(repo, work)
    # Original completion reports are a separate evidence domain, never product code.
    # Only provider-owned, non-executable report types may be excluded.
    if work.get('lifecycle_contract') == 2:
        from scripts.cao_provider_workflows import provider_output_dirs
        from scripts.path_guard import provider_artifact
        from pathlib import Path
        from scripts.implementation_evidence import records as process_records
        report_refs = list(((work.get('sdd') or {}).get('completion_evidence') or {}).get('reports', []))
        report_refs.extend({'path': row['path']} for row in process_records(repo, work) if row.get('path') and Path(row['path']).suffix.lower() in {'.md', '.json', '.yaml', '.yml', '.txt', '.log'})
        for ref in report_refs:
            name = ref['path']
            provider_artifact(repo, name, provider_output_dirs(work['sdd']['provider'], repo))
            if Path(name).suffix.lower() not in {'.md', '.json', '.yaml', '.yml', '.txt', '.log'}:
                raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
            excluded.add(name)
    from scripts.review_scope import acceptance_review_scope
    declaration_files, _ = acceptance_review_scope(repo, work)
    declaration_file = ((work.get('sdd') or {}).get('acceptance') or {}).get('declaration_file')
    if declaration_file in declaration_files:
        excluded.add(declaration_file)
    # Only exact declarative native sources are excluded; executable directories remain inputs.
    from pathlib import Path
    from scripts.snapshot_scope import SOURCE_SUFFIXES
    code_suffixes = SOURCE_SUFFIXES - {'.md', '.yaml', '.yml'}
    excluded = {path for path in excluded if Path(path).suffix.lower() not in code_suffixes}
    gates = set((work.get('path_assessment') or {}).get('required_gates', []))
    technical_gates = sorted(gates & {'tests', 'security_verification', 'verification', 'targeted_verification',
                                     'integration_verification', 'compatibility_verification', 'data_verification'})
    verification = cfg.get('verification') or {}
    environment = verification.get('environment_variables') or []
    if not isinstance(environment, list) or any(not isinstance(key, str) for key in environment):
        raise ValueError('VERIFICATION_ENVIRONMENT_INVALID')
    maximum = verification.get('maximum_evidence_age_seconds')
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or maximum <= 0):
        raise ValueError('VERIFICATION_EVIDENCE_AGE_INVALID')
    return {'environment_sha256': digest({key: os.environ.get(key) for key in environment}),
            'maximum_evidence_age_seconds': maximum, 'snapshot_format': SNAPSHOT_FORMAT, 'technical_input_version': 1,
            'code_snapshot': code_snapshot(repo, excluded, manifest=manifest), 'rules_snapshot': rules_snapshot(repo, cfg),
            'verification_config_sha256': verification_snapshot(cfg, repo),
            'project_context_sha256': state['fingerprint'],
            'technical_policy_sha256': digest({'gates': technical_gates,
                                               'scopes': state['scopes'],
                                               'architecture': state.get('architecture', {}).get('rules', [])})}


def task_inputs(repo, work, cfg):
    from scripts.cao_completion import verification_inputs
    from scripts.cao_plans import provider_verification_plan_state
    from scripts.cao_runtime import sha256_file
    sdd = work.get('sdd') or {}
    native_scope = provider_verification_plan_state(repo, work)
    if (sdd.get('acceptance') or {}).get('declaration', {}).get('lifecycle_metadata') == 'bmad-dispatch-status-v1':
        from copy import deepcopy
        native_scope = deepcopy(native_scope)
        for observation in native_scope.get('observations', {}).values():
            observation.pop('sha256', None)  # Source semantics are bound separately.
    return {**verification_inputs(repo, work, cfg), 'task_input_version': 1,
            'requirement_sha256': sha256_file(repo / work['requirement']['path']),
            'assessment_revision': (work.get('path_assessment') or {}).get('revision'),
            'native_scope': native_scope,
            'spec_binding': sdd.get('spec_binding'), 'route': sdd.get('route_decision'),
            'spec_approval': sdd.get('spec_approval')}
