"""Read stable BMAD project capabilities without executing provider code."""
from pathlib import Path
import re


def project_config(repo):
    paths = ['config.toml', 'config.user.toml', 'custom/config.toml', 'custom/config.user.toml']
    merged = {}
    for relative in paths:
        path = repo / '_bmad' / relative
        if not path.exists():
            continue
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError('BMAD_CONFIG_SYMLINK')
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                raise ValueError('BMAD_CONFIG_PARSER_REQUIRED: install CAO with its Python 3.10 tomli dependency')
        try:
            with path.open('rb') as stream:
                layer = tomllib.load(stream)
        except (ValueError, OSError) as exc:
            raise ValueError('BMAD_CONFIG_INVALID: ' + relative) from exc
        def merge(target, source):
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = value
        merge(merged, layer)
    return merged


def output_dirs(repo):
    repo = repo.resolve()
    config = project_config(repo)
    values = {}
    def collect(table):
        for key, value in table.items():
            if isinstance(value, dict):
                collect(value)
            elif key in {'output_folder', 'planning_artifacts', 'implementation_artifacts'}:
                if key in values:
                    raise ValueError('BMAD_OUTPUT_CONFIG_AMBIGUOUS: ' + key)
                values[key] = value
    collect(config)
    resolved = {}
    def expand(key, seen=()):
        if key in seen:
            raise ValueError('BMAD_OUTPUT_CONFIG_CYCLE')
        value = values.get(key, '_bmad-output' if key == 'output_folder' else '{output_folder}/' + key.replace('_', '-'))
        if not isinstance(value, str) or not value.strip():
            raise ValueError('BMAD_OUTPUT_CONFIG_INVALID: ' + key)
        value = value.replace('{project-root}', str(repo))
        for match in re.findall(r'\{(?:config\.)?([a-z_]+)\}', value):
            if match not in {'output_folder', 'planning_artifacts', 'implementation_artifacts'}:
                raise ValueError('BMAD_OUTPUT_CONFIG_UNRESOLVED: ' + match)
            value = value.replace('{'+match+'}', expand(match, (*seen, key)))
            value = value.replace('{config.'+match+'}', expand(match, (*seen, key)))
        if '{' in value or '}' in value:
            raise ValueError('BMAD_OUTPUT_CONFIG_UNRESOLVED: ' + key)
        path = Path(value)
        path = path if path.is_absolute() else repo / path
        if ('..' in path.parts or path.resolve() != path.absolute() or not path.resolve().is_relative_to(repo)
                or path == repo or path.relative_to(repo).parts[0] in {'.git', '.orchestrator', '_bmad', '.agents', '.pi', '.claude', '.codex', '.github', 'src', 'scripts', 'tests', 'app', 'lib', 'packages', 'modules'}):
            raise ValueError('BMAD_OUTPUT_CONFIG_UNSAFE: ' + key)
        return path.as_posix()
    for key in ('output_folder', 'planning_artifacts', 'implementation_artifacts'):
        resolved[key] = Path(expand(key)).relative_to(repo).as_posix()
    return sorted(set(['_bmad-output', *resolved.values()]))


def health(repo):
    # Preserve the existing native-workflow path for legacy BMAD installations.
    # An empty marker is never a legacy installation.
    legacy_config = repo / '_bmad/bmm/config.yaml'
    legacy_workflows = repo / '_bmad/bmm/workflows'
    has_build = any((repo / root / 'bmad-build/SKILL.md').is_file()
                    for root in ('.agents/skills', '.claude/skills', '.codex/skills', 'skills'))
    if not has_build and legacy_config.is_file() and legacy_config.stat().st_size and legacy_workflows.is_dir():
        workflows = list(legacy_workflows.rglob('workflow.yaml'))
        if workflows and all(path.stat().st_size and path.resolve() == path.absolute() for path in workflows):
            try:
                output_dirs(repo)
            except (ValueError, OSError, RuntimeError) as exc:
                return {'healthy': False, 'compatible': False, 'missing': [], 'reason': str(exc)}
            return {'healthy': True, 'compatible': True, 'missing': [], 'protocol': 'bmad-legacy-workflows', 'reason': None}
    missing = []
    for relative in ('_bmad/config.toml', '_bmad/scripts/render_skill.py', '_bmad/scripts/config_utils.py'):
        path = repo / relative
        if not path.is_file() or not path.stat().st_size or path.resolve() != path.absolute():
            missing.append(relative)
    roots = ('.agents/skills', '.claude/skills', '.codex/skills', 'skills')
    skills = [repo / root / 'bmad-build' for root in roots if (repo / root / 'bmad-build/SKILL.md').is_file()]
    if not skills:
        missing.append('bmad-build/SKILL.md')
    try:
        output_dirs(repo)
    except ValueError as exc:
        return {'healthy': False, 'compatible': False, 'missing': missing, 'reason': str(exc)}
    for skill in skills:
        for name in ('workflow.md', 'step-01-clarify-and-route.md', 'step-02-plan.md', 'step-03-implement.md',
                     'step-04-review.md', 'step-05-present.md', 'step-oneshot.md', 'spec-template.md', 'customize.toml'):
            path = skill / name
            if not path.is_file() or not path.stat().st_size or path.resolve() != path.absolute():
                missing.append(str(path.relative_to(repo)))
    try:
        compatible = bool(skills) and all((skill / 'spec-template.md').is_file() and
            'dispatch' in (skill / 'spec-template.md').read_text(encoding='utf-8-sig') for skill in skills)
    except (ValueError, OSError):
        compatible = False
    return {'healthy': not missing and compatible, 'compatible': compatible, 'missing': missing,
            'protocol': 'bmad-stable-build-v1' if compatible else None,
            'reason': 'BMAD_INSTALL_INCOMPLETE' if missing else None if compatible else 'BMAD_PROTOCOL_UNSUPPORTED'}
