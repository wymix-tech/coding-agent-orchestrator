"""Native route approval, implementation guards, and SDD lifecycle reconciliation."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.bmad_project import output_dirs as bmad_output_dirs
from scripts.cao_artifacts import artifact_reference
from scripts.cao_defaults import SDD_CATALOG
from scripts.cao_instructions import cao_invocation
from scripts.cao_path import path_assessment_instruction, path_assessment_state
from scripts.cao_plans import _checkbox_tasks, provider_verification_plan_state
from scripts.cao_provider_workflows import (
    provider_native_skills,
    provider_output_dirs,
    workflow_skill_identity,
)
from scripts.cao_providers import detect_current_host, host_session_identity
from scripts.cao_runtime import (
    action,
    ensure_initialized,
    locked_command,
    locked_repo,
    new_id,
    now,
    rel,
    repo_root,
    sha256_file,
    verification_scripts,
)
from scripts.cao_snapshot import verification_snapshot
from scripts.cao_work_store import (
    _refresh_work,
    execution_phase,
    list_lifecycle_events,
    load_index,
    queue_event,
    resolve_work,
    save_work,
    work_summary,
)
from scripts.light_spec_policy import binding_digest
from scripts.native_obligations import implementation_contract, implementation_state
from scripts.native_route import (
    bind_request_context,
    build_route as native_build_route,  # Retain the compatibility facade export.
    intent_hash,
    normalize_decision as normalize_route_decision,
    owned_file as native_owned_file,
    route_state,
    spec_preparation_diagnostics,
    digest as route_digest,
)
from scripts.path_guard import provider_artifact, repo_file
from scripts.provider_restart import restart_state
from scripts.route_inputs import (
    declaration_path as route_declaration_path,
    read_declaration as read_route_declaration,
)
from scripts.technology_guidance import technology_instruction


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _native_md_files(repo: Path, provider: str, root: Path, *, recursive: bool = True) -> list[Path]:
    dirs = [directory for directory in provider_output_dirs(provider, repo)
            if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
    files = []
    for path in (root.rglob("*.md") if recursive else root.glob("*.md")):
        try:
            owned = provider_artifact(repo, str(path), dirs)
        except (ValueError, OSError, RuntimeError):
            continue
        if owned.is_file() and owned not in files:
            files.append(owned)
    return sorted(files)


def _native_candidates(repo: Path, provider: str, work: dict[str, Any], native_ref: str) -> tuple[list[Path], Path | None]:
    """Return candidate artifact paths scoped to this Work Item, never the whole SDD tree by default."""
    sdd = work.get("sdd") or {}
    binding = sdd.get("spec_binding")
    if binding is not None:
        # The explicit Work-bound SPEC is authority even when its mutable native
        # status makes acceptance hashes stale. Never silently rebind those hashes.
        if (not isinstance(binding, dict) or binding.get("provider") != provider
                or binding.get("requirement_sha256") != (work.get("requirement") or {}).get("sha256")):
            return [], None
        spec_ref = binding.get("spec")
        if not isinstance(spec_ref, dict):
            return [], None
        native_ref = str(spec_ref.get("path") or "")
        if not native_ref:
            return [], None
    if native_ref:
        try:
            dirs = [directory for directory in provider_output_dirs(provider, repo)
                    if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
            ref_path = provider_artifact(repo, native_ref, dirs)
        except (ValueError, OSError, RuntimeError):
            return [], None
        if ref_path.is_file():
            if provider == "bmad" or ref_path.name.lower() == "tasks.md":
                return [ref_path], ref_path
            parent = ref_path.parent
            feature_root = repo.resolve() / ("openspec/changes" if provider == "openspec" else "specs")
            if ref_path.is_relative_to(feature_root):
                parts = ref_path.relative_to(feature_root).parts
                if len(parts) >= 2:
                    parent = (feature_root / parts[0] / parts[1]
                              if provider == "openspec" and parts[0] == "archive" and len(parts) >= 3
                              else feature_root / parts[0])
            return _native_md_files(repo, provider, parent, recursive=False), parent
        if ref_path.is_dir():
            return _native_md_files(repo, provider, ref_path), ref_path
        return [], None  # A broken explicit association must not adopt another file.
    requirement_path = str((work.get("requirement") or {}).get("path") or "")
    title = str(work.get("title") or "")
    identity_slug = lambda value: re.sub(r"[^\w]+", "-", value.casefold(), flags=re.UNICODE).replace("_", "-").strip("-")
    slugs = [identity_slug(Path(requirement_path).stem), identity_slug(title)]
    slugs = [value for value in dict.fromkeys(slugs) if value]

    if provider == "bmad":
        root = repo / "_bmad-output/implementation-artifacts"
        if not root.is_dir():
            return [], None
        all_specs = sorted((p for p in _native_md_files(repo, provider, root, recursive=False)
                            if p.parent == root.resolve() and p.name.startswith("spec-")),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        def bmad_matches(path: Path) -> bool:
            file_slug = identity_slug(path.stem.removeprefix("spec-"))
            return bool(file_slug and any(file_slug == wanted or file_slug in wanted or wanted in file_slug for wanted in slugs))
        selected = [p for p in all_specs if bmad_matches(p)]
        # Prefer an exact identity match before considering partial slug matches.
        # When more than one candidate remains, fail closed: none of them may
        # drive lifecycle state until the current Work Item is unambiguously bound.
        exact = [p for p in selected if identity_slug(p.stem.removeprefix("spec-")) in slugs]
        if len(exact) == 1:
            return exact, exact[0].parent
        if exact:
            return [], None
        if len(selected) == 1:
            return selected, selected[0].parent
        return [], None

    root = repo / ("openspec/changes" if provider == "openspec" else "specs")
    if not root.is_dir():
        return [], None
    dirs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "archive"]
    matching = [p for p in dirs if any(identity_slug(p.name) == wanted or identity_slug(p.name) in wanted or wanted in identity_slug(p.name) for wanted in slugs)]
    exact = [p for p in matching if identity_slug(p.name) in slugs]
    if len(exact) == 1:
        matching = exact
    elif exact or len(matching) != 1:
        return [], None
    feature_dir = matching[0]
    return _native_md_files(repo, provider, feature_dir, recursive=False), feature_dir


def _read_native_status(provider: str, candidates: list[Path]) -> tuple[str | None, str | None, str | None, dict[str, Any] | None]:
    """Extract only explicit machine status or task-checkbox evidence from work-specific files."""
    if provider == "bmad":
        states = []
        for path in candidates:
            try:
                content = path.read_text(encoding="utf-8-sig")[:65536]
            except (OSError, UnicodeError):
                continue
            if not content.startswith("---"):
                continue
            end = content.find("\n---", 3)
            if end < 0:
                continue
            frontmatter = content[3:end]
            entries = re.findall(r"(?im)^[ \t]*status[ \t]*:[ \t]*(.*)$", frontmatter)
            if len(entries) != 1:
                continue
            match = re.fullmatch(r"(?:'([a-z_-]+)'|\"([a-z_-]+)\"|([a-z_-]+))[ \t]*(?:#.*)?", entries[0], re.I)
            if match:
                raw = next(value for value in match.groups() if value is not None).lower().replace("-", "_")
                states.append((raw, path.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), None))
        return states[0] if len(states) == 1 else (None, None, None, None)

    task_files = [p for p in candidates if p.name.lower() == "tasks.md"]
    if len(task_files) != 1:
        return None, None, None, None
    tasks = task_files[0]
    try:
        content = tasks.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None, None, None, None
    tasks_read = _checkbox_tasks(content)
    checked = sum(task["done"] for task in tasks_read)
    unchecked = len(tasks_read) - checked
    total = checked + unchecked
    if not total:
        return None, tasks.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), None
    raw = "completed" if checked == total else "in_progress" if checked > 0 else "ready_for_dev"
    return raw, tasks.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), {"checked": checked, "unchecked": unchecked, "total": total}


def _canonical_native_state(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.lower().replace("-", "_").strip()
    if value in {"ready_for_dev", "ready", "ready_to_implement", "approved"}:
        return "ready"
    if value in {"in_progress", "in_process", "implementing", "implementation", "in_review", "in_reviewing"}:
        return "in_progress" if value not in {"in_review", "in_reviewing"} else "in_review"
    if value in {"done", "completed", "complete", "closed"}:
        return "completed"
    if value in {"blocked", "failed", "cancelled", "canceled"}:
        return value
    return None


def native_sync_state(repo: Path, work: dict[str, Any]) -> dict[str, Any]:
    """Read-only source/observation diagnostics, separate from acceptance readiness."""
    sdd = work.get("sdd") or {}
    provider = str(sdd.get("provider") or "").lower()
    if provider not in SDD_CATALOG or work.get("status") == "completed":
        return {"state": "not_applicable"}
    plan = provider_verification_plan_state(repo, work)
    if plan["state"] != "not_applicable":
        if plan["state"] == "blocked":
            return {"state": "source_unavailable", "source": None, "next": plan["next"], "verification_plan": plan}
        progress = bmad_progress_observation(repo, work, plan)
        return {"state": "observed" if plan["canonical"] else "status_unavailable", "source": ", ".join(plan["observations"]),
                "raw": plan["raw"], "canonical": plan["canonical"],
                "next": plan.get("next"), "verification_plan": plan, **progress}
    candidates, _ = _native_candidates(repo, provider, work, str(sdd.get("native_ref") or ""))
    if not candidates:
        explicit = sdd.get("spec_binding") is not None or bool(sdd.get("native_ref"))
        return {"state": "source_unavailable" if explicit else "unbound", "source": None,
                "next": "Review the Work's native source path and provider. For light, bind its actual SPEC with `cao sdd spec bind`; for other profiles, use the current Work's native reference. Do not guess another file or restart intake."}
    raw, source, _hash, _counts = _read_native_status(provider, candidates)
    canonical = _canonical_native_state(raw)
    path = next((p.relative_to(repo).as_posix() for p in candidates if p.name == source), None)
    return {"state": "observed" if canonical else "status_unavailable", "source": path,
            "raw": raw, "canonical": canonical,
            "next": None if canonical else "Follow the installed provider's native status/task conventions; do not infer execution from file existence."}


def bmad_progress_observation(repo: Path, work: dict, plan: dict) -> dict:
    decision = (work.get('sdd') or {}).get('route_decision') or {}
    if decision.get('provider') != 'bmad' or not decision.get('primary_artifact'):
        return {}
    try:
        path = native_owned_file(repo, decision['primary_artifact'], bmad_output_dirs(repo))
    except (ValueError, OSError, RuntimeError):
        return {'progress_conflict': {'reason': 'bound SPEC unavailable'},
                'next': 'Restore the bound native SPEC; do not adopt another file or infer execution from plan state.'}
    raw, source, sha, counts = _read_native_status('bmad', [path])
    canonical = _canonical_native_state(raw)
    if canonical and plan.get('canonical') and canonical != plan['canonical']:
        result = {'progress_conflict': {'spec': decision['primary_artifact'], 'spec_state': canonical,
                                       'plan_state': plan['canonical']},
                  'next': 'Synchronize the active SPEC and this requirement\'s current Story using the native status steps; preserve other Stories. Plan completion still requires ALL bound items done.'}
        # Show actual native implementation/review activity without promoting an
        # unfinished plan to completion or rewriting any provider-owned file.
        if canonical in {'in_progress', 'in_review'} and plan['canonical'] in {'ready', 'completed'}:
            result.update(canonical=canonical, raw=raw)
        return result
    return {}


def native_route_state(repo: Path, work: dict) -> dict:
    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    # Installed Build is an agent workflow with native routing and checkpoints.
    # Historical providers without this capability retain their native contracts.
    required = bool(sdd.get("native_route_required")) or provider == "bmad" and any(
        item["name"] == "bmad-build" for item in provider_native_skills(repo, provider))
    return route_state(repo, work, provider_output_dirs(provider, repo), required)


def route_preparation_action(repo: Path, work: dict, state: dict) -> dict:
    command = cao_invocation()["command"]
    provider = (work.get("sdd") or {}).get("provider")
    target = route_declaration_path(work)
    common = {"stage": "execution", "reason": state["reason"], "declaration_path": target}
    if state["reason"] != "NATIVE_ROUTE_REQUIRED":
        kind = "human" if state["reason"] in {"NATIVE_SPEC_APPROVAL_REQUIRED", "NATIVE_SPEC_CLARIFICATION_REQUIRED", "NATIVE_IMPLEMENTATION_PAUSED"} else "agent"
        return action(kind, state["next"], **common)
    skills = provider_native_skills(repo, provider)
    workflow = (work.get("sdd") or {}).get("workflow") or {}
    selected = workflow_skill_identity(repo, provider, workflow.get("entrypoint") or "")
    skill = next((item for item in skills if item["name"] == (selected or "bmad-build")), None)
    from scripts.project_context import context_state
    needs_technology = (not context_state(repo, work=work)["ready"] or
                        not workflow.get("name") and (work.get("path_assessment") or {}).get("profile") != "light")
    technology = (technology_instruction(command) if needs_technology
                  else "Inherit the accepted project background; do not ask for the stack again. Confirm authorization before a material stack change. ")
    instruction = technology + (f"Read the complete installed Skill `{repo / skill['rel_path']}`; invoke `{skill['name']}` "
                   "and use its exact renderer only when required. Renderer success loads instructions, not completed execution. "
                   if skill else "Read the selected provider's installed native workflow instructions. ")
    if (work.get("path_assessment") or {}).get("profile") != "light" and not workflow.get("name"):
        common["reason"] = "NATIVE_WORKFLOW_SELECTION_REQUIRED"
        entry = skill["name"] if skill else "<installed-native-workflow>"
        instruction += (f'Select the actual workflow with `{command} sdd workflow select --name "{entry}" '
                        f'--entrypoint "{entry}" --work {work["id"]}` before preparing the route. ')
    else:
        ref = (work.get("sdd") or {}).get("native_ref")
        diagnostics = spec_preparation_diagnostics(repo, work, provider_output_dirs(provider, repo))
        if diagnostics:
            common["reason"] = "NATIVE_SPEC_PREPARATION_REQUIRED"
            common.update(diagnostic=diagnostics[0], diagnostics=diagnostics)
            instruction += " ".join(f"Repair `{item['path'] or 'sdd.native_ref'}` ({item['field']}): {item['expected']}." for item in diagnostics) + " "
            instruction += (f"Continue the selected native workflow `{workflow.get('name') or 'bmad-build'}` and its planning prerequisites; "
                            "do not switch workflows merely because the route is missing. Execute only native planning/spec preparation now, "
                            "then immediately bind the actual primary Build SPEC "
                            f"with `{command} sdd artifact bind --artifact <actual-native-spec.md>`. ")
        else:
            instruction += f"Review the bound native SPEC `{ref}` and actual routing evidence. "
    instruction += (f"Once the workflow and SPEC exist, run `{command} sdd route prepare` to create/reuse `{target}` "
                    "(CAO input, separate from native artifacts and managed decisions). Fill the actual route, reason, version, artifacts and omissions. "
                    f"Use `{command} sdd route rule --source <native-rule-file> --start-line <first> --end-line <last>` "
                    "to write exact quotes and hashes; do not summarize or manually reconstruct evidence. "
                    f"Run `{command} sdd route check --declaration {target}` and repair ALL diagnostic fields before "
                    f"`{command} sdd route propose --declaration {target}`. --declaration is a file path, not inline JSON. "
                    "Read the bundled native route contract for all fields; for BMAD route is direct-build/full-planning "
                    "and build_route is dispatch/oneshot. Do not guess route names, rules or artifacts. "
                    "Do not begin business implementation or restart intake.")
    return action("agent", instruction, **common)


def implementation_preflight(repo: Path, work: dict) -> dict:
    from scripts.cao_completion import completion_next_instruction, work_sdd_ready
    from scripts.cao_tool_policy import verification_baseline_state

    restart = restart_state(repo, detect_current_host())
    if not restart["ready"]:
        return restart
    if not work_sdd_ready(repo, work)[0]:
        provider = (work.get("sdd") or {}).get("provider")
        return {"ready": False, "reason": "SDD_NOT_INSTALLED", "next": f"Restore the Work's provider with `cao sdd install --provider {provider} --yes`; do not restart intake."}
    for state in (verification_baseline_state(repo, work), path_assessment_state(repo, work)):
        if not state["ready"]:
            next_step = state.get("next") or path_assessment_instruction(work)
            if state.get("reason") == "requirement changed since work started":
                next_step = completion_next_instruction(repo, work, state) + " " + next_step
            return {**state, "next": next_step}
    route = native_route_state(repo, work)
    return {**route, "next": route_preparation_action(repo, work, route)["instruction"]} if not route["ready"] else route


def observe_implementation_read(repo: Path, work: dict, raw: dict, session_id: str) -> None:
    from scripts.cao_tool_policy import _collect_path_strings, _collect_values_for_keys, _event_key

    if _event_key(raw, 'tool_name', 'toolname', 'name').lower() != 'read' or raw.get('isError'):
        return
    try:
        contract = implementation_contract(repo, work)
    except (ValueError, OSError, RuntimeError):
        return
    if not contract:
        return
    content = '\n'.join(_collect_values_for_keys(raw.get('content') or [], {'text'}))
    source = repo / contract['path']
    if source.read_text().strip() not in content:
        return  # A partial read is not evidence of loading the complete native step.
    targets = _collect_path_strings(raw.get('input') or raw.get('tool_input') or {})
    if not any((Path(value) if Path(value).is_absolute() else repo / value).resolve() == source for value in targets):
        return
    work['sdd']['implementation_preparation'] = {'contract': contract, 'route_id': work['sdd']['route_decision']['id'],
                                               'session_id': session_id, 'observed_at': now()}
    save_work(repo, work)


def implementation_write_guard(repo: Path, work: dict, raw: dict, session_id: str, kind: str) -> dict:
    state = implementation_state(repo, work)
    if state['ready']:
        return state
    if state['reason'] != 'NATIVE_IMPLEMENTATION_HANDOFF_REQUIRED':
        return state
    contract = implementation_contract(repo, work)
    preparation = work['sdd'].get('implementation_preparation') or {}
    if preparation.get('contract') != contract or preparation.get('route_id') != work['sdd']['route_decision']['id']:
        return state
    spec = native_owned_file(repo, work['sdd']['route_decision']['primary_artifact'], bmad_output_dirs(repo))
    text = spec.read_text()
    status, _, _, _ = _read_native_status('bmad', [spec])
    if status not in {'in_progress', 'in_review', 'done'} or not re.search(r'(?m)^baseline_commit:\s*\S+', text):
        return {'ready': False, 'reason': 'NATIVE_IMPLEMENTATION_STATUS_REQUIRED',
                'next': 'Before product implementation, use native step-03 to set SPEC in-progress and baseline_commit (HEAD or NO_VCS), and synchronize the current Story.'}
    tools = raw.get('host_tools')
    unavailable = isinstance(tools, list) and not {'subagent', 'subagents_enable'}.intersection(tools)
    mode = 'subagent' if kind == 'subagent' else 'fallback' if unavailable else 'native-override' if not contract['subagent_default'] else None
    if mode is None:
        return state
    work['sdd']['native_implementation'] = {'contract': contract, 'route_id': work['sdd']['route_decision']['id'],
        'requirement_sha256': work['requirement']['sha256'], 'session_id': session_id, 'mode': mode, 'observed_at': now()}
    queue_event(work, {'event': 'sdd.implementation.observed', 'work_item_id': work['id'],
                       'mode': mode, 'session_id': session_id, 'occurred_at': now()})
    save_work(repo, work)
    return {'ready': True}


def approval_request_contexts(repo: Path, work: dict, host: str | None = None) -> list[dict[str, str]]:
    from scripts.cao_sessions import list_sessions

    contexts = [{"host": session["host"], "session_id": session["host_session_id"]}
                for session in list_sessions(repo)
                if session.get("work_item_id") == work["id"]
                and session.get("kind") == "primary" and session.get("status") == "active"]
    identity = host_session_identity(host)
    if identity:
        context = {"host": identity[0], "session_id": identity[1]}
        if context not in contexts:
            contexts.append(context)
    return contexts


def expose_approval_context(repo: Path, work: dict | None, host: str, session_id: str) -> None:
    if not work or work.get("status") != "active":
        return
    changed = False
    for record in ((work.get("sdd") or {}).get("spec_approval") or {},
                   work.get("verification_review") or {}):
        if record.get("status") == "pending" or record.get("stop_after_approval"):
            changed = bind_request_context(record, work["id"], host, session_id) or changed
    if changed:
        save_work(repo, work)


def ensure_verification_review(repo: Path, work: dict, cfg: dict) -> dict:
    """Reuse only a current Work-bound request; legacy approval is not transferable."""
    current_hash = verification_snapshot(cfg, repo)
    review = work.get("verification_review") or {}
    if (review.get("config_sha256") != current_hash or review.get("work_item_id") != work["id"]
            or "contexts" not in review):
        review = {"id": new_id("baseline-review"), "status": "pending",
                  "work_item_id": work["id"], "contexts": approval_request_contexts(repo, work),
                  "config_sha256": current_hash, "requested_at": now(),
                  "previous_config_sha256": (work.get("acceptance") or {}).get("config_sha256"),
                  "scripts": verification_scripts(cfg)}
        work["verification_review"] = review
        save_work(repo, work)
    return review


@locked_command
def cmd_sdd_route(args: argparse.Namespace) -> int:
    from scripts.cao_guidance import agent_guidance, current_work_action

    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    if args.route_action == "show":
        print(json.dumps({"ok": True, "route": (work.get("sdd") or {}).get("route_decision"),
                          "history": (work.get("sdd") or {}).get("route_history", []),
                          "state": native_route_state(repo, work)}, ensure_ascii=False)); return 0
    raw = None
    try:
        if args.route_action in {"prepare", "rule"}:
            from scripts.route_input_commands import dispatch
            result = dispatch(args, repo, work)
            print(json.dumps(result, ensure_ascii=False)); return 0
        args.declaration = args.declaration or route_declaration_path(work)
        if args.declaration.lstrip().startswith(("{", "[")):
            raise ValueError("NATIVE_ROUTE_DECLARATION_PATH_REQUIRED")
        if not path_assessment_state(repo, work)["ready"]:
            raise ValueError("PATH_ASSESSMENT_REQUIRED")
        declaration = repo_file(repo, args.declaration)
        if not declaration.is_file():
            raise ValueError("NATIVE_ROUTE_DECLARATION_FILE_MISSING")
        raw = read_route_declaration(declaration)
        provider = work["sdd"]["provider"]
        decision = normalize_route_decision(repo, work, raw, provider_output_dirs(provider, repo),
                                            provider_native_skills(repo, provider))
        if args.route_action == "check":
            print(json.dumps({"ok": True, "status": "validated", "declaration_path": rel(declaration, repo),
                              "decision": decision, "next": "Declaration fields are valid; propose it to record the decision. Native approval and execution remain required."}, ensure_ascii=False)); return 0
        old = work["sdd"].get("route_decision")
        if getattr(args, 'repair_evidence', False):
            from scripts.route_repair import relocate, migrate_evidence
            repaired, moves = relocate(repo, old, decision)
            repaired.setdefault('evidence_repairs', []).append({'at': now(), 'moves': moves,
                'declaration': {'path': rel(declaration, repo), 'sha256': sha256_file(declaration)}})
            work['sdd']['route_decision'] = repaired
            migrate_evidence(work['sdd'], moves)
            queue_event(work, {'event': 'sdd.route.evidence_relocated', 'work_item_id': work['id'],
                              'source': 'cao', 'route_id': repaired['id'], 'moves': moves, 'occurred_at': now()})
            save_work(repo, work)
            print(json.dumps({'ok': True, 'status': 'evidence_repaired', 'route': repaired,
                              'next': 'Continue the current workflow. Frozen review input paths remain historical; inspect affected review evidence rather than restarting implementation.'}, ensure_ascii=False))
            return 0
        if old and all(old.get(key) == value for key, value in decision.items()):
            print(json.dumps({"ok": True, "status": "unchanged", "route": old,
                              "implementation": native_route_state(repo, work)}, ensure_ascii=False)); return 0
        decision.update(id=new_id("route"), accepted_at=now(), assessed_by="agent",
                        declaration={"path": rel(declaration, repo), "sha256": sha256_file(declaration)},
                        supersedes=old.get("id") if old else None,
                        previous_route=old.get("route") if old else None)
        from scripts.bmad_units import archive as archive_native_unit
        archive_native_unit(repo, work, decision['primary_artifact'])
        work["sdd"].setdefault("route_history", []).append(decision)
        work["sdd"]["route_decision"] = decision
        work["sdd"]["native_ref"] = decision["primary_artifact"]
        work["sdd"].pop("spec_approval", None)
        work["sdd"].pop("spec_task_scope", None)
        work["verification"] = {}
        work["gate_status"] = {}
        # Reselecting a route must never retain a completed workflow certificate.
        workflow = work["sdd"].get("workflow")
        if workflow:
            workflow["status"] = "selected"
            for key in ("execution", "completed_at", "artifact_hashes", "failure_exception"):
                workflow.pop(key, None)
        for artifact in decision["required_artifacts"]:
            ref = artifact_reference(repo, artifact["path"], role=artifact["role"])
            refs = work["sdd"].setdefault("artifacts", [])
            refs[:] = [r for r in refs if r.get("path") != ref["path"]]
            refs.append(ref)
        queue_event(work, {"event": "sdd.route.accepted", "work_item_id": work["id"],
                           "source": "cao", "decision": decision, "occurred_at": now()})
        save_work(repo, work)
        reconcile_native_sdd_state(repo, work)
        state = native_route_state(repo, work)
        print(json.dumps({"ok": True, "route": decision, "implementation": state,
                          "next": state.get("next") or "Follow the accepted native route; synchronize progress with `cao sdd sync`."}, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        code = "NATIVE_ROUTE_DECLARATION_JSON_INVALID" if isinstance(exc, json.JSONDecodeError) else str(exc)
        current = current_work_action(repo, work, check_completion=False)
        result = {"ok": False, "error": code, "next": current["instruction"],
                  "guidance": agent_guidance(repo, work, next_action=current)}
        diagnostics = getattr(exc, "diagnostics", None)
        if not diagnostics and code.startswith("NATIVE_ROUTE_DECLARATION_"):
            diagnostics = [{"code": code, "field": "--declaration" if code.endswith(("PATH_REQUIRED", "FILE_MISSING")) else "$",
                            "expected": "an existing JSON declaration path; prepare the input after native SPEC preparation" if code.endswith(("PATH_REQUIRED", "FILE_MISSING")) else "a valid JSON object in the declaration file",
                            "line": getattr(exc, "lineno", None), "column": getattr(exc, "colno", None)}]
        if diagnostics:
            result.update(diagnostic=diagnostics[0], diagnostics=diagnostics)
        if diagnostics and code.startswith("NATIVE_ROUTE_RULE"):
            result["next"] = ("Repair every listed diagnostic field; hashes and quotes are checked independently. "
                              "Use `cao sdd route prepare --from <existing-declaration>` for a legacy input, then "
                              "`cao sdd route rule --source <actual-native-file> --start-line <first> --end-line <last> --index <rule-index>` "
                              "to replace the named rule with literal source evidence. Run `cao sdd route check` before proposing again. "
                              "Do not resubmit unchanged evidence or rewrite native rules to make a quote match.")
            result["guidance"] = agent_guidance(repo, work, next_action={**current, "reason": "NATIVE_ROUTE_RULE_EVIDENCE_MISMATCH"})
        elif diagnostics and code != "NATIVE_SPEC_PREPARATION_REQUIRED":
            result["next"] = " ".join(f"Repair {d['field']}: {d['expected']}." for d in diagnostics)
            result["next"] += " Run `cao sdd route check` once after repairing the declaration or named source. Do not resubmit unchanged evidence, lower the profile, invent replacement duties or begin implementation."
            result["guidance"] = agent_guidance(repo, work, next_action={"stage": "execution", "reason": "NATIVE_ROUTE_DECLARATION_INVALID"})
        if code in {"NATIVE_ROUTE_DECLARATION_PATH_REQUIRED", "NATIVE_ROUTE_DECLARATION_FILE_MISSING"}:
            result["reason"] = ("--declaration expects a path to an existing JSON file, not inline JSON. "
                                "Use the Work-specific CAO input path after native preparation; "
                                "a rejected write did not create the file.")
        if work["sdd"]["provider"] in SDD_CATALOG:
            result["declaration_path"] = route_declaration_path(work)
        if code == "NATIVE_ROUTE_NAME_INVALID":
            result["allowed_routes"] = ["direct-build", "full-planning"]
            result["allowed_build_routes"] = ["dispatch", "oneshot"]
        input_sha = None
        try:
            candidate = repo_file(repo, getattr(args, "declaration", None) or route_declaration_path(work))
            if candidate.is_file() and candidate.stat().st_size <= 1024 * 1024:
                input_sha = sha256_file(candidate)
        except (ValueError, OSError, RuntimeError, TypeError):
            pass
        result["repair_id"] = route_digest({"input": raw, "input_sha256": input_sha, "error": code, "diagnostics": result.get("diagnostics")})
        result["retry_policy"] = {"unchanged_input": "repair_instead_of_retry",
                                  "retry_when": "the declaration or named source has changed; follow diagnostics before choosing another command"}
        print(json.dumps(result, ensure_ascii=False)); return 2


@locked_command
def cmd_sdd_artifact_bind(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        path = native_owned_file(repo, args.artifact, provider_output_dirs(work["sdd"]["provider"], repo))
        value = rel(path, repo)
        decision = work["sdd"].get("route_decision")
        if decision and decision["primary_artifact"] != value:
            raise ValueError("NATIVE_ROUTE_SOURCE_CHANGE_REQUIRES_DECISION")
        work["sdd"]["native_ref"] = value
        refs = work["sdd"].setdefault("artifacts", [])
        refs[:] = [r for r in refs if r.get("path") != value]
        refs.append(artifact_reference(repo, value, role="native-progress-source"))
        queue_event(work, {"event": "sdd.artifact.bound", "work_item_id": work["id"],
                           "path": value, "sha256": sha256_file(path), "occurred_at": now()})
        save_work(repo, work)
        reconcile_native_sdd_state(repo, work)
        result = {"ok": True, "native_sync": native_sync_state(repo, work),
                  "implementation": native_route_state(repo, work)}
        preparation = route_preparation_action(repo, work, {"reason": "NATIVE_ROUTE_REQUIRED"})
        if preparation.get("diagnostics"):
            result["preparation"] = preparation
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2


@locked_command
def cmd_sdd_approval(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    if args.approval_action == "request":
        decision = work["sdd"].get("route_decision")
        state = native_route_state(repo, work)
        if not decision or (not state["ready"] and state["reason"] != "NATIVE_SPEC_APPROVAL_REQUIRED"):
            print(json.dumps({"ok": False, "error": state.get("reason", "NATIVE_ROUTE_REQUIRED")})); return 2
        path = native_owned_file(repo, decision["primary_artifact"], provider_output_dirs(work["sdd"]["provider"], repo))
        existing = work["sdd"].get("spec_approval") or {}
        if (existing.get("route_decision_id") == decision["id"]
                and existing.get("intent_sha256") == intent_hash(path)
                and existing.get("work_item_id") == work["id"]
                and existing.get("status") in {"pending", "approved"}):
            print(json.dumps({"ok": True, "status": "unchanged", "approval": existing,
                              "implementation": state}, ensure_ascii=False)); return 0
        approval = {"id": new_id("approval"), "status": "pending", "requested_at": now(),
                    "work_item_id": work["id"], "contexts": approval_request_contexts(repo, work),
                    "questions": [],
                    "path": decision["primary_artifact"], "intent_sha256": intent_hash(path),
                    "requirement_sha256": work["requirement"]["sha256"], "route_decision_id": decision["id"]}
        work["sdd"]["spec_approval"] = approval
        queue_event(work, {"event": "sdd.spec.approval_requested", "work_item_id": work["id"],
                           "approval": approval, "occurred_at": now()})
        save_work(repo, work)
    approval = work["sdd"].get("spec_approval") or {}
    next_step = ("Approval is recorded; follow the native workflow from its current stage and synchronize progress."
                 if approval.get("status") == "approved" else
                 "Resolve native Open Questions first. Present the complete spec summary and path. Ask the user to choose 批准并继续 / 批准并停止 (Approve and continue / Approve and stop), or review. Send the approval phrase as a standalone message without appended explanations. The host records the actual reply; answering implementation options is not approval.")
    if approval.get("stop_after_approval"):
        next_step = "The user approved and stopped; wait for explicit continuation of implementation."
    print(json.dumps({"ok": True, "approval": approval or None, "next": next_step}, ensure_ascii=False)); return 0


@locked_command
def cmd_sdd_sync(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"})); return 2
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({"ok": True, "work_item": work_summary(work),
                      "native_sync": native_sync_state(repo, work),
                      "implementation": native_route_state(repo, work)}, ensure_ascii=False)); return 0


@locked_repo
def reconcile_native_sdd_state(repo: Path, work: dict[str, Any]) -> bool:
    """Reconcile a Work Item with its work-specific native SDD evidence and emit durable events.

    Artifact discovery may bind a unique matching native reference, but artifact existence alone
    never means execution has started. Provider-native status/task progress is required for phase
    projection. Native completion advances CAO to review, never directly to closed/done.
    """
    from scripts.cao_guidance import work_prerequisite_action

    _refresh_work(repo, work)
    sdd = work.get("sdd") if isinstance(work.get("sdd"), dict) else {}
    provider = str(sdd.get("provider") or "").lower()
    if provider not in {"bmad", "openspec", "speckit"} or work.get("status") not in {"active", "suspended"}:
        return False
    if provider == 'bmad' and (sdd.get('native_loopback') or {}).get('status') == 'reimplementing':
        return False  # The old native terminal status cannot undo an explicit loopback.
    changed = False
    native_ref = str(sdd.get("native_ref") or "").strip()
    candidates, anchor = _native_candidates(repo, provider, work, native_ref)
    if isinstance(sdd.get("spec_binding"), dict) and anchor and candidates:
        bound = str(sdd["spec_binding"]["spec"]["path"])
        if native_ref != bound:
            sdd["native_ref"] = native_ref = bound
            changed = True
    if not native_ref and anchor and candidates:
        preferred_names = {
            "bmad": ("spec-",),
            "openspec": ("proposal.md", "tasks.md", "design.md"),
            "speckit": ("spec.md", "plan.md", "tasks.md"),
        }[provider]
        preferred: list[Path] = []
        for wanted in preferred_names:
            preferred = [p for p in candidates if (p.name.lower().startswith(wanted) if wanted.endswith("-") else p.name.lower() == wanted)]
            if preferred:
                break
        # Native directory anchors are valid stable references when no canonical entry file exists.
        chosen = preferred[0] if len(preferred) == 1 else (anchor if anchor.is_dir() and not preferred else None)
        if chosen:
            sdd["native_ref"] = chosen.relative_to(repo).as_posix()
            native_ref = sdd["native_ref"]
            changed = True
    raw_state, status_source, evidence_hash, task_counts = _read_native_status(provider, candidates)
    if status_source:
        source_path = next((p for p in candidates if p.name == status_source), None)
        if source_path:
            status_source = source_path.relative_to(repo).as_posix()
    canonical = _canonical_native_state(raw_state)
    plan = provider_verification_plan_state(repo, work)
    if plan["state"] != "not_applicable":
        if plan.get("spec_task_scope") and sdd.get("spec_task_scope") != plan["spec_task_scope"]:
            sdd["spec_task_scope"] = plan["spec_task_scope"]
            changed = True
        if plan.get("scope") and sdd.get("plan_scope") != plan["scope"]:
            sdd["plan_scope"] = plan["scope"]
            changed = True
        if plan["state"] == "blocked":
            raw_state = canonical = status_source = evidence_hash = task_counts = None
        else:
            raw_state = plan["raw"]
            canonical = plan["canonical"]
            status_source = ", ".join(plan["observations"])
            evidence_hash = binding_digest(plan["observations"])
            total = sum(item["total"] if "total" in item else len(item["states"])
                        for item in plan["observations"].values())
            task_counts = {"total": total, "checked": total - len(plan["pending_plan_items"]),
                           "unchecked": len(plan["pending_plan_items"])}
            progress = bmad_progress_observation(repo, work, plan)
            if progress.get('canonical'):
                raw_state, canonical = progress['raw'], progress['canonical']
                evidence_hash = binding_digest({'plan': plan['observations'], 'spec': sha256_file(repo / work['sdd']['route_decision']['primary_artifact'])})
            if progress.get('progress_conflict') != sdd.get('progress_conflict'):
                if progress.get('progress_conflict'):
                    sdd['progress_conflict'] = progress['progress_conflict']
                else:
                    sdd.pop('progress_conflict', None)
                changed = True
    old_native = sdd.get("native_state") if isinstance(sdd.get("native_state"), dict) else {}
    old_canonical = old_native.get("canonical")
    new_snapshot = {
        "raw": raw_state,
        "canonical": canonical,
        "source": status_source,
        "evidence_sha256": evidence_hash,
        "task_counts": task_counts,
    }
    if canonical is not None and (old_native.get("canonical") != canonical or old_native.get("raw") != raw_state or old_native.get("evidence_sha256") != evidence_hash or old_native.get("source") != status_source):
        revision = int(old_native.get("revision") or 0) + 1
        new_snapshot["revision"] = revision
        new_snapshot["observed_at"] = now()
        from_state = old_canonical or None
        identity = {
            "work_item_id": work.get("id"), "provider": provider, "native_ref": native_ref or status_source,
            "revision": revision, "from_state": from_state, "to_state": canonical,
            "evidence_sha256": evidence_hash,
        }
        idem = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
        base_event = {
            "event_id": "evt-" + idem[:20], "idempotency_key": idem, "work_item_id": work.get("id"),
            "source": provider, "observation_source": "provider_observation", "owner": provider, "native_ref": native_ref or None, "from_state": from_state,
            "to_state": canonical, "raw_state": raw_state, "source_path": status_source,
            "evidence_sha256": evidence_hash, "task_counts": task_counts,
            "plan_scope": sdd.get("plan_scope"), "occurred_at": new_snapshot["observed_at"],
        }
        if old_canonical != canonical:
            queue_event(work, {**base_event, "event": "sdd.native_state.changed"})
            if canonical == "ready":
                queue_event(work, {**base_event, "event": "task.ready", "idempotency_key": idem + ":ready", "event_id": "evt-" + idem[:16] + "ready"})
            elif canonical == "in_progress":
                queue_event(work, {**base_event, "event": "task.execution.started", "idempotency_key": idem + ":started", "event_id": "evt-" + idem[:16] + "started"})
            elif canonical == "completed":
                queue_event(work, {**base_event, "event": "sdd.execution.completed" if work.get("lifecycle_contract") == 2 else "task.execution.completed", "idempotency_key": idem + ":execution-completed", "event_id": "evt-" + idem[:16] + "complete"})
            elif canonical == "in_review":
                queue_event(work, {**base_event, "event": "sdd.review.started" if work.get("lifecycle_contract") == 2 else "task.review.started", "idempotency_key": idem + ":review", "event_id": "evt-" + idem[:16] + "review"})
            elif canonical == "blocked":
                queue_event(work, {**base_event, "event": "task.blocked", "idempotency_key": idem + ":blocked", "event_id": "evt-" + idem[:16] + "blocked"})
        elif old_native.get("evidence_sha256") != evidence_hash or old_native.get("source") != status_source:
            queue_event(work, {**base_event, "event": "sdd.native_artifact.changed", "idempotency_key": idem + ":artifact", "event_id": "evt-" + idem[:16] + "artifact"})
        sdd["native_state"] = new_snapshot
        changed = True
        work.setdefault("resume", {})["last_native_sync_at"] = new_snapshot["observed_at"]

    from scripts.lifecycle_contract import enabled, prerequisites, next_action
    if enabled(work):
        native_preconditions = prerequisites(repo, work)
        ready = native_preconditions['ready']
        current_action = next_action(repo, work)
        baseline_only = bool(native_preconditions.get('blockers')) and all(
            row.get('obligation') == 'baseline' for row in native_preconditions['blockers'])
        execution = work.setdefault('execution', {})
        phase = execution_phase(execution)
        desired = 'review' if (ready or baseline_only) and current_action['stage'] != 'execution' else 'implementation'
        # Preserve genuine planning/ready phases until native execution starts.
        active = canonical in {'in_progress', 'in_review', 'completed'} or phase in {'review', 'verification'}
        if active and not (ready and phase == 'verification' and current_action['stage'] != 'execution'):
            if execution.get('step') != desired or execution.get('phase') != desired:
                execution.update(step=desired, phase=desired)
                changed = True
        if execution.get('next_action') != current_action:
            execution['next_action'] = current_action
            changed = True
    else:
        # Reconcile the CAO phase on every read, not only on a newly observed native
        # transition. This repairs stale CAO state even if an earlier sync was missed.
        if canonical is not None:
            execution = work.setdefault("execution", {})
            current_step = str(execution.get("step") or "").lower()
            phase = execution_phase(execution)
            later_checkpoint = phase in {"verification", "review", "audit", "release", "done"}
            aggregate_pending_review = (plan["state"] == "pending" or canonical in {"in_progress", "in_review"}) and current_step == "review"
            if aggregate_pending_review:
                later_checkpoint = False  # A single completed SPEC cannot finish the whole requirement.
            if canonical == "ready" and (current_step in {"reopened", "path-selected", "planning", "plan-selected", "sdd-planning", "native-sdd-workflow-selected"} or aggregate_pending_review):
                execution["step"] = "ready-for-dev"
                execution["phase"] = "ready"
                execution["next_action"] = action("agent", f"Native {provider} work is ready; begin implementation using the bound native work item.")
                work.setdefault("resume", {})["summary"] = f"Native SDD reports ready for implementation ({provider})."
                changed = True
            elif canonical in {"in_progress", "in_review"} and current_step != "implementation" and not later_checkpoint:
                execution["step"] = "implementation"
                execution["phase"] = "implementation"
                execution["next_action"] = action("agent", f"Continue implementation using the native {provider} work item; phase synchronized from explicit native status/task progress.")
                work.setdefault("resume", {})["summary"] = f"Native SDD reports implementation in progress ({provider}); CAO phase reconciled."
                changed = True
            elif canonical == "completed" and not later_checkpoint:
                execution["step"] = "review"
                execution["phase"] = "review"
                execution["next_action"] = action("agent", "Native scope reports completion; check bound process and artifact evidence with `cao conformance check`, then run the project baseline with `cao verify`.")
                work.setdefault("resume", {})["summary"] = f"Native SDD reports scope completion ({provider}); CAO moved to review, not closed."
                changed = True
            elif canonical == "blocked":
                blockers = work.setdefault("blockers", [])
                if not any(isinstance(item, dict) and item.get("id") == "native-sdd-state" for item in blockers):
                    blockers.append({"id": "native-sdd-state", "reason": f"Native {provider} state is blocked", "source": status_source, "observed_at": now()})
                    changed = True
            if canonical in {"ready", "in_progress", "in_review", "completed"}:
                blockers = work.get("blockers") or []
                automatic = [item for item in blockers if isinstance(item, dict)
                             and item.get("id") == "native-sdd-state"]
                if automatic:
                    work["blockers"] = [item for item in blockers if item not in automatic]
                    queue_event(work, {"event": "sdd.native_blocker.cleared", "work_item_id": work["id"],
                                       "source": provider, "blockers": automatic, "to_state": canonical,
                                       "occurred_at": now()})
                    changed = True
    # Observation is retained even when it reveals an unauthorized transition.
    # It must not itself satisfy routing or approval prerequisites.
    previous_route_state = sdd.get("implementation_state") or {}
    route = native_route_state(repo, work)
    if route.get("state") != "not_applicable" and sdd.get("implementation_state") != route:
        sdd["implementation_state"] = route
        changed = True
    if not route["ready"]:
        nxt = work_prerequisite_action(repo, work) or route_preparation_action(repo, work, route)
        if work.setdefault("execution", {}).get("next_action") != nxt:
            work["execution"]["next_action"] = nxt
            changed = True
    elif previous_route_state.get("ready") is False:
        work.setdefault("execution", {})["next_action"] = action("agent",
            "Follow the accepted native route from its current stage; record workflow execution after it actually runs, and completion only after native review finishes.")
        changed = True
    if changed:
        work["sdd"] = sdd
        save_work(repo, work)
    return changed


def cmd_lifecycle_events(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    print(json.dumps({"ok": True, "work_item_id": wid, "events": list_lifecycle_events(repo, wid, args.limit)}, ensure_ascii=False, indent=2))
    return 0
