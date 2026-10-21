"""Communication language and provider-native language configuration."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from scripts.cao_defaults import CONFIG_REL, SDD_CATALOG
from scripts.cao_runtime import (
    config,
    ensure_initialized,
    load_json,
    locked_command,
    locked_repo,
    repo_root,
    save_json,
)


LANGUAGE_LABELS = {
    "zh-cn": "Simplified Chinese",
    "zh-hans": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "zh-hant": "Traditional Chinese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "ja": "Japanese",
    "ja-jp": "Japanese",
    "ko": "Korean",
    "ko-kr": "Korean",
    "es": "Spanish",
    "es-es": "Spanish",
    "fr": "French",
    "fr-fr": "French",
    "de": "German",
    "de-de": "German",
    "pt": "Portuguese",
    "pt-br": "Brazilian Portuguese",
    "ru": "Russian",
}


LANGUAGE_POLICY_START = "CAO_LANGUAGE_POLICY_START"


LANGUAGE_POLICY_END = "CAO_LANGUAGE_POLICY_END"


@locked_repo
def ensure_language_preference(repo: Path) -> str:
    """Persist the default user-language preference into existing CAO configs."""
    path = repo / CONFIG_REL
    if not path.exists():
        return "zh-CN"
    raw = load_json(path, {})
    language_cfg = raw.get("language") if isinstance(raw, dict) else None
    if not isinstance(language_cfg, dict) or not str(language_cfg.get("user") or "").strip():
        if not isinstance(language_cfg, dict):
            language_cfg = {}
        language_cfg["user"] = "zh-CN"
        raw["language"] = language_cfg
        save_json(path, raw)
    return str(language_cfg["user"]).strip()


def user_language(repo: Path) -> str:
    raw = (config(repo).get("language") or {}).get("user")
    language = str(raw or "zh-CN").strip()
    if not language:
        raise ValueError("language.user must be a non-empty language name or locale such as zh-CN")
    return language


def language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language.strip().lower(), language.strip())


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _set_toml_table_values(text: str, table: str, values: dict[str, str]) -> tuple[str, bool]:
    """Update keys inside a TOML table while preserving unrelated settings."""
    lines = text.splitlines()
    header_re = re.compile(r"^\s*\[" + re.escape(table) + r"\]\s*(?:#.*)?$")
    starts = [i for i, line in enumerate(lines) if header_re.match(line)]
    assignments = {key: f"{key} = {_toml_quote(value)}" for key, value in values.items()}
    if not starts:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{table}]")
        lines.extend(assignments.values())
        updated = "\n".join(lines) + "\n"
        return updated, updated != text

    start = starts[0]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*\[[^\]]+\]\s*(?:#.*)?$", lines[i]):
            end = i
            break
    found: set[str] = set()
    out: list[str] = []
    for i, line in enumerate(lines):
        if start < i < end:
            matched = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line)
            if matched and matched.group(1) in assignments:
                key = matched.group(1)
                if key in found:
                    continue
                out.append(assignments[key])
                found.add(key)
                continue
        out.append(line)
    missing = [assignments[key] for key in values if key not in found]
    if missing:
        # Recalculate the section end after removing duplicate assignments.
        new_end = len(out)
        for i in range(start + 1, len(out)):
            if re.match(r"^\s*\[[^\]]+\]\s*(?:#.*)?$", out[i]):
                new_end = i
                break
        out[new_end:new_end] = missing
    updated = "\n".join(out) + ("\n" if text.endswith("\n") or out else "")
    return updated, updated != text


def _set_yaml_root_values(text: str, values: dict[str, str]) -> tuple[str, bool]:
    """Update top-level YAML keys without rewriting unrelated module config."""
    lines = text.splitlines()
    found: set[str] = set()
    out: list[str] = []
    key_re = {key: re.compile(r"^" + re.escape(key) + r"\s*:") for key in values}
    for line in lines:
        key = next((key for key, pattern in key_re.items() if pattern.match(line)), None)
        if key is None:
            out.append(line)
        elif key not in found:
            out.append(f"{key}: {json.dumps(values[key], ensure_ascii=False)}")
            found.add(key)
    missing = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in values.items() if key not in found]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.extend(missing)
    updated = "\n".join(out) + ("\n" if text.endswith("\n") or out else "")
    return updated, updated != text


def _set_toml_language_values(text: str, values: dict[str, str]) -> tuple[str, bool]:
    """Update only BMAD's core language keys, preserving other tables."""
    return _set_toml_table_values(text, "core", values)


def configure_bmad_language(repo: Path, language: str) -> dict[str, Any]:
    """Synchronize CAO's two language keys across BMAD's installed config layers."""
    bmad_root = repo / "_bmad"
    if not bmad_root.is_dir():
        raise ValueError("BMAD is not installed: expected _bmad/ in the project")
    label = "Chinese" if language.lower() in {"zh-cn", "zh-hans"} else language_label(language)
    values = {"communication_language": label, "document_output_language": label}

    modern_candidates = [
        bmad_root / "config.toml",
        bmad_root / "config.user.toml",
        bmad_root / "custom" / "config.toml",
        bmad_root / "custom" / "config.user.toml",
    ]
    modern_installed = any(path.is_file() for path in modern_candidates)
    yaml_candidates = sorted(bmad_root.rglob("config.yaml")) + sorted(bmad_root.rglob("config.yml"))
    targets: list[tuple[Path, str]] = []
    if modern_installed:
        # Keep installer answers and existing overrides consistent. Only the two
        # CAO-managed language keys change; all other project settings survive.
        targets.extend((path, "toml") for path in modern_candidates
                       if path.is_file() or path.name == "config.user.toml")
    elif yaml_candidates:
        targets.extend((path, "yaml") for path in yaml_candidates)
    else:
        # A partially initialized/new BMAD directory still gets a valid modern
        # user-level config rather than failing after installation.
        targets.extend((path, "toml") for path in modern_candidates if path.name == "config.user.toml")

    # If a hybrid project contains both modern TOML and legacy YAML config files,
    # update the legacy files too so old module workflows don't fall back to English.
    if modern_installed and yaml_candidates:
        targets.extend((path, "yaml") for path in yaml_candidates)

    changed: list[str] = []
    unchanged: list[str] = []
    for path, fmt in targets:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        updated, did_change = (_set_toml_language_values(original, values) if fmt == "toml"
                               else _set_yaml_root_values(original, values))
        if did_change:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(updated, encoding="utf-8")
            changed.append(path.relative_to(repo).as_posix())
        else:
            unchanged.append(path.relative_to(repo).as_posix())
    return {"provider": "bmad", "language": language, "label": label,
            "configured": True, "language_values": values,
            "changed_files": changed, "unchanged_files": unchanged}


def _strip_managed_yaml_context(lines: list[str]) -> list[str]:
    out: list[str] = []
    inside = False
    for line in lines:
        marker = line.strip()
        if marker == LANGUAGE_POLICY_START:
            inside = True
            continue
        if marker == LANGUAGE_POLICY_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def _update_openspec_context(text: str, language: str) -> tuple[str, bool]:
    label = language_label(language)
    lines = _strip_managed_yaml_context(text.splitlines())
    if language.strip().lower() in {"zh-cn", "zh-hans"}:
        policy_lines = [
            "用户交流语言：简体中文。",
            "所有 OpenSpec 原生产物（变更提案、规格、设计、任务和总结）均使用简体中文。",
            "保留工作流要求必须保持不变的结构化关键字、代码标识符、命令和文件名。",
            "如果用户明确要求某个范围使用其他语言，则该范围遵循用户的明确要求。",
        ]
    elif language.strip().lower() in {"zh-tw", "zh-hant"}:
        policy_lines = [
            "使用者交流語言：繁體中文。",
            "所有 OpenSpec 原生產物（變更提案、規格、設計、任務和摘要）均使用繁體中文。",
            "保留工作流程要求必須保持不變的結構關鍵字、程式碼識別符、命令和檔名。",
            "若使用者明確要求特定範圍使用其他語言，該範圍遵循使用者的明確要求。",
        ]
    else:
        policy_lines = [
            f"User communication language: {label}.",
            f"Write all OpenSpec artifacts (proposal, specs, design, tasks, and summaries) in {label}.",
            "Preserve required structural keywords, code identifiers, commands, and file names.",
            "An explicit user request for another language takes precedence for the requested scope.",
        ]
    block = [f"  {LANGUAGE_POLICY_START}", *[f"  {line}" for line in policy_lines], f"  {LANGUAGE_POLICY_END}"]
    context_index = next((i for i, line in enumerate(lines) if re.match(r"^context\s*:", line)), None)
    if context_index is None:
        if lines and any(line.strip() for line in lines):
            lines.append("")
        lines.extend(["context: |", *block])
        return "\n".join(lines) + "\n", True

    context_line = lines[context_index]
    _, raw_value = context_line.split(":", 1)
    raw_value = raw_value.strip()
    if raw_value in {"|", "|-", "|+", ">", ">-", ">+"} or raw_value == "":
        end = len(lines)
        for i in range(context_index + 1, len(lines)):
            line = lines[i]
            if line.strip() and not line[0].isspace() and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line):
                end = i
                break
        lines[end:end] = block
    else:
        value = raw_value
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        lines[context_index:context_index + 1] = [f"context: |", f"  {value}", *block]
    updated = "\n".join(lines) + "\n"
    return updated, updated != text


def _openspec_language_config(repo: Path) -> Path:
    local = repo / "openspec" / "config.yaml"
    if not local.is_file():
        return local
    text = local.read_text(encoding="utf-8")
    match = re.search(r"^store\s*:\s*(.+?)\s*(?:#.*)?$", text, flags=re.MULTILINE)
    if not match:
        return local
    raw_store = match.group(1).strip().strip("\"'")
    if not raw_store:
        return local
    store = Path(raw_store).expanduser()
    if not store.is_absolute():
        store = (repo / store).resolve()
    if store.is_file():
        return store
    for candidate in (store / "openspec" / "config.yaml", store / "config.yaml"):
        if candidate.is_file():
            return candidate
    raise ValueError(f"OpenSpec points to external store {store}, but no config.yaml was found there")


def configure_openspec_language(repo: Path, language: str) -> dict[str, Any]:
    """Add a CAO-managed language context to OpenSpec's native project config."""
    config_path = _openspec_language_config(repo)
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else "schema: spec-driven\n"
    updated, changed = _update_openspec_context(original, language)
    if changed:
        config_path.write_text(updated, encoding="utf-8")
    return {"provider": "openspec", "language": language, "label": language_label(language),
            "configured": True, "config_file": config_path.resolve().as_posix(), "changed": changed}


def _update_managed_markdown_section(text: str, language: str, provider: str) -> tuple[str, bool]:
    label = language_label(language)
    start = f"<!-- {LANGUAGE_POLICY_START} -->"
    end = f"<!-- {LANGUAGE_POLICY_END} -->"
    if language.strip().lower() in {"zh-cn", "zh-hans"}:
        heading = "## 语言约定"
        lines = [
            "- 与用户交流时使用简体中文。",
            f"- {provider} 的所有规格、计划、任务、设计及相关文档均使用简体中文。",
            "- 保留必须保持不变的结构化关键字、代码符号、命令和文件名。",
            "- 如果用户明确要求某个范围使用其他语言，则该范围遵循用户的明确要求。",
        ]
    elif language.strip().lower() in {"zh-tw", "zh-hant"}:
        heading = "## 語言慣例"
        lines = [
            "- 與使用者交流時使用繁體中文。",
            f"- {provider} 的所有規格、計畫、任務、設計及相關文件均使用繁體中文。",
            "- 保留必須保持不變的結構關鍵字、程式碼符號、命令和檔名。",
            "- 若使用者明確要求特定範圍使用其他語言，該範圍遵循使用者的明確要求。",
        ]
    else:
        heading = "## Language policy"
        lines = [
            f"- Communicate with the user in {label}.",
            f"- Write all {provider} specifications, plans, tasks, designs, and related documents in {label}.",
            "- Preserve required structural keywords, code symbols, commands, and file names.",
            "- An explicit user request for another language takes precedence for the requested scope.",
        ]
    section = [start, heading, *lines, end]
    lines = text.splitlines()
    output: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == start:
            inside = True
            continue
        if line.strip() == end:
            inside = False
            continue
        if not inside:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend(section)
    updated = "\n".join(output) + "\n"
    return updated, updated != text


def configure_speckit_language(repo: Path, language: str) -> dict[str, Any]:
    """Maintain the language rule in Spec Kit's live constitution."""
    if not (repo / ".specify").is_dir():
        raise ValueError("Spec Kit is not installed: expected .specify/ in the project")
    constitution = repo / ".specify" / "memory" / "constitution.md"
    original = constitution.read_text(encoding="utf-8") if constitution.exists() else "# Project Constitution\n"
    updated, changed = _update_managed_markdown_section(original, language, "Spec Kit")
    if changed:
        constitution.parent.mkdir(parents=True, exist_ok=True)
        constitution.write_text(updated, encoding="utf-8")
    return {"provider": "speckit", "language": language, "label": language_label(language),
            "configured": True, "changed": changed, "config_file": constitution.relative_to(repo).as_posix()}


def apply_provider_language(repo: Path, provider: str, language: str | None = None) -> dict[str, Any]:
    actual_language = language or user_language(repo)
    handlers = {
        "bmad": configure_bmad_language,
        "openspec": configure_openspec_language,
        "speckit": configure_speckit_language,
    }
    if provider not in handlers:
        raise ValueError(f"Unsupported SDD provider for language configuration: {provider}")
    return handlers[provider](repo, actual_language)


def apply_installed_sdd_languages(repo: Path, providers: list[str] | None = None) -> dict[str, Any]:
    from scripts.cao_providers import detect_sdd

    ensure_language_preference(repo)
    selected = providers if providers is not None else [name for name in SDD_CATALOG if detect_sdd(repo, name)["installed"]]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    language = user_language(repo)
    for provider in selected:
        if provider not in SDD_CATALOG or not detect_sdd(repo, provider)["installed"]:
            continue
        try:
            results.append(apply_provider_language(repo, provider, language))
        except (OSError, UnicodeError, ValueError) as exc:
            failures.append({"provider": provider, "reason": str(exc)})
    return {"language": language, "label": language_label(language), "results": results, "failures": failures}


def cmd_language_show(args: argparse.Namespace) -> int:
    from scripts.cao_providers import detect_sdd

    repo = repo_root(args.repo)
    if not (repo / CONFIG_REL).exists():
        print(json.dumps({"ok": True, "initialized": False, "language": {"user": "zh-CN", "label": "Simplified Chinese"}}, ensure_ascii=False, indent=2))
        return 0
    ensure_language_preference(repo)
    current = user_language(repo)
    providers = {name: bool(detect_sdd(repo, name)["installed"]) for name in SDD_CATALOG}
    print(json.dumps({"ok": True, "language": {"user": current, "label": language_label(current), "communication": "follow-user", "sdd_artifacts": "follow-user"}, "installed_sdds": providers}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_language_set(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    lang = str(args.language or "").strip()
    if not lang:
        raise ValueError("--language must be a non-empty locale or language name, for example zh-CN, en-US, ja-JP")
    cfg = config(repo)
    cfg.setdefault("language", {})["user"] = lang
    save_json(repo / CONFIG_REL, cfg)
    result = apply_installed_sdd_languages(repo)
    print(json.dumps({"ok": not result["failures"], "language": result, "config_file": str(repo / CONFIG_REL)}, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


@locked_command
def cmd_language_apply(args: argparse.Namespace) -> int:
    from scripts.cao_providers import detect_sdd

    repo = repo_root(args.repo)
    ensure_initialized(repo)
    if args.provider and not detect_sdd(repo, args.provider)["installed"]:
        print(json.dumps({"ok": False, "error": "SDD_NOT_INSTALLED", "provider": args.provider}, ensure_ascii=False, indent=2))
        return 2
    provider_filter = [args.provider] if args.provider else None
    result = apply_installed_sdd_languages(repo, provider_filter)
    print(json.dumps({"ok": not result["failures"], "language": result}, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1
