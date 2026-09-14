#!/usr/bin/env python3
"""Check the runtime context footprint of SKILL.md and report reference size.

The goal is not to minimize documentation. It is to keep the always-loaded Skill
small and route detailed knowledge to lazily loaded references.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFAULT_MAX_LINES = 110
DEFAULT_MAX_BYTES = 7500
DEFAULT_MAX_WORDS = 1000


def metrics(repo: Path) -> dict:
    skill = repo / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    encoded = text.encode("utf-8")
    words = re.findall(r"\b[\w'-]+\b", text)
    direct_refs = sorted(set(re.findall(r"references/([A-Za-z0-9._*-]+)", text)))

    refs_dir = repo / "references"
    ref_files = sorted(refs_dir.glob("*.md")) if refs_dir.exists() else []
    ref_bytes = sum(p.stat().st_size for p in ref_files)
    ref_lines = sum(len(p.read_text(encoding="utf-8", errors="ignore").splitlines()) for p in ref_files)

    cross_edges = 0
    known = {p.name for p in ref_files}
    for p in ref_files:
        body = p.read_text(encoding="utf-8", errors="ignore")
        targets = set(re.findall(r"(?:references/)?([A-Za-z0-9_.-]+\.md)", body))
        cross_edges += len({t for t in targets if t in known and t != p.name})

    return {
        "skill": {
            "lines": len(text.splitlines()),
            "bytes": len(encoded),
            "words": len(words),
            "direct_reference_mentions": len(direct_refs),
        },
        "references": {
            "markdown_files": len(ref_files),
            "lines": ref_lines,
            "bytes": ref_bytes,
            "cross_reference_edges": cross_edges,
        },
    }


def validate(repo: Path, max_lines: int, max_bytes: int, max_words: int) -> tuple[dict, list[str]]:
    report = metrics(repo)
    text = (repo / "SKILL.md").read_text(encoding="utf-8")
    errors: list[str] = []

    sm = report["skill"]
    if sm["lines"] > max_lines:
        errors.append(f"SKILL.md has {sm['lines']} lines; limit is {max_lines}")
    if sm["bytes"] > max_bytes:
        errors.append(f"SKILL.md has {sm['bytes']} bytes; limit is {max_bytes}")
    if sm["words"] > max_words:
        errors.append(f"SKILL.md has {sm['words']} words; limit is {max_words}")

    if "Never preload all references" not in text:
        errors.append("SKILL.md is missing the lazy reference loading invariant")
    if re.search(r"(?im)^version\s*:", text):
        errors.append("SKILL.md frontmatter/body must not carry release version metadata")
    if re.search(r"\bV(?:[1-9]\d*)(?:\.\d+)*\b", text):
        errors.append("SKILL.md contains version-coupled capability labels")

    return report, errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    report, errors = validate(repo, args.max_lines, args.max_bytes, args.max_words)
    payload = {**report, "status": "PASS" if not errors else "FAIL", "errors": errors}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        s = report["skill"]
        r = report["references"]
        print(f"SKILL.md: {s['lines']} lines, {s['words']} words, {s['bytes']} bytes")
        print(f"References: {r['markdown_files']} md files, {r['lines']} lines, {r['bytes']} bytes")
        print(f"Reference cross-links: {r['cross_reference_edges']}")
        print(payload["status"])
        for err in errors:
            print(f"- {err}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
