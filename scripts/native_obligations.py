"""Native oneshot review evidence and local VCS completion, not semantic certification."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from scripts.light_spec_policy import _oneshot, _lines, _section
from scripts.path_guard import repo_file, provider_artifact


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def oneshot_source(repo, work):
    sdd = work.get('sdd') or {}
    binding = sdd.get('spec_binding') or {}
    if sdd.get('provider') != 'bmad':
        return None
    decision = sdd.get('route_decision') or {}
    if decision:
        if (decision.get('provider') != 'bmad'
                or decision.get('requirement_sha256') != work['requirement']['sha256']):
            raise ValueError('NATIVE_REVIEW_SOURCE_STALE')
        if decision.get('build_route') != 'oneshot':
            return None
        source = decision['primary_artifact']
    else:
        source = (binding.get('spec') or {}).get('path')
    if not source:
        return None
    path = provider_artifact(repo, source, ['_bmad-output'])
    raw = path.read_bytes()
    if not _oneshot(raw, _lines(raw)):
        if decision:
            raise ValueError('NATIVE_ONESHOT_SPEC_INVALID')
        return None
    content = raw.decode('utf-8-sig')
    frozen = re.search(r'<frozen-after-approval\b[^>]*>(.*?)</frozen-after-approval>', content, re.S)
    intent = frozen[1] if frozen else '\n'.join(_section(_lines(raw), 'Intent'))
    return {'path': path.relative_to(repo.resolve()).as_posix(), 'intent_sha256': digest(intent)}


def review_inputs(repo, work, code_snapshot):
    source = review_source(repo, work)
    if not source:
        raise ValueError('NATIVE_REVIEW_NOT_APPLICABLE')
    contract = ((work.get('sdd') or {}).get('native_reviews') or {}).get('_contract') or {}
    instructions = {path: hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest() for path in contract.get('sources', [])}
    return {'instructions': instructions, 'work_id': work['id'], 'requirement_sha256': work['requirement']['sha256'],
            'code_snapshot': code_snapshot(repo, {work['requirement']['path']}, {'_bmad-output', '_bmad', '.agents', '.pi', 'openspec', '.specify', 'specs'}), **source}


def review_state(repo, work, code_snapshot):
    try:
        if not review_source(repo, work):
            return {'ready': True, 'state': 'not_applicable'}
        implementation = implementation_state(repo, work)
        if not implementation['ready']:
            return implementation
        inputs = review_inputs(repo, work, code_snapshot)
        records = (work.get('sdd') or {}).get('native_reviews') or {}
        # Default native oneshot's Blind Hunter is not replaced by tests or a
        # manually satisfied implementation_review gate.
        layers = active_layers(repo, work)
        for layer in layers:
            run = records.get(layer) or {}
            if run.get('status') != 'passed' or run.get('inputs') != inputs:
                break
        else:
            run = records.get(layers[-1]) or {}
        next_step = ('Follow the rendered BMAD Review and Classify steps. Start `cao sdd review begin --layer <active-layer> --instructions <rendered-review-step.md> --model <current-model>`; '
                     'run the native prompt in an independent same-model reviewer (or use its external-session fallback), preserve the returned findings, '
                     'then `cao sdd review submit --layer blind-hunter --report <native-report.json>`. Fix/triage findings, review changed code again, then `cao verify`.')
        if run.get('status') != 'passed':
            return {'ready': False, 'state': 'pending', 'reason': 'NATIVE_REVIEW_REQUIRED', 'next': next_step}
        if any((records.get(layer) or {}).get('inputs') != inputs for layer in layers) or any(hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest() != sha
                                             for layer in layers for path, sha in (records.get(layer) or {}).get('evidence_hashes', {}).items()):
            return {'ready': False, 'state': 'stale', 'reason': 'NATIVE_REVIEW_STALE', 'next': next_step}
        return {'ready': True, 'state': 'recorded', 'run_id': run['id'],
                'note': 'Independent-review evidence is recorded; this is not proof of semantic correctness.'}
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        return {'ready': False, 'state': 'blocked', 'reason': str(exc), 'next': 'Restore/review the native SPEC and review evidence; rerun the native reviewer.'}


def dispatch(work):
    decision = (work.get('sdd') or {}).get('route_decision') or {}
    return decision.get('provider') == 'bmad' and decision.get('build_route') == 'dispatch'


def implementation_contract(repo, work):
    if not dispatch(work):
        return None
    for rule in work['sdd']['route_decision'].get('rules', []):
        path = repo_file(repo, repo_file(repo, rule['path']).parent / 'step-03-implement.md')
        if path.is_file():
            return {'path': path.relative_to(repo).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'subagent_default': bool(re.search(r'(?i)launch a subagent', path.read_text()))}
    return None


def implementation_state(repo, work):
    try:
        contract = implementation_contract(repo, work)
    except (ValueError, OSError, RuntimeError) as exc:
        return {'ready': False, 'reason': 'NATIVE_IMPLEMENTATION_INSTRUCTIONS_INVALID',
                'next': 'Restore the current installed native implementation step: ' + str(exc)}
    if not contract:
        return {'ready': True}
    decision = work['sdd']['route_decision']
    evidence = work['sdd'].get('native_implementation') or {}
    exception = (work['sdd'].get('workflow') or {}).get('unverified') or {}
    if exception.get('allowed') and str(exception.get('reason') or '').strip():
        return {'ready': True, 'state': 'explicit_unverified_exception'}
    if (evidence.get('contract') == contract and evidence.get('route_id') == decision['id']
            and evidence.get('requirement_sha256') == work['requirement']['sha256']):
        return {'ready': True, 'state': 'host_observed'}
    return {'ready': False, 'reason': 'NATIVE_IMPLEMENTATION_HANDOFF_REQUIRED',
            'next': 'Read the installed step-03 fully, set native status in-progress and baseline_commit, then use its actual implementation handoff. Without host observation use the existing audited --allow-unverified --reason exception; never invent historical execution.'}


def planning_state(repo, work):
    decision = (work.get('sdd') or {}).get('route_decision') or {}
    if decision.get('route') != 'full-planning' or decision.get('provider') != 'bmad':
        return {'ready': True}
    try:
        for artifact in decision.get('required_artifacts', []):
            role = artifact.get('role')
            if role not in {'prd', 'architecture'}:
                continue
            path = provider_artifact(repo, artifact['path'], ['_bmad-output'])
            text = path.read_text()
            header = re.match(r'\A---\s*\n(.*?)\n---(?:\s*\n|\Z)', text, re.S)
            statuses = re.findall(r'(?m)^status:\s*(.*?)\s*$', header[1]) if header else []
            if len(statuses) != 1 or statuses[0].strip("'\"") != 'final':
                raise ValueError('NATIVE_PLANNING_NOT_FINAL: ' + artifact['path'])
            skill = repo / '.agents/skills' / ('bmad-prd' if role == 'prd' else 'bmad-architecture') / 'SKILL.md'
            rules = skill.read_text() if skill.is_file() else ''
            if '.memlog.md' in rules:
                audit = provider_artifact(repo, path.parent / '.memlog.md', ['_bmad-output'])
                if not audit.is_file() or not audit.read_text().strip():
                    raise ValueError('NATIVE_PLANNING_AUDIT_REQUIRED: ' + artifact['path'])
            if role == 'architecture' and (work.get('path_assessment') or {}).get('profile') == 'comprehensive' and 'reviewer-gate' in rules:
                reviews = list((path.parent / 'reviews').glob('review-*.md'))
                if not reviews or any(not p.read_text().strip() for p in reviews):
                    raise ValueError('NATIVE_PLANNING_REVIEW_REQUIRED: ' + artifact['path'])
        return {'ready': True}
    except (ValueError, OSError, RuntimeError) as exc:
        return {'ready': False, 'reason': str(exc), 'next': 'Finish the installed native planning Finalize/audit/reviewer steps; Fast path does not certify completion. Preserve actual evidence, then retry.'}


def review_source(repo, work):
    if not dispatch(work):
        return oneshot_source(repo, work)
    decision = work['sdd']['route_decision']
    if decision.get('requirement_sha256') != work['requirement']['sha256']:
        raise ValueError('NATIVE_REVIEW_SOURCE_STALE')
    path = provider_artifact(repo, decision['primary_artifact'], ['_bmad-output'])
    content = path.read_text(encoding='utf-8-sig')
    frozen = re.search(r'<frozen-after-approval\b[^>]*>(.*?)</frozen-after-approval>', content, re.S)
    return {'path': path.relative_to(repo.resolve()).as_posix(), 'intent_sha256': digest(frozen[1] if frozen else content)}


def active_layers(repo, work):
    records = (work.get('sdd') or {}).get('native_reviews') or {}
    if not dispatch(work):
        return (records.get('_contract') or {}).get('layers') or ['blind-hunter']
    # Resolve the active immutable renderer snapshot, never a project-wide glob
    # which could accidentally select another run's review customization.
    rules = work['sdd']['route_decision'].get('rules', [])
    sources = [repo_file(repo, item['path']).parent / 'step-04-review.md' for item in rules]
    sources += [repo_file(repo, p) for p in (records.get('_contract') or {}).get('sources', [])]
    for source in sources:
        source = repo_file(repo, source)
        if source.name == 'step-04-review.md' and source.is_file():
            layers = sorted(set(re.findall(r'(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)', source.read_text())))
            if layers:
                return layers
    raise ValueError('NATIVE_REVIEW_INSTRUCTIONS_REQUIRED')


def dispatch_patch_state(repo, work, code_snapshot):
    """Review patches must follow classification of every active layer."""
    if not dispatch(work):
        return {'ready': True}
    records = work['sdd'].get('native_reviews') or {}
    started = [r for key, r in records.items() if key != '_contract']
    if not started:
        return {'ready': True}  # Initial implementation precedes review.
    try:
        for layer in active_layers(repo, work):
            run = records.get(layer) or {}
            if run.get('status') not in {'classified', 'passed'}:
                return {'ready': False, 'reason': 'NATIVE_REVIEW_CLASSIFY_REQUIRED'}
            if run.get('loopback_required'):
                return {'ready': False, 'reason': 'NATIVE_REVIEW_LOOPBACK_REQUIRED'}
            for path, sha in run.get('triage_hashes', {}).items():
                if hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest() != sha:
                    return {'ready': False, 'reason': 'NATIVE_REVIEW_TRIAGE_STALE'}
        return {'ready': True}
    except (ValueError, OSError, RuntimeError) as exc:
        return {'ready': False, 'reason': str(exc)}


def classify_review(repo, work, layer, report_path, code_snapshot):
    if not dispatch(work):
        raise ValueError('NATIVE_REVIEW_CLASSIFY_NOT_APPLICABLE')
    records = work['sdd'].get('native_reviews') or {}
    # No patches between collecting reviewers and their classification.
    inputs = review_inputs(repo, work, code_snapshot)
    if any((records.get(key) or {}).get('inputs') != inputs for key in active_layers(repo, work)):
        raise ValueError('NATIVE_REVIEW_ALL_LAYERS_REQUIRED')
    run = records.get(layer) or {}
    report_file = provider_artifact(repo, report_path, ['_bmad-output'])
    report = json.loads(report_file.read_text())
    validate_reviewer(run, report)
    if run.get('status') != 'running' or report.get('run_id') != run.get('id') or report.get('input_sha256') != digest(inputs):
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    findings_file = provider_artifact(repo, str(report.get('findings_file') or ''), ['_bmad-output'])
    raw = json.loads(findings_file.read_text())
    findings = report.get('findings')
    if not isinstance(raw, list) or not isinstance(findings, list):
        raise ValueError('NATIVE_REVIEW_TRIAGE_REQUIRED')
    ids = [f.get('id') for f in raw if isinstance(f, dict)]
    classified = [f.get('id') for f in findings if isinstance(f, dict)]
    if (len(ids) != len(raw) or len(classified) != len(findings)
            or any(not isinstance(i, str) or not i for i in ids + classified)
            or len(set(ids)) != len(ids) or sorted(ids) != sorted(classified)):
        raise ValueError('NATIVE_REVIEW_FINDING_MAPPING_INVALID')
    loopback = False
    for finding in findings:
        route = finding.get('disposition')
        if finding.get('verdict') not in {'high', 'medium', 'low', 'false', 'maybe-false'} or not str(finding.get('evidence') or '').strip():
            raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID')
        if route not in {'patch', 'defer', 'reject', 'bad_spec', 'intent_gap'}:
            raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID')
        if finding.get('violates_frozen_intent') is True and route not in {'bad_spec', 'intent_gap'}:
            raise ValueError('NATIVE_REVIEW_FROZEN_INTENT_LOOPBACK_REQUIRED')
        if route == 'defer' and (finding.get('scope') != 'outside-intent' or finding.get('violates_frozen_intent') is not False):
            raise ValueError('NATIVE_REVIEW_DEFER_OUTSIDE_INTENT_REQUIRED')
        if route == 'reject' and finding['verdict'] in {'high', 'medium', 'low'}:
            raise ValueError('NATIVE_REVIEW_VERIFIED_FINDING_REJECTED')
        loopback = loopback or route in {'bad_spec', 'intent_gap'}
    run.update(status='classified', reviewer=report['reviewer'], findings=findings,
               loopback_required=loopback, raw_count=len(raw), classified_count=len(findings),
               triage_hashes={p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (report_file, findings_file)})
    return run


def validate_reviewer(run, report):
    reviewer = report.get('reviewer') or {}
    if (not isinstance(reviewer, dict) or reviewer.get('mode') not in {'subagent', 'external-session'}
            or not str(reviewer.get('session_id') or '').strip() or reviewer.get('session_id') in run.get('primary_session_ids', [])
            or reviewer.get('model') != run.get('model')):
        raise ValueError('NATIVE_REVIEW_INDEPENDENT_MODEL_REQUIRED')


def resolve_review(repo, work, layer, report_path, code_snapshot):
    state = dispatch_patch_state(repo, work, code_snapshot)
    if not state['ready']:
        raise ValueError(state['reason'])
    run = work['sdd']['native_reviews'].get(layer) or {}
    if run.get('status') != 'classified':
        raise ValueError('NATIVE_REVIEW_CLASSIFY_REQUIRED')
    path = provider_artifact(repo, report_path, ['_bmad-output'])
    report = json.loads(path.read_text())
    resolved = report.get('resolved_ids')
    expected = [f['id'] for f in run['findings'] if f['disposition'] == 'patch']
    if report.get('run_id') != run['id'] or not isinstance(resolved, list) or sorted(resolved) != sorted(expected):
        raise ValueError('NATIVE_REVIEW_UNRESOLVED')
    current = review_inputs(repo, work, code_snapshot)
    if any(current.get(k) != run['inputs'].get(k) for k in current if k != 'code_snapshot'):
        raise ValueError('NATIVE_REVIEW_SOURCE_STALE')
    run.update(status='passed', inputs=current, evidence_hashes={**run['triage_hashes'], path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()})
    return run


def submit_review(repo, work, layer, report_path, code_snapshot):
    if dispatch(work):
        raise ValueError('NATIVE_REVIEW_CLASSIFY_REQUIRED: dispatch uses classify before patches, then resolve')
    run = (work.get('sdd') or {}).get('native_reviews', {}).get(layer) or {}
    report_file = provider_artifact(repo, report_path, ['_bmad-output'])
    report = json.loads(report_file.read_text())
    if not isinstance(report, dict):
        raise ValueError('NATIVE_REVIEW_REPORT_INVALID')
    if (run.get('status') != 'running' or report.get('run_id') != run.get('id')
            or report.get('input_sha256') != digest(run.get('inputs'))
            or run.get('inputs') != review_inputs(repo, work, code_snapshot)):
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    reviewer = report.get('reviewer') or {}
    if not isinstance(reviewer, dict):
        raise ValueError('NATIVE_REVIEW_REPORT_INVALID')
    if (reviewer.get('mode') not in {'subagent', 'external-session'} or not str(reviewer.get('session_id') or '').strip()
            or reviewer.get('session_id') in run.get('primary_session_ids', [])
            or reviewer.get('model') != run.get('model')):
        raise ValueError('NATIVE_REVIEW_INDEPENDENT_MODEL_REQUIRED')
    findings_file = provider_artifact(repo, str(report.get('findings_file') or ''), ['_bmad-output'])
    if findings_file == report_file or not findings_file.read_text().strip():
        raise ValueError('NATIVE_REVIEW_FINDINGS_REQUIRED')
    findings = report.get('findings')
    if not isinstance(findings, list) or not findings or len(findings) != len(re.findall(r'(?m)^ {0,3}(?:[-*+]|[0-9]+[.)])\s+\S', findings_file.read_text())):
        raise ValueError('NATIVE_REVIEW_TRIAGE_REQUIRED')
    ids = set()
    for finding in findings:
        if (not isinstance(finding, dict) or not finding.get('id') or finding['id'] in ids
                or finding.get('verdict') not in {'high', 'medium', 'low', 'false', 'maybe-false'}
                or finding.get('disposition') not in {'patch', 'defer', 'reject'}
                or not str(finding.get('evidence') or '').strip()
                or (finding['verdict'] in {'high', 'medium'} and finding['disposition'] == 'reject')
                or (finding['disposition'] == 'patch' and finding.get('resolved') is not True)):
            raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID_OR_UNRESOLVED')
        ids.add(finding['id'])
    run.update(status='passed', report=report_file.relative_to(repo.resolve()).as_posix(), reviewer=reviewer,
               evidence_hashes={path.relative_to(repo.resolve()).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (report_file, findings_file)})
    return run


def commit_state(repo, work, ignored_dirs):
    try:
        if not oneshot_source(repo, work) or not shutil.which('git'):
            return {'ready': True, 'state': 'not_applicable'}
        exists = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
        if exists.returncode:
            return {'ready': True, 'state': 'unavailable'}
        source = oneshot_source(repo, work)
        root = Path(exists.stdout.strip()).resolve()
        spec = (repo.resolve() / source['path']).relative_to(root).as_posix()
        def git(*args):
            return subprocess.run(['git', '-C', str(root), *args], capture_output=True)
        def blocked(code):
            return {'ready': False, 'state': 'pending', 'reason': code, 'spec': source['path'],
                    'next': 'Inspect SPEC tracking and Git diff; explicitly stage the intended SPEC and business changes, then commit. For ignored SPEC, review your ignore policy before staging; CAO never force-adds. Retry `cao done` without reanchoring solely for the commit.'}
        if git('ls-files', '--error-unmatch', '--', spec).returncode:
            ignored = git('check-ignore', '--quiet', '--', spec).returncode == 0
            return blocked('NATIVE_SPEC_IGNORED' if ignored else 'NATIVE_SPEC_UNTRACKED')
        if git('cat-file', '-e', 'HEAD:' + spec).returncode:
            return blocked('NATIVE_SPEC_NOT_IN_HEAD')
        # Git's own diff honors index flags, CRLF and clean filters; raw bytes do not.
        dirty = git('diff', '--quiet', 'HEAD', '--', spec)
        staged = git('diff', '--cached', '--quiet', 'HEAD', '--', spec)
        # Refresh explicitly with Git-cleaned content to avoid assume-unchanged/skip-worktree bypass.
        blob = git('hash-object', '--path=' + spec, '--', str(root / spec))
        head_blob = git('rev-parse', 'HEAD:' + spec)
        if dirty.returncode or staged.returncode or blob.returncode or head_blob.returncode or blob.stdout.strip() != head_blob.stdout.strip():
            return blocked('NATIVE_SPEC_UNCOMMITTED')
        status = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--', '.'], capture_output=True, check=True).stdout
        changed = []
        entries = status.split(b'\0'); i = 0
        while i < len(entries):
            entry = entries[i]; i += 1
            if not entry: continue
            state, path = entry[:2], entry[3:].decode('utf-8')
            paths = [path]
            if b'R' in state or b'C' in state:
                if i < len(entries): paths.append(entries[i].decode('utf-8')); i += 1
            for value in paths:
                prefix = repo.resolve().relative_to(Path(exists.stdout.strip()).resolve()).as_posix()
                if prefix != '.': value = Path(value).relative_to(prefix).as_posix()
                if value != source['path'] and not any(part in ignored_dirs for part in Path(value).parts):
                    changed.append(value)
        if changed:
            return {'ready': False, 'state': 'pending', 'reason': 'NATIVE_COMMIT_REQUIRED', 'changed': changed[:20],
                    'next': 'Follow the native Commit step: inspect `git status`/diff, stage the intended Work changes and commit them. Do not push unless authorized. Then retry `cao done`; do not edit CAO state or reanchor solely for the commit.'}
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        return {'ready': head.returncode == 0, 'state': 'committed' if head.returncode == 0 else 'pending',
                'reason': None if head.returncode == 0 else 'NATIVE_COMMIT_REQUIRED', 'head': head.stdout.strip()}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {'ready': False, 'state': 'blocked', 'reason': 'NATIVE_COMMIT_CHECK_FAILED', 'detail': str(exc)}
