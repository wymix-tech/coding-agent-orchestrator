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
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from code_intelligence_provider import CodeIntelligenceProvider, ProviderError
import fact_extractor
import repository_snapshot

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
NON_PRODUCT_ROOTS = {
    ".git", ".orchestrator", ".agents", ".claude", ".codex", ".pi",
    "_bmad", ".bmad", "bmad", "node_modules", "vendor", ".venv", "venv",
}
PRODUCT_FILE_NAMES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml",
    "requirements.txt", "go.mod", "cargo.toml", "composer.json", "makefile",
}
PRODUCT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".go", ".java", ".kt",
    ".kts", ".m", ".mm", ".php", ".py", ".rb", ".rs", ".scala", ".swift",
    ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".proto", ".graphql",
    ".graphqls", ".sql", ".sol", ".dart", ".ex", ".exs", ".erl", ".fs", ".fsx",
}


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
        parsed = json.loads(text)
        # Older CLI builds may return the standard MCP content envelope rather than the
        # raw tool payload. Decode a JSON text content item when present.
        if isinstance(parsed, dict) and isinstance(parsed.get("content"), list):
            for item in parsed["content"]:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    inner = item["text"].strip()
                    if inner.startswith(("{", "[")):
                        try:
                            return json.loads(inner)
                        except json.JSONDecodeError:
                            pass
        return parsed
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


def repository_baseline(repo: Path) -> Dict[str, Any]:
    """Classify whether CBM detect_changes has a meaningful Git/code baseline.

    BMAD and Orchestrator installation files are workflow metadata, not an application
    codebase. CBM's detect_changes requires HEAD and a resolvable base commit, so a genuinely
    empty greenfield project must not be reported as a provider outage.
    """
    repo = repo.resolve()
    product_files: List[str] = []
    if repo.exists():
        for path in repo.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(repo)
            except ValueError:
                continue
            if any(part.lower() in NON_PRODUCT_ROOTS for part in rel.parts[:-1]):
                continue
            if path.name.lower() in PRODUCT_FILE_NAMES or path.suffix.lower() in PRODUCT_SUFFIXES:
                product_files.append(rel.as_posix())
    head = _git_head(repo)
    return {
        "status": "empty_greenfield" if not product_files else ("ready" if head else "unborn_greenfield"),
        "git_head": head,
        "product_files": sorted(product_files),
    }


def empty_greenfield_impact(repo: Path, baseline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a neutral pre-implementation snapshot when there is no product code to analyze."""
    baseline = baseline or repository_baseline(repo)
    out = normalize_detect_changes(
        {"changed_files": [], "changed_symbols": [], "impacted_symbols": []},
        repo=repo,
        project=repo.resolve().name,
    )
    out["provider"].update({"available": None, "invoked": False})
    out["coverage"] = {"status": "not_applicable", "reason": "no_product_code"}
    out["collection"] = {
        "status": "not_applicable",
        "reason": "empty_greenfield_project",
        "cbm_invoked": False,
        "git_head": baseline.get("git_head"),
        "product_file_count": 0,
    }
    return out


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
    base_ref: Optional[str] = None,
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
        "repository_snapshot_id": repository_snapshot.fingerprint(repo),
        "comparison_basis": repository_snapshot.comparison_basis(repo, base_ref),
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
            "git_head": _git_head(repo),  # trace only; excluded from snapshot identity
            "repository_snapshot_id": snapshot_material["repository_snapshot_id"],
            "comparison_basis": snapshot_material["comparison_basis"],
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


def _combined_process_text(proc: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if proc.stderr:
        parts.append(proc.stderr.strip())
    if proc.stdout:
        parts.append(proc.stdout.strip())
    return "\n".join(x for x in parts if x)


def _looks_like_cli_shape_error(text: str, args: Optional[Dict[str, Any]] = None) -> bool:
    """Return True only when failure plausibly means the CLI invocation shape is unsupported.

    This is intentionally narrow. Runtime/indexing failures must not trigger a second mutation
    attempt because a compatibility fallback could mask the real cause or repeat side effects.
    """
    low = (text or "").lower()
    syntax_markers = (
        "unknown option", "unknown flag", "unrecognized option", "unrecognized argument",
        "unexpected argument", "unknown argument", "unknown tool", "unknown command", "invalid option",
    )
    if any(x in low for x in syntax_markers):
        return True
    # Older CBM builds did not read JSON from stdin. If we supplied a required field but the
    # tool says that exact field is missing, retrying with legacy inline JSON is safe and useful.
    for key in (args or {}):
        key_low = key.lower()
        phrases = (
            f"{key_low} is required",
            f"missing required {key_low}",
            f"missing required argument: {key_low}",
        )
        if any(x in low for x in phrases):
            return True
    return False



def _candidate_binary_paths(binary: str) -> List[str]:
    """Return deterministic CBM binary candidates, including common installer paths.

    Coding-agent subprocesses often inherit a smaller PATH than the user's interactive shell.
    The upstream installer commonly places the binary in ~/.local/bin, so PATH-only discovery
    creates a false PROVIDER_UNAVAILABLE even when CBM is installed and callable by the user.
    """
    candidates: List[str] = []
    env = os.environ.get("ORCHESTRATOR_CBM_BINARY") or os.environ.get("CBM_BINARY")
    if env:
        candidates.append(os.path.expanduser(env))
    if os.path.isabs(binary) or os.sep in binary:
        candidates.append(os.path.expanduser(binary))
    else:
        found = shutil.which(binary)
        if found:
            candidates.append(found)
        home = Path.home()
        candidates.extend([
            str(home / ".local" / "bin" / binary),
            str(home / "bin" / binary),
            f"/opt/homebrew/bin/{binary}",
            f"/usr/local/bin/{binary}",
            f"/usr/bin/{binary}",
        ])
    out: List[str] = []
    seen: Set[str] = set()
    for item in candidates:
        expanded = str(Path(item).expanduser())
        if expanded in seen:
            continue
        seen.add(expanded)
        p = Path(expanded)
        if p.is_file() and os.access(str(p), os.X_OK):
            out.append(str(p.resolve()))
    return out


def _flags_for_args(args: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Render simple MCP arguments as schema-generated CLI flags.

    Current CBM generates `--kebab-case` flags from each tool schema. Complex values fall back
    to stdin/inline JSON rather than guessing how a particular build serializes them.
    """
    flags: List[str] = []
    for key, value in (args or {}).items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                flags.append(flag)
            # False booleans are omitted; tools that need explicit false use JSON fallback.
            continue
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            flags.extend([flag, str(value)])
            continue
        if value is None:
            continue
        return None
    return flags


def _classify_provider_error(message: str) -> str:
    low = (message or "").lower()
    if "binary not found" in low or "not found on path" in low:
        return "BINARY_NOT_FOUND"
    if "timed out" in low:
        return "TIMEOUT"
    if "not parseable json" in low or "not json" in low or "empty stdout" in low:
        return "OUTPUT_PROTOCOL_ERROR"
    if _looks_like_cli_shape_error(low):
        return "CLI_INCOMPATIBLE"
    return "OPERATION_FAILED"


DEFAULT_CBM_TIMEOUT_SECONDS = float(os.environ.get("ORCHESTRATOR_CBM_TIMEOUT_SECONDS", "120"))


class CBMProvider(CodeIntelligenceProvider):
    provider_id = PROVIDER_ID

    def __init__(self, binary: str = "codebase-memory-mcp", timeout_seconds: Optional[float] = None) -> None:
        self.binary = binary
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None else DEFAULT_CBM_TIMEOUT_SECONDS)
        self._tool_protocol_cache: Dict[str, Dict[str, Any]] = {}
        self._last_diagnostics: Dict[str, Any] = {}
        if self.timeout_seconds <= 0:
            raise ProviderError("CBM timeout must be greater than zero")

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
        candidates = _candidate_binary_paths(self.binary)
        return candidates[0] if candidates else None

    def binary_candidates(self) -> List[str]:
        return _candidate_binary_paths(self.binary)

    def _version(self) -> Optional[str]:
        path = self._binary_path()
        if not path:
            return None
        for args in ([path, "--version"], [path, "version"]):
            try:
                p = subprocess.run(args, text=True, capture_output=True, check=False, timeout=min(self.timeout_seconds, 10.0))
            except subprocess.TimeoutExpired:
                continue
            if p.returncode == 0 and (p.stdout.strip() or p.stderr.strip()):
                return (p.stdout.strip() or p.stderr.strip()).splitlines()[0][:200]
        return None

    def _cli_protocol(self) -> Dict[str, Any]:
        """Probe CBM CLI surface without mutating project data."""
        path = self._binary_path()
        if not path:
            return {"status": "unavailable", "mode": None, "supports_raw": False, "supports_format": False}
        try:
            p = subprocess.run([path, "cli", "--help"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=min(self.timeout_seconds, 10.0))
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "mode": None, "supports_raw": False, "supports_format": False}
        except OSError as exc:
            return {"status": "unavailable", "mode": None, "supports_raw": False, "supports_format": False, "error": str(exc)}
        text = _combined_process_text(p)
        low = text.lower()
        supports_format = "--format" in low
        supports_raw = "--raw" in low
        if supports_format:
            mode = "schema-cli"
        elif supports_raw:
            mode = "legacy-raw"
        elif p.returncode == 0 or text:
            mode = "basic-cli"
        else:
            mode = "unknown"
        return {
            "status": "compatible" if mode != "unknown" else "unknown",
            "mode": mode,
            "supports_raw": supports_raw,
            "supports_format": supports_format,
        }

    def _tool_protocol(self, tool: str) -> Dict[str, Any]:
        cached = self._tool_protocol_cache.get(tool)
        if cached is not None:
            return cached
        path = self._binary_path()
        if not path:
            result = {"status": "unavailable", "supports_format": False, "help": ""}
            self._tool_protocol_cache[tool] = result
            return result
        try:
            p = subprocess.run(
                [path, "cli", tool, "--help"], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False, timeout=min(self.timeout_seconds, 10.0)
            )
        except subprocess.TimeoutExpired:
            result = {"status": "timeout", "supports_format": False, "help": ""}
            self._tool_protocol_cache[tool] = result
            return result
        except OSError as exc:
            result = {"status": "unavailable", "supports_format": False, "help": "", "error": str(exc)}
            self._tool_protocol_cache[tool] = result
            return result
        text = _combined_process_text(p)
        low = text.lower()
        result = {
            "status": "compatible" if (p.returncode == 0 or text) else "unknown",
            "supports_format": "--format" in low,
            "supports_json_envelope": "--json" in low,
            "help": text[-4000:],
        }
        self._tool_protocol_cache[tool] = result
        return result

    def diagnostics(self) -> Dict[str, Any]:
        path = self._binary_path()
        return {
            "provider": self.provider_id,
            "requested_binary": self.binary,
            "binary": path,
            "binary_candidates": self.binary_candidates(),
            "version": self._version() if path else None,
            "cli_protocol": self._cli_protocol() if path else {"status": "unavailable"},
            "last_call": self._last_diagnostics,
        }

    def health(self) -> Dict[str, Any]:
        path = self._binary_path()
        protocol = self._cli_protocol() if path else {"status": "unavailable", "mode": None}
        return {
            "provider": self.provider_id,
            "available": bool(path),
            "binary": path,
            "binary_candidates": self.binary_candidates(),
            "version": self._version() if path else None,
            "cli_protocol": protocol,
            "capabilities": self.capabilities(),
        }

    def probe_cli(self) -> Dict[str, Any]:
        """Run a non-mutating CLI smoke probe for doctor/diagnostics."""
        base = self.health()
        if not base.get("available"):
            return {**base, "probe_status": "unavailable", "probe_error": "binary not found"}
        try:
            self._run_tool("list_projects")
            return {**base, "probe_status": "compatible", "probe_error": None}
        except Exception as exc:
            return {**base, "probe_status": "incompatible", "probe_error": str(exc)[:2000]}

    def _run(self, cmd: List[str], *, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=self.timeout_seconds,
        )

    def _run_tool(self, tool: str, args: Optional[Dict[str, Any]] = None, *, tolerate: bool = False) -> Any:
        """Invoke CBM using the best protocol actually advertised by the installed build.

        Current builds expose schema-generated flags and `--format json`; older builds accept
        stdin or inline JSON.  A runtime/indexing failure is terminal: compatibility fallback is
        only allowed after a parser/shape error, which prevents duplicate side effects.
        """
        path = self._binary_path()
        if not path:
            searched = [str(Path.home() / ".local/bin/codebase-memory-mcp"), "/opt/homebrew/bin/codebase-memory-mcp", "/usr/local/bin/codebase-memory-mcp"]
            raise ProviderError(
                "codebase-memory-mcp binary not found on PATH or common install locations; "
                f"set ORCHESTRATOR_CBM_BINARY to the absolute path (checked: {', '.join(searched)})"
            )
        payload = json.dumps(args or {}, ensure_ascii=False)
        attempts: List[Dict[str, Any]] = []

        def record(label: str, proc: subprocess.CompletedProcess[str]) -> None:
            attempts.append({
                "mode": label,
                "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[-4000:],
                "stdout": (proc.stdout or "")[-4000:],
            })

        def invoke(label: str, cmd: List[str], *, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
            try:
                proc = self._run(cmd, input_text=input_text)
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                attempts.append({
                    "mode": label, "returncode": None, "timeout": self.timeout_seconds,
                    "stderr": str(stderr)[-4000:], "stdout": str(stdout)[-4000:],
                })
                self._last_diagnostics = {"tool": tool, "attempts": attempts, "error_class": "TIMEOUT"}
                detail = "\n".join(x for x in [str(stderr).strip(), str(stdout).strip()] if x)
                raise ProviderError(
                    f"CBM {tool} timed out after {self.timeout_seconds:g}s" + (f": {detail[-4000:]}" if detail else "")
                ) from exc
            record(label, proc)
            return proc

        def success(label: str, proc: subprocess.CompletedProcess[str]) -> Any:
            try:
                value = _json_from_text(proc.stdout)
            except ProviderError as exc:
                self._last_diagnostics = {
                    "tool": tool, "attempts": attempts, "error_class": "OUTPUT_PROTOCOL_ERROR",
                    "hint": "Installed CBM returned non-JSON output; prefer --format json on schema CLI builds.",
                }
                raise
            self._last_diagnostics = {"tool": tool, "attempts": attempts, "selected_mode": label, "error_class": None}
            return value

        # Preferred for modern CBM: schema-generated flags + explicit JSON output.  We only use
        # this shape when the tool's own help advertises --format, so an indexing command is not
        # speculatively executed twice.
        tool_protocol = self._tool_protocol(tool)
        rendered = _flags_for_args(args)
        if tool_protocol.get("supports_format") and rendered is not None:
            proc = invoke("schema-flags-json", [path, "cli", tool, *rendered, "--format", "json"])
            if proc.returncode == 0:
                return success("schema-flags-json", proc)
            error = _combined_process_text(proc)
            if not _looks_like_cli_shape_error(error, args):
                self._last_diagnostics = {"tool": tool, "attempts": attempts, "error_class": _classify_provider_error(error)}
                if tolerate:
                    return {"_tool_error": {"tool": tool, "error": error[-4000:], "attempts": attempts}}
                raise ProviderError(f"CBM {tool} failed: {error[-4000:]}")
            # A help/runtime mismatch is safe to retry with the documented JSON transport.

        # Backward-compatible JSON stdin. Current upstream still accepts this form for tools with
        # arguments; zero-argument tools intentionally receive no stdin.
        try:
            proc = invoke("stdin-json", [path, "cli", tool], input_text=payload if args else None)
        except ProviderError as exc:
            if tolerate:
                return {"_tool_error": {"tool": tool, "error": str(exc), "attempts": attempts}}
            raise
        if proc.returncode == 0:
            try:
                return success("stdin-json", proc)
            except ProviderError:
                # If this build has compact default output but help failed to advertise --format,
                # do not rerun a mutating tool. Surface an output-protocol error instead.
                if tolerate:
                    return {"_tool_error": {"tool": tool, "error": "CBM returned non-JSON output", "attempts": attempts}}
                raise
        first_error = _combined_process_text(proc)
        if not _looks_like_cli_shape_error(first_error, args):
            self._last_diagnostics = {"tool": tool, "attempts": attempts, "error_class": _classify_provider_error(first_error)}
            if tolerate:
                return {"_tool_error": {"tool": tool, "error": first_error[-4000:], "attempts": attempts}}
            raise ProviderError(f"CBM {tool} failed: {first_error[-4000:]}")

        # Legacy/backward-compatible inline JSON. Current CBM documents this as deprecated but
        # accepted. Only shape errors reach here, so runtime failures are never repeated.
        inline_cmd = [path, "cli", tool] + ([payload] if args else [])
        proc2 = invoke("inline-json", inline_cmd)
        if proc2.returncode == 0:
            return success("inline-json", proc2)
        second_error = _combined_process_text(proc2)
        if not _looks_like_cli_shape_error(second_error, args):
            self._last_diagnostics = {"tool": tool, "attempts": attempts, "error_class": _classify_provider_error(second_error)}
            if tolerate:
                return {"_tool_error": {"tool": tool, "error": second_error[-4000:], "attempts": attempts}}
            raise ProviderError(f"CBM {tool} failed: {second_error[-4000:]}")

        # Very old builds exposed --raw as a global CLI option. Probe before using it.
        protocol = self._cli_protocol()
        if protocol.get("supports_raw"):
            raw_cmd = [path, "cli", "--raw", tool] + ([payload] if args else [])
            proc3 = invoke("legacy-raw", raw_cmd)
            if proc3.returncode == 0:
                return success("legacy-raw", proc3)
            third_error = _combined_process_text(proc3)
            self._last_diagnostics = {"tool": tool, "attempts": attempts, "error_class": _classify_provider_error(third_error)}
            if tolerate:
                return {"_tool_error": {"tool": tool, "error": third_error[-4000:], "attempts": attempts}}
            raise ProviderError(f"CBM {tool} failed: {third_error[-4000:]}")

        error = second_error or first_error or "CBM CLI invocation failed"
        self._last_diagnostics = {"tool": tool, "attempts": attempts, "error_class": "CLI_INCOMPATIBLE"}
        if tolerate:
            return {"_tool_error": {"tool": tool, "error": error[-4000:], "attempts": attempts}}
        raise ProviderError(f"CBM {tool} failed: {error[-4000:]}")

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
        baseline = repository_baseline(repo)
        if baseline["status"] == "empty_greenfield":
            return empty_greenfield_impact(repo, baseline)
        if not self.health()["available"]:
            raise ProviderError("codebase-memory-mcp is unavailable")
        if depth < 1 or depth > 5:
            raise ProviderError("CBM depth must be between 1 and 5")

        index_result: Any = None
        project = repo.name
        if refresh_index:
            index_result = self._run_tool("index_repository", {"repo_path": str(repo)})
            project = self._project_name(index_result, repo)

        # CBM's real schema accepts scope=files|impact. The Orchestrator's
        # all/staged/unstaged/branch values are intake concepts and must never be passed through.
        args: Dict[str, Any] = {"scope": "impact", "depth": depth, "project": project}
        if base_branch:
            args["base_branch"] = base_branch
        if baseline["status"] == "unborn_greenfield":
            # detect_changes requires HEAD and a base commit. Before the first commit, model
            # every product file as new instead of misreporting CBM as unavailable.
            raw = {"changed_files": baseline["product_files"], "changed_symbols": [], "impacted_symbols": []}
        else:
            raw = self._run_tool("detect_changes", args)
        normalized = normalize_detect_changes(
            raw,
            repo=repo,
            project=project,
            provider_version=self._version(),
            base_ref=base_branch,
        )
        evidence_paths = sorted(set(normalized["changes"]["changed_files"]) | set(normalized["impact"]["affected_files"]))
        if evidence_paths:
            coverage = self._run_tool(
                "check_index_coverage", {"project": project, "paths": evidence_paths[:128]},
                tolerate=True,
            )
            normalized["coverage"] = _coverage_summary(coverage)
        else:
            normalized["coverage"] = {"status": "not_applicable", "reason": "no_evidence_paths"}
        normalized["collection"] = {
            "scope": scope,
            "base_branch": base_branch,
            "depth": depth,
            "index_refreshed": refresh_index,
            "baseline_status": baseline["status"],
            "detect_changes_invoked": baseline["status"] == "ready",
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
