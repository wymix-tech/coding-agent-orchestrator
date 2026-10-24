"""Native reviewer batch preparation; no second CAO code review."""
import argparse
import hashlib
import json
import re
import shlex
import shutil
from pathlib import Path

from scripts.cao_instructions import cao_invocation, host_adapter_status
from scripts.cao_providers import detect_current_host
from scripts.cao_runtime import new_id
from scripts.cao_sessions import list_sessions, load_session
from scripts.cao_snapshot import code_snapshot
from scripts.native_obligations import active_layers, dispatch as dispatch_review, dispatch_review_next, review_inputs, digest as native_digest
from scripts.path_guard import inside, repo_file, provider_artifact
from scripts.review_diagnostics import invalid
from scripts.runtime_store import identifier
from scripts.state_store import atomic_json


def coordinator(repo, identity):
    session = load_session(repo, identity[1], host=identity[0]) if identity else None
    if session and session.get('kind') == 'subagent':
        invalid('NATIVE_REVIEW_COORDINATOR_REQUIRED', 'session.kind', 'coordinator creates/restarts tickets; reviewer only attaches and reviews', 'subagent')


def begin_review(repo, work, args, identity):
    coordinator(repo, identity)
    records = work.setdefault('sdd', {}).setdefault('native_reviews', {})
    if not args.model.strip():
        raise ValueError("NATIVE_REVIEW_MODEL_REQUIRED")
    path = repo_file(repo, args.instructions)
    expected_name = "step-04-review.md" if dispatch_review(work) else "step-oneshot.md"
    if path.name != expected_name or not inside(path, repo / "_bmad/render") or (repo / "_bmad/render").resolve() != repo / "_bmad/render" or not path.is_file():
        raise ValueError("NATIVE_REVIEW_INSTRUCTIONS_INVALID")
    content = path.read_text()
    layers = sorted(set(re.findall(r"(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)", content)))
    if not layers or args.layer not in layers:
        raise ValueError("NATIVE_REVIEW_LAYER_NOT_ACTIVE")
    if dispatch_review(work) and layers != active_layers(repo, work):
        raise ValueError("NATIVE_REVIEW_CONTRACT_MISMATCH")
    records["_contract"] = {"sources": [path.relative_to(repo).as_posix()], "layers": layers}
    if any(record.get('model') != args.model for key, record in records.items() if key != '_contract'):
        # A model change starts a new review round; never mix layers
        # from two capability levels while retaining their old passes.
        for key in list(records):
            if key != '_contract':
                records.pop(key)
    inputs = review_inputs(repo, work, code_snapshot)
    primary_ids = [session.get("host_session_id") for session in list_sessions(repo)
                   if session.get("work_item_id") == work["id"] and session.get("kind") == "primary"]
    existing = records.get(args.layer) or {}
    matching_inputs = (existing.get('inputs') == inputs or (existing.get('status') == 'classified'
                       and all(existing.get('inputs', {}).get(k) == v for k, v in inputs.items() if k != 'code_snapshot')))
    reused = (dispatch_review(work) and not args.restart and existing.get('status') in {'running', 'classified'}
              and matching_inputs and existing.get('model') == args.model)
    if not args.restart and existing.get('status') == 'passed' and existing.get('inputs') == inputs and existing.get('model') == args.model:
        hashes = existing.get('evidence_hashes') or {}
        try:
            reused = bool(hashes and existing.get('execution_binding')) and all(
                hashlib.sha256(repo_file(repo, name).read_bytes()).hexdigest() == sha for name, sha in hashes.items())
        except (ValueError, OSError, RuntimeError):
            reused = False
    run = existing if reused else {"id": new_id("review"), "status": "running", "inputs": inputs,
           "input_sha256": native_digest(inputs), "model": args.model,
           "existing_session_ids": [session['id'] for session in list_sessions(repo)],
           "primary_session_ids": primary_ids}
    records[args.layer] = run
    output = {"ok": True, "review": run, "active_layers": layers, 'reused': reused}
    output['reviewer_start'] = (f'{cao_invocation()["command"]} sdd review attach --work {work["id"]} '
        f'--layer {args.layer} --run {run["id"]} --input-sha256 {run["input_sha256"]} --model {shlex.quote(args.model)}')
    output['next'] = ('Begin ALL active layers before launching reviewers. Each new independent reviewer must be bound before review, via child reviewer_start or coordinator --reviewer-session with observed child metadata. '
        'to acknowledge this ticket and frozen input, then inspect that exact code without product writes. '
        + ('Preserve unmodified native Markdown/JSON findings for each layer; classify ALL layers before patches, then resolve ALL layers. '
           'Use `cao sdd review classify --layer <layer> --report <triage.json>`, then `cao sdd review resolve --layer <layer> --report <resolved.json>`.'
           if dispatch_review(work) else
           'Preserve raw Markdown findings and submit triage with `cao sdd review submit --layer <layer> --report <report.json>`.')
        + ' Before launch, inspect the installed agent definitions and effective tools/model/backend; read-only children use coordinator attachment and all children need the CAO host adapter. '
        'Use an available agent name; do not assume task/general-purpose exist. Preserve native prompts/output formats, including Markdown. '
        'If no capable subagent is available, use a new independent external host session with its CAO adapter. Never reuse results from before this ticket.')
    if reused:
        output['next'] = dispatch_review_next(records, layers, inputs)
    return output


def bundle_root(repo, work, layer, run):
    relative = f"_bmad-output/cao-reviews/{identifier(work['id'])}/{identifier(run['id'])}/{identifier(layer)}"
    path = provider_artifact(repo, relative + '/report.json', ['_bmad-output']).parent
    for part in (path, *path.parents):
        if part == repo:
            break
        if part.is_symlink():
            raise ValueError('NATIVE_REVIEW_BUNDLE_SYMLINK')
    return path


def agent_capability(repo, definition, host):
    """Inspect an actual definition, not a guessed agent name or promised tools."""
    requested = Path(definition).expanduser()
    path = requested if requested.is_absolute() else repo / requested
    roots = [repo / '.pi/agents', repo / '.agents/agents',
             Path.home() / '.pi/agents', Path.home() / '.pi/agent/agents']
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        invalid('NATIVE_REVIEW_AGENT_UNAVAILABLE', 'agent_definition', 'an installed project/global agent definition', definition)
    if path.is_symlink() or path.resolve() != path.absolute():
        invalid('NATIVE_REVIEW_AGENT_UNAVAILABLE', 'agent_definition', 'a regular installed definition without symlink aliases', definition)
    content = path.read_text(encoding='utf-8-sig')
    header = re.match(r'\A---\s*\n(.*?)\n---(?:\n|$)', content, re.S)
    command = re.search(r'(?m)^[ \t]*command:[ \t]*([^\n]+)', header[1]) if header else None
    external = bool(header and re.search(r'(?m)^[ \t]*type:[ \t]*external-cli\s*$', header[1]))
    if command:
        argv = shlex.split(command[1])
        if not argv or not shutil.which(argv[0]):
            invalid('NATIVE_REVIEW_EXECUTABLE_UNAVAILABLE', 'agent.command', 'installed executable', command[1])
    if external:
        invalid('NATIVE_REVIEW_EXTERNAL_ADAPTER_REQUIRED', 'agent.runner',
                'use --external-session after checking backend parameter support and independent CAO session observation; do not inject model overrides', definition)
    match = re.search(r'(?m)^tools:[ \t]*([^\n]*)(?:\n((?:[ \t]+-[^\n]*\n?)*))?', header[1]) if header else None
    tools = set(re.findall(r'[a-z][a-z_-]*', ' '.join(value or '' for value in match.groups()))) if match else set()
    if 'read' not in tools:
        invalid('NATIVE_REVIEW_AGENT_CAPABILITY_REQUIRED', 'agent.tools', ['read'], sorted(tools))
    if header and re.search(r'(?m)^acceptanceRole:[ \t]*writer\s*$', header[1]):
        invalid('NATIVE_REVIEW_WRITER_CONTRACT', 'agent.acceptanceRole',
                'install templates/pi-reviewer.md as .pi/agents/cao-native-reviewer.md, then prepare with that reviewer definition', 'writer')
    status = host_adapter_status(repo, host)
    if not status.get('installed') or host == 'pi' and not status.get('child_hooks_installed'):
        invalid('NATIVE_REVIEW_ADAPTER_REQUIRED', 'host.adapter', 'install the CAO adapter before launching reviewers', host)
    return {'definition': path.relative_to(repo).as_posix() if path.is_relative_to(repo) else str(path), 'tools': sorted(tools), 'host': host,
            'definition_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'attach_mode': 'self' if 'bash' in tools else 'coordinator',
            'task_contract': {'role': 'reviewer', 'product_writes': False,
                              'evidence': ['original-findings', 'review-input', 'reviewer-identity']},
            'launch_preflight': ['verify effective runtime tools/extensions', 'verify backend executable and model-override support'],
            'model_override': 'only when supported by the selected host backend'}


def prepare_batch(repo, work, args, identity):
    coordinator(repo, identity)
    from scripts.native_obligations import review_state
    existing = review_state(repo, work, code_snapshot)
    if existing.get('ready') and existing.get('state') == 'recorded' and not args.restart:
        return {'ok': True, 'reviews': [], 'status': 'already_satisfied', 'evidence': existing,
                'next': 'Native review evidence is current. Continue remaining conformance/acceptance obligations; do not rerun code review.'}
    preflight = acceptance_preflight(repo, work)
    if not preflight['ready']:
        invalid('TASK_ACCEPTANCE_COVERAGE_INCOMPLETE', 'acceptance.coverage', preflight['next'], preflight['coverage'])
    host = identity[0] if identity else detect_current_host()
    if getattr(args, 'external_session', False):
        capability = {'mode': 'external-session', 'requires': ['new session per layer', 'shell', 'CAO host adapter'],
                      'verified_at': 'attach; preparation does not certify external execution'}
    elif getattr(args, 'agent_definition', None):
        capability = agent_capability(repo, args.agent_definition, host)
    else:
        invalid('NATIVE_REVIEW_CAPABILITY_REQUIRED', 'agent_definition',
                'inspect an installed agent with --agent-definition PATH, or prepare --external-session for an independent host')
    path = repo_file(repo, args.instructions)
    content = path.read_text(encoding='utf-8-sig')
    layers = sorted(set(re.findall(r'(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)', content)))
    if not layers:
        raise ValueError('NATIVE_REVIEW_LAYER_NOT_ACTIVE')
    outputs = []
    for layer in layers:
        item_args = argparse.Namespace(**vars(args))
        item_args.layer = layer
        output = begin_review(repo, work, item_args, identity)
        run = output['review']
        if output.get('reused') and run.get('status') == 'passed':
            continue
        root = bundle_root(repo, work, layer, run)
        root.mkdir(parents=True, exist_ok=True)
        report = root / 'report.json'
        if not report.exists():
            atomic_json(report, {'run_id': run['id'], 'input_sha256': run['input_sha256'],
                                 'reviewer': (run.get('execution_binding') or {}).get('reviewer'),
                                 'findings_file': (root / 'findings.md').relative_to(repo).as_posix(),
                                 'findings': [], 'intent_conflicts': []})
        package = {'layer': layer, 'run_id': run['id'], 'input_sha256': run['input_sha256'],
                   'inputs': run['inputs'], 'reviewer_start': output['reviewer_start'],
                   'report': report.relative_to(repo).as_posix(), 'capability': capability,
                   'task_contract': {'role': 'reviewer', 'product_writes': False, 'evidence': ['original-findings', 'review-input', 'reviewer-identity']},
                   'next': 'Launch one NEW capable session for this layer after ALL tickets exist. Bind the observed child with coordinator attach --reviewer-session (read-only), or run reviewer_start in the child (shell), before review. Read native instructions and unchanged inputs; preserve original findings. Attach stamps actual identity into the report. Classify all layers before patches; resolve all after fixes. Never restart tickets in a reviewer.'}
        atomic_json(root / 'dispatch.json', package)
        outputs.append(package)
    return {'ok': True, 'reviews': outputs, 'owner': 'sdd_provider',
            'next': 'Dispatch these native reviews once; CAO checks their evidence and does not run an additional code review.'}


def stamp_report(repo, work, layer, run):
    """Only host-observed attach can populate reviewer metadata in a fresh bundle."""
    path = bundle_root(repo, work, layer, run) / 'report.json'
    if not path.exists():
        return
    report = json.loads(path.read_text())
    if report.get('run_id') != run['id'] or report.get('input_sha256') != run['input_sha256']:
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    report['reviewer'] = run['execution_binding']['reviewer']
    atomic_json(path, report)


def import_review(repo, work, layer, report):
    from scripts.native_obligations import classify_review, submit_review
    run = ((work.get('sdd') or {}).get('native_reviews') or {}).get(layer) or {}
    if run.get('status') == 'passed':
        source = provider_artifact(repo, report, ['_bmad-output'])
        hashes = run.get('evidence_hashes') or {}
        if (run.get('inputs') != review_inputs(repo, work, code_snapshot)
                or source.relative_to(repo).as_posix() not in hashes
                or not hashes or any(hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest() != sha
                                     for path, sha in hashes.items())):
            invalid('NATIVE_REVIEW_STALE', 'evidence', 'current unchanged native review inputs and original report')
        if not run.get('execution_binding'):
            raise ValueError('NATIVE_REVIEW_EXECUTION_UNBOUND')
        return run
    if dispatch_review(work):
        return classify_review(repo, work, layer, report, code_snapshot)
    return submit_review(repo, work, layer, report, code_snapshot)


def draft_report(repo, work, layer, source):
    """Mechanical conversion only: never infer verdicts or disposition."""
    from scripts.native_obligations import load_findings, validate_reviewer
    run = ((work.get('sdd') or {}).get('native_reviews') or {}).get(layer) or {}
    binding = run.get('execution_binding') or {}
    validate_reviewer(run, {'reviewer': binding.get('reviewer')})
    if run.get('status') != 'running' or run.get('inputs') != review_inputs(repo, work, code_snapshot):
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    raw = provider_artifact(repo, source, ['_bmad-output'])
    findings = load_findings(raw)
    ids = [item.get('id') for item in findings]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        invalid('NATIVE_REVIEW_FINDING_MAPPING_INVALID', 'source.ids', 'unique nonempty IDs', ids)
    root = bundle_root(repo, work, layer, run)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'report.json'
    if path.exists() and json.loads(path.read_text()).get('findings'):
        invalid('NATIVE_REVIEW_DRAFT_EXISTS', 'report', 'reuse the existing triage; never overwrite judgments', str(path.relative_to(repo)))
    report = {'run_id': run['id'], 'input_sha256': run['input_sha256'], 'reviewer': binding['reviewer'],
              'findings_file': raw.relative_to(repo).as_posix(), 'intent_conflicts': [],
              'findings': [{'id': item, 'verdict': None, 'disposition': None, 'evidence': ''} for item in ids]}
    atomic_json(path, report)
    return {'ok': True, 'report': path.relative_to(repo).as_posix(), 'findings': findings,
            'source_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
            'next': 'Judge each complete native finding and fill verdict, disposition and evidence; then classify this report. Original findings remain unchanged.'}


def acceptance_preflight(repo, work):
    """Check coverage before spending reviewer sessions; legacy unbound work is unchanged."""
    if not work.get('project_binding'):
        return {'ready': True, 'state': 'legacy_unbound'}
    from scripts.acceptance_trace import coverage
    trace = coverage(repo, work)
    declaration = ((work.get('sdd') or {}).get('acceptance') or {}).get('declaration')
    return {'ready': bool(declaration and trace['required'] and not trace['missing'] and not trace['unknown']),
            'coverage': trace,
            'next': 'Configure task acceptance and map substantive source items before preparing native reviewers. Do not map document metadata to functional criteria.'}


def classify_batch(repo, work, report_path):
    """All active layers are classified atomically; shell loops are unnecessary."""
    from copy import deepcopy
    from scripts.native_obligations import classify_review
    from scripts.review_diagnostics import read_report
    mapping = read_report(provider_artifact(repo, report_path, ['_bmad-output']))
    layers = active_layers(repo, work)
    if set(mapping) != set(layers) or any(not isinstance(path, str) or not path for path in mapping.values()):
        invalid('NATIVE_REVIEW_BATCH_LAYERS_REQUIRED', '$', layers, mapping)
    candidate = deepcopy(work)
    results = []
    for layer in layers:
        try:
            run = classify_review(repo, candidate, layer, mapping[layer], code_snapshot)
        except ValueError as exc:
            from scripts.review_diagnostics import ReviewValidationError
            diagnostics = getattr(exc, 'diagnostics', [{'code': str(exc), 'field': 'review'}])
            raise ReviewValidationError(str(exc), [{**item, 'layer': layer} for item in diagnostics]) from exc
        results.append({'layer': layer, 'run_id': run['id'], 'status': run['status'],
                        'classified_count': run['classified_count']})
    work['sdd']['native_reviews'] = candidate['sdd']['native_reviews']
    return {'ok': True, 'reviews': results,
            'next': dispatch_review_next(work['sdd']['native_reviews'], layers, review_inputs(repo, work, code_snapshot))}
