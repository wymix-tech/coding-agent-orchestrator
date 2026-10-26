"""Bind the current complete rendered Build contract before implementation.

Legacy workflows without an implementation step keep their own contracts. Never
scan other renderer runs or expand an unresolved template as execution evidence.
"""
import hashlib
import re

from scripts.path_guard import repo_file


def resolve(repo, rules, build_route):
    roots = list(dict.fromkeys(repo_file(repo, item['path']).parent for item in rules))
    for item in rules:
        source = repo_file(repo, item['path'])
        if 'step-03-implement.md' in source.read_text(encoding='utf-8-sig') and not (source.parent / 'step-03-implement.md').is_file():
            raise ValueError('NATIVE_ROUTE_CONTRACT_FILE_REQUIRED: current implementation step is missing')
    roots = [root for root in roots if (root / 'step-03-implement.md').is_file()]
    if not roots:
        return {}
    snapshots = [root for root in roots if root.is_relative_to(repo / '_bmad/render')]
    if len(snapshots) != 1:
        raise ValueError('NATIVE_ROUTE_RENDERED_CONTRACT_REQUIRED: reference the current complete renderer snapshot')
    root = snapshots[0]
    review = root / ('step-04-review.md' if build_route == 'dispatch' else 'step-oneshot.md')
    if not review.is_file():
        raise ValueError('NATIVE_ROUTE_REVIEW_CONTRACT_REQUIRED: ' + review.relative_to(repo).as_posix())
    content = review.read_text(encoding='utf-8-sig')
    layers = sorted(set(re.findall(r'(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)', content)))
    if not layers:
        raise ValueError('NATIVE_ROUTE_REVIEW_LAYERS_REQUIRED: ' + review.relative_to(repo).as_posix())
    files = {'implementation': root / 'step-03-implement.md', 'review': review}
    for index, name in enumerate(sorted(set(re.findall(r'\bstep-05-[a-z-]+\.md\b', content)))):
        files['finalize-' + str(index + 1)] = root / name
    result = {'layers': layers}
    for role, path in files.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError('NATIVE_ROUTE_CONTRACT_FILE_REQUIRED: ' + path.relative_to(repo).as_posix())
        result[role] = {'path': path.relative_to(repo).as_posix(),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def validate(repo, contracts):
    for role, item in contracts.items():
        if role == 'layers':
            continue
        path = repo_file(repo, item['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('NATIVE_ROUTE_CONTRACT_STALE: ' + item['path'])
