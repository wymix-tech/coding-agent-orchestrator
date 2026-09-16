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
import shlex
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


def _normalize_title(title: str) -> str:
    """Comparable heading text: case, punctuation and trailing enumeration do not matter."""
    cleaned = title.replace('`', '').replace('*', '').strip().lower()
    while cleaned and cleaned[-1] in {':', '.', ')'}:
        cleaned = cleaned[:-1].strip()
    return ' '.join(cleaned.split())


def _outline(text: str) -> list[dict[str, Any]]:
    """Parse markdown into a heading tree. Level, parent chain and preamble are preserved.

    Flattening headings loses the only information that distinguishes "## Tasks" from
    "### Implementation" inside it, and loses prose written before the first heading.
    """
    sections: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    preamble: list[str] = []
    current: dict[str, Any] | None = None
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#') and len(stripped) > 1 and stripped.lstrip('#').startswith((' ', '\t')):
            level = len(stripped) - len(stripped.lstrip('#'))
            while stack and stack[-1]['level'] >= level:
                stack.pop()
            parent = stack[-1] if stack else None
            current = {
                'level': level,
                'title': stripped.lstrip('#').strip(),
                'parents': [p['title'] for p in stack],
                'parent_classification': (parent or {}).get('inherited_classification'),
                'body': [],
            }
            sections.append(current)
            stack.append(current)
            continue
        target = current['body'] if current is not None else preamble
        target.append(line)
    result: list[dict[str, Any]] = []
    if any(line.strip() for line in preamble):
        # Prose before the first heading is requirement content, not runtime noise.
        result.append({'level': 0, 'title': '(preamble)', 'parents': [],
                       'parent_classification': None, 'body': '\n'.join(preamble)})
    for section in sections:
        section['body'] = '\n'.join(section['body'])
        result.append(section)
    return result


# Known spellings of one and the same section. A multi-word heading such as
# "Tasks / Subtasks" still has to match exactly; what must not happen is "Status" claiming
# "Status API" because it appears somewhere inside it.
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "tasks": ("tasks", "tasks / subtasks", "tasks and subtasks", "tasks & subtasks", "subtasks",
              "checklist"),
    "change log": ("change log", "changelog", "change history"),
    "dev agent record": ("dev agent record", "dev agent records", "agent model used",
                         "debug log references", "completion notes list", "implementation notes"),
    "status": ("status",),
}


def _expand_names(names: tuple[str, ...] | list[str]) -> set[str]:
    expanded: set[str] = set()
    for name in names:
        raw = str(name or "").strip()
        if not raw:
            continue
        normalized = _normalize_title(raw)
        expanded.add(normalized)
        expanded.update(_normalize_title(alias) for alias in SECTION_ALIASES.get(normalized, ()))
    return expanded


def _exact_match(title: str, names: tuple[str, ...] | list[str]) -> bool:
    """Headings are matched in full; a substring like "Status" never claims "Status API"."""
    return _normalize_title(title) in _expand_names(names)


def _classify(section: dict[str, Any], runtime_names: list[str],
              requirement_names: list[str]) -> tuple[str, str]:
    """Return (classification, reason) for one heading, honouring parent ownership."""
    if _exact_match(section['title'], runtime_names):
        return 'runtime', 'heading matches a runtime section exactly'
    if _exact_match(section['title'], requirement_names):
        return 'requirement', 'heading matches a requirement section exactly'
    if section.get('parent_classification') == 'runtime':
        # A subsection of "Tasks" carries its parent's runtime ownership: ticking
        # "### Implementation" under it must never look like a requirement change.
        return 'runtime', 'inherited from a runtime parent section'
    if section.get('parent_classification') == 'requirement':
        return 'requirement', 'inherited from a requirement parent section'
    return 'requirement', 'unknown heading kept as requirement content (never dropped)'


def _requirement_region(repo: Path, source_ref: str, raw: bytes, *, boundary: dict | None) -> dict:
    """Split one mixed-content file into a requirement region and a runtime region.

    Runtime sections (task tick-boxes, dev records, change logs) are excluded from the
    content revision; every other section is kept, so content is never silently dropped.
    Unrecognised headings stay in the requirement content and are reported, so a missing
    boundary declaration is visible instead of changing revisions behind someone's back.
    """
    boundary = boundary or {}
    runtime_names = [str(n) for n in (boundary.get('runtime_sections') or RUNTIME_SECTIONS)]
    requirement_names = [str(n) for n in (boundary.get('requirement_sections') or REQUIREMENT_SECTIONS)]
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return {'error': 'MIXED_CONTENT_UNMAPPED', 'message': 'source is not UTF-8 text; declare an explicit boundary',
                'next_action': 'configure_content_boundary'}
    sections = _outline(text)
    if not sections:
        return {'content': _canonical_text(text), 'boundary': 'whole_file', 'runtime_sections': [],
                'requirement_sections': [], 'diagnostics': []}
    classified: list[tuple[dict[str, Any], str, str]] = []
    running: dict[int, str | None] = {}
    for section in sections:
        inherited = None
        for level in sorted((lv for lv in running if lv < section['level']), reverse=True):
            if running[level]:
                inherited = running[level]
                break
        if inherited:
            section['parent_classification'] = inherited
        classification, reason = _classify(section, runtime_names, requirement_names)
        running[section['level']] = classification
        for level in [lv for lv in running if lv > section['level']]:
            running.pop(level, None)
        classified.append((section, classification, reason))

    runtime_present = [s['title'] for s, c, _ in classified if c == 'runtime' and s['level'] > 0
                       and _exact_match(s['title'], runtime_names)]
    if not runtime_present:
        # Nothing mixes runtime progress into this document; keep the historical whole-file
        # revision so existing baselines stay comparable.
        return {'content': _canonical_text(text), 'boundary': 'whole_file', 'runtime_sections': [],
                'requirement_sections': [], 'diagnostics': []}

    kept, dropped, unmapped = [], [], []
    for section, classification, reason in classified:
        path = ' > '.join([*section['parents'], section['title']]).strip()
        if classification == 'runtime':
            dropped.append(path)
            continue
        kept.append((path, section['body']))
        if (not _exact_match(section['title'], requirement_names)
                and 'unknown heading' in reason and section['title'] != '(preamble)'):
            # Only genuinely unrecognised headings are reported. Sections that inherit their
            # parent's ownership are understood, they just do not need their own entry.
            unmapped.append({'heading': path, 'reason': reason})
    if dropped and not kept:
        return {'error': 'MIXED_CONTENT_NO_REQUIREMENT_REGION',
                'message': 'every section of this source is runtime content; declare an explicit boundary',
                'next_action': 'configure_content_boundary', 'runtime_sections': dropped}
    content = '\n\n'.join(f'{path}\n{_canonical_text(body)}' for path, body in kept)
    return {
        'content': content,
        'boundary': 'explicit' if boundary else 'default_headings',
        'requirement_sections': [path for path, _ in kept],
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


CONFIRMATION_BINDINGS = ('requirement_id', 'source_revision', 'incoming_source_revision', 'state_revision')


def confirmation_command(requirement_id: str, *, active_revision: str | None,
                         incoming_source_revision: str | None, state_revision: str | None,
                         phase: str | None = None, status: str | None = None,
                         repo: str | None = None, request_ref: str | None = None,
                         request: str | None = None) -> str:
    """The complete, runnable confirmation command for exactly this move.

    A printed command has to actually run: it names the entry point, the project, and the
    request it is confirming. Values are quoted, so a revision or a sentence with spaces
    survives being copied back into a shell.
    """
    def quote(value: Any) -> str:
        return shlex.quote(str(value))

    # `--repo` belongs to the front controller, not to `intake`: argparse rejects it after the
    # subcommand. The entry point is resolved where the Skill actually is, so the command runs
    # from any working directory instead of assuming `scripts/` is below the current one.
    # Even the fallback names this file's own directory: a command that assumes `scripts/` is
    # below the current working directory does not run from anywhere else.
    prefix = f"python3 {quote(Path(__file__).resolve().parent / 'coding_orchestrator.py')}"
    if repo:
        try:
            runtime = __import__("skill_runtime")
            prefix = runtime.recovery_command(Path(repo).resolve(), "").strip()
        except Exception:  # pragma: no cover - fallback keeps the hint printable
            prefix = f"{prefix} --repo {quote(repo)}"
    parts = [prefix, "intake"]
    if request_ref:
        parts.append(f"--request-file {quote(request_ref)}")
    elif request:
        # `intake` takes the request as a positional argument; there is no `--request` option.
        parts.append(quote(request))
    parts += [
        f"--requirement-id {quote(requirement_id or '<requirement-id>')}",
        "--revise-current --confirm-reset",
        f"--confirm-revision {quote(active_revision or '<old-active-source-revision>')}",
        f"--confirm-incoming-revision {quote(incoming_source_revision or '<new-source-revision>')}",
        f"--confirm-state-revision {quote(state_revision if state_revision is not None else '<observed-state-revision>')}",
    ]
    if phase:
        parts.append(f"--confirm-phase {quote(phase)}")
    if status:
        parts.append(f"--confirm-status {quote(status)}")
    return " ".join(parts)


def check_revision_confirmation(repo: Path, *, requirement_id: str, source_revision: str | None,
                                phase: str | None, status: str | None,
                                confirmation: dict[str, Any] | None,
                                active_revision: str | None = None,
                                state_revision: str | None = None,
                                repo_ref: str | None = None,
                                request_ref: str | None = None,
                                request: str | None = None) -> dict[str, Any]:
    """A destructive-revision confirmation is valid only for the exact move it was issued for.

    All four bindings are required: which requirement, which *old* source revision it was
    issued against, which *incoming* source revision it approves, and which execution state
    revision was observed. An old confirmation therefore cannot keep approving later source
    or state changes, and phase/status are diagnostics that never replace the state revision.
    """
    def required_shell(error: str, message: str, **extra: Any) -> dict[str, Any]:
        return {'allowed': False, 'error': error, 'message': message,
                'required_bindings': list(CONFIRMATION_BINDINGS),
                'confirm_command': confirmation_command(
                    requirement_id, active_revision=active_revision or last_source_revision(repo, requirement_id),
                    incoming_source_revision=source_revision, state_revision=state_revision,
                    phase=phase, status=status, repo=repo_ref or str(repo), request_ref=request_ref,
                    request=request),
                'next_action': 'confirm_requirement_revision', **extra}

    if not confirmation:
        return required_shell('REVISION_CONFIRMATION_REQUIRED',
                              'confirming a destructive revision requires the full binding set.')
    if str(confirmation.get('requirement_id') or '') != str(requirement_id):
        return required_shell('REVISION_CONFIRMATION_MISMATCH',
                              'the confirmation was issued for a different requirement.',
                              confirmed_requirement_id=confirmation.get('requirement_id'))
    missing = [name for name in CONFIRMATION_BINDINGS if str(confirmation.get(name) or '') == '']
    missing = [m for m in missing if m != 'requirement_id']
    if missing:
        return required_shell('REVISION_CONFIRMATION_REQUIRED',
                              'the confirmation is missing required bindings.', missing_bindings=missing)
    recorded = last_source_revision(repo, requirement_id)
    old_revisions = {str(value) for value in (active_revision, recorded) if value}
    if old_revisions and str(confirmation.get('source_revision')) not in old_revisions:
        return required_shell('REVISION_CONFIRMATION_SUPERSEDED',
                              'the confirmation was issued against an older source revision.',
                              confirmed_source_revision=confirmation.get('source_revision'),
                              current_source_revision=source_revision)
    if source_revision and str(confirmation.get('incoming_source_revision')) != str(source_revision):
        return required_shell('REVISION_CONFIRMATION_SUPERSEDED',
                              'the requirement source moved after the confirmation was issued; '
                              'a confirmation approves one incoming revision only.',
                              approved_incoming_revision=confirmation.get('incoming_source_revision'),
                              current_source_revision=source_revision)
    if state_revision is not None and str(confirmation.get('state_revision')) != str(state_revision):
        return required_shell('REVISION_CONFIRMATION_SUPERSEDED',
                              'the execution state moved after the confirmation was issued; '
                              're-confirm against the current state.',
                              confirmed_state_revision=confirmation.get('state_revision'),
                              current_state_revision=state_revision)
    # Phase and status are diagnostics only: they describe the state, they never replace the
    # state revision binding, and a mismatch always means re-confirmation.
    if phase is not None and confirmation.get('phase') is not None and str(confirmation.get('phase')) != str(phase):
        return required_shell('REVISION_CONFIRMATION_SUPERSEDED',
                              'the work item phase changed after the confirmation was issued.',
                              confirmed_phase=confirmation.get('phase'), current_phase=phase)
    if status is not None and confirmation.get('status') is not None and str(confirmation.get('status')) != str(status):
        return required_shell('REVISION_CONFIRMATION_SUPERSEDED',
                              'the work item status changed after the confirmation was issued.',
                              confirmed_status=confirmation.get('status'), current_status=status)
    return {'allowed': True}


def _alias_sources(alias: dict[str, Any] | None) -> list[str]:
    """The history entries an alias claims to carry over. Both shapes exist: one key, or many."""
    if not isinstance(alias, dict):
        return []
    sources = alias.get('from')
    if isinstance(sources, list):
        return [str(item) for item in sources if item is not None]
    return [str(sources)] if sources is not None else []


def _alias_links(aliases: dict[str, Any], *, from_key: str, to_revision: str) -> bool:
    """Does a justified alias carry `from_key`'s history over to `to_revision`?

    Aliases are keyed by the revision they were written for, not by the history they came
    from, so the lookup has to walk them: keying by the old revision silently misses all of
    them, and a missing alias then looks like "not completed".
    """
    for alias in (aliases or {}).values():
        if not isinstance(alias, dict) or not alias.get('justified'):
            continue
        if str(alias.get('to')) != str(to_revision):
            continue
        if str(from_key) in _alias_sources(alias):
            return True
    return False


def historical_mapping(repo: Path, requirement_id: str, *, content_revision: str | None) -> dict[str, Any]:
    """How pre-migration history relates to the current content revision.

    Migration may never assume that today's file content is the content past work was done
    against. History is only carried over when it is provable: the recorded raw digest has to
    match the content now, or the old key has to already be the current content revision.
    Otherwise the entry stays completed but is marked as awaiting an explicit confirmation,
    so it is neither silently re-opened nor silently marked done.
    """
    reg = load_registry(repo)
    entry = (reg.get('requirements') or {}).get(requirement_id) or {}
    revisions = entry.get('revisions') or {}
    mapping: dict[str, Any] = {
        'requirement_id': requirement_id,
        'content_revision': content_revision,
        'derived_from': None,
        'justified_by': None,
        'pending_confirmation': [],
        'next_action': None,
    }
    if not content_revision or not revisions:
        return mapping
    direct = revisions.get(content_revision) or {}
    if direct.get('status') in {'completed', 'archived'}:
        mapping['derived_from'] = content_revision
        mapping['justified_by'] = 'direct content revision'
        return mapping
    current_raw = None
    if entry.get('source_path') or entry.get('members'):
        resolved = requirement_content_revision(Path(repo).resolve(), entry.get('source_path'),
                                                members=entry.get('members'))
        current_raw = resolved.get('raw_digest')
    completed_keys = [key for key, value in revisions.items()
                      if isinstance(value, dict) and value.get('status') in {'completed', 'archived'}]
    aliases = entry.get('revision_aliases') or {}
    for key in completed_keys:
        # Only what that revision recorded about itself is evidence of what it was. The digest
        # of the file *now* is not: it says nothing about the content the work was done against.
        recorded_raw = (revisions.get(key) or {}).get('raw_digest')
        if _alias_links(aliases, from_key=key, to_revision=content_revision):
            mapping['derived_from'] = key
            mapping['justified_by'] = 'recorded migration alias or explicit confirmation'
            return mapping
        if recorded_raw and current_raw and recorded_raw == current_raw:
            continue
        mapping['pending_confirmation'].append(key)
    if mapping['pending_confirmation']:
        mapping['next_action'] = (
            f"python3 scripts/requirement_identity.py --repo . --confirm-history "
            f"{requirement_id} --revision {content_revision}")
    return mapping


def processed(repo: Path, requirement_id: str, revision_id: str) -> bool:
    """Has this content revision been completed already, without assuming history?"""
    reg = load_registry(repo)
    entry = (reg.get('requirements') or {}).get(requirement_id) or {}
    revisions = entry.get('revisions') or {}
    rev = revisions.get(revision_id) or {}
    if rev.get('status') in {'completed', 'archived'}:
        return True
    alias = (entry.get('revision_aliases') or {}).get(revision_id)
    if isinstance(alias, dict) and alias.get('justified'):
        # `from` may name one history entry or several; str() of a list is not a revision key.
        for source in _alias_sources(alias):
            old = revisions.get(source) or {}
            if old.get('status') in {'completed', 'archived'}:
                return True
    return False


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


def _map_history(repo: Path, entry: dict[str, Any], resolution: dict[str, Any],
                 previous_raw: str | None) -> dict[str, Any]:
    """Carry completion history over only when the mapping is provable.

    What can be proven here: the raw content digest recorded before a change still describes
    the content now, or history was already keyed by this content revision. What cannot be
    proven is left pending with a command, instead of being inherited silently.
    """
    new_key = str(resolution.get('source_revision') or '')
    raw_now = resolution.get('raw_digest')
    revisions = entry.get('revisions') or {}
    aliases = dict(entry.get('revision_aliases') or {})
    # The baseline is what was true *before* this migration. Writing the digest just read back
    # over it would make history appear provable by definition, on the next run and forever.
    updates: dict[str, Any] = {'baseline_raw_digest': previous_raw or entry.get('baseline_raw_digest'),
                              'revision_aliases': aliases}
    justified_by = None
    pending: list[str] = []
    for key, value in revisions.items():
        if not isinstance(value, dict) or value.get('status') not in {'completed', 'archived'}:
            continue
        if key == new_key:
            aliases[new_key] = {'from': key, 'to': new_key, 'justified': True,
                                'justified_by': 'history was already keyed by this content revision'}
            justified_by = justified_by or 'existing content revision key'
            continue
        recorded = value.get('raw_digest') or previous_raw
        if recorded and raw_now and str(recorded) == str(raw_now):
            aliases[new_key] = {'from': key, 'to': new_key, 'justified': True,
                                'justified_by': 'raw content digest recorded before migration '
                                                'matches the content now'}
            justified_by = justified_by or 'recorded raw digest matches current content'
        else:
            pending.append(key)
    if pending:
        updates['history_baseline'] = 'unknown'
        updates['pending_history_confirmation'] = True
        updates['history_next_action'] = (
            f"python3 scripts/requirement_identity.py --repo . --confirm-history "
            f"{entry.get('requirement_id') or '<requirement-id>'} --revision {new_key} "
            f"--actor <operator>")
    elif justified_by:
        updates['history_baseline'] = 'justified'
        updates['pending_history_confirmation'] = False
    return {'entry_updates': updates, 'justified_by': justified_by, 'pending': pending}


def confirm_history(repo: Path, *, requirement_id: str, content_revision: str, actor: str) -> dict[str, Any]:
    """Record that an operator compared the history and confirmed it applies to this content.

    This records who decided and against which digest. It does not pretend to be mechanical
    proof, and it never runs silently: the operator must say who they are.
    """
    root = repo.resolve()
    registry = load_registry(root)
    entry = (registry.get('requirements') or {}).get(requirement_id)
    if entry is None:
        return {'status': 'ACTION_REQUIRED', 'error': 'REQUIREMENT_UNKNOWN',
                'message': f'{requirement_id} is not in the registry', 'applied': False}
    if not actor or str(actor).strip().lower() in {'unspecified', 'agent', 'unknown'}:
        return {'status': 'ACTION_REQUIRED', 'error': 'HISTORY_CONFIRMATION_ACTOR_REQUIRED',
                'message': 'an explicit human or named agent must own this confirmation (--actor)',
                'next_action': 'confirm_history_mapping', 'applied': False}
    resolution = _resolve_legacy_source(root, entry)
    raw_now = resolution.get('raw_digest')
    if not raw_now:
        return {'status': 'ACTION_REQUIRED', 'error': 'SOURCE_UNREADABLE',
                'message': 'the requirement source cannot be read, so history cannot be compared',
                'next_action': 'repair_source_members', 'applied': False}
    # The confirmation is issued for the content revision that is current *now*. Confirming a
    # revision that no longer describes the source would bind history to the wrong content.
    current_revision = resolution.get('source_revision')
    if current_revision and str(current_revision) != str(content_revision):
        return {'status': 'ACTION_REQUIRED', 'error': 'HISTORY_CONFIRMATION_REVISION_MISMATCH',
                'message': (f'the source now reads as {current_revision}, not the confirmed '
                            f'{content_revision}; re-issue the confirmation against the current content'),
                'current_content_revision': current_revision,
                'next_action': 'confirm_history_mapping', 'applied': False}
    pending_entries = sorted(
        key for key, value in (entry.get('revisions') or {}).items()
        if isinstance(value, dict) and value.get('status') in {'completed', 'archived'}
        and str(key) != str(content_revision))
    if not pending_entries:
        return {'status': 'ACTION_REQUIRED', 'error': 'HISTORY_NOTHING_PENDING',
                'message': f'{requirement_id} has no completed history entry awaiting confirmation',
                'next_action': 'confirm_history_mapping', 'applied': False}
    aliases = dict(entry.get('revision_aliases') or {})
    aliases[str(content_revision)] = {
        'from': pending_entries,
        'to': str(content_revision),
        'confirmed_content_revision': str(current_revision or content_revision),
        'confirmed_raw_digest': raw_now,
        'justified': True,
        'justified_by': f'explicit history confirmation by {actor} against raw digest {raw_now[:12]}',
        'confirmed_by': actor,
        'confirmed_at': datetime.now(timezone.utc).isoformat(),
    }
    entry['revision_aliases'] = aliases
    entry['history_baseline'] = 'confirmed_by_operator'
    entry['pending_history_confirmation'] = False
    entry.pop('history_next_action', None)
    save_registry(root, registry)
    return {'status': 'HISTORY_CONFIRMED', 'requirement_id': requirement_id,
            'revision': content_revision, 'raw_digest': raw_now, 'confirmed_by': actor,
            'applied': True}


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
        previous_raw = entry.get('baseline_raw_digest')
        entry['source_revision'] = resolution['source_revision']
        entry['members'] = resolution.get('members') or entry.get('members') or []
        entry['identity_version'] = IDENTITY_VERSION
        entry.pop('legacy', None)
        entry.pop('legacy_reason', None)
        entry.pop('migration_command', None)
        history = _map_history(root, entry, resolution, previous_raw)
        entry.update({key: value for key, value in history['entry_updates'].items()})
        migrated.append({'requirement_id': requirement_id,
                         'source_revision': resolution['source_revision'],
                         'history_derivation': history['justified_by'] or 'awaiting_explicit_confirmation',
                         'pending_history_confirmation': bool(history['pending'])})
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
    p.add_argument('--confirm-history', help='record an explicit history-to-content mapping '
                                             'for one requirement after comparing the sources')
    p.add_argument('--revision', help='content revision a --confirm-history decision applies to')
    p.add_argument('--actor', default='unspecified', help='who owns --confirm-history')
    args = p.parse_args(argv)
    repo = args.repo.resolve()
    if args.confirm_history:
        result = confirm_history(repo, requirement_id=args.confirm_history,
                                 content_revision=args.revision or '', actor=args.actor)
    elif args.migrate:
        result = migrate(repo, dry_run=args.dry_run)
    else:
        result = preview_migration(repo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get('status') in {'ACTION_REQUIRED'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
