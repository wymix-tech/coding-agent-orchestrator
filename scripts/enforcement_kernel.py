#!/usr/bin/env python3
"""Host-neutral runtime enforcement kernel.

Host adapters translate Claude Code / Codex / Pi lifecycle events into this kernel.
The kernel does not own governance truth: Decision, Context, Policy, and Execution State
remain authoritative. It only enforces them at runtime lifecycle boundaries.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import bootstrap_guard
import action_guard
import repository_snapshot
import tool_actions
import context_plane
import execution_state_manager as sm
import policy_engine
import session_context
import start_router

DEFAULT_CONFIG = {
    "version": 1,
    "enabled": True,
    "post_mutation_policy_feedback": True,
    "completion_claim_only": True,
    "max_stop_blocks_per_session": 3,
}

COMPLETION_PATTERNS = [
    r"\bdone\b", r"\bfinished\b", r"\bcompleted\b", r"\bready to merge\b", r"\bready for merge\b",
    r"已完成", r"完成了", r"全部完成", r"可以合并", r"可以提交", r"开发完成",
]
TASK_COMPLETION_PATTERNS = [
    r"\b(task|step|story)\b.{0,60}\b(done|finished|completed)\b",
    r"\b(done|finished|completed)\b.{0,60}\b(task|step|story)\b",
    r"(任务|步骤|story).{0,40}(完成|已完成|结束)",
]
GLOBAL_COMPLETION_STRONG = [
    r"\bready to merge\b", r"\bready for merge\b", r"\ball (done|finished|completed)\b",
    r"\bimplementation (is )?(done|finished|completed)\b", r"全部完成", r"可以合并", r"可以提交", r"开发完成",
]

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required for enforcement config")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_config(repo: Path) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    p = repo / ".orchestrator" / "enforcement.yaml"
    if p.exists():
        raw = _load_yaml(p) or {}
        if not isinstance(raw, dict):
            raise ValueError(".orchestrator/enforcement.yaml must be an object")
        cfg.update(raw)
    return cfg


def runtime_path(repo: Path) -> Path:
    return repo / ".orchestrator" / "runtime" / "enforcement-state.json"


def load_runtime(repo: Path) -> dict[str, Any]:
    p = runtime_path(repo)
    if not p.exists():
        return {"version": 1, "last_repo_snapshot": None, "dirty": False, "stop_blocks": {}, "events": []}
    return _load_json(p)


def save_runtime(repo: Path, runtime: dict[str, Any]) -> None:
    p = runtime_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    runtime["updated_at"] = utc_now()
    p.write_text(json.dumps(runtime, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def repo_root(cwd: str | Path) -> Path:
    cwd = Path(cwd).resolve()
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
        return Path(out).resolve()
    except Exception:
        return cwd


def worktree_snapshot(repo: Path) -> str:
    return repository_snapshot.fingerprint(repo)


def state_path(repo: Path) -> Path:
    return repo / ".orchestrator" / "execution-state.yaml"


def _resolve_state_ref(repo: Path, ref: Optional[str]) -> Optional[Path]:
    if not ref:
        return None
    p = Path(ref)
    return p if p.is_absolute() else repo / p


def _decision_from_state(repo: Path, state: dict[str, Any]) -> Optional[dict[str, Any]]:
    p = _resolve_state_ref(repo, (state.get("analysis") or {}).get("decision_ref"))
    if p and p.exists():
        try:
            return _load_json(p)
        except Exception:
            return None
    return None


def _manifest_from_state(repo: Path, state: dict[str, Any]) -> tuple[Optional[Path], Optional[dict[str, Any]]]:
    p = _resolve_state_ref(repo, (state.get("analysis") or {}).get("context_manifest_ref"))
    if p and p.exists():
        try:
            return p, _load_json(p)
        except Exception:
            pass
    fallback = repo / ".orchestrator" / "intake" / "context-manifest.json"
    if fallback.exists():
        try:
            return fallback, _load_json(fallback)
        except Exception:
            pass
    return None, None


def _pack_text(repo: Path, state: dict[str, Any], role: Optional[str] = None) -> Optional[str]:
    ref = (state.get("analysis") or {}).get("context_pack_ref")
    p = _resolve_state_ref(repo, ref)
    candidates: list[Path] = []
    if p:
        candidates.extend([p.with_suffix(".md") if p.suffix == ".json" else p, p])
    if role:
        candidates.extend(sorted((repo / ".orchestrator" / "intake").glob(f"context-pack.{role}.*.md"), reverse=True))
    for c in candidates:
        if c and c.exists() and c.is_file():
            text = c.read_text(encoding="utf-8")
            return text[:10000]
    return None


def infer_role(state: dict[str, Any], raw: dict[str, Any]) -> str:
    explicit = raw.get("orchestrator_role") or raw.get("agent_type") or raw.get("role") or os.getenv("ORCHESTRATOR_ROLE")
    if explicit:
        e = str(explicit).lower()
        for r in ("planner", "implementer", "reviewer", "verifier", "debugger", "resume"):
            if r in e:
                return r
    phase = state.get("phase")
    return {"review": "reviewer", "verification": "verifier", "planning": "planner", "design": "planner", "specification": "planner"}.get(phase, "implementer")


def is_task_completion_claim(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(p, low, flags=re.I) for p in TASK_COMPLETION_PATTERNS)


def is_completion_claim(text: str) -> bool:
    low = (text or "").lower()
    if any(re.search(p, low, flags=re.I) for p in GLOBAL_COMPLETION_STRONG):
        return True
    if is_task_completion_claim(low):
        return False
    return any(re.search(p, low, flags=re.I) for p in COMPLETION_PATTERNS)


def extract_tool(raw: dict[str, Any]) -> tuple[str, Any]:
    return tool_actions.extract_tool(raw)


def shell_is_mutating(command: str) -> bool:
    return tool_actions.shell_action(command, Path.cwd()) != "read"


def mutation_info(raw: dict[str, Any], repo: Optional[Path] = None) -> dict[str, Any]:
    return tool_actions.describe(raw, repo or Path.cwd())


def path_is_code(repo: Path, path: str, cfg: dict[str, Any]) -> bool:
    return tool_actions.is_code(repo, path)


def canonical(event: str, *, decision: str = "allow", reason: Optional[str] = None, context: Optional[str] = None,
              actions: Optional[list[str]] = None, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"event": event, "decision": decision, "reason": reason, "additional_context": context,
            "actions": actions or [], "metadata": metadata or {}}


def _state_context(state: dict[str, Any], repo: Path) -> str:
    r = sm.resume_summary(state, repo)
    completion = r.get("completion") or {}
    return (
        f"Orchestrator state: flow={r.get('flow_profile')}; phase={r.get('phase')}; status={r.get('status')}; "
        f"revision={r.get('revision')}; next={r.get('next_action')}; blocked={r.get('blocked')}; "
        f"governance_close_ready={completion.get('governance_close_ready')}."
    )


def _ensure_external_change(repo: Path, runtime: dict[str, Any], state: Optional[dict[str, Any]], host: str, event: str) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    current = worktree_snapshot(repo)
    expected = ((state or {}).get("analysis") or {}).get("repository_snapshot_id")
    if state is not None and expected and current != expected and not (state.get("enforcement") or {}).get("dirty"):
        try:
            state = sm.mark_enforcement_dirty(state_path(repo), "external_change", [], host, current,
                                               "material repository content differs from analyzed input", state["revision"])
        except sm.StateError:
            # Authorization still compares live content, so a failed dirty write cannot grant access.
            state = sm._load(state_path(repo))
    runtime["dirty"] = bool(((state or {}).get("enforcement") or {}).get("dirty"))
    runtime["last_repo_snapshot"] = current
    return runtime, state


def handle(repo: Path, host: str, event: str, raw: dict[str, Any]) -> dict[str, Any]:
    auto_bootstrap = None
    if event in {"session_start", "prompt_submit", "subagent_start"}:
        auto_bootstrap = bootstrap_guard.ensure(repo, host=host, activation=True)
        if auto_bootstrap.get("status") == "ACTION_REQUIRED":
            detail = auto_bootstrap.get("detail") or "Project bootstrap requires an explicit authority/configuration decision."
            unresolved = auto_bootstrap.get("unresolved") or []
            if unresolved:
                detail += " " + "; ".join(str(x.get("detail") or x.get("code")) for x in unresolved[:4])
            return canonical(
                event,
                context=f"Orchestrator bootstrap is ACTION_REQUIRED: {detail}",
                actions=[str(auto_bootstrap.get("next_action") or "resolve_init_ambiguity")],
                metadata={"bootstrap_guard": auto_bootstrap.get("status"), "auto_bootstrap_performed": auto_bootstrap.get("performed", False)},
            )
    cfg = load_config(repo)
    if not cfg.get("enabled", True):
        return canonical(event)
    sp = state_path(repo)
    state = sm._load(sp) if sp.exists() else None
    runtime = load_runtime(repo)
    runtime, state = _ensure_external_change(repo, runtime, state, host, event)
    runtime.setdefault("events", []).append({"at": utc_now(), "host": host, "event": event})
    runtime["events"] = runtime["events"][-50:]

    start_route = None
    if event == "prompt_submit":
        prompt_text = str(raw.get("prompt") or raw.get("user_prompt") or raw.get("userPrompt") or raw.get("message") or "")
        if start_router.is_start_intent(prompt_text):
            start_route = start_router.resolve(repo, ensure_bootstrap=False, host=host)
            route = start_route.get("route")
            if route == "REQUEST_REQUIREMENT":
                save_runtime(repo, runtime)
                prefix = "Safe Auto project bootstrap completed in this session. " if (auto_bootstrap and auto_bootstrap.get("performed")) else ""
                return canonical(event, context=(
                    prefix + "Start Intent Router: project is READY_FOR_INTAKE, but no actionable requirement was found. "
                    "Ask the user for the first project goal/feature. Do not create production code or a fake work item."
                ), actions=["ask_for_first_requirement"], metadata={
                    "start_route": route,
                    "bootstrap_guard": (auto_bootstrap or {}).get("status"),
                    "auto_bootstrap_performed": (auto_bootstrap or {}).get("performed", False),
                })
            if route == "SELECT_REQUIREMENT":
                cands = (start_route.get("requirements") or {}).get("candidates") or []
                choices = "; ".join(f"{i+1}. {c.get('title')} [{c.get('path')}]" for i, c in enumerate(cands[:8]))
                save_runtime(repo, runtime)
                return canonical(event, context=f"Start Intent Router requires requirement selection: {choices}", actions=["select_requirement"], metadata={"start_route": route, "auto_bootstrap_performed": (auto_bootstrap or {}).get("performed", False)})
            if route == "AUTO_INTAKE_CANDIDATE":
                c = start_route.get("candidate") or {}
                save_runtime(repo, runtime)
                return canonical(event, context=(
                    f"Start Intent Router found exactly one high-confidence requirement: {c.get('title')} [{c.get('path')}]. "
                    "Run the packaged `coding-orchestrator --repo . start` command to perform canonical intake, then follow its resulting state."
                ), actions=["run_coding_orchestrator_start"], metadata={"start_route": route, "requirement_path": c.get("path"), "auto_bootstrap_performed": (auto_bootstrap or {}).get("performed", False)})
            if route == "SURFACE_BLOCKER":
                save_runtime(repo, runtime)
                return canonical(event, context="Start Intent Router found the active work item blocked. Surface the current blocker(s) and resolve them before continuing.", actions=[str(start_route.get("next_action") or "resolve_blocker")], metadata={"start_route": route, "auto_bootstrap_performed": (auto_bootstrap or {}).get("performed", False)})

    if event in {"session_start", "prompt_submit", "subagent_start"}:
        if state is None:
            if event in {"session_start", "subagent_start"} or (auto_bootstrap and auto_bootstrap.get("performed")):
                bootstrap = session_context.build_bootstrap(repo, role="implementer", host=host, session_id=str(raw.get("session_id") or raw.get("sessionId") or ""))
                _, md_path = session_context.persist_bootstrap(repo, bootstrap)
                msg = md_path.read_text(encoding="utf-8")
            else:
                msg = "Project is bootstrapped but no active work item exists. Run intake/classification before production-code mutation."
            if auto_bootstrap and auto_bootstrap.get("performed"):
                msg = "Safe Auto project bootstrap completed in this session.\n\n" + msg
            save_runtime(repo, runtime)
            return canonical(
                event,
                context=msg,
                actions=["run_intake"],
                metadata={"bootstrap_guard": (auto_bootstrap or {}).get("status"), "auto_bootstrap_performed": (auto_bootstrap or {}).get("performed", False)},
            )
        role = infer_role(state, raw)
        if event in {"session_start", "subagent_start"}:
            bootstrap = session_context.build_bootstrap(
                repo, role=role, host=host, session_id=str(raw.get("session_id") or raw.get("sessionId") or "")
            )
            json_path, md_path = session_context.persist_bootstrap(repo, bootstrap)
            runtime["last_bootstrap_ref"] = str(json_path.relative_to(repo))
            runtime["last_bootstrap_snapshot_id"] = bootstrap.get("bootstrap_snapshot_id")
            base = md_path.read_text(encoding="utf-8")
            save_runtime(repo, runtime)
            return canonical(event, context=base, metadata={
                "role": role,
                "bootstrap_snapshot_id": bootstrap.get("bootstrap_snapshot_id"),
                "bootstrap_guard": (auto_bootstrap or {}).get("status"),
                "auto_bootstrap_performed": (auto_bootstrap or {}).get("performed", False),
            })
        # Prompt-submit is intentionally delta-only. Do not re-inject a full cold-start pack on every turn.
        base = _state_context(state, repo)
        manifest_path, manifest = _manifest_from_state(repo, state)
        if manifest is not None:
            fresh = context_plane.validate_manifest(repo, manifest)
            if fresh.get("status") != "FRESH":
                base += " Context Manifest is STALE; refresh semantic intake/context before relying on it for new mutations."
        handoff_path = repo / ".orchestrator" / "session" / "latest-handoff.json"
        if handoff_path.exists():
            try:
                hs = session_context.validate_handoff(repo, json.loads(handoff_path.read_text(encoding="utf-8")), state)
                if hs.get("status") == "STALE":
                    base += " Latest handoff is stale and must not be treated as current truth."
            except Exception:
                base += " Latest handoff could not be validated; do not rely on it."
        save_runtime(repo, runtime)
        return canonical(event, context=base, metadata={"role": role, "context_mode": "delta_only", "start_route": (start_route or {}).get("route")})

    if event == "pre_tool":
        info = mutation_info(raw, repo)
        authorization = action_guard.authorize(repo, state, info["action"], role=infer_role(state, raw) if state else "implementer")
        save_runtime(repo, runtime)
        return canonical(event, decision=authorization["decision"],
                         reason="; ".join(r["message"] for r in authorization["reasons"]) or None,
                         actions=[authorization["next_action"]] if authorization["next_action"] else [],
                         metadata={"authorization": authorization})

    if event in {"post_tool", "file_changed"}:
        info = mutation_info(raw, repo) if event == "post_tool" else {"mutating": True, "action": "mutate_code", "paths": [str(raw.get("file_path") or "")], "tool": "FileChanged"}
        if info.get("action") == "mutate_code" and state is not None:
            snap = worktree_snapshot(repo)
            try:
                state = sm.mark_enforcement_dirty(sp, "mutation" if event == "post_tool" else "external_change", info.get("paths") or [], host, snap, f"{host}:{event}", state["revision"])
                runtime["dirty"] = True
                runtime["last_repo_snapshot"] = snap
                ctx = "Material change recorded. Semantic impact, Context Pack, and final verification are stale until re-analysis/context refresh. Continued implementation edits are allowed; review/verification/close are blocked by state guards."
                if cfg.get("post_mutation_policy_feedback", True):
                    analysis = state.get("analysis") or {}
                    plan_path = _resolve_state_ref(repo, analysis.get("policy_plan_ref"))
                    if plan_path and plan_path.exists():
                        try:
                            plan = _load_json(plan_path)
                            manifest_ref = plan.get("manifest")
                            manifest_path = _resolve_state_ref(repo, manifest_ref)
                            if manifest_path and manifest_path.exists():
                                evaluation = policy_engine.evaluate(repo, plan, manifest_path)
                                blocking = evaluation.get("blocking_violations") or []
                                if blocking:
                                    ids = [str(x.get("rule_id")) for x in blocking]
                                    ctx += " Blocking Engineering Policy violation(s): " + ", ".join(ids) + ". Fix before phase advance."
                        except Exception as policy_exc:
                            ctx += f" Policy post-check could not complete ({policy_exc}); do not infer compliance."
            except Exception as exc:
                ctx = f"Material change detected, but state dirty-mark failed: {exc}. Treat analysis/context/verification as stale."
            save_runtime(repo, runtime)
            return canonical(event, context=ctx, actions=["rerun_semantic_intake_before_review"])
        save_runtime(repo, runtime)
        return canonical(event)

    if event in {"stop", "subagent_stop"}:
        last = str(raw.get("last_assistant_message") or raw.get("message") or raw.get("assistant_message") or "")
        role = infer_role(state, raw) if state else "implementer"
        if event == "subagent_stop" and role in {"reviewer", "verifier"}:
            authorization = action_guard.authorize(repo, state, "finish_role", role=role)
            save_runtime(repo, runtime)
            return canonical(event, decision=authorization["decision"],
                             reason="; ".join(r["message"] for r in authorization["reasons"]) or None,
                             actions=[authorization["next_action"]] if authorization["next_action"] else [],
                             metadata={"authorization": authorization})
        should_check = (not cfg.get("completion_claim_only", True)) or is_completion_claim(last) or (state or {}).get("phase") in {"verification", "release", "closed"}
        if not should_check:
            save_runtime(repo, runtime)
            return canonical(event)
        authorization = action_guard.authorize(repo, state, "close")
        if not authorization["allowed"]:
            sid = str(raw.get("session_id") or raw.get("sessionId") or "default")
            counts = runtime.setdefault("stop_blocks", {})
            count = int(counts.get(sid, 0)) + 1
            counts[sid] = count
            retry = count <= int(cfg.get("max_stop_blocks_per_session", 3))
            save_runtime(repo, runtime)
            return canonical(event, decision="deny", reason="Cannot claim completion: " + "; ".join(r["message"] for r in authorization["reasons"]),
                             actions=[authorization["next_action"]], metadata={"authorization": authorization, "retry_recommended": retry})
        save_runtime(repo, runtime)
        return canonical(event, metadata={"authorization": authorization})

    save_runtime(repo, runtime)
    return canonical(event)


def normalize_event(host: str, raw: dict[str, Any]) -> str:
    n = str(raw.get("hook_event_name") or raw.get("event_name") or raw.get("event") or "").lower()
    if host == "pi":
        n = str(raw.get("_orchestrator_event") or n).lower()
    mapping = {
        "sessionstart": "session_start", "session_start": "session_start",
        "userpromptsubmit": "prompt_submit", "before_agent_start": "prompt_submit", "prompt_submit": "prompt_submit",
        "pretooluse": "pre_tool", "tool_call": "pre_tool", "pre_tool": "pre_tool",
        "posttooluse": "post_tool", "tool_result": "post_tool", "post_tool": "post_tool",
        "filechanged": "file_changed", "file_changed": "file_changed",
        "stop": "stop", "agent_end": "stop",
        "subagentstart": "subagent_start", "subagent_start": "subagent_start",
        "subagentstop": "subagent_stop", "subagent_stop": "subagent_stop",
    }
    return mapping.get(n, n or "unknown")


def format_host_output(host: str, event: str, result: dict[str, Any]) -> dict[str, Any]:
    decision = result.get("decision")
    reason = result.get("reason")
    context = result.get("additional_context")
    if host in {"claude-code", "codex"}:
        hook_name = {
            "session_start": "SessionStart", "prompt_submit": "UserPromptSubmit", "pre_tool": "PreToolUse",
            "post_tool": "PostToolUse", "stop": "Stop", "subagent_start": "SubagentStart", "subagent_stop": "SubagentStop",
            "file_changed": "FileChanged",
        }.get(event, event)
        out: dict[str, Any] = {}
        if decision == "deny":
            if event == "pre_tool":
                out["hookSpecificOutput"] = {"hookEventName": hook_name, "permissionDecision": "deny", "permissionDecisionReason": reason or "Blocked by orchestrator"}
            elif event in {"stop", "subagent_stop", "prompt_submit"}:
                out["decision"] = "block"
                out["reason"] = reason or "Blocked by orchestrator"
            else:
                out["decision"] = "block"
                out["reason"] = reason or "Blocked by orchestrator"
        if context:
            out.setdefault("hookSpecificOutput", {"hookEventName": hook_name})["additionalContext"] = context
        return out
    return result


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    h = sub.add_parser("host")
    h.add_argument("--host", required=True, choices=["claude-code", "codex", "pi"])
    h.add_argument("--repo", type=Path)
    h.add_argument("--event")
    h.add_argument("--canonical", action="store_true", help="emit canonical output instead of host-specific output")
    args = p.parse_args(argv)
    raw = json.load(sys.stdin)
    repo = repo_root(args.repo or raw.get("cwd") or os.getcwd())
    event = args.event or normalize_event(args.host, raw)
    result = handle(repo, args.host, event, raw)
    output = result if args.canonical or args.host == "pi" else format_host_output(args.host, event, result)
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
