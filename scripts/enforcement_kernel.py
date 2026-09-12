#!/usr/bin/env python3
"""V6.3 host-neutral runtime enforcement kernel.

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

import context_plane
import execution_state_manager as sm
import policy_engine

DEFAULT_CONFIG = {
    "version": 1,
    "enabled": True,
    "mode": "enforce",
    "require_state_for_code_mutation": True,
    "require_classified_decision_for_code_mutation": True,
    "require_fresh_context_before_first_mutation": True,
    "post_mutation_policy_feedback": True,
    "completion_claim_only": True,
    "max_stop_blocks_per_session": 3,
    "allowed_code_mutation_phases": ["implementation"],
    "source_roots": ["src/", "app/", "lib/", "packages/", "services/", "modules/"],
    "non_code_prefixes": [".orchestrator/", ".claude/", ".codex/", ".pi/", "openspec/", "docs/"],
}

COMPLETION_PATTERNS = [
    r"\bdone\b", r"\bfinished\b", r"\bcompleted\b", r"\bready to merge\b", r"\bready for merge\b",
    r"已完成", r"完成了", r"全部完成", r"可以合并", r"可以提交", r"开发完成",
]
READ_ONLY_SHELL = [
    r"^\s*(cat|head|tail|less|more|grep|rg|find|ls|pwd|git\s+(status|diff|log|show|branch)|mvn\s+.*test|gradle\s+.*test|./gradlew\s+.*test|npm\s+(test|run\s+test)|pytest|python\s+-m\s+pytest)\b"
]
MUTATING_SHELL_MARKERS = [
    ">", "tee ", "sed -i", "perl -pi", "rm ", "mv ", "cp ", "touch ", "mkdir ", "git checkout", "git reset",
    "git clean", "git restore", "git apply", "patch ", "npm install", "pnpm add", "yarn add", "mvn versions:", "gradle wrapper",
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
    """Deterministic fingerprint of material working-tree changes; ignores generated runtime/intake artifacts."""
    try:
        raw = subprocess.check_output(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=repo, stderr=subprocess.DEVNULL)
        parts = raw.split(b"\0")
        items: list[tuple[str, str, int]] = []
        for part in parts:
            if not part:
                continue
            text = part.decode("utf-8", errors="replace")
            path_text = text[3:] if len(text) >= 4 else text
            if " -> " in path_text:
                path_text = path_text.split(" -> ", 1)[1]
            rel = path_text.replace("\\", "/")
            if rel.startswith(".orchestrator/intake/") or rel.startswith(".orchestrator/runtime/") or rel in {
                ".orchestrator/execution-state.yaml", ".orchestrator/execution-history.jsonl"
            }:
                continue
            p = repo / rel
            if p.is_file():
                data = p.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                items.append((rel, digest, len(data)))
            else:
                items.append((rel, "missing", 0))
        head = ""
        try:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        except Exception:
            pass
        payload = json.dumps({"head": head, "items": sorted(items)}, sort_keys=True, separators=(",", ":")).encode()
        return "worktree:" + hashlib.sha256(payload).hexdigest()
    except Exception:
        return "worktree:unknown"


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
    explicit = raw.get("orchestrator_role") or raw.get("agent_type") or os.getenv("ORCHESTRATOR_ROLE")
    if explicit:
        e = str(explicit).lower()
        for r in ("planner", "implementer", "reviewer", "verifier", "debugger", "resume"):
            if r in e:
                return r
    phase = state.get("phase")
    return {"review": "reviewer", "verification": "verifier", "planning": "planner", "design": "planner", "specification": "planner"}.get(phase, "implementer")


def is_completion_claim(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(p, low, flags=re.I) for p in COMPLETION_PATTERNS)


def extract_tool(raw: dict[str, Any]) -> tuple[str, Any]:
    name = str(raw.get("tool_name") or raw.get("toolName") or raw.get("tool") or "")
    inp = raw.get("tool_input")
    if inp is None:
        inp = raw.get("input")
    if inp is None:
        inp = raw.get("args")
    return name, inp or {}


def shell_is_mutating(command: str) -> bool:
    c = command.strip()
    if any(re.search(p, c, re.I) for p in READ_ONLY_SHELL):
        return False
    low = c.lower()
    return any(m in low for m in MUTATING_SHELL_MARKERS)


def mutation_info(raw: dict[str, Any]) -> dict[str, Any]:
    name, inp = extract_tool(raw)
    low = name.lower()
    paths: list[str] = []
    mutating = False
    direct_file = False
    if low in {"write", "edit", "apply_patch", "applypatch"} or "write_file" in low or "edit_file" in low:
        mutating = True
        direct_file = True
        if isinstance(inp, dict):
            for key in ("file_path", "path", "filename"):
                if inp.get(key):
                    paths.append(str(inp[key]))
        if low == "apply_patch" and isinstance(inp, dict):
            cmd = str(inp.get("command") or inp.get("patch") or "")
            paths += re.findall(r"(?:\+\+\+ b/|--- a/|\*\*\* (?:Update|Add|Delete) File: )([^\n]+)", cmd)
    elif low in {"bash", "shell", "exec_command"}:
        cmd = str(inp.get("command") if isinstance(inp, dict) else inp)
        mutating = shell_is_mutating(cmd)
    return {"mutating": mutating, "direct_file": direct_file, "paths": paths, "tool": name, "input": inp}


def path_is_code(repo: Path, path: str, cfg: dict[str, Any]) -> bool:
    p = path.replace("\\", "/")
    try:
        abs_p = Path(path).resolve() if Path(path).is_absolute() else (repo / path).resolve()
        p = abs_p.relative_to(repo).as_posix()
    except Exception:
        pass
    if any(p.startswith(x) for x in cfg.get("non_code_prefixes", [])):
        return False
    if any(p.startswith(x) for x in cfg.get("source_roots", [])):
        return True
    return Path(p).suffix.lower() in {".java", ".kt", ".kts", ".py", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".cs", ".cpp", ".c", ".h", ".sql"}


def canonical(event: str, *, decision: str = "allow", reason: Optional[str] = None, context: Optional[str] = None,
              actions: Optional[list[str]] = None, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"event": event, "decision": decision, "reason": reason, "additional_context": context,
            "actions": actions or [], "metadata": metadata or {}}


def _state_context(state: dict[str, Any]) -> str:
    r = sm.resume_summary(state)
    completion = r.get("completion") or {}
    return (
        f"Orchestrator state: flow={r.get('flow_profile')}; phase={r.get('phase')}; status={r.get('status')}; "
        f"revision={r.get('revision')}; next={r.get('next_action')}; blocked={r.get('blocked')}; "
        f"governance_close_ready={completion.get('governance_close_ready')}."
    )


def _ensure_external_change(repo: Path, runtime: dict[str, Any], state: Optional[dict[str, Any]], host: str, event: str) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    current = worktree_snapshot(repo)
    # A successful semantic re-analysis clears canonical dirty state outside the hook path.
    # Re-baseline runtime tracking on the next lifecycle event so future external edits are detectable.
    if state is not None and runtime.get("dirty") and not (state.get("enforcement") or {}).get("dirty"):
        runtime["dirty"] = False
        runtime["dirty_reason"] = None
        runtime["last_repo_snapshot"] = current
        return runtime, state
    prior = runtime.get("last_repo_snapshot")
    if prior and prior != current and not runtime.get("dirty") and state is not None:
        try:
            updated = sm.mark_enforcement_dirty(state_path(repo), "external_change", [], host, current, "external/unobserved working tree change", state["revision"])
            state = updated
            runtime["dirty"] = True
            runtime["dirty_reason"] = "external_change_detected"
        except Exception:
            pass
    runtime["last_repo_snapshot"] = current
    return runtime, state


def handle(repo: Path, host: str, event: str, raw: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config(repo)
    if not cfg.get("enabled", True):
        return canonical(event)
    sp = state_path(repo)
    state = sm._load(sp) if sp.exists() else None
    runtime = load_runtime(repo)
    runtime, state = _ensure_external_change(repo, runtime, state, host, event)
    runtime.setdefault("events", []).append({"at": utc_now(), "host": host, "event": event})
    runtime["events"] = runtime["events"][-50:]

    if event in {"session_start", "prompt_submit", "subagent_start"}:
        if state is None:
            msg = "Orchestrator is installed but Canonical Execution State is not initialized. Reads are allowed; initialize/classify before production-code mutation."
            save_runtime(repo, runtime)
            return canonical(event, context=msg, actions=["initialize_execution_state", "run_intake"])
        role = infer_role(state, raw)
        base = _state_context(state)
        manifest_path, manifest = _manifest_from_state(repo, state)
        if manifest is not None:
            fresh = context_plane.validate_manifest(repo, manifest)
            if fresh.get("status") != "FRESH":
                base += " Context Manifest is STALE; refresh semantic intake/context before relying on it for new mutations."
        pack = _pack_text(repo, state, role)
        if pack:
            base += "\n\n" + pack[:7000]
        save_runtime(repo, runtime)
        return canonical(event, context=base, metadata={"role": role})

    if event == "pre_tool":
        info = mutation_info(raw)
        if not info["mutating"]:
            save_runtime(repo, runtime)
            return canonical(event)
        if state is None:
            save_runtime(repo, runtime)
            if cfg.get("require_state_for_code_mutation", True):
                return canonical(event, decision="deny", reason="Canonical Execution State is missing; run intake/init before mutation.", actions=["initialize_execution_state"])
            return canonical(event, context="Mutation is occurring without Canonical Execution State.")
        code_paths = [p for p in info["paths"] if path_is_code(repo, p, cfg)]
        code_mutation = bool(code_paths) or (not info["direct_file"] and info["tool"].lower() in {"bash", "shell", "exec_command"})
        if code_mutation:
            decision_doc = _decision_from_state(repo, state)
            if cfg.get("require_classified_decision_for_code_mutation", True) and (not decision_doc or decision_doc.get("status") != "CLASSIFIED"):
                save_runtime(repo, runtime)
                return canonical(event, decision="deny", reason="Production-code mutation requires a CLASSIFIED Decision Engine result.", actions=["run_semantic_intake"])
            if state.get("phase") not in set(cfg.get("allowed_code_mutation_phases", ["implementation"])):
                save_runtime(repo, runtime)
                return canonical(event, decision="deny", reason=f"Code mutation is not allowed in phase={state.get('phase')}; transition legally to implementation first.", actions=["transition_to_implementation"])
            role = infer_role(state, raw)
            if role in {"reviewer", "verifier"}:
                save_runtime(repo, runtime)
                return canonical(event, decision="deny", reason=f"Role {role} is read/verify-oriented and may not mutate production code.")
            manifest_path, manifest = _manifest_from_state(repo, state)
            enf = state.get("enforcement") or {}
            if cfg.get("require_fresh_context_before_first_mutation", True) and not enf.get("dirty"):
                if manifest is None:
                    save_runtime(repo, runtime)
                    return canonical(event, decision="deny", reason="Context Manifest is missing before first code mutation.", actions=["build_context_manifest"])
                fresh = context_plane.validate_manifest(repo, manifest)
                if fresh.get("status") != "FRESH":
                    save_runtime(repo, runtime)
                    return canonical(event, decision="deny", reason="Context Manifest is stale before code mutation.", actions=["refresh_context"])
        save_runtime(repo, runtime)
        return canonical(event, context="Mutation permitted by runtime guard; any successful material change will invalidate semantic/context/final-verification freshness.")

    if event in {"post_tool", "file_changed"}:
        info = mutation_info(raw) if event == "post_tool" else {"mutating": True, "paths": [str(raw.get("file_path") or "")], "tool": "FileChanged"}
        if info.get("mutating") and state is not None:
            snap = worktree_snapshot(repo)
            try:
                state = sm.mark_enforcement_dirty(sp, "mutation", info.get("paths") or [], host, snap, f"{host}:{event}", state["revision"])
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
        if state is None:
            save_runtime(repo, runtime)
            return canonical(event)
        last = str(raw.get("last_assistant_message") or raw.get("message") or raw.get("assistant_message") or "")
        role = infer_role(state, raw)
        if event == "subagent_stop":
            if role == "reviewer" and state.get("review", {}).get("required") and state.get("review", {}).get("status") == "pending":
                save_runtime(repo, runtime)
                return canonical(event, decision="deny", reason="Reviewer cannot finish before recording review outcome/evidence.")
            if role == "verifier" and state.get("verification", {}).get("status") != "passed":
                save_runtime(repo, runtime)
                return canonical(event, decision="deny", reason="Verifier cannot finish before recording verification outcome/evidence.")
        should_check = (not cfg.get("completion_claim_only", True)) or is_completion_claim(last) or state.get("phase") in {"verification", "release", "closed"}
        if not should_check:
            save_runtime(repo, runtime)
            return canonical(event)
        failures = list(sm.transition_guard(state, "closed", "completed"))
        enf = state.get("enforcement") or {}
        if enf.get("enabled") and enf.get("dirty"):
            failures.append("runtime enforcement marks semantic/context evidence stale after material mutation")
        if failures:
            sid = str(raw.get("session_id") or raw.get("sessionId") or "default")
            counts = runtime.setdefault("stop_blocks", {})
            count = int(counts.get(sid, 0))
            if count < int(cfg.get("max_stop_blocks_per_session", 3)):
                counts[sid] = count + 1
                save_runtime(repo, runtime)
                return canonical(event, decision="deny", reason="Cannot claim completion: " + "; ".join(failures), actions=[sm.compute_next_action(state)])
            save_runtime(repo, runtime)
            return canonical(event, metadata={"warning": "completion_not_governance_ready", "failures": failures})
        save_runtime(repo, runtime)
        return canonical(event)

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
