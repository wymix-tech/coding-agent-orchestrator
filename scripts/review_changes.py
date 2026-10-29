"""Complete product change inventory, with a real baseline and no source copies."""
import hashlib
import json
import re
import subprocess

from scripts.cao_runtime import config
from scripts.cao_snapshot import code_snapshot
from scripts.path_guard import repo_file
from scripts.runtime_store import safe_path
from scripts.state_store import atomic_json
from scripts.snapshot_scope import excluded_input, git_inputs, path_key, validate_scope

NATIVE_ROOTS = {'_bmad-output', '_bmad', '.agents', '.pi', 'openspec', '.specify', 'specs'}


def inventory(repo, work):
    from scripts.review_scope import acceptance_review_scope
    excluded, _ = acceptance_review_scope(repo, work)
    files = []
    from scripts.bmad_project import output_dirs
    roots = NATIVE_ROOTS | set(output_dirs(repo)) if (work.get('sdd') or {}).get('provider') == 'bmad' else NATIVE_ROOTS
    snapshot = code_snapshot(repo, {work['requirement']['path'], *excluded}, roots, manifest=files)
    return snapshot, files


def capture_baseline(repo, work):
    snapshot, files = inventory(repo, work)
    from scripts.evidence_archive import retain
    bodies = {row['path']: retain(repo, work['id'], repo_file(repo, row['path']).read_bytes()) for row in files}
    value = {'version': 2, 'kind': 'work_start', 'snapshot': snapshot, 'files': files, 'bodies': bodies}
    path = safe_path(repo, work['id'], 'review-baseline.json')
    atomic_json(path, value)
    work['review_baseline'] = {'path': path.relative_to(repo).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def freeze(repo, work, expected_snapshot):
    current_snapshot, files = inventory(repo, work)
    if current_snapshot != expected_snapshot:
        raise ValueError('NATIVE_REVIEW_INPUT_CHANGED_DURING_PREPARATION')
    current = {item['path']: item for item in files}
    ref = work.get('review_baseline') or {}
    before = None
    if ref:
        path = repo_file(repo, ref['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
            raise ValueError('NATIVE_REVIEW_BASELINE_STALE')
        before = json.loads(path.read_text())
    baseline = {'kind': 'legacy_current_tree', 'limitations': 'No historical baseline available; current tree only'}
    changes = [{'status': 'current', 'path': item['path']} for item in files]
    if before is not None:
        old = {item['path']: item for item in before['files']}
        changes = []
        for path in sorted(old.keys() | current.keys()):
            if old.get(path) != current.get(path):
                changes.append({'status': 'A' if path not in old else 'D' if path not in current else 'M',
                                'path': path, 'before': old.get(path), 'after': current.get(path)})
        baseline = {'kind': 'work_start', 'snapshot': before['snapshot'], 'reference': ref}
    if (repo / '.git').exists():
        requested = 'HEAD'
        source = ((work.get('sdd') or {}).get('route_decision') or {}).get('primary_artifact')
        if source:
            text = repo_file(repo, source).read_text(encoding='utf-8-sig')
            header = re.match(r'\A---\s*\n(.*?)\n---', text, re.S)
            match = re.search(r'(?m)^baseline_commit:\s*[\'\"]?([A-Za-z0-9_./-]+)', header[1]) if header else None
            if match and match[1] != 'NO_VCS':
                requested = match[1]
            elif match:
                return {'version': 1, 'baseline': baseline, 'code_snapshot': current_snapshot, 'files': files, 'changes': changes}
        commit = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options', requested + '^{commit}'], cwd=repo, capture_output=True, text=True, timeout=10)
        if commit.returncode == 0:
            oid = commit.stdout.strip()
            diff = subprocess.run(['git', 'diff', '--name-status', '-z', '-M', '--no-ext-diff', oid, '--'], cwd=repo, capture_output=True, timeout=10)
            if diff.returncode:
                raise ValueError('NATIVE_REVIEW_CHANGE_LIST_UNAVAILABLE')
            tokens = diff.stdout.decode('utf-8', errors='surrogateescape').split('\0')
            tracked = git_inputs(repo)
            scope = validate_scope(repo, config(repo), tracked)
            keys = {path_key(path) for path in tracked}
            from scripts.review_scope import acceptance_review_scope
            excluded, _ = acceptance_review_scope(repo, work)
            def included(path):
                from scripts.bmad_project import output_dirs
                roots = NATIVE_ROOTS | set(output_dirs(repo)) if (work.get('sdd') or {}).get('provider') == 'bmad' else NATIVE_ROOTS
                return path not in {work['requirement']['path'], *excluded} and not excluded_input(path, scope, keys, additional_roots=roots)
            changes, index = [], 0
            while index < len(tokens) and tokens[index]:
                status, name = tokens[index:index + 2]
                index += 2
                item = {'status': status, 'path': name}
                if status.startswith(('R', 'C')):
                    item.update(previous_path=name, path=tokens[index])
                    index += 1
                if included(item['path']) or included(item.get('previous_path', item['path'])):
                    changes.append(item)
            changed_paths = {item['path'] for item in changes}
            changes += [{'status': 'A', 'path': name} for name in sorted(current.keys() - tracked - changed_paths)]
            baseline = {'kind': 'git_commit', 'commit': oid, 'source': 'native_baseline_commit' if requested != 'HEAD' else 'current_HEAD'}
        elif requested != 'HEAD':
            raise ValueError('NATIVE_REVIEW_BASELINE_COMMIT_UNAVAILABLE')
    return {'version': 1, 'baseline': baseline, 'code_snapshot': current_snapshot, 'files': files, 'changes': changes}


def unified_diff(repo, work, manifest):
    """Freeze exact changed bytes for Git and NO_VCS Work-start baselines."""
    import difflib
    baseline = manifest['baseline']
    bodies = {}
    if baseline['kind'] == 'work_start':
        before = json.loads(repo_file(repo, baseline['reference']['path']).read_text())
        bodies = before.get('bodies') or {}
    chunks = []
    for change in manifest['changes']:
        name = change['path']
        previous = change.get('previous_path', name)
        old = b''
        added = change['status'].startswith('A') or change['status'] == 'current'
        if not added:
            if baseline['kind'] == 'git_commit':
                result = subprocess.run(['git', 'show', baseline['commit'] + ':' + previous], cwd=repo, capture_output=True, timeout=10)
                if result.returncode:
                    raise ValueError('NATIVE_REVIEW_DIFF_BASELINE_UNAVAILABLE: ' + previous)
                old = result.stdout
            elif previous in bodies:
                old = repo_file(repo, bodies[previous]).read_bytes()
                expected = (change.get('before') or {}).get('sha256')
                if expected and hashlib.sha256(old).hexdigest() != expected:
                    raise ValueError('NATIVE_REVIEW_BASELINE_STALE')
            else:
                raise ValueError('NATIVE_REVIEW_DIFF_BASELINE_UNAVAILABLE: restore the original baseline bytes or explicitly establish a native comparison baseline; ' + previous)
        path = repo_file(repo, name)
        new = path.read_bytes() if path.is_file() else b''
        header = 'diff --git a/' + previous + ' b/' + name + '\n'
        try:
            a, b = old.decode('utf-8').splitlines(keepends=True), new.decode('utf-8').splitlines(keepends=True)
            delta = ''.join(difflib.unified_diff(a, b, fromfile='/dev/null' if added else 'a/' + previous,
                tofile='/dev/null' if change['status'].startswith('D') else 'b/' + name))
            if not delta and previous != name:
                delta = 'rename from ' + previous + '\nrename to ' + name + '\n'
            if not delta and change.get('before') != change.get('after'):
                delta = 'file mode or metadata changed\n'
        except UnicodeError:
            delta = 'Binary files differ\n'
        if delta:
            chunks.append(header + delta + ('\n' if not delta.endswith('\n') else ''))
    if not chunks:
        raise ValueError('NATIVE_REVIEW_DIFF_EMPTY: no product changes to review')
    limitation = '# Baseline unavailable: best-effort current product content; this is not proof of historical additions.\n' if baseline['kind'] == 'legacy_current_tree' else ''
    return limitation + ''.join(chunks)
