"""Optional source-bound project knowledge CLI."""
import json

from scripts.cao_runtime import ensure_initialized, repo_root
from scripts.cao_work_store import resolve_work
from scripts.path_guard import repo_file
from scripts import project_knowledge as knowledge


def cmd_knowledge(args):
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if args.knowledge_action == 'collect':
        if not work:
            raise ValueError('NO_WORK_ITEM')
        extra = None
        if args.candidates:
            path = repo_file(repo, args.candidates)
            if path.stat().st_size > 256 * 1024:
                raise ValueError('KNOWLEDGE_CANDIDATES_TOO_LARGE')
            extra = json.loads(path.read_text(encoding='utf-8'))
        out = knowledge.collect(repo, work, extra)
    elif args.knowledge_action == 'refresh':
        out = knowledge.refresh(repo) if knowledge.enabled(repo) else {'status': 'disabled'}
    else:
        rows = knowledge.query(repo, work, include_inactive=args.all, limit=args.limit)
        if args.knowledge_action == 'show':
            rows = [row for row in knowledge.load_store(repo)['records'].values() if row['id'] == args.id]
            for row in rows:
                if not knowledge.current(repo, row) and row['status'] == 'active':
                    row['status'] = 'stale'
        out = {'records': rows, 'note': 'Knowledge is context, never user authorization or verification evidence.'}
    print(json.dumps({'ok': True, **out}, ensure_ascii=False, indent=2))
    return 0


def register_parser(sub):
    actions = sub.add_parser('knowledge', help='Collect and inspect optional source-bound project knowledge').add_subparsers(dest='knowledge_action', required=True)
    for name in ('collect', 'list', 'show', 'refresh'):
        parser = actions.add_parser(name)
        parser.add_argument('--repo', default='.')
        parser.add_argument('--work')
        parser.add_argument('--limit', type=int, default=8)
        parser.add_argument('--all', action='store_true', help='Include candidates, conflicts and stale records')
        if name == 'show':
            parser.add_argument('--id', required=True)
        if name == 'collect':
            parser.add_argument('--candidates', help='Optional JSON suggestions; always imported as untrusted Work-scoped candidates')
        parser.set_defaults(fn=cmd_knowledge)
