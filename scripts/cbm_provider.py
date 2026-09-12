#!/usr/bin/env python3
"""Codebase Memory (CBM) provider for V6 Semantic Impact Engine.

The adapter treats CBM as structural evidence only. CBM risk labels are retained for
forensics but are explicitly non-authoritative for flow selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from code_intelligence_provider import CodeIntelligenceProvider, ProviderError
import fact_extractor

PROVIDER_ID = "codebase-memory-mcp"
RELATIONS = {
    "CALLS", "CALL_REFERENCE", "IMPORTS", "IMPLEMENTS", "INHERITS", "EXTENDS",
    "HTTP_CALLS", "ASYNC_CALLS", "DATA_FLOWS", "WRITES", "READS", "USAGE",
    "EMITS", "LISTENS_ON", "PRODUCES", "CONSUMES", "HANDLES", "CONFIGURES",
}
CROSS_RELATION_RE = re.compile(r"^CROSS[_-]", re.I)
ASYNC_RELATIONS = {"ASYNC_CALLS", "EMITS", "LISTENS_ON", "PRODUCES", "CONSUMES"}
INTEGRATION_RELATIONS = {"HTTP_CALLS", "ASYNC_CALLS", "EMITS", "LISTENS_ON", "PRODUCES", "CONSUMES"}
DATA_RELATIONS = {"DATA_FLOWS", "WRITES", "READS"}
CONTRACT_SUFFIX_RE = re.compile(r"(^|/)(openapi|swagger)([^/]*)\.(ya?ml|json)$|\.proto$|\.graphqls?$", re.I)


def _walk(value: Any) -> Iterator[Tuple[Optional[str], Any]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield None, v
            yield from _walk(v)


def _unwrap(payload: Any) -> Any:
    cur = payload
    for _ in range(5):
        if not isinstance(cur, dict):
            break
        for key in ("structuredContent", "structured_content", "result", "data", "payload"):
            if key in cur and isinstance(cur[key], (dict, list)):
                cur = cur[key]
                break
        else:
            break
    return cur


def _json_from_text(text: str) -> Any:
    text = text.strip()
    if not text:
        raise ProviderError("CBM returned empty stdout")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some CLI builds can prepend a notice. Find the first plausible JSON payload.
        starts = [p for p in (text.find("{"), text.find("[")) if p >= 0]
        if not starts:
            raise ProviderError("CBM output is not JSON")
        start = min(starts)
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"CBM output is not parseable JSON: {exc}") from exc


def _node_path(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return item.replace("\\", "/")
    if not isinstance(item, dict):
        return None
    for key in ("file_path", "path", "file", "source_file", "relative_path"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.replace("\\", "/")
    return None


def _node_name(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ("qualified_name", "symbol", "name", "fq_name"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _node_kind(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ("label", "kind", "node_type", "symbol_type"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _collect_named_lists(raw: Any, names: Set[str]) -> List[Any]:
    out: List[Any] = []
    for key, value in _walk(raw):
        if not key or key.lower() not in names:
            continue
        if isinstance(value, list):
            out.extend(value)
        elif isinstance(value, dict):
            for child in ("items", "results", "rows", "entries", "data"):
                if isinstance(value.get(child), list):
                    out.extend(value[child])
                    break
    return out


def _pagination_status(raw: Any) -> Dict[str, Any]:
    signals: List[Dict[str, Any]] = []
    for key, value in _walk(raw):
        if not key:
            continue
        low = key.lower()
        if low == "has_more" and value is True:
            signals.append({"key": key, "value": True})
        elif (low.startswith("next_") and low.endswith("cursor")) or low.endswith("_next_cursor"):
            if value not in (None, "", False):
                signals.append({"key": key, "value": str(value)[:200]})
    return {
        "status": "partial" if signals else "complete",
        "complete": not bool(signals),
        "continuation_signals": signals,
    }


def _collect_relations(raw: Any) -> List[Dict[str, Any]]:
    relations: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()
    if not isinstance(raw, (dict, list)):
        return relations
    for _, value in _walk(raw):
        if not isinstance(value, dict):
            continue
        rel = None
        for key in ("relationship", "relationship_type", "edge_type", "relation", "edge"):
            v = value.get(key)
            if isinstance(v, str):
                upper = v.upper()
                if upper in RELATIONS or CROSS_RELATION_RE.match(upper):
                    rel = upper
                    break
        if not rel:
            continue
        src = str(value.get("source") or value.get("from") or value.get("caller") or "")
        dst = str(value.get("target") or value.get("to") or value.get("callee") or "")
        sig = (rel, src, dst)
        if sig in seen:
            continue
        seen.add(sig)
        relations.append({
            "type": rel,
            "source": src or None,
            "target": dst or None,
            "confidence": value.get("confidence"),
            "strategy": value.get("strategy") or value.get("resolution_strategy"),
            "file_path": _node_path(value),
        })
    return relations


def _find_scalar_values(raw: Any, key_names: Set[str]) -> List[Any]:
    out: List[Any] = []
    for key, value in _walk(raw):
        if key and key.lower() in key_names and not isinstance(value, (dict, list)):
            out.append(value)
    return out


def _dedupe_nodes(items: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()
    for item in items:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = _node_name(item) or ""
        path = _node_path(item) or ""
        kind = _node_kind(item) or ""
        sig = (name, path, kind)
        if sig in seen:
            continue
        seen.add(sig)
        out.append({
            "name": name or None,
            "file_path": path or None,
            "kind": kind or None,
            "module": item.get("module") or item.get("package"),
            "service": item.get("service") or item.get("deployable") or item.get("component"),
            "project": item.get("project") or item.get("project_name"),
            "distance": item.get("distance") if item.get("distance") is not None else item.get("depth"),
            "risk": item.get("risk") or item.get("risk_level") or item.get("risk_classification"),
        })
    return out


def _git_head(repo: Path) -> Optional[str]:
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=False)
        return p.stdout.strip() if p.returncode == 0 else None
    except OSError:
        return None


def _file_hash(repo: Path, relpath: str) -> Optional[str]:
    p = repo / relpath
    if not p.is_file():
        return None
    h = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _coverage_summary(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {"status": "unknown", "raw": None}
    payload = _unwrap(raw)
    values = _find_scalar_values(payload, {"coverage", "coverage_percent", "coverage_pct", "percent", "ratio"})
    numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    misses = _find_scalar_values(payload, {"missed", "missed_count", "unindexed", "uncovered", "missing_edges"})
    return {
        "status": "reported",
        "numeric_candidates": numeric[:10],
        "miss_candidates": misses[:10],
        "raw": payload,
    }


def normalize_detect_changes(
    raw: Any,
    *,
    repo: Path,
    project: Optional[str] = None,
    provider_version: Optional[str] = None,
    coverage_raw: Any = None,
) -> Dict[str, Any]:
    """Normalize CBM's evolving detect_changes JSON into a stable V6 document."""
    repo = repo.resolve()
    payload = _unwrap(raw)
    changed_file_items = _collect_named_lists(payload, {"changed_files", "files_changed"})
    changed_symbol_items = _collect_named_lists(payload, {"changed_symbols", "modified_symbols", "symbols_changed"})
    impacted_items = _collect_named_lists(payload, {
        "impacted_symbols", "affected_symbols", "impacted_callers", "affected_callers",
        "callers", "blast_radius", "impacted_nodes", "affected_nodes",
    })

    changed_files = sorted({p for x in changed_file_items if (p := _node_path(x))})
    changed_symbols = _dedupe_nodes(changed_symbol_items)
    impacted_symbols = _dedupe_nodes(impacted_items)
    relations = _collect_relations(payload)

    all_nodes = changed_symbols + impacted_symbols
    all_paths = sorted({p for n in all_nodes if (p := n.get("file_path"))} | set(changed_files))
    modules_set = {str(n["module"]) for n in all_nodes if n.get("module")}
    services_set = {str(n["service"]) for n in all_nodes if n.get("service")}
    projects_set = {str(n["project"]) for n in all_nodes if n.get("project")}
    # Current CBM payloads do not guarantee module/service fields on every symbol. Reuse
    # the repository's manifest/deployable boundaries as a deterministic fallback.
    for path in all_paths:
        mr = fact_extractor.nearest_manifest_root(repo, path)
        if mr is not None:
            modules_set.add(mr)
        dr = fact_extractor.nearest_deployable_root(repo, path)
        if dr is not None:
            services_set.add(dr)
    projects_set.update(str(x) for x in _find_scalar_values(
        payload, {"source_project", "target_project", "project_name"}
    ) if isinstance(x, str) and x)
    services_set.update(str(x) for x in _find_scalar_values(
        payload, {"source_service", "target_service", "service_name"}
    ) if isinstance(x, str) and x)
    modules = sorted(modules_set)
    services = sorted(services_set)
    projects = sorted(projects_set)

    relation_types = sorted({r["type"] for r in relations})
    async_types = sorted(set(relation_types) & ASYNC_RELATIONS)
    integration_types = sorted({r for r in relation_types if r in INTEGRATION_RELATIONS or CROSS_RELATION_RE.match(r)})
    data_types = sorted(set(relation_types) & DATA_RELATIONS)
    cross_project = bool(projects and (len(projects) > 1 or (project and any(p != project for p in projects))))
    cross_service = bool(integration_types or len(services) > 1 or cross_project)
    async_boundary = bool(async_types)

    changed_contract_files = [p for p in changed_files if CONTRACT_SUFFIX_RE.search(p)]
    changed_route_symbols = [n for n in changed_symbols if str(n.get("kind") or "").lower() == "route"]
    public_contract_signal = bool(changed_contract_files or changed_route_symbols)

    risk_values = _find_scalar_values(payload, {"risk", "risk_level", "risk_classification", "overall_risk"})
    provider_risk = sorted({str(x) for x in risk_values if x is not None})

    direct_count = 0
    transitive_count = 0
    unknown_distance = 0
    for node in impacted_symbols:
        d = node.get("distance")
        if d in (0, "0", 1, "1"):
            direct_count += 1
        elif isinstance(d, int) and d > 1:
            transitive_count += 1
        else:
            unknown_distance += 1

    snapshot_material = {
        "git_head": _git_head(repo),
        "changed_file_hashes": {p: _file_hash(repo, p) for p in changed_files},
        "provider_payload": payload,
        "project": project,
        "provider_version": provider_version,
    }
    snapshot_id = hashlib.sha256(
        json.dumps(snapshot_material, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:20]

    evidence: List[Dict[str, Any]] = []
    if changed_symbols:
        evidence.append({"claim": "changed_symbols", "strength": "observed", "count": len(changed_symbols)})
    if impacted_symbols:
        evidence.append({"claim": "blast_radius", "strength": "derived", "count": len(impacted_symbols)})
    if integration_types:
        evidence.append({"claim": "integration_boundary", "strength": "derived", "relations": integration_types})
    if async_types:
        evidence.append({"claim": "async_boundary", "strength": "derived", "relations": async_types})
    if public_contract_signal:
        evidence.append({
            "claim": "public_contract_signal",
            "strength": "observed",
            "files": changed_contract_files,
            "route_symbols": [n.get("name") for n in changed_route_symbols],
        })

    return {
        "schema_version": 1,
        "provider": {
            "id": PROVIDER_ID,
            "version": provider_version,
            "project": project,
            "authority": "structural_evidence_only",
            "provider_risk_authoritative": False,
        },
        "snapshot": {
            "id": snapshot_id,
            "git_head": snapshot_material["git_head"],
            "changed_file_hashes": snapshot_material["changed_file_hashes"],
        },
        "changes": {
            "changed_files": changed_files,
            "changed_symbols": changed_symbols,
        },
        "impact": {
            "impacted_symbols": impacted_symbols,
            "impacted_symbol_count": len(impacted_symbols),
            "direct_dependents": direct_count,
            "transitive_dependents": transitive_count,
            "unknown_distance_dependents": unknown_distance,
            "affected_files": all_paths,
            "affected_modules": modules,
            "affected_services": services,
            "affected_projects": projects,
        },
        "boundaries": {
            "relation_types": relation_types,
            "integration_relations": integration_types,
            "async_relations": async_types,
            "data_relations": data_types,
            "cross_service": cross_service,
            "cross_project": cross_project,
            "async_boundary": async_boundary,
        },
        "contracts": {
            "public_contract_signal": public_contract_signal,
            "changed_contract_files": changed_contract_files,
            "changed_route_symbols": changed_route_symbols,
        },
        "completeness": _pagination_status(payload),
        "coverage": _coverage_summary(coverage_raw),
        "provider_opinion": {
            "risk_labels": provider_risk,
            "authoritative_for_flow": False,
        },
        "evidence": evidence,
        "raw_digest": hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest(),
    }


class CBMProvider(CodeIntelligenceProvider):
    provider_id = PROVIDER_ID

    def __init__(self, binary: str = "codebase-memory-mcp") -> None:
        self.binary = binary

    def capabilities(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_id,
            "changed_symbols": True,
            "blast_radius": True,
            "call_graph": True,
            "cross_service": True,
            "async_relations": True,
            "data_flow": True,
            "index_coverage": True,
            "provider_risk": "ignored_for_flow",
        }

    def _binary_path(self) -> Optional[str]:
        if os.path.isabs(self.binary) and Path(self.binary).exists():
            return self.binary
        return shutil.which(self.binary)

    def _version(self) -> Optional[str]:
        path = self._binary_path()
        if not path:
            return None
        for args in ([path, "--version"], [path, "version"]):
            p = subprocess.run(args, text=True, capture_output=True, check=False)
            if p.returncode == 0 and (p.stdout.strip() or p.stderr.strip()):
                return (p.stdout.strip() or p.stderr.strip()).splitlines()[0][:200]
        return None

    def health(self) -> Dict[str, Any]:
        path = self._binary_path()
        return {
            "provider": self.provider_id,
            "available": bool(path),
            "binary": path,
            "version": self._version() if path else None,
            "capabilities": self.capabilities(),
        }

    def _run_tool(self, tool: str, args: Optional[Dict[str, Any]] = None, *, tolerate: bool = False) -> Any:
        path = self._binary_path()
        if not path:
            raise ProviderError("codebase-memory-mcp binary not found on PATH")
        payload = json.dumps(args or {}, ensure_ascii=False)
        # v0.10+ supports stdin JSON plus --format json; keep --quiet diagnostics off stdout.
        cmd = [path, "cli", "--quiet", tool, "--format", "json"]
        p = subprocess.run(
            cmd,
            input=payload if args else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if p.returncode != 0:
            # Compatibility fallback for older builds that expose --raw globally.
            legacy = [path, "cli", "--raw", tool]
            p2 = subprocess.run(
                legacy + ([payload] if args else []),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if p2.returncode == 0:
                return _json_from_text(p2.stdout)
            if tolerate:
                return {"_tool_error": {"tool": tool, "stderr": (p2.stderr or p.stderr)[-2000:]}}
            raise ProviderError(f"CBM {tool} failed: {(p2.stderr or p.stderr)[-2000:]}")
        return _json_from_text(p.stdout)

    @staticmethod
    def _project_name(index_result: Any, repo: Path) -> str:
        payload = _unwrap(index_result)
        if isinstance(payload, dict):
            for key in ("project", "project_name", "name"):
                val = payload.get(key)
                if isinstance(val, str) and val:
                    return val
        return repo.resolve().name

    def collect_impact(
        self,
        repo: Path,
        *,
        scope: str = "all",
        base_branch: Optional[str] = None,
        depth: int = 3,
        refresh_index: bool = True,
    ) -> Dict[str, Any]:
        repo = repo.resolve()
        if not self.health()["available"]:
            raise ProviderError("codebase-memory-mcp is unavailable")
        if depth < 1 or depth > 5:
            raise ProviderError("CBM depth must be between 1 and 5")

        index_result: Any = None
        project = repo.name
        if refresh_index:
            index_result = self._run_tool("index_repository", {"repo_path": str(repo)})
            project = self._project_name(index_result, repo)

        args: Dict[str, Any] = {"scope": scope, "depth": depth, "project": project}
        if scope == "branch" and base_branch:
            args["base_branch"] = base_branch
        raw = self._run_tool("detect_changes", args)
        coverage = self._run_tool("check_index_coverage", {"project": project}, tolerate=True)
        normalized = normalize_detect_changes(
            raw,
            repo=repo,
            project=project,
            provider_version=self._version(),
            coverage_raw=coverage,
        )
        normalized["collection"] = {
            "scope": scope,
            "base_branch": base_branch,
            "depth": depth,
            "index_refreshed": refresh_index,
            "index_result_digest": hashlib.sha256(
                json.dumps(index_result, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
            ).hexdigest() if index_result is not None else None,
        }
        return normalized


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", default="codebase-memory-mcp")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("capabilities")

    c = sub.add_parser("collect")
    c.add_argument("--repo", type=Path, default=Path("."))
    c.add_argument("--scope", choices=["unstaged", "staged", "all", "branch"], default="all")
    c.add_argument("--base-branch")
    c.add_argument("--depth", type=int, default=3)
    c.add_argument("--no-refresh-index", action="store_true")
    c.add_argument("--output", type=Path)

    n = sub.add_parser("normalize")
    n.add_argument("--input", type=Path, required=True)
    n.add_argument("--repo", type=Path, default=Path("."))
    n.add_argument("--project")
    n.add_argument("--output", type=Path)

    args = p.parse_args(argv)
    provider = CBMProvider(args.binary)
    if args.command == "health":
        out = provider.health()
    elif args.command == "capabilities":
        out = provider.capabilities()
    elif args.command == "collect":
        try:
            out = provider.collect_impact(
                args.repo,
                scope=args.scope,
                base_branch=args.base_branch,
                depth=args.depth,
                refresh_index=not args.no_refresh_index,
            )
        except ProviderError as exc:
            print(json.dumps({"status": "PROVIDER_UNAVAILABLE", "provider": PROVIDER_ID, "error": str(exc)}, indent=2))
            return 2
    else:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        out = normalize_detect_changes(raw, repo=args.repo, project=args.project)

    text = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output = getattr(args, "output", None)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
