"""Derived source index; originals and provider artifacts remain authoritative.

Only structural extraction is automated. Descriptions are source text, not
Agent-invented criteria. A report still needs real execution evidence per item.
"""
import re

from scripts.project_context import digest
from scripts.light_spec_policy import _lines, _section
from scripts.path_guard import repo_file

HEADINGS = {'acceptance', 'acceptance criteria', 'tasks & acceptance', 'requirements',
            '验收标准', '验收条件', '需求', '功能需求', 'intent'}


def source_items(repo, path, section=None, requirement=False):
    raw = repo_file(repo, path).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('TASK_ACCEPTANCE_SOURCE_TOO_LARGE')
    lines = _lines(raw)
    if section:
        lines = _section(lines, section)
    elif not requirement:
        selected, include = [], False
        for line in lines:
            heading = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
            if heading:
                include = heading[1].casefold() in HEADINGS
            elif include:
                selected.append(line)
        lines = selected
    items = []
    for line in lines:
        value = re.sub(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s*)?', '', line).strip()
        # Metadata and presentation labels are not executable requirements.
        plain = re.sub(r'[*_`]', '', value).strip()
        if (re.match(r'^(?:需求编号|创建日期|更新日期|状态|负责人|编号|日期|requirement[ _-]?id|created(?:[ _-]at)?|updated(?:[ _-]at)?|status|owner)\s*[:：]', plain, re.I)
                or re.fullmatch(r'<[^>]+>', plain)
                or plain.endswith((':', '：'))
                or re.fullmatch(r'[\s|:\-]+', plain)):
            continue
        if not value or value.startswith('#') or not re.search(r'\w', value):
            continue
        if value.casefold() in {'todo', 'tbd', '待填写', '待补充'}:
            continue
        items.append(value)
    return [{'id': 'src-' + digest({'path': path, 'text': text})[:24], 'source': path,
             'description': text, 'kind': 'requirement'} for text in dict.fromkeys(items)]


def criteria(repo, work):
    result = source_items(repo, work['requirement']['path'], requirement=True)
    sdd = work.get('sdd') or {}
    binding = sdd.get('spec_binding') or {}
    if binding:
        result += source_items(repo, binding['criteria']['path'], binding.get('criteria_section'))
    declaration = (sdd.get('acceptance') or {}).get('declaration') or {}
    native_sources = set(declaration.get('sources', [])) | set((sdd.get('workflow') or {}).get('artifacts', []))
    native_sources |= {item['path'] for item in sdd.get('artifacts', []) if item.get('path')}
    for path in sorted(native_sources):
        if not path.endswith('.md'):
            continue
        if not binding or path != binding['criteria']['path']:
            result += source_items(repo, path)
    for item in result:
        canonical = item['source']
        for move in reversed(sdd.get('archive_relocations', [])):
            destination = move['archive_root']
            if canonical == destination or canonical.startswith(destination + '/'):
                canonical = move['source_root'] + canonical[len(destination):]
        item['id'] = 'src-' + digest({'path': canonical, 'text': item['description']})[:24]
    return list({item['id']: item for item in result}.values())


def coverage(repo, work):
    expected = criteria(repo, work)
    declaration = ((work.get('sdd') or {}).get('acceptance') or {}).get('declaration') or {}
    declared = declaration.get('criteria', [])
    mapping = {item['id']: [entry['id'] for entry in declared if item['id'] in entry.get('covers', [])] for item in expected}
    unknown = sorted({identity for entry in declared for identity in entry.get('covers', [])}
                     - {item['id'] for item in expected})
    covered = {identity for identity, entries in mapping.items() if entries}
    # Exact source text permits existing native declarations without a duplicate mapping.
    for target in expected:
        if any(item['source'] == target['source'] and item['description'].strip() == target['description'] for item in declared):
            covered.add(target['id'])
            mapping[target['id']] += [item['id'] for item in declared if item['source'] == target['source'] and item['description'].strip() == target['description']]
    return {'required': len(expected), 'covered': len({item['id'] for item in expected} & covered),
            'missing': [item for item in expected if item['id'] not in covered], 'mapping': mapping, 'unknown': unknown}
