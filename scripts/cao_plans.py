"""Provider-native task plans, scoped bindings, and completion observations."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
from pathlib import Path

from scripts.cao_provider_workflows import provider_output_dirs
from scripts.cao_runtime import (
    ensure_initialized,
    locked_command,
    locked_repo,
    now,
    repo_root,
    sha256_file,
)
from scripts.cao_work_store import _refresh_work, queue_event, resolve_work, save_work
from scripts.native_progress import direct_build_decision, merge_plan_states, spec_task_state
from scripts.path_guard import provider_artifact


# Each provider records its own plan, and each format says "not done yet"
# differently: BMAD keeps epic/story states in `sprint-status.yaml`, OpenSpec
# and Spec Kit keep checkbox lists in `tasks.md`. A Work Item is a single unit,
# so without reading these an Agent can finish one story of thirteen and close
# the requirement as complete (demo11 did exactly that).
SPRINT_DONE_STATES = frozenset({"done"})


SPRINT_STATUS_NAMES = ("sprint-status.yaml", "sprint-status.yml")


# Same task-line contract as OpenSpec's parseTaskLines: CommonMark list
# markers, padded/empty/unknown single-character boxes, only x/X means done.
TASK_LINE_PATTERN = re.compile(r"^\s*(?:[-*+]|[0-9]{1,9}[.)])\s*\[(?:\s*([^\]\s]?)\s*\](?![\(\[])|\s+\])\s*(.*)")


def _checkbox_tasks(content: str) -> list[dict]:
    tasks = []
    for number, line in enumerate(content.split("\n"), 1):
        match = TASK_LINE_PATTERN.match(line)
        if match:
            tasks.append({"done": (match.group(1) or "").lower() == "x",
                          "description": match.group(2).strip(), "line": number})
    return tasks


def _pending_checkbox_tasks(path: Path) -> list[str]:
    """Unchecked tasks, as OpenSpec and Spec Kit write them."""
    return [(task["description"] or f"task at line {task['line']}")[:80]
            for task in _checkbox_tasks(path.read_text(encoding="utf-8-sig")) if not task["done"]]


def _sprint_states(path: Path) -> dict[str, str]:
    states = {}
    in_status = False
    seen_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.fullmatch(r"development_status:\s*(?:#.*)?", line):
            if seen_section:
                raise ValueError("PLAN_STATUS_INVALID: duplicate development_status")
            seen_section = in_status = True
            continue
        if not line[0].isspace():
            in_status = False
            continue
        if not in_status:
            continue
        match = re.fullmatch(r"[ \t]+(?P<name>[^#\s][^:]*?):[ \t]*(?:'(?P<single>[a-z][a-z_-]*)'|\"(?P<double>[a-z][a-z_-]*)\"|(?P<bare>[a-z][a-z_-]*))[ \t]*(?:#.*)?", line)
        if not match:
            raise ValueError("PLAN_STATUS_INVALID: " + path.name)
        name = match.group("name").strip().strip("\"'")
        if name in states:
            raise ValueError("PLAN_STATUS_INVALID: duplicate " + name)
        states[name] = next(match.group(key) for key in ("single", "double", "bare") if match.group(key))
    if not seen_section:
        raise ValueError("PLAN_STATUS_INVALID: missing/empty development_status")
    return states


def _sprint_selected_states(path: Path, ids: list[str]) -> dict[str, str]:
    """Epic selectors include their current and future stories, never another epic."""
    states = _sprint_states(path)
    selected = {}
    for item in ids:
        if item not in states:
            raise ValueError("PLAN_ITEM_MISSING: " + path.name + "#" + item)
        if re.fullmatch(r"epic-\d+", item):
            prefix = item.removeprefix("epic-") + "-"
            stories = {key: value for key, value in states.items()
                       if re.match(r"^" + re.escape(prefix) + r"\d+-", key)}
            if not stories:
                raise ValueError("PLAN_ITEM_INVALID: empty " + item)
            selected.update(stories)
        elif item.startswith("epic-") or item.endswith("-retrospective"):
            raise ValueError("PLAN_ITEM_INVALID: " + item)
        else:
            selected[item] = states[item]
    if not selected:
        raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + path.name)
    return selected


def _plan_pattern(repo: Path, provider: str, value: str) -> str:
    """Normalize explicit tracked-file globs without widening provider ownership."""
    if ".." in Path(value).parts:
        raise ValueError("PLAN_SOURCE_INVALID: " + value)
    dirs = [directory for directory in provider_output_dirs(provider, repo)
            if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
    path = provider_artifact(repo, value, dirs)
    return path.relative_to(repo.resolve()).as_posix()


def _pattern_plan_paths(repo: Path, provider: str, patterns: list[str]) -> set[str]:
    paths = set()
    for value in patterns:
        pattern = _plan_pattern(repo, provider, value)
        matches = [Path(path) for path in glob.glob(str(repo / pattern), recursive=True) if Path(path).is_file()]
        if not matches:
            raise ValueError("PLAN_SOURCE_MISSING: " + pattern)
        for path in matches:
            owned = _plan_pattern(repo, provider, str(path))
            if path.suffix.lower() != ".md":
                raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + owned)
            paths.add(owned)
    return paths


def _associated_task_roots(repo: Path, work: dict) -> set[Path]:
    """Derive feature/change roots only from this Work's actual associations."""
    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    if provider not in {"openspec", "speckit"}:
        return set()
    root = repo.resolve() / ("openspec/changes" if provider == "openspec" else "specs")
    dirs = [directory for directory in provider_output_dirs(provider, repo)
            if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
    refs = [sdd.get("native_ref"), *((sdd.get("workflow") or {}).get("artifacts") or [])]
    binding = sdd.get("spec_binding")
    if binding is not None:
        if (not isinstance(binding, dict) or binding.get("provider") != provider or
                binding.get("requirement_sha256") != (work.get("requirement") or {}).get("sha256")):
            raise ValueError("PLAN_SOURCE_INVALID: SPEC association")
        refs.append((binding.get("spec") or {}).get("path"))
    refs.extend(item.get("path") for item in sdd.get("artifacts", []) if isinstance(item, dict))
    roots = set()
    for value in refs:
        if not value:
            continue
        path = provider_artifact(repo, str(value), dirs)
        if path.is_relative_to(root):
            parts = path.relative_to(root).parts
            if parts and parts[0] != "archive":
                roots.add(root / parts[0])
            elif len(parts) >= 2:
                for migration in sdd.get("archive_relocations", []):
                    moved = repo.resolve() / migration["archive_root"]
                    if path.is_relative_to(moved):
                        roots.add(moved)
    return roots


def bind_plan_scope(repo: Path, work: dict, plan_paths: list[str],
                    selections: dict[str, list[str]], no_plan: bool) -> dict:
    from scripts.cao_artifacts import resolve_artifact_path

    repo = repo.resolve()
    sdd = work.get("sdd") or {}
    roots = [(repo / directory).resolve() for directory in provider_output_dirs(sdd.get("provider"), repo)]
    patterns = sorted(set((sdd.get("plan_scope") or {}).get("patterns") or []) |
                      {_plan_pattern(repo, sdd.get("provider"), str(value)) for value in plan_paths if glob.has_magic(str(value))})
    paths = {resolve_artifact_path(repo, value)[1] for value in plan_paths if not glob.has_magic(str(value))}
    paths.update(_pattern_plan_paths(repo, sdd.get("provider"), patterns))
    normalized = {}
    for value, ids in selections.items():
        relative = resolve_artifact_path(repo, value)[1]
        normalized.setdefault(relative, []).extend(ids)
    paths.update(normalized)
    anchors = set()
    if sdd.get("native_ref"):
        path, _ = resolve_artifact_path(repo, sdd["native_ref"])
        if path.exists():
            anchors.add(path if path.is_dir() else path.parent)
    for value in (sdd.get("workflow") or {}).get("artifacts", []):
        path, _ = resolve_artifact_path(repo, value)
        anchors.add(path.parent)
    anchors.update(_associated_task_roots(repo, work))
    discovered = set()
    ambiguous = False
    public_roots = set(roots) | {(repo / "openspec/changes").resolve(), (repo / "specs").resolve()}
    for anchor in anchors:
        for name in ("tasks.md", "stories.yaml", *SPRINT_STATUS_NAMES):
            path = anchor / name
            if path.is_file():
                discovered.add(path.relative_to(repo).as_posix())
                if anchor in public_roots:
                    ambiguous = True
    old = sdd.get("plan_scope") or {}
    if no_plan:
        if paths or discovered or old.get("mode") == "bound":
            raise ValueError("PLAN_SCOPE_REMOVAL_REQUIRES_REANCHOR")
        return {"version": 1, "mode": "none", "plans": []}
    if old.get("mode") == "bound":
        # Keep prior associations even on explicit repeat; a later native story
        # completion must not replace the requirement-wide scope with one story.
        if not paths:
            paths.update(value for value in discovered if (repo / value).parent not in public_roots)
        for plan in old.get("plans", []):
            paths.add(plan["path"])
            normalized.setdefault(plan["path"], []).extend(plan.get("item_ids") or [])
    if not paths and any(Path(value).name in SPRINT_STATUS_NAMES for value in discovered):
        raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + ", ".join(sorted(discovered)))
    if not paths and ambiguous:
        return {"version": 1, "mode": "unresolved", "plans": []}
    paths = paths or discovered
    if not paths:
        return {"version": 1, "mode": "unresolved", "plans": []}
    plans = []
    for value in sorted(paths):
        path, relative = resolve_artifact_path(repo, value)
        try:
            provider_artifact(repo, relative, provider_output_dirs(sdd.get("provider"), repo))
        except ValueError as exc:
            raise ValueError("PLAN_SOURCE_INVALID: " + relative) from exc
        if not path.is_file():
            raise ValueError("PLAN_SOURCE_INVALID: " + relative)
        ids = sorted(set(normalized.get(value, [])))
        if path.name in SPRINT_STATUS_NAMES:
            if not ids:
                raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + relative)
            try:
                selected = _sprint_selected_states(path, ids)
                # Retain today's story identities as well as dynamic epic selectors:
                # deleting a previously associated story must not shrink the scope.
                ids = sorted(set(ids) | set(selected))
            except ValueError as exc:
                raise ValueError("PLAN_ITEM_INVALID: " + relative + ": " + str(exc)) from exc
        elif sdd.get('provider') == 'bmad' and path.name == 'stories.yaml':
            from scripts.bmad_stories import story_states
            ids, _states, _files = story_states(repo, relative, ids)
        elif (path.name != "tasks.md" and not (sdd.get("provider") == "openspec" and path.suffix.lower() == ".md")) or ids:
            raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
        plan = {"path": relative, "item_ids": ids}
        previous = next((item for item in old.get("plans", []) if item.get("path") == relative), {})
        for field in ("task_ids", "minimum_tasks"):
            if field in previous:
                plan[field] = previous[field]
        if sdd.get("provider") == "openspec":
            plan["sha256"] = sha256_file(path)
        plans.append(plan)
    scope = {"version": 1, "mode": "bound", "plans": plans}
    if patterns:
        scope["patterns"] = patterns
    return scope


def provider_pending_plan_items(repo: Path, work: dict) -> list[str]:
    from scripts.cao_artifacts import resolve_artifact_path

    scope = (work.get("sdd") or {}).get("plan_scope") or {}
    if scope.get("mode") == "none":
        return []
    if scope.get("mode") != "bound" or not scope.get("plans"):
        raise ValueError("PLAN_SCOPE_UNRESOLVED")
    pending = []
    for plan in scope["plans"]:
        path, relative = resolve_artifact_path(repo, plan["path"])
        try:
            provider_artifact(repo, relative, provider_output_dirs((work.get("sdd") or {}).get("provider"), repo))
        except ValueError as exc:
            raise ValueError("PLAN_SOURCE_INVALID: " + plan["path"]) from exc
        if not path.is_file():
            raise ValueError("PLAN_SOURCE_MISSING: " + relative)
        if path.name in SPRINT_STATUS_NAMES:
            ids = plan.get("item_ids") or []
            if not ids:
                raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + relative)
            states = _sprint_selected_states(path, ids)
            pending.extend(f"{relative}#{item} ({state})" for item, state in states.items()
                           if state not in SPRINT_DONE_STATES)
        elif (work.get('sdd') or {}).get('provider') == 'bmad' and path.name == 'stories.yaml':
            from scripts.bmad_stories import story_states
            _ids, states, _files = story_states(repo, relative, plan.get('item_ids', []))
            pending.extend(f'{relative}#{item} ({state})' for item, state in states.items() if state != 'done')
        elif path.name == "tasks.md" or ((work.get("sdd") or {}).get("provider") == "openspec" and path.suffix.lower() == ".md"):
            pending.extend(f"{relative}: {item}" for item in _pending_checkbox_tasks(path))
        else:
            raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
    return pending


def bmad_verification_plan_state(repo: Path, work: dict) -> dict:
    """Current requirement's aggregate BMAD execution gate, independent of SPEC status."""
    from scripts.cao_native_sync import _canonical_native_state, _native_candidates, _read_native_status

    sdd = work.get("sdd") or {}
    if sdd.get("provider") != "bmad":
        return {"ready": True, "state": "not_applicable"}
    scope = sdd.get("plan_scope") or {}
    plans = [plan for plan in scope.get("plans", []) if Path(plan.get("path", "")).name in SPRINT_STATUS_NAMES]
    spec = spec_task_state(repo, work, provider_output_dirs("bmad", repo), _checkbox_tasks)
    from scripts.bmad_stories import state as stories_state
    stories = stories_state(repo, work)
    if stories['state'] == 'blocked':
        return stories
    if stories['state'] != 'not_applicable':
        spec = merge_plan_states(stories, spec)
    if spec["state"] in {"pending", "done"}:
        candidates, _ = _native_candidates(repo, "bmad", work, str(sdd.get("native_ref") or ""))
        raw_status, _, _, _ = _read_native_status("bmad", candidates)
        canonical = _canonical_native_state(raw_status)
        if canonical == "blocked":
            spec.update(ready=False, state="pending", raw=raw_status, canonical=canonical,
                        reason="BMAD_SPEC_BLOCKED", next="Resolve the native SPEC blocker, then synchronize and verify.")
        elif not spec["ready"] and canonical == "in_progress":
            spec.update(raw=raw_status, canonical=canonical)
    decision = direct_build_decision(work)
    selected_route = sdd.get('route_decision') or {}
    if (decision or stories['state'] != 'not_applicable' or selected_route.get('planning_duties')) and not plans and not any(item.get("role") == "sprint"
                                         for item in selected_route.get("required_artifacts", [])):
        return spec
    candidates = {repo / directory / name for directory in provider_output_dirs('bmad', repo) for name in SPRINT_STATUS_NAMES}
    candidates.update(repo / "_bmad-output/implementation-artifacts" / name for name in SPRINT_STATUS_NAMES)
    for ref in [sdd.get("native_ref"), ((sdd.get("spec_binding") or {}).get("spec") or {}).get("path"),
                *((sdd.get("workflow") or {}).get("artifacts") or [])]:
        if ref:
            candidates.update((repo / str(ref)).parent / name for name in SPRINT_STATUS_NAMES)
    if not plans and not any(path.exists() for path in candidates):
        return spec
    next_step = (f"Bind ALL stories or whole epics belonging to this requirement with `cao sdd plan bind --work {work['id']} "
                 "--plan-item <sprint-status.yaml-or-yml>#<story-id-or-epic-N>` (repeat for all associated items). "
                 "Continue the native workflow until every associated story is done, then run `cao verify`.")
    effective_scope = json.loads(json.dumps(scope))
    try:
        if scope.get("mode") != "bound" or not plans:
            raise ValueError("BMAD_PLAN_SCOPE_REQUIRED")
        if scope.get("requirement_sha256") not in {None, (work.get("requirement") or {}).get("sha256")}:
            raise ValueError("PLAN_SCOPE_REQUIREMENT_CHANGED")
        observations = {}
        pending = []
        for plan in plans:
            dirs = [directory for directory in provider_output_dirs("bmad", repo)
                    if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
            path = provider_artifact(repo, plan["path"], dirs)
            if not path.is_file():
                raise ValueError("PLAN_SOURCE_MISSING: " + plan["path"])
            known = set(plan.get("item_ids") or [])
            observed = _sprint_states(path)
            for selector in list(known):
                if re.fullmatch(r"epic-\d+", selector):
                    prefix = selector.removeprefix("epic-") + "-"
                    known.update(key for key in observed if re.match(r"^" + re.escape(prefix) + r"\d+-", key))
            effective = next(item for item in effective_scope["plans"] if item["path"] == plan["path"])
            effective["item_ids"] = sorted(known)
            states = _sprint_selected_states(path, effective["item_ids"])
            observations[plan["path"]] = {"selectors": effective["item_ids"], "states": states}
            pending.extend(f"{plan['path']}#{item} ({state})" for item, state in states.items() if state != "done")
        values = [state for observation in observations.values() for state in observation["states"].values()]
        if not pending:
            raw = "done"
        elif all(state in {"backlog", "drafted", "ready-for-dev", "ready_for_dev"} for state in values):
            raw = "ready_for_dev"
        elif any(state in {"in-progress", "in_progress", "in_process", "review", "in-review", "done"} for state in values):
            raw = "in_progress"
        else:
            raw = None
        sprint = {"ready": not pending, "state": "pending" if pending else "done",
                "raw": raw, "canonical": _canonical_native_state(raw),
                "reason": "BMAD_PLAN_NOT_DONE" if pending else None,
                "scope": effective_scope, "pending_plan_items": pending, "observations": observations, "next": next_step if pending else None}
        reviewing = [item for item in pending if item.endswith("(review)")]
        if reviewing:
            sprint["next"] = ("Invoke the installed bmad-code-review Skill for the completed Build SPEC and matching story key: " + ", ".join(reviewing) + ". Stable BMAD Build marks the sprint story review; native Code Review closes it to done. Do not rerun Build or edit status to skip that workflow.")
        return merge_plan_states(sprint, spec)
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        return {"ready": False, "state": "blocked", "reason": str(exc), "scope": effective_scope, "next": next_step}


def provider_verification_plan_state(repo: Path, work: dict) -> dict:
    """Aggregate all native execution plans associated with this requirement."""
    from scripts.cao_native_sync import _canonical_native_state

    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    if provider == "bmad":
        return bmad_verification_plan_state(repo, work)
    if provider not in {"openspec", "speckit"}:
        return {"ready": True, "state": "not_applicable"}
    scope = sdd.get("plan_scope") or {}
    next_step = (f"Finish every task in ALL this requirement's native plans before `cao verify`. "
                 f"Associate split changes/features with `cao sdd plan bind --work {work['id']} --plan <native-task-file>` "
                 "(repeat for every associated plan). For OpenSpec custom schemas, bind the actual apply.tracks outputs; "
                 "a quoted tracked-file glob also includes new shards. Do not guess unrelated changes or substitute a checklist.")
    effective_scope = None
    try:
        if scope.get("requirement_sha256") not in {None, (work.get("requirement") or {}).get("sha256")}:
            raise ValueError("PLAN_SCOPE_REQUIREMENT_CHANGED")
        paths = {plan["path"] for plan in scope.get("plans", [])}
        paths.update(_pattern_plan_paths(repo, provider, scope.get("patterns") or []))
        for root in _associated_task_roots(repo, work):
            path = root / "tasks.md"
            if path.exists() or path.is_symlink():
                paths.add(path.relative_to(repo.resolve()).as_posix())
        # A direct tasks reference remains a requirement association if its file vanishes.
        ref = sdd.get("native_ref")
        if ref and Path(ref).name == "tasks.md":
            paths.add(str(ref))
        if not paths:
            if scope.get("mode") == "bound":
                raise ValueError("PLAN_SCOPE_UNRESOLVED")
            return {"ready": True, "state": "not_applicable"}
        dirs = [directory for directory in provider_output_dirs(provider, repo)
                if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
        observations = {}
        pending = []
        plans = []
        for value in sorted(paths):
            path = provider_artifact(repo, value, dirs)
            relative = path.relative_to(repo.resolve()).as_posix()
            if not path.is_file():
                raise ValueError("PLAN_SOURCE_MISSING: " + relative)
            if (path.name != "tasks.md" and not (provider == "openspec" and path.suffix.lower() == ".md")):
                raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
            content = path.read_text(encoding="utf-8-sig")
            tasks = _checkbox_tasks(content)
            if not tasks:
                raise ValueError("PLAN_TASKS_UNRECOGNIZED: " + relative)
            ids = []
            for task in tasks:
                match = re.match(r"(T[0-9]+|[0-9]+(?:\.[0-9]+)+)(?=\s|[.:]|$)", task["description"], re.I)
                if match:
                    ids.append(match.group(1).upper())
            if len(ids) != len(set(ids)):
                raise ValueError("PLAN_TASK_ID_DUPLICATE: " + relative)
            previous = next((item for item in scope.get("plans", []) if item.get("path") == relative), {})
            if set(previous.get("task_ids") or []) - set(ids) or len(tasks) < int(previous.get("minimum_tasks") or 0):
                raise ValueError("PLAN_TASK_REMOVED: " + relative)
            plans.append({"path": relative, "item_ids": [], "task_ids": sorted(set(ids)),
                          "minimum_tasks": max(len(tasks), int(previous.get("minimum_tasks") or 0)),
                          **({"sha256": sha256_file(path)} if provider == "openspec" else {})})
            observations[relative] = {"sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                      "total": len(tasks), "checked": sum(task["done"] for task in tasks)}
            pending.extend(f"{relative}:{task['line']}: {task['description'] or 'unnamed task'}"
                           for task in tasks if not task["done"])
        effective_scope = {"version": 1, "mode": "bound", "plans": plans,
                           "requirement_sha256": work["requirement"]["sha256"]}
        if scope.get("patterns"):
            effective_scope["patterns"] = sorted(set(scope["patterns"]))
        checked = sum(item["checked"] for item in observations.values())
        raw = "completed" if not pending else "in_progress" if checked else "ready_for_dev"
        return {"ready": not pending, "state": "pending" if pending else "done",
                "raw": raw, "canonical": _canonical_native_state(raw),
                "reason": "SDD_PLAN_NOT_DONE" if pending else None, "observations": observations,
                "pending_plan_items": pending, "scope": effective_scope, "next": next_step if pending else None}
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        return {"ready": False, "state": "blocked", "reason": str(exc), "next": next_step}


@locked_repo
def observe_plan_scope(repo: Path, work: dict) -> dict:
    """Persist pure provider scope deltas before any verification early return."""
    _refresh_work(repo, work)
    observation = provider_verification_plan_state(repo, work)
    scope = observation.get("scope")
    spec_scope = observation.get("spec_task_scope")
    if spec_scope and spec_scope != (work.get("sdd") or {}).get("spec_task_scope"):
        work.setdefault("sdd", {})["spec_task_scope"] = spec_scope
        save_work(repo, work)
    if scope and scope != (work.get("sdd") or {}).get("plan_scope"):
        work.setdefault("sdd", {})["plan_scope"] = scope
        save_work(repo, work)
    return observation


@locked_command
def cmd_sdd_plan_bind(args: argparse.Namespace) -> int:
    from scripts.cao_native_sync import reconcile_native_sdd_state

    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        sdd = work["sdd"]
        selections = {}
        for plan in (sdd.get("plan_scope") or {}).get("plans", []):
            selections[plan["path"]] = list(plan.get("item_ids") or [])
        for value in args.plan_item or []:
            path, item = value.rsplit("#", 1)
            if not path or not item:
                raise ValueError("PLAN_ITEM_INVALID")
            selections.setdefault(path, []).append(item)
        if not args.plan and not args.plan_item:
            raise ValueError("PLAN_SCOPE_REQUIRED")
        scope = bind_plan_scope(repo, work, list(selections) + (args.plan or []), selections, False)
        scope["requirement_sha256"] = work["requirement"]["sha256"]
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc).split(":", 1)[0], "reason": str(exc)})); return 2
    sdd["plan_scope"] = scope
    queue_event(work, {"event": "sdd.plan.bound", "work_item_id": work["id"], "source": "cao",
                       "plan_scope": scope, "occurred_at": now()})
    save_work(repo, work)
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({"ok": True, "plan_scope": scope, "verification_plan": provider_verification_plan_state(repo, work)}, ensure_ascii=False)); return 0
