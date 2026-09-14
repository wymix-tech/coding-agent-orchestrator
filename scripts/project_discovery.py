#!/usr/bin/env python3
"""Conservative project discovery for Coding Agent Orchestrator v6.5.

Discovery emits evidence and recommendations. It must not turn an absent signal into a
negative architectural claim, and it never selects among multiple SDD authorities.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

import state_provider_detector
import host_runtime
import cbm_provider

EXCLUDED_DIRS = {
    ".git", ".orchestrator", "node_modules", "target", "build", "dist", ".gradle",
    ".idea", ".vscode", "vendor", ".venv", "venv", "__pycache__",
}


def _read_text(path: Path, limit: int = 2_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def _bounded_files(root: Path, suffixes: set[str] | None = None, max_depth: int = 7, limit: int = 4000) -> list[Path]:
    result: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            rel = current.relative_to(root)
        except ValueError:
            continue
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".cache")]
        if len(rel.parts) >= max_depth:
            dirnames[:] = []
        for name in filenames:
            p = current / name
            if suffixes and p.suffix.lower() not in suffixes:
                continue
            result.append(p)
            if len(result) >= limit:
                return result
    return result


def _rel(root: Path, paths: Iterable[Path]) -> list[str]:
    out = []
    for p in paths:
        try:
            out.append(p.resolve().relative_to(root.resolve()).as_posix())
        except Exception:
            out.append(str(p))
    return sorted(set(out))


def _git_info(root: Path) -> dict[str, Any]:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        return {"detected": True, "root": top, "branch": branch or None}
    except Exception:
        return {"detected": False, "root": None, "branch": None}


def _detect_technology(root: Path) -> dict[str, Any]:
    evidence: list[str] = []
    languages: list[str] = []
    build_tools: list[str] = []
    frameworks: list[str] = []
    capabilities: dict[str, Any] = {}

    pom = root / "pom.xml"
    gradle_files = [p for p in [root / "build.gradle", root / "build.gradle.kts"] if p.exists()]
    package_json = root / "package.json"
    pyproject = root / "pyproject.toml"
    go_mod = root / "go.mod"
    cargo = root / "Cargo.toml"

    java_files = _bounded_files(root, {".java"}, max_depth=8, limit=200)
    if pom.exists() or gradle_files or java_files:
        languages.append("java")
        evidence.extend(_rel(root, ([pom] if pom.exists() else []) + gradle_files + java_files[:3]))
    if pom.exists():
        build_tools.append("maven")
    if gradle_files:
        build_tools.append("gradle")

    build_text = "\n".join(_read_text(p) for p in ([pom] if pom.exists() else []) + gradle_files)
    lower_build = build_text.lower()
    if "spring-boot" in lower_build or "org.springframework.boot" in lower_build:
        frameworks.append("spring-boot")
        capabilities["spring_boot"] = True
        if "spring-boot-starter-web" in lower_build or "spring-web" in lower_build:
            capabilities["rest"] = True
        if "spring-data-jpa" in lower_build or "hibernate" in lower_build:
            capabilities["persistence"] = "jpa"
        if "spring-kafka" in lower_build:
            capabilities["messaging"] = "kafka"
        if "spring-security" in lower_build:
            capabilities["security"] = "spring-security"

    if package_json.exists():
        languages.append("javascript/typescript")
        build_tools.append("npm-compatible")
        evidence.append("package.json")
    if pyproject.exists() or (root / "requirements.txt").exists():
        languages.append("python")
        build_tools.append("python")
        evidence.extend(_rel(root, [p for p in [pyproject, root / "requirements.txt"] if p.exists()]))
    if go_mod.exists():
        languages.append("go"); build_tools.append("go"); evidence.append("go.mod")
    if cargo.exists():
        languages.append("rust"); build_tools.append("cargo"); evidence.append("Cargo.toml")

    if not languages:
        counts: dict[str, int] = {}
        mapping = {".java":"java", ".kt":"kotlin", ".py":"python", ".go":"go", ".rs":"rust", ".ts":"typescript", ".js":"javascript"}
        for p in _bounded_files(root, set(mapping), max_depth=5, limit=500):
            lang = mapping.get(p.suffix.lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
        languages = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {
        "languages": sorted(set(languages)),
        "build_tools": sorted(set(build_tools)),
        "frameworks": sorted(set(frameworks)),
        "capabilities": capabilities,
        "evidence": sorted(set(evidence))[:20],
    }


def _detect_architecture(root: Path, tech: dict[str, Any]) -> dict[str, Any]:
    java_files = _bounded_files(root, {".java"}, max_depth=9, limit=2500)
    rels = _rel(root, java_files)
    layers = {"web": [], "service": [], "dao": []}
    clean_markers: list[str] = []
    for rel in rels:
        low = "/" + rel.lower().replace("\\", "/") + "/"
        if "/controller/" in low or "/web/" in low:
            layers["web"].append(rel)
        if "/service/" in low:
            layers["service"].append(rel)
        if "/dao/" in low or "/repository/" in low:
            layers["dao"].append(rel)
        if any(token in low for token in ["/domain/", "/application/", "/infrastructure/", "/adapter/", "/adapters/"]):
            clean_markers.append(rel)

    present = [name for name, values in layers.items() if values]
    spring = "spring-boot" in (tech.get("frameworks") or [])
    if clean_markers and len(set(p.split("/")[0] for p in clean_markers)) >= 1:
        # Presence of domain/application/infrastructure-style packages is a strong reason not to
        # auto-apply the classic web/service/dao policy even if Spring Boot is used.
        return {
            "style": "non_classic_layered_candidate",
            "confidence": "medium",
            "auto_enable_policy_pack": None,
            "candidate_policy_packs": ["spring-boot-layered"] if spring else [],
            "evidence": clean_markers[:12],
            "reason": "clean/hexagonal/DDD-style package markers detected; classic Spring layered policy is not auto-enabled",
        }
    if spring and len(present) == 3:
        return {
            "style": "spring_boot_layered",
            "confidence": "high",
            "auto_enable_policy_pack": "spring-boot-layered",
            "candidate_policy_packs": [],
            "layers_detected": present,
            "evidence": sum([layers[k][:4] for k in ["web","service","dao"]], []),
            "reason": "Spring Boot plus web/service/dao-or-repository package structure detected",
        }
    if spring and len(present) >= 1:
        return {
            "style": "spring_boot_unknown_architecture",
            "confidence": "medium" if len(present) == 2 else "low",
            "auto_enable_policy_pack": None,
            "candidate_policy_packs": ["spring-boot-layered"],
            "layers_detected": present,
            "evidence": sum([layers[k][:4] for k in present], []),
            "reason": "Spring Boot detected, but repository structure is insufficient to prove classic layered architecture",
        }
    return {
        "style": "unknown",
        "confidence": "unknown",
        "auto_enable_policy_pack": None,
        "candidate_policy_packs": [],
        "layers_detected": present,
        "evidence": [],
        "reason": "architecture style cannot be proven from repository structure",
    }


def _detect_hosts(root: Path) -> dict[str, Any]:
    defs = {
        "claude-code": {"marker": root / ".claude", "binaries": ["claude"]},
        "codex": {"marker": root / ".codex", "binaries": ["codex"]},
        "pi": {"marker": root / ".pi", "binaries": ["pi"]},
    }
    detected: list[dict[str, Any]] = []
    for name, d in defs.items():
        evidence = []
        marker = d["marker"]
        if marker.exists():
            evidence.append(marker.relative_to(root).as_posix())
        binary = next((shutil.which(x) for x in d["binaries"] if shutil.which(x)), None)
        if binary:
            evidence.append(binary)
        if evidence:
            detected.append({"host": name, "confidence": "high" if marker.exists() else "medium", "evidence": evidence})
    return {
        "detected": detected,
        "names": [x["host"] for x in detected],
        "current_agent": host_runtime.detect_current_host(),
    }


def _detect_cbm() -> dict[str, Any]:
    provider = cbm_provider.CBMProvider("codebase-memory-mcp")
    health = provider.health()
    return {
        "provider": "codebase-memory-mcp",
        "available": bool(health.get("available")),
        "binary": health.get("binary"),
        "binary_candidates": health.get("binary_candidates") or [],
        "version": health.get("version"),
        "authority": "structural_evidence_only",
    }


def discover(root: Path) -> dict[str, Any]:
    root = root.resolve()
    tech = _detect_technology(root)
    sdd = state_provider_detector.detect(root)
    architecture = _detect_architecture(root, tech)
    orch = root / ".orchestrator"
    ambiguities: list[dict[str, Any]] = []
    if sdd.get("requires_authority_resolution"):
        ambiguities.append({
            "code": "MULTIPLE_SDD_AUTHORITIES",
            "detail": "multiple SDD/state providers were detected; select one explicitly",
            "candidates": [x.get("provider") for x in sdd.get("candidates", [])],
        })
    return {
        "schema_version": 1,
        "repo": str(root),
        "git": _git_info(root),
        "technology": tech,
        "architecture": architecture,
        "sdd": sdd,
        "hosts": _detect_hosts(root),
        "code_intelligence": _detect_cbm(),
        "orchestrator": {
            "initialized": (orch / "config.yaml").exists(),
            "directory_exists": orch.exists(),
            "config": str(orch / "config.yaml"),
        },
        "ambiguities": ambiguities,
        "status": "ACTION_REQUIRED" if ambiguities else "DISCOVERED",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", default=".")
    args = p.parse_args(argv)
    result = discover(Path(args.repo))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "ACTION_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
