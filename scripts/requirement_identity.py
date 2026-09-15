#!/usr/bin/env python3
"""Stable requirement/work-item identity and history registry.

Requirement identity is distinct from revision identity. A source/native identifier defines
identity where available; content hashes only identify revisions and must never merge two
independent requirements that happen to have identical prose.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_REL = Path('.orchestrator/requirements/registry.json')
JOURNAL_REL = Path('.orchestrator/requirements/migration-journal.json')
BACKUP_REL = Path('.orchestrator/requirements/registry.json.bak')

IDENTITY_VERSION = 2
LEGACY_MIGRATION_COMMAND = 'python3 scripts/requirement_identity.py --migrate --repo .'

# Structural boundary for mixed-content sources: requirement text vs runtime text in the same
# file. Anything outside these headings is runtime progress (task tick-boxes, dev records),
# and must never change the requirement content revision.
REQUIREMENT_SECTIONS = ('Story', 'Requirement', 'Description', 'Acceptance Criteria', '验收标准', '需求描述')
RUNTIME_SECTIONS = ('Status', 'Tasks', 'Subtasks', 'Dev Agent Record', 'QA Results', 'Change Log')


def _canonical_text(text: str) -> str:
    return '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n'))


def revision_id(text: str) -> str:
    return 'rev-' + hashlib.sha256(_canonical_text(text).encode('utf-8')).hexdigest()[:16]


def source_revision_id(repo: Path, source_ref: str | None) -> str | None:
    """Content revision of a source-backed requirement, or None when it cannot be read.

    A resume prompt such as "continue" must not look like a requirement change. When a
    requirement is identified by a source document, its revision is the content of that
    document, never the wording of the request that pointed at it.
    """
    if not source_ref:
        return None
    path = Path(source_ref)
    candidate = path if path.is_absolute() else (Path(repo).resolve() / path)
    try:
        content = candidate.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    norm = Path(source_ref).as_posix().lstrip('./')
    return revision_id(f'{norm}\n{content}')


def _source_path(repo: Path, source_ref: str) -> Path:
    path = Path(source_ref)
    try:
        return path if path.is_absolute() else (Path(repo).resolve() / path)
    except OSError:  # pragma: no cover - defensive
        return Path(repo).resolve() / source_ref


def _read_source_bytes(repo: Path, source_ref: str) -> tuple[bytes, None] | tuple[None, str]:
    candidate = _source_path(repo, source_ref)
    try:
        return candidate.read_bytes(), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{type(exc).__name__}"


def _headings(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs. Unknown layout yields an empty list."""
    sections: list[tuple[str, str]] = []
    current: str | None = None
    body: list[str] = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#') and len(stripped) > 1 and stripped.lstrip('#').startswith((' ', '\t')):
            if current is not None:
                sections.append((current, '\n'.join(body)))
            current = stripped.lstrip('#').strip()
            body = []
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        sections.append((current, '\n'.join(body)))
    return sections


def _matches(title: str, names: tuple[str, ...] | list[str]) -> bool:
    lowered = title.strip().lower()
    return any(str(name).strip().lower() in lowered for name in names)


def _requirement_region(repo: Path, source_ref: str, raw: bytes, *, boundary: dict | None) -> dict:
    """Split one mixed-content file into a requirement region and a runtime region.

    Runtime sections (task tick-boxes, dev records, change logs) are excluded from the
    content revision; every other section is kept, so content is never silently dropped.
    An unrecognised layout needs an explicit boundary instead of a broad regex.
    """
    boundary = boundary or {}
    runtime_names = [str(n) for n in (boundary.get('runtime_sections') or RUNTIME_SECTIONS)]
    requirement_names = [str(n) for n in (boundary.get('requirement_sections') or REQUIREMENT_SECTIONS)]
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return {'error': 'MIXED_CONTENT_UNMAPPED', 'message': 'source is not UTF-8 text; declare an explicit boundary',
                'next_action': 'configure_content_boundary'}
    sections = _headings(text)
    runtime_present = [title for title, _ in sections if _matches(title, runtime_names)]
    # Only a source that actually mixes runtime progress into the requirement file is split.
    # A plain requirement document stays whole-file: its content revision never changes shape
    # for existing records.
    if not sections or not runtime_present:
        return {'content': _canonical_text(text), 'boundary': 'whole_file', 'runtime_sections': [],
                'requirement_sections': [], 'diagnostics': []}
    kept, dropped, unmapped = [], [], []
    for title, body in sections:
        if _matches(title, runtime_names):
            dropped.append(title)
        else:
            kept.append((title, body))
            if not _matches(title, requirement_names) and not _matches(title, runtime_names):
                unmapped.append(title)
    if dropped and not kept:
        return {'error': 'MIXED_CONTENT_NO_REQUIREMENT_REGION',
                'message': 'every section of this source is runtime content; declare an explicit boundary',
                'next_action': 'configure_content_boundary', 'runtime_sections': dropped}
    content = '\n\n'.join(f'{title}\n{_canonical_text(body)}' for title, body in kept)
    return {
        'content': content,
        'boundary': 'explicit' if boundary else 'default_headings',
        'requirement_sections': [t for t, _ in kept],
        'runtime_sections': dropped,
        'diagnostics': [{'code': 'SOURCE_SECTION_UNMAPPED', 'headings': unmapped}] if unmapped else [],
    }


def file_set_revision(repo: Path, members: list[str], *, exclude: tuple[str, ...] = ()) -> dict:
    """Deterministic digest of an ordered, normalized file set.

    Symbolically linked paths and generated artefacts are excluded; membership itself is part
    of the revision, so adding or removing a member changes it.
    """
    resolved_root = Path(repo).resolve()
    normalized: list[str] = []
    for raw in members:
        path = _source_path(resolved_root, str(raw))
        if path.is_symlink():
            continue
        try:
            rel = path.resolve().relative_to(resolved_root).as_posix()
        except (OSError, ValueError):
            rel = str(Path(str(raw)).as_posix()).lstrip('./')
        if any(rel == pat or rel.startswith(f'{pat}/') for pat in ('.git', 'node_modules', '__pycache__', *exclude)):
            continue
        normalized.append(rel)
    ordered = sorted(set(normalized))
    digests = {}
    for rel in ordered:
        candidate = resolved_root / rel
        try:
            digests[rel] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            digests[rel] = None
    material = '\n'.join(f'{rel}\n{digests[rel] or "unreadable"}' for rel in ordered)
    return {
        'members': ordered,
        'member_digests': digests,
        'file_set_revision': 'srcrev-' + hashlib.sha256(material.encode('utf-8')).hexdigest()[:16],
        'missing_members': [rel for rel, d in digests.items() if d is None],
    }


def requirement_content_revision(repo: Path, source_ref: str | None, *, members: list[str] | None = None,
                                 boundary: dict | None = None, exclude: tuple[str, ...] = ()) -> dict:
    """Structured source revision: requirement content, kept separate from the raw digest.

    Always returns a diagnostic-bearing result. `source_revision` is None when it could not be
    computed; callers must never fall back to "unchanged" in that case.
    """
    root = Path(repo).resolve()
    if members is None:
        if not source_ref:
            return {'source_revision': None, 'raw_digest': None, 'members': [],
                    'error': 'SOURCE_UNCONFIGURED', 'message': 'no requirement source member was declared',
                    'next_action': 'declare_source_members'}
        members = [str(source_ref)]
    listing = file_set_revision(root, members, exclude=exclude)
    if listing['missing_members']:
        return {**listing, 'source_revision': None, 'raw_digest': None,
                'error': 'SOURCE_UNREADABLE', 'message': f"unreadable members: {', '.join(listing['missing_members'])}",
                'next_action': 'repair_source_members'}
    raw_digest = hashlib.sha256(
        ('\n'.join(f"{rel}\n{listing['member_digests'][rel]}" for rel in listing['members'])).encode('utf-8')
    ).hexdigest()

    pieces, diagnostics, requirement_sections, runtime_sections, boundary_kinds = [], [], [], [], []
    unreadable = False
    for rel in listing['members']:
        body, failure = _read_source_bytes(root, rel)
        if body is None:
            unreadable = True
            continue
        region = _requirement_region(root, rel, body, boundary=boundary)
        if region.get('error'):
            return {**listing, 'raw_digest': raw_digest, 'source_revision': None, 'member': rel, **region}
        pieces.append(f"{rel}\n{region['content']}")
        diagnostics.extend(region.get('diagnostics') or [])
        requirement_sections.extend(f'{rel}:{name}' for name in region.get('requirement_sections') or [])
        runtime_sections.extend(f'{rel}:{name}' for name in region.get('runtime_sections') or [])
        boundary_kinds.append(region.get('boundary'))
    if unreadable or not pieces:
        return {**listing, 'raw_digest': raw_digest, 'source_revision': None,
                'error': 'SOURCE_UNREADABLE', 'message': 'no requirement content could be read',
                'next_action': 'repair_source_members'}
    # A single plain-text member keeps the historical whole-content revision so that already
    # recorded baselines stay comparable; only structured (mixed-content) sources use regions.
    if len(listing['members']) == 1 and boundary_kinds == ['whole_file']:
        source_revision = source_revision_id(root, listing['members'][0])
    else:
        source_revision = revision_id('\n'.join(pieces))
    return {
        'members': listing['members'],
        'member_digests': listing['member_digests'],
        'source_revision': source_revision,
        'raw_digest': raw_digest,
        'boundary': boundary_kinds[0] if len(set(boundary_kinds)) == 1 else 'mixed',
        'requirement_sections': requirement_sections,
        'runtime_sections': runtime_sections,
        'diagnostics': diagnostics,
        'identity_version': IDENTITY_VERSION,
    }


def stable_requirement_id(*, provider: str, source_type: str | None = None,
                          source_path: str | None = None, native_id: str | None = None,
                          explicit_work_id: str | None = None, request_text: str = '') -> str:
    if native_id:
        return f'{provider}:native:{native_id}'
    if source_path:
        norm = Path(source_path).as_posix().lstrip('./')
        return f'{provider}:source:{norm}'
    if explicit_work_id:
        return f'{provider}:work:{explicit_work_id}'
    # Direct free-text intake has no external identity. Establish a stable local identity
    # from the first revision, but never use this to merge two source-backed requirements.
    return f'{provider}:direct:{hashlib.sha256(_canonical_text(request_text).encode()).hexdigest()[:16]}'


def candidate_identity(candidate: dict[str, Any], provider: str, *, repo: Path | None = None,
                       boundary: dict | None = None, explicit_revision: str | None = None,
                       native_state_revision: str | None = None) -> dict[str, Any]:
    """Resolve the v2 quadruple for one candidate.

    `request_revision` only tracks how the request was worded. When a source exists it never
    becomes the requirement revision: only parsed source content may do that.
    """
    text = str(candidate.get('request_text') or '')
    rid = candidate.get('requirement_id') or stable_requirement_id(
        provider=provider,
        source_type=candidate.get('source_type'),
        source_path=candidate.get('path'),
        native_id=candidate.get('native_id'),
        explicit_work_id=candidate.get('work_id') or candidate.get('native_id'),
        request_text=text,
    )
    request_revision = str(candidate.get('request_revision') or revision_id(text))
    source_revision, source_diagnostic = None, None
    members = candidate.get('members') if isinstance(candidate.get('members'), list) else None
    if (repo is not None or members) and (candidate.get('path') or members):
        resolved = requirement_content_revision(
            Path(repo).resolve() if repo is not None else Path('.'),
            candidate.get('path'),
            members=members,
            boundary=boundary,
        )
        source_revision = resolved.get('source_revision')
        source_diagnostic = resolved.get('error') or (resolved.get('diagnostics') or None)
    revision = str(explicit_revision or source_revision or request_revision)
    return {
        'requirement_id': str(rid),
        'revision_id': revision,
        'source_revision': str(source_revision) if source_revision else None,
        'request_revision': request_revision,
        'explicit_revision': str(explicit_revision) if explicit_revision else None,
        'native_state_revision': str(native_state_revision) if native_state_revision else None,
        'identity_version': IDENTITY_VERSION,
        'source_diagnostic': source_diagnostic,
    }


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_registry(repo: Path) -> dict[str, Any]:
    path = repo.resolve() / REGISTRY_REL
    if not path.exists():
        return {'schema_version': 1, 'requirements': {}}
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'schema_version': 1, 'requirements': {}, 'migration_warning': 'registry_unreadable'}
    if not isinstance(doc, dict) or not isinstance(doc.get('requirements', {}), dict):
        return {'schema_version': 1, 'requirements': {}, 'migration_warning': 'registry_shape_unknown'}
    doc.setdefault('schema_version', 1); doc.setdefault('requirements', {})
    return doc


def save_registry(repo: Path, registry: dict[str, Any]) -> None:
    _atomic_json(repo.resolve() / REGISTRY_REL, registry)


def record(repo: Path, *, requirement_id: str, revision_id: str, work_item_id: str,
           provider: str, source_path: str | None = None, native_id: str | None = None,
           status: str, completed_at: str | None = None,
           source_revision: str | None = None, request_revision: str | None = None,
           native_state_revision: str | None = None, explicit_revision: str | None = None,
           members: list[str] | None = None) -> None:
    reg = load_registry(repo)
    req = reg['requirements'].setdefault(requirement_id, {
        'provider': provider, 'source_path': source_path, 'native_id': native_id,
        'revisions': {}, 'work_items': [], 'identity_version': IDENTITY_VERSION,
    })
    req['provider'] = provider
    if source_path: req['source_path'] = source_path
    if native_id: req['native_id'] = native_id
    if source_revision: req['source_revision'] = source_revision
    if request_revision: req['request_revision'] = request_revision
    if native_state_revision: req['native_state_revision'] = native_state_revision
    if explicit_revision: req['explicit_revision'] = explicit_revision
    if members: req['members'] = sorted(set(str(m) for m in members))
    req['identity_version'] = IDENTITY_VERSION
    rev = req['revisions'].setdefault(revision_id, {})
    rev.update({'status': status, 'work_item_id': work_item_id})
    if completed_at: rev['completed_at'] = completed_at
    if work_item_id not in req['work_items']:
        req['work_items'].append(work_item_id)
    save_registry(repo, reg)


def last_source_revision(repo: Path, requirement_id: str) -> str | None:
    """Last recorded content revision of a source-backed requirement, if any."""
    reg = load_registry(repo)
    req = (reg.get('requirements') or {}).get(requirement_id) or {}
    value = req.get('source_revision')
    return str(value) if value else None


def processed(repo: Path, requirement_id: str, revision_id: str) -> bool:
    reg = load_registry(repo)
    rev = (((reg.get('requirements') or {}).get(requirement_id) or {}).get('revisions') or {}).get(revision_id) or {}
    return rev.get('status') in {'completed', 'archived'}


def is_legacy_entry(entry: dict[str, Any]) -> bool:
    """An entry whose source content revision was never recorded is not migration-free."""
    return not entry.get('source_revision') and not str(entry.get('requirement_id', '')).endswith('ignored')


def mark_legacy(registry: dict[str, Any]) -> dict[str, Any]:
    """Mark v1 entries explicitly. Unverified lack of change is never assumed."""
    for requirement_id, entry in (registry.get('requirements') or {}).items():
        if is_legacy_entry(entry):
            entry['identity_version'] = 1
            entry['legacy'] = True
            entry['legacy_reason'] = entry.get('legacy_reason') or 'no source_revision recorded'
            entry['migration_command'] = LEGACY_MIGRATION_COMMAND
    registry.setdefault('identity_version', IDENTITY_VERSION)
    return registry


def check_revision_confirmation(repo: Path, *, requirement_id: str, source_revision: str | None,
                                phase: str | None, status: str | None,
                                confirmation: dict[str, Any] | None,
                                active_revision: str | None = None) -> dict[str, Any]:
    """A destructive-revision confirmation is valid only for the state it was issued against."""
    if not confirmation:
        return {'allowed': False, 'error': 'REVISION_CONFIRMATION_REQUIRED',
                'message': 'confirming a destructive revision requires --requirement-revision and --current-state.',
                'next_action': 'confirm_requirement_revision'}
    if str(confirmation.get('requirement_id') or '') != str(requirement_id):
        return {'allowed': False, 'error': 'REVISION_CONFIRMATION_MISMATCH',
                'message': 'the confirmation was issued for a different requirement.',
                'next_action': 'confirm_requirement_revision'}
    recorded = last_source_revision(repo, requirement_id)
    accepted = {str(value) for value in (source_revision, recorded, active_revision) if value}
    if source_revision and recorded and str(confirmation.get('source_revision') or '') not in accepted:
        return {'allowed': False, 'error': 'REVISION_CONFIRMATION_SUPERSEDED',
                'message': 'the requirement source revision moved after the confirmation was issued.',
                'confirmed_source_revision': confirmation.get('source_revision'), 'current_source_revision': source_revision,
                'next_action': 'confirm_requirement_revision'}
    if phase is not None and confirmation.get('phase') is not None and str(confirmation.get('phase')) != str(phase):
        return {'allowed': False, 'error': 'REVISION_CONFIRMATION_SUPERSEDED',
                'message': 'the work item phase changed after the confirmation was issued; re-confirm against the current state.',
                'confirmed_phase': confirmation.get('phase'), 'current_phase': phase,
                'next_action': 'confirm_requirement_revision'}
    if status is not None and confirmation.get('status') is not None and str(confirmation.get('status')) != str(status):
        return {'allowed': False, 'error': 'REVISION_CONFIRMATION_SUPERSEDED',
                'message': 'the work item status changed after the confirmation was issued; re-confirm against the current state.',
                'confirmed_status': confirmation.get('status'), 'current_status': status,
                'next_action': 'confirm_requirement_revision'}
    return {'allowed': True}


# --- migration: preview -> backup -> atomic replace, idempotent and resumable --------------

def load_journal(repo: Path) -> dict[str, Any]:
    path = repo.resolve() / JOURNAL_REL
    if not path.exists():
        return {'status': 'none', 'completed': []}
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'status': 'unreadable', 'completed': []}
    if not isinstance(doc, dict):
        return {'status': 'unreadable', 'completed': []}
    completed = doc.get('completed')
    doc['completed'] = completed if isinstance(completed, list) else []
    return doc


def preview_migration(repo: Path) -> dict[str, Any]:
    """What migration would do. Writes nothing."""
    registry = mark_legacy(load_registry(repo))
    journal = load_journal(repo)
    pending, planned, unresolvable = [], [], []
    for requirement_id, entry in (registry.get('requirements') or {}).items():
        if not is_legacy_entry(entry):
            continue
        pending.append(requirement_id)
        resolution = _resolve_legacy_source(repo, entry)
        if resolution.get('source_revision'):
            planned.append({'requirement_id': requirement_id, 'source_revision': resolution['source_revision']})
        else:
            unresolvable.append({'requirement_id': requirement_id, 'error': resolution.get('error'),
                                 'next_action': resolution.get('next_action') or 'declare_source_members'})
    return {
        'status': 'PREVIEW',
        'identity_version': IDENTITY_VERSION,
        'backup_path': str(repo.resolve() / BACKUP_REL),
        'backup_exists': (repo.resolve() / BACKUP_REL).exists(),
        'pending': pending,
        'planned': planned,
        'unresolvable': unresolvable,
        'resumed_from_journal': journal.get('status') == 'in_progress',
        'already_migrated': journal.get('completed') or [],
    }


def _resolve_legacy_source(repo: Path, entry: dict[str, Any]) -> dict[str, Any]:
    source_path = entry.get('source_path')
    members = entry.get('members') if isinstance(entry.get('members'), list) else None
    if not source_path and not members:
        return {'source_revision': None, 'error': 'SOURCE_UNCONFIGURED', 'next_action': 'declare_source_members'}
    resolved = requirement_content_revision(Path(repo).resolve(), source_path, members=members)
    if not resolved.get('source_revision'):
        return {'source_revision': None, 'error': resolved.get('error') or 'SOURCE_UNREADABLE',
                'next_action': resolved.get('next_action') or 'repair_source_members'}
    return {'source_revision': resolved['source_revision'], 'raw_digest': resolved.get('raw_digest'),
            'members': resolved.get('members')}


def migrate(repo: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Migrate v1 entries to v2 in one atomic step, resuming from the journal after a crash."""
    root = repo.resolve()
    preview = preview_migration(root)
    if dry_run:
        return {**preview, 'applied': False}
    registry_path = root / REGISTRY_REL
    journal = load_journal(root)
    completed = set(journal.get('completed') or [])
    if registry_path.exists() and not (root / BACKUP_REL).exists():
        try:
            (root / BACKUP_REL).write_bytes(registry_path.read_bytes())
        except OSError as exc:
            return {'status': 'ACTION_REQUIRED', 'error': 'MIGRATION_BACKUP_FAILED', 'message': str(exc),
                    'next_action': 'free_disk_or_fix_permissions'}

    registry = load_registry(root)
    journal_path = root / JOURNAL_REL
    journal['status'] = 'in_progress'
    journal['identity_version'] = IDENTITY_VERSION
    _atomic_json(journal_path, journal)

    migrated, skipped, unresolved = [], [], []
    for requirement_id, entry in (registry.get('requirements') or {}).items():
        # The registry is authoritative: an entry that already carries its source revision is
        # settled, even if a journal intent exists for it. The journal only records intent, so
        # a crash before the atomic registry write simply means the step is redone.
        if not is_legacy_entry(entry):
            skipped.append(requirement_id)
            continue
        resolution = _resolve_legacy_source(root, entry)
        if not resolution.get('source_revision'):
            unresolved.append({'requirement_id': requirement_id, **resolution})
            entry['legacy'] = True
            entry['migration_command'] = LEGACY_MIGRATION_COMMAND
            continue
        entry['source_revision'] = resolution['source_revision']
        entry['members'] = resolution.get('members') or entry.get('members') or []
        entry['identity_version'] = IDENTITY_VERSION
        entry.pop('legacy', None)
        entry.pop('legacy_reason', None)
        entry.pop('migration_command', None)
        migrated.append({'requirement_id': requirement_id, 'source_revision': resolution['source_revision']})
        completed.add(requirement_id)
        journal['completed'] = sorted(completed)
        # Every accepted step is journalled before the next one begins.
        _atomic_json(journal_path, journal)

    mark_legacy(registry)
    registry['identity_version'] = IDENTITY_VERSION
    registry['migrated_at'] = datetime.now(timezone.utc).isoformat()
    _atomic_json(registry_path, registry)
    journal['status'] = 'done' if not unresolved else 'partial'
    journal['pending'] = [item['requirement_id'] for item in unresolved]
    _atomic_json(journal_path, journal)
    return {
        'status': 'MIGRATED' if not unresolved else 'MIGRATED_PARTIAL',
        'identity_version': IDENTITY_VERSION,
        'backup_path': str(root / BACKUP_REL),
        'migrated': migrated,
        'resumed_skipped': skipped,
        'unresolved': unresolved,
        'applied': True,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path('.'))
    p.add_argument('--migrate', action='store_true', help='migrate v1 requirement identities to v2')
    p.add_argument('--dry-run', action='store_true', help='preview only; write nothing')
    args = p.parse_args(argv)
    repo = args.repo.resolve()
    if args.migrate:
        result = migrate(repo, dry_run=args.dry_run)
    else:
        result = preview_migration(repo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get('status') in {'ACTION_REQUIRED'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
