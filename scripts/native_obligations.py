"""Native oneshot review evidence and local VCS completion, not semantic certification."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from scripts.bmad_project import output_dirs as bmad_output_dirs
from scripts.light_spec_policy import _oneshot, _lines, _section
from scripts.path_guard import repo_file, provider_artifact
from scripts.spec_intent import frozen_intent
from scripts.review_diagnostics import invalid, report_shape, read_report


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_findings(path):
    """Preserve native Markdown; assign stable IDs without rewriting reviewer prompts."""
    content = path.read_text(encoding='utf-8-sig')
    try:
        value = json.loads(content)
        if not isinstance(value, list):
            raise ValueError('NATIVE_REVIEW_FINDINGS_INVALID: expected an array')
        # Native JSON may omit CAO IDs; derive them without rewriting the source.
        if any(not isinstance(item, dict) for item in value):
            raise ValueError('NATIVE_REVIEW_FINDINGS_INVALID: expected finding objects')
        return [{**item, 'id': item.get('id', f'finding-{index + 1}')} for index, item in enumerate(value)]
    except json.JSONDecodeError:
        if path.suffix.lower() != '.md':
            raise ValueError('NATIVE_REVIEW_FINDINGS_INVALID')
    findings, block = [], []
    lines, fence = [], None
    for line in content.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is None and marker and (marker[1][0] != '`' or '`' not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            lines.append((line, False))
        elif fence is not None:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}[ \t]*', line):
                fence = None
            lines.append((line, False))
        else:
            lines.append((line, True))
    # Heading-based native reports group all explanatory bullets into one finding.
    wrappers = {'findings', 'review findings', 'native findings', 'review results', '审查发现', '审查结果', '问题列表'}
    headings = [(index, len(match[1])) for index, (line, outside) in enumerate(lines)
                if outside for match in [re.match(r'^ {0,3}(#{2,6})\s+(.+?)\s*#*$', line)]
                if match and match[2].strip().rstrip(':：').casefold() not in wrappers]
    if headings:
        level = min(depth for _, depth in headings)
        starts = [index for index, depth in headings if depth == level]
        return [{'id': f'finding-{number + 1}',
                 'content': '\n'.join(line for line, _ in lines[start:end]).strip()}
                for number, (start, end) in enumerate(zip(starts, starts[1:] + [len(lines)]))]
    candidates = [len(match[1]) for line, outside in lines if outside
                  for match in [re.match(r'^( {0,3})(?:[-*+]|[0-9]+[.)])\s+\S', line)] if match]
    indent = min(candidates, default=0)
    for line, outside in lines:
        if outside and re.match(r'^ {' + str(indent) + r'}(?:[-*+]|[0-9]+[.)])\s+\S', line):
            if block:
                findings.append({'id': f'finding-{len(findings) + 1}', 'content': '\n'.join(block).strip()})
            block = [line]
        elif block:
            block.append(line)
    if block:
        findings.append({'id': f'finding-{len(findings) + 1}', 'content': '\n'.join(block).strip()})
    if not findings:
        raise ValueError('NATIVE_REVIEW_FINDINGS_INVALID: native Markdown must contain a findings list; explicit zero findings use [] JSON')
    return findings


def dispatch_review_next(records, layers, inputs):
    if any((records.get(layer) or {}).get('loopback_required') for layer in layers):
        return ('Run `cao sdd review loopback --reason "<native finding>" --keep "<KEEP instructions>" --path <product-file>` (repeat --path for all affected files) before reverting. Follow the native intent_gap/bad_spec loopback first: resolve frozen intent conflicts with the human and refresh route/approval evidence, '
                'or amend the non-frozen design for bad_spec; preserve native KEEP instructions and rederive the code. '
                'After completing that native loopback, use `cao sdd review begin --restart` for ALL active layers to start the new review round. '
                'Restarting tickets alone does not resolve a known intent conflict.')
    for layer in layers:
        record = records.get(layer) or {}
        if not record:
            continue
        matches = all(record.get('inputs', {}).get(k) == v for k, v in inputs.items()
                      if k != 'code_snapshot' or record.get('status') != 'classified')
        if not matches:
            return 'Review evidence is stale. Begin fresh active layers for the current snapshot (use --restart to replace prior dispatch tickets), collect and classify all, patch/check, then resolve all layers before `cao verify`.'
    missing = [layer for layer in layers if not records.get(layer)]
    if missing:
        return ('Begin only missing layers: ' + ', '.join(missing) + '. Use `cao sdd review begin --layer <missing-layer> --instructions <actual-step-04-review.md> --model <current-model>`. '
                'Begin ALL layers before product patches; collect ALL layers before `cao sdd review classify`, then apply patches and `cao sdd review resolve`.')
    running = [layer for layer in layers if records[layer].get('status') == 'running']
    if running:
        unbound = [layer for layer in running if not records[layer].get('execution_binding')]
        return ('Continue existing tickets for ' + ', '.join(running) + '. '
                + ('Bind each NEW observed child via coordinator --reviewer-session or child reviewer_start for ' + ', '.join(unbound) + '. ' if unbound else '')
                + 'Collect native findings and `cao sdd review classify` ALL layers before product patches, then `cao sdd review resolve`. Do not begin again; use --restart only to explicitly replace a failed ticket.')
    classified = [layer for layer in layers if records[layer].get('status') == 'classified']
    # Classified reviews deliberately retain the pre-patch code snapshot.
    if classified and all(all(record.get('inputs', {}).get(k) == v for k, v in inputs.items() if k != 'code_snapshot')
                          for record in (records[layer] for layer in layers)):
        return ('All layers collected and classified. Finish/resume the existing patch worker, inspect partial changes after failures, and run native checks. '
                'Then `cao sdd review resolve --layer <layer> --report <resolved.json>` for ' + ', '.join(classified)
                + '; preserve existing tickets and passed layers. Do not begin or classify again. Continue to `cao verify` only after every layer is resolved.')
    return 'Review evidence is stale. Begin fresh active layers for the current snapshot, collect and classify all, patch/check, then resolve all layers before `cao verify`.'


def review_integrity(repo, work, snapshot=None, inputs=None):
    """Read shared round/evidence facts without requiring completed reviewers to live."""
    records = (work.get('sdd') or {}).get('native_reviews') or {}
    layers = active_layers(repo, work)
    models = {records[layer].get('model') for layer in layers if records.get(layer)}
    expected = (records.get('_contract') or {}).get('model')
    if len(models) > 1 or (expected and models and models != {expected}):
        return {'ready': False, 'reason': 'NATIVE_REVIEW_MODEL_MISMATCH'}
    from scripts.cao_snapshot import code_snapshot
    for layer in layers:
        run = records.get(layer) or {}
        if not run.get('input_manifest'):
            continue
        ref = run['input_manifest']
        try:
            valid_manifest = digest(json.loads(repo_file(repo, ref['path']).read_text())) == ref['content_sha256']
        except (ValueError, OSError, RuntimeError):
            valid_manifest = False
        if not valid_manifest:
            return {'ready': False, 'reason': 'NATIVE_REVIEW_INPUT_MANIFEST_STALE', 'layer': layer, 'file': ref['path']}
    current = inputs if inputs is not None else (review_inputs(repo, work, snapshot or code_snapshot)
                                                if any(records.get(layer) for layer in layers) else {})
    for layer in layers:
        run = records.get(layer) or {}
        if not run:
            continue
        if run.get('status') not in {'running', 'classified', 'passed'}:
            return {'ready': False, 'reason': 'NATIVE_REVIEW_RESTART_REQUIRED', 'layer': layer, 'field': 'status'}
        # Classified evidence deliberately keeps the original pre-patch snapshot.
        changed = [key for key, value in current.items() if run.get('inputs', {}).get(key) != value
                   and (key != 'code_snapshot' or run.get('status') != 'classified')]
        if changed:
            affected = [name for name in layers if records.get(name) and any(
                records[name].get('inputs', {}).get(key) != value and (key != 'code_snapshot' or records[name].get('status') != 'classified')
                for key, value in current.items())]
            return {'ready': False, 'reason': 'NATIVE_REVIEW_STALE', 'layer': layer, 'field': 'inputs',
                    'changed_inputs': sorted(changed), 'affected_layers': affected,
                    'still_valid_layers': [name for name in layers if records.get(name) and name not in affected]}
        if run.get('status') in {'classified', 'passed'}:
            try:
                validate_reviewer(run, {'reviewer': run.get('reviewer')})
            except ValueError:
                return {'ready': False, 'reason': 'NATIVE_REVIEW_EXECUTION_UNBOUND', 'layer': layer}
        hashes = run.get('evidence_hashes') if run.get('status') == 'passed' else run.get('triage_hashes')
        if run.get('status') in {'classified', 'passed'} and not hashes:
            return {'ready': False, 'reason': 'NATIVE_REVIEW_STALE', 'layer': layer, 'field': 'evidence_hashes'}
        for name, sha in (hashes or {}).items():
            try:
                valid = hashlib.sha256(repo_file(repo, name).read_bytes()).hexdigest() == sha
            except (OSError, ValueError, RuntimeError):
                valid = False
            if not valid:
                return {'ready': False, 'reason': 'NATIVE_REVIEW_TRIAGE_STALE' if run.get('status') == 'classified' else 'NATIVE_REVIEW_STALE',
                        'layer': layer, 'file': name}
    return {'ready': True}


def require_review_integrity(repo, work, snapshot=None):
    state = review_integrity(repo, work, snapshot)
    if not state['ready']:
        raise ValueError(state['reason'])


def review_next(repo, work, inputs):
    records = work['sdd'].get('native_reviews') or {}
    layers = active_layers(repo, work)
    integrity = review_integrity(repo, work, inputs=inputs)
    if not integrity['ready']:
        return ('Review evidence is stale: ' + integrity['reason'] + ' ' + str(integrity.get('layer', '')) + ' ' + str(integrity.get('file', ''))
                + '. Restore verified original evidence or explicitly restart the affected layer; changed models/common inputs require `cao sdd review prepare --restart` for all layers.')
    for layer in layers:
        record = records.get(layer) or {}
        if record and any(record.get('inputs', {}).get(key) != value for key, value in inputs.items() if record.get('status') != 'classified' or key != 'code_snapshot'):
            return 'Review evidence is stale: inputs changed. Inspect the changes, then explicitly use `cao sdd review prepare --restart` with the native instructions/model and reviewer capability arguments. Previous tickets remain in history.'
    if any((records.get(layer) or {}).get('loopback_required') for layer in layers):
        return 'Resolve recorded intent conflicts with the human; refresh native intent/route as required, then explicitly restart the review round. Do not submit unresolved conflicts.'
    missing = [layer for layer in layers if not records.get(layer)]
    if missing and dispatch(work):
        return dispatch_review_next(records, layers, inputs)
    if missing:
        return 'Prepare missing native reviewers with `cao sdd review prepare --instructions <actual-step-oneshot.md> --model <current-model> --external-session`; bind each new reviewer before inspection, classify all layers, patch, then `cao sdd review submit`.'
    if any(records[layer].get('status') == 'running' for layer in layers):
        from scripts.cao_sessions import load_session
        for layer in layers:
            if records[layer].get('status') != 'running':
                continue
            binding = records[layer].get('execution_binding') or {}
            reviewer = binding.get('reviewer') or {}
            if reviewer and binding.get('host'):
                session = load_session(repo, reviewer['session_id'], host=binding['host'])
                if not session or session.get('status') != 'active':
                    return ('Bound reviewer for ' + layer + ' has exited. If its original ticket-bound findings were collected, classify them; otherwise restore its actual host session or explicitly restart this layer. Do not attach another identity to the existing ticket.')
        if dispatch(work):
            return dispatch_review_next(records, layers, inputs)
        return 'Continue existing running tickets and reviewers; attach only unbound tickets. Collect and classify ALL layers before patches, then `cao sdd review submit` with separate resolved reports.'
    if dispatch(work):
        return dispatch_review_next(records, layers, inputs)
    if any(records[layer].get('status') == 'classified' for layer in layers):
        return 'All layers classified. Preserve raw and triage files; finish patches and checks, then `cao sdd review submit --layer <layer> --report <final.json>`. Do not classify again.'
    return 'Continue remaining native obligations, then `cao verify`.'


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
    path = provider_artifact(repo, source, bmad_output_dirs(repo))
    raw = path.read_bytes()
    if not _oneshot(raw, _lines(raw)):
        if decision:
            raise ValueError('NATIVE_ONESHOT_SPEC_INVALID')
        return None
    content = raw.decode('utf-8-sig')
    intent = frozen_intent(content, required=True)
    return {'path': path.relative_to(repo.resolve()).as_posix(), 'intent_sha256': digest(intent)}


def review_inputs(repo, work, code_snapshot):
    source = review_source(repo, work)
    if not source:
        raise ValueError('NATIVE_REVIEW_NOT_APPLICABLE')
    contract = ((work.get('sdd') or {}).get('native_reviews') or {}).get('_contract') or {}
    aliases = (work.get('sdd') or {}).get('evidence_aliases') or {}
    def canonical(path):
        seen = set()
        while path in aliases and path not in seen:
            seen.add(path)
            path = aliases[path]
        return path
    instructions = {canonical(path): hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest()
                    for path in contract.get('sources', [])}
    from scripts.review_scope import acceptance_review_scope
    excluded, acceptance_standard = acceptance_review_scope(repo, work)
    result = {'instructions': instructions, 'work_id': work['id'], 'requirement_sha256': work['requirement']['sha256'],
              'code_snapshot': code_snapshot(repo, {work['requirement']['path'], *excluded}, {'_bmad-output', '_bmad', '.agents', '.pi', 'openspec', '.specify', 'specs', *bmad_output_dirs(repo)}), **source}
    if acceptance_standard is not None:
        result['acceptance_standard_sha256'] = digest(acceptance_standard)
    if work.get('lifecycle_contract') == 2 and (work.get('sdd') or {}).get('native_review_dependencies'):
        from copy import deepcopy
        from scripts.evidence_domains import dependencies
        native = deepcopy(work)
        native['sdd']['native_execution_dependencies'] = []
        native['sdd']['workflow'] = {}
        result['native_review_dependencies_sha256'] = digest(dependencies(repo, native))

    return result


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
        next_step = review_next(repo, work, inputs)
        integrity = review_integrity(repo, work, code_snapshot)
        if not integrity['ready']:
            return {**integrity, 'state': 'stale', 'next': next_step}
        if any((records.get(layer) or {}).get('loopback_required') for layer in layers):
            return {'ready': False, 'state': 'blocked', 'reason': 'NATIVE_REVIEW_LOOPBACK_REQUIRED', 'next': next_step}
        if run.get('status') != 'passed':
            return {'ready': False, 'state': 'pending', 'reason': 'NATIVE_REVIEW_REQUIRED', 'next': next_step}
        if any(not (records.get(layer) or {}).get('execution_binding') for layer in layers):
            return {'ready': False, 'state': 'stale', 'reason': 'NATIVE_REVIEW_EXECUTION_UNBOUND', 'next': next_step}
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
    contracts = work['sdd']['route_decision'].get('contracts', {})
    if contracts.get('implementation'):
        from scripts.native_contracts import validate
        validate(repo, contracts)
        item = contracts['implementation']
        return {**item, 'subagent_default': bool(re.search(r'(?i)launch a subagent', repo_file(repo, item['path']).read_text()))}
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
            path = provider_artifact(repo, artifact['path'], bmad_output_dirs(repo))
            text = path.read_text()
            header = re.match(r'\A---\s*\n(.*?)\n---(?:\s*\n|\Z)', text, re.S)
            statuses = re.findall(r'(?m)^status:\s*(.*?)\s*$', header[1]) if header else []
            if len(statuses) != 1 or statuses[0].strip("'\"") != 'final':
                raise ValueError('NATIVE_PLANNING_NOT_FINAL: ' + artifact['path'])
            skill = repo / '.agents/skills' / ('bmad-prd' if role == 'prd' else 'bmad-architecture') / 'SKILL.md'
            rules = skill.read_text() if skill.is_file() else ''
            if '.memlog.md' in rules:
                audit = provider_artifact(repo, path.parent / '.memlog.md', bmad_output_dirs(repo))
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
    path = provider_artifact(repo, decision['primary_artifact'], bmad_output_dirs(repo))
    content = path.read_text(encoding='utf-8-sig')
    intent = frozen_intent(content, required=True)
    return {'path': path.relative_to(repo.resolve()).as_posix(), 'intent_sha256': digest(intent)}


def active_layers(repo, work):
    contracts = ((work.get('sdd') or {}).get('route_decision') or {}).get('contracts', {})
    if contracts:
        from scripts.native_contracts import validate
        validate(repo, contracts)
        return contracts['layers']
    records = (work.get('sdd') or {}).get('native_reviews') or {}
    if not dispatch(work):
        contract = records.get('_contract') or {}
        for name in contract.get('sources', []):
            source = repo_file(repo, name)
            if source.name == 'step-oneshot.md' and source.is_file():
                layers = sorted(set(re.findall(r'(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)', source.read_text())))
                if layers:
                    return layers
        return contract.get('layers') or ['blind-hunter']
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
    if not review_source(repo, work):
        return {'ready': True}
    records = work['sdd'].get('native_reviews') or {}
    started = [r for key, r in records.items() if key != '_contract']
    if not started:
        return {'ready': True}  # Initial implementation precedes review.
    try:
        require_review_integrity(repo, work, code_snapshot)
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


def validate_finding_policy(finding, dispatch_route):
    """Shared judgment policy for classification and compatibility submission."""
    if not isinstance(finding, dict):
        raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID')
    verdict = finding.get('verdict')
    if finding.get('disposition') == 'reject' and verdict in {'high', 'medium', 'low'}:
        if verdict in {'high', 'medium'} or (dispatch_route and finding.get('reject_reason') != 'unlikely-use-nontrivial-fix'):
            raise ValueError('NATIVE_REVIEW_VERIFIED_FINDING_REJECTED')


def validate_finding_mapping(raw, findings):
    from collections import Counter
    raw_ids = [item.get('id') if isinstance(item, dict) else None for item in raw]
    actual_ids = [item.get('id') if isinstance(item, dict) else None for item in findings]
    def valid(value):
        return isinstance(value, str) and bool(value.strip())
    expected = {value for value in raw_ids if valid(value)}
    actual = {value for value in actual_ids if valid(value)}
    duplicates = {field: [value for value, count in Counter(v for v in values if valid(v)).items() if count > 1]
                  for field, values in (('raw', raw_ids), ('classified', actual_ids))}
    if (not all(valid(value) for value in raw_ids + actual_ids)
            or any(duplicates.values()) or expected != actual):
        invalid('NATIVE_REVIEW_FINDING_MAPPING_INVALID', '$.findings[*].id',
                {'ids': raw_ids, 'missing': sorted(expected - actual)},
                {'ids': actual_ids, 'extra': sorted(actual - expected), 'duplicate': duplicates})


def validate_judgments(report, dispatch_route):
    findings = report['findings']
    conflicts = report.get('intent_conflicts', [])
    if not isinstance(conflicts, list) or any(not isinstance(c, str) or not c.strip() for c in conflicts):
        raise ValueError('NATIVE_REVIEW_INTENT_CONFLICTS_INVALID')
    loopback = bool(conflicts)
    for index, finding in enumerate(findings):
        route = finding.get('disposition')
        if finding.get('verdict') not in {'high', 'medium', 'low', 'false', 'maybe-false'} or not str(finding.get('evidence') or '').strip():
            raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID')
        if route not in ({'patch', 'defer', 'reject', 'bad_spec', 'intent_gap'} if dispatch_route else {'patch', 'defer', 'reject'}):
            raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID')
        if finding.get('violates_frozen_intent') is True and not dispatch_route:
            loopback = True
        elif finding.get('violates_frozen_intent') is True and route not in {'bad_spec', 'intent_gap'}:
            raise ValueError('NATIVE_REVIEW_FROZEN_INTENT_LOOPBACK_REQUIRED')
        if route == 'defer' and dispatch_route:
            reason = finding.get('defer_reason')
            if finding.get('violates_frozen_intent') is not False:
                raise ValueError('NATIVE_REVIEW_DEFER_OUTSIDE_INTENT_REQUIRED')
            if reason not in {'pre-existing', 'unverified', 'agent-context'}:
                raise ValueError('NATIVE_REVIEW_DEFER_REASON_REQUIRED: pre-existing, unverified, or agent-context; not merely absent from scope')
            if reason == 'unverified' and finding['verdict'] != 'maybe-false':
                raise ValueError('NATIVE_REVIEW_DEFER_REASON_INVALID')
        validate_finding_policy(finding, dispatch_route)
        loopback = loopback or route in {'bad_spec', 'intent_gap'}
    return conflicts, loopback


def classify_review(repo, work, layer, report_path, code_snapshot):
    require_review_integrity(repo, work, code_snapshot)
    if layer not in active_layers(repo, work):
        raise ValueError('NATIVE_REVIEW_LAYER_NOT_ACTIVE')
    records = work['sdd'].get('native_reviews') or {}
    # No patches between collecting reviewers and their classification.
    inputs = review_inputs(repo, work, code_snapshot)
    if any((records.get(key) or {}).get('inputs') != inputs for key in active_layers(repo, work)):
        raise ValueError('NATIVE_REVIEW_ALL_LAYERS_REQUIRED')
    run = records.get(layer) or {}
    report_file = provider_artifact(repo, report_path, bmad_output_dirs(repo))
    report = read_report(report_file)
    if not isinstance(report, dict):
        raise ValueError('NATIVE_REVIEW_REPORT_INVALID')
    report = complete_report_metadata(run, report)
    report_shape(report, dispatch=dispatch(work))
    validate_reviewer(run, report)
    if run.get('status') != 'running' or report.get('run_id') != run.get('id') or report.get('input_sha256') != digest(inputs):
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    findings_file = provider_artifact(repo, str(report.get('findings_file') or ''), bmad_output_dirs(repo))
    from scripts.review_observation import validate_source
    validate_source(repo, run, findings_file)
    raw = load_findings(findings_file)
    findings = report.get('findings')
    if not isinstance(raw, list) or not isinstance(findings, list):
        raise ValueError('NATIVE_REVIEW_TRIAGE_REQUIRED')
    validate_finding_mapping(raw, findings)
    conflicts, loopback = validate_judgments(report, dispatch(work))
    run.update(status='classified', reviewer=report['reviewer'], findings=findings,
               loopback_required=loopback, intent_conflicts=conflicts, raw_count=len(raw), classified_count=len(findings),
               triage_hashes={p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (report_file, findings_file)})
    return run


def complete_report_metadata(run, report):
    # Omitted mechanical fields come from the ticket; explicit conflicts still fail.
    binding = run.get('execution_binding') or {}
    return {'run_id': run.get('id'), 'input_sha256': run.get('input_sha256'),
            'reviewer': binding.get('reviewer'), **report}


def validate_reviewer(run, report):
    reviewer = report.get('reviewer') or {}
    legacy = 'NATIVE_REVIEW_INDEPENDENT_MODEL_REQUIRED'
    if not isinstance(reviewer, dict) or not reviewer:
        invalid('NATIVE_REVIEW_REVIEWER_REQUIRED', '$.reviewer', '{mode, session_id, model} from actual attach', reviewer, legacy=legacy)
    if reviewer.get('mode') not in {'subagent', 'external-session'}:
        invalid('NATIVE_REVIEW_MODE_INVALID', '$.reviewer.mode', ['subagent', 'external-session'], reviewer.get('mode'), legacy=legacy)
    if not str(reviewer.get('session_id') or '').strip() or reviewer.get('session_id') in run.get('primary_session_ids', []):
        invalid('NATIVE_REVIEW_SESSION_INVALID', '$.reviewer.session_id', 'actual independent reviewer identity', reviewer.get('session_id'), legacy=legacy)
    if reviewer.get('model') != run.get('model'):
        invalid('NATIVE_REVIEW_MODEL_MISMATCH', '$.reviewer.model', run.get('model'), reviewer.get('model'), legacy=legacy)
    binding = run.get('execution_binding') or {}
    if (binding.get('reviewer') != reviewer
            or binding.get('input_sha256') != run.get('input_sha256')):
        invalid('NATIVE_REVIEW_EXECUTION_UNBOUND', '$.reviewer',
                binding.get('reviewer') or 'attach an observed independent reviewer first', reviewer)


def resolve_review(repo, work, layer, report_path, code_snapshot):
    if not dispatch(work):
        raise ValueError('NATIVE_REVIEW_RESOLVE_NOT_APPLICABLE')
    state = dispatch_patch_state(repo, work, code_snapshot)
    if not state['ready']:
        raise ValueError(state['reason'])
    if layer not in active_layers(repo, work):
        raise ValueError('NATIVE_REVIEW_LAYER_NOT_ACTIVE')
    run = work['sdd']['native_reviews'].get(layer) or {}
    if run.get('status') != 'classified':
        raise ValueError('NATIVE_REVIEW_CLASSIFY_REQUIRED')
    path = provider_artifact(repo, report_path, bmad_output_dirs(repo))
    report = read_report(path)
    report.setdefault('run_id', run.get('id'))
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
    require_review_integrity(repo, work, code_snapshot)
    run = (work.get('sdd') or {}).get('native_reviews', {}).get(layer) or {}
    report_file = provider_artifact(repo, report_path, bmad_output_dirs(repo))
    report = read_report(report_file)
    if not isinstance(report, dict):
        raise ValueError('NATIVE_REVIEW_REPORT_INVALID')
    report = complete_report_metadata(run, report)
    report_shape(report, dispatch=False)
    if (run.get('loopback_required') or run.get('intent_conflicts') or report.get('intent_conflicts')
            or any(f.get('violates_frozen_intent') is True for f in report.get('findings', []))):
        raise ValueError('NATIVE_REVIEW_LOOPBACK_REQUIRED')
    layers = active_layers(repo, work)
    records = work['sdd']['native_reviews']
    if layer not in layers or any(not records.get(key) for key in layers):
        raise ValueError('NATIVE_REVIEW_ALL_LAYERS_REQUIRED')
    if any((records.get(key) or {}).get('loopback_required') for key in layers):
        raise ValueError('NATIVE_REVIEW_LOOPBACK_REQUIRED')
    classified = run.get('status') == 'classified'
    current = review_inputs(repo, work, code_snapshot)
    if classified:
        if any(hashlib.sha256(repo_file(repo, name).read_bytes()).hexdigest() != sha for name, sha in run.get('triage_hashes', {}).items()):
            raise ValueError('NATIVE_REVIEW_TRIAGE_STALE')
        if any((records.get(key) or {}).get('status') not in {'classified', 'passed'} for key in layers):
            raise ValueError('NATIVE_REVIEW_ALL_LAYERS_REQUIRED')
    matching = all(value == current.get(key) for key, value in run.get('inputs', {}).items() if not classified or key != 'code_snapshot')
    if (run.get('status') not in {'running', 'classified'} or report.get('run_id') != run.get('id')
            or report.get('input_sha256') != digest(run.get('inputs'))
            or not matching):
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    validate_reviewer(run, report)
    reviewer = report['reviewer']
    findings_file = provider_artifact(repo, str(report.get('findings_file') or ''), bmad_output_dirs(repo))
    if findings_file == report_file or not findings_file.read_text().strip():
        raise ValueError('NATIVE_REVIEW_FINDINGS_REQUIRED')
    from scripts.review_observation import validate_source
    validate_source(repo, run, findings_file)
    findings = report.get('findings')
    if not isinstance(findings, list) or not findings or len(findings) != len(load_findings(findings_file)):
        raise ValueError('NATIVE_REVIEW_TRIAGE_REQUIRED')
    if classified and findings_file.relative_to(repo).as_posix() not in run.get('triage_hashes', {}):
        raise ValueError('NATIVE_REVIEW_TRIAGE_STALE')
    validate_finding_mapping(load_findings(findings_file), findings)
    _, loopback = validate_judgments(report, False)
    if loopback:
        raise ValueError('NATIVE_REVIEW_LOOPBACK_REQUIRED')
    if classified and [{k: v for k, v in f.items() if k != 'resolved'} for f in findings] != [{k: v for k, v in f.items() if k != 'resolved'} for f in run['findings']]:
        raise ValueError('NATIVE_REVIEW_TRIAGE_STALE')
    ids = set()
    for finding in findings:
        validate_finding_policy(finding, False)
        if (not isinstance(finding, dict) or not finding.get('id') or finding['id'] in ids
                or finding.get('verdict') not in {'high', 'medium', 'low', 'false', 'maybe-false'}
                or finding.get('disposition') not in {'patch', 'defer', 'reject'}
                or not str(finding.get('evidence') or '').strip()
                or (finding['verdict'] in {'high', 'medium'} and finding['disposition'] == 'reject')
                or (finding['disposition'] == 'patch' and finding.get('resolved') is not True)):
            raise ValueError('NATIVE_REVIEW_TRIAGE_INVALID_OR_UNRESOLVED')
        ids.add(finding['id'])
    run.update(status='passed', inputs=current, report=report_file.relative_to(repo.resolve()).as_posix(), reviewer=reviewer,
               evidence_hashes={**(run.get('triage_hashes') or {}), **{path.relative_to(repo.resolve()).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (report_file, findings_file)}})
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
        from scripts.cao_runtime import config
        from scripts.snapshot_scope import CACHE_NAMES, INTERNAL_ROOTS, excluded_input, path_key, validate_scope
        scope = validate_scope(repo.resolve(), config(repo))
        extra_roots = {path_key(name) for name in ignored_dirs - CACHE_NAMES - INTERNAL_ROOTS}
        changed = []
        entries = status.split(b'\0'); i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if not entry:
                continue
            state, path = entry[:2], entry[3:].decode('utf-8')
            paths = [path]
            if b'R' in state or b'C' in state:
                if i < len(entries):
                    paths.append(entries[i].decode('utf-8'))
                    i += 1
            for value in paths:
                prefix = repo.resolve().relative_to(Path(exists.stdout.strip()).resolve()).as_posix()
                if prefix != '.':
                    value = Path(value).relative_to(prefix).as_posix()
                tracked = {path_key(value)} if state != b'??' else set()
                if value != source['path'] and not excluded_input(value, scope, tracked, additional_roots=extra_roots):
                    changed.append(value)
        if changed:
            return {'ready': False, 'state': 'pending', 'reason': 'NATIVE_COMMIT_REQUIRED', 'changed': changed[:20],
                    'next': 'Follow the native Commit step: inspect `git status`/diff, stage the intended Work changes and commit them. Do not push unless authorized. Then retry `cao done`; do not edit CAO state or reanchor solely for the commit.'}
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        return {'ready': head.returncode == 0, 'state': 'committed' if head.returncode == 0 else 'pending',
                'reason': None if head.returncode == 0 else 'NATIVE_COMMIT_REQUIRED', 'head': head.stdout.strip()}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {'ready': False, 'state': 'blocked', 'reason': 'NATIVE_COMMIT_CHECK_FAILED', 'detail': str(exc)}
