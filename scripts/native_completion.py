"""Native terminal state is a closure obligation, not a second code review."""
import re

from scripts.path_guard import provider_artifact


def state(repo, work):
    sdd = work.get('sdd') or {}
    decision = sdd.get('route_decision') or {}
    if sdd.get('provider') != 'bmad' or decision.get('build_route') != 'dispatch':
        return {'ready': True, 'state': 'not_applicable'}
    try:
        path = provider_artifact(repo, decision['primary_artifact'], ['_bmad-output'])
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
