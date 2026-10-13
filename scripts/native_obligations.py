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
    if sdd.get('provider') != 'bmad' or not binding.get('spec'):
        return None
    path = provider_artifact(repo, binding['spec']['path'], ['_bmad-output'])
    raw = path.read_bytes()
    if not _oneshot(raw, _lines(raw)):
        return None
    content = raw.decode('utf-8-sig')
    frozen = re.search(r'<frozen-after-approval\b[^>]*>(.*?)</frozen-after-approval>', content, re.S)
    intent = frozen[1] if frozen else '\n'.join(_section(_lines(raw), 'Intent'))
    return {'path': path.relative_to(repo.resolve()).as_posix(), 'intent_sha256': digest(intent)}


def review_inputs(repo, work, code_snapshot):
    source = oneshot_source(repo, work)
    if not source:
        raise ValueError('NATIVE_REVIEW_NOT_APPLICABLE')
    contract = ((work.get('sdd') or {}).get('native_reviews') or {}).get('_contract') or {}
    instructions = {path: hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest() for path in contract.get('sources', [])}
    return {'instructions': instructions, 'work_id': work['id'], 'requirement_sha256': work['requirement']['sha256'],
            'code_snapshot': code_snapshot(repo, {work['requirement']['path']}, {'_bmad-output', '_bmad', '.agents', '.pi', 'openspec', '.specify', 'specs'}), **source}


def review_state(repo, work, code_snapshot):
    try:
        if not oneshot_source(repo, work):
            return {'ready': True, 'state': 'not_applicable'}
        inputs = review_inputs(repo, work, code_snapshot)
        records = (work.get('sdd') or {}).get('native_reviews') or {}
        # Default native oneshot's Blind Hunter is not replaced by tests or a
        # manually satisfied implementation_review gate.
        layers = (records.get('_contract') or {}).get('layers') or ['blind-hunter']
        for layer in layers:
            run = records.get(layer) or {}
            if run.get('status') != 'passed' or run.get('inputs') != inputs:
                break
        else:
            run = records.get(layers[-1]) or {}
        next_step = ('Follow the rendered BMAD oneshot Review and Classify steps. Start `cao sdd review begin --layer <active-layer> --instructions <rendered-step-oneshot.md> --model <current-model>`; '
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


def submit_review(repo, work, layer, report_path, code_snapshot):
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
                if value == oneshot_source(repo, work)['path'] or not any(part in ignored_dirs for part in Path(value).parts):
                    changed.append(value)
        if changed:
            return {'ready': False, 'state': 'pending', 'reason': 'NATIVE_COMMIT_REQUIRED', 'changed': changed[:20],
                    'next': 'Follow the native Commit step: inspect `git status`/diff, stage the intended Work changes and commit them. Do not push unless authorized. Then retry `cao done`; do not edit CAO state or reanchor solely for the commit.'}
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        return {'ready': head.returncode == 0, 'state': 'committed' if head.returncode == 0 else 'pending',
                'reason': None if head.returncode == 0 else 'NATIVE_COMMIT_REQUIRED', 'head': head.stdout.strip()}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {'ready': False, 'state': 'blocked', 'reason': 'NATIVE_COMMIT_CHECK_FAILED', 'detail': str(exc)}
