"""Explicit native review loopback: archive reviews before re-deriving code."""
from copy import deepcopy

from scripts.cao_runtime import now, new_id
from scripts.path_guard import repo_file


def product_paths(repo, paths):
    from scripts.bmad_project import output_dirs
    protected = ['.git', '.orchestrator', '_bmad', '.agents', '.pi', *output_dirs(repo)]
    normalized = []
    for value in paths:
        if any(char in value for char in '*?[]:$`~'):
            raise ValueError('NATIVE_LOOPBACK_LITERAL_PATH_REQUIRED')
        path = repo_file(repo, value)
        if path == repo.resolve() or path.is_dir() or any(path.is_relative_to(repo.resolve() / root) for root in protected):
            raise ValueError('NATIVE_LOOPBACK_PRODUCT_FILE_REQUIRED')
        normalized.append(path.relative_to(repo.resolve()).as_posix())
    return sorted(set(normalized))


def begin(repo, work, reason, keep, paths, extend=False):
    from scripts.native_obligations import active_layers, require_review_integrity
    sdd = work['sdd']
    current = sdd.get('native_loopback') or {}
    if not reason.strip() or not keep.strip() or not paths:
        raise ValueError('NATIVE_LOOPBACK_CONTEXT_REQUIRED')
    normalized = product_paths(repo, paths)
    if extend:
        if current.get('status') != 'reimplementing':
            raise ValueError('NATIVE_LOOPBACK_NOT_ACTIVE')
        current.setdefault('scope_changes', []).append({'at': now(), 'paths': normalized, 'reason': reason.strip(), 'keep': keep.strip()})
        current['paths'] = sorted(set(current['paths']) | set(normalized))
        return {'ok': True, 'loopback': current, 'next': 'Continue native reimplementation within the recorded expanded file scope. Route, approval, checks and fresh review remain required.'}
    if current.get('status') == 'reimplementing':
        raise ValueError('NATIVE_LOOPBACK_ALREADY_ACTIVE')
    require_review_integrity(repo, work)
    records = sdd.get('native_reviews') or {}
    if not (sdd.get('route_decision') or {}).get('primary_artifact'):
        raise ValueError('NATIVE_LOOPBACK_ROUTE_REQUIRED: bind the actual Build route before a controlled code loopback')
    layers = active_layers(repo, work)
    if not layers or any((records.get(layer) or {}).get('status') != 'classified' for layer in layers):
        raise ValueError('NATIVE_REVIEW_CLASSIFY_REQUIRED')
    if not any(records[layer].get('loopback_required') for layer in layers):
        raise ValueError('NATIVE_LOOPBACK_NOT_REQUIRED')
    source = repo_file(repo, sdd['route_decision']['primary_artifact'])
    import re
    text = source.read_text(encoding='utf-8-sig')
    header = re.match(r'\A---\s*\n(.*?)\n---(?:\n|$)', text, re.S)
    baseline = re.search(r'(?m)^baseline_commit:\s*[\x27\x22]?([A-Za-z0-9_-]+)', header[1]) if header else None
    baseline_revision = baseline[1] if baseline else None
    added = set()
    restore_hashes = {}
    import json
    for layer in layers:
        ref = records[layer].get('input_manifest')
        if ref:
            manifest = json.loads(repo_file(repo, ref['path']).read_text())
            commit = (manifest.get('baseline') or {}).get('commit')
            if commit:
                baseline_revision = commit
            added.update(item['path'] for item in manifest.get('changes', []) if item.get('status', '').startswith(('A', 'R', 'C')))
            restore_hashes.update({item['path']: item['before']['sha256'] for item in manifest.get('changes', [])
                                   if (item.get('before') or {}).get('sha256')})
    if baseline_revision and baseline_revision != 'NO_VCS':
        import subprocess
        result = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options', baseline_revision + '^{commit}'], cwd=repo,
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError('NATIVE_LOOPBACK_BASELINE_UNAVAILABLE')
        baseline_revision = result.stdout.strip()
    record = {'id': new_id('loopback'), 'status': 'reimplementing', 'reason': reason.strip(),
              'keep': keep.strip(), 'paths': sorted(set(normalized)), 'started_at': now(),
              'baseline_commit': baseline_revision, 'added_files': sorted(added & set(normalized)),
              'restore_hashes': {path: sha for path, sha in restore_hashes.items() if path in normalized},
              'source': sdd['route_decision']['primary_artifact'], 'review_contract': deepcopy(records.get('_contract')),
              'previous_execution': deepcopy(work.get('execution')),
              'requirement_sha256': work['requirement']['sha256']}
    intent_gap = any(records[layer].get('intent_conflicts') or any(
        finding.get('disposition') == 'intent_gap' or finding.get('violates_frozen_intent') is True
        for finding in records[layer].get('findings', [])) for layer in layers)
    if intent_gap:
        sdd.pop('spec_approval', None)
        record['human_resolution_required'] = True
    for layer in layers:
        sdd.setdefault('native_review_history', []).append({'layer': layer, 'review': deepcopy(records[layer]),
            'contract': deepcopy(records.get('_contract')), 'replacement_reason': 'native loopback', 'loopback_id': record['id'], 'replaced_at': now()})
    sdd.setdefault('native_loopback_history', []).extend([current] if current else [])
    sdd['native_loopback'] = record
    sdd['native_reviews'] = {}
    sdd.pop('native_implementation', None)
    sdd.pop('implementation_preparation', None)
    work['verification'] = {}
    work['gate_status'] = {}
    if isinstance(work.get('execution'), dict):
        work['execution'].update(step='implementation', phase='implementation')
    workflow = sdd.get('workflow') or {}
    if workflow:
        workflow['status'] = 'selected'
        for key in ('execution', 'completed_at', 'artifact_hashes'):
            workflow.pop(key, None)
    return {'ok': True, 'loopback': record, 'next': 'Follow native step-04: preserve KEEP, revert only the recorded product files using the original baseline, resolve intent gaps with the human, refresh route/approval if changed, then follow step-03 to reimplement. Do not launch review until reimplementation is complete. Use review prepare for the new round.'}


def rollback_write(repo, work, raw):
    """Allow only explicit file restores from the recorded original revision.

    Native loopback requires reverting BEFORE planning/approval and step-03.
    General reimplementation still goes through every ordinary write gate.
    """
    record = ((work or {}).get('sdd') or {}).get('native_loopback') or {}
    baseline = record.get('baseline_commit')
    if record.get('status') != 'reimplementing' or (work or {}).get('status') != 'active':
        return False
    from scripts.cao_tool_policy import _collect_values_for_keys, _shell_segments, SHELL_TOOLS, _event_key, tool_cwd
    tool = _event_key(raw, 'tool_name', 'toolname', 'tool', 'name').lower()
    payload = raw.get('tool_input') or raw.get('input') or raw.get('arguments') or raw.get('params') or {}
    if tool in {'write', 'write_file', 'create_file'} and isinstance(payload, dict) and isinstance(payload.get('content'), str):
        import hashlib
        try:
            path = repo_file(repo, payload.get('path') or payload.get('file_path') or payload.get('filePath')).relative_to(repo.resolve()).as_posix()
            expected = record.get('restore_hashes', {}).get(path)
            return bool(expected) and hashlib.sha256(payload['content'].encode()).hexdigest() == expected
        except (ValueError, OSError, TypeError):
            return False
    if tool not in SHELL_TOOLS:
        return False
    commands = _collect_values_for_keys(raw, {'command', 'cmd'})
    cwd = tool_cwd(raw, repo)
    if len(commands) != 1 or cwd is None or cwd.resolve() != repo.resolve():
        return False
    try:
        segments = _shell_segments(commands[0])
        if len(segments) != 1:
            return False
        tokens = segments[0]
        if baseline and baseline != 'NO_VCS' and tokens[:6] == ['git', 'restore', '--source', baseline, '--worktree', '--']:
            paths = tokens[6:]
            allowed = set(record['paths']) - set(record['added_files'])
        elif tokens[:2] == ['rm', '--']:
            paths = tokens[2:]
            allowed = set(record['added_files'])
        else:
            return False
        return bool(paths) and all(value in allowed and repo_file(repo, value).relative_to(repo.resolve()).as_posix() == value for value in paths)
    except (ValueError, OSError, RuntimeError):
        return False


def write_state(repo, work, raw):
    record = (work.get('sdd') or {}).get('native_loopback') or {}
    if record.get('status') != 'reimplementing':
        return {'ready': True}
    from scripts.cao_tool_policy import tool_effect
    analysis = tool_effect(raw, repo)
    from scripts.cao_runtime import verification_scripts, config
    from scripts.cao_tool_policy import _collect_values_for_keys, tool_cwd
    commands = _collect_values_for_keys(raw, {'command', 'cmd'})
    cwd = tool_cwd(raw, repo)
    trusted = {item['command'] for item in verification_scripts(config(repo))}
    if len(commands) == 1 and commands[0] in trusted and cwd is not None and cwd.resolve() == repo.resolve():
        return {'ready': True}
    allowed = {repo_file(repo, path) for path in record['paths']}
    if any(repo_file(repo, path).relative_to(repo.resolve()).as_posix() != path for path in record['paths']):
        return {'ready': False, 'reason': 'NATIVE_LOOPBACK_SCOPE_CHANGED', 'next': 'Restore the recorded file locations; linked aliases cannot expand a loopback scope.'}
    targets = {repo_file(repo, path) for path in analysis['targets']}
    if len(commands) == 1 and not analysis['diagnostics'] and targets:
        from scripts.cao_tool_policy import _shell_segments
        segments = _shell_segments(commands[0])
        if len(segments) == 1 and segments[0][:2] == ['mkdir', '-p'] and all(
                target != repo.resolve() and any(path.is_relative_to(target) for path in allowed) for target in targets):
            return {'ready': True}
    if analysis['diagnostics'] or not targets or not targets.issubset(allowed):
        return {'ready': False, 'reason': 'NATIVE_LOOPBACK_SCOPE_REQUIRED',
                'next': 'Use explicit recorded file paths for native rollback and reimplementation; do not use an unbounded reset/restore or change unrelated files.'}
    return {'ready': True}
