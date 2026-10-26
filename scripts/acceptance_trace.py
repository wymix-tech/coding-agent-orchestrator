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


CATEGORIES = {'functional', 'constraint', 'requirement', 'implementation_task', 'metadata'}
FUNCTIONAL = {'acceptance', 'acceptance criteria', '验收标准', '验收条件', '功能需求', 'functional requirements'}
CONSTRAINTS = {'design', 'constraints', 'non-functional requirements', '设计', '设计约束', '约束', '非功能需求', '不在范围', 'out of scope'}
TASKS = {'tasks', 'implementation tasks', 'implementation', '任务', '实现任务', '实施任务'}
METADATA = re.compile(r'^(?:需求编号|创建日期|更新日期|状态|负责人|编号|日期|requirement[ _-]?id|created(?:[ _-]at)?|updated(?:[ _-]at)?|status|owner)\s*[:：]', re.I)


def source_items(repo, path, section=None, requirement=False, catalog=False):
    raw = repo_file(repo, path).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('TASK_ACCEPTANCE_SOURCE_TOO_LARGE')
    text = raw.decode('utf-8-sig')
    lines = _lines(raw)
    if section:
        lines = _section(lines, section)
    category, include = 'requirement', bool(requirement or section)
    entries = []
    for line in lines:
        heading = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
        if heading:
            name = heading[1].casefold()
            category = ('functional' if name in FUNCTIONAL else 'constraint' if name in CONSTRAINTS
                        else 'implementation_task' if name in TASKS else 'requirement')
            include = bool(requirement or section or name in HEADINGS | FUNCTIONAL | CONSTRAINTS | TASKS)
            continue
        if not include:
            continue
        value = re.sub(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s*)?', '', line).strip()
        plain = re.sub(r'[*_`]', '', value).strip()
        metadata = bool(METADATA.match(plain) or re.fullmatch(r'<[^>]+>', plain) or plain.endswith((':', '：')))
        if (not value or not re.search(r'\w', value) or re.fullmatch(r'[\s|:\-]+', plain)
                or value.casefold() in {'todo', 'tbd', '待填写', '待补充'}):
            continue
        kind = 'metadata' if metadata else category
        if not catalog and kind in {'metadata', 'implementation_task'}:
            continue
        location = next((i for i, original in enumerate(text.splitlines(), 1) if original.strip() == line.strip()), None)
        entries.append({'id': 'src-' + digest({'path': path, 'text': value})[:24], 'source': path,
                        'description': value, 'kind': 'design' if kind == 'constraint' else 'requirement',
                        'category': kind, 'line': location, 'classification': 'structural',
                        'requires_acceptance': kind not in {'metadata', 'implementation_task'}})
    if catalog:
        header = re.match(r'\A---[ \t]*\r?\n(.*?)\r?\n---', text, re.S)
        if header:
            for index, line in enumerate(header[1].splitlines(), 2):
                if line.strip() and ':' in line:
                    value = line.strip()
                    entries.append({'id': 'src-' + digest({'path': path, 'text': value})[:24], 'source': path,
                                    'description': value, 'kind': 'requirement', 'category': 'metadata',
                                    'line': index, 'classification': 'structural', 'requires_acceptance': False})
    return list({item['id']: item for item in entries}.values())


def criteria(repo, work, catalog=False):
    result = source_items(repo, work['requirement']['path'], requirement=True, catalog=catalog)
    sdd = work.get('sdd') or {}
    binding = sdd.get('spec_binding') or {}
    if binding:
        result += source_items(repo, binding['criteria']['path'], binding.get('criteria_section'), catalog=catalog)
    declaration = (sdd.get('acceptance') or {}).get('declaration') or {}
    native_sources = set(declaration.get('sources', [])) | set((sdd.get('workflow') or {}).get('artifacts', []))
    native_sources |= {item['path'] for item in sdd.get('artifacts', []) if item.get('path')}
    for path in sorted(native_sources):
        if not path.endswith('.md'):
            continue
        if not binding or path != binding['criteria']['path']:
            result += source_items(repo, path, catalog=catalog)
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
    for key in mapping:
        mapping[key] = sorted(set(mapping[key]))
    return {'category_counts': {category: sum(item['category'] == category for item in expected) for category in CATEGORIES}, 'required': len(expected), 'covered': len({item['id'] for item in expected} & covered),
            'missing': [item for item in expected if item['id'] not in covered], 'mapping': mapping, 'unknown': unknown}
