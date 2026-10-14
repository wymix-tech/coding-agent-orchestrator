"""Work-bound Direct Build tasks, separate from shared BMAD sprint discovery."""
import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from scripts.light_spec_policy import _heading, _lines
from scripts.native_route import owned_file


def direct_build_decision(work: dict[str, Any]) -> dict[str, Any] | None:
    decision = (work.get("sdd") or {}).get("route_decision") or {}
    if (decision.get("provider") == "bmad" and decision.get("route") == "direct-build"
            and decision.get("requirement_sha256") == work["requirement"]["sha256"]):
        return decision
    return None


def spec_task_state(repo: Path, work: dict[str, Any], dirs: list[str],
                    task_reader: Callable[[str], list[dict]]) -> dict[str, Any]:
    decision = direct_build_decision(work)
    if not decision:
        return {"ready": True, "state": "not_applicable"}
    sources = {decision["primary_artifact"]}
    sources.update(item["path"] for item in decision.get("required_artifacts", [])
                   if item.get("role") == "spec")
    observations, pending = {}, []
    next_step = ("Finish every task in the current route's native SPEC Tasks & Acceptance sections; "
                 "`--no-plan` does not remove SPEC tasks. Then run `cao sdd sync` and `cao verify`.")
    try:
        for source in sorted(sources):
            path = owned_file(repo, source, dirs)
            raw = path.read_bytes()
            lines = _lines(raw)
            previous = ((work.get("sdd") or {}).get("spec_task_scope") or {}).get(source, {})
            tasks = []
            selected_level = None
            for line in lines:
                heading = _heading(line)
                if heading:
                    if selected_level is not None and heading[0] <= selected_level:
                        selected_level = None
                    title = re.sub(r"\s+", " ", heading[1]).casefold()
                    if title in {"tasks & acceptance", "tasks and acceptance", "任务与验收", "任务及验收"}:
                        selected_level = heading[0]
                elif selected_level is not None:
                    tasks.extend(task_reader(line))
            if not tasks:
                if previous:
                    raise ValueError("BMAD_SPEC_TASK_REMOVED: " + source)
                continue  # Native oneshot has Intent rather than a task checklist.
            descriptions = sorted(task["description"] for task in tasks)
            if any(descriptions.count(value) < previous.get("descriptions", []).count(value)
                   for value in previous.get("descriptions", [])):
                raise ValueError("BMAD_SPEC_TASK_REMOVED: " + source)
            observations[source] = {"sha256": hashlib.sha256(raw).hexdigest(), "total": len(tasks),
                                    "checked": sum(task["done"] for task in tasks),
                                    "descriptions": descriptions}
            pending.extend(f"{source}: {task['description'] or 'unnamed task'}"
                           for task in tasks if not task["done"])
        if not observations:
            previous = (work.get("sdd") or {}).get("spec_task_scope") or {}
            if previous:
                raise ValueError("BMAD_SPEC_TASK_REMOVED")
            return {"ready": True, "state": "not_applicable"}
        checked = sum(item["checked"] for item in observations.values())
        raw_state = "done" if not pending else "in_progress" if checked else "ready_for_dev"
        return {"ready": not pending, "state": "pending" if pending else "done",
                "reason": "BMAD_SPEC_TASKS_NOT_DONE" if pending else None,
                "raw": raw_state, "canonical": "completed" if not pending else "in_progress" if checked else "ready",
                "observations": observations, "pending_plan_items": pending,
                "spec_task_scope": {key: {"descriptions": item["descriptions"]} for key, item in observations.items()},
                "next": next_step if pending else None}
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        return {"ready": False, "state": "blocked", "reason": str(exc), "next": next_step}


def merge_plan_states(sprint: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if spec["state"] == "not_applicable" or sprint["state"] == "blocked":
        return sprint
    if sprint["state"] == "not_applicable" or spec["state"] == "blocked":
        return spec
    result = dict(sprint)
    result["observations"] = {**sprint["observations"], **spec["observations"]}
    result["pending_plan_items"] = sprint["pending_plan_items"] + spec["pending_plan_items"]
    result["spec_task_scope"] = spec["spec_task_scope"]
    if not spec["ready"]:
        result.update(ready=False, state="pending", reason=spec["reason"], next=spec["next"],
                      raw="in_progress", canonical="in_progress")
    return result
