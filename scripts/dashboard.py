"""Local CAO dashboard with bounded artifact preview and hash-bound confirmation."""
from __future__ import annotations

import ipaddress
import json
import re
import sysconfig
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

from scripts.cao import locked_repo


def dashboard_html_path() -> Path:
    """Locate the dashboard page in a source checkout or installed wheel."""
    candidates = [
        Path(__file__).with_name("dashboard.html"),
        Path(sysconfig.get_path("data")) / "share" / "coding-agent-orchestrator" / "web" / "dashboard.html",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Dashboard HTML asset is missing from this CAO installation")


def timestamp_label(value: Any) -> str | None:
    """Return an ISO timestamp without changing the stored value."""
    if isinstance(value, bool):
        return None
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return str(value) if isinstance(value, str) and value else None
    if stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(timespec="seconds")


def _verification_view(work: dict[str, Any]) -> dict[str, Any] | None:
    raw = work.get("verification")
    if not isinstance(raw, dict):
        return None
    results = []
    for row in raw.get("results") or []:
        if not isinstance(row, dict):
            continue
        results.append({
            "name": row.get("name") or "verification",
            "command": row.get("command"),
            "required": bool(row.get("required", True)),
            "status": row.get("status") or "unknown",
            "exit_code": row.get("exit_code"),
            "reason": row.get("reason") or "",
            "started_at": timestamp_label(row.get("started_at")),
        })
    return {
        "status": raw.get("status") or "unknown",
        "source": raw.get("source") or "unknown",
        "at": timestamp_label(raw.get("at")),
        "results": results,
        # Dashboard intentionally avoids an expensive full-repository snapshot on every poll.
        "freshness_note": "最终快照新鲜度由 cao done 重新检查",
    }



_PREVIEW_EXTENSIONS = {
    ".md", ".mdx", ".txt", ".rst", ".adoc", ".json", ".yaml", ".yml",
    ".toml", ".xml", ".csv", ".html", ".htm", ".sql", ".py", ".ts",
    ".tsx", ".js", ".jsx", ".java", ".kt", ".go", ".rs", ".sh", ".feature",
}
_PREVIEW_MAX_BYTES = 512 * 1024
# The SDD artifact catalog is a document workbench, not a source-code browser.
# Keep this allowlist narrower than preview support: code/config files may be
# previewable elsewhere, but must never be classified as native SDD documents.
_SDD_DOCUMENT_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".txt"}


def _safe_repo_file(repo: Path, raw_path: str | None) -> tuple[Path | None, str | None]:
    """Resolve a repo-relative path and refuse paths that escape via ../ or symlinks."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None, None
    root = repo.resolve()
    try:
        resolved = (root / candidate).resolve(strict=False)
        relative = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None, None
    return resolved, relative


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:72]


def _artifact_label(path: str) -> tuple[str, str]:
    name = Path(path).name.lower()
    if name in {"proposal.md", "change.md"}:
        return "proposal", "Proposal / 变更提案"
    if name in {"spec.md", "specification.md", "spec.md"} or name == "spec.md":
        return "spec", "Spec / 需求规格"
    if name in {"design.md", "architecture.md", "architecture-decision-record.md"}:
        return "design", "Design / 架构设计"
    if name in {"plan.md", "implementation-plan.md"}:
        return "plan", "Plan / 实施计划"
    if name in {"tasks.md", "task.md"}:
        return "tasks", "Tasks / 实施任务"
    if name in {"prd.md", "product-requirements.md"}:
        return "prd", "PRD / 产品需求"
    if name in {"epics.md", "epic.md"} or name.startswith("epic-"):
        return "epics", "Epics / 史诗拆分"
    if "story" in name:
        return "story", "Story / 用户故事"
    if name in {"research.md", "data-model.md", "quickstart.md", "constitution.md"}:
        return Path(name).stem.replace("-", " ").title(), Path(name).stem.replace("-", " ").title()
    return "other", Path(path).name


def build_sdd_artifact_catalog(repo: Path, work: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe expected and discovered native artifacts without copying their contents."""
    from scripts.cao import sha256_file

    repo = repo.resolve()
    sdd = work.get("sdd") if isinstance(work.get("sdd"), dict) else {}
    provider = str(sdd.get("provider") or "none").lower()
    native_ref = str(sdd.get("native_ref") or "").strip()
    registered = [item for item in (sdd.get("artifacts") or []) if isinstance(item, dict) and item.get("path")]
    expected: list[dict[str, Any]] = []
    discovered: dict[str, dict[str, Any]] = {}

    def add_expected(role: str, title: str, path: str | None, location_hint: str = "") -> None:
        expected.append({"role": role, "title": title, "path": path, "location_hint": location_hint})

    def add_discovered(path_value: str, role: str | None = None, title: str | None = None, source: str = "provider") -> None:
        safe, relative = _safe_repo_file(repo, path_value)
        if safe is None or relative is None or not safe.is_file():
            return
        # A file being registered or located beneath a provider directory does
        # not make it an SDD artifact. Exclude implementation source, tests,
        # configuration, and other non-document files for every provider,
        # including explicit native_ref and `cao artifact add` references.
        if safe.suffix.lower() not in _SDD_DOCUMENT_EXTENSIONS:
            return
        inferred_role, inferred_title = _artifact_label(relative)
        discovered[relative] = {
            "role": role or inferred_role,
            "title": title or inferred_title,
            "path": relative,
            "source": source,
        }

    # Use the explicit native_ref first. If it names a directory, it anchors this
    # Work Item's artifact set. A symbolic native_ref is interpreted conservatively.
    native_path, native_relative = _safe_repo_file(repo, native_ref) if native_ref else (None, None)
    native_is_file = bool(native_path and native_path.is_file())
    native_is_dir = bool(native_path and native_path.is_dir())
    if native_is_file and native_relative:
        add_discovered(native_relative, source="native_ref")

    root: Path | None = native_path if native_is_dir else (native_path.parent if native_is_file else None)
    root_rel: str | None = None
    if root:
        try:
            root_rel = root.resolve().relative_to(repo).as_posix()
        except ValueError:
            root = None

    def choose_feature_root(parent: Path, known_prefix: str) -> tuple[Path | None, str | None]:
        if root and root.is_dir():
            return root, root_rel
        if native_ref:
            candidates = [native_ref]
            if provider == "openspec":
                if native_ref.startswith("changes/"):
                    candidates.insert(0, "openspec/" + native_ref)
                elif not native_ref.startswith("openspec/") and "/" not in native_ref:
                    candidates.append("openspec/changes/" + native_ref)
            elif provider == "speckit":
                if native_ref.startswith("specs/"):
                    parts = Path(native_ref).parts
                    if len(parts) >= 2:
                        candidates.insert(0, "/".join(parts[:2]))
                elif "/" not in native_ref:
                    candidates.append("specs/" + native_ref)
            for item in candidates:
                safe, relpath = _safe_repo_file(repo, item)
                if safe and safe.is_dir() and relpath:
                    return safe, relpath
        parent_path = repo / parent
        if parent_path.is_dir():
            dirs = [p for p in parent_path.iterdir() if p.is_dir() and not p.name.startswith(".")]
            identity = _slug(str(work.get("id") or ""))
            title_slug = _slug(str(work.get("title") or ""))
            ranked = [d for d in dirs if d.name.lower() in {identity, title_slug} or identity.startswith(d.name.lower() + "-") or title_slug.startswith(d.name.lower() + "-")]
            if len(ranked) == 1:
                return ranked[0], ranked[0].relative_to(repo).as_posix()
            if len(dirs) == 1:
                return dirs[0], dirs[0].relative_to(repo).as_posix()
        return None, None

    if provider == "openspec":
        feature_root, feature_rel = choose_feature_root(Path("openspec/changes"), "openspec/changes")
        if feature_rel:
            add_expected("proposal", "Proposal / 变更提案", f"{feature_rel}/proposal.md")
            add_expected("design", "Design / 设计方案", f"{feature_rel}/design.md")
            add_expected("tasks", "Tasks / 实施任务", f"{feature_rel}/tasks.md")
            spec_dir = feature_root / "specs" if feature_root else None
            spec_files = sorted(spec_dir.rglob("*.md")) if spec_dir and spec_dir.is_dir() else []
            if spec_files:
                for file in spec_files:
                    add_discovered(file.relative_to(repo).as_posix(), "spec", f"Spec / {file.stem}")
            else:
                add_expected("spec", "Spec / 需求规格", f"{feature_rel}/specs/", "生成后显示 change 下的规格文件")
        else:
            # A Work Item may not have native_ref yet, and OpenSpec repositories can
            # contain archived/older changes alongside the active change. Do not make
            # discovery depend on there being exactly one directory: surface real
            # native artifacts from change folders until an explicit anchor is known.
            changes_root = repo / "openspec/changes"
            candidates = sorted(
                (p for p in changes_root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ) if changes_root.is_dir() else []
            identity = _slug(str(work.get("id") or ""))
            title_slug = _slug(str(work.get("title") or ""))
            matched = [p for p in candidates if p.name.lower() in {identity, title_slug} or identity.startswith(p.name.lower() + "-") or title_slug.startswith(p.name.lower() + "-")]
            scan_roots = matched or candidates
            for change_dir in scan_roots:
                for file in sorted(change_dir.rglob("*.md")):
                    add_discovered(file.relative_to(repo).as_posix(), source="provider-discovery")
            if not discovered:
                add_expected("proposal", "Proposal / 变更提案", None, "未定位到 OpenSpec change；生成文件后会自动扫描")
                add_expected("spec", "Spec / 需求规格", None, "未定位到 OpenSpec change；生成文件后会自动扫描")
                add_expected("design", "Design / 设计方案", None, "未定位到 OpenSpec change；生成文件后会自动扫描")
                add_expected("tasks", "Tasks / 实施任务", None, "未定位到 OpenSpec change；生成文件后会自动扫描")
    elif provider == "speckit":
        feature_root, feature_rel = choose_feature_root(Path("specs"), "specs")
        # Spec Kit commonly stores spec.md, plan.md and tasks.md under specs/<feature>/.
        if feature_rel:
            for name, role, title in (
                ("spec.md", "spec", "Spec / 需求规格"),
                ("plan.md", "design", "Plan / 技术计划"),
                ("tasks.md", "tasks", "Tasks / 实施任务"),
            ):
                add_expected(role, title, f"{feature_rel}/{name}")
            for file in sorted(feature_root.rglob("*.md")) if feature_root else []:
                add_discovered(file.relative_to(repo).as_posix())
        else:
            # As with other native SDDs, discover real files even when CAO has not
            # yet persisted a feature-specific native_ref or several feature folders exist.
            specs_root = repo / "specs"
            feature_dirs = sorted(
                (p for p in specs_root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ) if specs_root.is_dir() else []
            identity = _slug(str(work.get("id") or ""))
            title_slug = _slug(str(work.get("title") or ""))
            matched = [p for p in feature_dirs if p.name.lower() in {identity, title_slug} or identity.startswith(p.name.lower() + "-") or title_slug.startswith(p.name.lower() + "-")]
            for feature_dir in (matched or feature_dirs):
                for file in sorted(feature_dir.rglob("*.md")):
                    add_discovered(file.relative_to(repo).as_posix(), source="provider-discovery")
            if not discovered:
                add_expected("spec", "Spec / 需求规格", None, "未定位到 Spec Kit feature；生成文件后会自动扫描")
                add_expected("design", "Plan / 技术计划", None, "未定位到 Spec Kit feature；生成文件后会自动扫描")
                add_expected("tasks", "Tasks / 实施任务", None, "未定位到 Spec Kit feature；生成文件后会自动扫描")
        constitution = repo / ".specify/memory/constitution.md"
        if constitution.is_file():
            add_discovered(constitution.relative_to(repo).as_posix(), "constitution", "Constitution / 项目原则")
        elif (repo / ".specify/memory").is_dir():
            add_expected("constitution", "Constitution / 项目原则", ".specify/memory/constitution.md")
    elif provider == "bmad":
        planning = repo / "_bmad-output/planning-artifacts"
        known = [
            ("prd.md", "prd", "PRD / 产品需求", ["prd.md", "PRD.md", "product-requirements.md"]),
            ("architecture.md", "design", "Architecture / 架构设计", ["architecture.md", "ARCHITECTURE.md"]),
            ("epics.md", "epics", "Epics / 史诗拆分", ["epics.md", "epic.md"]),
        ]
        bmad_root = repo / "_bmad-output"
        all_docs = sorted(bmad_root.rglob("*.md")) if bmad_root.is_dir() else []
        for default_name, role, title, aliases in known:
            match = next((p for p in all_docs if p.name in aliases), None)
            expected_path = match.relative_to(repo).as_posix() if match else f"_bmad-output/planning-artifacts/{default_name}"
            add_expected(role, title, expected_path)
        story_files = [p for p in all_docs if "story" in p.name.lower() or "implementation-artifact" in str(p.parent).lower()]
        if story_files:
            for file in story_files:
                add_discovered(file.relative_to(repo).as_posix(), "story", f"Story / {file.stem}")
        else:
            add_expected("story", "Stories / 实施故事", "_bmad-output/implementation-artifacts/", "由 BMAD 原生工作流创建后自动发现")
        # BMAD's native_ref can point to an individual story/spec outside the usual output tree.
        if native_is_file and native_relative:
            add_discovered(native_relative, "spec", "Native Spec / 原生规格", "native_ref")
        for file in all_docs:
            add_discovered(file.relative_to(repo).as_posix())
    else:
        # Unknown/custom providers remain useful: show all registered refs and any explicit native file.
        if not registered and not native_is_file:
            add_expected("spec", "Spec / 原生规格", None, "此 Provider 尚无可识别的工件目录约定")

    # Always include refs explicitly registered through `cao artifact add`, including custom SDD files.
    for ref in registered:
        add_discovered(str(ref.get("path")), str(ref.get("role") or _artifact_label(str(ref.get("path")))[0]), None, "reference")

    # Existing files at expected provider paths are discovered as generated artifacts.
    # Directory placeholders (such as OpenSpec specs/) are represented by their children instead.
    for item in expected:
        raw_path = item.get("path")
        safe, relative = _safe_repo_file(repo, raw_path) if raw_path else (None, None)
        if safe and relative and safe.is_file():
            add_discovered(relative, str(item.get("role") or "other"), str(item.get("title") or relative), "provider")

    # Turn expected entries into rows, but avoid duplicate placeholders when a matching file exists.
    rows_by_path: dict[str, dict[str, Any]] = {}
    rows_without_path: list[dict[str, Any]] = []
    for item in expected:
        path_value = item.get("path")
        if path_value and path_value in discovered:
            continue
        if any(found.get("role") == item.get("role") for found in discovered.values()):
            continue
        row = {
            "id": f"missing:{item['role']}:{path_value or item['title']}",
            "role": item["role"], "title": item["title"], "path": path_value,
            "location_hint": item.get("location_hint") or "",
            "exists": False, "status": "not_generated", "status_label": "未生成",
            "previewable": False, "registered": False, "confirmed_at": None,
            "sha256": None, "modified_at": None, "size": None,
            "confirmation_stale": False,
        }
        if path_value:
            rows_by_path.setdefault(path_value, row)
        else:
            rows_without_path.append(row)

    refs_by_path = {str(item.get("path")): item for item in registered if item.get("path")}
    for relative, item in discovered.items():
        path, safe_relative = _safe_repo_file(repo, relative)
        if not path or not safe_relative or not path.is_file():
            continue
        ref = refs_by_path.get(safe_relative) or {}
        current_sha = sha256_file(path)
        confirmed_at = ref.get("confirmed_at")
        stored_sha = ref.get("sha256")
        confirmed = bool(confirmed_at and stored_sha == current_sha)
        stale_confirmation = bool(confirmed_at and stored_sha != current_sha)
        stat = path.stat()
        rows_by_path[safe_relative] = {
            "id": safe_relative, "role": item["role"], "title": item["title"],
            "path": safe_relative, "location_hint": "", "exists": True,
            "status": "confirmed" if confirmed else "generated",
            "status_label": "已确认" if confirmed else "已生成",
            "previewable": path.suffix.lower() in _PREVIEW_EXTENSIONS and stat.st_size <= _PREVIEW_MAX_BYTES,
            "registered": bool(ref), "confirmed_at": timestamp_label(confirmed_at),
            "confirmed_by": ref.get("confirmed_by"), "sha256": current_sha,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "size": stat.st_size, "confirmation_stale": stale_confirmation,
            "source": item.get("source", "provider"),
        }

    rows = list(rows_by_path.values()) + rows_without_path
    role_order = {"spec": 0, "proposal": 1, "prd": 2, "design": 3, "plan": 4, "tasks": 5, "epics": 6, "story": 7, "constitution": 8, "other": 9}
    rows.sort(key=lambda row: (role_order.get(row.get("role"), 9), row.get("title", "").lower(), row.get("path") or ""))
    return rows[:150]


@locked_repo
def confirm_artifact(repo: Path, work_id: str, artifact_path: str, confirmed_by: str = "dashboard") -> dict[str, Any]:
    """Record an explicit human confirmation against the current file hash."""
    from scripts.cao import load_work, mark_artifact_confirmed

    repo = repo.resolve()
    work = load_work(repo, work_id)
    if not isinstance(work, dict):
        raise ValueError("WORK_ITEM_NOT_FOUND")
    catalog = build_sdd_artifact_catalog(repo, work)
    item = next((row for row in catalog if row.get("path") == artifact_path and row.get("exists")), None)
    if not item:
        raise ValueError("ARTIFACT_NOT_DISCOVERED_OR_MISSING")
    ref = mark_artifact_confirmed(repo, work, artifact_path, confirmed_by)
    return {"ok": True, "work_item_id": work_id, "artifact": ref}


def preview_artifact(repo: Path, work_id: str, artifact_path: str) -> dict[str, Any]:
    """Return bounded text content for a discovered SDD artifact, never arbitrary paths."""
    from scripts.cao import load_work

    repo = repo.resolve()
    work = load_work(repo, work_id)
    if not isinstance(work, dict):
        raise ValueError("WORK_ITEM_NOT_FOUND")
    catalog = build_sdd_artifact_catalog(repo, work)
    item = next((row for row in catalog if row.get("path") == artifact_path and row.get("exists")), None)
    if not item:
        raise ValueError("ARTIFACT_NOT_DISCOVERED_OR_MISSING")
    path, relative = _safe_repo_file(repo, artifact_path)
    if not path or not relative or not path.is_file() or path.suffix.lower() not in _PREVIEW_EXTENSIONS:
        raise ValueError("ARTIFACT_PREVIEW_NOT_SUPPORTED")
    size = path.stat().st_size
    if size > _PREVIEW_MAX_BYTES:
        raise ValueError("ARTIFACT_TOO_LARGE_TO_PREVIEW")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "work_item_id": work_id, "path": relative, "title": item.get("title"), "role": item.get("role"), "content": content, "size": size, "sha256": item.get("sha256"), "status": item.get("status")}

def infer_execution_phase(work: dict[str, Any], artifact_catalog: list[dict[str, Any]]) -> dict[str, str]:
    """Infer a dashboard phase from common CAO signals and native SDD artifact roles.

    Explicit later CAO checkpoints win. If the stored step is stale at planning,
    the presence of native task/story artifacts is a cross-provider signal that the
    native workflow has reached implementation.
    """
    execution = work.get("execution") if isinstance(work.get("execution"), dict) else {}
    step = str(execution.get("step") or "").lower()
    if work.get("status") == "completed" or re.search(r"done|complete|closed", step):
        return {"id": "done", "source": "cao-state"}
    if re.search(r"verif|test", step):
        return {"id": "verification", "source": "cao-state"}
    if re.search(r"review|audit", step):
        return {"id": "review", "source": "cao-state"}
    if re.search(r"implement|code|build|bugfix", step):
        return {"id": "implementation", "source": "cao-state"}

    generated_roles = {
        str(item.get("role") or "").lower()
        for item in artifact_catalog
        if item.get("exists") and item.get("status") in {"generated", "confirmed"}
    }
    workflow = ((work.get("sdd") or {}).get("workflow") or {})
    if generated_roles.intersection({"tasks", "story"}) or workflow.get("status") == "completed":
        return {"id": "implementation", "source": "native-sdd-artifacts"}
    if not work.get("path_assessment"):
        return {"id": "path", "source": "cao-state"}
    return {"id": "planning", "source": "cao-state"}


@locked_repo
def build_dashboard_overview(repo: Path, stale_after_minutes: int = 30) -> dict[str, Any]:
    """Build a read-only projection from CAO's existing JSON state files."""
    from scripts.cao import (
        HANDOFF_DIR,
        SESSION_DIR,
        WORK_DIR,
        SDD_CATALOG,
        config,
        detect_sdd,
        empty_index,
        list_handoff_records,
        list_sessions,
        load_index,
        load_json,
        path_assessment_state,
        reconcile_native_sdd_state,
        rel,
        session_activity,
        work_summary,
    )

    repo = repo.resolve()
    initialized = (repo / ".orchestrator" / "config.json").is_file()
    cfg = config(repo) if initialized else {}
    index = load_index(repo) if initialized else empty_index()
    raw_works: list[dict[str, Any]] = []
    work_root = repo / WORK_DIR
    if work_root.is_dir():
        for path in work_root.glob("*.json"):
            value = load_json(path, None)
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                if value.get("status") in {"active", "suspended"} and reconcile_native_sdd_state(repo, value):
                    value = load_json(path, value)
                raw_works.append(value)

    handoffs = list_handoff_records(repo) if initialized else []
    sessions = list_sessions(repo) if initialized else []
    work_by_id = {work["id"]: work for work in raw_works}
    active_id = index.get("active_work")
    if active_id not in work_by_id or work_by_id[active_id].get("status") != "active":
        active_id = next((w["id"] for w in raw_works if w.get("status") == "active"), None)

    handoff_counts: dict[str, int] = {}
    latest_by_work: dict[str, dict[str, Any]] = {}
    for handoff in handoffs:
        wid = handoff.get("work_item")
        if not isinstance(wid, str):
            continue
        handoff_counts[wid] = handoff_counts.get(wid, 0) + 1
        latest_by_work.setdefault(wid, handoff)

    work_items: list[dict[str, Any]] = []
    for work in raw_works:
        summary = work_summary(work)
        assessment = work.get("path_assessment") if isinstance(work.get("path_assessment"), dict) else None
        try:
            assessment_state = path_assessment_state(repo, work)
        except (OSError, ValueError, KeyError):
            assessment_state = {"ready": False, "reason": "path assessment unavailable"}
        requirement = work.get("requirement") or {}
        sdd = work.get("sdd") or {}
        execution = work.get("execution") or {}
        resume = work.get("resume") or {}
        artifact_catalog = build_sdd_artifact_catalog(repo, work)
        current_phase = infer_execution_phase(work, artifact_catalog)
        work_items.append({
            "id": work["id"],
            "title": work.get("title") or work["id"],
            "status": work.get("status") or "unknown",
            "created_at": timestamp_label(work.get("created_at")),
            "updated_at": timestamp_label(work.get("updated_at")),
            "started_at": timestamp_label(work.get("started_at")),
            "requirement": {"path": requirement.get("path"), "sha256": requirement.get("sha256")},
            "sdd": {"provider": sdd.get("provider") or "none", "native_ref": sdd.get("native_ref"), "artifacts": sdd.get("artifacts") or []},
            "execution": {
                "step": execution.get("step") or "not recorded",
                "next_action": execution.get("next_action") or {},
                "current_phase": current_phase["id"],
                "phase_source": current_phase["source"],
            },
            "path_assessment": assessment,
            "path_state": assessment_state,
            "blockers": work.get("blockers") or [],
            "decisions": (work.get("decisions") or [])[-8:][::-1],
            "verification": _verification_view(work),
            "resume": {
                "summary": resume.get("summary") or "暂无进度摘要",
                "completed": resume.get("completed") or [],
                "pending": resume.get("pending") or [],
                "last_agent": resume.get("last_agent"),
                "last_checkpoint_at": timestamp_label(resume.get("last_checkpoint_at")),
            },
            "handoff_count": handoff_counts.get(work["id"], 0),
            "latest_handoff_id": (latest_by_work.get(work["id"]) or {}).get("id"),
            "artifact_count": len(sdd.get("artifacts") or []),
            "artifact_catalog": artifact_catalog,
            "decision_count": len(work.get("decisions") or []),
            "last_agent": summary.get("last_agent"),
        })

    status_order = {"active": 0, "suspended": 1, "todo": 2, "completed": 3}
    work_items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    work_items.sort(key=lambda item: status_order.get(item["status"], 4))

    session_views = []
    for session in sessions:
        item = dict(session)
        item["activity"] = session_activity(session, stale_after_minutes)
        item["started_at_label"] = timestamp_label(session.get("started_at"))
        item["last_event_at_label"] = timestamp_label(session.get("last_event_at"))
        item["ended_at_label"] = timestamp_label(session.get("ended_at"))
        work = work_by_id.get(session.get("work_item_id"))
        item["work_title"] = work.get("title") if work else None
        session_views.append(item)

    handoff_views = []
    for handoff in handoffs[:40]:
        wid = handoff.get("work_item")
        work = work_by_id.get(wid)
        handoff_views.append({
            "id": handoff.get("id"),
            "sequence": handoff.get("sequence"),
            "work_item": wid,
            "work_title": work.get("title") if work else wid,
            "host": handoff.get("host") or "unknown",
            "reason": handoff.get("reason") or "未注明原因",
            "created_at": timestamp_label(handoff.get("created_at")),
            "step": handoff.get("step"),
            "summary": handoff.get("summary") or "暂无摘要",
            "pending": handoff.get("pending") or [],
        })

    installed_sdds = []
    selected_provider = str((cfg.get("sdd") or {}).get("provider") or "auto") if initialized else "auto"
    if initialized:
        for provider, meta in SDD_CATALOG.items():
            try:
                detected = detect_sdd(repo, provider)
            except Exception:
                detected = {"installed": False}
            installed_sdds.append({"id": provider, "name": meta.get("name", provider), "installed": bool(detected.get("installed")), "selected": selected_provider == provider})

    counts = {
        "total_work": len(work_items),
        "active_work": sum(1 for w in work_items if w["status"] == "active"),
        "blocked_work": sum(1 for w in work_items if w["blockers"]),
        "completed_work": sum(1 for w in work_items if w["status"] == "completed"),
        "active_sessions": sum(1 for s in session_views if s.get("status") == "active"),
        "stale_sessions": sum(1 for s in session_views if s.get("activity") == "stale"),
        "handoffs": len(handoffs),
        "open_blockers": sum(len(w["blockers"]) for w in work_items),
    }
    return {
        "ok": True,
        "initialized": initialized,
        "repo": repo.name or str(repo),
        "repo_path": str(repo),
        "active_work": active_id,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "work_items": work_items,
        "sessions": session_views,
        "handoffs": handoff_views,
        "sdds": installed_sdds,
        "state_paths": {
            "config": ".orchestrator/config.json",
            "work": str(WORK_DIR).replace("\\", "/"),
            "sessions": str(SESSION_DIR).replace("\\", "/"),
            "handoffs": str(HANDOFF_DIR).replace("\\", "/"),
        },
    }


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind_host(host: str, allow_remote: bool = False) -> None:
    """Avoid accidentally publishing project details beyond this machine."""
    if not _is_loopback(host) and not allow_remote:
        raise ValueError("Dashboard binds only to loopback by default; use --allow-remote to explicitly expose it")


def make_handler(repo: Path, refresh_seconds: int = 10, *, allow_remote: bool = False, bind_host: str = "127.0.0.1"):
    """Create a handler bound to one repository with GET views and a loopback-only confirm action."""
    html_path = dashboard_html_path()
    refresh_seconds = max(3, int(refresh_seconds))

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CAO-Dashboard"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _host_allowed(self) -> bool:
            if allow_remote:
                return True
            host_header = self.headers.get("Host", "")
            try:
                request_host = (urlsplit("//" + host_header).hostname or "").lower()
            except ValueError:
                return False
            return request_host in {"localhost", "127.0.0.1", "::1", bind_host.lower()}

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_allowed():
                self._send(403, "text/plain; charset=utf-8", b"Host is not allowed")
                return
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                try:
                    page = html_path.read_bytes()
                    can_confirm = "true" if _is_loopback(bind_host) else "false"
                    config_path = repo / ".orchestrator" / "config.json"
                    try:
                        cfg = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
                    except (OSError, json.JSONDecodeError):
                        cfg = {}
                    language = str((cfg.get("language") or {}).get("user") or "zh-CN")
                    language_json = json.dumps(language, ensure_ascii=False)
                    runtime = f"<script>window.CAO_REFRESH_SECONDS={refresh_seconds};window.CAO_CAN_CONFIRM_ARTIFACTS={can_confirm};window.CAO_LANGUAGE={language_json};</script>".encode("utf-8")
                    page = page.replace(b"<!-- CAO_RUNTIME_CONFIG -->", runtime)
                    self._send(200, "text/html; charset=utf-8", page)
                except OSError:
                    self._send(500, "text/plain; charset=utf-8", b"Dashboard UI is unavailable")
                return
            if path == "/api/artifact-preview":
                query = parse_qs(urlparse(self.path).query)
                work_id = (query.get("work_item_id") or [""])[0]
                artifact_path = (query.get("path") or [""])[0]
                try:
                    payload = preview_artifact(repo, work_id, artifact_path)
                    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                except ValueError as exc:
                    status = 404 if str(exc) in {"WORK_ITEM_NOT_FOUND", "ARTIFACT_NOT_DISCOVERED_OR_MISSING"} else 400
                    body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self._send(status, "application/json; charset=utf-8", body)
                except Exception as exc:
                    body = json.dumps({"ok": False, "error": "ARTIFACT_PREVIEW_FAILED", "reason": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self._send(500, "application/json; charset=utf-8", body)
                return
            if path == "/api/overview":
                try:
                    payload = build_dashboard_overview(repo)
                    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                except Exception as exc:  # Keep server alive if one state record is malformed.
                    body = json.dumps({"ok": False, "error": "DASHBOARD_READ_FAILED", "reason": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self._send(500, "application/json; charset=utf-8", body)
                return
            self._send(404, "text/plain; charset=utf-8", b"Not found")

        def do_POST(self) -> None:  # noqa: N802
            # Confirmations only mutate CAO metadata and are deliberately disabled for remote binds.
            if not self._host_allowed():
                self._send(403, "application/json; charset=utf-8", b'{"ok":false,"error":"HOST_NOT_ALLOWED"}')
                return
            path = urlparse(self.path).path
            if path != "/api/artifact/confirm":
                self._send(405, "text/plain; charset=utf-8", b"Unsupported dashboard action")
                return
            if not _is_loopback(bind_host):
                self._send(403, "application/json; charset=utf-8", b'{"ok":false,"error":"LOCAL_CONFIRMATION_ONLY"}')
                return
            host_header = self.headers.get("Host", "")
            origin = self.headers.get("Origin", "")
            try:
                origin_parts = urlsplit(origin)
            except ValueError:
                origin_parts = None
            if not origin_parts or origin_parts.scheme != "http" or origin_parts.netloc.lower() != host_header.lower():
                self._send(403, "application/json; charset=utf-8", b'{"ok":false,"error":"ORIGIN_NOT_ALLOWED"}')
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, "application/json; charset=utf-8", b'{"ok":false,"error":"JSON_REQUIRED"}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 1 or length > 8192:
                self._send(413, "application/json; charset=utf-8", b'{"ok":false,"error":"INVALID_BODY_SIZE"}')
                return
            try:
                payload = json.loads(self.rfile.read(length))
                work_id = str(payload.get("work_item_id") or "")
                artifact_path = str(payload.get("path") or "")
                if not work_id or not artifact_path:
                    raise ValueError("WORK_ITEM_AND_PATH_REQUIRED")
                result = confirm_artifact(repo, work_id, artifact_path)
                body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            except (ValueError, json.JSONDecodeError) as exc:
                body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(400, "application/json; charset=utf-8", body)
            except Exception as exc:
                body = json.dumps({"ok": False, "error": "ARTIFACT_CONFIRM_FAILED", "reason": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(500, "application/json; charset=utf-8", body)

        def do_PUT(self) -> None:  # noqa: N802
            self._send(405, "text/plain; charset=utf-8", b"Read-only dashboard")

        def do_DELETE(self) -> None:  # noqa: N802
            self._send(405, "text/plain; charset=utf-8", b"Read-only dashboard")

    return DashboardHandler


def serve_dashboard(args: Any) -> int:
    repo = Path(args.repo).resolve()
    validate_bind_host(args.host, bool(args.allow_remote))
    if not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repo}")
    refresh_seconds = max(3, int(args.refresh))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(repo, refresh_seconds, allow_remote=bool(args.allow_remote), bind_host=args.host))
    server.daemon_threads = True
    port = server.server_address[1]
    url_host = f"[{args.host}]" if ":" in args.host and not args.host.startswith("[") else args.host
    url = f"http://{url_host}:{port}/"
    print(json.dumps({
        "ok": True,
        "dashboard": url,
        "repo": str(repo),
        "read_only": False,
        "safe_actions": ["confirm_artifact"] if _is_loopback(args.host) else [],
        "remote_access": not _is_loopback(args.host),
        "refresh_seconds": refresh_seconds,
    }, ensure_ascii=False, indent=2), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("CAO dashboard stopped", flush=True)
    finally:
        server.server_close()
    return 0
