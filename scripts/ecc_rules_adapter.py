#!/usr/bin/env python3
"""Discover ECC rule packs as an optional external policy/guidance source.

ECC remains an upstream rule source. This adapter never promotes external prose to a
blocking project policy automatically. Project-owned machine policies remain authoritative.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _split_list(value: str) -> list[str]:
    parts = re.split(r"[,\s]+", value.strip())
    return [p for p in parts if p]


def parse_metadata(text: str) -> dict[str, Any]:
    """Parse the small metadata subset used by common agent rule formats."""
    out: dict[str, Any] = {}
    m = FRONTMATTER_RE.match(text)
    if m:
        raw_meta = None
        if yaml is not None:
            try:
                raw_meta = yaml.safe_load(m.group(1)) or {}
            except Exception:
                raw_meta = None
        if isinstance(raw_meta, dict):
            path_value = raw_meta.get("paths", raw_meta.get("fileMatch", raw_meta.get("fileMatchPattern")))
            if isinstance(path_value, str):
                out["paths"] = _split_list(path_value)
            elif isinstance(path_value, list):
                out["paths"] = [str(x) for x in path_value if str(x).strip()]
            aa = raw_meta.get("alwaysApply", raw_meta.get("always_apply"))
            if aa is not None:
                out["always_apply"] = bool(aa)
            for key in ("description", "name", "inclusion"):
                if key in raw_meta:
                    out[key] = str(raw_meta[key])
        else:
            for line in m.group(1).splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip(), value.strip().strip('"\'')
                if key in {"paths", "fileMatch", "fileMatchPattern"}:
                    out["paths"] = _split_list(value)
                elif key in {"alwaysApply", "always_apply"}:
                    out["always_apply"] = value.lower() in {"true", "yes", "1"}
                elif key in {"description", "name", "inclusion"}:
                    out[key] = value
    # Fallback for markdown/table renderings or rules without frontmatter.
    if "paths" not in out:
        m2 = re.search(r"(?mi)^paths\s*(?:\||:)\s*(.+)$", text)
        if m2:
            out["paths"] = _split_list(m2.group(1).replace("**/", "**/"))
    return out


def discover(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    rules: list[dict[str, Any]] = []
    if not root.exists():
        return {"source": "ecc", "root": str(root), "available": False, "rules": [], "packs": []}
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        pack = parts[0] if len(parts) > 1 else "common"
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = parse_metadata(text)
        rules.append({
            "id": f"ecc:{rel[:-3].replace('/', ':')}",
            "source": "ecc",
            "pack": pack,
            "name": path.stem,
            "path": str(path),
            "relative_path": rel,
            "paths": meta.get("paths", []),
            "always_apply": bool(meta.get("always_apply", pack == "common")),
            "description": meta.get("description"),
            "authority": "external_guidance",
            "blocking": False,
        })
    return {
        "source": "ecc",
        "root": str(root),
        "available": True,
        "packs": sorted({r["pack"] for r in rules}),
        "rules": rules,
    }


def relevant_rules(catalog: dict[str, Any], files: Iterable[str], packs: Iterable[str] = ()) -> list[dict[str, Any]]:
    files = list(files)
    pack_set = set(packs)
    selected = []
    for rule in catalog.get("rules", []):
        if pack_set and rule.get("pack") not in pack_set and rule.get("pack") != "common":
            continue
        globs = rule.get("paths") or []
        if rule.get("always_apply") or not globs:
            selected.append(rule)
            continue
        if any(any(fnmatch.fnmatch(f, g) for g in globs) for f in files):
            selected.append(rule)
    return selected


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path, nargs="?", default=Path(".claude/rules/ecc"))
    p.add_argument("--files", nargs="*", default=[])
    p.add_argument("--packs", nargs="*", default=[])
    args = p.parse_args(argv)
    catalog = discover(args.root)
    catalog["selected"] = relevant_rules(catalog, args.files, args.packs)
    print(json.dumps(catalog, ensure_ascii=False, indent=2))
    return 0 if catalog["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
