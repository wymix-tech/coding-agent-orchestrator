"""Exact CAO review evidence paths, excluded from product inputs and checked separately."""
import re
from scripts.path_guard import provider_artifact


def outputs(repo, work):
    from scripts.review_dispatch import bundle_root
    from scripts.cao_provider_workflows import provider_output_dirs
    sdd = work.get('sdd') or {}
    if sdd.get('provider') != 'bmad':
        return set()
    records = [r for k, r in sdd.get('native_reviews', {}).items() if k != '_contract']
    records += [r['review'] for r in sdd.get('native_review_history', []) if isinstance(r.get('review'), dict)]
    result = set()
    fixed = {'input-manifest.json', 'context-policy.json', 'reviewer-prompt.md', 'changes.diff', 'current-changes.diff',
             'dispatch.json', 'report.json', 'resolution.json', 'findings.md', 'findings.json',
             'recovered-original-message.txt', 'recovered-findings.md'}
    for run in records:
        if not run.get('id') or not run.get('layer'):
            continue
        root = bundle_root(repo, work, run['layer'], run)
        result.update((root / name).relative_to(repo).as_posix() for name in fixed)
        if root.is_dir():
            for path in root.iterdir():
                if re.fullmatch(r'(?:original-message|message-findings|findings)-[a-f0-9]{64}\.(?:txt|md|json)', path.name):
                    result.add(path.relative_to(repo).as_posix())
        for name in {*run.get('triage_hashes', {}), *run.get('evidence_hashes', {})}:
            try:
                path = provider_artifact(repo, name, provider_output_dirs('bmad', repo))
            except ValueError:
                continue  # Changed product references must remain product inputs.
            if path.suffix.lower() in {'.md', '.json', '.txt', '.log'}:
                result.add(path.relative_to(repo).as_posix())
    return result
