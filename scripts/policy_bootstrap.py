#!/usr/bin/env python3
"""Install V6 starter Engineering Policy files into a repository without overwriting by default."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    src = Path(__file__).resolve().parents[1] / "policies"
    dst = args.repo.resolve() / ".orchestrator" / "policies"
    dst.mkdir(parents=True, exist_ok=True)
    copied = []
    skipped = []
    for item in sorted(src.glob("*.yaml")):
        target = dst / item.name
        if target.exists() and not args.force:
            skipped.append(str(target))
            continue
        shutil.copy2(item, target)
        copied.append(str(target))
    print("copied:")
    for x in copied:
        print(f"  {x}")
    if skipped:
        print("skipped existing:")
        for x in skipped:
            print(f"  {x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
