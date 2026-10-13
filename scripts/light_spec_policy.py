"""Bounded, structural SPEC/criteria checks for light work; no semantic verdicts."""
import hashlib
import json
import re
import shlex
from pathlib import Path

from scripts.path_guard import provider_artifact

MAX_BYTES = 1024 * 1024


def binding_digest(binding: dict) -> str:
    return hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _lines(raw: bytes) -> list[str]:
    text = raw.decode("utf-8-sig")
    text = re.sub(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return lines


def _heading(line: str):
    match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
    return (len(match[1]), match[2].strip()) if match else None


def _content(lines: list[str]) -> bool:
    for index, line in enumerate(lines):
        # A table header and separator alone do not declare an acceptance item.
        if "|" in line and index + 1 < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[index + 1]) and "-" in lines[index + 1]:
            continue
        if _heading(line):
            continue
        value = re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", line.strip())
        value = re.sub(r"^\[[ xX]\]\s*", "", value).strip(" |*-_")
        if (not value or re.fullmatch(r"[\s|:\-]+", value)
                or re.fullmatch(r"(?i)(?:todo|tbd|n/?a|none|待填写|待补充|占位)", value)
                or re.fullmatch(r"(?:\{[^}]+\}|<[^>]+>|[A-Z][A-Z_]+)", value)):
            continue
        if any(char.isalnum() for char in value):
            return True
    return False


def _read(repo: Path, relative: str, dirs: list[str]) -> tuple[dict, list[str]]:
    path = provider_artifact(repo, relative, dirs)
    if not path.is_file():
        raise ValueError("LIGHT_SPEC_SOURCE_INVALID")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("LIGHT_SPEC_SOURCE_TOO_LARGE")
    lines = _lines(raw)
    if not _content(lines):
        raise ValueError("LIGHT_SPEC_BODY_REQUIRED")
    return {"path": path.relative_to(repo.resolve()).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest()}, lines


def make_binding(repo: Path, work: dict, spec: str, criteria: str | None,
                 section: str | None, dirs: list[str]) -> dict:
    try:
        spec_ref, _ = _read(repo, spec, dirs)
        criteria_ref, lines = _read(repo, criteria or spec, dirs)
        section = section.strip() if section else None
        if spec_ref["path"] == criteria_ref["path"] and not section:
            raise ValueError("LIGHT_SPEC_CRITERIA_SECTION_REQUIRED")
        if section:
            matches = [(i, _heading(line)[0]) for i, line in enumerate(lines)
                       if _heading(line) and _heading(line)[1] == section]
            if len(matches) != 1:
                raise ValueError("LIGHT_SPEC_CRITERIA_SECTION_INVALID")
            index, level = matches[0]
            end = next((i for i in range(index + 1, len(lines))
                        if _heading(lines[i]) and _heading(lines[i])[0] <= level), len(lines))
            lines = lines[index + 1:end]
        if not _content(lines):
            raise ValueError("LIGHT_SPEC_CRITERIA_REQUIRED")
        return {"version": 1, "requirement_sha256": work["requirement"]["sha256"],
                "provider": work["sdd"]["provider"], "spec": spec_ref,
                "criteria": criteria_ref, "criteria_section": section}
    except (OSError, UnicodeError, RuntimeError) as exc:
        raise ValueError("LIGHT_SPEC_SOURCE_INVALID") from exc


def validate_binding(repo: Path, work: dict, binding: dict, dirs: list[str]) -> dict:
    try:
        current = make_binding(repo, work, binding["spec"]["path"],
                               binding["criteria"]["path"], binding.get("criteria_section"), dirs)
        if current != binding:
            raise ValueError("LIGHT_SPEC_CHANGED")
        return {"ready": True, "state": "valid", "reason": None}
    except (ValueError, KeyError, TypeError) as exc:
        return {"ready": False, "state": "stale", "reason": str(exc)}


def light_spec_state(repo: Path, work: dict, dirs: list[str]) -> dict:
    binding = (work.get("sdd") or {}).get("spec_binding")
    if not isinstance(binding, dict):
        return {"ready": False, "state": "unconfigured", "reason": "light spec and acceptance criteria are not bound",
                "next": f'Bind native SPEC/criteria: `cao sdd spec bind --spec <provider-spec.md> --criteria-section "<heading>" --work {work["id"]}`.'}
    state = validate_binding(repo, work, binding, dirs)
    if not state["ready"]:
        args = ["cao", "sdd", "spec", "bind", "--spec", (binding.get("spec") or {}).get("path", "<provider-spec.md>"),
                "--criteria", (binding.get("criteria") or {}).get("path", "<provider-criteria.md>")]
        if binding.get("criteria_section"):
            args += ["--criteria-section", binding["criteria_section"]]
        args += ["--replace", "--work", work["id"]]
        state["next"] = "Review SPEC/criteria, then rebind and verify: `" + shlex.join(args) + "` → `cao verify`."
    return state
