"""Small repository evidence fixture for state-transition tests."""
import json
from pathlib import Path

import context_plane


def refresh_context(repo, path):
    intake = repo / ".orchestrator/intake"
    manifest = context_plane.build_manifest(repo, intake, state_ref=path,
                                            request_ref=intake / "request-context.md")
    context_plane._dump_json(intake / "context-manifest.json", manifest)
    return manifest


def attach_fixture_analysis(sm, path, snapshot="A1"):
    repo = path.parent.parent
    intake = repo / ".orchestrator/intake"
    intake.mkdir(parents=True, exist_ok=True)
    docs = {
        "decision.json": {"status": "CLASSIFIED", "flow_profile": sm._load(path)["flow_profile"]},
        "semantic-impact.json": {"completeness": {"complete": True}, "snapshot": {"id": snapshot}},
        "work-facts.semantic-draft.json": {"extraction": {"analysis_snapshot_id": snapshot}},
        "verification-plan.json": {"items": []},
    }
    for name, doc in docs.items():
        (intake / name).write_text(json.dumps(doc), encoding="utf-8")
    (intake / "request-context.md").write_text("Fixture requirement and acceptance criteria.", encoding="utf-8")
    sm.attach_analysis(path, "test", snapshot, str(intake / "semantic-impact.json"),
                       str(intake / "work-facts.semantic-draft.json"), str(intake / "decision.json"),
                       str(intake / "verification-plan.json"),
                       context_manifest_ref=str(intake / "context-manifest.json"))
    refresh_context(repo, path)
