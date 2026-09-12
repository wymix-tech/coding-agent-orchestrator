#!/usr/bin/env python3
"""Engineering Policy Router and lightweight enforcement engine for V6.1.

Project-owned machine policy is authoritative. External rule sources (such as ECC) are
context/guidance unless explicitly promoted into a project policy with an enforcement mapping.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import ecc_rules_adapter

LEVEL_RANK = {"PREFER": 0, "SHOULD": 1, "MUST": 2}
JAVA_IMPORT_RE = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;")
JAVA_PACKAGE_RE = re.compile(r"(?m)^\s*package\s+([\w.]+)\s*;")


def load_doc(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is None:
        raise RuntimeError("PyYAML is required to read YAML policy files")
    return yaml.safe_load(text)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


def _match(value: str, pattern: str) -> bool:
    """Glob match where **/ may match zero directory segments as users expect."""
    if fnmatch.fnmatch(value, pattern):
        return True
    variants = {pattern}
    while "/**/" in pattern:
        pattern = pattern.replace("/**/", "/", 1)
        variants.add(pattern)
    return any(fnmatch.fnmatch(value, p) for p in variants)


def infer_languages(files: Iterable[str]) -> list[str]:
    mapping = {".java": "java", ".kt": "kotlin", ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".go": "golang", ".rs": "rust"}
    langs = []
    for f in files:
        lang = mapping.get(Path(f).suffix.lower())
        if lang:
            langs.append(lang)
    return sorted(set(langs))


def infer_frameworks(repo: Path) -> list[str]:
    frameworks: set[str] = set()
    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        p = repo / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            if "spring-boot" in text or "org.springframework" in text:
                frameworks.add("spring-boot")
    return sorted(frameworks)


def infer_layers(files: Iterable[str], layer_defs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for layer, spec in layer_defs.items():
        for f in files:
            if any(_match(f, g) for g in spec.get("path_globs", [])):
                found.append(layer)
                break
    return sorted(set(found))


def _scope_matches(rule: dict[str, Any], ctx: dict[str, Any], stage: str) -> bool:
    load = rule.get("load") or {}
    stages = load.get("stages") or []
    if stages and stage not in stages and not load.get("always"):
        return False
    scope = rule.get("scope") or {}
    files = ctx["files"]
    if scope.get("languages") and not set(scope["languages"]) & set(ctx["languages"]):
        return False
    if scope.get("frameworks") and not set(scope["frameworks"]) & set(ctx["frameworks"]):
        return False
    if scope.get("layers") and not set(scope["layers"]) & set(ctx["layers"]):
        return False
    if scope.get("file_globs") and not any(_match(f, g) for f in files for g in scope["file_globs"]):
        return False
    signals = set(ctx.get("signals", []))
    if scope.get("signals") and not set(scope["signals"]) & signals:
        return False
    return True


def _merge_rules(packs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge by ID using precedence; lower precedence is loaded first.

    A higher-precedence rule may strengthen a lower rule. It may not silently downgrade a
    MUST to SHOULD/PREFER; attempted downgrades are reported as conflicts.
    """
    by_id: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for pack in sorted(packs, key=lambda p: int(p.get("precedence", 0))):
        for raw in pack.get("rules", []):
            rule = dict(raw)
            rule.setdefault("source_pack", pack.get("id"))
            rule.setdefault("precedence", pack.get("precedence", 0))
            rid = rule["id"]
            old = by_id.get(rid)
            if old and LEVEL_RANK.get(rule.get("level", "PREFER"), 0) < LEVEL_RANK.get(old.get("level", "PREFER"), 0):
                conflicts.append({
                    "rule_id": rid,
                    "type": "ILLEGAL_DOWNGRADE",
                    "kept_level": old.get("level"),
                    "rejected_level": rule.get("level"),
                    "kept_source": old.get("source_pack"),
                    "rejected_source": rule.get("source_pack"),
                })
                continue
            by_id[rid] = rule
    return list(by_id.values()), conflicts


def load_policy(repo: Path, manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = load_doc(manifest_path) or {}
    base = manifest_path.parent
    packs = []
    for ref in manifest.get("packs", []):
        if not ref.get("enabled", True):
            continue
        doc = load_doc((base / ref["path"]).resolve()) or {}
        packs.append({
            "id": ref.get("id") or doc.get("id") or ref["path"],
            "precedence": int(ref.get("precedence", doc.get("precedence", 0))),
            "rules": doc.get("rules", []),
        })
    rules, conflicts = _merge_rules(packs)
    return manifest, rules, conflicts


def build_context(repo: Path, impact: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    changed = [x.get("file_path") for x in (impact.get("changes") or {}).get("changed_symbols", []) if x.get("file_path")]
    changed += (impact.get("changes") or {}).get("changed_files", [])
    affected = (impact.get("impact") or {}).get("affected_files", [])
    files = _unique(changed + affected)
    layers = infer_layers(files, manifest.get("layers") or {})
    signals = []
    boundaries = impact.get("boundaries") or {}
    contracts = impact.get("contracts") or {}
    if boundaries.get("cross_service"):
        signals.append("cross_service")
    if boundaries.get("async_boundary"):
        signals.append("async")
    if contracts.get("public_contract_signal"):
        signals.append("public_contract")
    return {
        "files": files,
        "changed_files": _unique(changed),
        "affected_files": _unique(affected),
        "languages": infer_languages(files),
        "frameworks": infer_frameworks(repo),
        "layers": layers,
        "signals": signals,
    }


def route(repo: Path, impact: dict[str, Any], manifest_path: Path, stage: str = "implementation", ecc_root: Optional[Path] = None) -> dict[str, Any]:
    manifest, rules, conflicts = load_policy(repo, manifest_path)
    ctx = build_context(repo, impact, manifest)
    applicable = [r for r in rules if _scope_matches(r, ctx, stage)]
    applicable.sort(key=lambda r: (-LEVEL_RANK.get(r.get("level", "PREFER"), 0), r["id"]))

    external: list[dict[str, Any]] = []
    ext_cfg = manifest.get("external_sources") or []
    for source in ext_cfg:
        if source.get("kind") != "ecc_rules" or not source.get("enabled", True):
            continue
        root = ecc_root or Path(source.get("root", ".claude/rules/ecc"))
        if not root.is_absolute():
            root = repo / root
        catalog = ecc_rules_adapter.discover(root)
        selected = ecc_rules_adapter.relevant_rules(catalog, ctx["files"], source.get("packs", [])) if catalog.get("available") else []
        external.append({
            "id": source.get("id", "ecc"),
            "kind": "ecc_rules",
            "available": catalog.get("available", False),
            "root": catalog.get("root"),
            "selected_rules": selected,
            "authority": "external_guidance",
            "can_block": False,
        })

    enforcements = []
    for rule in applicable:
        for e in rule.get("enforcement", []):
            enforcements.append({
                "rule_id": rule["id"],
                "level": rule.get("level", "PREFER"),
                "engine": e.get("engine"),
                "gate": e.get("gate"),
                "required": bool(e.get("required", rule.get("level") == "MUST")),
                "command": e.get("command"),
            })

    material = {"stage": stage, "context": ctx, "rules": [r["id"] for r in applicable], "manifest": str(manifest_path)}
    snapshot = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]
    return {
        "schema_version": 1,
        "status": "CONFLICT" if conflicts else "ROUTED",
        "stage": stage,
        "policy_snapshot_id": snapshot,
        "manifest": str(manifest_path),
        "context": ctx,
        "applicable_rules": applicable,
        "enforcements": enforcements,
        "conflicts": conflicts,
        "external_sources": external,
        "loading": {
            "principle": "manifest at bootstrap; summaries at planning; exact rules at implementation; evidence at review/verification",
            "always_loaded_rule_ids": [r["id"] for r in applicable if (r.get("load") or {}).get("always")],
            "exact_rule_ids": [r["id"] for r in applicable],
        },
    }


def _java_packages(repo: Path) -> dict[str, list[Path]]:
    packages: dict[str, list[Path]] = {}
    for p in repo.rglob("*.java"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = JAVA_PACKAGE_RE.search(text)
        if m:
            packages.setdefault(m.group(1), []).append(p)
    return packages


def _target_layer(import_name: str, manifest: dict[str, Any]) -> Optional[str]:
    for layer, spec in (manifest.get("layers") or {}).items():
        for pkg in spec.get("package_globs", []):
            # Convert Java package glob to fnmatch-friendly shape.
            if _match(import_name, pkg):
                return layer
    return None


def evaluate(repo: Path, plan: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    manifest, _, _ = load_policy(repo, manifest_path)
    changed = set(plan.get("context", {}).get("changed_files", []))
    results = []
    for rule in plan.get("applicable_rules", []):
        constraint = rule.get("constraint") or {}
        kind = constraint.get("type")
        if kind != "forbidden_dependency":
            results.append({
                "rule_id": rule["id"],
                "level": rule.get("level"),
                "status": "pending_external_enforcement" if rule.get("enforcement") else "advisory",
                "violations": [],
            })
            continue
        from_layer = constraint.get("from_layer")
        to_layers = set(constraint.get("to_layers") or [constraint.get("to_layer")]) - {None}
        layer_spec = (manifest.get("layers") or {}).get(from_layer, {})
        files = []
        for g in layer_spec.get("path_globs", []):
            files.extend(repo.glob(g))
        if constraint.get("scope", "changed") == "changed":
            files = [p for p in files if p.relative_to(repo).as_posix() in changed]
        violations = []
        for p in sorted(set(files)):
            if p.suffix != ".java":
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for imp in JAVA_IMPORT_RE.findall(text):
                target = _target_layer(imp, manifest)
                if target in to_layers:
                    violations.append({
                        "file": p.relative_to(repo).as_posix(),
                        "import": imp,
                        "from_layer": from_layer,
                        "to_layer": target,
                    })
        status = "failed" if violations else "passed"
        results.append({"rule_id": rule["id"], "level": rule.get("level"), "status": status, "violations": violations})
    blocking = [r for r in results if r["status"] == "failed" and r.get("level") == "MUST"]
    return {
        "schema_version": 1,
        "policy_snapshot_id": plan.get("policy_snapshot_id"),
        "status": "FAILED" if blocking else "PASSED",
        "results": results,
        "blocking_violations": blocking,
    }



def build_state_gates(plan: dict[str, Any], evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate applicable policy enforcement into V5 quality-gate records."""
    eval_by_rule = {r.get("rule_id"): r for r in evaluation.get("results", [])}
    gates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for enf in plan.get("enforcements", []):
        rid = enf.get("rule_id")
        gate = enf.get("gate") or enf.get("engine") or "policy"
        key = (str(rid), str(gate))
        if key in seen:
            continue
        seen.add(key)
        required = bool(enf.get("required"))
        engine = enf.get("engine")
        ev = eval_by_rule.get(rid) or {}
        status = "pending"
        evidence_ref = None
        if engine == "v6-policy-check" and ev.get("status") in {"passed", "failed"}:
            status = ev["status"]
            evidence_ref = f"policy-evaluation.json#/{rid}"
        gates.append({
            "name": f"policy:{rid}:{gate}",
            "rule_id": rid,
            "gate": gate,
            "engine": engine,
            "required": required,
            "status": status,
            "command": enf.get("command"),
            "evidence_ref": evidence_ref,
        })
    return gates


def render_context(plan: dict[str, Any]) -> str:
    """Render a compact, role-neutral policy context pack. Full rule corpus stays external."""
    lines = [
        "# Engineering Policy Context",
        "",
        f"Policy snapshot: `{plan.get('policy_snapshot_id')}`",
        f"Stage: `{plan.get('stage')}`",
        "",
        "## Applicable project rules",
    ]
    rules = plan.get("applicable_rules", [])
    if not rules:
        lines.append("- None selected.")
    for rule in rules:
        lines.append(f"- **{rule.get('id')} [{rule.get('level','PREFER')}]** {rule.get('title','')}")
        stmt = str(rule.get("statement") or "").strip()
        if stmt:
            lines.append(f"  {stmt}")
    ext = []
    for src in plan.get("external_sources", []):
        for rule in src.get("selected_rules", []):
            ext.append(rule)
    lines += ["", "## External guidance references"]
    if not ext:
        lines.append("- None available/selected.")
    else:
        for rule in ext:
            lines.append(f"- `{rule.get('id')}` -> `{rule.get('path')}` (non-authoritative guidance)")
    lines += ["", "Do not infer permission from missing rules. Project MUST rules remain authoritative.", ""]
    return "\n".join(lines)

def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route")
    r.add_argument("--repo", type=Path, default=Path("."))
    r.add_argument("--impact", type=Path, required=True)
    r.add_argument("--manifest", type=Path, default=Path(".orchestrator/policies/manifest.yaml"))
    r.add_argument("--stage", default="implementation", choices=["bootstrap", "planning", "implementation", "review", "verification"])
    r.add_argument("--ecc-root", type=Path)
    r.add_argument("--output", type=Path)
    e = sub.add_parser("evaluate")
    e.add_argument("--repo", type=Path, default=Path("."))
    e.add_argument("--plan", type=Path, required=True)
    e.add_argument("--manifest", type=Path, default=Path(".orchestrator/policies/manifest.yaml"))
    e.add_argument("--output", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "route":
        impact = load_doc(args.impact)
        result = route(args.repo.resolve(), impact, args.manifest.resolve(), args.stage, args.ecc_root)
    else:
        plan = load_doc(args.plan)
        result = evaluate(args.repo.resolve(), plan, args.manifest.resolve())
    if args.output:
        dump_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"FAILED", "CONFLICT"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
