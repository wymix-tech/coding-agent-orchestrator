#!/usr/bin/env python3
"""Coding Agent Orchestrator: a small, agent-neutral work control plane."""
from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import sysconfig
import time
import uuid
from pathlib import Path
from typing import Any

ORCH = Path(".orchestrator")
CONFIG_REL = ORCH / "config.json"
INDEX_REL = ORCH / "index.json"
WORK_DIR = ORCH / "work"
SESSION_DIR = ORCH / "sessions"
HANDOFF_DIR = ORCH / "handoffs"
SNAPSHOT_IGNORE_DIRS = {".git", ".orchestrator", ".venv", "venv", "node_modules", "target", "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache"}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "verification": {"scripts": []},
    "rules": {"files": []},
    "sdd": {
        "provider": "auto",
        "selected_at": None,
        "requirement_globs": ["requirements/**/*.md", "specs/**/*.md", "openspec/**/*.md", "_bmad/**/*.md"],
        "exclude_globs": [],
        "command": {"inspect": "", "next": "", "transition": ""},
    },
}

WORK_STATUSES = {"todo", "active", "suspended", "completed"}
ACTION_TYPES = {"agent", "verification", "sdd", "human", "none"}

SDD_CATALOG: dict[str, dict[str, Any]] = {
    "openspec": {
        "name": "OpenSpec",
        "description": "Spec-driven development for AI coding assistants.",
        "project_markers": ["openspec/config.yaml", "openspec"],
        "cli": "openspec",
        "install": {
            "prerequisite": "Node.js >= 20.19.0",
            "external_commands": [
                ["npm", "install", "-g", "@fission-ai/openspec@latest"],
                ["pnpm", "add", "-g", "@fission-ai/openspec@latest"],
                ["bun", "add", "-g", "@fission-ai/openspec@latest"],
                ["yarn", "global", "add", "@fission-ai/openspec@latest"],
            ],
            "project_command": ["openspec", "init", "--tools", "all"],
        },
    },
    "bmad": {
        "name": "BMad Method",
        "description": "Agile AI-driven development method with project-local agents and workflows.",
        "project_markers": ["_bmad"],
        "cli": "bmad",
        "install": {
            "prerequisite": "Node.js >= 20.12.0, Python >= 3.10, uv",
            "external_commands": [],
            "tool_by_host": {
                "claude-code": "claude-code",
                "codex": "codex",
                "pi": "pi",
            },
            # BMad requires host-specific tool selection for a fresh non-interactive install.
            # Placeholders are rendered by build_bmad_project_command().
            "project_command": [
                "npx", "bmad-method", "install",
                "--directory", "{repo}",
                "--modules", "bmm",
                "--tools", "{tool}",
                "--yes",
            ],
        },
    },
    "speckit": {
        "name": "GitHub Spec Kit",
        "description": "GitHub's spec-driven development toolkit with the `specify` CLI and agent integrations.",
        "project_markers": [".specify"],
        "cli": "specify",
        "install": {
            "prerequisite": "uv",
            "external_commands": [["uv", "tool", "install", "specify-cli"]],
            "integration_by_host": {
                "claude-code": "claude",
                "codex": "codex",
                "pi": "pi",
            },
            "project_command": ["specify", "init", "--here", "--force", "--non-interactive", "--integration", "codex", "--ignore-agent-tools"],
        },
    },
}


def command_exists(command: str) -> bool:
    from shutil import which
    return which(command) is not None


def instruction_template_dir() -> Path | None:
    """Locate project-level instruction templates in a checkout or installed wheel."""
    source_root = Path(__file__).resolve().parents[1]
    candidates = [
        source_root / "templates",
        Path(sysconfig.get_path("data")) / "share" / "coding-agent-orchestrator" / "templates",
        Path(sysconfig.get_path("data")) / "templates",
    ]
    required = ("AGENTS.md", "CLAUDE.md")
    for candidate in candidates:
        if all((candidate / name).is_file() for name in required):
            return candidate
    return None


def install_project_instruction_templates(repo: Path) -> dict[str, Any]:
    """Install missing project bootstrap instructions without overwriting user files."""
    template_dir = instruction_template_dir()
    result: dict[str, Any] = {
        "status": "installed",
        "installed": [],
        "skipped_existing": [],
        "missing_templates": [],
    }
    if template_dir is None:
        result["status"] = "templates_unavailable"
        result["missing_templates"] = ["AGENTS.md", "CLAUDE.md"]
        return result

    for name in ("AGENTS.md", "CLAUDE.md"):
        target = repo / name
        source = template_dir / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(source.read_text(encoding="utf-8"))
            result["installed"].append(name)
        except FileExistsError:
            result["skipped_existing"].append(name)
        except OSError as exc:
            result["status"] = "partial"
            result.setdefault("errors", []).append({"file": name, "reason": str(exc)})
    if result.get("status") != "partial":
        if result["installed"]:
            result["status"] = "installed"
        elif result["skipped_existing"]:
            result["status"] = "already_present"
    return result


def command_output(argv: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def version_at_least(actual: tuple[int, ...] | None, required: tuple[int, ...]) -> bool:
    if actual is None:
        return False
    a = actual + (0,) * (len(required) - len(actual))
    return a[: len(required)] >= required


def find_sdd_project_markers(repo: Path, provider: str) -> list[str]:
    meta = SDD_CATALOG.get(provider) or {}
    found: list[str] = []
    for marker in meta.get("project_markers") or []:
        p = repo / marker
        if p.exists():
            found.append(marker)
    return found


def detect_sdd(repo: Path, provider: str, include_cli_version: bool = False) -> dict[str, Any]:
    meta = SDD_CATALOG.get(provider) or {}
    markers = find_sdd_project_markers(repo, provider)
    cli = str(meta.get("cli") or provider)
    cli_installed = command_exists(cli)
    cli_version = None
    if cli_installed and include_cli_version:
        rc, out, err = command_output([cli, "--version"])
        if rc == 0:
            cli_version = out or err
    return {
        "provider": provider,
        "name": meta.get("name", provider),
        "description": meta.get("description", ""),
        "installed": bool(markers),
        "initialized": bool(markers),
        "project_markers": markers,
        "cli_installed": cli_installed,
        "cli_version": cli_version,
    }


def sdd_status(repo: Path) -> dict[str, Any]:
    cfg = config(repo)
    selected = str((cfg.get("sdd") or {}).get("provider") or "auto")
    providers = [detect_sdd(repo, name) for name in SDD_CATALOG]
    installed = [x for x in providers if x["installed"]]
    selected_status = None
    if selected in SDD_CATALOG:
        selected_status = detect_sdd(repo, selected)
    return {
        "selected": selected,
        "selected_status": selected_status,
        "installed": installed,
        "providers": providers,
        "selection_required": not installed or (selected == "auto" and len(installed) > 1),
    }


def sdd_options(status: dict[str, Any]) -> list[dict[str, Any]]:
    options = []
    for item in status["providers"]:
        options.append({
            "provider": item["provider"],
            "name": item["name"],
            "description": item["description"],
            "installed": item["installed"],
            "cli_installed": item["cli_installed"],
            "project_markers": item["project_markers"],
        })
    return options


def select_sdd_provider(repo: Path, provider: str) -> None:
    if provider not in SDD_CATALOG:
        raise ValueError(f"Unsupported SDD provider: {provider}")
    cfg_path = repo / CONFIG_REL
    cfg = config(repo)
    cfg["sdd"]["provider"] = provider
    cfg["sdd"]["selected_at"] = now()
    save_json(cfg_path, cfg)


def find_python_runtime(required: tuple[int, ...]) -> dict[str, Any]:
    """Find any usable Python interpreter meeting the minimum version.

    Do not require the generic `python3` alias: macOS/Linux environments often
    expose a versioned executable such as `python3.11` instead.
    """
    candidates = ["python3"] + [f"python3.{minor}" for minor in range(20, 9, -1)] + ["python"]
    if sys.executable:
        candidates.append(sys.executable)

    seen: set[str] = set()
    available: list[dict[str, Any]] = []
    selected: str | None = None
    selected_version: str | None = None

    for name in candidates:
        if name in seen or not command_exists(name):
            continue
        seen.add(name)
        rc, out, err = command_output([name, "--version"])
        version_text = out or err
        actual = version_tuple(version_text)
        ok = rc == 0 and version_at_least(actual, required)
        available.append({"name": name, "ok": ok, "version": version_text})
        if ok and selected is None:
            selected = name
            selected_version = version_text

    if selected:
        return {
            "name": "python",
            "ok": True,
            "selected": selected,
            "version": selected_version,
            "required": ".".join(map(str, required)) + "+",
            "candidates": available,
        }

    return {
        "name": "python",
        "ok": False,
        "reason": "Python >= 3.10 is not installed or no compatible executable was found",
        "required": ".".join(map(str, required)) + "+",
        "candidates": available,
    }


def sdd_prerequisite_check(provider: str) -> dict[str, Any]:
    meta = SDD_CATALOG[provider]
    checks: list[dict[str, Any]] = []
    if provider == "openspec":
        if not command_exists("node"):
            checks.append({"name": "node", "ok": False, "reason": "Node.js is not installed", "required": ">= 20.19.0"})
        else:
            rc, out, err = command_output(["node", "--version"])
            actual = version_tuple(out or err)
            checks.append({"name": "node", "ok": rc == 0 and version_at_least(actual, (20, 19, 0)), "version": out or err, "required": ">= 20.19.0"})
    elif provider == "bmad":
        if not command_exists("node"):
            checks.append({"name": "node", "ok": False, "reason": "node is not installed", "required": ">= 20.12.0"})
        else:
            rc, out, err = command_output(["node", "--version"])
            actual = version_tuple(out or err)
            checks.append({"name": "node", "ok": rc == 0 and version_at_least(actual, (20, 12, 0)), "version": out or err, "required": "20.12.0+"})

        python_check = find_python_runtime((3, 10, 0))
        checks.append(python_check)
        uv_ok = command_exists("uv")
        checks.append({"name": "uv", "ok": uv_ok, "reason": "uv is not installed" if not uv_ok else None})
    elif provider == "speckit":
        checks.append({"name": "uv", "ok": command_exists("uv"), "reason": "uv is not installed" if not command_exists("uv") else None})
    return {"provider": provider, "ok": all(c["ok"] for c in checks), "checks": checks, "declared": meta["install"].get("prerequisite")}


def choose_external_install_command(provider: str) -> list[str] | None:
    for candidate in SDD_CATALOG[provider]["install"].get("external_commands") or []:
        if candidate and command_exists(candidate[0]):
            return candidate
    return None


def run_install_command(argv: list[str], repo: Path) -> dict[str, Any]:
    started = now()
    try:
        result = subprocess.run(argv, cwd=repo, text=True, capture_output=True)
    except OSError as exc:
        return {"command": shlex.join(argv), "exit_code": 127, "stdout": "", "stderr": str(exc), "started_at": started}
    return {"command": shlex.join(argv), "exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "started_at": started}


def repo_root(path: str | Path = ".") -> Path:
    p = Path(path).resolve()
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=p, text=True, stderr=subprocess.DEVNULL).strip()
        return Path(out).resolve()
    except Exception:
        return p


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


def config(repo: Path) -> dict[str, Any]:
    raw = load_json(repo / CONFIG_REL, {})
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if raw:
        deep_merge(merged, raw)

    # Migrate the previous command-list model only when the persisted config
    # does not explicitly contain the new `scripts` key. This distinction is
    # important because DEFAULT_CONFIG itself supplies an empty scripts list.
    raw_verification = raw.get("verification") if isinstance(raw, dict) else None
    if isinstance(raw_verification, dict) and "scripts" not in raw_verification:
        verification = merged.setdefault("verification", {})
        old_default = "python -m unittest discover -s tests -p 'test_*.py'"
        legacy_command = raw_verification.get("command")
        legacy_commands = raw_verification.get("commands")
        if isinstance(legacy_commands, list) and legacy_commands:
            verification["scripts"] = legacy_commands
        elif legacy_command and legacy_command != old_default:
            verification["scripts"] = [{"name": "default", "command": str(legacy_command), "required": True}]
        else:
            verification["scripts"] = []
    return merged


def verification_scripts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    verification = cfg.get("verification") or {}
    raw = verification.get("scripts")
    if raw is None:
        # Backward compatibility for configs created before the script model.
        raw = verification.get("commands") or []
        if not raw and verification.get("command"):
            raw = [{"name": "default", "command": str(verification["command"]), "required": True}]
    if not isinstance(raw, list):
        raise ValueError("verification.scripts must be a list")

    scripts: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if isinstance(item, str):
            command = item.strip()
            name = f"script-{index}"
            required = True
        elif isinstance(item, dict):
            command = str(item.get("command") or "").strip()
            name = str(item.get("name") or f"script-{index}").strip()
            required = bool(item.get("required", True))
        else:
            raise ValueError(f"verification.scripts[{index - 1}] must be an object or string")
        if not command:
            raise ValueError(f"verification.scripts[{index - 1}] has an empty command")
        scripts.append({"name": name or f"script-{index}", "command": command, "required": required})
    return scripts


# Compatibility alias for internal callers and older integrations.
verification_commands = verification_scripts


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}-{uuid.uuid4().hex[:6]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rel(path: Path, repo: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, f"*/{p}") for p in patterns)


def requirement_files(repo: Path, cfg: dict[str, Any]) -> list[Path]:
    sdd = cfg.get("sdd") or {}
    files: set[Path] = set()
    for pattern in sdd.get("requirement_globs") or []:
        for item in glob.glob(str(repo / pattern), recursive=True):
            p = Path(item)
            if p.is_file() and not matches_any(rel(p, repo), sdd.get("exclude_globs") or []):
                files.add(p.resolve())
    return sorted(files)


def code_snapshot(repo: Path, excluded: set[str] | None = None) -> str:
    excluded = excluded or set()
    h = hashlib.sha256()
    for p in sorted(repo.rglob("*")):
        if not p.is_file():
            continue
        rp = rel(p, repo)
        parts = Path(rp).parts
        if any(part in SNAPSHOT_IGNORE_DIRS for part in parts) or rp in excluded:
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(rp.encode()); h.update(b"\0"); h.update(hashlib.sha256(data).digest()); h.update(b"\0")
    return h.hexdigest()


def verification_snapshot(cfg: dict[str, Any]) -> str:
    payload = verification_scripts(cfg)
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(data)


def rules_snapshot(repo: Path, cfg: dict[str, Any]) -> str:
    files: set[Path] = set()
    for pattern in (cfg.get("rules") or {}).get("files") or []:
        for item in glob.glob(str(repo / pattern), recursive=True):
            p = Path(item)
            if p.is_file():
                files.add(p.resolve())
    h = hashlib.sha256()
    for p in sorted(files):
        rp = rel(p, repo)
        h.update(rp.encode()); h.update(b"\0"); h.update(sha256_file(p).encode()); h.update(b"\0")
    return h.hexdigest()


def stable_work_id(repo: Path, requirement: Path) -> str:
    key = rel(requirement, repo)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(key).stem).strip("-").lower() or "work"
    return f"{slug}-{sha256_bytes(key.encode())[:8]}"


def work_path(repo: Path, work_id: str) -> Path:
    return repo / WORK_DIR / f"{work_id}.json"


def session_path(repo: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id)
    return repo / SESSION_DIR / f"{safe}.json"


def handoff_path(repo: Path, work_id: str) -> Path:
    return repo / HANDOFF_DIR / f"{work_id}.json"


def empty_index() -> dict[str, Any]:
    return {"version": 1, "active_work": None, "work_items": [], "sessions": []}


def load_index(repo: Path) -> dict[str, Any]:
    return load_json(repo / INDEX_REL, empty_index())


def save_index(repo: Path, index: dict[str, Any]) -> None:
    index["updated_at"] = now()
    save_json(repo / INDEX_REL, index)


def load_work(repo: Path, work_id: str) -> dict[str, Any] | None:
    p = work_path(repo, work_id)
    return load_json(p, None) if p.exists() else None


def sync_index_for_work(repo: Path, work: dict[str, Any]) -> None:
    """Project a Work Item into index.json after every Work Item mutation."""
    index = load_index(repo)
    items = [item for item in index.get("work_items", []) if item.get("id") != work.get("id")]
    items.append(work_summary(work))
    index["work_items"] = items
    if work.get("status") == "active":
        index["active_work"] = work["id"]
    elif index.get("active_work") == work.get("id"):
        index["active_work"] = None
    save_index(repo, index)


def save_work(repo: Path, work: dict[str, Any]) -> None:
    work["updated_at"] = now()
    save_json(work_path(repo, work["id"]), work)
    sync_index_for_work(repo, work)


def load_session(repo: Path, session_id: str) -> dict[str, Any] | None:
    p = session_path(repo, session_id)
    return load_json(p, None) if p.exists() else None


def save_session(repo: Path, session: dict[str, Any]) -> None:
    session["updated_at"] = now()
    save_json(session_path(repo, session["id"]), session)


def current_work_id(repo: Path, index: dict[str, Any] | None = None) -> str | None:
    index = index or load_index(repo)
    return index.get("active_work")


def requirement_evidence(repo: Path, requirement: Path) -> dict[str, str]:
    return {"path": rel(requirement, repo), "sha256": sha256_file(requirement)}


def infer_sdd_provider(repo: Path, requirement: Path, cfg: dict[str, Any]) -> str:
    p = rel(requirement, repo).lower()
    configured = str((cfg.get("sdd") or {}).get("provider") or "auto")
    if configured != "auto":
        return configured
    if p.startswith("openspec/"):
        return "openspec"
    if p.startswith("_bmad/") or p.startswith("bmad/"):
        return "bmad"
    if "/specs/" in f"/{p}" or p.startswith("specs/"):
        if (repo / ".specify").exists():
            return "speckit"
        return "generic-sdd"
    return "generic"


def run_sdd_command(repo: Path, name: str, extra: list[str]) -> dict[str, Any] | None:
    command = str((((config(repo).get("sdd") or {}).get("command") or {}).get(name) or "")).strip()
    if not command:
        return None
    env = os.environ.copy()
    env.update({"CAO_REPO": str(repo), "CAO_COMMAND": name})
    result = subprocess.run(shlex.split(command) + extra, cwd=repo, text=True, capture_output=True, env=env)
    return {"command": command, "args": extra, "exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def action(kind: str, instruction: str, **extra: Any) -> dict[str, Any]:
    if kind not in ACTION_TYPES:
        raise ValueError(f"unsupported action type: {kind}")
    return {"type": kind, "instruction": instruction, **extra}


def work_summary(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": work["id"],
        "title": work["title"],
        "status": work["status"],
        "step": work.get("execution", {}).get("step"),
        "next_action": work.get("execution", {}).get("next_action"),
        "sdd": work.get("sdd"),
        "blockers": work.get("blockers") or [],
        "decision_count": len(work.get("decisions") or []),
        "verification": (work.get("verification") or {}).get("status"),
        "last_agent": work.get("resume", {}).get("last_agent"),
    }


def completion_status(repo: Path, work: dict[str, Any] | None) -> dict[str, Any]:
    if not work:
        return {"ready": False, "reason": "no work item"}
    if work.get("status") != "active":
        return {"ready": False, "reason": f"work status is {work.get('status')}"}
    if work.get("blockers"):
        return {"ready": False, "reason": "active blocker exists"}
    req = repo / work["requirement"]["path"]
    if not req.exists():
        return {"ready": False, "reason": "requirement file missing"}
    req_sha = sha256_file(req)
    if req_sha != work["requirement"]["sha256"]:
        return {"ready": False, "reason": "requirement changed since work started"}
    cfg = config(repo)
    code_sha = code_snapshot(repo, {work["requirement"]["path"]})
    rules_sha = rules_snapshot(repo, cfg)
    verification_cfg_sha = verification_snapshot(cfg)
    v = work.get("verification") or {}
    config_bound = v.get("source", "config") == "config"
    fresh = (
        v.get("status") == "passed"
        and v.get("code_snapshot") == code_sha
        and v.get("rules_snapshot") == rules_sha
        and v.get("requirement_sha256") == req_sha
        and (not config_bound or v.get("verification_config_sha256") == verification_cfg_sha)
    )
    return {
        "ready": fresh,
        "reason": None if fresh else "verification is missing or stale",
        "current_code_snapshot": code_sha,
        "current_rules_snapshot": rules_sha,
        "current_verification_config_sha256": verification_cfg_sha,
    }


def resume_payload(repo: Path, work: dict[str, Any], session_id: str | None = None, host: str | None = None) -> dict[str, Any]:
    c = completion_status(repo, work)
    handoff = load_json(handoff_path(repo, work["id"]), None) or {}
    return {
        "work_item": work_summary(work),
        "requirement": work.get("requirement"),
        "execution": work.get("execution"),
        "resume": work.get("resume"),
        "decisions": work.get("decisions") or [],
        "blockers": work.get("blockers") or [],
        "verification": work.get("verification"),
        "completion": c,
        "handoff": handoff or None,
        "session": {"id": session_id, "host": host} if session_id else None,
    }


def render_context(repo: Path, work: dict[str, Any] | None, *, session_id: str | None = None, host: str | None = None) -> str:
    if not work:
        return "# Coding Agent Orchestrator\nNo active work item. Run `cao start --requirement <path>` after selecting a requirement."
    p = resume_payload(repo, work, session_id=session_id, host=host)
    e = p["execution"] or {}
    lines = [
        "# Coding Agent Orchestrator Resume Context",
        f"Work Item: {p['work_item']['title']} ({p['work_item']['id']})",
        f"Status: {p['work_item']['status']}",
        f"Requirement: {p['requirement']['path']}",
        f"SDD: {(p.get('work_item', {}).get('sdd') or {}).get('provider', 'none')}",
        f"Step: {e.get('step') or 'not recorded'}",
        "",
        "## Completed / Current",
        str((p.get("resume") or {}).get("summary") or "No handoff summary recorded."),
        "",
        "## Important Decisions",
    ]
    decisions = p.get("decisions") or []
    lines.extend([f"- {d.get('decision')}" + (f" | reason: {d.get('reason')}" if d.get("reason") else "") for d in decisions[-8:]] or ["- None recorded"])
    lines.append("")
    lines.append("## Blockers")
    blockers = p.get("blockers") or []
    lines.extend([f"- {b.get('reason')}" + (f" | resolution: {b.get('resolution')}" if b.get("resolution") else "") for b in blockers] or ["- None"])
    lines.extend([
        "",
        "## Next Action",
        json.dumps(e.get("next_action") or action("none", "No next action recorded."), ensure_ascii=False),
        "",
        "## Verification",
        json.dumps(p.get("verification") or {"status": "pending"}, ensure_ascii=False),
        f"Completion ready: {p['completion'].get('ready')}",
    ])
    return "\n".join(lines)


def register_session(repo: Path, work: dict[str, Any] | None, session_id: str, host: str) -> dict[str, Any]:
    ts = now()
    existing = load_session(repo, session_id)
    session = existing or {"id": session_id, "created_at": ts, "host": host}
    session.update({"host": host, "work_item": work.get("id") if work else None, "status": "active", "last_seen": ts, "last_action": (work or {}).get("execution", {}).get("next_action")})
    save_session(repo, session)
    index = load_index(repo)
    if work and work.get("status") == "active":
        index["active_work"] = work["id"]
    sessions = [s for s in index.get("sessions", []) if s.get("id") != session_id]
    sessions.append({"id": session_id, "host": host, "work_item": work.get("id") if work else None, "status": "active", "updated_at": ts})
    index["sessions"] = sessions[-30:]
    save_index(repo, index)
    if work:
        active = [s for s in work.get("sessions", []) if s.get("id") != session_id]
        active.append({"id": session_id, "host": host, "status": "active", "last_seen": ts})
        work["sessions"] = active[-20:]
        work.setdefault("resume", {})["last_agent"] = host
        save_work(repo, work)
    return session


def create_handoff(repo: Path, work: dict[str, Any], session_id: str | None, host: str | None, reason: str = "session stop") -> dict[str, Any]:
    handoff = {
        "version": 1,
        "work_item": work["id"],
        "session_id": session_id,
        "host": host,
        "reason": reason,
        "created_at": now(),
        "step": work.get("execution", {}).get("step"),
        "next_action": work.get("execution", {}).get("next_action"),
        "summary": work.get("resume", {}).get("summary"),
        "completed": work.get("resume", {}).get("completed", []),
        "pending": work.get("resume", {}).get("pending", []),
        "decisions": work.get("decisions", [])[-8:],
        "blockers": work.get("blockers", []),
        "verification": work.get("verification"),
    }
    save_json(handoff_path(repo, work["id"]), handoff)
    if session_id:
        s = load_session(repo, session_id) or {"id": session_id, "host": host}
        s.update({"status": "ended", "ended_at": now(), "last_action": handoff.get("next_action")})
        save_session(repo, s)
        index = load_index(repo)
        for item in index.get("sessions", []):
            if item.get("id") == session_id:
                item.update({"status": "ended", "updated_at": now()})
        save_index(repo, index)
    return handoff


def resolve_requirement(repo: Path, path: str | None) -> Path | None:
    if path:
        candidate = Path(path)
        p = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
        return p if p.is_file() else None
    files = requirement_files(repo, config(repo))
    return files[0] if len(files) == 1 else None


def ensure_initialized(repo: Path) -> None:
    if not (repo / CONFIG_REL).exists():
        raise RuntimeError("Project is not initialized. Run `cao init` first.")


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Return the always-on startup context for an Agent.

    Bootstrap establishes CAO state, verifies that an SDD is installed in the
    project, and only then exposes the Work Item next action. It never creates
    a Work Item and never reruns requirement intake merely because a session or
    Agent changed.
    """
    repo = repo_root(args.repo)
    project_instructions = install_project_instruction_templates(repo)
    if not (repo / CONFIG_REL).exists():
        save_json(repo / CONFIG_REL, json.loads(json.dumps(DEFAULT_CONFIG)))
        save_index(repo, load_index(repo))
        for d in (WORK_DIR, SESSION_DIR, HANDOFF_DIR):
            (repo / d).mkdir(parents=True, exist_ok=True)

    status = sdd_status(repo)
    cfg = config(repo)
    selected = status["selected"]
    installed = status["installed"]
    active_id = load_index(repo).get("active_work")
    work = load_work(repo, active_id) if active_id else None

    # Existing work owns its provider choice. A missing provider is repaired
    # before any resume/implementation action is exposed.
    work_provider = str(((work or {}).get("sdd") or {}).get("provider") or "auto")
    if work and work_provider in SDD_CATALOG and not detect_sdd(repo, work_provider)["installed"]:
        next_out = {
            "ok": True,
            "mode": "sdd_install_required",
            "work_item": work_summary(work),
            "sdd": {"selected": work_provider, "status": detect_sdd(repo, work_provider)},
            "options": sdd_options(status),
            "next": action("human", f"SDD `{work_provider}` is not installed in this project. Run `{sdd_install_instruction(work_provider, getattr(args, 'host', None))}`, then run `cao bootstrap` again."),
        }
        next_out["project_instructions"] = project_instructions
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    if not installed:
        next_out = {
            "ok": True,
            "mode": "sdd_select",
            "work_item": work_summary(work) if work else None,
            "sdd": {"status": "not_installed", "selected": selected},
            "options": sdd_options(status),
            "next": action("human", "No SDD is installed in this project. Ask the user to choose OpenSpec, BMad Method, or GitHub Spec Kit, then run `cao sdd install --provider <provider> --yes`; for BMad pass `--host` or explicit `--tools`, and for Spec Kit pass `--host` or `--integration`. Do not start or modify production code before installation completes."),
        }
        next_out["project_instructions"] = project_instructions
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    # If exactly one SDD is already installed, make it the project selection
    # automatically. Multiple installed SDDs require an explicit choice.
    if selected == "auto" and len(installed) == 1:
        select_sdd_provider(repo, installed[0]["provider"])
        selected = installed[0]["provider"]
    elif selected == "auto" and len(installed) > 1:
        next_out = {
            "ok": True,
            "mode": "sdd_select",
            "work_item": work_summary(work) if work else None,
            "sdd": {"status": "multiple_installed", "selected": "auto"},
            "options": sdd_options(status),
            "next": action("human", "Multiple SDDs are installed. Ask the user to choose one, then run `cao sdd select --provider <provider>` and bootstrap again."),
        }
        next_out["project_instructions"] = project_instructions
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    if work:
        next_out = {
            "ok": True,
            "mode": "resume",
            "work_item": work_summary(work),
            "sdd": sdd_status(repo),
            "context": resume_payload(repo, work),
            "next": None,
        }
        execution = work.get("execution") or {}
        configured = execution.get("next_action")
        completion = completion_status(repo, work)
        if work.get("status") in {"todo", "suspended"}:
            next_out["next"] = action("agent", f"Resume this work item with `cao resume --work {work['id']}`.")
        elif work.get("blockers"):
            next_out["next"] = action("human", "Resolve the active blocker before continuing.")
        elif (work.get("verification") or {}).get("status") == "failed":
            next_out["next"] = action("agent", "Fix the verification failure, then run `cao verify`.")
        elif completion["ready"]:
            next_out["next"] = action("agent", "Run `cao done` to close the work item.")
        else:
            next_out["next"] = configured or action("agent", "Continue the current work and record a checkpoint before changing requirements or switching agents.")
    else:
        next_out = {
            "ok": True,
            "mode": "select",
            "work_item": None,
            "sdd": sdd_status(repo),
            "context": None,
            "next": action("agent", "Select a requirement and run `cao start --requirement <path>`. Do not invent a new requirement while resuming."),
        }
    next_out["project_instructions"] = project_instructions
    print(json.dumps(next_out, ensure_ascii=False, indent=2))
    return 0


def cmd_sdd_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    if (repo / CONFIG_REL).exists():
        status = sdd_status(repo)
        status["providers"] = [detect_sdd(repo, name, include_cli_version=True) for name in SDD_CATALOG]
        status["installed"] = [x for x in status["providers"] if x["installed"]]
        selected = status["selected"]
        status["selected_status"] = detect_sdd(repo, selected, include_cli_version=True) if selected in SDD_CATALOG else None
    else:
        providers = [detect_sdd(repo, name, include_cli_version=True) for name in SDD_CATALOG]
        status = {
            "selected": "auto",
            "selected_status": None,
            "installed": [x for x in providers if x["installed"]],
            "providers": providers,
            "selection_required": True,
        }
    print(json.dumps({"ok": True, **status}, ensure_ascii=False, indent=2))
    return 0


def cmd_sdd_select(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    detected = detect_sdd(repo, args.provider)
    if not detected["installed"]:
        print(json.dumps({
            "ok": False,
            "error": "SDD_NOT_INSTALLED",
            "provider": args.provider,
            "status": detected,
            "next": f"cao sdd install --provider {args.provider} --yes",
        }, ensure_ascii=False, indent=2))
        return 2
    select_sdd_provider(repo, args.provider)
    print(json.dumps({"ok": True, "selected": args.provider, "status": detect_sdd(repo, args.provider)}, ensure_ascii=False, indent=2))
    return 0


def speckit_integration_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["speckit"]["install"].get("integration_by_host") or {}).get(host) or "") or None


def bmad_tool_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["bmad"]["install"].get("tool_by_host") or {}).get(host) or "") or None


def sdd_install_instruction(provider: str, host: str | None = None, tools: str | None = None) -> str:
    if provider == "speckit":
        if host:
            return f"cao sdd install --provider speckit --host {host} --yes"
        return "cao sdd install --provider speckit --host <claude-code|codex|pi> --yes"
    if provider == "bmad":
        if tools:
            return f"cao sdd install --provider bmad --tools {tools} --yes"
        if host:
            return f"cao sdd install --provider bmad --host {host} --yes"
        return "cao sdd install --provider bmad --host <claude-code|codex|pi> --yes"
    return f"cao sdd install --provider {provider} --yes"


def build_bmad_project_command(args: argparse.Namespace, repo: Path) -> list[str]:
    tools = args.tools or bmad_tool_for_host(args.host)
    if not tools:
        raise ValueError("BMad installation requires --host <claude-code|codex|pi> or explicit --tools <tool-id>")
    template = list(SDD_CATALOG["bmad"]["install"].get("project_command") or [])
    if not template:
        raise ValueError("BMad installation command template is not configured")
    replacements = {"{repo}": str(repo), "{tool}": tools}
    return [replacements.get(part, part) for part in template]


def build_speckit_project_command(args: argparse.Namespace) -> list[str]:
    integration = args.integration or speckit_integration_for_host(args.host)
    if not integration:
        raise ValueError("Spec Kit installation requires --host <claude-code|codex|pi> or --integration <spec-kit integration>")
    command = ["specify", "init", "--here", "--force", "--non-interactive", "--integration", integration, "--ignore-agent-tools"]
    if args.integration_options:
        command.append(f"--integration-options={args.integration_options}")
    return command


def cmd_sdd_install(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    provider = args.provider
    if provider not in SDD_CATALOG:
        print(json.dumps({"ok": False, "error": "SDD_PROVIDER_REQUIRED", "options": sdd_options(sdd_status(repo))}, ensure_ascii=False, indent=2))
        return 2

    detected = detect_sdd(repo, provider)
    if detected["installed"]:
        select_sdd_provider(repo, provider)
        print(json.dumps({"ok": True, "status": "already_installed", "selected": provider, "sdd": detected}, ensure_ascii=False, indent=2))
        return 0

    prereq = sdd_prerequisite_check(provider)
    if not prereq["ok"]:
        print(json.dumps({"ok": False, "error": "SDD_PREREQUISITE_FAILED", "provider": provider, "prerequisites": prereq}, ensure_ascii=False, indent=2))
        return 2

    meta = SDD_CATALOG[provider]["install"]
    external = choose_external_install_command(provider)
    plan = []
    if external:
        plan.append({"scope": "machine", "command": shlex.join(external)})
    project_command = list(meta.get("project_command") or [])
    if provider == "openspec" and args.tools:
        project_command = ["openspec", "init", "--tools", args.tools]
    elif provider == "bmad":
        try:
            project_command = build_bmad_project_command(args, repo)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "SDD_INSTALL_ARGUMENTS_REQUIRED", "provider": provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    elif provider == "speckit":
        project_command = build_speckit_project_command(args)
    if project_command:
        plan.append({"scope": "project", "command": shlex.join(project_command)})

    if not args.yes:
        print(json.dumps({
            "ok": True,
            "status": "confirmation_required",
            "provider": provider,
            "sdd": detected,
            "prerequisites": prereq,
            "install_plan": plan,
            "next": f"Review the install plan and rerun `cao sdd install --provider {provider} --yes` after the user confirms.",
        }, ensure_ascii=False, indent=2))
        return 0

    results = []
    if external:
        result = run_install_command(external, repo); results.append(result)
        if result["exit_code"] != 0:
            print(json.dumps({"ok": False, "error": "SDD_MACHINE_INSTALL_FAILED", "provider": provider, "results": results}, ensure_ascii=False, indent=2))
            return 1

    # Provider project installers are invoked only after explicit user
    # confirmation via --yes. Built-in commands are constructed in non-
    # interactive form so Bootstrap never gets trapped inside a provider prompt.
    if project_command:
        try:
            result = subprocess.run(project_command, cwd=repo, text=True)
            results.append({"command": shlex.join(project_command), "exit_code": result.returncode})
        except OSError as exc:
            results.append({"command": shlex.join(project_command), "exit_code": 127, "stderr": str(exc)})
            result = None
        if result is None or result.returncode != 0:
            print(json.dumps({"ok": False, "error": "SDD_PROJECT_INSTALL_FAILED", "provider": provider, "results": results}, ensure_ascii=False, indent=2))
            return 1

    final = detect_sdd(repo, provider)
    if not final["installed"]:
        print(json.dumps({"ok": False, "error": "SDD_INSTALL_NOT_DETECTED", "provider": provider, "results": results, "status": final}, ensure_ascii=False, indent=2))
        return 1
    select_sdd_provider(repo, provider)
    print(json.dumps({"ok": True, "status": "installed", "selected": provider, "sdd": final, "results": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_sdd(args: argparse.Namespace) -> int:
    return int(args.fn(args))


def resolve_work(repo: Path, work_id: str | None = None) -> dict[str, Any] | None:
    index = load_index(repo)
    wid = work_id or index.get("active_work")
    return load_work(repo, wid) if wid else None


def resolve_artifact_path(repo: Path, value: str) -> tuple[Path, str]:
    p = Path(value)
    absolute = p if p.is_absolute() else repo / p
    resolved = absolute.resolve()
    try:
        relative = resolved.relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError("Artifact path must be inside the repository") from exc
    return resolved, relative.as_posix()


def artifact_reference(repo: Path, value: str, role: str | None = None, native_type: str | None = None, native_ref: str | None = None) -> dict[str, Any]:
    path, relative = resolve_artifact_path(repo, value)
    if not path.is_file():
        raise ValueError(f"Artifact file not found: {relative}")
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "role": role,
        "native_type": native_type,
        "native_ref": native_ref,
    }


def cmd_artifact_add(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    try:
        ref = artifact_reference(repo, args.path, args.role, args.native_type, args.native_ref)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    artifacts[:] = [a for a in artifacts if a.get("path") != ref["path"]]
    artifacts.append(ref)
    save_work(repo, work)
    print(json.dumps({"ok": True, "artifact": ref, "work_item": work_summary(work)}, ensure_ascii=False, indent=2)); return 0


def cmd_artifact_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    print(json.dumps({"ok": True, "work_item": work["id"], "artifacts": work.get("sdd", {}).get("artifacts", [])}, ensure_ascii=False, indent=2)); return 0


def cmd_artifact_remove(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    _, relative = resolve_artifact_path(repo, args.path)
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    before = len(artifacts)
    work["sdd"]["artifacts"] = [a for a in artifacts if a.get("path") != relative]
    save_work(repo, work)
    print(json.dumps({"ok": True, "removed": before - len(work["sdd"]["artifacts"]), "artifacts": work["sdd"]["artifacts"]}, ensure_ascii=False, indent=2)); return 0


def cmd_artifact_refresh(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    target = args.path
    refreshed = []
    for item in artifacts:
        if target:
            _, relative = resolve_artifact_path(repo, target)
            if item.get("path") != relative:
                continue
        p = repo / item["path"]
        if p.is_file():
            item["sha256"] = sha256_file(p)
            refreshed.append(item)
    if target and not refreshed:
        print(json.dumps({"ok": False, "error": "ARTIFACT_NOT_FOUND", "path": target}, ensure_ascii=False, indent=2)); return 2
    save_work(repo, work)
    print(json.dumps({"ok": True, "refreshed": refreshed, "artifacts": artifacts}, ensure_ascii=False, indent=2)); return 0


def cmd_init(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    cfg_path = repo / CONFIG_REL
    if cfg_path.exists() and not args.force:
        out = {"ok": True, "status": "already_initialized", "config": str(cfg_path)}
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if args.sdd:
        cfg["sdd"]["provider"] = args.sdd
    verify_scripts = args.verify_script or args.verify_command or []
    if verify_scripts:
        cfg["verification"]["scripts"] = [
            {"name": f"script-{i}", "command": str(command).strip(), "required": True}
            for i, command in enumerate(verify_scripts, 1) if str(command).strip()
        ]
    save_json(cfg_path, cfg)
    index = load_index(repo)
    save_index(repo, index)
    for d in (WORK_DIR, SESSION_DIR, HANDOFF_DIR):
        (repo / d).mkdir(parents=True, exist_ok=True)
    out = {"ok": True, "status": "initialized", "config": str(cfg_path), "state": str(repo / INDEX_REL)}
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_start(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    if args.sdd_provider:
        detected = detect_sdd(repo, args.sdd_provider)
        if not detected["installed"]:
            print(json.dumps({
                "ok": False,
                "error": "SDD_NOT_INSTALLED",
                "provider": args.sdd_provider,
                "status": detected,
                "next": f"cao sdd install --provider {args.sdd_provider} --yes",
            }, ensure_ascii=False, indent=2)); return 2
        select_sdd_provider(repo, args.sdd_provider)
    sdd_state = sdd_status(repo)
    installed = sdd_state["installed"]
    if not installed:
        print(json.dumps({"ok": False, "error": "SDD_SELECTION_REQUIRED", "options": sdd_options(sdd_state), "next": "cao bootstrap"}, ensure_ascii=False, indent=2)); return 2
    selected = str((config(repo).get("sdd") or {}).get("provider") or "auto")
    if selected == "auto" and len(installed) > 1:
        print(json.dumps({"ok": False, "error": "SDD_SELECTION_REQUIRED", "options": sdd_options(sdd_state), "next": "cao sdd select --provider <provider>"}, ensure_ascii=False, indent=2)); return 2
    if selected == "auto" and len(installed) == 1:
        select_sdd_provider(repo, installed[0]["provider"])
    p = resolve_requirement(repo, args.requirement)
    if p is None:
        candidates = [rel(x, repo) for x in requirement_files(repo, config(repo))]
        print(json.dumps({"ok": False, "error": "REQUIREMENT_SELECTION_REQUIRED", "candidates": candidates}, ensure_ascii=False, indent=2)); return 2
    index = load_index(repo)
    active_id = index.get("active_work")
    if active_id:
        active = load_work(repo, active_id)
        if active and active.get("status") == "active":
            if not args.replace:
                print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_ACTIVE", "work_item": work_summary(active)}, ensure_ascii=False, indent=2)); return 2
            active["status"] = "suspended"
            active.setdefault("resume", {})["pending"] = list(dict.fromkeys((active.get("resume", {}).get("pending") or []) + [f"Execution re-anchored to a new work item: {Path(p).stem}"]))
            save_work(repo, active)
            create_handoff(repo, active, None, None, reason="execution re-anchored to another work item")
    wid = stable_work_id(repo, p)
    existing = load_work(repo, wid)
    if existing and existing.get("status") == "completed" and not args.reopen:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_COMPLETED", "work_item": work_summary(existing)}, ensure_ascii=False, indent=2)); return 2
    configured_provider = str((config(repo).get("sdd") or {}).get("provider") or "auto")
    provider = configured_provider if configured_provider != "auto" else infer_sdd_provider(repo, p, config(repo))
    work = existing or {
        "version": 1,
        "id": wid,
        "title": args.title or p.stem,
        "status": "todo",
        "requirement": requirement_evidence(repo, p),
        "sdd": {"provider": provider, "native_ref": args.sdd_ref, "artifacts": []},
        "execution": {"step": "intake", "next_action": action("agent", "Read the requirement and create/locate the SDD-native planning artifacts before implementation.")},
        "resume": {"summary": "Work item created. Requirement is ready for intake.", "completed": [], "pending": []},
        "decisions": [],
        "blockers": [],
        "verification": None,
        "sessions": [],
        "created_at": now(),
    }
    work["status"] = "active"
    work["started_at"] = work.get("started_at") or now()
    work["requirement"] = requirement_evidence(repo, p)
    if args.title:
        work["title"] = args.title
    work["sdd"]["provider"] = provider
    if args.sdd_ref:
        work["sdd"]["native_ref"] = args.sdd_ref
    save_work(repo, work)
    native = run_sdd_command(repo, "inspect", [rel(p, repo)])
    print(json.dumps({"ok": True, "action": "IMPLEMENT", "work_item": work_summary(work), "sdd": native}, ensure_ascii=False, indent=2)); return 0


def cmd_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    items: list[dict[str, Any]] = []
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        work = load_json(p, None)
        if work:
            items.append(work_summary(work))
    index["work_items"] = items
    save_index(repo, index)
    print(json.dumps({"ok": True, "active_work": index.get("active_work"), "work_items": items}, ensure_ascii=False, indent=2)); return 0


def cmd_status(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    out = {"ok": True, "work": resume_payload(repo, work) if work else None, "work_items": []}
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        w = load_json(p, None)
        if w:
            out["work_items"].append(work_summary(w))
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_next(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    if not work:
        out = {"ok": True, "action": action("agent", "Select a requirement and run `cao start --requirement <path>`."), "work_item": None}
    elif not work_sdd_ready(repo, work)[0]:
        provider = ((work.get("sdd") or {}).get("provider") or "auto")
        out = {"ok": True, "action": action("human", f"Install the work item's SDD `{provider}` before continuing: `cao sdd install --provider {provider} --yes`."), "work_item": work_summary(work)}
    elif work.get("status") in {"todo", "suspended"}:
        out = {"ok": True, "action": action("agent", "Resume this work item with `cao resume --work %s`." % work["id"]), "work_item": work_summary(work)}
    elif work.get("status") == "completed":
        out = {"ok": True, "action": action("none", "Work item is completed."), "work_item": work_summary(work)}
    elif work.get("blockers"):
        out = {"ok": True, "action": action("human", "Resolve the active blocker before continuing."), "work_item": work_summary(work)}
    else:
        execution = work.get("execution") or {}
        configured = execution.get("next_action")
        c = completion_status(repo, work)
        if (work.get("verification") or {}).get("status") == "failed":
            nxt = action("agent", "Fix the verification failure, then run `cao verify`.")
        elif c["ready"]:
            nxt = action("agent", "Run `cao done` to close the work item.")
        else:
            nxt = configured or action("agent", "Continue implementation and update progress with `cao checkpoint`.")
        out = {"ok": True, "action": nxt, "work_item": work_summary(work)}
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.step:
        work.setdefault("execution", {})["step"] = args.step
    if args.next:
        work.setdefault("execution", {})["next_action"] = action(args.next_type, args.next, command=args.command) if args.next_type != "none" else action("none", args.next)
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    if args.completed:
        work.setdefault("resume", {}).setdefault("completed", []).append(args.completed)
    if args.pending:
        work.setdefault("resume", {}).setdefault("pending", []).append(args.pending)
    work.setdefault("resume", {})["last_checkpoint_at"] = now()
    save_work(repo, work)
    if args.session:
        s = load_session(repo, args.session)
        if s:
            s["last_seen"] = now(); s["last_action"] = work.get("execution", {}).get("next_action"); save_session(repo, s)
    print(json.dumps({"ok": True, "work_item": work_summary(work), "next": work.get("execution", {}).get("next_action")}, ensure_ascii=False, indent=2)); return 0


def cmd_decision(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    d = {"id": new_id("decision"), "decision": args.decision, "reason": args.reason, "scope": args.scope, "created_at": now(), "supersedes": args.supersedes}
    work.setdefault("decisions", []).append(d)
    save_work(repo, work)
    print(json.dumps({"ok": True, "decision": d}, ensure_ascii=False, indent=2)); return 0


def cmd_block(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    b = {"id": new_id("blocker"), "reason": args.reason, "resolution": args.resolution, "created_at": now()}
    work.setdefault("blockers", []).append(b)
    work["status"] = "suspended" if args.suspend else work.get("status", "active")
    save_work(repo, work)
    print(json.dumps({"ok": True, "blocker": b, "status": work["status"]}, ensure_ascii=False, indent=2)); return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = load_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_NOT_FOUND"}, ensure_ascii=False, indent=2)); return 2
    before = len(work.get("blockers") or [])
    if args.blocker:
        work["blockers"] = [b for b in work.get("blockers", []) if b.get("id") != args.blocker]
    else:
        work["blockers"] = []
    if work.get("status") == "suspended" and not work["blockers"]:
        work["status"] = "active"
    save_work(repo, work)
    print(json.dumps({"ok": True, "removed": before - len(work.get("blockers") or []), "work_item": work_summary(work)}, ensure_ascii=False, indent=2)); return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    save_work(repo, work)
    handoff = create_handoff(repo, work, args.session, args.host, args.reason)
    print(json.dumps({"ok": True, "handoff": handoff}, ensure_ascii=False, indent=2)); return 0


def choose_resume_work(repo: Path, work_id: str | None) -> dict[str, Any] | None:
    if work_id:
        return load_work(repo, work_id)
    index = load_index(repo); active = index.get("active_work")
    if active:
        return load_work(repo, active)
    candidates = []
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        w = load_json(p, None)
        if w and w.get("status") in {"active", "suspended"}:
            candidates.append(w)
    return candidates[0] if len(candidates) == 1 else None


def work_sdd_ready(repo: Path, work: dict[str, Any] | None) -> tuple[bool, dict[str, Any] | None]:
    if not work:
        return False, None
    provider = str(((work.get("sdd") or {}).get("provider") or "auto"))
    if provider not in SDD_CATALOG:
        return True, None
    detected = detect_sdd(repo, provider)
    return bool(detected["installed"]), detected


def cmd_resume(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = choose_resume_work(repo, args.work)
    if not work:
        candidates = []
        for p in sorted((repo / WORK_DIR).glob("*.json")):
            w = load_json(p, None)
            if w and w.get("status") in {"active", "suspended"}:
                candidates.append(work_summary(w))
        if candidates:
            print(json.dumps({"ok": False, "error": "WORK_ITEM_SELECTION_REQUIRED", "candidates": candidates}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"ok": False, "error": "NO_RESUMABLE_WORK"}, ensure_ascii=False, indent=2))
        return 2
    ready, detected = work_sdd_ready(repo, work)
    if not ready:
        provider = ((work.get("sdd") or {}).get("provider") or "auto")
        print(json.dumps({"ok": False, "error": "SDD_NOT_INSTALLED", "provider": provider, "status": detected, "next": f"cao sdd install --provider {provider} --yes"}, ensure_ascii=False, indent=2))
        return 2
    if work.get("status") == "suspended" and args.reactivate:
        work["status"] = "active"
        save_work(repo, work)
    elif work.get("status") == "suspended" and work.get("blockers"):
        # Resume can inspect suspended work without silently changing its status.
        pass
    session_id = args.session or new_id(args.host or "session")
    session = register_session(repo, work, session_id, args.host or "unknown")
    print(render_context(repo, work, session_id=session_id, host=args.host))
    print("\n## Session\n" + json.dumps(session, ensure_ascii=False, indent=2))
    return 0


def verification_status_for_exit_code(exit_code: int) -> str:
    if exit_code == 0:
        return "passed"
    if exit_code == 1:
        return "failed"
    if exit_code == 2:
        return "blocked"
    return "error"


def verification_reason(stdout: str, stderr: str, status: str) -> str:
    for stream in (stdout, stderr):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1][-1000:]
    defaults = {
        "passed": "Verification script passed.",
        "failed": "Verification script failed.",
        "blocked": "Verification could not be completed.",
        "error": "Verification script execution failed.",
    }
    return defaults[status]


def overall_verification_status(results: list[dict[str, Any]]) -> str:
    required = [r for r in results if r["required"]]
    if not required:
        return "passed"
    statuses = {r["status"] for r in required}
    if "error" in statuses:
        return "error"
    if "blocked" in statuses:
        return "blocked"
    if "failed" in statuses:
        return "failed"
    return "passed"


def cmd_verify(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2

    cfg = config(repo)
    cfg_scripts = verification_scripts(cfg)
    cli_values = args.script or args.command or []
    cli_override = [
        {"name": f"script-{i}", "command": str(command).strip(), "required": True}
        for i, command in enumerate(cli_values, 1) if str(command).strip()
    ]
    source = "cli" if cli_override else "config"
    scripts = cli_override or cfg_scripts
    if not scripts:
        print(json.dumps({
            "ok": False,
            "error": "VERIFICATION_SCRIPT_REQUIRED",
            "next": "Configure verification.scripts in .orchestrator/config.json"
        }, ensure_ascii=False, indent=2)); return 2

    req = repo / work["requirement"]["path"]
    if not req.exists():
        print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING"}, ensure_ascii=False, indent=2)); return 2
    req_sha = sha256_file(req); code_sha = code_snapshot(repo, {work["requirement"]["path"]}); rules_sha = rules_snapshot(repo, cfg)
    verification_cfg_sha = verification_snapshot(cfg)
    started = now()
    results: list[dict[str, Any]] = []
    for item in scripts:
        command = item["command"]
        command_started = now()
        try:
            result = subprocess.run(command, cwd=repo, shell=True, text=True, capture_output=True)
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except OSError as exc:
            exit_code = 127
            stdout = ""
            stderr = str(exc)
        status_for_script = verification_status_for_exit_code(exit_code)
        results.append({
            "name": item["name"],
            "command": command,
            "required": item["required"],
            "exit_code": exit_code,
            "status": status_for_script,
            "reason": verification_reason(stdout, stderr, status_for_script),
            "started_at": command_started,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        })

    status = overall_verification_status(results)
    verification = {
        "status": status,
        "source": source,
        "scripts": scripts,
        "results": results,
        "at": started,
        "requirement_sha256": req_sha,
        "code_snapshot": code_sha,
        "rules_snapshot": rules_sha,
        "verification_config_sha256": verification_cfg_sha if source == "config" else None,
    }
    work["verification"] = verification
    work["execution"]["step"] = "verification"
    work["execution"]["next_action"] = action("agent", "Run `cao done` after verification passes and remains fresh." if status == "passed" else "Fix or unblock the verification script(s), then run `cao verify`.")
    work["resume"]["summary"] = "Verification passed." if status == "passed" else f"Verification {status}. See verification.results for script status and reasons."
    save_work(repo, work)
    cao_exit = {"passed": 0, "failed": 1, "blocked": 2, "error": 3}[status]
    print(json.dumps({"ok": status == "passed", "verification": verification, "next": work["execution"]["next_action"]}, ensure_ascii=False, indent=2)); return cao_exit


def sdd_transition(repo: Path, target: str) -> dict[str, Any] | None:
    return run_sdd_command(repo, "transition", [target])


def completion_next_instruction(repo: Path, work: dict[str, Any] | None, completion: dict[str, Any]) -> str:
    """Return a concrete, executable next step for a blocked completion."""
    reason = str(completion.get("reason") or "")
    if not work:
        return "Start a work item first: `cao start --requirement <path>`."
    wid = work["id"]
    if reason == "active blocker exists":
        return f"Resolve the active blocker, then run `cao unblock --work {wid}`."
    if reason == "requirement file missing":
        return f"Restore the requirement file `{work['requirement']['path']}`, then run `cao verify`."
    if reason == "requirement changed since work started":
        path = work["requirement"]["path"]
        return (
            f"The requirement changed after work started. Review and confirm the new requirement; "
            f"if the change is intentional, re-anchor the work item with `cao start --requirement {path} --replace`; "
            f"otherwise restore the previous requirement and run `cao verify`."
        )
    if reason == "verification is missing or stale":
        return "Run `cao verify` after the requirement, code, rules, and verification configuration are stable."
    if reason.startswith("work status is "):
        status = reason.removeprefix("work status is ")
        if status in {"suspended", "todo"}:
            return f"Resume the work item with `cao resume --work {wid} --reactivate`, then continue from its recorded next action."
        if status == "completed":
            return "The work item is already completed; no further action is required."
        return f"Make the work item active before closing it: `cao resume --work {wid} --reactivate`."
    return "Resolve the blocking condition shown above, then run `cao verify` and retry `cao done`."


def cmd_done(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    c = completion_status(repo, work)
    if not c["ready"]:
        print(json.dumps({
            "ok": False,
            "error": "COMPLETION_BLOCKED",
            "reason": c["reason"],
            "next": completion_next_instruction(repo, work, c),
        }, ensure_ascii=False, indent=2)); return 2
    transition = sdd_transition(repo, "done")
    if transition is not None and transition["exit_code"] != 0:
        print(json.dumps({"ok": False, "error": "SDD_TRANSITION_FAILED", "transition": transition}, ensure_ascii=False, indent=2)); return 1
    work["status"] = "completed"; work["completed_at"] = now(); work["execution"]["step"] = "done"; work["execution"]["next_action"] = action("none", "Work item is completed.")
    work["resume"]["summary"] = "Work item completed and verification was fresh."
    save_work(repo, work)
    print(json.dumps({"ok": True, "status": "completed", "work_item": work_summary(work), "sdd_transition": transition}, ensure_ascii=False, indent=2)); return 0


def cmd_context(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    print(render_context(repo, work, session_id=args.session, host=args.host)); return 0


def tool_is_mutating(raw: dict[str, Any]) -> bool:
    tool = str(raw.get("tool_name") or raw.get("tool") or raw.get("name") or "").lower()
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw
    if tool in {"write", "edit", "apply_patch", "str_replace", "insert", "delete", "mv", "move", "write_file"}:
        return True
    if any(k in payload for k in ("new_string", "patch", "content")):
        return True
    if tool in {"bash", "shell", "exec", "command"}:
        cmd = str(payload.get("command") or payload.get("cmd") or "") if isinstance(payload, dict) else str(payload)
        return not readonly_shell(cmd)
    return False


def readonly_shell(command: str) -> bool:
    low = command.strip().lower()
    if not low:
        return True
    prefixes = ("git status", "git diff", "git log", "git show", "git branch", "ls", "dir", "cat ", "type ", "pwd", "find ", "grep ", "rg ", "sed ", "head ", "tail ", "pytest", "python -m unittest", "mvn test", "gradle test", "npm test")
    return low.startswith(prefixes)


def host_event(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd()); raw = json.load(sys.stdin)
    event = str(raw.get("event") or raw.get("hook_event_name") or raw.get("event_name") or "").lower()
    mapping = {"sessionstart": "session_start", "session_start": "session_start", "sessionend": "stop", "session_end": "stop", "userpromptsubmit": "prompt_submit", "before_agent_start": "prompt_submit", "pretooluse": "pre_tool", "tool_call": "pre_tool", "posttooluse": "post_tool", "tool_result": "post_tool", "subagentstart": "subagent_start", "subagentstop": "subagent_stop", "agent_end": "stop"}
    event = mapping.get(event, event)
    index = load_index(repo) if (repo / CONFIG_REL).exists() else empty_index()
    wid = index.get("active_work"); work = load_work(repo, wid) if wid else None
    session_id = str(raw.get("session_id") or raw.get("sessionId") or raw.get("thread_id") or f"{args.host}-default")

    if event in {"session_start", "subagent_start"}:
        if work:
            register_session(repo, work, session_id, args.host)
            result = {"decision": "allow", "additional_context": render_context(repo, work, session_id=session_id, host=args.host), "session_id": session_id}
        else:
            result = {"decision": "allow", "additional_context": "No active CAO work item. Use `cao start --requirement <path>` when a requirement is selected.", "session_id": session_id}
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False)); return 0

    if event == "prompt_submit":
        result = {"decision": "allow", "additional_context": render_context(repo, work, session_id=session_id, host=args.host)}
        if work:
            register_session(repo, work, session_id, args.host)
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False)); return 0

    if event == "pre_tool":
        if tool_is_mutating(raw) and (not work or work.get("status") != "active"):
            result = {"decision": "deny", "reason": "No active work item. Start or resume a requirement before changing production code: `cao start --requirement <path>` or `cao resume --work <id>`."}
        elif tool_is_mutating(raw) and work and not work_sdd_ready(repo, work)[0]:
            provider = ((work.get("sdd") or {}).get("provider") or "auto")
            result = {"decision": "deny", "reason": f"SDD `{provider}` is not installed in the project. Install it before changing production code: `{sdd_install_instruction(provider, args.host) if provider == 'speckit' else sdd_install_instruction(provider)}`."}
        else:
            result = {"decision": "allow"}
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False)); return 0

    if event in {"post_tool", "file_changed"}:
        if work and tool_is_mutating(raw):
            if work.get("verification", {}).get("status") == "passed":
                work["resume"]["summary"] = "Code changed after the last verification; verification is now stale."
            save_work(repo, work)
        print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False)); return 0

    if event in {"stop", "subagent_stop"}:
        if work:
            create_handoff(repo, work, session_id, args.host, reason=event)
        should_gate = bool(work and ((work.get("status") == "active") and str(raw.get("last_assistant_message") or "").strip()))
        if should_gate and re.search(r"(?i)\b(done|finished|completed|ready to merge)\b|已完成|完成|可以合并", str(raw.get("last_assistant_message") or "")):
            c = completion_status(repo, work)
            if not c["ready"]:
                result = {"decision": "deny", "reason": f"Completion is blocked: {c['reason']}. Next: {completion_next_instruction(repo, work, c)}"}
                print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False)); return 0
        print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False)); return 0

    print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False)); return 0


def native_hook_output(host: str, event: str, result: dict[str, Any]) -> dict[str, Any]:
    if host == "pi":
        return result
    hook_name = {"session_start": "SessionStart", "prompt_submit": "UserPromptSubmit", "pre_tool": "PreToolUse", "post_tool": "PostToolUse", "subagent_start": "SubagentStart", "subagent_stop": "SubagentStop", "stop": "Stop"}.get(event, event)
    out: dict[str, Any] = {}
    context = result.get("additional_context")
    if context:
        out["hookSpecificOutput"] = {"hookEventName": hook_name, "additionalContext": context}
    if result.get("decision") == "deny":
        if event == "pre_tool":
            out["hookSpecificOutput"] = {"hookEventName": hook_name, "permissionDecision": "deny", "permissionDecisionReason": result.get("reason") or "Blocked by CAO"}
        else:
            out["decision"] = "block"; out["reason"] = result.get("reason") or "Blocked by CAO"
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cao", description="Coding Agent Orchestrator")
    sub = p.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("init"); sp.add_argument("--repo", default="."); sp.add_argument("--sdd", default=None); sp.add_argument("--verify-script", action="append", default=None, help="Project verification script command; repeat for multiple scripts"); sp.add_argument("--verify-command", action="append", default=None, help=argparse.SUPPRESS); sp.add_argument("--force", action="store_true"); sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("bootstrap"); sp.add_argument("--repo", default="."); sp.add_argument("--host", default=None, choices=["claude-code", "codex", "pi"]); sp.set_defaults(fn=cmd_bootstrap)
    sdd = sub.add_parser("sdd", help="Detect, select, and install a project SDD")
    sdd_sub = sdd.add_subparsers(dest="sdd_command", required=True)
    sp = sdd_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_sdd_list)
    sp = sdd_sub.add_parser("select"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", required=True, choices=sorted(SDD_CATALOG)); sp.set_defaults(fn=cmd_sdd_select)
    sp = sdd_sub.add_parser("install"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--tools", default=None, help="OpenSpec init tool ids, or all/none"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"], default=None, help="Current coding-agent host; used by Spec Kit to select its integration"); sp.add_argument("--integration", default=None, help="Spec Kit integration key, e.g. claude, codex, pi, copilot"); sp.add_argument("--integration-options", default=None, help="Spec Kit integration options passed through as one value"); sp.add_argument("--yes", action="store_true", help="Confirm installation of external/project software"); sp.set_defaults(fn=cmd_sdd_install)
    sp = sub.add_parser("start"); sp.add_argument("--repo", default="."); sp.add_argument("--requirement"); sp.add_argument("--title", default=None, help="Human-readable Work Item title; overrides the requirement filename"); sp.add_argument("--sdd-ref"); sp.add_argument("--sdd-provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--replace", action="store_true"); sp.add_argument("--reopen", action="store_true"); sp.set_defaults(fn=cmd_start)
    artifact = sub.add_parser("artifact", help="Manage SDD artifact references")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    sp = artifact_sub.add_parser("add"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.add_argument("--role"); sp.add_argument("--native-type", dest="native_type"); sp.add_argument("--native-ref", dest="native_ref"); sp.set_defaults(fn=cmd_artifact_add)
    sp = artifact_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_artifact_list)
    sp = artifact_sub.add_parser("remove"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.set_defaults(fn=cmd_artifact_remove)
    sp = artifact_sub.add_parser("refresh"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path"); sp.set_defaults(fn=cmd_artifact_refresh)
    sp = sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_list)
    sp = sub.add_parser("status"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("next"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_next)
    sp = sub.add_parser("resume"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--host", default="unknown"); sp.add_argument("--session"); sp.add_argument("--reactivate", action="store_true"); sp.set_defaults(fn=cmd_resume)
    sp = sub.add_parser("checkpoint"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--step"); sp.add_argument("--next"); sp.add_argument("--next-type", choices=sorted(ACTION_TYPES), default="agent"); sp.add_argument("--command"); sp.add_argument("--summary"); sp.add_argument("--completed"); sp.add_argument("--pending"); sp.set_defaults(fn=cmd_checkpoint)
    sp = sub.add_parser("decision"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--decision", required=True); sp.add_argument("--reason"); sp.add_argument("--scope"); sp.add_argument("--supersedes"); sp.set_defaults(fn=cmd_decision)
    sp = sub.add_parser("block"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--reason", required=True); sp.add_argument("--resolution"); sp.add_argument("--suspend", action="store_true"); sp.set_defaults(fn=cmd_block)
    sp = sub.add_parser("unblock"); sp.add_argument("--repo", default="."); sp.add_argument("--work", required=True); sp.add_argument("--blocker"); sp.set_defaults(fn=cmd_unblock)
    sp = sub.add_parser("handoff"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host"); sp.add_argument("--reason", default="manual checkpoint"); sp.add_argument("--summary"); sp.set_defaults(fn=cmd_handoff)
    sp = sub.add_parser("verify"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--script", action="append", help="One-off verification script command; repeat to run multiple scripts"); sp.add_argument("--command", action="append", help=argparse.SUPPRESS); sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("done"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_done)
    sp = sub.add_parser("context"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host", default="unknown"); sp.set_defaults(fn=cmd_context)
    sp = sub.add_parser("host-event"); sp.add_argument("--repo", default="."); sp.add_argument("--host", default="unknown"); sp.set_defaults(fn=host_event)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.fn(args))
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
