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
from pathlib import Path
from typing import Any

REGISTRY_REL = Path('.orchestrator/requirements/registry.json')


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


def candidate_identity(candidate: dict[str, Any], provider: str) -> dict[str, str]:
    text = str(candidate.get('request_text') or '')
    rid = candidate.get('requirement_id') or stable_requirement_id(
        provider=provider,
        source_type=candidate.get('source_type'),
        source_path=candidate.get('path'),
        native_id=candidate.get('native_id'),
        request_text=text,
    )
    rev = candidate.get('revision_id') or revision_id(text)
    return {'requirement_id': str(rid), 'revision_id': str(rev)}


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
           source_revision: str | None = None) -> None:
    reg = load_registry(repo)
    req = reg['requirements'].setdefault(requirement_id, {
        'provider': provider, 'source_path': source_path, 'native_id': native_id,
        'revisions': {}, 'work_items': [],
    })
    req['provider'] = provider
    if source_path: req['source_path'] = source_path
    if native_id: req['native_id'] = native_id
    if source_revision: req['source_revision'] = source_revision
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
