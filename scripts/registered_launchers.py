"""Recognize explicitly registered project CAO installations across host copies."""
import json
from pathlib import Path


def project_launchers(cwd):
    cwd = Path(cwd).resolve()
    result = set()
    for repo in (cwd, *cwd.parents):
        settings = repo / '.pi/settings.json'
        if not settings.is_file():
            continue
        try:
            raw = json.loads(settings.read_text(encoding='utf-8'))
            for entry in raw.get('packages', []):
                source = entry.get('source') if isinstance(entry, dict) else entry
                if not isinstance(source, str) or source.startswith(('npm:', 'git:', 'https:')):
                    continue
                root = Path(source).expanduser()
                if not root.is_absolute():
                    root = settings.parent / root
                root = root.resolve()
                package = json.loads((root / 'package.json').read_text(encoding='utf-8'))
                if (package.get('name') == 'coding-agent-orchestrator'
                        and (root / 'scripts/cao.py').is_file()
                        and (root / 'hosts/pi/coding-orchestrator.ts').is_file()
                        and (root / 'cao').is_file()):
                    result.add(str((root / 'cao').resolve()))
        except (ValueError, OSError, TypeError):
            continue
        break
    return result
