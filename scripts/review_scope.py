"""Separate acceptance trace links from executable/native review inputs."""
from copy import deepcopy
import json

from scripts.path_guard import repo_file
from scripts.sdd_acceptance_policy import normalize_declaration


def acceptance_review_scope(repo, work):
    record = ((work.get('sdd') or {}).get('acceptance') or {})
    path = record.get('declaration_file')
    if not path:
        return set(), None
    file = repo_file(repo, path)
    raw = json.loads(file.read_text())
    declaration = normalize_declaration(raw)
    if declaration != record.get('declaration'):
        # Do not hide a file changed outside the accepted configuration.
        return set(), None
    standard = deepcopy(raw)
    for item in standard.get('criteria', []):
        item.pop('covers', None)
    return {path}, {'path': path, 'content': standard, 'executable': file.stat().st_mode & 0o111}
