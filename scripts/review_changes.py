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
    snapshot = code_snapshot(repo, {work['requirement']['path'], *excluded}, NATIVE_ROOTS, manifest=files)
    return snapshot, files


def capture_baseline(repo, work):
    snapshot, files = inventory(repo, work)
    value = {'version': 1, 'kind': 'work_start', 'snapshot': snapshot, 'files': files}
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
                return path not in {work['requirement']['path'], *excluded} and not excluded_input(path, scope, keys, additional_roots=NATIVE_ROOTS)
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
