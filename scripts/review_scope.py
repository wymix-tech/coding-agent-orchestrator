"""Separate acceptance trace links from executable/native review inputs."""
from copy import deepcopy
import json

from scripts.path_guard import repo_file
from scripts.sdd_acceptance_policy import normalize_declaration


def acceptance_review_scope(repo, work):
    record = ((work.get('sdd') or {}).get('acceptance') or {})
    path = record.get('declaration_file')
    if not path:
        declaration = record.get('declaration') or {}
        executor = declaration.get('executor') or {}
        excluded = set()
        if executor.get('review_impact') == 'acceptance_only' and executor.get('mode') == 'command':
            from scripts.cao_provider_workflows import workflow_command_identities
            from pathlib import Path
            for identity in workflow_command_identities(executor['entrypoint']):
                candidate = Path(identity)
                candidate = candidate if candidate.is_absolute() else repo / candidate
                if candidate.is_file() and candidate.resolve().is_relative_to(repo / 'scripts/verification'):
                    excluded.add(candidate.relative_to(repo).as_posix())
        return excluded, None
    file = repo_file(repo, path)
    raw = json.loads(file.read_text())
    declaration = normalize_declaration(raw)
    accepted = record.get('declaration') or {}
    if accepted.get('evidence_policy') and not declaration.get('evidence_policy'):
        declaration['evidence_policy'] = accepted['evidence_policy']
    if declaration != accepted:
        # Do not hide a file changed outside the accepted configuration.
        return set(), None
    standard = deepcopy(raw)
    for item in standard.get('criteria', []):
        item.pop('covers', None)
    excluded = {path}
    executor = declaration.get('executor') or {}
    if executor.get('review_impact') == 'acceptance_only' and executor.get('mode') == 'command':
        from scripts.cao_provider_workflows import workflow_command_identities
        for identity in workflow_command_identities(executor['entrypoint']):
            from pathlib import Path
            candidate = Path(identity)
            candidate = candidate if candidate.is_absolute() else repo / candidate
            if candidate.is_file() and candidate.resolve().is_relative_to(repo / 'scripts/verification'):
                excluded.add(candidate.relative_to(repo).as_posix())
        # Only execution transport is separated; criterion descriptions and
        # sources remain native review inputs. Technical/acceptance snapshots
        # still include exact executor bytes.
        standard.pop('executor', None)
    return excluded, {'path': path, 'content': standard, 'executable': file.stat().st_mode & 0o111}
