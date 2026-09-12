#!/usr/bin/env python3
"""Install/merge V6.3 host enforcement adapters into a repository.

This script only mutates host configuration when --apply is provided. It creates a backup
next to an existing JSON file before merging orchestrator hook groups.
"""
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KERNEL = (ROOT / "scripts" / "enforcement_kernel.py").resolve()


def load_template(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").replace("__KERNEL__", str(KERNEL).replace("\\", "\\\\")))


def merge_hooks(target: Path, fragment: dict[str, Any], apply: bool) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    merged = dict(existing)
    if fragment.get("description") and not merged.get("description"):
        merged["description"] = fragment["description"]
    hooks = dict(merged.get("hooks") or {})
    for event, groups in (fragment.get("hooks") or {}).items():
        current = list(hooks.get(event) or [])
        encoded = {json.dumps(x, sort_keys=True) for x in current}
        for group in groups:
            e = json.dumps(group, sort_keys=True)
            if e not in encoded:
                current.append(group); encoded.add(e)
        hooks[event] = current
    merged["hooks"] = hooks
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        target.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def install_pi(repo: Path, apply: bool) -> str:
    template = (ROOT / "hosts" / "pi" / "coding-orchestrator.template.ts").read_text(encoding="utf-8")
    text = template.replace("__KERNEL__", str(KERNEL).replace("\\", "\\\\"))
    target = repo / ".pi" / "extensions" / "coding-orchestrator.ts"
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists(): shutil.copy2(target, target.with_suffix(".ts.bak"))
        target.write_text(text, encoding="utf-8")
    return str(target)


def ensure_enforcement_config(repo: Path, apply: bool) -> str:
    target = repo / ".orchestrator" / "enforcement.yaml"
    if apply and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples" / "enforcement.yaml", target)
    return str(target)


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--host", choices=["claude-code","codex","pi","all"], default="all")
    p.add_argument("--apply", action="store_true")
    args=p.parse_args(); repo=args.repo.resolve()
    selected=["claude-code","codex","pi"] if args.host=="all" else [args.host]
    out={"apply":args.apply,"kernel":str(KERNEL),"enforcement_config":ensure_enforcement_config(repo,args.apply),"hosts":{}}
    if "claude-code" in selected:
        frag=load_template(ROOT/"hosts"/"claude-code"/"hooks.template.json")
        target=repo/".claude"/"settings.json"
        out["hosts"]["claude-code"]={"target":str(target),"merged":merge_hooks(target,frag,args.apply)}
    if "codex" in selected:
        frag=load_template(ROOT/"hosts"/"codex"/"hooks.template.json")
        target=repo/".codex"/"hooks.json"
        out["hosts"]["codex"]={"target":str(target),"merged":merge_hooks(target,frag,args.apply),"note":"project hooks require Codex trust review"}
    if "pi" in selected:
        out["hosts"]["pi"]={"target":install_pi(repo,args.apply)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
