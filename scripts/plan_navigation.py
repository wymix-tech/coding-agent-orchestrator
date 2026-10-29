"""Resolve independent-plan scope from the accepted native route, without inventing tasks."""
import shlex


def permits_none(work):
    route = (work.get('sdd') or {}).get('route_decision') or {}
    return (route.get('provider') == 'bmad' and route.get('route') == 'direct-build'
            and not route.get('planning_duties') and not any(row.get('role') in {'sprint', 'stories', 'tasks', 'plan'} for row in route.get('required_artifacts', [])))


def establish(repo, work):
    from scripts.cao_plans import bind_plan_scope
    if work.get('status') not in {'active', 'suspended'}:
        return False
    sdd = work.get('sdd') or {}
    old = sdd.get('plan_scope') or {}
    if old.get('mode') == 'bound' or not permits_none(work):
        return False
    # A route is the explicit declaration that the native primary contains the
    # task's planning duties; discover real standalone task inventories first.
    try:
        scope = bind_plan_scope(repo, work, [], {}, False)
        if scope['mode'] == 'unresolved' and not scope.get('candidates'):
            scope = bind_plan_scope(repo, work, [], {}, True)
    except (ValueError, OSError) as exc:
        scope = {'version': 1, 'mode': 'unresolved', 'plans': [], 'reason': str(exc)}
    if scope['mode'] == 'none':
        scope.update(basis={'kind': 'accepted_native_route', 'route_id': sdd['route_decision']['id'],
                            'source': sdd['route_decision']['primary_artifact'],
                            'reason': 'Direct Build primary carries native scope; no associated independent plan was discovered.'})
    scope['requirement_sha256'] = work['requirement']['sha256']
    if old == scope:
        return False
    sdd['plan_scope'] = scope
    from scripts.cao_work_store import queue_event
    queue_event(work, {'event': 'sdd.plan.scope_resolved', 'work_item_id': work['id'], 'plan_scope': scope})
    return True


def repair(repo, work):
    from scripts.cao_plans import bind_plan_scope
    prefix = 'cao sdd plan bind --work ' + shlex.quote(work['id'])
    try:
        scope = bind_plan_scope(repo, work, [], {}, False)
        if scope['mode'] == 'bound':
            args = []
            for plan in scope['plans']:
                if plan.get('item_ids'):
                    args.extend('--plan-item ' + shlex.quote(plan['path'] + '#' + item) for item in plan['item_ids'])
                else:
                    args.append('--plan ' + shlex.quote(plan['path']))
            return prefix + ' ' + ' '.join(args)
        if scope.get('candidates'):
            return 'Inspect which native task file belongs to this requirement: ' + ', '.join(scope['candidates']) + '. Bind each confirmed association with ' + prefix + ' --plan <candidate-path>; this needs an explicit association, not a guessed project-wide plan.'
        if permits_none(work):
            bind_plan_scope(repo, work, [], {}, True)
            return prefix + ' --no-plan'
    except (ValueError, OSError) as exc:
        if 'SHARED_PLAN_ITEM_BINDING_REQUIRED' in str(exc):
            return 'Select the current requirement story/epic identities in ' + str(exc).split(':', 1)[-1].strip() + '; then bind each exact path#identity with ' + prefix + ' --plan-item. Do not use the whole shared plan or invent an item.'
        return 'Restore the associated native plan scope: ' + str(exc) + '. Inspect `cao sdd plan show`; retain all previously bound items.'
    return 'Complete the route-required native planning outputs and identify their current requirement task/story files, then bind them with ' + prefix + ' --plan <native-task-file>. SPEC is not a standalone tasks file.'
