#!/usr/bin/env python3
"""Run evidence collection -> optional semantic resolution -> strict flow classification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import decision_engine
import fact_extractor
import fact_resolver


def dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--request", default="")
    p.add_argument("--request-file", type=Path)
    p.add_argument("--base-ref")
    p.add_argument("--resolutions", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path(".orchestrator/intake"))
    args = p.parse_args(argv)

    request = args.request_file.read_text(encoding="utf-8") if args.request_file else args.request
    draft = fact_extractor.extract(args.repo, request=request, base_ref=args.base_ref)
    outdir = args.output_dir
    dump(outdir / "work-facts.draft.json", draft)

    facts = draft
    if args.resolutions:
        resolution_doc = json.loads(args.resolutions.read_text(encoding="utf-8"))
        facts = fact_resolver.apply_resolutions(draft, resolution_doc)
        dump(outdir / "work-facts.resolved.json", facts)

    decision = decision_engine.classify(facts, strict_evidence=True)
    dump(outdir / "decision.json", decision)
    summary = {
        "status": decision.get("status"),
        "flow_profile": decision.get("flow_profile"),
        "snapshot_id": draft.get("extraction", {}).get("snapshot_id"),
        "unresolved_count": len(facts.get("extraction", {}).get("resolution_queue", [])),
        "artifacts": {
            "draft": str(outdir / "work-facts.draft.json"),
            "decision": str(outdir / "decision.json"),
        },
    }
    if args.resolutions:
        summary["artifacts"]["resolved"] = str(outdir / "work-facts.resolved.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if decision.get("status") == "CLASSIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
