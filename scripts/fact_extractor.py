#!/usr/bin/env python3
"""Evidence-backed Work Facts draft extractor for Adaptive SDD Coding Orchestrator.

This collector is deliberately conservative: it auto-sets only mechanically defensible
facts. Pattern matches that merely suggest a semantic fact are emitted as hints and leave
the fact unresolved (null). Absence of a signal never becomes a false fact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MANIFESTS = (
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "package.json", "pyproject.toml", "go.mod", "Cargo.toml", "Gemfile", "composer.json",
)
DEPLOYABLE_MARKERS = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Chart.yaml")
TEST_PARTS = {"test", "tests", "spec", "specs", "__tests__", "e2e", "integration-test", "integration-tests"}
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
PUBLIC_CONTRACT_RE = re.compile(
    r"(^|/)(openapi|swagger)([^/]*)\.(ya?ml|json)$|\.proto$|(^|/)(schema\.graphqls?|graphql/schema[^/]*)$",
    re.IGNORECASE,
)
MIGRATION_PATH_RE = re.compile(r"(^|/)(migrations?|db/migration|liquibase|flyway)(/|$)", re.IGNORECASE)
INFRA_PATH_RE = re.compile(r"(^|/)(terraform|infra|k8s|kubernetes|helm)(/|$)|\.tf$", re.IGNORECASE)
PROD_INFRA_RE = re.compile(r"(^|/)(prod|production)(/|$)", re.IGNORECASE)
AUTH_HINT_RE = re.compile(r"(^|/|[_-])(auth|oauth|oidc|sso|token|session|jwt|permission|rbac)(/|[_-]|\.)", re.IGNORECASE)
SECURITY_HINT_RE = re.compile(r"(^|/|[_-])(security|crypto|secret|credential|certificate|keystore|truststore)(/|[_-]|\.)", re.IGNORECASE)
PAYMENT_HINT_RE = re.compile(r"(^|/|[_-])(payment|billing|invoice|settlement|balance|ledger|pricing)(/|[_-]|\.)", re.IGNORECASE)
ASYNC_HINT_RE = re.compile(r"(^|/|[_-])(queue|kafka|rabbit|retry|scheduler|async|event|outbox)(/|[_-]|\.)", re.IGNORECASE)
PERF_HINT_RE = re.compile(r"(^|/|[_-])(perf|performance|load|benchmark)(/|[_-]|\.)", re.IGNORECASE)
DESTRUCTIVE_SQL_RE = re.compile(r"\b(DROP\s+(TABLE|COLUMN|DATABASE)|TRUNCATE\s+TABLE|DELETE\s+FROM)\b", re.IGNORECASE)


def run(cmd: Sequence[str], cwd: Path) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def git_root(repo: Path) -> Path:
    code, out = run(["git", "rev-parse", "--show-toplevel"], repo)
    return Path(out).resolve() if code == 0 and out else repo.resolve()


def rel(root: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return p.as_posix()


def file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def empty_facts() -> Dict[str, Any]:
    return {
        "ambiguity": {
            "goal_explicit": None,
            "acceptance_criteria_explicit": None,
            "boundaries_explicit": None,
            "conflicting_requirements": None,
            "multiple_observable_interpretations": None,
            "unresolved_external_contract": None,
        },
        "complexity": {
            "predicted_components": None,
            "architecture_decision_required": None,
            "concurrency_or_transaction": None,
            "distributed_coordination": None,
            "new_state_machine": None,
        },
        "scope": {
            "files_estimate": None,
            "modules_touched": None,
            "deployable_units": None,
            "external_consumers": None,
            "cross_team_contract": None,
            "public_contract_change": None,
        },
        "risk": {
            "user_visible_failure": None,
            "persistent_data_change": None,
            "security_sensitive": None,
            "authn_authz": None,
            "cryptography_or_secrets": None,
            "payment_or_financial": None,
            "destructive_migration": None,
            "compliance_or_privacy": None,
            "production_infra": None,
            "irreversible_or_hard_to_recover": None,
        },
        "novelty": {
            "exact_repo_precedent": None,
            "new_external_dependency_or_protocol": None,
            "first_repo_use": None,
            "unproven_architecture_assumption": None,
            "docs_or_examples_missing": None,
        },
        "verification": {
            "deterministic_local": None,
            "requires_integration_boundary": None,
            "async_retry_timing": None,
            "compatibility_or_migration": None,
            "security_properties": None,
            "concurrency_or_distributed_faults": None,
            "performance_or_load": None,
            "special_harness_required": None,
        },
        "policy": {"minimum_flow": None, "fixed_flow": None},
        "provenance": {},
    }


def set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def add_provenance(
    facts: Dict[str, Any], path: str, value: Any, *, source_type: str, source: str,
    evidence: Any, strength: str = "observed", detector: str = "fact_extractor",
) -> None:
    set_path(facts, path, value)
    facts.setdefault("provenance", {}).setdefault(path, []).append({
        "value": value,
        "source_type": source_type,
        "source": source,
        "evidence": evidence,
        "strength": strength,
        "detector": detector,
    })


def collect_changed_files(root: Path, base_ref: Optional[str]) -> Tuple[List[str], Dict[str, Any]]:
    commands: List[Sequence[str]] = []
    if base_ref:
        commands.append(["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...HEAD"])
    commands.extend([
        ["git", "diff", "--name-only", "--diff-filter=ACMR"],
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ])
    files: List[str] = []
    traces: List[Dict[str, Any]] = []
    for cmd in commands:
        code, out = run(cmd, root)
        traces.append({"command": " ".join(cmd), "exit_code": code})
        if code == 0 and out:
            files.extend(x.strip().replace("\\", "/") for x in out.splitlines() if x.strip())
    return sorted(set(files)), {"commands": traces, "base_ref": base_ref}


def is_test_or_doc(path: str) -> bool:
    p = Path(path)
    parts = {x.lower() for x in p.parts}
    if parts & TEST_PARTS:
        return True
    if p.suffix.lower() in DOC_SUFFIXES:
        return True
    low = p.name.lower()
    return low.startswith("test_") or low.endswith("_test.py") or low.endswith("test.java") or low.endswith("tests.cs")


def nearest_manifest_root(root: Path, file_path: str) -> Optional[str]:
    p = (root / file_path).resolve()
    cur = p.parent
    rr = root.resolve()
    while True:
        if any((cur / m).exists() for m in MANIFESTS):
            return rel(rr, cur) or "."
        if cur == rr or rr not in cur.parents:
            return None
        cur = cur.parent


def nearest_deployable_root(root: Path, file_path: str) -> Optional[str]:
    p = (root / file_path).resolve()
    cur = p.parent
    rr = root.resolve()
    while True:
        if any((cur / m).exists() for m in DEPLOYABLE_MARKERS):
            return rel(rr, cur) or "."
        # Helm charts use Chart.yaml under chart root.
        if (cur / "Chart.yaml").exists():
            return rel(rr, cur) or "."
        if cur == rr or rr not in cur.parents:
            return None
        cur = cur.parent


def diff_text(root: Path, base_ref: Optional[str]) -> str:
    chunks: List[str] = []
    if base_ref:
        code, out = run(["git", "diff", "--unified=0", f"{base_ref}...HEAD"], root)
        if code == 0 and out:
            chunks.append(out)
    for cmd in (["git", "diff", "--unified=0"], ["git", "diff", "--cached", "--unified=0"]):
        code, out = run(cmd, root)
        if code == 0 and out:
            chunks.append(out)
    return "\n".join(chunks)


def detect_sdd(root: Path, changed_files: Optional[List[str]] = None) -> Dict[str, Any]:
    markers = {
        "openspec": ["openspec", ".openspec"],
        "bmad": ["_bmad", "_bmad-output", ".bmad", "bmad"],
    }
    found: Dict[str, List[str]] = {}
    for kind, candidates in markers.items():
        hits = [c for c in candidates if (root / c).exists()]
        if hits:
            found[kind] = hits

    artifacts: Dict[str, Any] = {"openspec": [], "bmad": []}
    openspec_changes = root / "openspec" / "changes"
    if openspec_changes.exists():
        for d in sorted(x for x in openspec_changes.iterdir() if x.is_dir())[:100]:
            if d.name.lower() in {"archive", "archived"}:
                continue
            files = []
            try:
                files = sorted(rel(root, x) for x in d.rglob("*") if x.is_file())[:80]
            except OSError:
                pass
            artifacts["openspec"].append({"change_id": d.name, "path": rel(root, d), "files": files})

    bmad_roots = [root / x for x in ("_bmad-output", "_bmad", ".bmad", "bmad") if (root / x).exists()]
    bmad_name_rx = re.compile(r"(story|epic|prd|architecture|sprint|brief|requirement)", re.I)
    bmad_seen = set()
    for br in bmad_roots:
        try:
            for f in br.rglob("*"):
                if not f.is_file() or not bmad_name_rx.search(f.name):
                    continue
                rp = rel(root, f)
                if rp not in bmad_seen:
                    bmad_seen.add(rp)
                    artifacts["bmad"].append(rp)
                if len(artifacts["bmad"]) >= 150:
                    break
        except OSError:
            pass

    changed_files = changed_files or []
    changed_sdd = [
        f for f in changed_files
        if f.startswith("openspec/") or f.startswith(".openspec/")
        or f.startswith("_bmad/") or f.startswith("_bmad-output/")
        or f.startswith(".bmad/") or f.startswith("bmad/")
    ]
    return {
        "detected": found,
        "multiple_sdd_markers": len(found) > 1,
        "artifacts": artifacts,
        "changed_sdd_artifacts": changed_sdd,
    }


def collect_instruction_files(root: Path) -> List[str]:
    candidates = [
        "AGENTS.md", "CLAUDE.md", "GEMINI.md", "CONTRIBUTING.md", "CONTRIBUTING.rst",
        ".github/CONTRIBUTING.md", "docs/architecture.md", "docs/ARCHITECTURE.md",
    ]
    return [c for c in candidates if (root / c).exists()]


def collect_gate_signals(root: Path) -> List[Dict[str, str]]:
    candidates: List[Path] = []
    gh = root / ".github" / "workflows"
    if gh.exists():
        candidates.extend(sorted(gh.glob("*.yml")))
        candidates.extend(sorted(gh.glob("*.yaml")))
    for name in ("pom.xml", "build.gradle", "build.gradle.kts", "pyproject.toml", "package.json", ".gitlab-ci.yml", "Jenkinsfile"):
        p = root / name
        if p.exists():
            candidates.append(p)
    patterns = {
        "semgrep": re.compile(r"\bsemgrep\b", re.I),
        "mutation": re.compile(r"\b(pitest|pitest-maven|stryker|mutmut|mutation)\b", re.I),
        "coverage": re.compile(r"\b(jacoco|coverage|nyc|istanbul|cobertura)\b", re.I),
        "test": re.compile(r"\b(pytest|mvn\s+test|gradle\s+test|npm\s+test|go\s+test|cargo\s+test|junit)\b", re.I),
        "lint": re.compile(r"\b(eslint|ruff|flake8|pylint|checkstyle|spotbugs|golangci-lint)\b", re.I),
    }
    out: List[Dict[str, str]] = []
    seen = set()
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")[:500_000]
        except OSError:
            continue
        for gate, rx in patterns.items():
            if rx.search(text):
                key = (gate, rel(root, p))
                if key not in seen:
                    seen.add(key)
                    out.append({"gate": gate, "source": key[1]})
    return out


def hint(path: str, files: List[str], rx: re.Pattern[str], reason: str) -> Optional[Dict[str, Any]]:
    hits = [f for f in files if rx.search(f)]
    if not hits:
        return None
    return {"fact": path, "suggested_value": True, "reason": reason, "evidence": hits, "strength": "heuristic"}


def request_hints(request: str) -> List[Dict[str, Any]]:
    if not request.strip():
        return []
    hints: List[Dict[str, Any]] = []
    if re.search(r"\b(given|when|then|acceptance criteria|must|should)\b|验收|必须|应当|当.+时", request, re.I):
        hints.append({
            "fact": "ambiguity.acceptance_criteria_explicit",
            "suggested_value": True,
            "reason": "request contains acceptance/behavioral language; inspect semantics before accepting",
            "evidence": request[:500],
            "strength": "heuristic",
        })
    if len(request.strip()) < 20 or re.search(r"优化|改进|better|improve|enhance", request, re.I):
        hints.append({
            "fact": "ambiguity.goal_explicit",
            "suggested_value": False,
            "reason": "request may be thematic rather than observable; semantic confirmation required",
            "evidence": request[:500],
            "strength": "heuristic",
        })
    return hints


def extract(repo: Path, request: str = "", base_ref: Optional[str] = None) -> Dict[str, Any]:
    root = git_root(repo)
    facts = empty_facts()
    changed, git_trace = collect_changed_files(root, base_ref)
    production = [f for f in changed if not is_test_or_doc(f)]
    modules = sorted({m for f in production if (m := nearest_manifest_root(root, f)) is not None})
    deployables = sorted({m for f in production if (m := nearest_deployable_root(root, f)) is not None})
    dtext = diff_text(root, base_ref)
    sdd = detect_sdd(root, changed)
    gates = collect_gate_signals(root)
    instruction_files = collect_instruction_files(root)

    # Structural facts are auto-filled only when a concrete change set exists.
    if changed:
        add_provenance(
            facts, "scope.files_estimate", len(production), source_type="git",
            source="working-tree/base diff", evidence=production, strength="observed", detector="changed_files",
        )
        if production and len(modules) == len({nearest_manifest_root(root, f) for f in production} - {None}):
            # Count discovered module roots; root-level files without a manifest make the value unresolved.
            unresolved = [f for f in production if nearest_manifest_root(root, f) is None]
            if not unresolved:
                add_provenance(
                    facts, "scope.modules_touched", len(modules), source_type="repository_structure",
                    source="nearest build/package manifest", evidence=modules, strength="derived", detector="module_roots",
                )
        if production:
            unresolved_deployable = [f for f in production if nearest_deployable_root(root, f) is None]
            if not unresolved_deployable:
                add_provenance(
                    facts, "scope.deployable_units", len(deployables), source_type="repository_structure",
                    source="nearest deployable marker", evidence=deployables, strength="derived", detector="deployable_roots",
                )

    contract_files = [f for f in production if PUBLIC_CONTRACT_RE.search(f)]
    if contract_files:
        add_provenance(
            facts, "scope.public_contract_change", True, source_type="git",
            source="changed public contract artifact", evidence=contract_files, strength="observed", detector="public_contract_files",
        )
        add_provenance(
            facts, "verification.compatibility_or_migration", True, source_type="git",
            source="changed public contract artifact", evidence=contract_files, strength="derived", detector="public_contract_files",
        )

    migration_files = [f for f in production if MIGRATION_PATH_RE.search(f)]
    if migration_files:
        add_provenance(
            facts, "risk.persistent_data_change", True, source_type="git",
            source="changed migration artifact", evidence=migration_files, strength="derived", detector="migration_files",
        )
        add_provenance(
            facts, "verification.compatibility_or_migration", True, source_type="git",
            source="changed migration artifact", evidence=migration_files, strength="derived", detector="migration_files",
        )
    migration_text_chunks = [dtext]
    for mf in migration_files:
        try:
            migration_text_chunks.append((root / mf).read_text(encoding="utf-8", errors="ignore")[:500_000])
        except OSError:
            pass
    migration_text = "\n".join(migration_text_chunks)
    destructive_matches = sorted({m.group(0) for m in DESTRUCTIVE_SQL_RE.finditer(migration_text)})
    if destructive_matches and migration_files:
        add_provenance(
            facts, "risk.destructive_migration", True, source_type="git_diff",
            source="destructive SQL in migration diff", evidence=destructive_matches, strength="observed", detector="destructive_sql",
        )
        add_provenance(
            facts, "risk.irreversible_or_hard_to_recover", True, source_type="git_diff",
            source="destructive migration operation", evidence=destructive_matches, strength="derived", detector="destructive_sql",
        )

    prod_infra_files = [f for f in production if INFRA_PATH_RE.search(f) and PROD_INFRA_RE.search(f)]
    if prod_infra_files:
        add_provenance(
            facts, "risk.production_infra", True, source_type="git",
            source="changed production infrastructure artifact", evidence=prod_infra_files, strength="derived", detector="production_infra_files",
        )

    suggestions = [x for x in (
        hint("risk.authn_authz", production, AUTH_HINT_RE, "auth/token/session path signal"),
        hint("risk.security_sensitive", production, SECURITY_HINT_RE, "security/credential path signal"),
        hint("risk.payment_or_financial", production, PAYMENT_HINT_RE, "payment/financial path signal"),
        hint("verification.async_retry_timing", production, ASYNC_HINT_RE, "async/queue/retry path signal"),
        hint("verification.performance_or_load", production, PERF_HINT_RE, "performance/load path signal"),
    ) if x]
    dependency_manifest_changes = [f for f in changed if Path(f).name in MANIFESTS]
    if dependency_manifest_changes:
        suggestions.append({
            "fact": "novelty.new_external_dependency_or_protocol",
            "suggested_value": True,
            "reason": "dependency manifest changed; inspect diff to determine whether a new runtime dependency/protocol was introduced",
            "evidence": dependency_manifest_changes,
            "strength": "heuristic",
        })
    suggestions.extend(request_hints(request))

    code, head = run(["git", "rev-parse", "HEAD"], root)
    head = head if code == 0 else None
    observations = {
        "repo_root": str(root),
        "git_head": head,
        "git": git_trace,
        "changed_files": changed,
        "changed_file_hashes": {f: file_sha256(root / f) for f in changed},
        "production_files": production,
        "module_roots": modules,
        "deployable_roots": deployables,
        "public_contract_files": contract_files,
        "migration_files": migration_files,
        "production_infra_files": prod_infra_files,
        "sdd": sdd,
        "quality_gate_signals": gates,
        "instruction_files": instruction_files,
        "dependency_manifests_changed": [f for f in changed if Path(f).name in MANIFESTS],
        "request_present": bool(request.strip()),
        "request_hash": hashlib.sha256(request.encode("utf-8")).hexdigest() if request else None,
    }
    unresolved = []
    for section, values in facts.items():
        if section in {"policy", "provenance"} or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if value is None:
                unresolved.append(f"{section}.{key}")

    changed_hashes = {f: file_sha256(root / f) for f in changed}
    digest_source = json.dumps(
        {"head": head, "changed": changed, "changed_hashes": changed_hashes, "request": request, "base_ref": base_ref},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    snapshot_id = hashlib.sha256(digest_source).hexdigest()[:16]
    facts["extraction"] = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "collector": "fact_extractor.py",
        "mode": "conservative",
        "principle": "positive evidence may set facts; absence of signal never proves false",
        "observations": observations,
        "suggestions": suggestions,
        "resolution_queue": unresolved,
    }
    return facts


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--request", default="")
    p.add_argument("--request-file", type=Path)
    p.add_argument("--base-ref")
    p.add_argument("--output", type=Path)
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args(argv)
    request = args.request
    if args.request_file:
        request = args.request_file.read_text(encoding="utf-8")
    result = extract(args.repo, request=request, base_ref=args.base_ref)
    text = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty or args.output else None, sort_keys=bool(args.pretty or args.output))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
