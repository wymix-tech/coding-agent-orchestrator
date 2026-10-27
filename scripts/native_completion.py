"""Native terminal state is a closure obligation, not a second code review."""
import re

from scripts.bmad_project import output_dirs as bmad_output_dirs
from scripts.path_guard import provider_artifact


def legacy_state(repo, work):
    sdd = work.get('sdd') or {}
    decision = sdd.get('route_decision') or {}
    if sdd.get('provider') != 'bmad' or decision.get('build_route') != 'dispatch':
        return {'ready': True, 'state': 'not_applicable'}
    try:
        path = provider_artifact(repo, decision['primary_artifact'], bmad_output_dirs(repo))
        text = path.read_text(encoding='utf-8-sig')
        header = re.match(r'\A---\s*\n(.*?)\n---(?:\n|$)', text, re.S)
        status = re.search(r"(?m)^status:\s*['\"]?([a-z-]+)", header[1]) if header else None
        if status and status[1] == 'done':
            return {'ready': True, 'state': 'completed', 'source': decision['primary_artifact']}
        return {'ready': False, 'state': 'pending', 'reason': 'NATIVE_FINALIZE_REQUIRED',
                'source': decision['primary_artifact'], 'actual': status[1] if status else None,
                'next': 'Complete the current installed BMAD Finalize step and set the native SPEC done through that workflow. Then refresh any affected verification/acceptance evidence and retry cao done; CAO does not mark the SPEC for you.'}
    except (ValueError, OSError, KeyError) as exc:
        return {'ready': False, 'state': 'blocked', 'reason': 'NATIVE_FINALIZE_SOURCE_INVALID',
                'next': 'Restore the bound native source and follow its actual Finalize instructions.', 'error': str(exc)}


def state(repo, work):
    if work.get('lifecycle_contract') != 2:
        return legacy_state(repo, work)
    from scripts.cao_native_sync import native_sync_state
    from scripts.cao_plans import provider_verification_plan_state
    from scripts.completion_policy import workflow_ready
    provider = (work.get('sdd') or {}).get('provider')
    if provider not in {'bmad', 'openspec', 'speckit'}:
        return {'ready': False, 'state': 'blocked', 'reason': 'NATIVE_COMPLETION_PROVIDER_UNSUPPORTED'}
    plan = provider_verification_plan_state(repo, work)
    if not plan['ready']:
        return {**plan, 'ready': False, 'owner': provider}
    observation = native_sync_state(repo, work)
    if observation.get('canonical') in {'ready', 'in_progress', 'in_review', 'blocked', 'failed', 'cancelled'}:
        return {'ready': False, 'state': 'pending', 'owner': provider,
                'reason': 'NATIVE_FINALIZE_REQUIRED', 'observed': observation,
                'next': 'Finish every associated native unit, including review/acceptance and required close steps; native in-review is still implementation.'}
    if provider == 'bmad':
        decision = (work.get('sdd') or {}).get('route_decision') or {}
        if decision.get('build_route') == 'dispatch':
            terminal = legacy_state(repo, work)
            if not terminal['ready']:
                return terminal
    if (work.get('path_assessment') or {}).get('profile') != 'light':
        ready, reason = workflow_ready(work, repo)
        if not ready:
            return {'ready': False, 'state': 'pending', 'reason': reason,
                    'next': 'Finish and record the actual native workflow and all outputs using cao sdd workflow complete.'}
    return {'ready': True, 'state': 'completed', 'owner': provider, 'observed': observation,
            'basis': 'associated_native_scope_and_workflow',
            'note': 'Conformance still requires a recognized native completion/test report and native review provenance.'}
