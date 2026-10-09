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

# Keep path selection intentionally small and deterministic. The Agent performs
# the lightweight contextual assessment; CAO only validates the result and
# applies these hard minimums.
EXECUTION_PROFILES = ("light", "standard", "comprehensive")
PROFILE_RANK = {name: index for index, name in enumerate(EXECUTION_PROFILES)}
PATH_RISK_MINIMUM: dict[str, str] = {
    "security": "comprehensive",
    "data_migration": "comprehensive",
    "irreversible_change": "comprehensive",
    "cross_system": "comprehensive",
    "major_ambiguity": "comprehensive",
    "external_contract": "standard",
    "multi_module": "standard",
    "compatibility": "standard",
}
PATH_RISK_GATES: dict[str, list[str]] = {
    "security": ["security_review", "security_verification"],
    "data_migration": ["migration_plan", "rollback_plan", "data_verification"],
    "irreversible_change": ["recovery_plan", "explicit_human_confirmation"],
    "cross_system": ["integration_plan", "integration_verification"],
    "major_ambiguity": ["requirements_clarification"],
    "external_contract": ["contract_review", "compatibility_verification"],
    "multi_module": ["integration_verification"],
    "compatibility": ["compatibility_verification"],
}
PROFILE_BASE_GATES: dict[str, list[str]] = {
    "light": ["targeted_verification"],
    "standard": ["scope_review", "tests", "implementation_review", "verification"],
    "comprehensive": ["requirements_review", "design_review", "implementation_review", "verification"],
}

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
        "path_guidance": {
            "light": "Use the smallest native OpenSpec change workflow supported by the installed version. Do not create unrelated planning artifacts; preserve required native preconditions.",
            "standard": "Create or update the relevant OpenSpec proposal/spec/tasks as needed, apply the change, and verify the affected behavior.",
            "comprehensive": "Use a complete OpenSpec change with explicit proposal, affected specs, design/tasks where supported, staged implementation, and verification. Split independent changes when that reduces risk.",
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
        "path_guidance": {
            "light": "Use BMAD's smallest safe native build/quick-fix path. Do not generate full PRD, architecture, epics, or stories unless the task's risk or uncertainty requires them.",
            "standard": "Use relevant BMAD analysis/specification or build workflows and create only the native artifacts needed to clarify scope, implement, review, and verify the change.",
            "comprehensive": "Use the applicable full BMAD planning path, including requirements, architecture, epics/stories when warranted, then implement in stages with explicit review and verification.",
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
        "path_guidance": {
            "light": "Use the minimal Spec Kit-native implementation path while preserving any required Specify/spec artifact. Omit optional clarification, checklist, or analysis steps when they add no safety value.",
            "standard": "Use the relevant Spec Kit Specify/Plan/Tasks/Implement steps needed for the change; create only the specifications and plans justified by the scope.",
            "comprehensive": "Use a full Spec Kit specification, planning, task decomposition, and staged implementation flow. Apply clarification and analysis steps where uncertainty or integration risk warrants them.",
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
    """Return the storage path for a CAO-internal Session ID."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id)
    return repo / SESSION_DIR / f"{safe}.json"


def stable_session_id(host: str, host_session_id: str) -> str:
    """Build a stable internal ID that cannot collide across different hosts."""
    key = f"{host.strip().lower()}\0{host_session_id}"
    return f"session-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def handoff_history_dir(repo: Path, work_id: str) -> Path:
    """Return the immutable history directory for a Work Item's Handoffs."""
    safe_work_id = re.sub(r"[^A-Za-z0-9._-]+", "-", work_id).strip(".-") or "work"
    return repo / HANDOFF_DIR / safe_work_id


def handoff_record_path(repo: Path, work_id: str, handoff_id: str) -> Path:
    safe_handoff_id = re.sub(r"[^A-Za-z0-9._-]+", "-", handoff_id).strip(".-") or "handoff"
    return handoff_history_dir(repo, work_id) / f"{safe_handoff_id}.json"


def _handoff_time_key(handoff: dict[str, Any]) -> tuple[float, int, str]:
    """Normalize timestamps and per-work sequence numbers for stable sorting."""
    value = handoff.get("created_at", 0)
    timestamp = 0.0
    if isinstance(value, (int, float)):
        timestamp = float(value)
    else:
        try:
            timestamp = float(str(value))
        except (TypeError, ValueError):
            try:
                from datetime import datetime
                timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                timestamp = 0.0
    try:
        sequence = int(handoff.get("sequence", 0))
    except (TypeError, ValueError):
        sequence = 0
    return timestamp, sequence, str(handoff.get("id") or "")


def list_handoff_records(repo: Path, work_id: str | None = None) -> list[dict[str, Any]]:
    """Read immutable Handoff records from the per-Work-Item history layout."""
    root = repo / HANDOFF_DIR
    if not root.is_dir():
        return []

    if work_id:
        paths = list(handoff_history_dir(repo, work_id).glob("*.json"))
    else:
        paths = [path for history_dir in root.iterdir() if history_dir.is_dir()
                 for path in history_dir.glob("*.json")]

    records: dict[str, dict[str, Any]] = {}
    for path in paths:
        record = load_json(path, None)
        if not isinstance(record, dict):
            continue
        record_work_id = record.get("work_item")
        if not isinstance(record_work_id, str) or (work_id and record_work_id != work_id):
            continue
        handoff_id = record.get("id")
        if not isinstance(handoff_id, str) or not handoff_id:
            continue
        records[handoff_id] = record
    return sorted(records.values(), key=_handoff_time_key, reverse=True)


def latest_handoff(repo: Path, work_id: str) -> dict[str, Any] | None:
    """Return the newest immutable Handoff record for a Work Item."""
    records = list_handoff_records(repo, work_id)
    return records[0] if records else None

def find_handoff(repo: Path, handoff_id: str) -> dict[str, Any] | None:
    return next((item for item in list_handoff_records(repo) if item.get("id") == handoff_id), None)


def empty_index() -> dict[str, Any]:
    return {"version": 1, "active_work": None, "work_items": []}


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


def is_current_session_record(raw: Any) -> bool:
    """Return whether a record satisfies the current Session data contract.

    Session persistence intentionally has no legacy-record normalization or
    migration path. The host-native ID and Host together determine the stable
    internal ID, and every required field must be present in the current shape.
    """
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        return False
    required = {
        "id", "host", "host_session_id", "work_item_id", "kind", "parent_session_id",
        "status", "started_at", "last_event", "last_event_at", "updated_at",
        "ended_at", "end_reason", "last_handoff_id",
    }
    if not required.issubset(raw):
        return False
    host = raw.get("host")
    native_id = raw.get("host_session_id")
    if not isinstance(host, str) or not host or not isinstance(native_id, str) or not native_id:
        return False
    if raw.get("id") != stable_session_id(host, native_id):
        return False
    if raw.get("kind") not in {"primary", "subagent"} or raw.get("status") not in {"active", "ended"}:
        return False
    if raw.get("work_item_id") is not None and not isinstance(raw.get("work_item_id"), str):
        return False
    if raw.get("parent_session_id") is not None and not isinstance(raw.get("parent_session_id"), str):
        return False
    for key in ("started_at", "last_event_at", "updated_at"):
        if not isinstance(raw.get(key), int) or isinstance(raw.get(key), bool) or raw[key] < 0:
            return False
    if raw.get("ended_at") is not None and (not isinstance(raw.get("ended_at"), int) or isinstance(raw.get("ended_at"), bool) or raw["ended_at"] < 0):
        return False
    if not isinstance(raw.get("last_event"), str) or not raw["last_event"]:
        return False
    if raw.get("end_reason") is not None and not isinstance(raw.get("end_reason"), str):
        return False
    if raw.get("last_handoff_id") is not None and not isinstance(raw.get("last_handoff_id"), str):
        return False
    return True


def _session_files(repo: Path) -> list[Path]:
    return sorted((repo / SESSION_DIR).glob("*.json")) if (repo / SESSION_DIR).exists() else []


def _read_current_session(path: Path) -> dict[str, Any] | None:
    raw = load_json(path, None)
    if not is_current_session_record(raw):
        return None
    if path.name != f"{raw['id']}.json":
        return None
    return raw


def load_session(repo: Path, session_ref: str | None, host: str | None = None) -> dict[str, Any] | None:
    """Find a current-schema Session by internal ID or host-native ID.

    Files from older Session layouts are ignored; this model deliberately does
    not migrate previous paths or fields.
    """
    if not session_ref:
        return None
    if host == "unknown":
        host = None
    direct = _read_current_session(session_path(repo, session_ref))
    if direct:
        if host and direct.get("host") not in {host, "unknown"}:
            return None
        return direct

    candidates = [
        session for path in _session_files(repo)
        if (session := _read_current_session(path)) is not None
        and (session.get("id") == session_ref or session.get("host_session_id") == session_ref)
        and (not host or session.get("host") in {host, "unknown"})
    ]
    if not candidates:
        return None
    if host:
        exact = [session for session in candidates if session.get("host") == host]
        if exact:
            candidates = exact
    if len(candidates) > 1 and not host:
        raise RuntimeError(f"Session reference {session_ref!r} is ambiguous across hosts; supply --host.")
    return candidates[0]


def save_session(repo: Path, session: dict[str, Any]) -> None:
    session["schema_version"] = 2
    session["updated_at"] = now()
    save_json(session_path(repo, session["id"]), session)


def session_activity(session: dict[str, Any], stale_after_minutes: int = 30, current_time: int | None = None) -> str:
    if session.get("status") == "ended":
        return "ended"
    current_time = now() if current_time is None else current_time
    try:
        last_event_at = int(session.get("last_event_at") or 0)
    except (TypeError, ValueError):
        last_event_at = 0
    if not last_event_at or current_time - last_event_at > max(1, stale_after_minutes) * 60:
        return "stale"
    return "recent"


def list_sessions(repo: Path) -> list[dict[str, Any]]:
    """List current-schema Session records without migrating older formats."""
    found: dict[str, dict[str, Any]] = {}
    for path in _session_files(repo):
        session = _read_current_session(path)
        if session is not None:
            found[session["id"]] = session
    return sorted(found.values(), key=lambda s: (s.get("last_event_at") or 0, s.get("id") or ""), reverse=True)


def update_session_event(
    repo: Path,
    session_ref: str,
    host: str,
    event: str,
    work: dict[str, Any] | None = None,
    *,
    kind: str = "primary",
    parent_session_id: str | None = None,
) -> dict[str, Any]:
    session = load_session(repo, session_ref, host=host)
    if session is None:
        return register_session(repo, work, session_ref, host, event=event, kind=kind, parent_session_id=parent_session_id)
    if session.get("host") == "unknown" and host and host != "unknown":
        # Re-register to re-key the stored file using the now-known Host identity.
        return register_session(
            repo,
            work,
            session_ref,
            host,
            event=event,
            kind=kind if kind else session.get("kind", "primary"),
            parent_session_id=parent_session_id if parent_session_id is not None else session.get("parent_session_id"),
        )
    if host and host != "unknown":
        session["host"] = host
    session["work_item_id"] = work.get("id") if work else session.get("work_item_id")
    if session.get("status") != "ended" and event not in {"stop", "session_end", "subagent_stop"}:
        session["status"] = "active"
    session["last_event"] = event
    session["last_event_at"] = now()
    if event in {"session_start", "subagent_start"} and session.get("status") == "ended":
        session["started_at"] = now()
        session["ended_at"] = None
        session["end_reason"] = None
    save_session(repo, session)
    if work:
        work.setdefault("resume", {})["last_agent"] = host
        save_work(repo, work)
    return session


def touch_session(repo: Path, session_ref: str, event: str, work: dict[str, Any] | None = None, host: str | None = None) -> dict[str, Any] | None:
    session = load_session(repo, session_ref, host=host)
    if not session:
        return None
    session["last_event"] = event
    session["last_event_at"] = now()
    if work:
        session["work_item_id"] = work.get("id")
    save_session(repo, session)
    return session


def end_session(repo: Path, session_ref: str, host: str | None, reason: str, event: str = "stop") -> dict[str, Any] | None:
    session = load_session(repo, session_ref, host=host)
    if not session:
        return None
    session["status"] = "ended"
    session["last_event"] = event
    session["last_event_at"] = now()
    session["ended_at"] = now()
    session["end_reason"] = reason
    save_session(repo, session)
    return session


def cmd_sessions_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    sessions = list_sessions(repo)
    if args.host:
        sessions = [s for s in sessions if s.get("host") == args.host]
    if args.work:
        sessions = [s for s in sessions if s.get("work_item_id") == args.work]
    if args.status:
        sessions = [s for s in sessions if s.get("status") == args.status]
    out = []
    for session in sessions:
        item = dict(session)
        item["activity"] = session_activity(session, args.stale_after_minutes)
        out.append(item)
    print(json.dumps({"ok": True, "sessions": out, "stale_after_minutes": args.stale_after_minutes}, ensure_ascii=False, indent=2))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    session = load_session(repo, args.id, host=args.host)
    if not session:
        print(json.dumps({"ok": False, "error": "SESSION_NOT_FOUND", "id": args.id}, ensure_ascii=False, indent=2))
        return 2
    out = dict(session)
    out["activity"] = session_activity(session, args.stale_after_minutes)
    print(json.dumps({"ok": True, "session": out}, ensure_ascii=False, indent=2))
    return 0


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
        "execution_profile": (work.get("path_assessment") or {}).get("profile"),
        "sdd": work.get("sdd"),
        "blockers": work.get("blockers") or [],
        "decision_count": len(work.get("decisions") or []),
        "verification": (work.get("verification") or {}).get("status"),
        "last_agent": work.get("resume", {}).get("last_agent"),
    }


def normalize_path_risk_flag(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def path_guidance_for_provider(provider: str, profile: str) -> str:
    provider_meta = SDD_CATALOG.get(provider) or {}
    guidance = provider_meta.get("path_guidance") or {}
    return str(guidance.get(profile) or (
        "Use the selected SDD's smallest native workflow that satisfies the required gates. "
        "Do not create duplicate artifacts or bypass provider-native prerequisites."
    ))


def build_path_assessment(
    *,
    requested_profile: str,
    reason: str,
    risk_flags: list[str],
    provider: str,
    requirement_sha256: str,
    questions: list[str] | None = None,
    assessed_by: str = "agent",
) -> dict[str, Any]:
    """Validate an Agent's lightweight path assessment and enforce hard minimums."""
    if requested_profile not in PROFILE_RANK:
        raise ValueError(f"unsupported execution profile: {requested_profile}")
    if not reason.strip():
        raise ValueError("path assessment reason must not be empty")

    normalized: list[str] = []
    for flag in risk_flags:
        value = normalize_path_risk_flag(flag)
        if value not in PATH_RISK_MINIMUM:
            allowed = ", ".join(sorted(PATH_RISK_MINIMUM))
            raise ValueError(f"unknown path risk flag `{flag}`; expected one of: {allowed}")
        if value not in normalized:
            normalized.append(value)

    open_questions = [str(q).strip() for q in (questions or []) if str(q).strip()]
    # Open questions in this model are material questions that affect the
    # expected behavior. Never let an assessment record questions while
    # accidentally leaving the implementation gate open.
    if open_questions and "major_ambiguity" not in normalized:
        normalized.append("major_ambiguity")

    effective_profile = requested_profile
    escalation_reasons: list[str] = []
    for flag in normalized:
        minimum = PATH_RISK_MINIMUM[flag]
        if PROFILE_RANK[minimum] > PROFILE_RANK[effective_profile]:
            escalation_reasons.append(f"risk flag `{flag}` requires at least `{minimum}`")
            effective_profile = minimum

    gates = list(PROFILE_BASE_GATES[effective_profile])
    for flag in normalized:
        for gate in PATH_RISK_GATES.get(flag, []):
            if gate not in gates:
                gates.append(gate)

    return {
        "policy_version": 1,
        "requested_profile": requested_profile,
        "profile": effective_profile,
        "reason": reason.strip(),
        "risk_flags": normalized,
        "escalation_reasons": escalation_reasons,
        "required_gates": gates,
        "provider": provider,
        "provider_guidance": path_guidance_for_provider(provider, effective_profile),
        "open_questions": open_questions,
        "assessed_by": assessed_by.strip() or "agent",
        "assessed_at": now(),
        "requirement_sha256": requirement_sha256,
    }


def path_assessment_state(repo: Path, work: dict[str, Any]) -> dict[str, Any]:
    """Return freshness and blocking details for a Work Item's path assessment."""
    assessment = work.get("path_assessment")
    if not isinstance(assessment, dict):
        return {"ready": False, "reason": "execution path has not been assessed", "assessment": None}
    if assessment.get("policy_version") != 1:
        return {"ready": False, "reason": "execution path assessment uses an unsupported policy version", "assessment": assessment}
    profile = assessment.get("profile")
    if profile not in PROFILE_RANK:
        return {"ready": False, "reason": "execution path assessment is invalid", "assessment": assessment}
    work_provider = str((work.get("sdd") or {}).get("provider") or "auto")
    if assessment.get("provider") != work_provider:
        return {"ready": False, "reason": "execution path assessment is stale because the SDD provider changed", "assessment": assessment}
    flags = assessment.get("risk_flags") or []
    if not isinstance(flags, list) or any(flag not in PATH_RISK_MINIMUM for flag in flags):
        return {"ready": False, "reason": "execution path assessment is invalid", "assessment": assessment}
    required_profile = max((PATH_RISK_MINIMUM[flag] for flag in flags), key=lambda item: PROFILE_RANK[item], default="light")
    if PROFILE_RANK[profile] < PROFILE_RANK[required_profile]:
        return {"ready": False, "reason": "execution path assessment violates a required risk minimum", "assessment": assessment}
    requirement = work.get("requirement") or {}
    req_path = repo / str(requirement.get("path") or "")
    if not req_path.is_file():
        return {"ready": False, "reason": "requirement file missing", "assessment": assessment}
    current_sha = sha256_file(req_path)
    if current_sha != requirement.get("sha256"):
        return {"ready": False, "reason": "requirement changed since work started", "assessment": assessment}
    if current_sha != assessment.get("requirement_sha256"):
        return {"ready": False, "reason": "execution path assessment is stale", "assessment": assessment}
    if "major_ambiguity" in flags or assessment.get("open_questions"):
        return {"ready": False, "reason": "major requirement ambiguity requires clarification", "assessment": assessment}
    return {"ready": True, "reason": None, "assessment": assessment}


def path_assessment_instruction(work: dict[str, Any] | None = None) -> str:
    wid = (work or {}).get("id")
    work_arg = f" --work {wid}" if wid else ""
    return (
        "Perform a lightweight scope/risk assessment, choose light, standard, or comprehensive, "
        "then record it with `cao path assess --profile <profile> --reason \"<short reason>\"" +
        " [--risk-flag <flag>]" + work_arg + "`. Do not build a separate evidence model or scorecard."
    )


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
    path_state = path_assessment_state(repo, work)
    if not path_state["ready"]:
        return {"ready": False, "reason": path_state["reason"]}
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
    handoff = latest_handoff(repo, work["id"]) or {}
    return {
        "work_item": work_summary(work),
        "requirement": work.get("requirement"),
        "execution": work.get("execution"),
        "path_assessment": work.get("path_assessment"),
        "path_assessment_state": path_assessment_state(repo, work),
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
        "## Minimum-Safe Execution Path",
        json.dumps(p.get("path_assessment") or {"status": "required", "next": path_assessment_instruction(work)}, ensure_ascii=False),
        f"Path assessment ready: {p['path_assessment_state'].get('ready')}",
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


def register_session(
    repo: Path,
    work: dict[str, Any] | None,
    session_id: str,
    host: str,
    *,
    event: str = "session_start",
    kind: str = "primary",
    parent_session_id: str | None = None,
) -> dict[str, Any]:
    """Create or reattach a Session using the host-native ID as its lookup key."""
    ts = now()
    existing = load_session(repo, session_id, host=host)
    previous_internal_id = existing.get("id") if existing else None
    effective_host = existing.get("host") if host == "unknown" and existing else host
    effective_host = effective_host or "unknown"
    # If an earlier adapter registered the Session before it knew the Host,
    # re-key it once the concrete Host arrives so the ID invariant stays true.
    if existing and existing.get("host") == "unknown" and effective_host != "unknown":
        internal_id = stable_session_id(effective_host, session_id)
    else:
        internal_id = existing["id"] if existing else stable_session_id(effective_host, session_id)
    if existing and existing.get("status") == "ended" and event in {"session_start", "subagent_start", "resume"}:
        started_at = ts
    else:
        started_at = (existing or {}).get("started_at", ts)
    session = {
        "schema_version": 2,
        "id": internal_id,
        "host": effective_host,
        "host_session_id": session_id,
        "work_item_id": work.get("id") if work else (existing or {}).get("work_item_id"),
        "kind": kind if kind in {"primary", "subagent"} else (existing or {}).get("kind", "primary"),
        "parent_session_id": parent_session_id if parent_session_id is not None else (existing or {}).get("parent_session_id"),
        "status": "active",
        "started_at": started_at,
        "last_event": event,
        "last_event_at": ts,
        "updated_at": ts,
        "ended_at": None,
        "end_reason": None,
        "last_handoff_id": (existing or {}).get("last_handoff_id"),
    }
    save_session(repo, session)
    if previous_internal_id and previous_internal_id != internal_id:
        previous_path = session_path(repo, previous_internal_id)
        if previous_path.exists():
            try:
                previous_path.unlink()
            except OSError:
                pass
    if work:
        work.setdefault("resume", {})["last_agent"] = effective_host
        save_work(repo, work)
    return session


def create_handoff(repo: Path, work: dict[str, Any], session_id: str | None, host: str | None, reason: str = "session stop") -> dict[str, Any]:
    session = load_session(repo, session_id, host=host) if session_id else None
    existing_records = list_handoff_records(repo, work["id"])
    sequence = max((int(item.get("sequence", 0) or 0) for item in existing_records), default=0) + 1
    handoff = {
        "version": 1,
        "sequence": sequence,
        "id": new_id("handoff"),
        "work_item": work["id"],
        "session_id": session.get("id") if session else None,
        "host_session_id": session.get("host_session_id") if session else session_id,
        "host": host if host and host != "unknown" else (session or {}).get("host"),
        "reason": reason,
        "created_at": now(),
        "step": work.get("execution", {}).get("step"),
        "next_action": work.get("execution", {}).get("next_action"),
        "path_assessment": work.get("path_assessment"),
        "summary": work.get("resume", {}).get("summary"),
        "completed": work.get("resume", {}).get("completed", []),
        "pending": work.get("resume", {}).get("pending", []),
        "decisions": work.get("decisions", [])[-8:],
        "blockers": work.get("blockers", []),
        "verification": work.get("verification"),
    }
    # Each Handoff is an independent immutable record. There is no flat latest
    # projection; readers derive the latest record from this history directory.
    save_json(handoff_record_path(repo, work["id"], handoff["id"]), handoff)
    if session:
        session["last_handoff_id"] = handoff["id"]
        save_session(repo, session)
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
        elif work.get("status") == "completed":
            next_out["next"] = action("none", "Work item is completed.")
        elif work.get("blockers"):
            next_out["next"] = action("human", "Resolve the active blocker before continuing.")
        elif not path_assessment_state(repo, work)["ready"]:
            path_state = path_assessment_state(repo, work)
            if path_state["reason"] == "major requirement ambiguity requires clarification":
                next_out["next"] = action("human", "Clarify the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`.")
            elif path_state["reason"] == "requirement changed since work started":
                next_out["next"] = action("human", completion_next_instruction(repo, work, {"ready": False, "reason": path_state["reason"]}))
            else:
                next_out["next"] = action("agent", path_assessment_instruction(work))
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
        "execution": {"step": "path-assessment", "next_action": action("agent", path_assessment_instruction())},
        "path_assessment": None,
        "resume": {"summary": "Work item created. Requirement is ready for intake.", "completed": [], "pending": []},
        "decisions": [],
        "blockers": [],
        "verification": None,
        "created_at": now(),
    }
    work["status"] = "active"
    work["started_at"] = work.get("started_at") or now()
    work["requirement"] = requirement_evidence(repo, p)
    if not isinstance(work.get("path_assessment"), dict):
        work.setdefault("execution", {})["step"] = "path-assessment"
        work.setdefault("execution", {})["next_action"] = action("agent", path_assessment_instruction(work))
    if args.title:
        work["title"] = args.title
    work["sdd"]["provider"] = provider
    if args.sdd_ref:
        work["sdd"]["native_ref"] = args.sdd_ref
    save_work(repo, work)
    native = run_sdd_command(repo, "inspect", [rel(p, repo)])
    print(json.dumps({"ok": True, "action": "ASSESS_PATH", "work_item": work_summary(work), "next_action": work.get("execution", {}).get("next_action"), "sdd": native}, ensure_ascii=False, indent=2)); return 0


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


def cmd_path_assess(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2))
        return 2

    requirement = work.get("requirement") or {}
    req_path = repo / str(requirement.get("path") or "")
    if not req_path.is_file():
        print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING", "path": requirement.get("path")}, ensure_ascii=False, indent=2))
        return 2
    current_sha = sha256_file(req_path)
    if current_sha != requirement.get("sha256"):
        print(json.dumps({
            "ok": False,
            "error": "REQUIREMENT_CHANGED",
            "reason": "Review the requirement change and re-anchor the Work Item before reassessing its execution path.",
            "next": f"Review `{requirement.get('path')}`; if intentional, run `cao start --requirement {requirement.get('path')} --replace`.",
        }, ensure_ascii=False, indent=2))
        return 2

    provider = str((work.get("sdd") or {}).get("provider") or "auto")
    assessment = build_path_assessment(
        requested_profile=args.profile,
        reason=args.reason,
        risk_flags=args.risk_flag or [],
        provider=provider,
        requirement_sha256=current_sha,
        questions=args.question or [],
        assessed_by=args.assessed_by,
    )
    work["path_assessment"] = assessment
    execution = work.setdefault("execution", {})
    if "major_ambiguity" in assessment["risk_flags"]:
        execution["step"] = "clarification-required"
        questions = "; ".join(assessment["open_questions"])
        detail = f" Open questions: {questions}." if questions else ""
        execution["next_action"] = action(
            "human",
            "Clarify the material requirement ambiguity with the user, then rerun `cao path assess` without `--risk-flag major-ambiguity`. Do not implement until clarified." + detail,
            profile=assessment["profile"],
        )
    else:
        execution["step"] = "path-selected"
        gate_text = ", ".join(assessment["required_gates"])
        execution["next_action"] = action(
            "agent",
            f"Apply the {assessment['profile']} path using {provider}'s native workflow. {assessment['provider_guidance']} Required gates: {gate_text}. Reassess only if the scope, risk, or requirement materially changes.",
            profile=assessment["profile"],
            provider=provider,
        )
    save_work(repo, work)
    print(json.dumps({"ok": True, "work_item": work_summary(work), "path_assessment": assessment, "next_action": execution["next_action"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_path_show(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "ok": True,
        "work_item": work_summary(work),
        "path_assessment": work.get("path_assessment"),
        "state": path_assessment_state(repo, work),
    }, ensure_ascii=False, indent=2))
    return 0


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
    elif not path_assessment_state(repo, work)["ready"]:
        path_state = path_assessment_state(repo, work)
        if path_state["reason"] == "major requirement ambiguity requires clarification":
            nxt = action("human", "Clarify the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`.")
        elif path_state["reason"] == "requirement changed since work started":
            nxt = action("human", completion_next_instruction(repo, work, {"ready": False, "reason": path_state["reason"]}))
        else:
            nxt = action("agent", path_assessment_instruction(work))
        out = {"ok": True, "action": nxt, "work_item": work_summary(work)}
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
        touch_session(repo, args.session, "checkpoint", work, host=args.host)
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


def cmd_handoff_list(args: argparse.Namespace, repo: Path) -> int:
    if args.limit < 1:
        print(json.dumps({"ok": False, "error": "INVALID_LIMIT", "reason": "--limit must be >= 1"}, ensure_ascii=False, indent=2))
        return 2
    records = list_handoff_records(repo, args.work)[:args.limit]
    summaries = [{
        "id": record.get("id"),
        "sequence": record.get("sequence"),
        "work_item": record.get("work_item"),
        "host": record.get("host"),
        "session_id": record.get("session_id"),
        "reason": record.get("reason"),
        "created_at": record.get("created_at"),
        "step": record.get("step"),
        "summary": record.get("summary"),
    } for record in records]
    print(json.dumps({"ok": True, "count": len(summaries), "handoffs": summaries}, ensure_ascii=False, indent=2))
    return 0


def cmd_handoff_show(args: argparse.Namespace, repo: Path) -> int:
    if not args.handoff_id:
        print(json.dumps({"ok": False, "error": "HANDOFF_ID_REQUIRED", "next": "cao handoff show --id <handoff-id>"}, ensure_ascii=False, indent=2))
        return 2
    record = find_handoff(repo, args.handoff_id)
    if not record:
        print(json.dumps({"ok": False, "error": "HANDOFF_NOT_FOUND", "id": args.handoff_id}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, "handoff": record}, ensure_ascii=False, indent=2))
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    if args.handoff_action == "list":
        return cmd_handoff_list(args, repo)
    if args.handoff_action == "show":
        return cmd_handoff_show(args, repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    save_work(repo, work)
    handoff = create_handoff(repo, work, args.session, args.host, args.reason)
    if args.end_session and args.session:
        end_session(repo, args.session, args.host, args.reason, event="stop")
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
    session = register_session(repo, work, session_id, args.host or "unknown", event="resume")
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
    if reason in {"execution path has not been assessed", "execution path assessment is stale"}:
        return f"Review the requirement and current scope, then record or refresh the minimum-safe-path assessment: `cao path assess --profile <light|standard|comprehensive> --reason \"<short reason>\" --work {wid}`."
    if reason == "major requirement ambiguity requires clarification":
        return "Ask the user to resolve the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`. Do not implement while the ambiguity remains unresolved."
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


def cmd_path(args: argparse.Namespace) -> int:
    if args.path_command == "assess":
        return cmd_path_assess(args)
    if args.path_command == "show":
        return cmd_path_show(args)
    raise ValueError(f"unsupported path command: {args.path_command}")


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
    if low.startswith(prefixes):
        return True
    # CAO governance commands mutate CAO state, not product code. Permit these
    # single commands through the code-mutation hook so an unassessed Work Item
    # can run `cao path assess` and other required orchestration actions.
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or any(token in {";", "&&", "||", "|", ">", ">>", "<"} for token in tokens):
        return False
    command_tokens = tokens
    if tokens[0] == "python" or re.fullmatch(r"python3(?:\.\d+)?", tokens[0]):
        script_index = next((i for i, token in enumerate(tokens[1:], 1) if Path(token).as_posix().endswith("scripts/cao.py")), None)
        if script_index is None or script_index + 1 >= len(tokens):
            return False
        command_tokens = tokens[script_index + 1:]
    elif tokens[0] == "./cao":
        command_tokens = tokens[1:]
    elif tokens[0] == "cao":
        command_tokens = tokens[1:]
    else:
        return False
    safe_control_commands = {
        "init", "bootstrap", "sdd", "start", "path", "list", "status", "next", "resume",
        "checkpoint", "decision", "block", "unblock", "handoff", "sessions", "verify", "done",
        "context", "artifact", "host-event",
    }
    return bool(command_tokens and command_tokens[0] in safe_control_commands)


def host_event(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd())
    raw = json.load(sys.stdin)
    event = str(raw.get("event") or raw.get("hook_event_name") or raw.get("event_name") or "").lower()
    mapping = {
        "sessionstart": "session_start", "session_start": "session_start",
        "sessionend": "stop", "session_end": "stop",
        "userpromptsubmit": "prompt_submit", "before_agent_start": "prompt_submit",
        "pretooluse": "pre_tool", "tool_call": "pre_tool",
        "posttooluse": "post_tool", "tool_result": "post_tool",
        "subagentstart": "subagent_start", "subagentstop": "subagent_stop",
        "agent_end": "stop",
    }
    event = mapping.get(event, event)
    index = load_index(repo) if (repo / CONFIG_REL).exists() else empty_index()
    wid = index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if event in {"subagent_start", "subagent_stop"}:
        native_session_value = raw.get("subagent_id") or raw.get("agent_id") or raw.get("session_id") or raw.get("sessionId") or raw.get("thread_id")
    else:
        native_session_value = raw.get("session_id") or raw.get("sessionId") or raw.get("thread_id") or raw.get("agent_id") or raw.get("subagent_id")
    host_session_id = str(native_session_value or f"{args.host}-default")
    kind = "subagent" if event in {"subagent_start", "subagent_stop"} else "primary"
    parent_native = raw.get("parent_session_id") or raw.get("parentSessionId") or raw.get("parent_session")
    parent_id = stable_session_id(args.host, str(parent_native)) if parent_native else None

    if event in {"session_start", "subagent_start"}:
        session = register_session(repo, work, host_session_id, args.host, event=event, kind=kind, parent_session_id=parent_id)
    else:
        session = update_session_event(repo, host_session_id, args.host, event, work, kind=kind, parent_session_id=parent_id)

    if event in {"session_start", "subagent_start"}:
        context = render_context(repo, work, session_id=host_session_id, host=args.host) if work else "No active CAO work item. Use `cao start --requirement <path>` when a requirement is selected."
        result = {"decision": "allow", "additional_context": context, "session_id": host_session_id}
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False))
        return 0

    if event == "prompt_submit":
        result = {"decision": "allow", "additional_context": render_context(repo, work, session_id=host_session_id, host=args.host)}
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False))
        return 0

    if event == "pre_tool":
        if tool_is_mutating(raw) and (not work or work.get("status") != "active"):
            result = {"decision": "deny", "reason": "No active work item. Start or resume a requirement before changing production code: `cao start --requirement <path>` or `cao resume --work <id>`."}
        elif tool_is_mutating(raw) and work and not work_sdd_ready(repo, work)[0]:
            provider = ((work.get("sdd") or {}).get("provider") or "auto")
            install = sdd_install_instruction(provider, args.host) if provider == "speckit" else sdd_install_instruction(provider)
            result = {"decision": "deny", "reason": f"SDD `{provider}` is not installed in the project. Install it before changing production code: `{install}`."}
        elif tool_is_mutating(raw) and work and not path_assessment_state(repo, work)["ready"]:
            path_state = path_assessment_state(repo, work)
            if path_state["reason"] == "requirement changed since work started":
                instruction = completion_next_instruction(repo, work, {"ready": False, "reason": path_state["reason"]})
            elif path_state["reason"] == "major requirement ambiguity requires clarification":
                instruction = "Clarify the material requirement ambiguity with the user, then rerun `cao path assess` without `--risk-flag major-ambiguity`. Do not implement while it remains unresolved."
            else:
                instruction = f"Assess scope/risk with `cao path assess --profile <light|standard|comprehensive> --reason \"<short reason>\" --work {work['id']}`."
            result = {"decision": "deny", "reason": f"Execution path is not ready ({path_state['reason']}). {instruction}"}
        else:
            result = {"decision": "allow"}
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False))
        return 0

    if event in {"post_tool", "file_changed"}:
        if work and tool_is_mutating(raw):
            if work.get("verification", {}).get("status") == "passed":
                work.setdefault("resume", {})["summary"] = "Code changed after the last verification; verification is now stale."
            save_work(repo, work)
        print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False))
        return 0

    if event == "subagent_stop":
        end_session(repo, host_session_id, args.host, reason="subagent stopped", event="subagent_stop")
        print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False))
        return 0

    if event == "stop":
        should_gate = bool(work and work.get("status") == "active" and str(raw.get("last_assistant_message") or "").strip())
        if should_gate and re.search(r"(?i)\b(done|finished|completed|ready to merge)\b|已完成|完成|可以合并", str(raw.get("last_assistant_message") or "")):
            completion = completion_status(repo, work)
            if not completion["ready"]:
                # The host denied the stop, so the Session remains active.
                touch_session(repo, host_session_id, "stop_blocked", work, host=args.host)
                result = {"decision": "deny", "reason": f"Completion is blocked: {completion['reason']}. Next: {completion_next_instruction(repo, work, completion)}"}
                print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False))
                return 0
        if work and session.get("kind") != "subagent":
            create_handoff(repo, work, host_session_id, args.host, reason=event)
        end_session(repo, host_session_id, args.host, reason=event, event="stop")
        print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False))
        return 0

    print(json.dumps(native_hook_output(args.host, event, {"decision": "allow"}), ensure_ascii=False))
    return 0


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
    path = sub.add_parser("path", help="Assess and inspect the minimum-safe execution path")
    path_sub = path.add_subparsers(dest="path_command", required=True)
    sp = path_sub.add_parser("assess", help="Record an Agent's lightweight scope/risk assessment")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.add_argument("--profile", required=True, choices=EXECUTION_PROFILES)
    sp.add_argument("--reason", required=True, help="Short explanation for the selected profile")
    risk_cli_names = sorted(set(PATH_RISK_MINIMUM) | {name.replace("_", "-") for name in PATH_RISK_MINIMUM})
    sp.add_argument("--risk-flag", action="append", default=[], choices=risk_cli_names, help="Risk requiring a minimum profile; repeat as needed")
    sp.add_argument("--question", action="append", default=[], help="Unresolved requirement question; repeat as needed")
    sp.add_argument("--assessed-by", default="agent", help="Who performed the assessment")
    sp.set_defaults(fn=cmd_path_assess)
    sp = path_sub.add_parser("show", help="Show the current Work Item's path assessment")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.set_defaults(fn=cmd_path_show)
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
    sp = sub.add_parser("checkpoint"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--step"); sp.add_argument("--next"); sp.add_argument("--next-type", choices=sorted(ACTION_TYPES), default="agent"); sp.add_argument("--command"); sp.add_argument("--summary"); sp.add_argument("--completed"); sp.add_argument("--pending"); sp.set_defaults(fn=cmd_checkpoint)
    sp = sub.add_parser("decision"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--decision", required=True); sp.add_argument("--reason"); sp.add_argument("--scope"); sp.add_argument("--supersedes"); sp.set_defaults(fn=cmd_decision)
    sp = sub.add_parser("block"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--reason", required=True); sp.add_argument("--resolution"); sp.add_argument("--suspend", action="store_true"); sp.set_defaults(fn=cmd_block)
    sp = sub.add_parser("unblock"); sp.add_argument("--repo", default="."); sp.add_argument("--work", required=True); sp.add_argument("--blocker"); sp.set_defaults(fn=cmd_unblock)
    sp = sub.add_parser("handoff", help="Create or inspect Handoff records"); sp.add_argument("handoff_action", nargs="?", choices=["list", "show"]); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host"); sp.add_argument("--reason", default="manual checkpoint"); sp.add_argument("--summary"); sp.add_argument("--end-session", action="store_true", help="End the referenced Session after creating the handoff"); sp.add_argument("--id", dest="handoff_id"); sp.add_argument("--limit", type=int, default=20); sp.set_defaults(fn=cmd_handoff)
    sessions = sub.add_parser("sessions", help="Inspect Agent Sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    sp = sessions_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--work"); sp.add_argument("--status", choices=["active", "ended"]); sp.add_argument("--stale-after-minutes", type=int, default=30); sp.set_defaults(fn=cmd_sessions_list)
    sp = sessions_sub.add_parser("show"); sp.add_argument("--repo", default="."); sp.add_argument("--id", required=True); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--stale-after-minutes", type=int, default=30); sp.set_defaults(fn=cmd_sessions_show)
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
