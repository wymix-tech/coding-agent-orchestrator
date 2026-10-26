"""Bounded native inputs and exact Work-scoped acceptance outputs; never writes Work."""
import hashlib
import json
from pathlib import Path
import re

from scripts.path_guard import provider_artifact, repo_file
from scripts.sdd_acceptance_policy import normalize_declaration, AcceptanceValidationError

MAX_REPORT_BYTES = 1024 * 1024


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("SDD_ACCEPTANCE_REPORT_TOO_LARGE")
    return raw


def read_json(path: Path) -> dict:
    try:
        value = json.loads(_read_bytes(path).decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("SDD_ACCEPTANCE_RESULT_INVALID")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        if str(exc) == "SDD_ACCEPTANCE_REPORT_TOO_LARGE":
            raise
        raise ValueError("SDD_ACCEPTANCE_RESULT_INVALID") from exc


def source_hashes(repo: Path, declaration: dict, provider_dirs: list[str]) -> dict[str, str]:
    try:
        declaration = normalize_declaration(declaration)
        hashes = {}
        for relative in declaration["sources"]:
            path = provider_artifact(repo, relative, provider_dirs)
            if not path.is_file():
                raise ValueError("SDD_ACCEPTANCE_SOURCE_INVALID")
            raw = _read_bytes(path)
            text = raw.decode("utf-8-sig")
            text = re.sub(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", "", text, flags=re.S)
            # Mechanical empty/title-only detection, not interpretation of the SPEC.
            body = [line.strip() for line in text.splitlines()
                    if line.strip() and not re.match(r"^\s*#{1,6}(?:\s|$)", line)]
            if not body:
                raise ValueError("SDD_ACCEPTANCE_SOURCE_INVALID")
            if declaration.get('evidence_policy') == 'criterion-checks-v1':
                # Terminal status is lifecycle metadata, not a changed criterion.
                original = raw.decode('utf-8-sig')
                header = re.match(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', original, re.S)
                if header:
                    semantic_header = re.sub(r'(?m)^status:[^\n]*(?:\n|$)', '', header[1])
                    raw = (semantic_header + original[header.end():]).encode()
            hashes[relative] = hashlib.sha256(raw).hexdigest()
        return hashes
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise ValueError("SDD_ACCEPTANCE_SOURCE_INVALID") from exc


def _linked_path(path: Path, repo: Path) -> bool:
    """A linked report or ancestor could alias another Work or a protected input."""
    while path != repo:
        if path.is_symlink():
            return True
        path = path.parent
    return False


def output_paths(repo: Path, declaration: dict, provider_dirs: list[str],
                 work_id: str, previous: dict[str, str]) -> set[str]:
    repo = repo.resolve()
    try:
        declaration = normalize_declaration(declaration)
        if not isinstance(work_id, str) or work_id in {"", ".", ".."} or any(char in work_id for char in "/\\"):
            raise ValueError("SDD_ACCEPTANCE_OUTPUT_INVALID")
        sources = {provider_artifact(repo, value, provider_dirs) for value in declaration["sources"]}
        result = set()
        for relative in declaration["outputs"]:
            lexical = repo / relative
            path = provider_artifact(repo, relative, provider_dirs)
            scopes = [(repo / directory / "cao-acceptance" / work_id).resolve() for directory in provider_dirs]
            if (lexical.suffix.lower() not in {".md", ".json", ".txt"} or path in sources
                    or _linked_path(lexical, repo)
                    or not any(path != scope and path.is_relative_to(scope) for scope in scopes)):
                raise ValueError("SDD_ACCEPTANCE_OUTPUT_INVALID")
            if path.exists():
                if not path.is_file() or previous.get(relative) != _file_hash(path):
                    raise ValueError("SDD_ACCEPTANCE_OUTPUT_INVALID")
            result.add(relative)
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        raise AcceptanceValidationError("SDD_ACCEPTANCE_OUTPUT_INVALID", "$.declaration.outputs",
            "new .md/.json/.txt files under <provider>/cao-acceptance/<work-id>/; existing files need matching CAO ownership") from exc


def report_hashes(repo: Path, results: list[dict], outputs: list[str]) -> dict[str, str]:
    references = [(relative, f"$.declaration.outputs[{index}]") for index, relative in enumerate(outputs)]
    for index, item in enumerate(results):
        references.extend((ref["path"], f"$.items[{index}].evidence[{ref_index}].path")
                          for ref_index, ref in enumerate(item["evidence"]))
    hashes = {}
    for relative, field in references:
        if relative in hashes:
            continue
        try:
            path = repo_file(repo, relative)
            if not path.is_file():
                raise ValueError("SDD_ACCEPTANCE_EVIDENCE_INVALID")
            hashes[relative] = _file_hash(path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise AcceptanceValidationError("SDD_ACCEPTANCE_EVIDENCE_INVALID", field,
                f"an existing repository file: {relative}; declared outputs must be written before the executor exits") from exc
    return hashes
