"""Per-finding repair references and current native verification evidence."""
import hashlib
import json
from scripts.path_guard import repo_file


def file_hash(repo, reference):
    if not isinstance(reference, dict) or not reference.get('path') or not reference.get('sha256'):
        raise ValueError('NATIVE_REVIEW_RESOLUTION_REFERENCE_REQUIRED')
    path = repo_file(repo, reference['path'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
        raise ValueError('NATIVE_REVIEW_RESOLUTION_REFERENCE_CHANGED: ' + reference['path'])
    return path.relative_to(repo).as_posix(), reference['sha256']


def validate(repo, work, run, report, current):
    if run.get('resolution_binding_version') != 1:
        return {}, {'protocol': 'legacy_declared_ids', 'note': 'Historical protocol; not recertified as per-finding evidence.'}
    expected = {f['id']: f for f in run.get('findings', report.get('findings', [])) if f['disposition'] == 'patch'}
    resolutions = report.get('resolutions', [])
    if not isinstance(resolutions, list) or any(not isinstance(row, dict) for row in resolutions):
        raise ValueError('NATIVE_REVIEW_RESOLUTION_EVIDENCE_REQUIRED')
    ids = [row.get('id') for row in resolutions]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('NATIVE_REVIEW_RESOLUTION_EVIDENCE_REQUIRED: one resolution per patch finding, with original content_sha256')
    if not expected:
        return {}, {'protocol': 1, 'items': []}
    if report.get('code_snapshot') != current['code_snapshot']:
        raise ValueError(f'NATIVE_REVIEW_RESOLUTION_SNAPSHOT_STALE: code_snapshot (review snapshot) expected={current["code_snapshot"]} actual={report.get("code_snapshot")}; inspect resolution-draft, preserve assertions and revalidate after edits')
    manifest = json.loads(repo_file(repo, run['input_manifest']['path']).read_text())
    original = {row['path']: row for row in manifest['files']}
    from scripts.review_changes import inventory
    _, files = inventory(repo, work)
    products = {row['path']: row for row in files}
    hashes, summary = {}, []
    for row in resolutions:
        finding = expected[row['id']]
        if not finding.get('content_sha256') or row.get('content_sha256') != finding['content_sha256']:
            raise ValueError('NATIVE_REVIEW_FINDING_CONTENT_MISMATCH')
        changes = row.get('changes')
        checks = row.get('verification')
        if not isinstance(changes, list) or not changes or not isinstance(checks, list) or not checks:
            raise ValueError('NATIVE_REVIEW_RESOLUTION_EVIDENCE_REQUIRED: changed files and verification are required')
        for ref in changes:
            if not isinstance(ref, dict) or not str(ref.get('reason', '')).strip():
                raise ValueError('NATIVE_REVIEW_RESOLUTION_CHANGE_REQUIRED')
            name = ref.get('path')
            if ref.get('deleted') is True:
                if name not in original or name in products or ref.get('previous_sha256') != original[name]['sha256']:
                    raise ValueError('NATIVE_REVIEW_RESOLUTION_CHANGE_REQUIRED')
            else:
                name, sha = file_hash(repo, ref)
                if name not in products or (name in original and sha == original[name]['sha256']):
                    raise ValueError('NATIVE_REVIEW_RESOLUTION_CHANGE_REQUIRED: reference an actual product change since review')
                hashes[name] = sha
        authorities = []
        for check_index, check in enumerate(checks):
            if not isinstance(check, dict) or check.get('kind') not in {'test', 'static', 'manual'}:
                raise ValueError('NATIVE_REVIEW_RESOLUTION_VERIFICATION_REQUIRED')
            if (check.get('code_snapshot') != current['code_snapshot'] or not str(check.get('assertion', '')).strip()
                    or not str(check.get('observed', '')).strip()):
                raise ValueError('NATIVE_REVIEW_RESOLUTION_VERIFICATION_REQUIRED: identify the assertion, observed result and current snapshot')
            refs = check.get('evidence')
            if not isinstance(refs, list) or not refs:
                raise ValueError(f'NATIVE_REVIEW_RESOLUTION_VERIFICATION_REQUIRED: resolutions[id={row["id"]}].verification[{check_index}].evidence must be a nonempty array of path/sha256 references')
            observed_refs = []
            for ref in refs:
                name, sha = file_hash(repo, ref)
                hashes[name] = sha
                observed_refs.append({'path': name, 'sha256': sha})
            if check['kind'] in {'test', 'static'}:
                from scripts.native_execution import receipt_matches
                if not receipt_matches(repo, work, observed_refs):
                    raise ValueError('NATIVE_REVIEW_RESOLUTION_EXECUTION_REQUIRED: capture the native check output for the current snapshot')
                authorities.append('observed_execution')
            else:
                authorities.append('native_manual_declaration')
        summary.append({'id': row['id'], 'content_sha256': row['content_sha256'], 'verification_authorities': authorities})
    return hashes, {'protocol': 1, 'code_snapshot': current['code_snapshot'], 'items': summary}


def draft(repo, work, layer):
    from scripts.native_obligations import review_inputs
    from scripts.cao_snapshot import code_snapshot
    from scripts.review_dispatch import bundle_root
    from scripts.state_store import atomic_json
    run = (work.get('sdd', {}).get('native_reviews') or {}).get(layer) or {}
    if run.get('status') != 'classified':
        raise ValueError('NATIVE_REVIEW_CLASSIFY_REQUIRED')
    path = bundle_root(repo, work, layer, run) / 'resolution.json'
    current = review_inputs(repo, work, code_snapshot)
    if path.exists():
        existing = json.loads(path.read_text())
        return {'ok': True, 'report': path.relative_to(repo).as_posix(), 'status': 'existing', 'review_code_snapshot': current['code_snapshot'], 'draft_code_snapshot': existing.get('code_snapshot'), 'snapshot_fresh': existing.get('code_snapshot') == current['code_snapshot'], 'verification_example': verification_example(current['code_snapshot']), 'next': 'Preserve and complete the existing per-finding resolution evidence.'}
    current = review_inputs(repo, work, code_snapshot)
    findings = [f for f in run['findings'] if f['disposition'] == 'patch']
    report = {'run_id': run['id'], 'code_snapshot': current['code_snapshot'], 'resolved_ids': [f['id'] for f in findings],
              'resolutions': [{'id': f['id'], 'content_sha256': f.get('content_sha256'), 'changes': [], 'verification': []} for f in findings]}
    atomic_json(path, report)
    return {'ok': True, 'report': path.relative_to(repo).as_posix(), 'review_code_snapshot': current['code_snapshot'], 'verification_example': verification_example(current['code_snapshot']), 'next': 'Fill actual changed-file hashes/reasons and native verification assertions/results/evidence; refresh code_snapshot after final edits. A draft does not resolve findings.'}


def verification_example(snapshot):
    return {'kind': 'test', 'code_snapshot': snapshot,
            'assertion': '<finding-specific expected behavior>', 'observed': '<actual native check result>',
            'evidence': [{'path': '<captured native output path>', 'sha256': '<actual file hash>'}]}
