"""V1 project-bound technical inputs and separately bound task requirements."""
import os
from scripts.project_context import binding_state, digest, load_context, fingerprint

TECHNICAL_KEYS = ('snapshot_format', 'technical_input_version', 'code_snapshot', 'rules_snapshot',
                  'verification_config_sha256', 'project_context_sha256', 'technical_policy_sha256',
                  'environment_sha256', 'maximum_evidence_age_seconds')


def technical_inputs(repo, work, cfg):
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
    # Only exact declarative native sources are excluded; executable directories remain inputs.
    excluded = {path for path in excluded if not path.endswith(('.py', '.java', '.js', '.ts', '.sh', '.go', '.rs'))}
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
            'code_snapshot': code_snapshot(repo, excluded), 'rules_snapshot': rules_snapshot(repo, cfg),
            'verification_config_sha256': verification_snapshot(cfg, repo),
            'project_context_sha256': state['fingerprint'],
            'technical_policy_sha256': digest({'gates': technical_gates,
                                               'scopes': state['scopes']})}


def task_inputs(repo, work, cfg):
    from scripts.cao_completion import verification_inputs
    from scripts.cao_plans import provider_verification_plan_state
    from scripts.cao_runtime import sha256_file
    sdd = work.get('sdd') or {}
    return {**verification_inputs(repo, work, cfg), 'task_input_version': 1,
            'requirement_sha256': sha256_file(repo / work['requirement']['path']),
            'assessment_revision': (work.get('path_assessment') or {}).get('revision'),
            'native_scope': provider_verification_plan_state(repo, work),
            'spec_binding': sdd.get('spec_binding'), 'route': sdd.get('route_decision'),
            'spec_approval': sdd.get('spec_approval')}
