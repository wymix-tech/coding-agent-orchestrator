"""Narrow installed BMAD preparation capabilities, before route acceptance."""
import re
from pathlib import Path


def bmad_preparation(repo, tokens):
    repo = repo.resolve()
    args = list(tokens)
    if args[:2] == ['uv', 'run']:
        args = args[2:]
        while args and args[0] in {'--no-cache', '--offline', '--no-sync', '-q'}:
            args.pop(0)
    elif args and re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(args[0]).name):
        args = args[1:]
        while args and args[0] in {'-B', '-u'}:
            args.pop(0)
    else:
        return False
    if not args:
        return False
    candidate = Path(args.pop(0))
    candidate = candidate if candidate.is_absolute() else repo / candidate
    # Never trust a symlink replacement or an unrelated helper with the same name.
    try:
        if not candidate.is_file() or candidate.resolve() != Path(candidate.absolute()):
            return False
        relative = candidate.relative_to(repo).as_posix()
    except (ValueError, OSError, RuntimeError):
        return False
    from scripts.cao_provider_workflows import PROVIDER_SKILL_ROOTS
    for root in PROVIDER_SKILL_ROOTS:
        if relative.startswith(root + '/'):
            relative = '.agents/skills/' + relative[len(root) + 1:]
            break
    modes = {
        '_bmad/scripts/resolve_config.py': ({'--project-root', '--key'}, set(), set(), None),
        '_bmad/scripts/resolve_customization.py': ({'--project-root', '--skill', '--key'}, set(), set(), None),
        '_bmad/scripts/memlog.py': ({'--workspace', '--path', '--field', '--text', '--type', '--by', '--key', '--value'}, set(), {'--workspace', '--path'}, {'init', 'append', 'set'}),
        '.agents/skills/bmad-architecture/scripts/lint_spine.py': ({'--workspace', '--output', '-o'}, set(), {'--workspace', '--output', '-o'}, None),
        '.agents/skills/bmad-sprint-planning/scripts/sprint_plan.py': (
            {'--epic-file', '--status-file', '--stories-dir', '--project', '--date', '--project-key', '--tracking-system', '--story-location', '--set', '--stale-days'},
            {'--dry-run', '--fresh'}, {'--epic-file', '--status-file', '--stories-dir', '--story-location'}, {'generate', 'status', 'validate'}),
    }
    if relative not in modes:
        return False
    valued, flags, outputs, commands = modes[relative]
    if commands is not None and (not args or args.pop(0) not in commands):
        return False
    seen = set()
    while args:
        key, eq, value = args.pop(0).partition('=')
        if key in flags and not eq:
            continue
        if key not in valued:
            return False
        if not eq:
            if not args:
                return False
            value = args.pop(0)
        seen.add(key)
        if key in outputs or key in {'--project-root', '--skill'}:
            path = Path(value)
            path = path if path.is_absolute() else repo / path
            try:
                resolved = path.resolve()
                if resolved != Path(path.absolute()):
                    return False
                if key == '--project-root':
                    if resolved != repo:
                        return False
                elif key == '--skill':
                    if not any(resolved.is_relative_to(repo / root) for root in PROVIDER_SKILL_ROOTS) or not (resolved / 'SKILL.md').is_file():
                        return False
                else:
                    from scripts.bmad_project import output_dirs
                    if not any(resolved.is_relative_to(repo / root) for root in output_dirs(repo)):
                        return False
            except (OSError, ValueError, RuntimeError):
                return False
    if relative.endswith(('resolve_config.py', 'resolve_customization.py')):
        return '--project-root' in seen and (not relative.endswith('resolve_customization.py') or '--skill' in seen)
    return bool(seen & outputs)
