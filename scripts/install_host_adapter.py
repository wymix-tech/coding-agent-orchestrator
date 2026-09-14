#!/usr/bin/env python3
"""Install/merge host enforcement + session-context adapters into a repository.

This script only mutates host configuration when --apply is provided. It creates a backup
next to an existing JSON file before merging orchestrator hook groups.
"""
from __future__ import annotations
import argparse, base64, copy, json, os, re, shlex, shutil
from pathlib import Path
from typing import Any

import project_activation

ROOT = Path(__file__).resolve().parents[1]
KERNEL = (ROOT / "scripts" / "enforcement_kernel.py").resolve()
ENCODED_PREFIX = 'python3 -c "import base64,json,os,sys;os.execv(sys.executable,[sys.executable]+json.loads(base64.b64decode('


def _embedded(path: Path) -> str:
    """Render a filesystem path for embedding inside a JSON/TS double-quoted string literal."""
    return json.dumps(str(path), ensure_ascii=True)[1:-1]


def _shell_command(argv: list[str], *, windows: bool | None = None) -> str:
    windows = os.name == "nt" if windows is None else windows
    if windows:
        # Windows hosts may launch through cmd, PowerShell, or Git Bash. Transport
        # paths as data so %, $, backticks, and spaces cannot be shell-expanded.
        encoded = base64.b64encode(json.dumps(argv[1:]).encode("utf-8")).decode("ascii")
        return ENCODED_PREFIX + "'" + encoded + "')))\""
    return shlex.join(argv)


def _managed_hook(hook: dict[str, Any]) -> bool:
    command = str(hook.get("command") or "")
    if hook.get("type") != "command":
        return False
    try:
        if command.startswith(ENCODED_PREFIX):
            encoded = command[len(ENCODED_PREFIX):].split("'", 2)[1]
            argv = ["python3", *json.loads(base64.b64decode(encoded))]
        else:
            argv = shlex.split(command)
        return (len(argv) >= 5 and Path(argv[0]).name.lower() in {"python", "python3", "python.exe", "python3.exe"}
                and argv[1].replace("\\", "/").rsplit("/", 1)[-1] == "enforcement_kernel.py"
                and argv[2:4] == ["host", "--host"]
                and argv[4] in {"claude-code", "codex", "pi"})
    except (ValueError, IndexError, TypeError):
        return False


def load_template(path: Path, repo: Path) -> dict[str, Any]:
    if not path.is_file():
        # A partial Skill install (for example one that ships without `hosts/`) used to fail with a
        # bare path and no explanation. Say what is missing and how to fix it.
        raise FileNotFoundError(
            f"Packaged host template is missing: {path}. This Skill installation is incomplete; "
            "reinstall it with the `hosts/` directory included, or initialize with `--host none`."
        )
    # Decode the JSON first, substitute whole arguments, then quote for the shell.
    # Shell escaping and JSON string encoding are different serialization layers.
    fragment = json.loads(path.read_text(encoding="utf-8"))
    values = {"__KERNEL__": str(KERNEL), "__REPO__": str(repo.resolve())}
    for groups in fragment.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("type") == "command":
                    argv = [values.get(arg, arg) for arg in shlex.split(hook["command"])]
                    hook["command"] = _shell_command(argv)
    return fragment


def merge_hooks(target: Path, fragment: dict[str, Any], apply: bool) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    merged = dict(existing)
    if fragment.get("description") and not merged.get("description"):
        merged["description"] = fragment["description"]
    hooks = {}
    for event, groups in (existing.get("hooks") or {}).items():
        kept = []
        for group in groups:
            copied = copy.deepcopy(group)
            entries = copied.get("hooks") or []
            copied["hooks"] = [hook for hook in entries if not _managed_hook(hook)]
            if copied["hooks"] or not entries:
                kept.append(copied)
        hooks[event] = kept
    for event, groups in (fragment.get("hooks") or {}).items():
        current = list(hooks.get(event) or [])
        encoded = {json.dumps(x, sort_keys=True) for x in current}
        for group in groups:
            e = json.dumps(group, sort_keys=True)
            if e not in encoded:
                current.append(group); encoded.add(e)
        hooks[event] = current
    merged["hooks"] = hooks
    if apply and merged != existing:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        target.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def render_pi(repo: Path) -> str:
    template = (ROOT / "hosts" / "pi" / "coding-orchestrator.template.ts").read_text(encoding="utf-8")
    values = {"__KERNEL__": _embedded(KERNEL), "__REPO__": _embedded(repo.resolve())}
    return re.sub(r"__KERNEL__|__REPO__", lambda match: values[match.group()], template)


def install_pi(repo: Path, apply: bool) -> str:
    text = render_pi(repo)
    target = repo / ".pi" / "extensions" / "coding-orchestrator.ts"
    if apply and (not target.exists() or target.read_text(encoding="utf-8") != text):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists(): shutil.copy2(target, target.with_suffix(".ts.bak"))
        target.write_text(text, encoding="utf-8")
    return str(target)




def is_installed(repo: Path, host: str) -> bool:
    """An adapter is installed only when its managed hooks match this runtime/repo."""
    repo = repo.resolve()
    try:
        if host in {"claude-code", "codex"}:
            p = repo / (".claude/settings.json" if host == "claude-code" else ".codex/hooks.json")
            if not p.exists():
                return False
            desired = load_template(ROOT / "hosts" / host / "hooks.template.json", repo)
            return merge_hooks(p, desired, False) == json.loads(p.read_text(encoding="utf-8"))
        if host == "pi":
            p = repo / ".pi" / "extensions" / "coding-orchestrator.ts"
            if not p.exists():
                return False
            return p.read_text(encoding="utf-8") == render_pi(repo)
    except Exception:
        return False
    return False

def ensure_enforcement_config(repo: Path, apply: bool) -> str:
    target = repo / ".orchestrator" / "enforcement.yaml"
    if apply and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples" / "enforcement.yaml", target)
    return str(target)



def ensure_session_context_config(repo: Path, apply: bool) -> str:
    target = repo / ".orchestrator" / "session-context.yaml"
    if apply and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples" / "session-context.yaml", target)
    return str(target)

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--host", choices=["claude-code","codex","pi","all"], default="all")
    p.add_argument("--apply", action="store_true")
    args=p.parse_args(); repo=args.repo.resolve()
    selected=["claude-code","codex","pi"] if args.host=="all" else [args.host]
    out={"apply":args.apply,"kernel":str(KERNEL),"enforcement_config":ensure_enforcement_config(repo,args.apply),"session_context_config":ensure_session_context_config(repo,args.apply),"hosts":{}}
    out["activation"] = project_activation.install(repo, selected, apply=args.apply)
    if "claude-code" in selected:
        frag=load_template(ROOT/"hosts"/"claude-code"/"hooks.template.json", repo)
        target=repo/".claude"/"settings.json"
        out["hosts"]["claude-code"]={"target":str(target),"merged":merge_hooks(target,frag,args.apply)}
    if "codex" in selected:
        frag=load_template(ROOT/"hosts"/"codex"/"hooks.template.json", repo)
        target=repo/".codex"/"hooks.json"
        out["hosts"]["codex"]={"target":str(target),"merged":merge_hooks(target,frag,args.apply),"note":"project hooks require Codex trust review"}
    if "pi" in selected:
        out["hosts"]["pi"]={"target":install_pi(repo,args.apply)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
